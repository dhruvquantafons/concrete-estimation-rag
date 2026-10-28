"""Page-level text extraction.

For every PDF page we decide between the embedded text layer and Tesseract OCR:
  * ocr=never  -> text layer only
  * ocr=force  -> OCR every page (legacy scans with a garbled text layer, image-only SORs)
  * ocr=auto   -> OCR when the text layer is empty or scores below QUALITY_THRESHOLD

Results (text + word boxes + confidences) are cached per page under
data/interim/pages/<doc_id>/p0001.json so parsers can iterate without re-OCR.
When OCR is used, the original text layer is kept in `alt_text` - having two independent
readings of the same page lets us cross-check numbers in critical tables.
"""
from __future__ import annotations

import io
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pymupdf
from PIL import Image, ImageOps

from concrete_rag.config import get_settings
from concrete_rag.schemas import PageText, SourceDoc, Word

QUALITY_THRESHOLD = 0.75   # auto mode only catches empty/badly garbled pages; known legacy scans use ocr: force
_GOOD_TOKEN = re.compile(r"^(?:[A-Za-z]{1,15}|\d[\d.,:/%-]*|[ivxIVX]+|[a-z]\))$")
_STRIP = "()[]{}.,;:'\"“”‘’—–-"


def text_quality(text: str) -> float:
    """Share of tokens that look like normal words/numbers. Garbled legacy OCR
    (merged words, stray symbols) scores low; clean digital text scores ~0.9+."""
    toks = [t.strip(_STRIP) for t in text.split()]
    toks = [t for t in toks if t]
    if len(toks) < 30:
        return 0.0 if len(toks) < 5 else 0.5
    good = sum(1 for t in toks if _GOOD_TOKEN.match(t))
    return good / len(toks)


def _psm_for(doc: SourceDoc) -> int:
    # SOR pages are ruled tables: treat the page as a single block so each line spans all columns.
    # IS codes are two-column prose: let Tesseract segment columns (psm 3).
    return 6 if doc.doc_type in ("sor", "dar") else 3


def page_image(doc: pymupdf.Document, page: pymupdf.Page, dpi: int = 300):
    """Image to OCR plus the pixel->point transform (ox, oy, sx, sy).

    Scanned pages are a single embedded image. OCR-ing that image at its *native* resolution is
    markedly more accurate than re-rendering: on the 96-dpi CPWD DSR scans, re-rendering to 300 dpi
    interpolates the glyphs and Tesseract confuses 8/6/2 (3 of 19 labour rates right), while the native
    image reads 23 of 23. Pages without one dominant image are rendered at `dpi`."""
    infos = [i for i in page.get_image_info(xrefs=True) if i.get("xref")]
    if len(infos) == 1:
        bbox = pymupdf.Rect(infos[0]["bbox"])
        a, b, c, dd = infos[0]["transform"][:4]
        upright = a > 0 and dd > 0 and abs(b) < 1e-3 and abs(c) < 1e-3   # flipped/rotated placements -> render
        if upright and bbox.width * bbox.height >= 0.8 * page.rect.width * page.rect.height:
            try:
                raw = doc.extract_image(infos[0]["xref"])
                img = Image.open(io.BytesIO(raw["image"]))
                eff_dpi = img.width / (bbox.width / 72.0)
                if 70 <= eff_dpi <= 600 and page.rotation == 0:
                    img = ImageOps.autocontrast(img.convert("L"))
                    return img, (bbox.x0, bbox.y0, bbox.width / img.width, bbox.height / img.height)
            except Exception:
                pass
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    img = ImageOps.autocontrast(Image.open(io.BytesIO(pix.tobytes("png"))))
    return img, (0.0, 0.0, 72.0 / dpi, 72.0 / dpi)


def _ocr_page(pdf_path: str, page_index: int, dpi: int, psm: int, lang: str) -> dict:
    import pytesseract

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    with pymupdf.open(pdf_path) as d:
        page = d[page_index]
        width, height = page.rect.width, page.rect.height
        img, (ox, oy, sx, sy) = page_image(d, page, dpi)
    data = pytesseract.image_to_data(
        img, lang=lang, config=f"--psm {psm} --oem 1 -c preserve_interword_spaces=1",
        output_type=pytesseract.Output.DICT,
    )
    words, lines, confs = [], {}, []
    for i, txt in enumerate(data["text"]):
        txt = (txt or "").strip()
        if not txt:
            continue
        conf = float(data["conf"][i])
        x, y, w, h = (data[k][i] for k in ("left", "top", "width", "height"))
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        words.append(Word(text=txt, x0=ox + x * sx, y0=oy + y * sy, x1=ox + (x + w) * sx, y1=oy + (y + h) * sy,
                          conf=conf, block=data["block_num"][i], line=data["line_num"][i]).model_dump())
        lines.setdefault(key, []).append(txt)
        if conf >= 0:
            confs.append(conf)
    # reading order = tesseract block/par/line order; blank line between paragraphs
    out, prev_par = [], None
    for (b, p, _l), toks in lines.items():
        if prev_par is not None and (b, p) != prev_par:
            out.append("")
        out.append(" ".join(toks))
        prev_par = (b, p)
    return {
        "text": "\n".join(out),
        "words": words,
        "width": width,
        "height": height,
        "mean_conf": (sum(confs) / len(confs)) if confs else None,
    }


def _text_layer(page: pymupdf.Page) -> tuple[str, list[dict]]:
    """Column-aware reading order from the embedded text layer (blocks sorted by column, then y)."""
    blocks = [b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()]
    mid = page.rect.width / 2
    two_col = sum(1 for b in blocks if b[2] < mid + 5) >= 3 and sum(1 for b in blocks if b[0] > mid - 5) >= 3

    # Two-column pages: full-width blocks (titles, tables) split the page into horizontal bands;
    # inside each band read the left column, then the right.
    if two_col:
        blocks.sort(key=lambda b: b[1])
        bands, cur = [], []
        for b in blocks:
            full = b[0] < mid - 40 and b[2] > mid + 40
            if full:
                if cur:
                    bands.append(cur)
                    cur = []
                bands.append([b])
            else:
                cur.append(b)
        if cur:
            bands.append(cur)
        ordered = []
        for band in bands:
            ordered += sorted(band, key=lambda b: (0 if b[0] < mid - 5 else 1, b[1], b[0]))
    else:
        ordered = sorted(blocks, key=lambda b: (round(b[1], 1), b[0]))
    text = "\n".join(b[4].strip() for b in ordered)
    words = [Word(text=w[4], x0=w[0], y0=w[1], x1=w[2], y1=w[3], block=w[5], line=w[6]).model_dump()
             for w in page.get_text("words")]
    return text, words


def page_cache_dir(doc_id: str) -> Path:
    d = get_settings().interim_dir / "pages" / doc_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_pages(doc_id: str) -> list[PageText]:
    d = page_cache_dir(doc_id)
    return [PageText(**json.loads(p.read_text())) for p in sorted(d.glob("p*.json"))]


def extract_document(doc: SourceDoc, force: bool = False, progress=None) -> list[PageText]:
    """Extract all pages of `doc`, OCR-ing as required. Uses the cache unless force=True."""
    s = get_settings()
    pdf_path = str(s.root / doc.path)
    cache = page_cache_dir(doc.doc_id)
    pdf = pymupdf.open(pdf_path)
    n = len(pdf)
    results: dict[int, PageText] = {}
    to_ocr: list[int] = []
    layer: dict[int, tuple[str, list[dict], float]] = {}

    for i in range(n):
        f = cache / f"p{i + 1:04d}.json"
        if f.exists() and not force:
            results[i] = PageText(**json.loads(f.read_text()))
            continue
        text, words = _text_layer(pdf[i])
        q = text_quality(text)
        layer[i] = (text, words, q)
        if doc.ocr == "force" or (doc.ocr == "auto" and q < QUALITY_THRESHOLD):
            to_ocr.append(i)
        else:
            pt = PageText(doc_id=doc.doc_id, page=i + 1, method="text_layer", text=text, words=words,
                          width=pdf[i].rect.width, height=pdf[i].rect.height, quality=q)
            results[i] = pt
            f.write_text(pt.model_dump_json())

    if to_ocr:
        psm = _psm_for(doc)
        with ProcessPoolExecutor(max_workers=s.ocr_workers) as ex:
            futs = {ex.submit(_ocr_page, pdf_path, i, s.ocr_dpi, psm, s.tesseract_lang): i for i in to_ocr}
            for done, fut in enumerate(as_completed(futs), 1):
                i = futs[fut]
                r = fut.result()
                pt = PageText(doc_id=doc.doc_id, page=i + 1, method="ocr", text=r["text"],
                              alt_text=layer[i][0], words=r["words"], width=r["width"], height=r["height"],
                              quality=text_quality(r["text"]), mean_conf=r["mean_conf"])
                results[i] = pt
                (cache / f"p{i + 1:04d}.json").write_text(pt.model_dump_json())
                if progress:
                    progress(done, len(to_ocr))
    return [results[i] for i in range(n)]
