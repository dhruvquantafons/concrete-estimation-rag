"""Bill of Quantities + cost estimate for RCC works.

quantities (IS 1200)  ->  SOR item mapping (config/cost/boq_mapping.yaml)  ->  DSR rates x location index
-> BOQ lines with citations, cement consumption statement (DSR coefficients), and an indicative
material / (labour + equipment + overheads) split using DSR basic rates and mix-design quantities.
"""
from __future__ import annotations

import datetime as dt
import uuid
from functools import lru_cache
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from concrete_rag.calc import rates
from concrete_rag.calc.mix_design import MixDesignInput, design_mix
from concrete_rag.calc.quantities import Element, takeoff
from concrete_rag.config import get_settings
from concrete_rag.store import docstore


@lru_cache
def _cfg(name: str) -> dict:
    return yaml.safe_load((get_settings().root / "config" / "cost" / f"{name}.yaml").read_text()) or {}


class EstimateRequest(BaseModel):
    project: str = "Untitled project"
    location: str = "delhi"
    sor_year: int = 2023
    mix_type: Literal["design_mix", "nominal_mix"] = "design_mix"
    elements: list[Element]
    pcc_mix: str | None = Field("1:4:8", description="PCC mix for pcc_bed elements")
    floors_above_v: int = 0                     # storeys above floor V (adds DSR extra item per 4 floors)
    contingency_percent: float | None = None
    include_material_split: bool = True
    mix_design_overrides: dict = Field(default_factory=dict, description="passed to MixDesignInput for material split")


def _concrete_code(e: Element, mix_type: str, pcc_mix: str | None) -> str:
    m = _cfg("boq_mapping")["concrete"]
    if e.type == "pcc_bed":
        return m["pcc"][pcc_mix or "1:4:8"]
    lvl = e.level
    if mix_type == "design_mix":
        table = m["design_mix"][lvl]
        g = e.grade.replace(" ", "").upper()
        if g not in table:
            raise ValueError(f"no design-mix DSR item for grade {e.grade}; available {list(table)}")
        return table[g]
    nm = m["nominal_mix"][lvl]
    return nm.get(e.type, nm["default"]) if isinstance(nm, dict) else nm


def build_estimate(req: EstimateRequest) -> dict:
    mapping = _cfg("boq_mapping")
    defaults = _cfg("estimation_defaults")
    year = req.sor_year
    assumptions: list[str] = []
    warnings: list[str] = []

    # --- steel density defaults
    for e in req.elements:
        if e.steel_kg_per_m3 is None and e.type != "pcc_bed":
            e.steel_kg_per_m3 = defaults["steel_kg_per_m3"].get(e.type, 100)
            assumptions.append(f"{e.id}: reinforcement {e.steel_kg_per_m3} kg/m3 (thumb rule; replace with BBS)")
    qt = takeoff(req.elements)

    # --- aggregate quantities per SOR item
    agg: dict[str, dict] = {}

    def add(code: str, qty: float, src: str):
        a = agg.setdefault(code, {"qty": 0.0, "sources": []})
        a["qty"] += qty
        a["sources"].append(src)

    for e, row in zip(req.elements, qt["rows"]):
        add(_concrete_code(e, req.mix_type, req.pcc_mix), row["concrete_m3"], f"{e.id} concrete")
        if row["formwork_m2"] and e.type in mapping["formwork"]:
            add(mapping["formwork"][e.type], row["formwork_m2"], f"{e.id} formwork")
        if row["steel_kg"]:
            add(mapping["reinforcement"]["default"], row["steel_kg"], f"{e.id} reinforcement")
    if req.floors_above_v > 0:
        n_extra = -(-req.floors_above_v // 4)
        above = sum(r["concrete_m3"] for e, r in zip(req.elements, qt["rows"]) if e.level == "above_plinth")
        add(mapping["concrete"]["above_floor_v_extra"], above * n_extra, f"extra for {req.floors_above_v} floors above V")
        assumptions.append("Extra item for floors above V applied to all above-plinth concrete (refine per floor).")

    # --- price lines
    lines = []
    for i, (code, a) in enumerate(sorted(agg.items(), key=lambda kv: [int(x) if x.isdigit() else 0
                                                                       for x in kv[0].replace("A", ".").split(".")]), 1):
        r = rates.sor_rate(code, year=year, location=req.location)
        qty = round(a["qty"], 2)
        amount = round(qty * (r["rate"] or 0), 2)
        if not r["rate_confirmed"]:
            warnings.append(f"item {code}: rate {r['dsr_rate']} NOT confirmed by OCR cross-check "
                            f"({'; '.join(r['review_reasons'])}) - verify in DSR before use")
        lines.append({"sl": i, "item_code": code, "description": r["description"], "unit": r["unit"],
                      "quantity": qty, "rate": r["rate"], "amount": amount, "dsr_rate": r["dsr_rate"],
                      "cost_index": r["cost_index"], "citation": r["citation"], "rate_confirmed": r["rate_confirmed"],
                      "derived_from": a["sources"]})
    subtotal = round(sum(l["amount"] for l in lines), 2)
    cont_pct = req.contingency_percent if req.contingency_percent is not None else defaults["contingency_percent"]
    contingency = round(subtotal * cont_pct / 100, 2)

    # --- cement consumption statement (DSR coefficients, quintal per unit)
    cement = []
    for l in lines:
        co = docstore.get_cement_coefficient(l["item_code"])
        if co:
            cement.append({"item_code": l["item_code"], "quantity": l["quantity"], "unit": l["unit"],
                           "coefficient_qtl_per_unit": co["cement_quintal_per_unit"],
                           "cement_qtl": round(l["quantity"] * co["cement_quintal_per_unit"], 2),
                           "confirmed": co.get("confirmed", False)})

    # --- indicative material split
    split = _material_split(req, qt, lines) if req.include_material_split else None

    est = {
        "id": uuid.uuid4().hex[:12],
        "project": req.project,
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "location": req.location,
        "sor": f"CPWD-DSR {year}",
        "quantities": qt,
        "boq": lines,
        "summary": {"subtotal": subtotal, "contingency_percent": cont_pct, "contingency": contingency,
                    "grand_total": round(subtotal + contingency, 2),
                    "note": "DSR item rates include materials, labour, T&P, water charges, contractor's profit & "
                            "overheads and GST @18% (DSR 2023 preface)."},
        "cement_statement": cement,
        "cost_heads": split,
        "assumptions": assumptions + qt["assumptions"],
        "warnings": warnings,
        "references": qt["refs"],
    }
    docstore.save_estimate(est["id"], req.project, est)
    return est


def _material_split(req: EstimateRequest, qt: dict, lines: list[dict]) -> dict:
    """Material cost from mix design x basic rates; remainder of item amounts = labour + T&P + overheads + GST."""
    d = _cfg("estimation_defaults")
    mc = d["material_codes"]
    br = {k: docstore.get_basic_rate(v["code"]) for k, v in mc.items()}
    missing = [k for k, v in br.items() if not v or v.get("rate") is None]
    if missing:
        return {"error": f"basic rates missing for {missing}"}
    mat_total = 0.0
    detail = []
    conc_by_grade: dict[str, float] = {}
    for e, r in zip(req.elements, qt["rows"]):
        if e.type != "pcc_bed":
            conc_by_grade[e.grade] = conc_by_grade.get(e.grade, 0) + r["concrete_m3"]
    for grade, vol in conc_by_grade.items():
        try:
            mix = design_mix(MixDesignInput(grade=grade, exposure=req.elements[0].exposure,
                                            **{"w_c_ratio": 0.45, **req.mix_design_overrides}))
        except Exception as ex:   # noqa: BLE001
            return {"error": f"mix design for material split failed: {ex}"}
        m = mix.outputs["per_m3_ssd"]
        cem_t = m["cement_kg"] / 1000 * vol
        fa_m3 = m["fine_aggregate_kg"] / d["bulk_density_kg_m3"]["fine_aggregate"] * vol
        ca_m3 = m["coarse_aggregate_kg"] / d["bulk_density_kg_m3"]["coarse_aggregate"] * vol
        amt = cem_t * br["cement_opc43"]["rate"] + fa_m3 * br["coarse_sand"]["rate"] + ca_m3 * br["aggregate_20mm"]["rate"]
        mat_total += amt
        detail.append({"grade": grade, "concrete_m3": round(vol, 2), "cement_t": round(cem_t, 2),
                       "fine_aggregate_m3": round(fa_m3, 2), "coarse_aggregate_m3": round(ca_m3, 2),
                       "amount": round(amt, 2), "mix_w_c_assumed": mix.inputs["w_c_ratio"]})
    steel_kg = qt["totals"]["steel_kg"]
    steel_amt = steel_kg / 100 * br["steel_tmt_fe500d"]["rate"] + steel_kg / 1000 * d["binding_wire_kg_per_tonne_steel"] \
        * br["binding_wire"]["rate"]
    mat_total += steel_amt
    detail.append({"item": "reinforcement steel + binding wire", "steel_kg": steel_kg, "amount": round(steel_amt, 2)})
    work_total = sum(l["amount"] for l in lines)
    return {
        "materials_basic_cost": round(mat_total, 2),
        "labour_equipment_overheads_gst_formwork": round(work_total - mat_total, 2),
        "materials_share_percent": round(100 * mat_total / work_total, 1) if work_total else None,
        "detail": detail,
        "basis": "DSR 2023 basic rates (0367 cement, 0982 coarse sand, 0295 20 mm aggregate, 1005 TMT, 7335 binding wire) "
                 "x IS 10262 trial-mix quantities (w/c 0.45 unless overridden). Indicative only: a full labour / T&P "
                 "split needs CPWD DAR 2023 analyses (config/cost/rate_analysis.yaml).",
    }
