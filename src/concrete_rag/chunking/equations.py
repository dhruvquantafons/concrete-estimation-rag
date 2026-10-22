"""Page-checked content added to chunks of scanned codes, beside their (unchanged) OCR text.

- Curated equations (config/equations/*.yaml) -> their clause chunks. The OCR of printed formulas is unreadable
  (IS 456 cl. 26.2.1 "L = 95s").
- Curated code-table values (config/code_tables/*.yaml) -> their source chunks. OCR of some tables is garbled
  (IS 456 Table 20, cl. 26.2.1.1 bond stress), and answers were built from the garbled numbers.
"""
from __future__ import annotations

from functools import lru_cache

import yaml

from concrete_rag.config import get_settings


@lru_cache
def by_clause() -> dict[tuple[str, str], list[dict]]:
    """(doc_id, clause_no) -> equations."""
    out: dict[tuple[str, str], list[dict]] = {}
    folder = get_settings().root / "config" / "equations"
    for f in sorted(folder.glob("*.yaml")):
        raw = yaml.safe_load(f.read_text()) or {}
        for e in raw.get("equations") or []:
            e = {"checked_by": raw.get("checked_by"), "checked_on": raw.get("checked_on"), **e}
            out.setdefault((e["doc_id"], str(e["clause"])), []).append(e)
    return out


def for_clause(doc_id: str, clause_no: str | None) -> list[dict]:
    return by_clause().get((doc_id, str(clause_no)), []) if clause_no else []


def block(eqs: list[dict]) -> str:
    """Text appended to the chunk: readable form first (for search and the LLM), then where it was checked."""
    lines = ["Equations (read from the scanned page, " + _status(eqs) + "):"]
    for e in eqs:
        lines.append(f"- {' '.join(str(e['text']).split())}")
    return "\n".join(lines)


def _status(eqs: list[dict]) -> str:
    by = {str(e.get("checked_by") or "") for e in eqs}
    return "AI-checked, not yet signed off" if all(b.lower().startswith("claude-vision") for b in by) else "checked"


def table_blocks() -> dict[str, str]:
    """chunk_id -> 'Checked values' block for each curated code table that names a source chunk."""
    from concrete_rag.calc import code_tables as ct
    out = {}
    for t in ct.tables().values():
        cid = (t.get("source") or {}).get("chunk")
        rows = _table_lines(t)
        if cid and rows:
            status = "AI-checked, not yet signed off" if ct.signed_status(t.get("verified_by")) != "verified" else \
                f"signed off by {t['verified_by']}"
            out[cid] = "\n".join([f"Checked values ({status}): {t.get('title', '')}"] + rows)
    return out


def _grade_label(g: str, grades: dict) -> str:
    """IS 456 Tables 19 / 20 and cl. 26.2.1.1 print the last column as 'M 40 and above'."""
    return f"{g} and above" if g == "M40" and g == list(grades)[-1] else g


def _table_lines(t: dict) -> list[str]:
    if "rows" in t and "columns" in t:
        return [" | ".join(t["columns"])] + [" | ".join("-" if v is None else str(v) for v in r) for r in t["rows"]]
    if "pt" in t and "grades" in t:                       # IS 456 Table 19
        return ["100As/bd | " + " | ".join(_grade_label(g, t["grades"]) for g in t["grades"])] + [
            f"{p} | " + " | ".join(str(col[i]) for col in t["grades"].values()) for i, p in enumerate(t["pt"])]
    if "grades" in t:
        return ["; ".join(f"{_grade_label(g, t['grades'])}: {v}" for g, v in t["grades"].items())]
    if "by_size_mm" in t:
        cols = t.get("zones")
        head = [" | ".join(["size mm"] + cols)] if cols else []
        return head + [f"{k} mm: " + (" | ".join(map(str, v)) if isinstance(v, list) else str(v))
                       for k, v in t["by_size_mm"].items()]
    if "bands" in t:
        return [f"M{lo} to M{hi}: {v}" if hi < 999 else f"M{lo} and above: {v}" for lo, hi, v in t["bands"]]
    if "xu_max_by_fy" in t:
        return ["fy | xu,max/d"] + [f"{fy} | {v}" for fy, v in t["xu_max_by_fy"].items()]
    return []
