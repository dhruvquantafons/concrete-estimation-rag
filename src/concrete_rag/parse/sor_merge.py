"""Merge the scanned DSR with a clean copy of the original printing; the CLEAN COPY HAS PRIORITY.

The scans in data/CPWD SOR are the corrected edition (w.e.f. 16.10.2023, corrections until 10.11.2023) but are
96-dpi OCR. data/CPWD SOR HQ is a clean copy of the ORIGINAL printing (July/Aug 2023). By the owner's decision
(2026-10-28) whatever the clean copy prints wins: code, description, unit and rate. Per item:

  both have it, same rate      -> confirmed (source "scan+clean")
  both have it, rates differ   -> the clean copy's rate (source "clean_over_scan", the scan's kept as scan_rate);
                                  listed for review only when no scan reading and no DAR analysis confirms the
                                  clean copy's reading
  scan code mis-decoded        -> ("13.10.0.1" for 13.100.1) renamed to the clean copy's code
  only the clean copy has it   -> added (the scan's OCR missed it), marked source "clean"
  only the scan has it         -> kept and listed for review (an OCR artefact, a mis-read code, or an item a later
                                  correction slip added)

Reviewer corrections (config/corrections) checked before CLEAN_COPY_SINCE were made by reading the scan: the clean
copy overrides them. Corrections checked on or after it (made against the clean copy, or a deliberate override in
the review UI) win over the clean copy. Cement-consumption coefficients come from the clean copy; scan-only
coefficients are kept, unconfirmed.
"""
from __future__ import annotations

import difflib
import re

from concrete_rag.chunking.enrich import tag
from concrete_rag.normalize.item_codes import parent_code
from concrete_rag.parse.sor import SorParseResult
from concrete_rag.schemas import SorItem

CLEAN_COPY_SINCE = "2026-10-28"
NOT_IN_CLEAN = "not in the clean copy (HQ)"


def _key(code: str) -> str:
    return re.sub(r"\W", "", code)                    # "13.10.0.1" and "13.100.1" -> "131001"


def _head(code: str) -> str:
    return code.split(".")[0]


def _dar_rate(code: str) -> float | None:
    try:
        from concrete_rag.store import docstore
        a = docstore.get_dar_analysis(code)
        return a.get("rate") if a else None
    except Exception:  # noqa: BLE001 - DAR not ingested yet
        return None


def is_fresh(correction: dict) -> bool:
    """Checked against the clean copy (or a deliberate override of it), so it wins over the clean copy."""
    return str(correction.get("checked_on") or "") >= CLEAN_COPY_SINCE


def _similar(a: str, b: str) -> bool:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.9


def _same(reading: str | None, rate: float | None) -> bool:
    try:
        return reading is not None and rate is not None and abs(float(reading) - rate) < 0.005
    except ValueError:
        return False


def apply_corrections(res: SorParseResult, corrections: dict, from_clean: set[str] | None = None) -> dict:
    """Reviewer corrections keyed by the FINAL item key (recoded or clean-copy items get new keys, so the scan
    parser could not apply them). Items in `from_clean` take only fresh corrections; an older one is reported in
    `overridden` when it disagrees with the clean copy. Idempotent for corrections it already applied."""
    from_clean = from_clean or set()
    n, keep, overridden = 0, [], []
    for it in res.items:
        c = corrections.get(it.item_key)
        if c and it.item_key in from_clean and not is_fresh(c):
            diff = {f: (c[f], getattr(it, f)) for f in ("rate", "unit", "code")
                    if c.get(f) is not None and str(c[f]) != str(getattr(it, f))}
            if diff:
                overridden.append({"item_key": it.item_key, **{f: f"{a} -> {b}" for f, (a, b) in diff.items()}})
            c = None
        if c and c.get("exclude"):
            n += 1
            continue
        if c:
            if c.get("rate") is not None:
                it.rate = float(c["rate"])
            if c.get("unit"):
                it.unit = c["unit"]
            if c.get("code"):
                it.code, it.parent_code = str(c["code"]), parent_code(str(c["code"]))
            if c.get("description"):
                it.description = c["description"]
            # an older scan-based check does not settle an item the clean copy does not have
            it.review_reasons = [] if is_fresh(c) else [r for r in it.review_reasons if r.startswith(NOT_IN_CLEAN)]
            it.needs_review = bool(it.review_reasons)
            n += 1
        keep.append(it)
    res.items = keep
    return {"applied": n, "overridden": overridden}


def _take_clean(it: SorItem, n: SorItem) -> None:
    """Everything printed in the clean copy replaces the scan's reading."""
    it.code, it.parent_code = n.code, n.parent_code
    it.description, it.full_description = n.description, n.full_description
    it.parent_description, it.group_heading = n.parent_description, n.group_heading or it.group_heading
    it.material = tag(n.full_description)["material_type"]
    it.unit, it.unit_raw = n.unit, n.unit_raw
    it.rate, it.rate_raw = n.rate, n.rate_raw
    it.clean_rate, it.clean_doc_id, it.clean_page = n.rate, n.doc_id, n.page


def _dar_basic_rate(sor_year: int, code: str) -> float | None:
    try:
        from sqlalchemy import select

        from concrete_rag.store.docstore import basic_rates, engine
        with engine().connect() as c:
            r = c.execute(select(basic_rates.c.rate).where(basic_rates.c.key == f"CPWD-DAR-{sor_year}:BR:{code}")).first()
        return r[0] if r else None
    except Exception:  # noqa: BLE001 - DAR not ingested yet
        return None


def merge_basic_rates(res: SorParseResult, clean: SorParseResult, doc_id: str, corrections: dict) -> dict:
    """Basic rates by the same rules as items: the clean copy's code, description, unit and rate win; further scan
    rows of a code (OCR duplicates) are dropped; scan-only codes are listed for review."""
    if not clean.basic_rates:
        return {}
    stats = {"confirmed": 0, "rate_differs": 0, "rate_differs_unconfirmed": 0, "added_from_clean": 0,
             "scan_only": 0, "scan_duplicates_dropped": 0, "old_corrections_overridden_by_clean": []}
    clean_by = {b.code: b for b in clean.basic_rates}
    used: set[str] = set()
    out = []
    for b in res.basic_rates:
        n = clean_by.get(b.code)
        if n is None:
            if any(c.rate == b.rate and _similar(c.description, b.description) for c in clean_by.values()):
                stats["scan_duplicates_dropped"] += 1
                continue
            stats["scan_only"] += 1
            b.needs_review = True
            b.review_reasons = [r for r in b.review_reasons if not r.startswith(NOT_IN_CLEAN)] + [
                f"{NOT_IN_CLEAN} - an OCR artefact or a mis-read code: check the scan page"]
            out.append(b)
            continue
        if b.code in used:
            stats["scan_duplicates_dropped"] += 1
            continue
        used.add(b.code)
        c = corrections.get(b.key)
        if c and not is_fresh(c) and c.get("rate") is not None and float(c["rate"]) != n.rate:
            stats["old_corrections_overridden_by_clean"].append({"key": b.key, "rate": f"{c['rate']} -> {n.rate}"})
        scan_rate = b.rate
        b.description, b.unit, b.unit_raw, b.rate, b.rate_raw = n.description, n.unit, n.unit_raw, n.rate, n.rate_raw
        b.category, b.clean_doc_id, b.clean_page = n.category, n.doc_id, n.page
        b.review_reasons = list(n.review_reasons)
        if scan_rate == n.rate:
            stats["confirmed"] += 1
            b.source = "scan+clean"
        else:
            stats["rate_differs"] += 1
            b.source, b.scan_rate = "clean_over_scan", scan_rate
            dar = _dar_basic_rate(b.sor_year, b.code)
            if dar != n.rate:
                stats["rate_differs_unconfirmed"] += 1
                b.review_reasons.append(f"clean copy reads {n.rate} (used); the scan reads {scan_rate}"
                                        + (f"; DAR {dar}" if dar is not None else "") + " - check the clean copy")
        if c and is_fresh(c):                            # checked against the clean copy: it wins
            b.rate = float(c["rate"]) if c.get("rate") is not None else b.rate
            b.unit = c.get("unit") or b.unit
            b.description = c.get("description") or b.description
            b.review_reasons = []
        b.needs_review = bool(b.review_reasons)
        out.append(b)
    for code, n in clean_by.items():
        if code not in used:
            n.source, n.clean_doc_id, n.clean_page, n.doc_id = "clean", n.doc_id, n.page, doc_id
            out.append(n)
            stats["added_from_clean"] += 1
    res.basic_rates = out
    return stats


def merge_clean_copy(res: SorParseResult, clean: SorParseResult, doc_id: str,
                     corrections: dict | None = None) -> dict:
    """Merge `clean` (original printing) into `res` (corrected scan) in place. Returns counts for the ingest
    report."""
    corrections = corrections or {}
    stats = {"confirmed": 0, "rate_differs": 0, "rate_differs_unconfirmed": 0, "recoded": 0, "added_from_clean": 0,
             "scan_only": 0, "unit_from_clean": 0}
    clean_by = {it.code: it for it in clean.items}
    used: set[str] = set()
    scan_codes = {it.code for it in res.items}
    from_clean: set[str] = set()
    scan_only: list[SorItem] = []

    def twin(it: SorItem) -> str | None:
        free = [c for c in clean_by if c not in scan_codes and c not in used and _head(c) == _head(it.code)]
        same_digits = [c for c in free if _key(c) == _key(it.code)]
        if len(same_digits) == 1:
            return same_digits[0]
        same_rate = [c for c in free if it.rate is not None and clean_by[c].rate == it.rate]
        return same_rate[0] if len(same_rate) == 1 else None

    for it in res.items:
        code = None
        if "#" not in it.item_key:                       # a duplicate row of the scan: the first one is matched
            code = it.code if it.code in clean_by and it.code not in used else twin(it)
        if code is None:
            scan_only.append(it)
            continue
        if code != it.code:
            prefix = it.item_key.split(":")[0]
            it.item_key = f"{prefix}:{code}"
            stats["recoded"] += 1
        used.add(code)
        n = clean_by[code]
        scan_rate, scan_unit = it.rate, it.unit
        readings = (list(it.rate_readings) + [None, None])[:2] + [n.rate_raw]
        _take_clean(it, n)
        stats["unit_from_clean"] += bool(n.unit and n.unit != scan_unit)
        it.rate_readings = readings
        it.rate_votes = sum(_same(r, n.rate) for r in readings) if n.rate is not None else 0
        it.review_reasons = list(n.review_reasons)       # the scan's OCR doubts no longer apply
        from_clean.add(it.item_key)
        if scan_rate == n.rate:
            stats["confirmed"] += 1
            it.source = "scan+clean"
        else:
            stats["rate_differs"] += 1
            it.source, it.scan_rate = "clean_over_scan", scan_rate
            dar = _dar_rate(code)
            if it.rate_votes < 2 and n.rate is not None and dar != n.rate:
                stats["rate_differs_unconfirmed"] += 1
                it.review_reasons.append(
                    f"clean copy reads {n.rate} (used); the scan (corrected edition, to 10.11.2023) reads {scan_rate}"
                    + (f"; DAR (Sep 2023) {dar}" if dar else "") + " - check the clean copy's reading")
        it.needs_review = bool(it.review_reasons)

    # items only the clean copy has (the scan's OCR missed them)
    keys = {it.item_key for it in res.items}
    for code, n in clean_by.items():
        if code in used:
            continue
        if n.item_key in keys:                      # a reviewer recoded the scan row but kept its key (5.4 -> 5.4A)
            n.item_key = f"{n.item_key}#clean"
        keys.add(n.item_key)
        n.source, n.clean_rate, n.clean_doc_id, n.clean_page = "clean", n.rate, n.doc_id, n.page
        n.doc_id, n.rate_votes = doc_id, 1          # belongs to the DSR volume; its page is in the clean copy
        n.rate_readings = [None, None, n.rate_raw]
        res.items.append(n)
        from_clean.add(n.item_key)
        stats["added_from_clean"] += 1

    # a scan row the clean copy does not have: a second OCR row of an item already merged (same rate, same code or
    # text) is dropped; anything else is kept for review
    clean_items = [it for it in res.items if it.item_key in from_clean]
    dropped = set()
    for it in scan_only:
        if any(c.rate == it.rate and (c.code == it.code or _similar(c.description, it.description))
               for c in clean_items):
            dropped.add(id(it))
            continue
        it.needs_review = True
        it.review_reasons.append(f"{NOT_IN_CLEAN} - an OCR artefact, a mis-read code, or an item added by a "
                                 f"correction slip after Aug 2023: check the scan page")
    res.items = [it for it in res.items if id(it) not in dropped]
    stats["scan_only"], stats["scan_duplicates_dropped"] = len(scan_only) - len(dropped), len(dropped)

    # a recoded or added item can land on the key of a scan row a reviewer recoded (key kept, code changed)
    seen: dict[str, int] = {}
    for it in res.items:
        k = seen[it.item_key] = seen.get(it.item_key, 0) + 1
        if k > 1:
            it.item_key = f"{it.item_key}#{k}"
            it.review_reasons.append("same code as another row after merging the clean copy - check")
            it.needs_review = True

    applied = apply_corrections(res, corrections, from_clean)
    stats["corrections_after_merge"] = applied["applied"]
    stats["old_corrections_overridden_by_clean"] = applied["overridden"]

    stats["basic_rates"] = merge_basic_rates(res, clean, doc_id, corrections)

    # cement-consumption coefficients: clean copy first, scan-only ones kept unconfirmed
    clean_co = {c["code"]: c for c in clean.cement_coefficients}
    co_keys = {_key(c) for c in clean_co}
    kept = [{**c, "confirmed": False} for c in res.cement_coefficients
            if c["code"] not in clean_co and _key(c["code"]) not in co_keys]
    stats["cement_coefficients_clean"], stats["cement_coefficients_scan_only"] = len(clean_co), len(kept)
    # stored with the DSR volume (so a re-ingest replaces them); the page refers to the clean copy
    res.cement_coefficients = [{**c, "doc_id": doc_id, "clean_doc_id": c["doc_id"], "source": "clean"}
                               for c in clean_co.values()] + kept
    return stats
