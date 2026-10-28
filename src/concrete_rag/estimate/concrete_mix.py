"""Concrete-mix report: everything about the concrete for a set of elements in one result.

mix design per grade (IS 10262 / IS 456)  +  element volumes (IS 1200 Part 2/5)  +  reinforcement
(BBS / learned / thumb-rule kg per m3)  +  wastage  ->  materials per m3 and to procure, with IS clause
citations, the code editions used and a confidence score.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from concrete_rag.calc import confidence as cf
from concrete_rag.calc.code_tables import Ref, editions_used
from concrete_rag.calc.mix_design import MixDesignInput, design_mix
from concrete_rag.calc.quantities import Element, takeoff
from concrete_rag.estimate.boq import STEEL_SOURCE_SCORE, _cfg, fill_steel_defaults, wastage

EXPOSURES = ["mild", "moderate", "severe", "very_severe", "extreme"]
# mix output key -> wastage key
MATERIALS = {"cement_kg": "cement", "fly_ash_kg": "fly_ash", "fine_aggregate_kg": "fine_aggregate",
             "coarse_aggregate_kg": "coarse_aggregate", "water_to_add_kg": "water", "admixture_kg": "admixture"}


class ConcreteMixRequest(BaseModel):
    mix: MixDesignInput = Field(..., description="mix inputs; its w_c_ratio applies to mix.grade only - other element "
                                                 "grades take w/c from IS 10262 Fig. 1")
    elements: list[Element] = Field(default_factory=list, max_length=500)
    wastage_percent: dict[str, float] = Field(default_factory=dict,
                                              description="override config/cost/wastage.yaml, e.g. {'cement': 2.5}")


def _severest(exposures: list[str]) -> str:
    return max(exposures, key=lambda x: EXPOSURES.index(x) if x in EXPOSURES else 0)


def _grade_key(g: str) -> str:
    return g.replace(" ", "").upper()


def concrete_mix_report(req: ConcreteMixRequest) -> dict:
    wp, approved = wastage()
    wp = {**wp, **req.wastage_percent}
    approved = approved and not req.wastage_percent
    dens = _cfg("estimation_defaults")["bulk_density_kg_m3"]
    assumptions, warnings = [], []

    rcc = [e for e in req.elements if e.type != "pcc_bed"]
    if len(rcc) < len(req.elements):
        warnings.append("pcc_bed elements are nominal-mix PCC; they are listed under elements but not mix-designed")
    steel_notes, steel_src = fill_steel_defaults(req.elements)
    assumptions += steel_notes
    qt = takeoff(req.elements) if req.elements else {"rows": [], "totals": {}, "refs": [], "assumptions": []}
    assumptions += qt["assumptions"]

    # --- one mix design per grade; the requested grade first
    vols: dict[str, float] = {}
    exps: dict[str, list[str]] = {}
    ids: dict[str, list[str]] = {}
    for e, r in zip(req.elements, qt["rows"]):
        if e.type == "pcc_bed":
            continue
        g = _grade_key(e.grade)
        vols[g] = vols.get(g, 0.0) + r["concrete_m3"]
        exps.setdefault(g, []).append(e.exposure)
        ids.setdefault(g, []).append(e.id)
    main = _grade_key(req.mix.grade)
    order = [main] + sorted(g for g in vols if g != main)

    grades, refs, totals = [], [Ref(**r) for r in qt["refs"]], {}
    for g in order:
        inp = req.mix.model_copy(update={"grade": g, "w_c_ratio": req.mix.w_c_ratio if g == main else None})
        exp = _severest(exps.get(g, []) + [req.mix.exposure])
        if exp != inp.exposure:
            inp = inp.model_copy(update={"exposure": exp})
            assumptions.append(f"{g}: designed for '{exp}' exposure (severest among its elements)")
        if g != main and req.mix.w_c_ratio is not None:
            assumptions.append(f"{g}: w/c from IS 10262 Fig. 1 (the w/c input applies to {main} only)")
        try:
            res = design_mix(inp)
        except ValueError as ex:
            warnings.append(f"{g}: mix design not possible - {ex}")
            continue
        refs += [r for s in res.steps for r in s.refs] + [r for c in res.checks for r in c.refs]
        o = res.outputs
        field = o["per_m3_field"]
        with_w = {k: round(field[k] * (1 + wp.get(w, 0) / 100), 1) for k, w in MATERIALS.items()}
        vol = round(vols.get(g, 0.0), 2)
        tot = {k: round(v * vol, 1) for k, v in with_w.items()}
        if vol:
            tot |= {"cement_bags": round(tot["cement_kg"] / 50, 1),
                    "fine_aggregate_m3": round(tot["fine_aggregate_kg"] / dens["fine_aggregate"], 2),
                    "coarse_aggregate_m3": round(tot["coarse_aggregate_kg"] / dens["coarse_aggregate"], 2),
                    "admixture_l": round(tot["admixture_kg"] / inp.admixture.specific_gravity, 1)}
            for k, v in tot.items():
                totals[k] = round(totals.get(k, 0) + v, 2)
        grades.append({
            "grade": g, "exposure": inp.exposure, "elements": ids.get(g, []), "concrete_m3": vol,
            "w_c_ratio": o["w_c_ratio"], "target_strength_n_mm2": o["target_strength_n_mm2"],
            "ratio_by_mass": o["ratio_by_mass"], "per_m3_ssd": o["per_m3_ssd"], "per_m3_field": field,
            "per_m3_with_wastage": with_w, "materials_to_procure": tot if vol else None,
            "all_checks_passed": o["all_checks_passed"], "checks": [c.model_dump() for c in res.checks],
            "confidence": o["confidence"], "warnings": res.warnings,
        })

    # --- reinforcement
    rows = []
    for e, r, s in zip(req.elements, qt["rows"], steel_src):
        rows.append({**r, "steel_kg_per_m3": e.steel_kg_per_m3 if e.type != "pcc_bed" else None,
                     "steel_source": s if r["steel_kg"] else None})
    net_steel = qt["totals"].get("steel_kg", 0.0)
    steel = {"net_kg": net_steel, "wastage_percent": wp.get("steel", 0),
             "to_procure_kg": round(net_steel * (1 + wp.get("steel", 0) / 100), 1),
             "binding_wire_kg": round(net_steel / 1000 * _cfg("estimation_defaults")["binding_wire_kg_per_tonne_steel"]
                                      * (1 + wp.get("binding_wire", 0) / 100), 1),
             "basis": "kg per m3 of concrete per element (see elements[].steel_source); give steel_kg_per_m3 from "
                      "the BBS (/v1/bbs) for a firm quantity"}

    # --- confidence
    factors = []
    if grades:
        vol_all = sum(x["concrete_m3"] for x in grades)
        wts = [(x["concrete_m3"] if vol_all else 1.0) for x in grades]
        mix_score = sum(x["confidence"]["score"] * w for x, w in zip(grades, wts)) / sum(wts)
        failed = [f"{x['grade']} ({', '.join(c['name'] for c in x['checks'] if not c['passed'])})"
                  for x in grades if not x["all_checks_passed"]]
        factors.append(cf.Factor(name="mix design", score=mix_score, weight=3, cap=0.6 if failed else None,
                                 detail=f"code checks failed: {'; '.join(failed)}" if failed
                                 else ", ".join(f"{x['grade']} {x['confidence']['score']}" for x in grades)))
    if net_steel:
        factors.append(cf.Factor(name="reinforcement quantities", weight=2,
                                 score=sum((r["steel_kg"] or 0) * STEEL_SOURCE_SCORE[r["steel_source"] or "input"]
                                           for r in rows) / net_steel,
                                 detail=", ".join(f"{r['id']}: {r['steel_source']}" for r in rows if r["steel_kg"])))
    if req.elements:
        factors.append(cf.refs_factor([Ref(**r) for r in qt["refs"]], name="measurement rules (IS 1200)", weight=1))
    srcs = _cfg("wastage").get("sources") or {}
    used = ["cement", "fine_aggregate", "coarse_aggregate"] + (["steel"] if net_steel else []) \
        + (["admixture"] if req.mix.admixture.type != "none" else [])
    if approved:
        factors.append(cf.Factor(name="wastage factors", score=1.0, weight=1,
                                 detail="approved in config/cost/wastage.yaml"))
    else:
        overridden = set(req.wastage_percent)
        basis = {m: "input" if m in overridden else (srcs.get(m) or {}).get("basis", "practice") for m in used}
        factors.append(cf.Factor(
            name="wastage factors", weight=1,
            score=sum({"cpwd": 0.9, "input": 0.8}.get(b, 0.6) for b in basis.values()) / len(basis),
            detail="; ".join(f"{b}: {', '.join(m for m in used if basis[m] == b)}"
                             for b in ("cpwd", "input", "practice") if b in basis.values())))

    cites = {}
    for r in refs:
        cites.setdefault(r.label(), r.status)
    return {
        "mix_grade": main,
        "grades": grades,
        "elements": rows,
        "concrete_m3": qt["totals"].get("concrete_m3", 0.0),
        "formwork_m2": qt["totals"].get("formwork_m2", 0.0),
        "reinforcement": steel,
        "materials_to_procure": totals or None,
        "wastage": {"percent": wp, "approved": approved, "sources": _cfg("wastage").get("sources") or {},
                    "note": "added to net mix quantities for procurement only; BOQ quantities stay net (IS 1200) "
                            "because DSR item rates already allow for wastage"},
        "citations": [{"ref": k, "status": v} for k, v in sorted(cites.items())],
        "code_versions": editions_used(refs),
        "confidence": cf.combine(factors),
        "assumptions": assumptions,
        "warnings": warnings + ["Proportions are for TRIAL MIX No. 1; confirm by trial batches (IS 10262 cl. 5.8)."],
    }
