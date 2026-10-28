"""Bill of Quantities + cost estimate for RCC works.

quantities (IS 1200)  ->  SOR item mapping (config/cost/boq_mapping.yaml, hybrid-retrieval fallback for
unmapped work)  ->  unit conversion to the item's unit  ->  DSR rates x location index (each checked against
its CPWD DAR analysis)  ->  BOQ lines with SOR / DAR / IS citations, cement consumption statement (DSR
coefficients), the material / labour / equipment / carriage / sundries / water / GST / CPOH / cess split of
every line from its DAR analysis, and a confidence score.
"""
from __future__ import annotations

import datetime as dt
import uuid
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from concrete_rag.calc import confidence as cf
from concrete_rag.calc import rates
from concrete_rag.calc.code_tables import Ref, editions_used
from concrete_rag.calc.quantities import Element, takeoff
from concrete_rag.config import get_settings
from concrete_rag.normalize.units import convert_quantity
from concrete_rag.store import docstore

# where an element's reinforcement density came from -> confidence in the steel quantity
STEEL_SOURCE_SCORE = {"input": 1.0, "learned": 0.8, "thumb_rule": 0.5}


@lru_cache
def _cfg(name: str) -> dict:
    return yaml.safe_load((get_settings().root / "config" / "cost" / f"{name}.yaml").read_text()) or {}


def wastage() -> tuple[dict[str, float], bool]:
    """Wastage % by material (config/cost/wastage.yaml) and whether a person approved the values."""
    w = _cfg("wastage")
    return {k: float(v) for k, v in (w.get("percent") or {}).items()}, bool(w.get("approved_by"))


def fill_steel_defaults(elements: list[Element]) -> tuple[list[str], list[str]]:
    """Give every RCC element a reinforcement density. Returns (assumptions, source per element):
    input (BBS / user) > learned from past projects' actuals (approved calibration) > thumb rule."""
    from concrete_rag.learn.calibrate import approved_steel_density
    defaults = _cfg("estimation_defaults")
    assumptions, sources = [], []
    for e in elements:
        if e.type == "pcc_bed":
            sources.append("input")
            continue
        if e.steel_kg_per_m3 is not None:
            sources.append("input")
            continue
        learned = approved_steel_density(e.type)
        if learned:
            e.steel_kg_per_m3 = learned["value"]
            sources.append("learned")
            assumptions.append(f"{e.id}: reinforcement {e.steel_kg_per_m3} kg/m3 (learned from {learned['projects']} "
                               f"projects' actual steel, default {learned['default']}; replace with BBS)")
        else:
            e.steel_kg_per_m3 = defaults["steel_kg_per_m3"].get(e.type, 100)
            sources.append("thumb_rule")
            assumptions.append(f"{e.id}: reinforcement {e.steel_kg_per_m3} kg/m3 (thumb rule; replace with BBS)")
    return assumptions, sources


class EstimateRequest(BaseModel):
    project: str = "Untitled project"
    location: str = "delhi"
    sor_year: int = 2023
    mix_type: Literal["design_mix", "nominal_mix"] = "design_mix"
    elements: list[Element]
    pcc_mix: str | None = Field("1:4:8", description="PCC mix for pcc_bed elements")
    floors_above_v: int = 0                     # storeys above floor V (adds DSR extra item per 4 floors)
    contingency_percent: float | None = None
    include_material_split: bool = Field(True, description="include the cost-head split of every line (CPWD DAR)")


LEVEL_TEXT = {"below_plinth": "all work up to plinth level", "above_plinth": "above plinth level up to floor V level"}


def _grade(e: Element) -> str:
    return e.grade.replace(" ", "").upper()


def _concrete_item(e: Element, mix_type: str, pcc_mix: str | None) -> tuple[str | None, str, str]:
    """(mapped DSR code or None, retrieval query for unmapped work, preferred sub-head)."""
    m = _cfg("boq_mapping")["concrete"]
    if e.type == "pcc_bed":
        mix = pcc_mix or "1:4:8"
        return m["pcc"].get(mix), f"Providing and laying in position cement concrete of specified grade {mix} " \
                                  f"excluding the cost of centering and shuttering {LEVEL_TEXT['below_plinth']}", "4"
    lvl = e.level
    g = _grade(e)
    if mix_type == "design_mix" and g not in m["design_mix"][lvl]:
        # DSR design-mix RCC (5.33) runs M25-M50; search would only find a wrong item (e.g. the RCA variant 5.33A)
        if g != "M20":
            n = int(g[1:]) if g[1:].isdigit() else None
            why = "IS 456 Table 5 needs at least M20 for RCC" if n and n < 20 else \
                "price it with a rate analysis (/v1/rate-analysis) or the nearest item plus 5.35 (extra cement)"
            raise ValueError(f"{e.id}: CPWD DSR has design-mix RCC items for M25 to M50 only (item 5.33), "
                             f"none for {e.grade}; {why}.")
        mix_type = "nominal_mix"            # M20 is nominal mix 1:1.5:3 (IS 456 Table 9), DSR 5.1.2 / 5.2.2 / 5.3
    if mix_type == "design_mix":
        return m["design_mix"][lvl][g], f"Providing and laying in position design mix cement concrete of grade " \
                                        f"{g} for reinforced cement concrete work {LEVEL_TEXT[lvl]}", "5"
    nm = m["nominal_mix"][lvl]
    code = nm.get(e.type, nm["default"]) if isinstance(nm, dict) else nm
    return code, f"Providing and laying in position specified grade of reinforced cement concrete 1:1.5:3 " \
                 f"for {e.type.replace('_', ' ')} {LEVEL_TEXT[lvl]}", "5"


def _formwork_item(e: Element) -> tuple[str | None, str, str]:
    q = f"Centering and shuttering including strutting, propping etc. and removal of form for {e.type.replace('_', ' ')}"
    return _cfg("boq_mapping")["formwork"].get(e.type), q, "5"


def _steel_item(e: Element) -> tuple[str | None, str, str]:
    m = _cfg("boq_mapping")["reinforcement"]
    q = "Steel reinforcement for R.C.C. work including straightening, cutting, bending, placing in position and " \
        f"binding all complete, thermo-mechanically treated bars of grade Fe-500D, {LEVEL_TEXT[e.level]}"
    return m.get(e.level, m["default"]), q, "5"


def build_estimate(req: EstimateRequest) -> dict:
    mapping = _cfg("boq_mapping")
    defaults = _cfg("estimation_defaults")
    year = req.sor_year
    warnings: list[str] = []

    assumptions, steel_src = fill_steel_defaults(req.elements)
    qt = takeoff(req.elements)
    m20 = [e.id for e in req.elements if req.mix_type == "design_mix" and e.type != "pcc_bed" and _grade(e) == "M20"]
    if m20:
        warnings.append(f"{', '.join(m20)}: M20 priced as nominal mix 1:1.5:3 (IS 456 Table 9; DSR 5.1.2 / 5.2.2 / "
                        "5.3) because the DSR's design-mix items (5.33) start at M25. Use M25 for a design-mix rate.")

    # --- aggregate quantities per SOR item, in the item's own unit
    agg: dict[str, dict] = {}
    resolved: dict[str, tuple[str, dict | None]] = {}

    def resolve(code: str | None, query: str, unit: str, sub_head: str) -> tuple[str, dict | None]:
        """Mapped item if it exists in the parsed SOR, else hybrid retrieval (flagged for checking)."""
        key = code or query
        if key in resolved:
            return resolved[key]
        if code and docstore.get_sor_item(code, year=year):
            resolved[key] = (code, None)
        else:
            why = f"mapped item {code} not in the parsed SOR" if code else "no item mapped in config/cost/boq_mapping.yaml"
            try:
                hit = rates.find_sor_item(query, unit, year, sub_head=sub_head)
            except Exception as ex:   # noqa: BLE001 - retrieval index missing / no match
                raise ValueError(f"{why} for '{query}', and the retrieval fallback found none ({ex}). "
                                 "Add the item to config/cost/boq_mapping.yaml.") from ex
            resolved[key] = (hit["item_code"], {"reason": why, "query": query, "score": hit["score"],
                                                "alternatives": hit["alternatives"]})
            warnings.append(f"{why} for '{query}': item {hit['item_code']} chosen by search - CHECK it and add the "
                            "right item to config/cost/boq_mapping.yaml")
        return resolved[key]

    def add(item: tuple[str | None, str, str], qty: float, unit: str, src: str):
        code, found = resolve(item[0], item[1], unit, item[2])
        item_unit = docstore.get_sor_item(code, year=year)["unit"] or unit
        try:
            q = convert_quantity(qty, unit, item_unit)
        except ValueError as ex:
            raise ValueError(f"{src}: measured in {unit} but SOR item {code} is per {item_unit} - fix the item in "
                             "config/cost/boq_mapping.yaml") from ex
        a = agg.setdefault(code, {"qty": 0.0, "sources": [], "unit_conversions": [], "search": found})
        a["qty"] += q
        a["sources"].append(src)
        if unit != item_unit:
            a["unit_conversions"].append(f"{src}: {qty:g} {unit} = {q:g} {item_unit}")

    for e, row in zip(req.elements, qt["rows"]):
        add(_concrete_item(e, req.mix_type, req.pcc_mix), row["concrete_m3"], "m3", f"{e.id} concrete")
        if row["formwork_m2"] and e.type in mapping["formwork"]:
            add(_formwork_item(e), row["formwork_m2"], "m2", f"{e.id} formwork")
        if row["steel_kg"]:
            add(_steel_item(e), row["steel_kg"], "kg", f"{e.id} reinforcement")
    # DSR 5.38: extra per cum of concrete above floor V level, once for each four floors or part thereof, i.e.
    # ceil(j / 4) times for concrete in the j-th storey above floor V
    above_v = [(e, r) for e, r in zip(req.elements, qt["rows"]) if e.level == "above_plinth"]
    extra_qty = sum(r["concrete_m3"] * -(-(e.floor - 5) // 4) for e, r in above_v if e.floor > 5)
    if not extra_qty and req.floors_above_v > 0:     # storeys not given per element: spread evenly over all storeys
        n = req.floors_above_v
        share = sum(-(-j // 4) for j in range(1, n + 1)) / (5 + n)
        extra_qty = sum(r["concrete_m3"] for _, r in above_v) * share
        assumptions.append(f"Extra item above floor V: above-plinth concrete taken as spread evenly over {5 + n} "
                           f"storeys, storey j above floor V charged ceil(j/4) times (factor {share:.3f}); give each "
                           "element its floor for an exact figure.")
    if extra_qty:
        add((mapping["concrete"]["above_floor_v_extra"], "Extra for work in reinforced cement concrete above floor V "
             "level for each four floors", "5"), extra_qty, "m3", "extra for concrete above floor V")

    # --- price lines
    lines = []
    for i, (code, a) in enumerate(sorted(agg.items(), key=lambda kv: [int(x) if x.isdigit() else 0
                                                                       for x in kv[0].replace("A", ".").split(".")]), 1):
        r = rates.sor_rate(code, year=year, location=req.location)
        qty = round(a["qty"], 2)
        amount = round(qty * (r["rate"] or 0), 2)
        if r.get("dar_match") is False:
            warnings.append(f"item {code}: the DSR scan reads {r['dsr_rate']} but its DAR analysis gives {r['dar_rate']} - "
                            "check the printed DSR")
        elif not r["rate_confirmed"] and not r.get("dar_match"):
            warnings.append(f"item {code}: rate {r['dsr_rate']} NOT confirmed by OCR cross-check "
                            f"({'; '.join(r['review_reasons'])}) - verify in DSR before use")
        lines.append({"sl": i, "item_code": code, "description": r["description"], "unit": r["unit"],
                      "quantity": qty, "rate": r["rate"], "amount": amount, "dsr_rate": r["dsr_rate"],
                      "cost_index": r["cost_index"], "citation": r["citation"],
                      "rate_confirmed": bool(r.get("dar_match")) or (r["rate_confirmed"] and r.get("dar_match") is not False),
                      "rate_votes": r["rate_votes"], "dar_rate": r.get("dar_rate"), "dar_match": r.get("dar_match"),
                      "dar_citation": r.get("dar_citation"), "matched_by": "search" if a["search"] else "mapping",
                      "search": a["search"], "unit_conversions": a["unit_conversions"], "derived_from": a["sources"]})
    subtotal = round(sum(l["amount"] for l in lines), 2)
    cont_pct = req.contingency_percent if req.contingency_percent is not None else defaults["contingency_percent"]
    contingency = round(subtotal * cont_pct / 100, 2)

    # --- cement consumption statement (DSR coefficients, quintal per unit)
    cement = []
    for l in lines:
        co = docstore.get_cement_coefficient(l["item_code"])
        if co:
            per = co.get("unit") or l["unit"]                       # coefficients may be per 100 sqm etc.
            try:
                q = convert_quantity(l["quantity"], l["unit"], per)
            except ValueError:
                warnings.append(f"item {l['item_code']}: cement coefficient is per {per}, the BOQ line per {l['unit']}")
                continue
            cement.append({"item_code": l["item_code"], "quantity": l["quantity"], "unit": l["unit"],
                           "coefficient_qtl_per_unit": co["cement_quintal_per_unit"], "coefficient_unit": per,
                           "cement_qtl": round(q * co["cement_quintal_per_unit"], 2),
                           "confirmed": co.get("confirmed", False)})

    # --- cost heads of every line from its DAR analysis
    split = cost_heads(lines) if req.include_material_split else None

    sor_edition = rates.sor_edition(mapping.get("sor_name", "CPWD-DSR"), year)
    qt_refs = [Ref(**r) for r in qt["refs"]]
    est = {
        "id": uuid.uuid4().hex[:12],
        "project": req.project,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "location": req.location,
        "sor": f"CPWD-DSR {year}",
        "sor_edition": sor_edition,
        "code_versions": editions_used(qt_refs) + [sor_edition],
        "quantities": qt,
        "boq": lines,
        "summary": {"subtotal": subtotal, "contingency_percent": cont_pct, "contingency": contingency,
                    "grand_total": round(subtotal + contingency, 2),
                    "note": "DSR item rates include materials, labour, T&P, water charges, contractor's profit & "
                            "overheads and GST @18% (DSR 2023 preface)."},
        "cement_statement": cement,
        "cost_heads": split,
        "confidence": _confidence(req, qt, lines, steel_src, qt_refs),
        "assumptions": assumptions + qt["assumptions"],
        "warnings": warnings,
        "references": qt["refs"],
    }
    docstore.save_estimate(est["id"], req.project, est)
    return est


def refresh_estimate(est: dict) -> dict:
    """Re-price a saved estimate with today's data: same quantities and items, current DSR rates (and their DAR
    check), DAR cost heads and confidence. Element sizes are not stored with an estimate, so take-off is not redone."""
    location = est.get("location", "delhi")
    defaults = _cfg("estimation_defaults")
    lines, warnings = [], []
    for old in est["boq"]:
        try:
            r = rates.sor_rate(old["item_code"], location=location)
        except KeyError:
            warnings.append(f"item {old['item_code']} no longer in the SOR - kept at its old rate")
            lines.append(old)
            continue
        if r.get("dar_match") is False:
            warnings.append(f"item {old['item_code']}: the DSR scan reads {r['dsr_rate']} but its DAR analysis gives "
                            f"{r['dar_rate']} - check the printed DSR")
        lines.append({**old, "description": r["description"], "unit": r["unit"], "rate": r["rate"],
                      "amount": round(old["quantity"] * (r["rate"] or 0), 2), "dsr_rate": r["dsr_rate"],
                      "cost_index": r["cost_index"], "citation": r["citation"],
                      "rate_confirmed": bool(r.get("dar_match")) or (r["rate_confirmed"] and r.get("dar_match") is not False),
                      "rate_votes": r["rate_votes"], "dar_rate": r.get("dar_rate"), "dar_match": r.get("dar_match"),
                      "dar_citation": r.get("dar_citation"), "matched_by": old.get("matched_by", "mapping"),
                      "search": old.get("search"), "unit_conversions": old.get("unit_conversions", [])})
    subtotal = round(sum(l["amount"] for l in lines), 2)
    cont_pct = est["summary"].get("contingency_percent", defaults["contingency_percent"])
    contingency = round(subtotal * cont_pct / 100, 2)
    qt = est["quantities"]
    # where each element's steel figure came from, recovered from the estimate's assumptions
    src = []
    for row in qt["rows"]:
        note = next((a for a in est.get("assumptions", []) if a.startswith(f"{row['id']}: reinforcement")), "")
        src.append("learned" if "learned" in note else "thumb_rule" if "thumb rule" in note else "input")
    req = EstimateRequest(project=est["project"], location=location, elements=[])
    year = int(str(est.get("sor", "CPWD-DSR 2023")).split()[-1])
    sor_edition = rates.sor_edition("CPWD-DSR", year)
    qt_refs = [Ref(**r) for r in qt["refs"]]
    return {**est, "boq": lines, "sor_edition": sor_edition, "code_versions": editions_used(qt_refs) + [sor_edition],
            "summary": {**est["summary"], "subtotal": subtotal, "contingency": contingency,
                        "grand_total": round(subtotal + contingency, 2)},
            "cost_heads": cost_heads(lines), "confidence": _confidence(req, qt, lines, src, qt_refs),
            "warnings": warnings + [w for w in est.get("warnings", []) if "NOT confirmed" not in w and "DAR analysis" not in w],
            "refreshed": {"on": dt.datetime.now().isoformat(timespec="seconds"),
                          "previous_grand_total": est["summary"]["grand_total"],
                          "note": "re-priced with current DSR / DAR data; quantities unchanged"}}


def _confidence(req: EstimateRequest, qt: dict, lines: list[dict], steel_src: list[str], refs: list[Ref]) -> dict:
    total = sum(l["amount"] for l in lines) or 1.0
    by_amount = lambda f: sum(f(l) * l["amount"] for l in lines) / total  # noqa: E731
    searched = [l["item_code"] for l in lines if l["matched_by"] == "search"]
    unconfirmed = [l["item_code"] for l in lines if not l["rate_confirmed"]]
    factors = [
        cf.Factor(name="SOR rates", score=by_amount(cf.rate_score), weight=3,
                  detail=f"{sum(1 for l in lines if l.get('dar_match'))}/{len(lines)} item rates equal their DAR analysis, "
                         f"{len(lines) - len(unconfirmed)}/{len(lines)} confirmed"
                         + (f"; check {', '.join(unconfirmed)}" if unconfirmed else "")),
        cf.Factor(name="item selection", score=by_amount(lambda l: 0.5 if l["matched_by"] == "search" else 1.0),
                  weight=2, cap=0.75 if searched else None,
                  detail=f"chosen by search, confirm: {', '.join(searched)}" if searched
                  else "all items from config/cost/boq_mapping.yaml"),
        cf.refs_factor(refs, name="measurement rules (IS 1200)", weight=1),
        cf.Factor(name="location cost index", score=1.0 if req.location.strip().lower() == "delhi" else 0.9, weight=1,
                  detail="DSR base (Delhi)" if req.location.strip().lower() == "delhi"
                  else f"{req.location} index from config/cost/cost_indices.yaml"),
    ]
    kg = [(r["steel_kg"] or 0, s) for r, s in zip(qt["rows"], steel_src)]
    steel_total = sum(k for k, _ in kg)
    if steel_total:
        n_rule = sum(1 for k, s in kg if k and s == "thumb_rule")
        factors.append(cf.Factor(name="reinforcement quantities", weight=2,
                                 score=sum(k * STEEL_SOURCE_SCORE[s] for k, s in kg) / steel_total,
                                 detail=f"{n_rule} element(s) use thumb-rule kg/m3; give a BBS" if n_rule
                                 else "from BBS / input or learned densities"))
    return cf.combine(factors)


def cost_heads(lines: list[dict]) -> dict:
    """Split each BOQ amount by its item's CPWD DAR analysis (shares of the rate), and total the heads."""
    heads_order = rates.BASE_HEADS + rates.OVERHEAD_HEADS
    totals: dict[str, float] = {}
    per_line, missing = [], []
    for l in lines:
        b = rates.dar_breakdown(l["item_code"])
        if not b:
            missing.append(l["item_code"])
            continue
        split = {k: round(l["amount"] * b["share_percent"].get(k, 0) / 100, 2) for k in heads_order
                 if b["share_percent"].get(k)}
        for k, v in split.items():
            totals[k] = totals.get(k, 0.0) + v
        per_line.append({"item_code": l["item_code"], "amount": l["amount"], "split": split, "citation": b["citation"],
                         "analysis_flagged": b["needs_review"]})
    analysed = sum(x["amount"] for x in per_line)
    total = sum(l["amount"] for l in lines)
    return {
        "heads": {k: round(totals[k], 2) for k in heads_order if k in totals},
        "share_percent": {k: round(100 * totals[k] / analysed, 2) for k in heads_order if k in totals} if analysed else {},
        "analysed_amount": round(analysed, 2), "not_analysed_amount": round(total - analysed, 2),
        "not_analysed_items": missing, "lines": per_line,
        "basis": "Each BOQ amount is split in the proportions of its item's CPWD DAR 2023 analysis: material, labour, "
                 "equipment (hire & running of plant), carriage, sundries, then water charges 1 %, GST (factor 0.2127), "
                 "contractor's profit & overheads 15 % and cess 1 %. Sub-analyses (e.g. mortar) are split by their own "
                 "analyses. At other locations the cost index scales every head alike.",
    }
