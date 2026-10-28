"""Rate lookup (SOR), location cost index, CPWD DAR analyses (cost-head split, re-pricing with other basic
rates) and DAR-style rate analysis from basic rates."""
from __future__ import annotations

import re
from collections import defaultdict
from functools import lru_cache

import yaml

from concrete_rag.config import get_settings
from concrete_rag.normalize.currency import apply_cost_index
from concrete_rag.normalize.units import convert_quantity
from concrete_rag.store import docstore


@lru_cache
def _cfg(name: str) -> dict:
    return yaml.safe_load((get_settings().root / "config" / "cost" / f"{name}.yaml").read_text()) or {}


def cost_index(location: str | None) -> tuple[float, str]:
    c = _cfg("cost_indices")
    loc = (location or "delhi").strip().lower()
    entry = (c.get("indices") or {}).get(loc)
    if not entry:
        raise ValueError(f"no cost index configured for '{location}'. Add it to config/cost/cost_indices.yaml "
                         f"(known: {', '.join((c.get('indices') or {}).keys())})")
    return float(entry["index"]), entry.get("ref", "")


def sor_edition(sor_name: str, year: int) -> str:
    """'CPWD-DSR 2023 (w.e.f. 16.10.2023, corrections to 10.11.2023)' from config/sor/."""
    from concrete_rag.parse.sor import load_sor_config
    c = load_sor_config(sor_name, year)
    dmy = lambda d: ".".join(reversed(str(d).split("-")))  # noqa: E731
    bits = ([f"w.e.f. {dmy(c['effective_from'])}"] if c.get("effective_from") else []) + \
        ([f"corrections to {dmy(c['corrections_upto'])}"] if c.get("corrections_upto") else [])
    return f"{sor_name} {year}" + (f" ({', '.join(bits)})" if bits else "")


def sor_rate(code: str, sor_name: str = "CPWD-DSR", year: int | None = None, location: str | None = None) -> dict:
    it = docstore.get_sor_item(code, sor_name, year)
    if not it:
        raise KeyError(f"SOR item {sor_name} {year or ''} {code} not found")
    idx, idx_ref = cost_index(location)
    rate = it["rate"]
    out = {
        "item_code": it["code"], "description": it["full_description"], "unit": it["unit"], "dsr_rate": rate,
        "cost_index": idx, "cost_index_ref": idx_ref,
        "rate": round(apply_cost_index(rate, idx), 2) if rate is not None else None,
        "citation": f"{sor_name} {it['sor_year']}, item {it['code']} [pdf p."
                    + (f"{it['clean_page']} of the clean copy (original Aug 2023 printing)]"
                       if it.get("source") == "clean_over_scan" else
                       f"{it['page']}" + (" of the original Aug 2023 printing" if it.get("source") == "clean" else "") + "]"),
        "sor_edition": sor_edition(sor_name, it["sor_year"]),
        "rate_confirmed": not it["needs_review"],
        "rate_votes": it.get("rate_votes", 0),
        "review_reasons": it["review_reasons"],
        "region": it.get("region"), "revision": it.get("revision"), "material": it.get("material") or [],
    }
    a = docstore.get_dar_analysis(it["code"], it["sor_year"])
    if a and a.get("rate") is not None:
        out["dar_rate"] = a["rate"]
        out["dar_match"] = abs(a["rate"] - (rate or 0)) <= 0.011
        out["dar_citation"] = f"CPWD DAR {a['sor_year']}, analysis of item {a['code']} [pdf p.{a['page_start']}]"
    return out


def find_sor_item(query: str, measured_unit: str | None = None, year: int | None = None, top_k: int = 8,
                  sub_head: str | None = None) -> dict:
    """Hybrid retrieval (dense + BM25 + rerank) over SOR item chunks, for work with no mapped item code.
    Returns the best item whose unit the measured quantity can be converted to, plus the alternatives,
    so a person can confirm the choice (it is always flagged for checking). Items under `sub_head`
    (e.g. "5" = RCC) rank ahead of look-alikes from other sub-heads (e.g. PCC in 4)."""
    from concrete_rag.retrieval.hybrid import retrieve
    filters = {"chunk_type": "sor_item", **({"sor_year": year} if year else {})}
    pack = retrieve(query, top_k=top_k, filters=filters, use_expand=False, use_exact=False)
    cands = []
    for c in pack.chunks:
        it = docstore.get_sor_item(c.metadata.get("item_code"), year=c.metadata.get("sor_year"))
        if not it or it.get("rate") is None:
            continue
        if measured_unit and it.get("unit"):
            try:
                convert_quantity(1.0, measured_unit, it["unit"])
            except ValueError:
                continue
        cands.append({"item_code": it["code"], "unit": it["unit"], "rate": it["rate"], "score": round(c.score, 3),
                      "description": it["full_description"][:200]})
    if sub_head:
        cands.sort(key=lambda c: not c["item_code"].startswith(f"{sub_head}."))     # stable: keeps rerank order
    if not cands:
        raise KeyError(f"no SOR item found by retrieval for '{query}'" + (f" in unit {measured_unit}" if measured_unit else ""))
    return {**cands[0], "alternatives": cands[1:4]}


def rate_analysis(template_name: str, allow_example: bool = False) -> dict:
    """Build up a rate from basic rates the way CPWD DAR does: W = M + L + T&P; X = W + water %;
    Y = X + X x GST factor; Z = Y + CPOH %; rate = Z + cess %, rounded to 0.05."""
    cfg = _cfg("rate_analysis")
    t = (cfg.get("templates") or {}).get(template_name)
    if not t:
        raise KeyError(f"rate-analysis template '{template_name}' not found in config/cost/rate_analysis.yaml")
    if t.get("status") == "example" and not allow_example:
        raise ValueError(f"template '{template_name}' is an EXAMPLE with placeholder coefficients; "
                         "fill it from DAR 2023 and remove status: example")
    lines, heads = [], {"material": 0.0, "labour": 0.0, "hire": 0.0}
    for head, key in (("material", "materials"), ("labour", "labour"), ("hire", "hire")):
        for code, qty in (t.get(key) or {}).items():
            br = docstore.get_basic_rate(str(code))
            if not br or br.get("rate") is None:
                raise KeyError(f"basic rate code {code} not found / no rate")
            amt = float(qty) * br["rate"]
            heads[head] += amt
            lines.append({"head": head, "code": code, "description": br["description"], "unit": br["unit"],
                          "qty": qty, "rate": br["rate"], "amount": round(amt, 2),
                          "rate_confirmed": not br["needs_review"]})
    base = sum(heads.values()) + float(t.get("sundries_lump") or 0)
    water = base * cfg["water_charges_percent"] / 100
    gst = (base + water) * cfg["gst_factor"]
    cpoh = (base + water + gst) * cfg["contractor_profit_overheads_percent"] / 100
    cess = (base + water + gst + cpoh) * cfg["cess_percent"] / 100
    total = base + water + gst + cpoh + cess
    return {"template": template_name, "unit": t.get("unit"), "lines": lines,
            "cost_heads": {k: round(v, 2) for k, v in heads.items()},
            "water_charges": round(water, 2), "gst": round(gst, 2), "cp_oh": round(cpoh, 2), "cess": round(cess, 2),
            "rate": _round05(total), "basis": cfg.get("source"),
            "settings_verified": bool(cfg.get("verified_by"))}


# ------------------------------------------------------------------ CPWD DAR analyses
BASE_HEADS = ("material", "labour", "equipment", "carriage", "sundries", "other")
OVERHEAD_HEADS = ("water_charges", "gst", "cpoh", "cess")


def _step_head(label: str) -> str:
    low = label.lower()
    return ("water_charges" if "water" in low else "gst" if "gst" in low else
            "cpoh" if "cpoh" in low or "profit" in low else "cess" if "cess" in low else "other")


def _pct(label: str) -> float | None:
    fm = re.search(r"factor\s*([\d.]+)", label)
    pm = re.search(r"([\d.]+)\s*%", label)
    return float(fm.group(1)) if fm else float(pm.group(1)) / 100 if pm else None


def dar_breakdown(code: str, year: int | None = None, _depth: int = 0) -> dict | None:
    """Split an item's DAR rate (per unit) into material / labour / equipment / carriage / sundries / other and
    water charges / GST / CPOH / cess, as CPWD built it. Sub-analyses ("Rate as per item 3.8", "Sub AR-1") are
    expanded into heads by their own analyses. The heads sum to the DAR rate."""
    a = docstore.get_dar_analysis(code, year)
    if not a or not a.get("rate"):
        return None
    heads: dict[str, float] = defaultdict(float)
    unexpanded = []
    for ln in a["lines"]:
        if ln["category"] == "sub_analysis":
            sub = dar_breakdown(ln["code"], year, _depth + 1) if _depth < 4 else None
            if sub:
                for k, v in sub["per_unit"].items():
                    heads[k] += ln["amount"] * v / sub["rate"]
                continue
            unexpanded.append(ln["code"])
            heads["other"] += ln["amount"]
        else:
            heads[ln["category"]] += ln["amount"]
    for st in a["steps"]:
        lab = st["label"].lower()
        if st.get("amount") is not None and lab.startswith(("add", "deduct", "less")):
            heads[_step_head(st["label"])] += -st["amount"] if lab.startswith(("deduct", "less")) else st["amount"]
    total = sum(heads.values())
    if total <= 0:
        return None
    scale = a["rate"] / total                       # per-quantity -> per unit, and the 'Say' rounding
    per_unit = {k: round(heads.get(k, 0.0) * scale, 4) for k in BASE_HEADS + OVERHEAD_HEADS if heads.get(k)}
    return {"code": a["code"], "rate": a["rate"], "unit": a.get("unit"), "per_unit": per_unit,
            "share_percent": {k: round(100 * v / a["rate"], 2) for k, v in per_unit.items()},
            "citation": f"CPWD DAR {a['sor_year']}, analysis of item {a['code']} [pdf p.{a['page_start']}-{a['page_end']}]",
            "needs_review": a["needs_review"], "dsr_match": a.get("dsr_match"), "unexpanded": unexpanded}


def _round05(x: float) -> float:
    return round(round(x / 0.05) * 0.05, 2)


def reprice(code: str, basic_rates: dict[str, float] | None = None, year: int | None = None,
            _depth: int = 0) -> dict:
    """Rebuild an item's rate from its DAR analysis with other basic rates (market rates, another location):
    same quantities, same build-up (water, GST factor, CPOH, cess, rows excluded from overheads). With no
    overrides it reproduces the DAR rate. Returns the new rate and every line that changed."""
    a = docstore.get_dar_analysis(code, year)
    if not a or not a.get("rate"):
        raise KeyError(f"no DAR analysis with a rate for item {code}")
    basic_rates = {str(k): float(v) for k, v in (basic_rates or {}).items()}
    marker_amt: dict[str, float] = defaultdict(float)
    w, changed = 0.0, []
    for ln in a["lines"]:
        amt, q = ln["amount"], ln.get("quantity")
        if ln["code"] in basic_rates and q is not None:
            amt = round(q * basic_rates[ln["code"]], 2)
        elif ln["category"] == "sub_analysis" and q is not None and _depth < 4:
            try:
                sub = reprice(ln["code"], basic_rates, year, _depth + 1)
                if sub["changed"]:
                    amt = round(q * sub["rate"], 2)
            except KeyError:
                pass
        elif ln["code"] is None:
            m = re.search(r"([\d.]+)\s*%\s*of\s*([A-Z])\b", ln["description"])
            if m and ln.get("marker") != m.group(2) and m.group(2) in marker_amt:
                amt = round(float(m.group(1)) / 100 * marker_amt[m.group(2)], 2)
        if abs(amt - ln["amount"]) > 0.005:
            changed.append({"code": ln["code"], "description": ln["description"][:80], "old": ln["amount"], "new": amt})
        if ln.get("marker"):
            marker_amt[ln["marker"]] += amt
        w += amt

    def build(base: float, markers: dict[str, float]) -> float:
        running, added = base, 0.0
        for st in a["steps"]:
            lab, low = st["label"], st["label"].lower()
            if low.startswith("total"):
                running, added = running + added, 0.0
            elif low.startswith(("add", "deduct", "less")):
                pct = _pct(lab)
                if pct is None:
                    continue
                excl = sum(markers.get(mk, 0.0) for mk in _excluded(lab))
                amt = (running + added - excl) * pct if low.startswith("add") else -(running + added - excl) * pct
                added += amt
        return running + added

    dar_markers: dict[str, float] = defaultdict(float)
    for ln in a["lines"]:
        if ln.get("marker"):
            dar_markers[ln["marker"]] += ln["amount"]
    dar_total = build(sum(ln["amount"] for ln in a["lines"]), dar_markers)
    ratio = a["rate"] / dar_total if dar_total else 1.0          # per quantity -> per unit
    new_rate = _round05(build(w, marker_amt) * ratio) if changed else a["rate"]
    return {"code": a["code"], "unit": a.get("unit"), "dar_rate": a["rate"], "rate": new_rate,
            "changed": changed, "citation": f"CPWD DAR {a['sor_year']}, analysis of item {a['code']}",
            "needs_review": a["needs_review"]}


def _excluded(label: str) -> list[str]:
    from concrete_rag.parse.dar import excluded_markers
    return excluded_markers(label)
