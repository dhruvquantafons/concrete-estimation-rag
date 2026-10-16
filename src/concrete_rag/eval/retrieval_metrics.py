"""Retrieval metrics against a golden set whose targets are structural references (code, clause,
table, SOR item) rather than chunk ids - so the set survives re-chunking and re-ingestion."""
from __future__ import annotations

import math


def matches(meta: dict, chunk_type: str, target: dict) -> bool:
    for k, v in target.items():
        if k == "chunk_type":
            if chunk_type != v:
                return False
            continue
        mv = meta.get(k)
        if k == "clause_no":
            if not mv or not (mv == v or mv.startswith(v + ".")):
                return False
        elif str(mv) != str(v):
            return False
    return True


def score_query(results: list[tuple[dict, str]], targets: list[dict], k: int) -> dict:
    """results: ranked [(metadata, chunk_type)]."""
    top = results[:k]
    rel = [any(matches(m, ct, t) for t in targets) for m, ct in top]
    found = [any(matches(m, ct, t) for m, ct in top) for t in targets]
    first = next((i for i, r in enumerate(rel) if r), None)
    dcg = sum(1 / math.log2(i + 2) for i, r in enumerate(rel) if r)
    ideal = sum(1 / math.log2(i + 2) for i in range(min(k, max(1, sum(rel)))))
    return {
        "precision": sum(rel) / k,
        "recall": sum(found) / len(targets),
        "hit": float(first is not None),
        "mrr": 1 / (first + 1) if first is not None else 0.0,
        "ndcg": dcg / ideal if ideal else 0.0,
        "first_rank": None if first is None else first + 1,
    }


def aggregate(rows: list[dict]) -> dict:
    keys = ("precision", "recall", "hit", "mrr", "ndcg")
    n = max(len(rows), 1)
    return {k: round(sum(r[k] for r in rows) / n, 4) for k in keys}
