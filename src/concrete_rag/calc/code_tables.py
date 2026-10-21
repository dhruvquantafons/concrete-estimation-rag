"""Curated code tables (config/code_tables/*.yaml) with provenance and verification status.

Calculators never read numbers from OCR text. They read these curated tables, and every value they
use is returned with a `Ref` (code, table/clause, status). Status is:
  verified          - a human compared the table with the licensed standard (verified_by set)
  ocr_confirmed     - every row was found in the OCR'd source table text (automatic cross-check)
  partially_confirmed / unconfirmed - some rows could not be matched -> human check required
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml
from pydantic import BaseModel

from concrete_rag.config import get_settings


class Ref(BaseModel):
    code: str
    item: str                   # "Table 5" / "cl. 26.4.2"
    value: Any = None
    status: str = "unconfirmed"
    note: str | None = None

    def label(self) -> str:
        return f"{self.code}, {self.item}"


@lru_cache
def tables() -> dict[str, dict]:
    out = {}
    for f in sorted((get_settings().root / "config" / "code_tables").glob("*.yaml")):
        for k, v in (yaml.safe_load(f.read_text()) or {}).items():
            out[k] = v
    return out


@lru_cache
def _verification() -> dict[str, dict]:
    try:
        return verify_all()
    except Exception:          # docstore not built yet
        return {}


def status(name: str) -> str:
    t = tables()[name]
    if t.get("verified_by"):
        return "verified"
    return _verification().get(name, {}).get("status", "unconfirmed")


def ref(name: str, value: Any = None, note: str | None = None) -> Ref:
    t = tables()[name]
    src = t["source"]
    if "table" in src:
        item = f"Table {src['table']}"
    elif "figure" in src:
        item = f"Fig. {src['figure']}"
    elif "clause" in src:
        item = f"cl. {src['clause']}"
    else:
        item = "cl. " + ", ".join(src.get("clauses", []))
    return Ref(code=src["code"], item=item, value=value, status=status(name), note=note)


# ------------------------------------------------------------------ verification against OCR text
def _nums(s: str) -> list[str]:
    s = s.replace(",", ".")
    return re.findall(r"\d+(?:\.\d+)?", s)


def _fmt(v: Any) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        m = re.match(r"^M\s?(\d+)$", v)
        return m.group(1) if m else None
    return f"{v:.2f}" if isinstance(v, float) and v < 10 else (f"{v:g}" if isinstance(v, float) else str(v))


def _row_in_text(values: list[str], lines: list[list[str]]) -> bool:
    """True if some line contains all values in order (allowing extra tokens in between)."""
    for toks in lines:
        it = iter(toks)
        if all(any(_eq(v, t) for t in it) for v in values):
            return True
    return False


def _eq(a: str, b: str) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except ValueError:
        return a == b


def _rows_of(name: str, t: dict) -> list[list[str]]:
    if "rows" in t:
        return [[x for x in map(_fmt, r) if x is not None] for r in t["rows"]]
    if "grades" in t and "pt" in t:          # Table 19: one row per pt
        gs = list(t["grades"].values())
        return [[_fmt(p)] + [_fmt(g[i]) for g in gs] for i, p in enumerate(t["pt"])]
    if "grades" in t:
        return [[_fmt(v) for v in t["grades"].values()]]
    if "by_size_mm" in t:
        rows = []
        for size, v in t["by_size_mm"].items():
            rows.append([str(size)] + ([_fmt(x) for x in v] if isinstance(v, list) else [_fmt(v)]))
        return rows
    if "bands" in t:
        return [[_fmt(b[2])] for b in t["bands"]]
    return []


def verify_all() -> dict[str, dict]:
    from concrete_rag.store import docstore
    report = {}
    for name, t in tables().items():
        chunk_id = t.get("source", {}).get("chunk")
        rows = _rows_of(name, t)
        if not chunk_id or not rows:
            report[name] = {"status": "verified" if t.get("verified_by") else "unconfirmed", "checked": 0,
                            "matched": 0, "mismatches": ["no automatic check possible (figure/clause text)"]}
            continue
        ch = docstore.get_chunks([chunk_id]).get(chunk_id)
        if not ch:
            report[name] = {"status": "unconfirmed", "checked": len(rows), "matched": 0,
                            "mismatches": [f"source chunk {chunk_id} not found"]}
            continue
        lines = [_nums(ln) for ln in ch["text"].split("\n")]
        lines.append(_nums(ch["text"]))          # whole text as one line for vertically split tables
        matched, mism = 0, []
        for r in rows:
            if _row_in_text(r, lines):
                matched += 1
            else:
                mism.append(" ".join(r))
        st = "verified" if t.get("verified_by") else (
            "ocr_confirmed" if matched == len(rows) else "partially_confirmed" if matched else "unconfirmed")
        report[name] = {"status": st, "checked": len(rows), "matched": matched, "mismatches": mism}
    return report


# ------------------------------------------------------------------ typed accessors
def grade_num(grade: str | int) -> int:
    if isinstance(grade, int):
        return grade
    m = re.match(r"^\s*M\s?(\d+)\s*$", str(grade), re.I)
    if not m:
        raise ValueError(f"bad concrete grade '{grade}' (expected e.g. 'M25')")
    return int(m.group(1))


def band_value(name: str, fck: int) -> tuple[float, Ref]:
    for lo, hi, v in tables()[name]["bands"]:
        if lo <= fck <= hi:
            return v, ref(name, v)
    raise ValueError(f"grade M{fck} outside {name}")


def exposure_row(exposure: str) -> dict:
    t = tables()["table_5_durability"]
    key = exposure.lower().replace(" ", "_").replace("-", "_")
    for r in t["rows"]:
        if r[0] == key:
            return dict(zip(t["columns"], r))
    raise ValueError(f"unknown exposure '{exposure}' (mild/moderate/severe/very_severe/extreme)")


def fig1_curve(cement_type: str) -> str:
    """IS 10262 Fig. 1 curve for a cement (Note 1: curves 1/2/3 for OPC 33/43/53; PPC/PSC use curve 2 by default)."""
    return "curve_3_opc53" if "53" in cement_type else "curve_1_opc33" if "33" in cement_type else "curve_2_opc43"


def fig1_range(cement_type: str) -> tuple[float, float]:
    pts = tables()["fig_1_wc_vs_strength"]["curves"].get(fig1_curve(cement_type)) or [[0, 0]]
    return pts[0][0], pts[-1][0]


def interp(x: float, xs: list[float], ys: list[float]) -> float:
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if x <= xs[i]:
            x0, x1, y0, y1 = xs[i - 1], xs[i], ys[i - 1], ys[i]
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return ys[-1]


def grade_key(fck: int, available: list[str]) -> str:
    """Nearest lower tabulated grade (conservative), capped at the highest column ('M40 and above')."""
    nums = sorted(int(g[1:]) for g in available)
    cands = [n for n in nums if n <= fck] or [nums[0]]
    return f"M{cands[-1]}"
