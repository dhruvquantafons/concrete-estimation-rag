"""Provider-agnostic LLM interface. Everything above this layer talks to `LLMProvider`; adapters live in
separate modules and are chosen with CRAG_LLM_PROVIDER."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class LLMResult:
    text: str
    stop_reason: str | None = None
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, system: str, user: str, max_tokens: int | None = None) -> LLMResult: ...


def get_provider() -> LLMProvider:
    from concrete_rag.config import get_settings
    s = get_settings()
    p = s.llm_provider.lower()
    if p in ("openai_compat", "ollama", "vllm", "local"):
        from concrete_rag.llm.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider()
    from concrete_rag.llm.fake import FakeProvider
    return FakeProvider()
