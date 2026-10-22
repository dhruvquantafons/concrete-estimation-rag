"""Central settings. Every model / store / provider choice is an env var (prefix CRAG_)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CRAG_", env_file=ROOT / ".env", extra="ignore")

    # paths
    root: Path = ROOT
    sources_file: Path = ROOT / "config" / "sources.yaml"
    interim_dir: Path = ROOT / "data" / "interim"        # OCR cache, page text
    processed_dir: Path = ROOT / "data" / "processed"    # chunks.jsonl, sor_items.jsonl, reports
    storage_dir: Path = ROOT / "storage"                 # sqlite docstore, embedded qdrant
    output_dir: Path = ROOT / "outputs"                  # BOQ exports

    # OCR
    ocr_dpi: int = 300
    ocr_workers: int = 8
    tesseract_lang: str = "eng"

    # stores
    docstore_url: str = ""                 # default sqlite:///storage/docstore.db
    qdrant_url: str = ""                   # e.g. http://localhost:6333 ; empty => embedded local mode
    qdrant_api_key: str = ""
    qdrant_collection: str = "concrete_rag"

    # models
    embed_model: str = "BAAI/bge-m3"
    embed_max_seq_len: int = 512
    embed_batch_size: int = 16
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_enabled: bool = True
    rerank_pool: int = 24                  # candidates passed to the cross-encoder (latency ~ linear)
    device: str = ""                       # "" => auto (mps / cuda / cpu)

    # retrieval
    prefetch_k: int = 50
    top_k: int = 8

    # LLM: fake (offline) | openai_compat (Ollama, vLLM)
    llm_provider: str = "fake"
    llm_model: str = ""
    llm_base_url: str = ""                 # openai_compat: e.g. http://localhost:11434/v1 (Ollama)
    llm_api_key: str = ""
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.0
    llm_reasoning_effort: str = ""         # openai_compat: "none" disables thinking (e.g. Qwen3 on Ollama)

    # API
    api_rate_limit: str = "60/minute"
    api_bootstrap_admin_key: str = ""      # if set, accepted as an admin key (dev only)
    api_cors_origins: str = ""             # comma separated

    @property
    def docstore_uri(self) -> str:
        return self.docstore_url or f"sqlite:///{self.storage_dir / 'docstore.db'}"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    for d in (s.interim_dir, s.processed_dir, s.storage_dir, s.output_dir):
        d.mkdir(parents=True, exist_ok=True)
    return s
