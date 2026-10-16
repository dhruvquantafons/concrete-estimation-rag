"""Cross-encoder reranking (bge-reranker-v2-m3 by default, local)."""
from __future__ import annotations

from functools import lru_cache

from concrete_rag.config import get_settings
from concrete_rag.embed.embedder import _device


@lru_cache
def reranker():
    from sentence_transformers import CrossEncoder
    s = get_settings()
    return CrossEncoder(s.rerank_model, device=_device(), max_length=512)


def rerank(query: str, docs: list[str], batch_size: int = 16) -> list[float]:
    if not docs:
        return []
    scores = reranker().predict([(query, d) for d in docs], batch_size=batch_size, show_progress_bar=False)
    return [float(s) for s in scores]
