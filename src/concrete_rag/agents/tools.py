"""Tools exposed to any LLM: retrieval + deterministic calculators. The LLM orchestrates; every number
it reports must come from one of these tool outputs (checked afterwards by agents.generate.verify_numbers)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from concrete_rag.calc import rates, rcc
from concrete_rag.calc.mix_design import MixDesignInput, design_mix
from concrete_rag.calc.quantities import Element, takeoff
from concrete_rag.calc.steel import development_length, lap_length, unit_weight_kg_m
from concrete_rag.estimate.boq import EstimateRequest, build_estimate
from concrete_rag.llm.base import ToolSpec
from concrete_rag.retrieval.hybrid import retrieve
from concrete_rag.store import docstore


class RetrieveArgs(BaseModel):
    query: str
    top_k: int = 6
    code_no: str | None = Field(None, description="restrict to a standard, e.g. 'IS 456', 'IS 10262', 'IS 1200', 'CPWD DSR'")


class ClauseArgs(BaseModel):
    code_no: str = Field(..., description="e.g. 'IS 456'")
    clause: str = Field(..., description="e.g. '26.4.2' or 'B-2.1'")
    part: str | None = None


class TableArgs(BaseModel):
    code_no: str
    table: str
    part: str | None = None


class SorArgs(BaseModel):
    item_code: str = Field(..., description="DSR item code, e.g. '5.33.1.1'")
    location: str = "delhi"


class SorSearchArgs(BaseModel):
    text: str = Field(..., description="keywords, e.g. 'centering shuttering columns'")
    unit: str | None = None


class DevLenArgs(BaseModel):
    dia_mm: float
    grade: str = "M25"
    fy: float = 500
    case: str = Field("flexural_tension", description="flexural_tension | direct_tension | compression")


class TakeoffArgs(BaseModel):
    elements: list[Element]


def _pack(args: dict) -> dict:
    a = RetrieveArgs(**args)
    p = retrieve(a.query, top_k=a.top_k, filters={"code_no": a.code_no} if a.code_no else None)
    return {"chunks": [{"citation": c.citation.ref, "verified": c.citation.verified, "text": c.text[:1500],
                        "table": c.table, "via": c.retrieval} for c in p.chunks],
            "rate_items": [{k: it[k] for k in ("code", "unit", "rate", "full_description", "needs_review")}
                           for it in p.rate_items], "warnings": p.warnings}


def _schema(model: type[BaseModel]) -> dict:
    s = model.model_json_schema()
    s.pop("title", None)
    return s


def all_tools() -> list[ToolSpec]:
    return [
        ToolSpec("retrieve", "Hybrid search (BM25 + dense + rerank) over IS codes and CPWD DSR. Returns cited chunks.",
                 _schema(RetrieveArgs), _pack),
        ToolSpec("get_clause", "Exact text of a clause (and its sub-clauses) of an Indian Standard.",
                 _schema(ClauseArgs), lambda a: docstore.find_clause(**ClauseArgs(**a).model_dump())),
        ToolSpec("get_table", "A table of an Indian Standard with its rows as JSON.",
                 _schema(TableArgs), lambda a: docstore.find_table(a["code_no"], a["table"], a.get("part"))),
        ToolSpec("get_sor_rate", "Rate of a CPWD DSR item, adjusted by location cost index, with citation and OCR check status.",
                 _schema(SorArgs), lambda a: rates.sor_rate(a["item_code"], location=a.get("location"))),
        ToolSpec("search_sor_items", "Find CPWD DSR items by keywords (returns code, unit, rate, description).",
                 _schema(SorSearchArgs), lambda a: [{k: it[k] for k in ("code", "unit", "rate", "full_description",
                                                                        "needs_review")}
                                                    for it in docstore.search_sor_items(a["text"], unit=a.get("unit"),
                                                                                        limit=15)]),
        ToolSpec("mix_design", "IS 10262:2019 concrete mix proportioning (deterministic). Requires w_c_ratio unless "
                 "Fig. 1 curves are configured.", _schema(MixDesignInput),
                 lambda a: design_mix(MixDesignInput(**a)).model_dump()),
        ToolSpec("design_beam", "IS 456 limit-state design of a rectangular RCC beam (flexure + shear).",
                 _schema(rcc.BeamInput), lambda a: rcc.design_beam(rcc.BeamInput(**a)).model_dump()),
        ToolSpec("design_slab", "IS 456 one-way slab design.", _schema(rcc.SlabInput),
                 lambda a: rcc.design_slab_one_way(rcc.SlabInput(**a)).model_dump()),
        ToolSpec("design_column", "IS 456 short axially loaded column (cl. 39.3).", _schema(rcc.ColumnInput),
                 lambda a: rcc.design_column_axial(rcc.ColumnInput(**a)).model_dump()),
        ToolSpec("design_footing", "IS 456 isolated square footing sizing and reinforcement.", _schema(rcc.FootingInput),
                 lambda a: rcc.design_footing_square(rcc.FootingInput(**a)).model_dump()),
        ToolSpec("development_length", "IS 456 development and lap length of a bar.", _schema(DevLenArgs),
                 lambda a: _devlen(DevLenArgs(**a))),
        ToolSpec("quantity_takeoff", "IS 1200 quantities (concrete m3, formwork m2, steel kg) for RCC elements.",
                 _schema(TakeoffArgs), lambda a: takeoff(TakeoffArgs(**a).elements)),
        ToolSpec("build_estimate", "Full BOQ + cost estimate from elements using CPWD DSR rates.",
                 _schema(EstimateRequest), lambda a: _est(EstimateRequest(**a))),
    ]


def _devlen(a: DevLenArgs) -> dict:
    ld, refs = development_length(a.dia_mm, a.grade, a.fy)
    lap, lrefs = lap_length(a.dia_mm, a.grade, a.fy, a.case)  # type: ignore[arg-type]
    return {"Ld_mm": round(ld), "lap_mm": round(lap), "unit_weight_kg_m": round(unit_weight_kg_m(a.dia_mm), 3),
            "refs": [r.model_dump() for r in refs + lrefs]}


def _est(req: EstimateRequest) -> dict[str, Any]:
    e = build_estimate(req)
    return {k: e[k] for k in ("id", "boq", "summary", "cement_statement", "cost_heads", "warnings", "assumptions")}
