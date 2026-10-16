"""Adapter for local / self-hosted models behind an OpenAI-compatible /chat/completions endpoint
(Ollama, vLLM, LM Studio, llama.cpp server). Configure CRAG_LLM_BASE_URL (e.g. http://localhost:11434/v1)
and CRAG_LLM_MODEL (e.g. qwen2.5:14b-instruct). The model must support function calling for run_tools()."""
from __future__ import annotations

import json

import httpx

from concrete_rag.config import get_settings
from concrete_rag.llm.base import AgentResult, ToolSpec, execute_tool


class OpenAICompatProvider:
    name = "openai_compat"

    def __init__(self):
        s = get_settings()
        if not s.llm_base_url or not s.llm_model:
            raise ValueError("set CRAG_LLM_BASE_URL and CRAG_LLM_MODEL for the openai_compat provider")
        self.base = s.llm_base_url.rstrip("/")
        self.model = s.llm_model
        self.max_tokens = s.llm_max_tokens
        self.temperature = s.llm_temperature
        self.headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
        self.http = httpx.Client(timeout=600)

    def _chat(self, messages: list, tools: list | None = None) -> dict:
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens,
                "temperature": self.temperature}
        if tools:
            body["tools"] = tools
        r = self.http.post(f"{self.base}/chat/completions", json=body, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def generate(self, system: str, user: str, max_tokens: int | None = None) -> AgentResult:
        d = self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
        ch = d["choices"][0]
        return AgentResult(text=ch["message"].get("content") or "", stop_reason=ch.get("finish_reason"),
                           model=d.get("model", self.model), usage=d.get("usage") or {})

    def run_tools(self, system: str, user: str, tools: list[ToolSpec], max_steps: int = 12) -> AgentResult:
        by_name = {t.name: t for t in tools}
        defs = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                  "parameters": t.input_schema}} for t in tools]
        messages: list = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        records = []
        d = None
        for _ in range(max_steps):
            d = self._chat(messages, defs)
            msg = d["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            if not calls:
                return AgentResult(text=msg.get("content") or "", tool_calls=records,
                                   stop_reason=d["choices"][0].get("finish_reason"), model=d.get("model", self.model))
            messages.append(msg)
            for c in calls:
                fn = c["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError as e:
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": f"invalid JSON arguments: {e}"})
                    continue
                rec = execute_tool(by_name, fn["name"], args)
                records.append(rec)
                messages.append({"role": "tool", "tool_call_id": c["id"],
                                 "content": rec.error or json.dumps(rec.output, default=str)[:60000]})
        return AgentResult(text="", tool_calls=records, stop_reason="max_steps", model=self.model)
