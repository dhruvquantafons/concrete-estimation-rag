"""Cross-encoder reranking (bge-reranker-v2-m3 by default, local). If the learning loop has promoted a
fine-tuned version (storage/learned/active.json), that one is used instead."""
from __future__ import annotations

from functools import lru_cache

from concrete_rag.config import get_settings
from concrete_rag.embed.embedder import _device


@lru_cache(maxsize=2)
def _load(name_or_path: str):
    from sentence_transformers import CrossEncoder
    return CrossEncoder(name_or_path, device=_device(), max_length=512)


def reranker():
    from concrete_rag.learn.state import reranker_path
    return _load(reranker_path() or get_settings().rerank_model)


def rerank(query: str, docs: list[str], batch_size: int = 16) -> list[float]:
    if not docs:
        return []
    scores = reranker().predict([(query, d) for d in docs], batch_size=batch_size, show_progress_bar=False)
    return [float(s) for s in scores]
