import pytest

from concrete_rag.store import docstore


def corpus_ready() -> bool:
    try:
        return docstore.counts()["chunks"] > 0
    except Exception:
        return False


needs_corpus = pytest.mark.skipif(not corpus_ready(), reason="run `crag ingest` first")
