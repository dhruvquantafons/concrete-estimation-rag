"""Deterministic offline provider (default until an LLM is chosen, and for tests).

generate(): returns an extractive answer built from the numbered sources in the prompt.
run_tools(): calls the `retrieve` tool with the user text and returns the cited top results.
"""
from __future__ import annotations

import re

from concrete_rag.llm.base import AgentResult, ToolSpec, execute_tool


class FakeProvider:
    name = "fake"
    model = "extractive-fake"

    def generate(self, system: str, user: str, max_tokens: int | None = None) -> AgentResult:
        sources = re.findall(r"\[(S\d+)\] (.+?)\n(.*?)(?=\n\[S\d+\] |\n### QUESTION|\Z)", user, re.S)
        lines = ["(offline extractive answer - configure CRAG_LLM_PROVIDER for generated answers)"]
        for sid, ref, body in sources[:3]:
            snippet = " ".join(body.split())[:350]
            lines.append(f"- {snippet} [{sid}]")
        return AgentResult(text="\n".join(lines), stop_reason="end_turn", model=self.model)

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], max_steps: int = 12) -> AgentResult:
        by_name = {t.name: t for t in tools}
        rec = execute_tool(by_name, "retrieve", {"query": user, "top_k": 5})
        out = rec.output or {}
        refs = [c["citation"] for c in out.get("chunks", [])]
        text = "Retrieved (offline mode, no reasoning):\n" + "\n".join(f"- {r}" for r in refs)
        return AgentResult(text=text, tool_calls=[rec], stop_reason="end_turn", model=self.model)
