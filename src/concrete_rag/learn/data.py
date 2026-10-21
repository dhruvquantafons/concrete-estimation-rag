"""Feedback -> learning examples, and the evaluation used to gate every promotion.

Retrieval example (one per logged query with at least one 👍 source):
    positives = sources rated 👍
    negatives = sources rated 👎  +  unrated sources shown ABOVE a 👍 one ("skip-above": the user read past
                them to reach the useful one - the standard click-model assumption)
Discarded feedback (admin, Learning page) is ignored everywhere.
"""
from __future__ import annotations

import hashlib
import time
from collections import defaultdict

from concrete_rag.eval.retrieval_metrics import aggregate, score_query
from concrete_rag.store import docstore


def retrieval_examples() -> list[dict]:
    by_q: dict[str, dict] = {}
    for f in docstore.list_feedback(kind="chunk", limit=100_000):
        if f["status"] == "discarded" or not f["chunk_id"]:
            continue
        ex = by_q.setdefault(f["query_id"], {"query_id": f["query_id"], "query": f["query"],
                                              "filters": f["filters"], "shown": f["results"] or [],
                                              "pos": set(), "neg": set()})
        (ex["pos"] if f["rating"] > 0 else ex["neg"]).add(f["chunk_id"])
    out = []
    for ex in by_q.values():
        ex["neg"] -= ex["pos"]
        if not ex["pos"]:
            continue
        last_pos = max((i for i, c in enumerate(ex["shown"]) if c in ex["pos"]), default=-1)
        ex["neg"] |= {c for c in ex["shown"][:last_pos] if c not in ex["pos"]}
        out.append(ex)
    return out


def split(examples: list[dict], holdout_share: float = 0.2) -> tuple[list[dict], list[dict]]:
    """Deterministic split by query text, so the same question never lands on both sides."""
    train, hold = [], []
    for ex in examples:
        h = int(hashlib.md5(ex["query"].strip().lower().encode()).hexdigest(), 16) % 100
        (hold if h < holdout_share * 100 else train).append(ex)
    return train, hold


def _load_golden() -> list[dict]:
    from concrete_rag.eval.run_eval import _load
    return _load("retrieval_golden.jsonl")


def golden_scores(k: int = 8, use_rerank: bool = True) -> dict:
    """Retrieval metrics on eval/datasets/retrieval_golden.jsonl with whatever components are live
    (or overridden via learn.state.override)."""
    from concrete_rag.retrieval.hybrid import retrieve
    rows, t0 = [], time.time()
    gold = _load_golden()
    for q in gold:
        pack = retrieve(q["query"], top_k=k, use_expand=False, use_exact=False, use_rerank=use_rerank)
        rows.append(score_query([(c.metadata, c.chunk_type) for c in pack.chunks], q["targets"], k))
    agg = aggregate(rows)
    agg["latency_s_per_query"] = round((time.time() - t0) / max(len(gold), 1), 3)
    return agg


def feedback_mrr(examples: list[dict], k: int = 8, use_rerank: bool = True) -> float | None:
    """Mean reciprocal rank of the first 👍 source when the logged queries are re-run."""
    from concrete_rag.retrieval.hybrid import retrieve
    if not examples:
        return None
    rr = []
    for ex in examples:
        pack = retrieve(ex["query"], top_k=k, filters=ex["filters"], use_expand=False, use_exact=False,
                        use_rerank=use_rerank)
        ids = [c.chunk_id for c in pack.chunks]
        rank = next((i for i, c in enumerate(ids) if c in ex["pos"]), None)
        rr.append(0.0 if rank is None else 1 / (rank + 1))
    return round(sum(rr) / len(rr), 4)


def summary() -> dict:
    ex = retrieval_examples()
    per_status: dict = defaultdict(int)
    for f in docstore.list_feedback(limit=100_000):
        per_status[f"{f['kind']}:{f['status']}"] += 1
    return {"retrieval_examples": len(ex), "positives": sum(len(e["pos"]) for e in ex),
            "negatives": sum(len(e["neg"]) for e in ex), "feedback": dict(per_status)}
