"""Compare estimated vs actual project costs (eval/datasets/actual_costs.csv).

CSV columns: project, item_code, description, unit, est_qty, est_amount, actual_qty, actual_amount
(+ optional element_type, concrete_m3 - used by `crag learn calibrate`)
Reports per-item variance %, MAPE on amounts and quantities, and total variance per project."""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path


def _f(x) -> float | None:
    try:
        return float(str(x).replace(",", ""))
    except (TypeError, ValueError):
        return None


def variance_report(csv_path: Path) -> dict:
    rows = list(csv.DictReader(open(csv_path, newline="")))
    if not rows:
        return {"error": "no rows"}
    items, by_proj = [], defaultdict(lambda: {"est": 0.0, "act": 0.0})
    ape_amt, ape_qty = [], []
    for r in rows:
        ea, aa, eq, aq = _f(r.get("est_amount")), _f(r.get("actual_amount")), _f(r.get("est_qty")), _f(r.get("actual_qty"))
        v = (ea - aa) / aa * 100 if ea is not None and aa else None
        vq = (eq - aq) / aq * 100 if eq is not None and aq else None
        if v is not None:
            ape_amt.append(abs(v))
        if vq is not None:
            ape_qty.append(abs(vq))
        by_proj[r["project"]]["est"] += ea or 0
        by_proj[r["project"]]["act"] += aa or 0
        items.append({**r, "amount_variance_pct": None if v is None else round(v, 2),
                      "qty_variance_pct": None if vq is None else round(vq, 2)})
    projects = {p: {"estimated": round(x["est"], 2), "actual": round(x["act"], 2),
                    "variance_pct": round((x["est"] - x["act"]) / x["act"] * 100, 2) if x["act"] else None}
                for p, x in by_proj.items()}
    return {"items": items, "projects": projects,
            "mape_amount_pct": round(sum(ape_amt) / len(ape_amt), 2) if ape_amt else None,
            "mape_quantity_pct": round(sum(ape_qty) / len(ape_qty), 2) if ape_qty else None,
            "within_10pct_share": round(sum(1 for a in ape_amt if a <= 10) / len(ape_amt), 3) if ape_amt else None}
