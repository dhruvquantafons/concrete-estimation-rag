"""Evaluation runner -> eval/reports/report_<timestamp>.{json,md}

1. Retrieval: P@k, R@k, Hit@k, MRR, nDCG@k on the golden set, ablated over bm25 / dense / hybrid /
   hybrid+rerank (+ exact-reference routing).
2. Generation (only when a real LLM provider is configured): number-grounding faithfulness, citation
   rate, and an LLM-judge context-relevancy / faithfulness score on eval/datasets/generation_golden.jsonl.
3. Calculators: IS 10262 Annex A worked example reproduced within tolerance.
4. Code-table verification status (curated values vs OCR text).
5. Estimation accuracy vs actual costs, if eval/datasets/actual_costs.csv exists.
"""
from __future__ import annotations

import datetime as dt
import json
import time

from concrete_rag.config import get_settings
from concrete_rag.eval.retrieval_metrics import aggregate, score_query

THRESHOLDS = {"hybrid+rerank": {"recall": 0.75, "mrr": 0.60}}


def _load(name: str) -> list[dict]:
    p = get_settings().root / "eval" / "datasets" / name
    return [json.loads(l) for l in open(p) if l.strip()] if p.exists() else []


def eval_retrieval(k: int, quick: bool, log=print) -> dict:
    from concrete_rag.retrieval.hybrid import retrieve
    gold = _load("retrieval_golden.jsonl")
    configs = {"bm25": dict(mode="bm25", use_rerank=False, use_exact=False),
               "dense": dict(mode="dense", use_rerank=False, use_exact=False),
               "hybrid": dict(mode="hybrid", use_rerank=False, use_exact=False),
               "hybrid+rerank": dict(mode="hybrid", use_rerank=True, use_exact=False),
               "hybrid+rerank+exact": dict(mode="hybrid", use_rerank=True, use_exact=True)}
    if quick:
        configs = {k2: v for k2, v in configs.items() if k2 in ("hybrid", "hybrid+rerank")}
    out = {}
    for name, cfg in configs.items():
        rows, t0 = [], time.time()
        for q in gold:
            pack = retrieve(q["query"], top_k=k, use_expand=False, **cfg)
            res = [(c.metadata, c.chunk_type) for c in pack.chunks]
            s = score_query(res, q["targets"], k)
            rows.append({"id": q["id"], **s})
        agg = aggregate(rows)
        agg["latency_s_per_query"] = round((time.time() - t0) / max(len(gold), 1), 3)
        out[name] = {"aggregate": agg, "per_query": rows}
        log(f"  {name:22s} {agg}")
    return out


def eval_generation(log=print) -> dict:
    from concrete_rag.agents.generate import answer
    from concrete_rag.llm.base import get_provider
    llm = get_provider()
    if llm.name == "fake":
        return {"skipped": "no LLM configured (CRAG_LLM_PROVIDER=fake)"}
    gold = _load("generation_golden.jsonl")
    rows = []
    for q in gold:
        r = answer(q["query"])
        judge = _judge(llm, q["query"], r)
        rows.append({"id": q["id"], "faithful_numbers": r["verification"]["faithful_numbers"],
                     "cites": bool(r["citations"]),
                     "contains_expected": all(e.lower() in r["answer"].lower() for e in q.get("expect", [])),
                     **judge})
    agg = {}
    for k in ("faithful_numbers", "cites", "contains_expected", "judge_faithfulness", "judge_context_relevance"):
        vals = [float(r[k]) for r in rows if r.get(k) is not None]      # a judge failure is not a score of 0
        agg[k] = round(sum(vals) / len(vals), 3) if vals else None
    agg["judge_errors"] = sum(1 for r in rows if "judge_error" in r)
    log(f"  generation {agg}")
    return {"aggregate": agg, "per_query": rows}


def _judge(llm, query: str, r: dict, max_chars: int = 1500) -> dict:
    """LLM-as-judge. The judge must see the source TEXT (not just the references), otherwise it can never
    confirm a claim and scores every answer 0."""
    from concrete_rag.store import docstore
    texts = docstore.get_chunks([s["chunk_id"] for s in r["sources"]])
    blocks = [f"[{s['id']}] {s['ref']}\n{(texts.get(s['chunk_id']) or {}).get('text', '')[:max_chars]}" for s in r["sources"]]
    prompt = (f"Question: {query}\n\nSources:\n" + "\n\n".join(blocks) +
              f"\n\nAnswer:\n{r['answer']}\n\nRate 0-1: (a) faithfulness - every claim in the answer is supported by "
              "the source text above; (b) context_relevance - the sources contain what is needed to answer the question. "
              'Reply ONLY JSON {"faithfulness": x, "context_relevance": y}.')
    try:
        txt = llm.generate("You are a strict evaluator.", prompt, max_tokens=2000).text
        d = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
        return {"judge_faithfulness": _score(d["faithfulness"]), "judge_context_relevance": _score(d["context_relevance"])}
    except Exception as e:  # noqa: BLE001
        return {"judge_error": str(e)[:200]}


def _score(v) -> float:
    """Small judges sometimes answer true/false (or "true") instead of 0-1."""
    if isinstance(v, str) and v.strip().lower() in ("true", "yes", "false", "no"):
        return 1.0 if v.strip().lower() in ("true", "yes") else 0.0
    return min(1.0, max(0.0, float(v)))


def eval_calculators() -> dict:
    from concrete_rag.calc.mix_design import Admixture, MixDesignInput, design_mix
    r = design_mix(MixDesignInput(grade="M40", exposure="severe", max_aggregate_size_mm=20, slump_mm=75,
                                  cement_type="PPC", sg_cement=2.88, sg_coarse=2.74, sg_fine=2.65,
                                  fine_aggregate_zone="II", w_c_ratio=0.36,
                                  admixture=Admixture(type="superplasticizer", dosage_percent=1.0,
                                                      water_reduction_percent=23, specific_gravity=1.145)))
    o = r.outputs["per_m3_ssd"]
    expected = {"cement_kg": 412, "water_kg": 148, "fine_aggregate_kg": 648, "coarse_aggregate_kg": 1234}
    cases = {k: {"expected": v, "got": o[k], "ok": abs(o[k] - v) <= max(1.5, 0.005 * v)} for k, v in expected.items()}
    return {"is10262_annex_a": {"passed": all(c["ok"] for c in cases.values()), "cases": cases}}


def eval_estimates() -> dict:
    p = get_settings().root / "eval" / "datasets" / "actual_costs.csv"
    if not p.exists():
        return {"skipped": "eval/datasets/actual_costs.csv not provided (see actual_costs_template.csv)"}
    from concrete_rag.eval.estimate_variance import variance_report
    r = variance_report(p)
    return {k: v for k, v in r.items() if k != "items"} | {"n_items": len(r.get("items", []))}


def run(k: int = 8, quick: bool = False, log=print) -> dict:
    from concrete_rag.calc.code_tables import verify_all
    log("[eval] retrieval ...")
    report = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "k": k,
              "retrieval": eval_retrieval(k, quick, log)}
    log("[eval] generation ...")
    report["generation"] = eval_generation(log)
    report["calculators"] = eval_calculators()
    log(f"  calculators {report['calculators']['is10262_annex_a']['passed']}")
    report["code_tables"] = {n: {kk: v[kk] for kk in ("status", "matched", "checked")} for n, v in verify_all().items()}
    report["estimates"] = eval_estimates()
    fails = []
    for cfg, th in THRESHOLDS.items():
        agg = report["retrieval"].get(cfg, {}).get("aggregate", {})
        for m, v in th.items():
            if agg and agg[m] < v:
                fails.append(f"{cfg} {m} {agg[m]} < {v}")
    if not report["calculators"]["is10262_annex_a"]["passed"]:
        fails.append("IS 10262 Annex A reproduction failed")
    report["threshold_failures"] = fails
    out = get_settings().root / "eval" / "reports"
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    (out / f"report_{stamp}.json").write_text(json.dumps(report, indent=2, default=str))
    (out / f"report_{stamp}.md").write_text(to_markdown(report))
    log(f"[eval] report -> eval/reports/report_{stamp}.md ; threshold failures: {fails or 'none'}")
    return report


def to_markdown(r: dict) -> str:
    L = [f"# Evaluation report ({r['timestamp']}, k={r['k']})", "", "## Retrieval", "",
         "| config | P@k | R@k | Hit@k | MRR | nDCG@k | s/query |", "|---|---|---|---|---|---|---|"]
    for name, v in r["retrieval"].items():
        a = v["aggregate"]
        L.append(f"| {name} | {a['precision']} | {a['recall']} | {a['hit']} | {a['mrr']} | {a['ndcg']} | {a['latency_s_per_query']} |")
    best = r["retrieval"].get("hybrid+rerank+exact") or r["retrieval"].get("hybrid+rerank")
    if best:
        miss = [q["id"] for q in best["per_query"] if not q["hit"]]
        L += ["", f"Queries with no relevant result in top-k (best config): {', '.join(miss) or 'none'}"]
    L += ["", "## Generation", "", f"```\n{json.dumps(r['generation'].get('aggregate', r['generation']), indent=1)}\n```",
          "", "## Calculators", "", f"```\n{json.dumps(r['calculators'], indent=1)}\n```",
          "", "## Code-table verification", "", "| table | status | rows matched |", "|---|---|---|"]
    for n, v in r["code_tables"].items():
        L.append(f"| {n} | {v['status']} | {v['matched']}/{v['checked']} |")
    L += ["", "## Estimation accuracy", "", f"```\n{json.dumps(r['estimates'], indent=1)}\n```",
          "", f"## Threshold failures\n\n{r['threshold_failures'] or 'none'}"]
    return "\n".join(L) + "\n"
