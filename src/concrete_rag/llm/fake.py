"""Deterministic offline provider (no LLM configured, and for tests).

generate(): returns an extractive answer built from the numbered sources in the prompt.
"""
from __future__ import annotations

import re

from concrete_rag.llm.base import LLMResult


class FakeProvider:
    name = "fake"
    model = "extractive-fake"

    def generate(self, system: str, user: str, max_tokens: int | None = None) -> LLMResult:
        sources = re.findall(r"\[(S\d+)\] (.+?)\n(.*?)(?=\n\[S\d+\] |\n### QUESTION|\Z)", user, re.S)
        lines = ["(offline extractive answer - configure CRAG_LLM_PROVIDER for generated answers)"]
        for sid, ref, body in sources[:3]:
            snippet = " ".join(body.split())[:350]
            lines.append(f"- {snippet} [{sid}]")
        return LLMResult(text="\n".join(lines), stop_reason="end_turn", model=self.model)
