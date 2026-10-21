"""Estimates learn from actual project costs (eval/datasets/actual_costs.csv).

Only the thumb-rule assumptions learn - never DSR rates or IS 1200 measurement:
- steel_kg_per_m3 per element type (used when no BBS is given). Observed density = actual steel kg /
  concrete m3 of that element type, from reinforcement rows that carry `element_type` and `concrete_m3`.
  It is blended with the current default so a single project cannot swing it:
      learned = (PRIOR_WEIGHT x default + n_projects x observed) / (PRIOR_WEIGHT + n_projects)
- Quantity and amount variance per element type / item, reported for information.

Writes config/cost/learned_adjustments.yaml with status `proposed`. The BOQ uses the learned values only
after an admin approves them (`crag learn approve-calibration --by <name>`).
"""
from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from pathlib import Path

import yaml

from concrete_rag.config import get_settings
from concrete_rag.eval.estimate_variance import _f

PRIOR_WEIGHT = 3          # the default counts as this many projects


def path() -> Path:
    return get_settings().root / "config" / "cost" / "learned_adjustments.yaml"


def load() -> dict:
    p = path()
    return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}


def approved_steel_density(element_type: str) -> dict | None:
    d = load()
    if d.get("status") != "approved":
        return None
    return (d.get("steel_kg_per_m3") or {}).get(element_type)


def _reinforcement_codes() -> set[str]:
    m = yaml.safe_load((get_settings().root / "config" / "cost" / "boq_mapping.yaml").read_text())
    return set(m["reinforcement"].values())


def calibrate(csv_path: Path | None = None) -> dict:
    root = get_settings().root
    csv_path = csv_path or root / "eval" / "datasets" / "actual_costs.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"{csv_path} not found - copy eval/datasets/actual_costs_template.csv and fill it")
    defaults = yaml.safe_load((root / "config" / "cost" / "estimation_defaults.yaml").read_text())["steel_kg_per_m3"]
    rcodes = _reinforcement_codes()
    steel = defaultdict(lambda: {"kg": 0.0, "m3": 0.0, "projects": set()})
    variance = defaultdict(lambda: {"est_qty": 0.0, "act_qty": 0.0, "est_amt": 0.0, "act_amt": 0.0, "rows": 0})
    for r in csv.DictReader(open(csv_path, newline="")):
        et = (r.get("element_type") or "").strip()
        aq, eq, cm = _f(r.get("actual_qty")), _f(r.get("est_qty")), _f(r.get("concrete_m3"))
        if et and r.get("item_code") in rcodes and aq and cm:
            s = steel[et]
            s["kg"] += aq
            s["m3"] += cm
            s["projects"].add(r["project"])
        v = variance[(et or "-", r.get("item_code"))]
        v["rows"] += 1
        for k, x in (("est_qty", eq), ("act_qty", aq), ("est_amt", _f(r.get("est_amount"))),
                     ("act_amt", _f(r.get("actual_amount")))):
            v[k] += x or 0.0
    learned = {}
    for et, s in sorted(steel.items()):
        n, observed = len(s["projects"]), s["kg"] / s["m3"]
        prior = defaults.get(et, 100)
        learned[et] = {"value": round((PRIOR_WEIGHT * prior + n * observed) / (PRIOR_WEIGHT + n), 1),
                       "default": prior, "observed": round(observed, 1), "projects": n,
                       "concrete_m3": round(s["m3"], 2)}
    pct = lambda a, b: round((a - b) / b * 100, 2) if b else None  # noqa: E731
    info = [{"element_type": et, "item_code": code, "rows": v["rows"],
             "qty_variance_pct": pct(v["est_qty"], v["act_qty"]), "amount_variance_pct": pct(v["est_amt"], v["act_amt"])}
            for (et, code), v in sorted(variance.items())]
    out = {"status": "proposed", "approved_by": None, "approved_on": None,
           "generated_on": dt.date.today().isoformat(),
           "source": str(csv_path.relative_to(root)) if csv_path.is_relative_to(root) else str(csv_path),
           "method": f"learned = ({PRIOR_WEIGHT} x default + n_projects x observed) / ({PRIOR_WEIGHT} + n_projects)",
           "steel_kg_per_m3": learned, "variance_estimated_vs_actual": info}
    header = ("# Written by `crag learn calibrate` from actual project costs. Used by the BOQ only when\n"
              "# status is `approved` (`crag learn approve-calibration --by <name>`). DSR rates never change here.\n")
    path().write_text(header + yaml.safe_dump(out, sort_keys=False, allow_unicode=True))
    return out


def approve(by: str) -> dict:
    d = load()
    if not d:
        raise FileNotFoundError("no learned_adjustments.yaml - run `crag learn calibrate` first")
    d.update(status="approved", approved_by=by, approved_on=dt.date.today().isoformat())
    text = path().read_text()
    header = "".join(l for l in text.splitlines(keepends=True) if l.startswith("#"))
    path().write_text(header + yaml.safe_dump(d, sort_keys=False, allow_unicode=True))
    return d
