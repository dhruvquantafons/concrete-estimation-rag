"""Limit-state design of common RCC elements per IS 456 : 2000 (deterministic, cited).

Units: mm, N, N/mm2 internally; loads in kN, kN/m, kN/m2; moments in kNm.
Scope: singly/doubly reinforced rectangular beams (flexure + shear), one-way slabs, short axially
loaded columns (cl. 39.3), isolated square footings. Results are design aids - not a substitute for
a checked structural design by a qualified engineer.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, Field

from concrete_rag.calc import code_tables as ct
from concrete_rag.calc.base import CalcResult, r2
from concrete_rag.calc.steel import bar_area


def _R(item: str, status: str = "ocr_confirmed") -> ct.Ref:
    return ct.Ref(code="IS 456", item=item, status=status)


def xu_max_ratio(fy: float) -> tuple[float, ct.Ref]:
    t = ct.tables()["flexure_38_1"]["xu_max_by_fy"]
    if fy in t:
        v = t[fy]
    else:  # cl. 38.1: xu,max/d = 0.0035 / (0.0055 + 0.87 fy / Es)
        v = 0.0035 / (0.0055 + 0.87 * fy / 200000)
    return v, ct.ref("flexure_38_1", v)


def mu_lim(fck: float, fy: float, b: float, d: float) -> tuple[float, float, ct.Ref]:
    k, ref = xu_max_ratio(fy)
    xu = k * d
    m = 0.36 * fck * b * xu * (d - 0.42 * xu)        # Annex G-1.1(c)
    return m, k, ref


def ast_singly(mu_nmm: float, fck: float, fy: float, b: float, d: float) -> float:
    """Annex G-1.1(b): Mu = 0.87 fy Ast d (1 - Ast fy / (b d fck))."""
    disc = 1 - 4.6 * mu_nmm / (fck * b * d * d)
    if disc < 0:
        raise ValueError("section too small for this moment")
    return 0.5 * fck / fy * (1 - math.sqrt(disc)) * b * d


def tau_c(fck: int, pt: float) -> tuple[float, ct.Ref]:
    t = ct.tables()["table_19_tau_c"]
    g = ct.grade_key(fck, list(t["grades"]))
    v = ct.interp(min(max(pt, t["pt"][0]), t["pt"][-1]), t["pt"], t["grades"][g])
    return v, ct.ref("table_19_tau_c", v, f"{g}, pt={pt:.2f}")


def tau_c_max(fck: int) -> tuple[float, ct.Ref]:
    t = ct.tables()["table_20_tau_c_max"]["grades"]
    g = ct.grade_key(fck, list(t))
    return t[g], ct.ref("table_20_tau_c_max", t[g], g)


def span_depth_allowed(support: str, span_m: float, fy: float, ast_req: float, ast_prov: float, b: float, d: float,
                       asc_prov: float = 0.0) -> tuple[float, str]:
    """cl. 23.2.1: allowable L/d = basic x (10/span if span > 10 m) x MF_tension (Fig. 4) x MF_compression (Fig. 5).
    Fig. 4 / Fig. 5 are read with the usual closed-form fits of the curves (as used with SP 16):
      MF_t = 1 / (0.225 + 0.003 22 fs - 0.625 log10(1/pt)) <= 2.0,  fs = 0.58 fy Ast,req/Ast,prov
      MF_c = 1 + pc / (3 + pc) <= 1.5"""
    basic = ct.tables()["detailing_limits"]["span_depth_basic"][support]
    long_span = 10 / span_m if span_m > 10 and support != "cantilever" else 1.0
    fs = 0.58 * fy * ast_req / ast_prov
    pt = max(100 * ast_prov / (b * d), 0.1)
    mf_t = min(2.0, max(0.3, 1 / (0.225 + 0.00322 * fs - 0.625 * math.log10(1 / pt))))
    pc = 100 * asc_prov / (b * d)
    mf_c = min(1.5, 1 + pc / (3 + pc))
    allowed = basic * long_span * mf_t * mf_c
    note = (f"basic {basic}" + (f" x 10/{span_m:g}" if long_span != 1 else "") + f" x MF_t {mf_t:.2f} "
            f"(fs {fs:.0f}, pt {pt:.2f} %)" + (f" x MF_c {mf_c:.2f} (pc {pc:.2f} %)" if pc else "")
            + f" = {allowed:.1f}")
    return allowed, note


# ------------------------------------------------------------------ beams
class BeamInput(BaseModel):
    b_mm: float = Field(..., gt=0)
    D_mm: float = Field(..., gt=0)
    span_m: float | None = None
    grade: str = "M25"
    fy: float = 500
    clear_cover_mm: float = 25
    main_bar_dia_mm: float = 16
    stirrup_dia_mm: float = 8
    stirrup_legs: int = 2
    Mu_kNm: float | None = None           # factored moment; or give loads below
    Vu_kN: float | None = None            # factored shear
    dead_load_kN_m: float | None = None   # service UDL (excluding self weight)
    live_load_kN_m: float | None = None
    support: Literal["simply_supported", "cantilever"] = "simply_supported"
    load_factor: float = 1.5
    concrete_unit_weight_kN_m3: float = 25.0


def design_beam(inp: BeamInput) -> CalcResult:
    res = CalcResult(calculator="rcc_beam_is456", inputs=inp.model_dump())
    fck, fy, b, D = ct.grade_num(inp.grade), inp.fy, inp.b_mm, inp.D_mm
    d = res.step("effective depth d", D - inp.clear_cover_mm - inp.stirrup_dia_mm - inp.main_bar_dia_mm / 2, "mm",
                 "D - cover - stirrup dia - bar dia/2")
    Mu, Vu = inp.Mu_kNm, inp.Vu_kN
    if Mu is None or Vu is None:
        if inp.span_m is None or inp.dead_load_kN_m is None:
            raise ValueError("give Mu_kNm & Vu_kN, or span_m with dead/live loads")
        sw = inp.concrete_unit_weight_kN_m3 * b * D / 1e6
        w = res.step("factored UDL wu", r2(inp.load_factor * (inp.dead_load_kN_m + sw + (inp.live_load_kN_m or 0)), 3),
                     "kN/m", f"{inp.load_factor} x (DL + self weight {sw:.2f} + LL)", [_R("Table 18 / cl. 36.4")])
        L = inp.span_m
        if inp.support == "simply_supported":
            Mu = Mu if Mu is not None else w * L * L / 8
            Vu = Vu if Vu is not None else w * L / 2
        else:
            Mu = Mu if Mu is not None else w * L * L / 2
            Vu = Vu if Vu is not None else w * L
    res.step("design moment Mu", r2(Mu), "kNm")
    res.step("design shear Vu", r2(Vu), "kN")

    mlim, k, kref = mu_lim(fck, fy, b, d)
    res.step("Mu,lim = 0.36 fck b xu,max (d - 0.42 xu,max)", r2(mlim / 1e6), "kNm", refs=[kref, _R("Annex G-1.1")])
    mu = Mu * 1e6
    if mu <= mlim:
        ast = ast_singly(mu, fck, fy, b, d)
        asc = 0.0
        res.step("Ast required (singly reinforced)", r2(ast, 0), "mm2", "Annex G-1.1(b)", [_R("Annex G-1.1(b)")])
    else:
        # doubly reinforced: fsc approximated as 0.87 fy x min(1, strain ratio) (SP 16 Table F gives exact values)
        ast1 = 0.36 * fck * b * k * d / (0.87 * fy)
        d_c = inp.clear_cover_mm + inp.stirrup_dia_mm + inp.main_bar_dia_mm / 2
        xu = k * d
        esc = 0.0035 * (xu - d_c) / xu
        fsc = min(0.87 * fy, esc * 200000)
        asc = (mu - mlim) / ((fsc - 0.446 * fck) * (d - d_c))
        ast = ast1 + asc * fsc / (0.87 * fy)
        res.warnings.append("Mu > Mu,lim: doubly reinforced; fsc from strain compatibility (elastic-perfectly-plastic) "
                            "- check against SP 16 Table F for HYSD bars")
        res.step("Asc required", r2(asc, 0), "mm2", refs=[_R("Annex G-1.2")])
        res.step("Ast required (doubly reinforced)", r2(ast, 0), "mm2", refs=[_R("Annex G-1.2")])
    ast_min = 0.85 * b * d / fy
    ast_max = 0.04 * b * D
    lim = ct.ref("detailing_limits")
    if ast < ast_min:
        res.warnings.append(f"Ast raised to minimum 0.85 bd/fy = {ast_min:.0f} mm2 (cl. 26.5.1.1 a)")
        ast = ast_min
    res.check("Ast >= 0.85 bd/fy", ast >= ast_min, f"{ast:.0f} >= {ast_min:.0f} mm2", [lim])
    res.check("Ast <= 0.04 bD", ast <= ast_max, f"{ast:.0f} <= {ast_max:.0f} mm2", [lim])
    if asc:
        res.check("Asc <= 0.04 bD", asc <= ast_max, f"{asc:.0f} <= {ast_max:.0f} mm2", [_R("cl. 26.5.1.2")])
    n = math.ceil(ast / bar_area(inp.main_bar_dia_mm))
    ast_prov = n * bar_area(inp.main_bar_dia_mm)
    res.step("tension bars provided", f"{n} x {inp.main_bar_dia_mm:g} mm (Ast = {ast_prov:.0f} mm2)")

    # --- shear (cl. 40)
    tv = Vu * 1e3 / (b * d)
    pt = 100 * ast_prov / (b * d)
    tc, tcref = tau_c(fck, pt)
    tcm, tcmref = tau_c_max(fck)
    res.step("nominal shear stress tau_v = Vu/(b d)", r2(tv, 3), "N/mm2", refs=[_R("cl. 40.1")])
    res.step("tau_c", r2(tc, 3), "N/mm2", refs=[tcref])
    res.check("tau_v <= tau_c,max", tv <= tcm, f"{tv:.2f} <= {tcm}", [tcmref])
    asv = inp.stirrup_legs * bar_area(inp.stirrup_dia_mm)
    fyv = min(fy, 415)    # cl. 40.4: fy of stirrups not taken > 415
    sv_max = min(0.75 * d, 300)
    sv_min_reinf = 0.87 * fyv * asv / (0.4 * b)
    if tv <= tc:
        sv = min(sv_min_reinf, sv_max)
        res.step("stirrups: minimum shear reinforcement governs", r2(sv, 0), "mm",
                 "0.87 fy Asv / (0.4 b)", [_R("cl. 26.5.1.6")])
    else:
        vus = Vu * 1e3 - tc * b * d
        sv = min(0.87 * fyv * asv * d / vus, sv_max, sv_min_reinf)
        res.step("Vus = Vu - tau_c b d", r2(vus / 1e3), "kN", refs=[_R("cl. 40.4")])
        res.step("stirrup spacing sv = 0.87 fy Asv d / Vus", r2(sv, 0), "mm", refs=[_R("cl. 40.4 a")])
    sv = math.floor(sv / 5) * 5
    res.check("stirrup spacing <= min(0.75 d, 300)", sv <= sv_max, f"{sv} <= {sv_max:.0f} mm", [lim])

    # --- deflection (cl. 23.2.1 span/depth with Fig. 4 / Fig. 5 modification factors)
    if inp.span_m:
        ratio = inp.span_m * 1000 / d
        allowed, note = span_depth_allowed(inp.support, inp.span_m, fy, ast, ast_prov, b, d, asc)
        res.check("span/d <= allowable (cl. 23.2.1)", ratio <= allowed, f"L/d = {ratio:.1f} <= {note}",
                  [_R("cl. 23.2.1 / Fig. 4 / Fig. 5")])
        res.warnings.append("Fig. 4/5 factors use curve fits; read the figures for a borderline result.")

    res.outputs = {"d_mm": d, "Ast_required_mm2": r2(ast, 0), "Asc_required_mm2": r2(asc, 0),
                   "tension_bars": f"{n}-{inp.main_bar_dia_mm:g}mm", "Ast_provided_mm2": r2(ast_prov, 0),
                   "pt_percent": r2(pt, 3), "stirrups": f"{inp.stirrup_legs}L-{inp.stirrup_dia_mm:g}mm @ {sv:.0f} mm c/c",
                   "Mu_kNm": r2(Mu), "Vu_kN": r2(Vu), "Mu_lim_kNm": r2(mlim / 1e6)}
    return res.finalize()


# ------------------------------------------------------------------ one-way slab
class SlabInput(BaseModel):
    span_m: float = Field(..., gt=0, description="effective span (cl. 22.2)")
    D_mm: float | None = None
    grade: str = "M25"
    fy: float = 500
    clear_cover_mm: float = 20
    bar_dia_mm: float = 10
    dist_bar_dia_mm: float = 8
    live_load_kN_m2: float = 3.0
    floor_finish_kN_m2: float = 1.0
    support: Literal["simply_supported", "cantilever", "continuous"] = "simply_supported"
    concrete_unit_weight_kN_m3: float = 25.0


def design_slab_one_way(inp: SlabInput) -> CalcResult:
    res = CalcResult(calculator="rcc_one_way_slab_is456", inputs=inp.model_dump())
    fck, fy = ct.grade_num(inp.grade), inp.fy
    basic = ct.tables()["detailing_limits"]["span_depth_basic"][inp.support]
    D = inp.D_mm
    if D is None:   # trial depth from basic span/d with an assumed modification factor of 1.4 (HYSD, low pt)
        d_trial = inp.span_m * 1000 / (basic * 1.4)
        D = math.ceil((d_trial + inp.clear_cover_mm + inp.bar_dia_mm / 2) / 5) * 5
        res.warnings.append(f"D not given: trial D = {D} mm from span/(basic x 1.4); verify deflection with Fig. 4")
    d = res.step("effective depth d", D - inp.clear_cover_mm - inp.bar_dia_mm / 2, "mm")
    w = 1.5 * (inp.concrete_unit_weight_kN_m3 * D / 1000 + inp.floor_finish_kN_m2 + inp.live_load_kN_m2)
    res.step("factored load wu (per m width)", r2(w, 3), "kN/m2", "1.5 (self weight + FF + LL)",
             [_R("Table 18")])
    L = inp.span_m
    coef = {"simply_supported": 1 / 8, "cantilever": 1 / 2, "continuous": 1 / 10}[inp.support]
    Mu = res.step("Mu per m width", r2(w * L * L * coef, 3), "kNm/m",
                  f"wu L2 x {coef:.3f}" + (" (approx. Table 12 coefficient)" if inp.support == "continuous" else ""))
    Vu = w * L * (1.0 if inp.support == "cantilever" else 0.5)
    mlim, _, kref = mu_lim(fck, fy, 1000, d)
    res.check("Mu <= Mu,lim", Mu * 1e6 <= mlim, f"{Mu:.2f} <= {mlim / 1e6:.2f} kNm/m", [kref])
    ast = ast_singly(min(Mu * 1e6, mlim), fck, fy, 1000, d)
    lim = ct.tables()["detailing_limits"]
    ast_min = lim["slab_min_steel_fraction"]["hysd" if fy > 250 else "mild_steel"] * 1000 * D
    res.step("Ast required", r2(ast, 0), "mm2/m")
    ast = max(ast, ast_min)
    ast_req = ast
    sp_main = min(1000 * bar_area(inp.bar_dia_mm) / ast, 3 * d, 300)
    sp_main = math.floor(sp_main / 5) * 5
    dist = ast_min
    sp_dist = math.floor(min(1000 * bar_area(inp.dist_bar_dia_mm) / dist, 5 * d, 450) / 5) * 5
    lref = ct.ref("detailing_limits")
    res.check("main bar dia <= D/8", inp.bar_dia_mm <= D / 8, f"{inp.bar_dia_mm} <= {D / 8:.1f}", [lref])
    tc, tcref = tau_c(fck, 100 * (1000 * bar_area(inp.bar_dia_mm) / sp_main) / (1000 * d))
    k = 1.3 if D <= 150 else max(1.0, 1.3 - (D - 150) * 0.3 / 150)    # cl. 40.2.1.1
    tv = Vu * 1e3 / (1000 * d)
    res.check("one-way shear tau_v <= k tau_c", tv <= k * tc, f"{tv:.3f} <= {k:.2f} x {tc:.3f}", [tcref, _R("cl. 40.2.1.1")])
    ratio = L * 1000 / d
    ast_prov = 1000 * bar_area(inp.bar_dia_mm) / sp_main
    allowed, note = span_depth_allowed(inp.support, L, fy, ast_req, ast_prov, 1000, d)
    res.check("span/d <= allowable (cl. 23.2.1)", ratio <= allowed, f"L/d = {ratio:.1f} <= {note}",
              [_R("cl. 23.2.1 / Fig. 4")])
    res.outputs = {"D_mm": D, "d_mm": d, "Mu_kNm_per_m": Mu, "Ast_main_mm2_per_m": r2(ast, 0),
                   "main_bars": f"{inp.bar_dia_mm:g}mm @ {sp_main} mm c/c",
                   "distribution_bars": f"{inp.dist_bar_dia_mm:g}mm @ {sp_dist} mm c/c",
                   "Ast_dist_mm2_per_m": r2(dist, 0)}
    return res.finalize()


# ------------------------------------------------------------------ column
class ColumnInput(BaseModel):
    b_mm: float
    D_mm: float
    Pu_kN: float = Field(..., description="factored axial load")
    unsupported_length_m: float = 3.0
    effective_length_factor: float = 1.0      # Annex E / Table 28
    grade: str = "M25"
    fy: float = 500
    bar_dia_mm: float = 16
    tie_dia_mm: float = 8


def design_column_axial(inp: ColumnInput) -> CalcResult:
    res = CalcResult(calculator="rcc_short_column_axial_is456", inputs=inp.model_dump())
    fck, fy, b, D = ct.grade_num(inp.grade), inp.fy, inp.b_mm, inp.D_mm
    lex = inp.effective_length_factor * inp.unsupported_length_m * 1000
    slender = max(lex / b, lex / D)
    res.check("short column: lex/least lateral dimension < 12", slender < 12, f"{slender:.1f} < 12", [_R("cl. 25.1.2")])
    emin = max(inp.unsupported_length_m * 1000 / 500 + min(b, D) / 30, 20)
    res.step("minimum eccentricity e_min", r2(emin, 1), "mm", "l/500 + D/30 >= 20", [_R("cl. 25.4")])
    res.check("e_min <= 0.05 D (cl. 39.3 formula applicable)", emin <= 0.05 * min(b, D),
              f"{emin:.1f} <= {0.05 * min(b, D):.1f} mm (else design for Mu = Pu e_min)", [_R("cl. 39.3")])
    Ag = b * D
    asc = (inp.Pu_kN * 1e3 - 0.4 * fck * Ag) / (0.67 * fy - 0.4 * fck)
    res.step("Asc = (Pu - 0.4 fck Ag)/(0.67 fy - 0.4 fck)", r2(asc, 0), "mm2", refs=[_R("cl. 39.3")])
    lim = ct.tables()["detailing_limits"]["column_long_steel_fraction"]
    asc = max(asc, lim["min"] * Ag)
    n = max(math.ceil(asc / bar_area(inp.bar_dia_mm)), 4)
    n += n % 2
    prov = n * bar_area(inp.bar_dia_mm)
    lref = ct.ref("detailing_limits")
    res.check("0.8 % <= p <= 4 %", lim["min"] <= prov / Ag <= lim["max"], f"p = {100 * prov / Ag:.2f} %", [lref])
    tie_dia = max(inp.tie_dia_mm, inp.bar_dia_mm / 4, 6)
    pitch = min(min(b, D), 16 * inp.bar_dia_mm, 300)
    res.outputs = {"longitudinal_bars": f"{n}-{inp.bar_dia_mm:g}mm", "Asc_provided_mm2": r2(prov, 0),
                   "steel_percent": r2(100 * prov / Ag, 2), "ties": f"{tie_dia:g}mm @ {pitch:.0f} mm c/c",
                   "Pu_capacity_kN": r2((0.4 * fck * (Ag - prov) + 0.67 * fy * prov) / 1e3, 1)}
    res.step("lateral ties", res.outputs["ties"], None, "dia >= max(phi/4, 6 mm); pitch <= min(least dim, 16 phi, 300)",
             [_R("cl. 26.5.3.2")])
    return res.finalize()


# ------------------------------------------------------------------ isolated footing
class FootingInput(BaseModel):
    column_b_mm: float
    column_D_mm: float
    service_load_kN: float
    sbc_kN_m2: float = Field(..., description="safe bearing capacity")
    grade: str = "M25"
    fy: float = 500
    bar_dia_mm: float = 12
    clear_cover_mm: float = 50


def design_footing_square(inp: FootingInput) -> CalcResult:
    res = CalcResult(calculator="rcc_isolated_footing_is456", inputs=inp.model_dump())
    fck, fy = ct.grade_num(inp.grade), inp.fy
    A = 1.10 * inp.service_load_kN / inp.sbc_kN_m2
    B = math.ceil(math.sqrt(A) * 20) / 20
    res.step("plan size B x B (10 % for self weight)", f"{B:.2f} m x {B:.2f} m", None, "sqrt(1.1 P / SBC)")
    qu = 1.5 * inp.service_load_kN / (B * B)
    res.step("factored net upward pressure qu", r2(qu, 2), "kN/m2")
    cb, cD = sorted((inp.column_b_mm / 1000, inp.column_D_mm / 1000))
    cant = (B - cb) / 2           # longer cantilever (from the shorter column side) governs a square footing
    Mu = qu * B * cant ** 2 / 2
    res.step("Mu at column face (cl. 34.2.3.2)", r2(Mu, 2), "kNm", f"qu B c2/2, c = (B - {cb:g})/2 = {cant:.3f} m",
             refs=[_R("cl. 34.2.3.2")])
    ks = min(1.0, 0.5 + cb / cD)   # cl. 31.6.3.1, beta_c = short/long side of column
    tp_allow = ks * 0.25 * math.sqrt(fck)
    d = 200.0
    for _ in range(200):
        mlim, _, _ = mu_lim(fck, fy, B * 1000, d)
        # one-way shear at d from face
        x = cant - d / 1000
        Vu1 = qu * B * max(x, 0)
        ast = ast_singly(min(Mu * 1e6, mlim * 0.999), fck, fy, B * 1000, d)
        tc1, _ = tau_c(fck, 100 * ast / (B * 1000 * d))
        # punching shear at d/2 from face (cl. 31.6.3.1)
        p1, p2 = cb + d / 1000, cD + d / 1000
        Vp = qu * (B * B - p1 * p2)
        tp = Vp * 1e3 / (2 * (p1 + p2) * 1000 * d)
        ok = Mu * 1e6 <= mlim and Vu1 * 1e3 / (B * 1000 * d) <= tc1 and tp <= tp_allow
        if ok:
            break
        d += 25
    res.step("effective depth d (iterated for bending, one-way and punching shear)", d, "mm",
             refs=[_R("cl. 34.2.4 / 31.6.3.1")])
    ast = max(ast, 0.0012 * B * 1000 * (d + inp.clear_cover_mm + inp.bar_dia_mm))
    n = math.ceil(ast / bar_area(inp.bar_dia_mm))
    sp = math.floor((B * 1000 - 2 * inp.clear_cover_mm) / max(n - 1, 1) / 5) * 5
    res.check("punching shear <= ks 0.25 sqrt(fck)", tp <= tp_allow, f"{tp:.3f} <= {ks:.2f} x {0.25 * math.sqrt(fck):.3f}",
              [_R("cl. 31.6.3.1")])
    res.check("one-way shear at d from face <= tau_c", Vu1 * 1e3 / (B * 1000 * d) <= tc1,
              f"{Vu1 * 1e3 / (B * 1000 * d):.3f} <= {tc1:.3f}", [_R("cl. 34.2.4.1 a")])
    res.outputs = {"B_m": B, "D_mm": d + inp.clear_cover_mm + inp.bar_dia_mm, "d_mm": d,
                   "bars_each_way": f"{n}-{inp.bar_dia_mm:g}mm @ {sp} mm c/c", "Ast_each_way_mm2": r2(ast, 0)}
    return res.finalize()
