"""Citation-backed generation + post-hoc verification.

answer():  retrieve -> numbered sources -> LLM answer that must cite [S#] -> verification
agent():   LLM with tools (retrieval + calculators) -> verification against tool outputs

verify_numbers(): every number in the answer must appear in the supplied context or a tool output;
anything else is reported as `ungrounded_numbers` (flagged, never silently corrected).
"""
from __future__ import annotations

import json
import re

from concrete_rag.agents.tools import all_tools
from concrete_rag.llm.base import get_provider
from concrete_rag.retrieval.hybrid import retrieve

SYSTEM_RAG = """You are a civil-engineering assistant for concrete works in India (IS 456, IS 10262, IS 1200,
IS 2386, CPWD DSR). Answer ONLY from the numbered sources provided. Cite every statement with its source id,
e.g. [S2]. Quote numerical limits exactly as written in the source. If the sources do not contain the answer,
say so - do not use outside knowledge. Sources marked UNVERIFIED come from OCR that was not cross-checked;
mention that when you rely on them."""

SYSTEM_AGENT = """You are an estimation engineer for concrete works in India. Use the tools: `retrieve` /
`get_clause` / `get_table` for code provisions, the calculators for every design or arithmetic result, and
`get_sor_rate` / `build_estimate` for costs. Never do arithmetic yourself and never invent code values or rates -
every number in your answer must come from a tool result. Cite clauses/tables/DSR items as the tools report them.
State assumptions and surface any tool warnings (unverified OCR values, failed checks).
Before answering, list (to yourself) every quantity the user asked for, and make sure each one has come from a
tool call - e.g. a rate needs `get_sor_rate` or `search_sor_items`, a design value needs a calculator. If something
is still missing, call the tool for it first. Only answer when every requested item is covered."""

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
    return {
        "query": query, "answer": res.text, "provider": llm.name, "model": res.model, "stop_reason": res.stop_reason,
        "citations": [pack.chunks[i - 1].citation.model_dump() for i in cited if 0 < i <= len(pack.chunks)],
        "sources": [{"id": f"S{i}", **c.citation.model_dump()} for i, c in enumerate(pack.chunks, 1)],
        "verification": {**verify_numbers(res.text, evidence),
                         "cites_sources": bool(cited) or res.stop_reason != "end_turn"},
        "warnings": pack.warnings, "usage": res.usage,
        "exemplars_used": [{"feedback_id": e["id"], "similarity": e["similarity"]} for e in examples],
    }


def agent(task: str, max_steps: int = 12) -> dict:
    llm = get_provider()
    res = llm.run_tools(SYSTEM_AGENT, task, all_tools(), max_steps=max_steps)
    evidence = "\n".join(json.dumps(t.output, default=str) for t in res.tool_calls if t.output is not None)
    warnings = []
    for t in res.tool_calls:
        if isinstance(t.output, dict):
            warnings += t.output.get("warnings", []) or []
    return {"task": task, "answer": res.text, "provider": llm.name, "model": res.model, "stop_reason": res.stop_reason,
            "tool_calls": [{"name": t.name, "input": t.input, "error": t.error} for t in res.tool_calls],
            "verification": verify_numbers(res.text, evidence + "\n" + task),
            "warnings": sorted(set(map(str, warnings))), "usage": res.usage}
