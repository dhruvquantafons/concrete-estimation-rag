"""Claude adapter (official `anthropic` SDK; `pip install -e .[anthropic]`).

Credentials resolve from the environment (ANTHROPIC_API_KEY / `ant auth login` profile) or
CRAG_LLM_API_KEY. Default model claude-opus-5-5 (override with CRAG_LLM_MODEL). Thinking is always
on for this model; depth is set with output_config.effort. Server-side refusal fallback is enabled
(`fallbacks: "default"`), so a safety-classifier decline is retried on a fallback model in the same call.
"""
from __future__ import annotations

import json

from concrete_rag.config import get_settings
from concrete_rag.llm.base import AgentResult, ToolSpec, execute_tool

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, effort: str = "high"):
        import anthropic

        s = get_settings()
        self.client = anthropic.Anthropic(api_key=s.llm_api_key) if s.llm_api_key else anthropic.Anthropic()
        self.model = s.llm_model or DEFAULT_MODEL
        self.max_tokens = s.llm_max_tokens or 16000
        self.effort = effort

    def _create(self, system: str, messages: list, tools: list[dict] | None = None, max_tokens: int | None = None):
        kwargs = dict(model=self.model, max_tokens=max_tokens or self.max_tokens, system=system, messages=messages,
                      output_config={"effort": self.effort}, betas=[FALLBACK_BETA], fallbacks="default")
        if tools:
            kwargs["tools"] = tools
        return self.client.beta.messages.create(**kwargs)

    @staticmethod
    def _text(resp) -> str:
        return "".join(b.text for b in resp.content if b.type == "text")

    @staticmethod
    def _usage(resp) -> dict:
        u = resp.usage
        return {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}

    def generate(self, system: str, user: str, max_tokens: int | None = None) -> AgentResult:
        resp = self._create(system, [{"role": "user", "content": user}], max_tokens=max_tokens)
        if resp.stop_reason == "refusal":
            cat = resp.stop_details.category if resp.stop_details else None
            return AgentResult(text="", stop_reason=f"refusal:{cat}", model=resp.model, usage=self._usage(resp))
        return AgentResult(text=self._text(resp), stop_reason=resp.stop_reason, model=resp.model,
                           usage=self._usage(resp))

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], max_steps: int = 12) -> AgentResult:
        by_name = {t.name: t for t in tools}
        tool_defs = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in tools]
        messages: list = [{"role": "user", "content": user}]
        records = []
        usage = {"input_tokens": 0, "output_tokens": 0}
        resp = None
        for _ in range(max_steps):
            resp = self._create(system, messages, tool_defs)
            for k, v in self._usage(resp).items():
                usage[k] += v
            if resp.stop_reason == "refusal":
                cat = resp.stop_details.category if resp.stop_details else None
                return AgentResult(text="", tool_calls=records, stop_reason=f"refusal:{cat}", model=resp.model,
                                   usage=usage)
            if resp.stop_reason == "max_tokens":
                return AgentResult(text=self._text(resp), tool_calls=records, stop_reason="max_tokens",
                                   model=resp.model, usage=usage)
            if resp.stop_reason != "tool_use":
                return AgentResult(text=self._text(resp), tool_calls=records, stop_reason=resp.stop_reason,
                                   model=resp.model, usage=usage)
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                rec = execute_tool(by_name, block.name, dict(block.input))
                records.append(rec)
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": rec.error or json.dumps(rec.output, default=str)[:60000],
                                "is_error": rec.error is not None})
            messages.append({"role": "user", "content": results})   # all results in one message
        return AgentResult(text=self._text(resp) if resp else "", tool_calls=records, stop_reason="max_steps",
                           model=self.model, usage=usage)
