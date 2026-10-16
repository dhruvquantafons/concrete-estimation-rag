"""Rate lookup (SOR), location cost index, and DAR-style rate analysis from basic rates."""
from __future__ import annotations

from functools import lru_cache

import yaml

from concrete_rag.config import get_settings
from concrete_rag.normalize.currency import apply_cost_index
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
        "citation": f"{sor_name} {it['sor_year']}, item {it['code']} [pdf p.{it['page']}]",
        "rate_confirmed": not it["needs_review"],
        "review_reasons": it["review_reasons"],
    }
    return out


def rate_analysis(template_name: str, allow_example: bool = False) -> dict:
    """Build up a rate from basic rates: (M + L + T&P) x (1 + water%) x (1 + CP&OH%) [+ GST%]."""
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
    cpoh = (base + water) * cfg["contractor_profit_overheads_percent"] / 100
    gst = (base + water + cpoh) * cfg["gst_percent"] / 100
    total = base + water + cpoh + gst
    return {"template": template_name, "unit": t.get("unit"), "lines": lines,
            "cost_heads": {k: round(v, 2) for k, v in heads.items()},
            "water_charges": round(water, 2), "cp_oh": round(cpoh, 2), "gst": round(gst, 2),
            "rate": round(total, 2),
            "settings_verified": bool(cfg.get("verified_by")),
            "warning": None if cfg.get("verified_by") else
            "water/CP&OH/GST percentages not yet verified against CPWD DAR 2023 (config/cost/rate_analysis.yaml)"}
