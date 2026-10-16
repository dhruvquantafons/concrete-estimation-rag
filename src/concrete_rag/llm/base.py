"""Provider-agnostic LLM interface. The LLM is undecided (local or API), so everything above this
layer talks to `LLMProvider`; adapters live in separate modules and are chosen with CRAG_LLM_PROVIDER."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


@dataclass
class ToolCallRecord:
    name: str
    input: dict[str, Any]
    output: Any
    error: str | None = None


@dataclass
class AgentResult:
    text: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    stop_reason: str | None = None
    model: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    name: str
    model: str

    def generate(self, system: str, user: str, max_tokens: int | None = None) -> AgentResult: ...

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], max_steps: int = 12) -> AgentResult: ...


def execute_tool(tools: dict[str, ToolSpec], name: str, args: dict[str, Any]) -> ToolCallRecord:
    import json
    spec = tools.get(name)
    if spec is None:
        return ToolCallRecord(name, args, None, f"unknown tool '{name}'")
    try:
        out = spec.handler(args)
        json.dumps(out, default=str)
        return ToolCallRecord(name, args, out)
    except Exception as e:  # noqa: BLE001 - tool errors are returned to the model, not raised
        return ToolCallRecord(name, args, None, f"{type(e).__name__}: {e}")


def get_provider() -> LLMProvider:
    from concrete_rag.config import get_settings
    s = get_settings()
    p = s.llm_provider.lower()
    if p == "anthropic":
        from concrete_rag.llm.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if p in ("openai_compat", "ollama", "vllm", "local"):
        from concrete_rag.llm.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider()
    from concrete_rag.llm.fake import FakeProvider
    return FakeProvider()
