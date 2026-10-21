"""CPWD Delhi Schedule of Rates (scanned, bilingual) parser.

Produces:
  * SorItem rows  - sub-head items (code, description incl. parent text, unit, rate)
  * BasicRate rows - 01 hire charges / 02 labour / 03 materials
  * cement consumption coefficients (quintal of cement per unit of item)

Reliability measures (the source scans are 96 dpi):
  * Hindi pages (alternate pages) are detected by OCR confidence and not used for text, but their
    rate column is aligned with the English rate sequence as an independent second reading;
  * every rate cell is re-OCR'd from the native image with a digits-only whitelist (third reading);
  * readings are combined by majority vote; a rate is `confirmed` only when >= 2 readings agree;
  * item codes are decoded as a sequence (normalize.item_codes.decode_sequence);
  * sibling-rate outliers and missing units/rates are flagged.
Everything doubtful gets needs_review=True and lands in the review CSV; human corrections in
config/corrections/<sor>.yaml are applied on the next run.
"""
from __future__ import annotations

import difflib
import io
import json
import os
import re
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf
import yaml
from PIL import Image

from concrete_rag.config import get_settings
from concrete_rag.ingest.layout import Line, page_lines
from concrete_rag.ingest.pages import page_image
from concrete_rag.normalize.item_codes import REVIEW_COST, canonical_key, decode_sequence, parse_code, parent_code
from concrete_rag.normalize.units import normalize_unit
from concrete_rag.schemas import BasicRate, PageText, SorItem, SourceDoc

ENGLISH_CONF = 70.0
RATE_RE = re.compile(r"^\d{1,6}\.\d{2}$")
CODE_TOKEN_RE = re.compile(r"^[(\[{]?[\dOoIl|§$][\dOoIl|SsA-H.,:;'’ ]{0,9}[)\]}.,*\"]?$")


# ------------------------------------------------------------------ helpers
def load_sor_config(sor_name: str, year: int) -> dict:
    p = get_settings().root / "config" / "sor" / f"{sor_name.lower().replace('-', '_')}_{year}.yaml"
    return yaml.safe_load(p.read_text()) if p.exists() else {"sub_heads": {}}


def load_corrections(sor_name: str, year: int) -> dict:
    p = get_settings().root / "config" / "corrections" / f"{sor_name.lower().replace('-', '_')}_{year}.yaml"
    if not p.exists():
        return {}
    data = yaml.safe_load(p.read_text()) or {}
    return data.get("items", {}) or {}


def load_code_tokens(sor_name: str, year: int) -> dict:
    """Page-verified readings for code tokens OCR read as letters: {"DOC_ID:page:token": "8.15"}."""
    p = get_settings().root / "config" / "corrections" / f"{sor_name.lower().replace('-', '_')}_{year}.yaml"
    data = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
    return {str(k): str(v) for k, v in (data.get("code_tokens") or {}).items()}


def is_english(page: PageText) -> bool:
    return (page.mean_conf or 0) >= ENGLISH_CONF and len(page.words) > 15


def norm_rate(raw: str | None) -> str | None:
    """OCR rate text -> canonical 'dddd.dd' string (DSR rates always carry 2 decimals)."""
    if not raw:
        return None
    s = raw.strip().replace(" ", "").replace("'", "").replace("’", "")
    s = s.translate(str.maketrans({"$": "8", "§": "5", "O": "0", "o": "0", "l": "1", "I": "1", "|": "1",
                                   "B": "8", "S": "5", "T": "7", "Z": "2", "-": ".", ",": ".", ":": "."}))
    s = re.sub(r"[^\d.]", "", s)
    s = re.sub(r"\.+", ".", s).strip(".")
    if not s:
        return None
    if "." in s:
        whole, _, frac = s.rpartition(".")
        whole = whole.replace(".", "")
        if len(frac) == 2 and whole:
            return f"{int(whole)}.{frac}"
        if len(frac) > 2:
            return None
        return None
    if len(s) >= 3:                       # decimal point lost: '765485' -> '7654.85'
        return f"{int(s[:-2])}.{s[-2:]}"
    return None


def _vote(readings: list[str | None]) -> tuple[str | None, int]:
    """Majority vote over normalized rate strings. Returns (value, n_agreeing)."""
    vals = [r for r in readings if r]
    if not vals:
        return None, 0
    c = Counter(vals)
    best, n = c.most_common(1)[0]
    if n >= 2:
        return best, n
    # the dominant OCR failure on these scans is a dropped digit: if one reading is another
    # reading plus exactly one digit, prefer the longer one (still reported as unconfirmed)
    for longer in sorted(vals, key=len, reverse=True):
        for shorter in vals:
            if len(longer) == len(shorter) + 1 and _is_subsequence(shorter, longer):
                return longer, 1
    # digit-wise vote among same-length readings
    by_len = Counter(len(v) for v in vals)
    L, cnt = by_len.most_common(1)[0]
    if cnt >= 3:
        same = [v for v in vals if len(v) == L]
        digits = "".join(Counter(col).most_common(1)[0][0] for col in zip(*same))
        return digits, 1
    return vals[0], 1


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


def _similar(a: str, b: str) -> float:
    if a == b:
        return 2.0
    if len(a) == len(b) and sum(x != y for x, y in zip(a, b)) <= 2:
        return 1.0
    if difflib.SequenceMatcher(None, a, b).ratio() >= 0.8:
        return 0.5
    return -1.0


def align(seq_a: list[str], seq_b: list[str], band: int = 120) -> dict[int, int]:
    """Banded Needleman-Wunsch alignment of two rate sequences. Returns {index_a: index_b}."""
    n, m = len(seq_a), len(seq_b)
    if not n or not m:
        return {}
    gap = -0.6
    NEG = -1e9
    score = [dict() for _ in range(n + 1)]
    back = [dict() for _ in range(n + 1)]
    ratio = m / n
    for i in range(n + 1):
        center = int(i * ratio)
        for j in range(max(0, center - band), min(m, center + band) + 1):
            if i == 0 and j == 0:
                score[0][0] = 0.0
                continue
            best, arg = NEG, None
            if i > 0 and j > 0 and (j - 1) in score[i - 1]:
                v = score[i - 1][j - 1] + _similar(seq_a[i - 1], seq_b[j - 1])
                if v > best:
                    best, arg = v, "d"
            if i > 0 and j in score[i - 1]:
                v = score[i - 1][j] + gap
                if v > best:
                    best, arg = v, "u"
            if j > 0 and (j - 1) in score[i]:
                v = score[i][j - 1] + gap
                if v > best:
                    best, arg = v, "l"
            if arg:
                score[i][j], back[i][j] = best, arg
    # backtrack from the best end cell in the last row
    i = n
    j = max(score[n], key=lambda k: score[n][k]) if score[n] else 0
    out = {}
    while i > 0 and j >= 0 and j in back[i]:
        a = back[i][j]
        if a == "d":
            if _similar(seq_a[i - 1], seq_b[j - 1]) > 0:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif a == "u":
            i -= 1
        else:
            j -= 1
    return out


# ------------------------------------------------------------------ alternate rate reading
def _alt_read_page(pdf_path: str, page_index: int, boxes: list[tuple[float, float, float, float]]) -> list[str]:
    """Digits-only re-OCR of rate cells, from the native image upscaled x2 and binarised."""
    import numpy as np
    import pytesseract

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    with pymupdf.open(pdf_path) as d:
        img, (ox, oy, sx, sy) = page_image(d, d[page_index])
    out = []
    for (x0, y0, x1, y1) in boxes:
        px0, py0 = int((x0 - ox) / sx) - 4, int((y0 - oy) / sy) - 3
        px1, py1 = int((x1 - ox) / sx) + 4, int((y1 - oy) / sy) + 3
        crop = img.crop((max(px0, 0), max(py0, 0), min(px1, img.width), min(py1, img.height)))
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
        a = np.array(crop)
        crop = Image.fromarray(((a > 170) * 255).astype("uint8"))
        canvas = Image.new("L", (crop.width + 40, crop.height + 40), 255)
        canvas.paste(crop, (20, 20))
        txt = pytesseract.image_to_string(canvas, config="--psm 7 -c tessedit_char_whitelist=0123456789.,")
        out.append(txt.strip())
    return out


# ------------------------------------------------------------------ page parsing
@dataclass
class Row:
    raw_code: str
    page: int
    section: str                    # "item" | "basic" | "coeff_cement" | "coeff_bitumen"
    head: int | None
    category: str | None
    group: str | None
    desc: list[str] = field(default_factory=list)
    unit_tokens: list[str] = field(default_factory=list)
    rate_tokens: list[str] = field(default_factory=list)
    rate_box: tuple[float, float, float, float] | None = None
    remarks: list[str] = field(default_factory=list)
    conf: float = 100.0
    rate_alt: str | None = None
    rate_hindi: str | None = None
    code: str | None = None
    code_cost: float = 0.0


@dataclass
class Columns:
    unit_x: float
    rate_x: float
    remarks_x: float | None
    code_max_x: float


def _header_columns(lines: list[Line]) -> Columns | None:
    for ln in lines[:14]:
        texts = [w.text.lower() for w in ln.words]
        if "unit" in texts and ("rate" in texts or "basic" in texts or "quantity" in texts):
            unit_w = ln.words[texts.index("unit")]
            rk = "rate" if "rate" in texts else ("basic" if "basic" in texts else "quantity")
            rate_w = ln.words[texts.index(rk)]
            rem = ln.words[texts.index("remarks")] if "remarks" in texts else None
            code_w = ln.words[0]
            return Columns(unit_x=unit_w.x0 - 18, rate_x=min(rate_w.x0, unit_w.x1 + 20) - 18,
                           remarks_x=(rem.x0 - 12) if rem else None, code_max_x=code_w.x0 + 70)
    return None


def _section_of(page_text: str, sub_heads: dict[int, str]) -> tuple[str | None, int | None, str | None]:
    """Detect (section, head, category) from header/footer text of an English page."""
    t = page_text.upper()
    if "COEFFICIENT" in t and "CEMENT CONSUMPTION" in t:
        m = re.search(r"[S$§&5]\.?\s*H\.?\s*[:;7]?\s*(\d{1,2})", t)
        return "coeff_cement", int(m.group(1)) if m else None, None
    if "COEFFICIENT" in t and "BITUMEN" in t:
        return "coeff_bitumen", None, None
    if "BASIC RATES" in t or "BASIC RATE" in t[:300]:
        cat = "materials" if "MATERIALS" in t else "labour" if "LABOUR" in t else (
            "hire_charges" if "HIRE CHARGES" in t else None)
        return "basic", None, cat
    m = re.search(r"[S5$][UO]?B\s*H?E?AD\s*[:;7]?\s*(\d{1,2})\s*[.,]\s*0\s+([A-Z &.,\-]+)", t)
    if m:
        title = m.group(2).strip()
        best = difflib.get_close_matches(title.title(), list(sub_heads.values()), n=1, cutoff=0.5)
        if best:
            head = next(k for k, v in sub_heads.items() if v == best[0])
            return "item", int(head), None
        return "item", int(m.group(1)), None
    m = re.match(r"^\s*(\d{1,2})\.0\s+([A-Z][A-Z &.,\-]+)$", page_text.strip().split("\n")[0].upper())
    if m:
        best = difflib.get_close_matches(m.group(2).title(), list(sub_heads.values()), n=1, cutoff=0.5)
        if best:
            return "item", int(next(k for k, v in sub_heads.items() if v == best[0])), None
    return None, None, None


def _is_code_token(words, left_margin: float, cols: Columns, head: int | None = None) -> bool:
    """Codes appear at three indents (x ~ margin, +55, +110 pt for X.Y / X.Y.Z / X.Y.Z.W)."""
    w = words[0]
    if w.x0 > left_margin + 150 or not CODE_TOKEN_RE.match(w.text):
        return False
    digits = re.sub(r"\D", "", w.text)
    if not digits:
        return False
    nxt = words[1] if len(words) > 1 else None
    if w.x0 < left_margin + 25:          # code column proper (parent / basic-rate codes)
        return True
    # deeper indents: must look like a code of the current sub-head and be followed by a wide gap
    if len(digits) < 2 or (head is not None and not digits.startswith(str(head))):
        return False
    return nxt is None or (nxt.x0 - w.x1) >= 12 or nxt.text in ("—", "-", "–", "_")


_LETTER_AS_HEAD = {"n": (11,), "N": (11,)}   # "a" for 8/9 is ambiguous ("a7" was 9.71) - left to the review queue


def _letter_code(tok: str, head: int | None, section: str | None) -> str | None:
    """OCR reads a code's leading sub-head number as a look-alike letter: 'n2' = 11.2, 'nT' = 11.7 (T = 7),
    basic rate 'TTT1' = 7771. Repaired only when the letter matches the current
    sub-head (basic rates: only when the result is a 4-digit code); the sequence decoder then places it."""
    if section == "basic":
        return tok.replace("T", "7") if re.fullmatch(r"[T7]{1,3}\d{1,3}", tok) and len(tok) == 4 and "T" in tok else None
    m = re.fullmatch(r"([nNaA])([\dTOoIl|.,]{1,6})", tok)
    if not m or head not in _LETTER_AS_HEAD.get(m.group(1), ()):
        return None
    rest = m.group(2).replace("T", "7")
    return f"{head}{rest}" if re.search(r"\d", rest) else None


def _uppercase_heading(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 4 and sum(c.isupper() for c in letters) / len(letters) > 0.85 and len(text) < 90


def parse_pages(doc: SourceDoc, pages: list[PageText], cfg: dict,
                code_tokens: dict | None = None) -> tuple[list[Row], list[dict]]:
    sub_heads = {int(k): v for k, v in (cfg.get("sub_heads") or {}).items()}
    rows: list[Row] = []
    notes: list[dict] = []
    section, head, category, group = None, None, None, None
    default_cols: Columns | None = None
    for page in pages:
        if not is_english(page):
            continue
        lines = page_lines(page, allow_columns=False)
        if len(lines) < 4:
            continue
        sec, h, cat = _section_of("\n".join(ln.text for ln in lines[:3] + lines[-2:]), sub_heads)
        if sec:
            if sec != section or h != head:
                group = None
            section, head = sec, (h if h is not None else head)
            category = cat or (category if sec == "basic" else None)
        if section is None:
            continue
        cols = _header_columns(lines) or default_cols
        if cols is None:
            continue
        default_cols = cols
        left_margin = min(ln.x0 for ln in lines)
        cur: Row | None = rows[-1] if rows and rows[-1].page == page.page - 2 else None  # continued item
        for ln in lines:
            t = ln.text.strip()
            tl = t.lower()
            if re.search(r"sub\s*head|basic rates\s*:|coefficients for|^code\b|^no\.?$|description\s+unit", tl):
                continue
            if re.fullmatch(r"[\d\s|(){}\[\].,-]{1,6}", t) or tl in ("z", "=", "₹", "rs"):
                continue
            words = ln.words
            fixed = None
            if words[0].x0 < left_margin + 25:          # code column only
                fixed = (code_tokens or {}).get(f"{doc.doc_id}:{page.page}:{words[0].text}") or \
                    _letter_code(words[0].text, head, section)
            if fixed:
                words = [words[0].model_copy(update={"text": fixed})] + words[1:]
            if _is_code_token(words, left_margin, cols, head if section != "basic" else None):
                cur = Row(raw_code=words[0].text, page=page.page, section=section, head=head,
                          category=category, group=group, conf=words[0].conf)
                rows.append(cur)
                words = words[1:]
                while words and words[0].text in ("—", "-", "–", "_", "=", "~"):
                    words = words[1:]
            elif _uppercase_heading(t) and (cur is None or cur.rate_tokens or section == "basic"):
                group = t.title()
                cur = None
                continue
            elif tl.startswith("note") and section == "item":
                notes.append({"head": head, "page": page.page, "text": t})
                cur = None
                continue
            if cur is None:
                if notes and notes[-1]["page"] == page.page:
                    notes[-1]["text"] += " " + t
                continue
            desc, unit, rate, rem = [], [], [], []
            for w in words:
                if cols.remarks_x and w.x0 >= cols.remarks_x:
                    rem.append(w)
                elif w.x0 >= cols.rate_x:
                    rate.append(w)
                elif w.x0 >= cols.unit_x:
                    unit.append(w)
                else:
                    desc.append(w)
            if desc:
                cur.desc.append(" ".join(w.text for w in desc))
            if unit:
                cur.unit_tokens += [w.text for w in unit]
            if rate:
                cur.rate_tokens = [w.text for w in rate]
                cur.rate_box = (min(w.x0 for w in rate), min(w.y0 for w in rate),
                                max(w.x1 for w in rate), max(w.y1 for w in rate))
                cur.conf = min(cur.conf, min(w.conf for w in rate))
            if rem:
                cur.remarks.append(" ".join(w.text for w in rem))
    return rows, notes


def hindi_rate_sequence(pages: list[PageText], rate_x: float) -> list[str]:
    seq = []
    for page in pages:
        if is_english(page) or len(page.words) < 15:
            continue
        for ln in page_lines(page, allow_columns=False):
            toks = [w.text for w in ln.words if w.x0 >= rate_x - 25]
            r = norm_rate("".join(toks)) if toks else None
            if r:
                seq.append(r)
    return seq


def _parent_chain(code: str, group_desc: dict[str, str]) -> list[str]:
    parents, p = [], parent_code(code)
    while p:
        if p in group_desc:
            parents.insert(0, group_desc[p])
        p = parent_code(p)
    return parents


# ------------------------------------------------------------------ main entry
@dataclass
class SorParseResult:
    items: list[SorItem]
    basic_rates: list[BasicRate]
    cement_coefficients: list[dict]
    notes: list[dict]
    stats: dict


def parse_sor(doc: SourceDoc, pages: list[PageText], alt_read: bool = True) -> SorParseResult:
    s = get_settings()
    cfg = load_sor_config(doc.sor_name or "CPWD-DSR", doc.revision_year)
    corrections = load_corrections(doc.sor_name or "CPWD-DSR", doc.revision_year)
    rows, notes = parse_pages(doc, pages, cfg, load_code_tokens(doc.sor_name or "CPWD-DSR", doc.revision_year))

    # ---- alternate digits-only reading of each rate cell (parallel per page)
    if alt_read:
        cache_file = s.interim_dir / "alt_rates" / f"{doc.doc_id}.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache = json.loads(cache_file.read_text()) if cache_file.exists() else {}
        key = lambda r: f"{r.page}:{','.join(f'{v:.0f}' for v in r.rate_box)}"  # noqa: E731
        by_page: dict[int, list[Row]] = {}
        for r in rows:
            if r.rate_box:
                if key(r) in cache:
                    r.rate_alt = norm_rate(cache[key(r)])
                else:
                    by_page.setdefault(r.page, []).append(r)
        if by_page:
            with ProcessPoolExecutor(max_workers=s.ocr_workers) as ex:
                futs = {pg: ex.submit(_alt_read_page, str(s.root / doc.path), pg - 1, [r.rate_box for r in rs])
                        for pg, rs in by_page.items()}
                for pg, fut in futs.items():
                    for r, txt in zip(by_page[pg], fut.result()):
                        cache[key(r)] = txt
                        r.rate_alt = norm_rate(txt)
            cache_file.write_text(json.dumps(cache))

    # ---- Hindi pages as an independent reading, aligned over the whole volume
    rated = [r for r in rows if r.rate_tokens]
    eng_seq = [norm_rate("".join(r.rate_tokens)) or "?" for r in rated]
    rate_x = statistics.median([r.rate_box[0] for r in rated]) if rated else 450
    hin_seq = hindi_rate_sequence(pages, rate_x)
    mapping = align(eng_seq, hin_seq)
    for i, r in enumerate(rated):
        if i in mapping:
            r.rate_hindi = hin_seq[mapping[i]]

    # ---- decode item codes per sub-head
    seg_start = 0
    item_rows = [r for r in rows if r.section in ("item", "coeff_cement")]
    for i in range(1, len(item_rows) + 1):
        if i == len(item_rows) or (item_rows[i].head, item_rows[i].section) != (item_rows[seg_start].head,
                                                                               item_rows[seg_start].section):
            seg = item_rows[seg_start:i]
            decoded = decode_sequence([r.raw_code for r in seg], seg[0].head, [bool(r.rate_tokens) for r in seg])
            for r, (code, cost) in zip(seg, decoded):
                r.code, r.code_cost = code, cost
            # group-heading rows (no rate) take the parent code of the following item when consistent
            for j, r in enumerate(seg):
                if r.rate_tokens or j + 1 >= len(seg):
                    continue
                nxt = seg[j + 1].code or ""
                par = parent_code(nxt) if nxt else None
                digits, pdig = re.sub(r"\D", "", r.raw_code), re.sub(r"\D", "", par or "")
                # "113" -> 11.3, and also "13" when OCR dropped the leading "1." of "11.3"
                if par and (digits.startswith(pdig) or (len(digits) >= 2 and pdig.endswith(digits))) and r.code != par and \
                        not (r.code or "").startswith(par + "."):
                    r.code, r.code_cost = par, min(r.code_cost, 1.0)
            seg_start = i

    sor_name, year = doc.sor_name or "CPWD-DSR", doc.revision_year
    items: list[SorItem] = []
    basic: list[BasicRate] = []
    basic_seen: dict[str, int] = {}
    coeffs: list[dict] = []
    by_code: dict[str, SorItem] = {}
    group_desc: dict[str, str] = {}
    for r in rows:
        readings = [norm_rate("".join(r.rate_tokens)) if r.rate_tokens else None, r.rate_alt, r.rate_hindi]
        rate_s, agree = _vote(readings)
        reasons = []
        if r.rate_tokens and agree < 2:
            reasons.append(f"rate readings disagree {readings}")
        desc = re.sub(r"\s+", " ", " ".join(r.desc)).strip(" -—:;")
        unit_raw = " ".join(r.unit_tokens).strip() or None
        unit = normalize_unit(unit_raw)
        # word-by-word fallback strips OCR junk ("© cum", "sqm sqm"), but never drops a leading count ("10 Nos"
        # is not "each") or picks one word out of a compound unit ("per cm depth per cm width per metre length")
        if unit is None and r.unit_tokens and not re.fullmatch(r"\d+", r.unit_tokens[0]) \
                and len(re.findall(r"per", unit_raw, re.I)) < 2:
            unit = next((u for u in map(normalize_unit, r.unit_tokens) if u), None)
        if r.section == "basic":
            code = re.sub(r"[^\d]", "", r.raw_code.replace("O", "0").replace("o", "0").replace("I", "1")
                          .replace("l", "1").replace("|", "1"))
            if rate_s is None:
                reasons.append("no rate")
            if unit_raw and not unit:
                reasons.append(f"unknown unit '{unit_raw}'")
            base = f"{sor_name}-{year}:BR:{code}"
            # OCR can give several rows the same code: the n-th is keyed "<code>#n" here and in the docstore, and a
            # correction for it uses that key. A plain-key correction is for the first row, except "exclude",
            # which removes every row with that code (junk rows usually repeat).
            n = basic_seen[base] = basic_seen.get(base, 0) + 1
            key = base if n == 1 else f"{base}#{n}"
            corr = corrections.get(key) or (corrections.get(base, {}) if n == 1 else {})
            if corrections.get(base, {}).get("exclude") or corr.get("exclude"):
                continue
            if corr.get("code"):
                code = str(corr["code"])
                key = f"{sor_name}-{year}:BR:{code}"
            basic.append(BasicRate(
                key=key, sor_name=sor_name, sor_year=year, category=r.category or "unknown", code=code,
                description=corr.get("description", desc), unit=corr.get("unit", unit), unit_raw=unit_raw,
                rate=float(corr.get("rate", rate_s)) if (corr.get("rate") or rate_s) else None,
                rate_raw=" ".join(r.rate_tokens), page=r.page, doc_id=doc.doc_id,
                needs_review=bool(reasons) and not corr, review_reasons=[] if corr else reasons))
            continue
        code = r.code or r.raw_code
        if r.code_cost >= REVIEW_COST:
            reasons.append(f"item code uncertain (raw '{r.raw_code}' -> '{code}')")
        if r.section == "coeff_cement":
            if rate_s:
                coeffs.append({"sor_name": sor_name, "sor_year": year, "code": code, "description": desc,
                               "unit": unit, "cement_quintal_per_unit": float(rate_s), "page": r.page,
                               "doc_id": doc.doc_id, "confirmed": agree >= 2, "review_reasons": reasons})
            continue
        if r.section != "item":
            continue
        if not r.rate_tokens:
            group_desc[code] = desc
            continue
        if unit is None:
            reasons.append(f"unit not recognised ('{unit_raw}')")
        key = canonical_key(sor_name, year, code)
        dup = key in by_code
        final_key = f"{key}#p{r.page}" if dup else key        # duplicate code after decoding -> keep both
        corr = corrections.get(final_key, {})
        if corr.get("exclude"):          # reviewer marked this row as an OCR artefact
            by_code.setdefault(key, None)
            continue
        if dup and not corr:
            reasons.append("duplicate item code after OCR decoding")
        out_code = str(corr.get("code") or code)             # human-corrected item code
        # parent description chain from the decoded code (headings and items are decoded together, so they agree);
        # only when that finds nothing, from the human-corrected code (e.g. 4.20A.1.1, 5.33A.2.1)
        parents = _parent_chain(code, group_desc) or _parent_chain(out_code, group_desc)
        item = SorItem(
            item_key=final_key, sor_name=sor_name, sor_year=year, doc_id=doc.doc_id, code=out_code, raw_code=r.raw_code,
            sub_head=f"{r.head}.0 {cfg.get('sub_heads', {}).get(r.head, '')}".strip() if r.head else None,
            group_heading=r.group, parent_code=parent_code(out_code),
            parent_description=" ".join(parents) or None, description=corr.get("description", desc),
            full_description=" ".join(parents + [corr.get("description", desc)]).strip(),
            unit=corr.get("unit", unit), unit_raw=unit_raw,
            rate=float(corr["rate"]) if corr.get("rate") else (float(rate_s) if rate_s else None),
            rate_raw=" ".join(r.rate_tokens), page=r.page, region=doc.region, min_conf=r.conf,
            rate_votes=agree, rate_readings=readings,
            needs_review=bool(reasons) and not corr, review_reasons=[] if corr else reasons)
        by_code.setdefault(key, item)
        items.append(item)

    # ---- sibling outlier check (same parent and unit)
    sib: dict[tuple, list[SorItem]] = {}
    for it in items:
        if it.rate and it.parent_code:
            sib.setdefault((it.parent_code, it.unit), []).append(it)
    for group_items in sib.values():
        if len(group_items) < 3:
            continue
        med = statistics.median(x.rate for x in group_items)
        for x in group_items:
            if med > 0 and not (0.4 <= x.rate / med <= 2.5) and x.rate_votes < 3 and x.item_key not in corrections:
                x.needs_review = True
                x.review_reasons.append(f"rate {x.rate} is an outlier vs siblings (median {med:.2f})")

    stats = {
        "rows": len(rows),
        "items": len(items),
        "items_rate_confirmed": sum(1 for it in items if not any("rate readings" in r for r in it.review_reasons)),
        "items_needing_review": sum(1 for it in items if it.needs_review),
        "basic_rates": len(basic),
        "basic_needing_review": sum(1 for b in basic if b.needs_review),
        "cement_coefficients": len(coeffs),
        "hindi_aligned": len(mapping),
        "english_rate_rows": len(rated),
        "corrections_applied": sum(1 for it in items if it.item_key in corrections),
    }
    return SorParseResult(items=items, basic_rates=basic, cement_coefficients=coeffs, notes=notes, stats=stats)
