"""Adapter for local / self-hosted models behind an OpenAI-compatible /chat/completions endpoint
(Ollama, vLLM, LM Studio, llama.cpp server). Configure CRAG_LLM_BASE_URL (e.g. http://localhost:11434/v1)
and CRAG_LLM_MODEL (e.g. qwen2.5:14b-instruct)."""
from __future__ import annotations

import httpx

from concrete_rag.config import get_settings
from concrete_rag.llm.base import LLMResult


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
        self.reasoning_effort = s.llm_reasoning_effort
        self.headers = {"Authorization": f"Bearer {s.llm_api_key}"} if s.llm_api_key else {}
        self.http = httpx.Client(timeout=600)

    def _chat(self, messages: list) -> dict:
        body = {"model": self.model, "messages": messages, "max_tokens": self.max_tokens,
                "temperature": self.temperature}
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        r = self.http.post(f"{self.base}/chat/completions", json=body, headers=self.headers)
        r.raise_for_status()
        return r.json()

    def generate(self, system: str, user: str, max_tokens: int | None = None) -> LLMResult:
        d = self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}])
        ch = d["choices"][0]
        return LLMResult(text=_text(ch["message"]), stop_reason=ch.get("finish_reason"),
                        model=d.get("model", self.model), usage=d.get("usage") or {})


def _text(msg: dict) -> str:
    """Answer text. Some servers (Ollama with hybrid-thinking templates) put a non-thinking model's whole
    answer in `reasoning` and leave `content` empty - fall back to it rather than returning nothing."""
    return (msg.get("content") or "").strip() or (msg.get("reasoning") or "").strip()
