"""Common result model for deterministic calculators: every number is a traceable step."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from concrete_rag.calc.code_tables import Ref


class Step(BaseModel):
    name: str
    formula: str | None = None
    value: Any = None
    unit: str | None = None
    refs: list[Ref] = Field(default_factory=list)


class Check(BaseModel):
    name: str
    passed: bool
    detail: str
    refs: list[Ref] = Field(default_factory=list)


class CalcResult(BaseModel):
    calculator: str
    inputs: dict[str, Any]
    outputs: dict[str, Any] = Field(default_factory=dict)
    steps: list[Step] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def step(self, name: str, value: Any, unit: str | None = None, formula: str | None = None,
             refs: list[Ref] | None = None) -> Any:
        self.steps.append(Step(name=name, formula=formula, value=value, unit=unit, refs=refs or []))
        return value

    def check(self, name: str, passed: bool, detail: str, refs: list[Ref] | None = None) -> bool:
        self.checks.append(Check(name=name, passed=bool(passed), detail=detail, refs=refs or []))
        return passed

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def finalize(self) -> "CalcResult":
        used = {(r.code, r.item): r.status for s in self.steps for r in s.refs}
        used.update({(r.code, r.item): r.status for c in self.checks for r in c.refs})
        unverified = sorted(f"{c}, {i} ({st})" for (c, i), st in used.items() if st not in ("verified", "ai_checked", "ocr_confirmed"))
        if unverified:
            self.warnings.append("code values not yet human-verified/OCR-confirmed: " + "; ".join(unverified))
        self.outputs["all_checks_passed"] = self.ok
        return self


def r2(x: float, nd: int = 2) -> float:
    return round(float(x) + 0.0, nd)
