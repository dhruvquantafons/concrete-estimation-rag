"""Citation-backed generation + post-hoc verification.

answer():  retrieve -> numbered sources -> LLM answer that must cite [S#] -> verification

verify_numbers(): every number in the answer must appear in the supplied context;
anything else is reported as `ungrounded_numbers` (flagged, never silently corrected).
"""
from __future__ import annotations

import re

from concrete_rag.calc.confidence import answer_confidence
from concrete_rag.llm.base import get_provider
from concrete_rag.retrieval.hybrid import retrieve

SYSTEM_RAG = """You are a civil-engineering assistant for concrete works in India (IS 456, IS 10262, IS 1200,
IS 2386, CPWD DSR). Answer ONLY from the numbered sources provided. Cite every statement with its source id,
e.g. [S2]. Quote numerical limits exactly as written in the source. If the sources do not contain the answer,
say so - do not use outside knowledge. Sources marked UNVERIFIED come from OCR that was not cross-checked;
mention that when you rely on them."""

NUM_RE = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    out = set()
    for m in NUM_RE.findall(text):
        v = m.replace(",", "")
        try:
            f = float(v)
        except ValueError:
            continue
        out.add(f"{f:g}")
    return out


def verify_numbers(answer: str, evidence: str) -> dict:
    ignore = {str(i) for i in range(0, 11)}           # list numbering / small counts
    a = {n for n in _numbers(re.sub(r"\[S\d+\]", "", answer)) if n not in ignore}
    ev = _numbers(evidence)
    ungrounded = sorted(a - ev, key=lambda x: float(x))
    return {"numbers_in_answer": len(a), "grounded": len(a) - len(ungrounded), "ungrounded_numbers": ungrounded,
            "faithful_numbers": not ungrounded}


def answer(query: str, top_k: int = 6, filters: dict | None = None) -> dict:
    pack = retrieve(query, top_k=top_k, filters=filters)
    blocks = []
    for i, c in enumerate(pack.chunks, 1):
        flag = "" if c.citation.verified else " (UNVERIFIED OCR)"
        body = c.text if not c.table else c.text
        blocks.append(f"[S{i}] {c.citation.ref}{flag}\n{body[:2500]}")
    from concrete_rag.learn.exemplars import prompt_block, similar
    try:
        examples = similar(query)
    except Exception:  # noqa: BLE001 - exemplars are an optional aid, never a reason to fail
        examples = []
    user = prompt_block(examples) + "### SOURCES\n" + "\n\n".join(blocks) + f"\n\n### QUESTION\n{query}"
    llm = get_provider()
    res = llm.generate(SYSTEM_RAG, user)
    cited = sorted({int(x) for x in re.findall(r"\[S(\d+)\]", res.text)})
    evidence = "\n".join(c.text for c in pack.chunks) + "\n" + "\n".join(c.citation.ref for c in pack.chunks)
    verification = {**verify_numbers(res.text, evidence), "cites_sources": bool(cited) or res.stop_reason != "end_turn"}
    citations = [pack.chunks[i - 1].citation.model_dump() for i in cited if 0 < i <= len(pack.chunks)]
    return {
        "query": query, "answer": res.text, "provider": llm.name, "model": res.model, "stop_reason": res.stop_reason,
        "citations": citations,
        "sources": [{"id": f"S{i}", **c.citation.model_dump()} for i, c in enumerate(pack.chunks, 1)],
        "verification": verification,
        "confidence": answer_confidence(verification, citations),
        "warnings": pack.warnings, "usage": res.usage,
        "exemplars_used": [{"feedback_id": e["id"], "similarity": e["similarity"]} for e in examples],
    }

