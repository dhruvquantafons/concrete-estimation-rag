"""Quantity take-off for RCC elements following IS 1200 (Part 2) : 2025 concrete, (Part 5) : 2025
formwork and (Part 8) reinforcement measurement rules."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from pydantic import BaseModel, Field

from concrete_rag.calc.code_tables import Ref

R_P2 = lambda item: Ref(code="IS 1200 (Part 2)", item=item, status="ocr_confirmed")  # noqa: E731
R_P5 = lambda item: Ref(code="IS 1200 (Part 5)", item=item, status="ocr_confirmed")  # noqa: E731


def rnd(x: float, step: str) -> float:
    """Round half-up to the IS 1200 precision ('0.01' or '0.005')."""
    q = Decimal(step)
    return float((Decimal(str(x)) / q).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * q)


class Opening(BaseModel):
    width_m: float
    height_m: float
    count: int = 1


class Element(BaseModel):
    """Geometry of one element type. Dimensions in metres (as on drawings)."""
    id: str
    type: Literal["footing", "column", "beam", "slab", "wall", "lintel", "chajja", "staircase_waist", "pcc_bed"]
    length_m: float = Field(..., gt=0, description="beam: clear length face-to-face of columns (cl. 5.2.2.3); "
                                                    "column: top of base/slab to underside of slab above (5.2.2.1)")
    width_m: float = Field(..., gt=0)
    depth_m: float = Field(..., gt=0, description="beam: below slab soffit; slab/footing/pcc: thickness")
    count: int = 1
    grade: str = "M25"
    exposure: str = "moderate"
    level: Literal["below_plinth", "above_plinth"] = "above_plinth"
    floor: int = 1
    openings: list[Opening] = Field(default_factory=list)
    steel_kg_per_m3: float | None = Field(None, description="reinforcement density if no BBS (kg/m3)")
    formwork_sides: Literal["auto", "none"] = "auto"


def _vol_and_form(e: Element) -> tuple[float, float, list[str]]:
    L = rnd(e.length_m, "0.01")
    W = rnd(e.width_m, "0.01")
    D = rnd(e.depth_m, "0.005" if e.type in ("slab", "chajja") else "0.01")
    notes = []
    vol = L * W * D
    # openings: no deduction for openings up to 0.1 m2 (Part 2 cl. 5.1.8 b)
    for o in e.openings:
        a = o.width_m * o.height_m
        if a > 0.1:
            vol -= a * D * o.count if e.type in ("slab",) else a * W * o.count
            notes.append(f"deducted opening {o.width_m}x{o.height_m} m x{o.count}")
        else:
            notes.append(f"opening {o.width_m}x{o.height_m} m not deducted (<= 0.1 m2, Part 2 cl. 5.1.8 b)")
    # formwork: contact area (Part 5 cl. 6.1); openings <= 0.4 m2 not deducted (cl. 6.5)
    form = 0.0
    if e.formwork_sides == "auto":
        if e.type == "column":
            form = 2 * (W + D) * L                               # perimeter x height (length_m = height)
        elif e.type in ("beam", "lintel"):
            form = (W + 2 * D) * L                               # soffit + two sides below slab
        elif e.type in ("slab", "chajja"):
            form = L * W                                         # soffit; edges measured separately (RM)
        elif e.type in ("footing", "pcc_bed"):
            form = 2 * (L + W) * D                               # sides
        elif e.type == "wall":
            form = 2 * L * D                                     # both faces (depth_m = height)
        elif e.type == "staircase_waist":
            form = L * W
        for o in e.openings:
            if o.width_m * o.height_m > 0.4:
                form -= o.width_m * o.height_m * o.count
    return vol, form, notes


def takeoff(elements: list[Element]) -> dict:
    rows = []
    for e in elements:
        vol, form, notes = _vol_and_form(e)
        v = rnd(vol * e.count, "0.01")
        f = rnd(form * e.count, "0.01")
        steel = round(v * e.steel_kg_per_m3, 1) if e.steel_kg_per_m3 else None
        rows.append({"id": e.id, "type": e.type, "grade": e.grade, "level": e.level, "floor": e.floor,
                     "count": e.count, "concrete_m3": v, "formwork_m2": f, "steel_kg": steel, "notes": notes})
    refs = [R_P2("cl. 3.5.1 (dimensions to 0.01 m; slab thickness 0.005 m)"), R_P2("cl. 5.1.8 (no deductions)"),
            R_P2("cl. 5.2.2.1 / 5.2.2.3 (columns / beams)"), R_P5("cl. 6.1 (contact area)"),
            R_P5("cl. 6.5 (openings up to 0.4 m2 not deducted)")]
    return {"rows": rows,
            "totals": {"concrete_m3": rnd(sum(r["concrete_m3"] for r in rows), "0.01"),
                       "formwork_m2": rnd(sum(r["formwork_m2"] for r in rows), "0.01"),
                       "steel_kg": round(sum(r["steel_kg"] or 0 for r in rows), 1)},
            "refs": [r.model_dump() for r in refs],
            "assumptions": ["Beam depth measured below slab soffit; slab measured over beams.",
                            "Formwork: beams = soffit + 2 sides below slab; slabs = soffit; columns = perimeter x height; "
                            "footings = vertical sides. Edges of slabs < 200 mm are measured in running metres separately (Part 5 cl. 5 n)."]}
