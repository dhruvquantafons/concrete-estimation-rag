"""Qdrant index: one collection, named vectors {dense, bm25}. Embedded local mode by default
(storage/qdrant), or a Qdrant server via CRAG_QDRANT_URL."""
from __future__ import annotations

import atexit
import uuid
from functools import lru_cache
from typing import Any

from qdrant_client import QdrantClient, models

from concrete_rag.config import get_settings
from concrete_rag.embed.embedder import BM25Encoder, dense_dim, embed_query, embed_texts

PAYLOAD_INDEXES = {
    "code_no": models.PayloadSchemaType.KEYWORD, "doc_id": models.PayloadSchemaType.KEYWORD,
    "chunk_type": models.PayloadSchemaType.KEYWORD, "is_current": models.PayloadSchemaType.BOOL,
    "revision_year": models.PayloadSchemaType.INTEGER, "work_item": models.PayloadSchemaType.KEYWORD,
    "material_type": models.PayloadSchemaType.KEYWORD, "element_type": models.PayloadSchemaType.KEYWORD,
    "region": models.PayloadSchemaType.KEYWORD, "sor_year": models.PayloadSchemaType.INTEGER,
    "item_code": models.PayloadSchemaType.KEYWORD, "clause_no": models.PayloadSchemaType.KEYWORD,
    "table_no": models.PayloadSchemaType.KEYWORD, "doc_type": models.PayloadSchemaType.KEYWORD,
}


@lru_cache
def client() -> QdrantClient:
    s = get_settings()
    if s.qdrant_url:
        c = QdrantClient(url=s.qdrant_url, api_key=s.qdrant_api_key or None)
    else:
        c = QdrantClient(path=str(s.storage_dir / "qdrant"))
    atexit.register(c.close)          # close before interpreter teardown (avoids noisy __del__ errors)
    return c


def point_id(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def bm25_stats_path():
    return get_settings().storage_dir / "bm25_stats.json"


def recreate_collection():
    s = get_settings()
    c = client()
    if c.collection_exists(s.qdrant_collection):
        c.delete_collection(s.qdrant_collection)
    c.create_collection(
        s.qdrant_collection,
        vectors_config={"dense": models.VectorParams(size=dense_dim(), distance=models.Distance.COSINE)},
        sparse_vectors_config={"bm25": models.SparseVectorParams(modifier=models.Modifier.IDF)},
    )
    if s.qdrant_url:   # payload indexes only matter (and are only supported) on a server
        for field, schema in PAYLOAD_INDEXES.items():
            c.create_payload_index(s.qdrant_collection, field, field_schema=schema)


def _payload(ch: dict[str, Any]) -> dict[str, Any]:
    m = ch["metadata"]
    return {**m, "chunk_id": ch["chunk_id"], "chunk_type": ch["chunk_type"], "text": ch["text"],
            "content_hash": ch.get("content_hash")}


def index_chunks(chunks: list[dict[str, Any]], batch: int = 64, progress=None) -> int:
    """Full (re)build: fits BM25 length stats on the corpus, embeds, upserts."""
    s = get_settings()
    recreate_collection()
    bm25 = BM25Encoder().fit([c["embed_text"] for c in chunks])
    bm25.save(bm25_stats_path())
    c = client()
    done = 0
    for i in range(0, len(chunks), batch):
        part = chunks[i:i + batch]
        dense = embed_texts([x["embed_text"] for x in part])
        points = []
        for ch, vec in zip(part, dense):
            idx, val = bm25.encode_doc(ch["embed_text"])
            points.append(models.PointStruct(
                id=point_id(ch["chunk_id"]),
                vector={"dense": vec, "bm25": models.SparseVector(indices=idx, values=val)},
                payload=_payload(ch)))
        c.upsert(s.qdrant_collection, points=points, wait=True)
        done += len(part)
        if progress:
            progress(done, len(chunks))
    return done


def build_filter(filters: dict[str, Any] | None) -> models.Filter | None:
    if not filters:
        return None
    must = []
    for k, v in filters.items():
        if v is None:
            continue
        if isinstance(v, list):
            must.append(models.FieldCondition(key=k, match=models.MatchAny(any=v)))
        else:
            must.append(models.FieldCondition(key=k, match=models.MatchValue(value=v)))
    return models.Filter(must=must) if must else None


def search_dense(query: str, limit: int, filters: dict | None = None, vector: list[float] | None = None):
    s = get_settings()
    res = client().query_points(s.qdrant_collection, query=vector or embed_query(query), using="dense",
                                limit=limit, query_filter=build_filter(filters), with_payload=True)
    return [(p.payload, p.score) for p in res.points]


def search_bm25(query: str, limit: int, filters: dict | None = None):
    s = get_settings()
    idx, val = BM25Encoder.encode_query(query)
    if not idx:
        return []
    res = client().query_points(s.qdrant_collection, query=models.SparseVector(indices=idx, values=val),
                                using="bm25", limit=limit, query_filter=build_filter(filters), with_payload=True)
    return [(p.payload, p.score) for p in res.points]


def collection_info() -> dict:
    s = get_settings()
    c = client()
    if not c.collection_exists(s.qdrant_collection):
        return {"exists": False}
    return {"exists": True, "points": c.count(s.qdrant_collection).count}
