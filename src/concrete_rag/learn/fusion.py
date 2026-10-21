"""Learn the dense-vs-BM25 weighting of the hybrid fusion from feedback (offline bandit).

Each candidate weighting is an "arm". Its reward is measured by replaying logged 👍 queries and the golden
set: did the useful chunk get into the reranker's pool (recall@pool), and how high did it rank (MRR@k)?
Search results are fetched once per query and re-fused per arm, so trying arms is cheap. The best arm is
promoted only if it beats the live weighting and does not lose golden-set recall.
"""
from __future__ import annotations

from concrete_rag.config import get_settings
from concrete_rag.eval.retrieval_metrics import matches
from concrete_rag.learn import data, state
from concrete_rag.retrieval.hybrid import candidate_lists, default_filters, fuse
from concrete_rag.retrieval.router import parse_query

ARMS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]        # dense weight; BM25 fixed at 1.0
MIN_GAIN = 0.005


def _queries(examples: list[dict]) -> list[dict]:
    """[{query, filters, is_rel(chunk) -> bool, source}] from the golden set + 👍 feedback."""
    out = []
    for q in data._load_golden():
        out.append({"query": q["query"], "filters": None, "source": "golden",
                    "is_rel": lambda ch, t=q["targets"]: any(matches(ch["metadata"], ch["chunk_type"], x) for x in t)})
    for ex in examples:
        out.append({"query": ex["query"], "filters": ex["filters"], "source": "feedback",
                    "is_rel": lambda ch, p=ex["pos"]: ch["chunk_id"] in p})
    return out


def _score(ranked: list, is_rel, k: int, pool: int) -> tuple[float, float]:
    rel = [is_rel(ch) for ch, _, _ in ranked[:pool]]
    first = next((i for i, r in enumerate(rel) if r), None)
    return float(any(rel)), (1 / (first + 1) if first is not None and first < k else 0.0)


def tune(k: int = 8, promote: bool = True, log=print) -> dict:
    s = get_settings()
    pool = max(s.rerank_pool, k)
    qs = _queries(data.retrieval_examples())
    log(f"[fusion] {len(qs)} queries ({sum(q['source'] == 'feedback' for q in qs)} from feedback)")
    fetched = []
    for q in qs:
        f = default_filters(parse_query(q["query"]), q["filters"])
        fetched.append((q, candidate_lists(q["query"], f, s.prefetch_k)))
    results = {}
    current = state.fusion_weights()
    arms = sorted(set(ARMS) | {current.get("dense", 1.0) / current.get("bm25", 1.0)})
    for w in arms:
        weights = {"dense": w, "bm25": 1.0}
        rec = {"golden": [], "feedback": []}
        mrr = {"golden": [], "feedback": []}
        for q, lists in fetched:
            r, m = _score(fuse(lists, weights), q["is_rel"], k, pool)
            rec[q["source"]].append(r)
            mrr[q["source"]].append(m)
        avg = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
        all_r, all_m = rec["golden"] + rec["feedback"], mrr["golden"] + mrr["feedback"]
        results[w] = {"weights": weights, "golden_recall_pool": avg(rec["golden"]), "golden_mrr": avg(mrr["golden"]),
                      "feedback_recall_pool": avg(rec["feedback"]), "feedback_mrr": avg(mrr["feedback"]),
                      "reward": round((avg(all_r) + avg(all_m)) / 2, 4)}
        log(f"  dense:bm25 = {w}:1  {results[w]}")
    cur_w = current.get("dense", 1.0) / current.get("bm25", 1.0)
    best_w = max(results, key=lambda w: (results[w]["reward"], -abs(w - 1.0)))
    best, cur = results[best_w], results[cur_w]
    ok = (best_w != cur_w and best["reward"] >= cur["reward"] + MIN_GAIN
          and best["golden_recall_pool"] >= cur["golden_recall_pool"])
    decision = {"current": cur, "best": best, "promoted": False,
                "reason": "no better weighting found" if not ok else "better reward, golden recall not lower"}
    if ok and promote:
        v = state.next_version("fusion")
        state.promote("fusion", {"version": v, "weights": best["weights"],
                                 "metrics": {"before": cur, "after": best}, "n_queries": len(qs)})
        decision["promoted"] = True
        decision["version"] = v
    log(f"[fusion] {decision['reason']}; promoted={decision['promoted']}")
    return decision
