"""Confidence score for calculator, estimate and answer outputs.

The score (0-1) is an *evidence* score, not a statistical probability: a weighted mean of named factors
(how well the code values were checked, how the inputs were obtained, whether rates were confirmed by
the OCR cross-check, ...). Every factor is returned with its detail so a user can see what lowers it.
"""
from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from concrete_rag.calc.code_tables import Ref

# code-table / clause status (see calc.code_tables) -> score
STATUS_SCORE = {"verified": 1.0, "ai_checked": 0.9, "ocr_confirmed": 0.8, "partially_confirmed": 0.5,
                "unconfirmed": 0.3}
HIGH, MEDIUM = 0.85, 0.65
BASIS = "evidence score 0-1 (weighted mean of the factors below), not a statistical probability"


class Factor(BaseModel):
    name: str
    score: float
    weight: float = 1.0
    detail: str = ""
    cap: float | None = None       # upper bound on the overall score while this factor stands (e.g. a failed check)


def level(score: float) -> str:
    return "high" if score >= HIGH else "medium" if score >= MEDIUM else "low"


def combine(factors: Iterable[Factor]) -> dict:
    fs = [f for f in factors if f.weight > 0]
    total = sum(f.weight for f in fs)
    score = sum(f.score * f.weight for f in fs) / total if total else 0.0
    caps = [f for f in fs if f.cap is not None and f.cap < score]
    if caps:
        score = min(f.cap for f in caps)
    score = round(score, 2)
    out = {"score": score, "level": level(score), "basis": BASIS,
           "factors": [{**f.model_dump(exclude={"cap"}), "score": round(f.score, 2)} for f in fs]}
    if caps:
        out["capped_by"] = [f.name for f in caps]
    return out


def refs_factor(refs: Iterable[Ref], name: str = "code values", weight: float = 3.0) -> Factor:
    """Mean verification status of the code values / clauses a result relies on."""
    uniq = {(r.code, r.item): r.status for r in refs}
    if not uniq:
        return Factor(name=name, score=0.5, weight=weight, detail="no code references recorded")
    score = sum(STATUS_SCORE.get(s, 0.3) for s in uniq.values()) / len(uniq)
    counts: dict[str, int] = {}
    for s in uniq.values():
        counts[s] = counts.get(s, 0) + 1
    return Factor(name=name, score=score, weight=weight,
                  detail=", ".join(f"{n} {s.replace('_', ' ')}" for s, n in sorted(counts.items())))


def checks_factor(checks: Iterable, weight: float = 2.0) -> Factor:
    cs = list(checks)
    failed = [c.name for c in cs if not c.passed]
    if not cs:
        return Factor(name="code compliance checks", score=1.0, weight=0)
    return Factor(name="code compliance checks", score=0.3 if failed else 1.0, weight=weight,
                  detail=f"failed: {', '.join(failed)}" if failed else f"all {len(cs)} passed",
                  cap=0.6 if failed else None)


def rate_score(item: dict) -> float:
    """One SOR / basic rate: equal to its (digital) CPWD DAR analysis -> 1.0; DAR disagrees -> 0.4; otherwise by
    OCR readings: 3 agreeing -> 1.0, 2 -> 0.9, 1 -> 0.6; flagged for review -> 0.4."""
    if item.get("dar_match") is True:
        return 1.0
    if item.get("dar_match") is False or item.get("needs_review") or not item.get("rate_confirmed", True):
        return 0.4
    votes = item.get("rate_votes") or 0
    return 1.0 if votes >= 3 else 0.9 if votes == 2 else 0.6


def answer_confidence(verification: dict, citations: list[dict]) -> dict:
    """Generated answer: are its numbers in the sources, does it cite, are the cited sources verified."""
    n = verification.get("numbers_in_answer", 0)
    grounded = verification.get("grounded", 0)
    verified = [c for c in citations if c.get("verified")]
    return combine([
        Factor(name="numbers found in sources", score=grounded / n if n else 1.0, weight=3,
               detail=f"{grounded}/{n}" + (f", not found: {', '.join(verification['ungrounded_numbers'])}"
                                           if verification.get("ungrounded_numbers") else "")),
        Factor(name="cites sources", score=1.0 if citations else 0.0, weight=2,
               detail=f"{len(citations)} cited" if citations else "answer cites no source"),
        Factor(name="cited sources verified", score=len(verified) / len(citations) if citations else 0.0, weight=2,
               detail=f"{len(verified)}/{len(citations)} not flagged for OCR review"),
    ])
