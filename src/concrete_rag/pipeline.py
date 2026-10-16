"""End-to-end ingestion: registry -> page extraction/OCR -> parsing -> chunking -> docstore,
plus processed artefacts (chunks.jsonl, sor_items.csv, review queue CSVs, ingest report)."""
from __future__ import annotations

import csv
import json
from collections import Counter

from concrete_rag.chunking.chunker import chunk_is_code, chunk_sor
from concrete_rag.config import get_settings
from concrete_rag.ingest.pages import extract_document
from concrete_rag.parse.is_code import parse_document
from concrete_rag.parse.sor import parse_sor
from concrete_rag.registry import load_sources, update_manifest
from concrete_rag.schemas import SourceDoc
from concrete_rag.store import docstore


def ingest_document(doc: SourceDoc, force_ocr: bool = False, alt_read: bool = True, log=print) -> dict:
    pages = extract_document(doc, force=force_ocr,
                             progress=lambda d, n: log(f"  OCR {doc.doc_id}: {d}/{n}") if d % 50 == 0 else None)
    if doc.doc_type == "sor":
        res = parse_sor(doc, pages, alt_read=alt_read)
        chunks = chunk_sor(doc, res)
        stats = {**res.stats, "pages": len(pages), "chunks": len(chunks)}
        docstore.replace_document(doc.doc_id, doc.model_dump(), doc.sha256, stats, chunks, res.items,
                                  res.basic_rates, res.cement_coefficients)
    else:
        units, kinds = parse_document(doc, pages)
        chunks = chunk_is_code(doc, units)
        stats = {"pages": len(pages), "ocr_pages": sum(p.method == "ocr" for p in pages),
                 "page_kinds": dict(Counter(kinds.values())), "chunks": len(chunks),
                 "chunk_types": dict(Counter(c.chunk_type for c in chunks)),
                 "needs_review": sum(c.metadata.needs_review for c in chunks)}
        docstore.replace_document(doc.doc_id, doc.model_dump(), doc.sha256, stats, chunks)
    log(f"  {doc.doc_id}: {stats}")
    return stats


def ingest(only: list[str] | None = None, force_ocr: bool = False, alt_read: bool = True, log=print) -> dict:
    docs = load_sources(only)
    update_manifest(docs)
    report = {}
    # IS codes first (fast), SORs last
    for d in sorted(docs, key=lambda d: d.doc_type == "sor"):
        log(f"[ingest] {d.doc_id} ({d.path})")
        report[d.doc_id] = ingest_document(d, force_ocr=force_ocr, alt_read=alt_read, log=log)
    export_processed()
    s = get_settings()
    (s.processed_dir / "ingest_report.json").write_text(json.dumps(report, indent=2))
    return report


def export_processed() -> dict:
    """Write inspectable artefacts and the human review queue."""
    s = get_settings()
    out = s.processed_dir
    chunks = docstore.all_chunks()
    with open(out / "chunks.jsonl", "w") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    q = docstore.review_queue()
    items = docstore.search_sor_items(limit=100000)
    with open(out / "sor_items.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_key", "code", "sub_head", "unit", "rate", "rate_votes", "needs_review", "page", "description"])
        for it in sorted(items, key=lambda x: (x["doc_id"], x["page"])):
            w.writerow([it["item_key"], it["code"], it.get("sub_head"), it.get("unit"), it.get("rate"),
                        it.get("rate_votes"), it["needs_review"], it["page"], it["full_description"][:300]])
    with open(out / "review_sor_items.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["item_key", "doc_id", "pdf_page", "parsed_code", "raw_code", "parsed_unit", "parsed_rate",
                    "ocr_readings", "reasons", "description", "corrected_code", "corrected_unit", "corrected_rate"])
        for it in sorted(q["sor_items"], key=lambda x: (x["doc_id"], x["page"])):
            w.writerow([it["item_key"], it["doc_id"], it["page"], it["code"], it["raw_code"], it.get("unit"),
                        it.get("rate"), " / ".join(str(r) for r in it.get("rate_readings") or []),
                        "; ".join(it["review_reasons"]), it["description"][:200], "", "", ""])
    with open(out / "review_basic_rates.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "doc_id", "pdf_page", "category", "code", "description", "unit", "rate", "reasons",
                    "corrected_rate", "corrected_unit"])
        for b in sorted(q["basic_rates"], key=lambda x: (x["doc_id"], x["page"])):
            w.writerow([b["key"], b["doc_id"], b["page"], b["category"], b["code"], b["description"][:150],
                        b.get("unit"), b.get("rate"), "; ".join(b["review_reasons"]), "", ""])
    with open(out / "review_is_chunks.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["chunk_id", "doc_id", "pages", "reasons", "text_preview"])
        for c in q["chunks"]:
            m = c["metadata"]
            w.writerow([c["chunk_id"], m["doc_id"], f"{m['page_start']}-{m['page_end']}",
                        "; ".join(m.get("review_reasons") or []), c["text"][:200].replace("\n", " ")])
    return {"chunks": len(chunks), "review_items": len(q["sor_items"]), "review_basic": len(q["basic_rates"]),
            "review_chunks": len(q["chunks"])}


def index(log=print) -> int:
    from concrete_rag.store import vector
    chunks = docstore.all_chunks()
    log(f"[index] embedding {len(chunks)} chunks with {get_settings().embed_model} ...")
    n = vector.index_chunks(chunks, progress=lambda d, t: log(f"  {d}/{t}") if d % 512 == 0 or d == t else None)
    log(f"[index] done: {vector.collection_info()}")
    return n
