"""Reinforcement: bar areas/weights, development & lap length (IS 456 cl. 26.2), and bar bending
schedule cutting lengths. Bend/hook allowances follow common SP 34 practice and are configurable."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel

from concrete_rag.calc import code_tables as ct

STANDARD_DIAS = [6, 8, 10, 12, 16, 20, 25, 28, 32, 36, 40]

# detailing conventions (SP 34 / common site practice) - not IS 456 values
HOOK_180_LEN_D = 9          # standard U-hook allowance per end
BEND_90_LEN_D = 8
STIRRUP_HOOK_135_LEN_D = 10
BEND_DEDUCTION_D = {45: 1, 90: 2, 135: 3}


def bar_area(dia_mm: float) -> float:
    return math.pi * dia_mm ** 2 / 4


def unit_weight_kg_m(dia_mm: float) -> float:
    """d^2/162 kg/m (steel density 7850 kg/m3) - the standard mass used under IS 1200 (Part 8) cl. 11.1."""
    return dia_mm ** 2 / 162.0


def development_length(dia_mm: float, grade: str, fy: float = 500, deformed: bool = True,
                       compression: bool = False) -> tuple[float, list[ct.Ref]]:
    """Ld = phi sigma_s / (4 tau_bd), sigma_s = 0.87 fy (cl. 26.2.1)."""
    t = ct.tables()["bond_stress_26_2_1_1"]
    g = ct.grade_key(ct.grade_num(grade), list(t["grades"]))
    tbd = t["grades"][g]
    if deformed:
        tbd *= 1 + t["deformed_bar_increase"]
    if compression:
        tbd *= 1 + t["compression_increase"]
    ld = dia_mm * 0.87 * fy / (4 * tbd)
    return ld, [ct.ref("bond_stress_26_2_1_1", tbd), ct.Ref(code="IS 456", item="cl. 26.2.1", status="ocr_confirmed")]


def lap_length(dia_mm: float, grade: str, fy: float = 500, case: Literal["flexural_tension", "direct_tension",
               "compression"] = "flexural_tension") -> tuple[float, list[ct.Ref]]:
    """cl. 26.2.5.1(c): flexural tension max(Ld, 30 phi); direct tension max(2 Ld, 30 phi); compression Ld(c) >= 24 phi."""
    if case == "compression":
        ld, refs = development_length(dia_mm, grade, fy, compression=True)
        lap = max(ld, 24 * dia_mm)
    else:
        ld, refs = development_length(dia_mm, grade, fy)
        lap = max(ld if case == "flexural_tension" else 2 * ld, 30 * dia_mm)
    return lap, refs + [ct.Ref(code="IS 456", item="cl. 26.2.5.1", status="ocr_confirmed")]


class BarMark(BaseModel):
    mark: str
    member: str
    dia_mm: float
    shape: Literal["straight", "hooked_both", "bent_up", "stirrup_rect", "L_bar"]
    a_mm: float                      # main dimension (length / stirrup width)
    b_mm: float = 0                  # stirrup height / L leg
    nos: int = 1
    members: int = 1
    bends_90: int = 0
    bends_45: int = 0
    laps: int = 0
    lap_len_mm: float = 0


def cutting_length(m: BarMark) -> float:
    d = m.dia_mm
    if m.shape == "straight":
        L = m.a_mm
    elif m.shape == "hooked_both":
        L = m.a_mm + 2 * HOOK_180_LEN_D * d
    elif m.shape == "L_bar":
        L = m.a_mm + m.b_mm - BEND_DEDUCTION_D[90] * d
    elif m.shape == "bent_up":        # crank: extra 0.42 x crank height per bend, 45-degree bends
        L = m.a_mm + 2 * 0.42 * m.b_mm - 4 * BEND_DEDUCTION_D[45] * d
    else:                              # closed rectangular stirrup with two 135-degree hooks
        L = 2 * (m.a_mm + m.b_mm) + 2 * STIRRUP_HOOK_135_LEN_D * d - 3 * BEND_DEDUCTION_D[90] * d \
            - 2 * BEND_DEDUCTION_D[135] * d
    L += m.laps * m.lap_len_mm - m.bends_90 * BEND_DEDUCTION_D[90] * d - m.bends_45 * BEND_DEDUCTION_D[45] * d
    return L


def bbs(marks: list[BarMark]) -> dict:
    rows, by_dia = [], {}
    for m in marks:
        cl = cutting_length(m)
        total_m = cl / 1000 * m.nos * m.members
        kg = total_m * unit_weight_kg_m(m.dia_mm)
        by_dia[m.dia_mm] = by_dia.get(m.dia_mm, 0) + kg
        rows.append({**m.model_dump(), "cutting_length_mm": round(cl), "total_length_m": round(total_m, 2),
                     "unit_weight_kg_m": round(unit_weight_kg_m(m.dia_mm), 3), "weight_kg": round(kg, 2)})
    return {"rows": rows, "weight_by_dia_kg": {f"{k:g}mm": round(v, 2) for k, v in sorted(by_dia.items())},
            "total_weight_kg": round(sum(by_dia.values()), 2),
            "conventions": "hook 9d, 135-deg stirrup hook 10d, bend deductions 45:1d 90:2d 135:3d (SP 34 practice); "
                           "mass d^2/162 kg/m; laps measured per IS 1200 (Part 8) cl. 11.1"}
