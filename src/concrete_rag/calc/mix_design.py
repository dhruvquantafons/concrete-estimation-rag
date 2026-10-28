"""Concrete mix proportioning per IS 10262 : 2019, Section 2 (ordinary & standard grades), with
durability limits from IS 456 : 2000 Table 5. Reproduces the Annex A worked example (see tests)."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

from concrete_rag.calc import code_tables as ct
from concrete_rag.calc import confidence as cf
from concrete_rag.calc.base import CalcResult, r2

ZONES = {"IV": 0, "III": 1, "II": 2, "I": 3}


class Admixture(BaseModel):
    type: Literal["none", "water_reducer", "superplasticizer"] = "none"
    dosage_percent: float = 0.0           # % by mass of cementitious material
    water_reduction_percent: float = 0.0  # from trial data / manufacturer (IS 10262 cl. 5.3: 5-10 % WR, 20-30 % SP)
    specific_gravity: float = 1.145


class MixDesignInput(BaseModel):
    grade: str = Field(..., examples=["M30"])
    exposure: Literal["mild", "moderate", "severe", "very_severe", "extreme"] = "moderate"
    concrete_type: Literal["rcc", "pcc"] = "rcc"
    max_aggregate_size_mm: Literal[10, 20, 40] = 20
    slump_mm: float = 75
    cement_type: str = "OPC 43"
    sg_cement: float = 3.15
    sg_coarse: float = 2.74
    sg_fine: float = 2.65
    fine_aggregate_zone: Literal["I", "II", "III", "IV"] = "II"
    aggregate_shape: Literal["angular", "sub_angular", "gravel_with_crushed", "rounded_gravel"] = "angular"
    site_control: Literal["good", "fair"] = "good"
    known_std_dev: float | None = Field(None, description="from >= 30 site results (cl. 4.2.1.1)")
    w_c_ratio: float | None = Field(None, description="free w/c from Fig. 1 / trial data; required unless a verified Fig. 1 curve is configured")
    admixture: Admixture = Admixture()
    fly_ash_percent: float = 0.0          # part replacement of cementitious content
    sg_fly_ash: float = 2.2
    increase_cm_for_fly_ash: bool = True  # cl. 5.4.1: +10 % cementitious for preliminary trial when fly ash >= 20 %
    pumpable: bool = False
    ca_reduction_percent: float = 0.0     # cl. 5.5.2, up to 10 % for pumping / congested reinforcement
    water_absorption_coarse_percent: float = 0.5
    water_absorption_fine_percent: float = 1.0
    surface_moisture_coarse_percent: float = 0.0    # free (surface) moisture = total moisture - absorption
    surface_moisture_fine_percent: float = 0.0      # (Annex B-2 j); 0 = dry aggregate as in Annex A
    max_cement_content: float = 450.0     # IS 456 cl. 8.2.4.2 (excluding fly ash / GGBS)
    round_like_annex_a: bool = True       # round water & cement up to whole kg as in IS 10262 Annex A


def _fig1_wc(target: float, cement_type: str) -> tuple[float | None, ct.Ref]:
    t = ct.tables()["fig_1_wc_vs_strength"]
    curve = ct.fig1_curve(cement_type)
    pts = t["curves"].get(curve) or []
    if not pts:
        return None, ct.ref("fig_1_wc_vs_strength", None, f"{curve} not digitized")
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return ct.interp(target, xs, ys), ct.ref("fig_1_wc_vs_strength", None, curve)


def field_mass(ssd: float, absorption_percent: float, free_moisture_percent: float) -> float:
    """Mass of aggregate to batch for `ssd` kg in SSD condition. Wet (free surface moisture > 0, Annex B-11):
    SSD x (1 + free / 100). Otherwise dry, as in Annex A ("moisture: Nil", A-10): SSD / (1 + absorption / 100).
    The mixing water changes by (SSD - field mass)."""
    if free_moisture_percent > 0:
        return ssd * (1 + free_moisture_percent / 100)
    return ssd / (1 + absorption_percent / 100)


def design_mix(inp: MixDesignInput) -> CalcResult:
    res = CalcResult(calculator="mix_design_is10262_2019", inputs=inp.model_dump())
    fck = ct.grade_num(inp.grade)

    # --- 4.2 target strength
    x, xref = ct.band_value("table_1_x_factor", fck)
    if inp.known_std_dev:
        s, sref = inp.known_std_dev, ct.Ref(code="IS 10262", item="cl. 4.2.1.1", value=inp.known_std_dev,
                                            status="verified", note="user-supplied site standard deviation")
    else:
        s, sref = ct.band_value("table_2_assumed_sd", fck)
        if inp.site_control == "fair":
            s += ct.tables()["table_2_assumed_sd"]["fair_control_increase"]
    res.step("standard deviation S", s, "N/mm2", refs=[sref])
    res.step("factor X", x, "N/mm2", refs=[xref])
    t1, t2 = fck + 1.65 * s, fck + x
    target = res.step("target mean strength f'ck", r2(max(t1, t2)), "N/mm2",
                      f"max(fck + 1.65 S = {t1:.2f}, fck + X = {t2:.2f})",
                      refs=[ct.Ref(code="IS 10262", item="cl. 4.2", status="ocr_confirmed")])

    # --- 5.1 w/c
    dur = ct.exposure_row(inp.exposure)
    pre = "rcc" if inp.concrete_type == "rcc" else "pcc"
    max_wc = dur[f"{pre}_max_wc"]
    dref = ct.ref("table_5_durability", dur)
    wc = inp.w_c_ratio
    wc_factor = cf.Factor(name="w/c ratio", score=1.0, weight=2, detail="input (from Fig. 1 / trials)")
    if wc is None:
        wc, fref = _fig1_wc(target, inp.cement_type)
        if wc is None:
            raise ValueError("w_c_ratio is required: IS 10262 Fig. 1 curves are not digitized in "
                             "config/code_tables/is10262_2019.yaml (see README 'Your next steps').")
        res.step("free w/c from Fig. 1", r2(wc, 3), None, refs=[fref])
        wc_factor = cf.Factor(name="w/c ratio", score=cf.STATUS_SCORE.get(fref.status, 0.3), weight=2,
                              detail=f"read from digitized Fig. 1 ({fref.status.replace('_', ' ')})")
        lo, hi = ct.fig1_range(inp.cement_type)
        if not lo <= target <= hi:
            wc_factor = cf.Factor(name="w/c ratio", score=0.5, weight=2,
                                  detail="target strength outside Fig. 1; w/c taken at the end of the curve")
            res.warnings.append(f"target strength {target} N/mm2 is outside IS 10262 Fig. 1 ({fref.note}: "
                                f"{lo}-{hi} N/mm2); w/c taken at the end of the curve - establish w/c by trials")
    else:
        res.step("free w/c (input, from Fig. 1 / trials)", wc, None,
                 refs=[ct.Ref(code="IS 10262", item="cl. 5.1 / Fig. 1", status="verified", note="user input")])
    if wc > max_wc:
        res.warnings.append(f"w/c {wc:.3f} exceeds durability limit {max_wc} -> limited to {max_wc} (cl. 5.1.1)")
        wc = max_wc
    res.check("w/c within durability limit", wc <= max_wc, f"{wc} <= {max_wc} ({inp.exposure}, {pre.upper()})",
              [dref])
    min_grade = dur[f"{pre}_min_grade"]
    if min_grade:
        res.check("grade >= minimum for exposure", fck >= ct.grade_num(min_grade), f"M{fck} vs {min_grade}", [dref])

    # --- 5.3 water content
    t4 = ct.tables()["table_4_water_content"]
    w0 = t4["by_size_mm"][inp.max_aggregate_size_mm]
    w0 -= t4["aggregate_shape_reduction_kg"][inp.aggregate_shape]
    res.step("water content for 50 mm slump", w0, "kg/m3", refs=[ct.ref("table_4_water_content", w0)])
    adj = (inp.slump_mm - 50) / 25 * t4["slump_adjust_percent_per_25mm"] / 100
    w1 = res.step("water adjusted for slump", r2(w0 * (1 + adj)), "kg/m3",
                  f"{w0} x (1 + {adj:.4f})  [3 % per 25 mm slump]",
                  [ct.Ref(code="IS 10262", item="cl. 5.3", status="ocr_confirmed")])
    red = inp.admixture.water_reduction_percent / 100 if inp.admixture.type != "none" else 0
    water = w1 * (1 - red)
    if red:
        res.step("water after admixture reduction", r2(water), "kg/m3", f"{w1} x (1 - {red})")
    if inp.round_like_annex_a:
        water = math.ceil(water - 1e-9)
    water = res.step("free water content", water, "kg/m3")

    # --- 5.4 cementitious content
    cm = water / wc
    fa_frac = inp.fly_ash_percent / 100
    if fa_frac >= 0.20 and inp.increase_cm_for_fly_ash:
        cm *= 1.10
        res.warnings.append("cementitious content increased by 10 % for fly ash >= 20 % (cl. 5.4.1, preliminary trial)")
    if inp.round_like_annex_a:
        cm = math.ceil(cm - 1e-9)
    cm = res.step("cementitious content", r2(cm), "kg/m3", f"water / (w/c) = {water} / {wc}")
    size_adj = ct.tables()["table_5_durability"]["adjustments_table_6"][inp.max_aggregate_size_mm]
    min_c = dur[f"{pre}_min_cement_kg_m3"] + size_adj
    if cm < min_c:
        res.warnings.append(f"cementitious content raised from {cm} to minimum {min_c} kg/m3 (IS 456 Table 5/6)")
        cm = min_c
    res.check("cement content >= minimum", cm >= min_c, f"{cm} >= {min_c} kg/m3", [dref])
    cement = r2(cm * (1 - fa_frac))
    fly_ash = r2(cm * fa_frac)
    res.check("cement content <= maximum (excl. fly ash)", cement <= inp.max_cement_content,
              f"{cement} <= {inp.max_cement_content} kg/m3",
              [ct.Ref(code="IS 456", item="cl. 8.2.4.2", status="ocr_confirmed")])
    wcm = water / cm
    res.step("final water-cementitious ratio", r2(wcm, 3))

    # --- 5.5 coarse aggregate proportion
    t5 = ct.tables()["table_5_coarse_aggregate_volume"]
    ca0 = t5["by_size_mm"][inp.max_aggregate_size_mm][ZONES[inp.fine_aggregate_zone]]
    res.step("CA volume fraction at w/c 0.50", ca0, None, refs=[ct.ref("table_5_coarse_aggregate_volume", ca0)])
    ca = ca0 + (0.50 - wcm) / 0.05 * t5["wc_correction_per_0_05"]
    red_ca = inp.ca_reduction_percent / 100 or (0.10 if inp.pumpable else 0)
    if red_ca:
        ca *= (1 - min(red_ca, t5["pumpable_reduction_max"]))
    ca = res.step("CA volume fraction (corrected)", r2(ca, 3), None,
                  f"{ca0} + (0.50 - {wcm:.3f})/0.05 x 0.01" + (f", x (1 - {red_ca})" if red_ca else ""),
                  [ct.Ref(code="IS 10262", item="cl. 5.5.1/5.5.2", status="ocr_confirmed")])

    # --- 5.7 absolute volumes
    air = ct.tables()["table_3_entrapped_air"]["by_size_mm"][inp.max_aggregate_size_mm] / 100
    res.step("entrapped air", air, "m3", refs=[ct.ref("table_3_entrapped_air", air)])
    v_c = cement / inp.sg_cement / 1000
    v_f = fly_ash / inp.sg_fly_ash / 1000
    v_w = water / 1000
    adm_mass = r2(cm * inp.admixture.dosage_percent / 100) if inp.admixture.type != "none" else 0.0
    v_a = adm_mass / inp.admixture.specific_gravity / 1000 if adm_mass else 0.0
    v_agg = (1 - air) - (v_c + v_f + v_w + v_a)
    res.step("volume of all-in aggregate", r2(v_agg, 4), "m3",
             f"(1 - {air}) - ({v_c:.4f} + {v_f:.4f} + {v_w:.4f} + {v_a:.4f})",
             [ct.Ref(code="IS 10262", item="cl. 5.7", status="ocr_confirmed")])
    ca_ssd = v_agg * ca * inp.sg_coarse * 1000
    fa_ssd = v_agg * (1 - ca) * inp.sg_fine * 1000
    res.step("coarse aggregate (SSD)", r2(ca_ssd, 1), "kg/m3")
    res.step("fine aggregate (SSD)", r2(fa_ssd, 1), "kg/m3")

    # --- field adjustment (Annex A-10 dry aggregate / Annex B-11 wet aggregate)
    ca_field = field_mass(ca_ssd, inp.water_absorption_coarse_percent, inp.surface_moisture_coarse_percent)
    fa_field = field_mass(fa_ssd, inp.water_absorption_fine_percent, inp.surface_moisture_fine_percent)
    water_field = water + (ca_ssd - ca_field) + (fa_ssd - fa_field)

    refs = [r for st in res.steps for r in st.refs] + [r for c in res.checks for r in c.refs]
    res.outputs = {
        "mix_grade": f"M{fck}",
        "per_m3_ssd": {"cement_kg": cement, "fly_ash_kg": fly_ash, "water_kg": water, "fine_aggregate_kg": r2(fa_ssd, 1),
                       "coarse_aggregate_kg": r2(ca_ssd, 1), "admixture_kg": adm_mass},
        "per_m3_field": {"cement_kg": cement, "fly_ash_kg": fly_ash, "water_to_add_kg": r2(water_field, 1),
                         "fine_aggregate_kg": r2(fa_field, 1), "coarse_aggregate_kg": r2(ca_field, 1),
                         "admixture_kg": adm_mass},
        "w_c_ratio": r2(wcm, 3),
        "target_strength_n_mm2": target,
        "ratio_by_mass": f"1 : {fa_ssd / cm:.2f} : {ca_ssd / cm:.2f} (w/cm {wcm:.2f})",
        "cement_bags_per_m3": r2(cement / 50, 2),
        "ca_volume_fraction": ca,
        "code_versions": ct.editions_used(refs),
        "confidence": cf.combine([
            cf.refs_factor(refs), wc_factor,
            cf.Factor(name="standard deviation", score=1.0 if inp.known_std_dev else 0.8, weight=1,
                      detail="site data (>= 30 results)" if inp.known_std_dev else "assumed from IS 10262 Table 2"),
            cf.checks_factor(res.checks)]),
    }
    res.warnings.append("Proportions are for TRIAL MIX No. 1; confirm by trial batches (IS 10262 cl. 5.8).")
    return res.finalize()
