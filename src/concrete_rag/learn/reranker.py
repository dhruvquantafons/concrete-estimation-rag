"""Fine-tune the cross-encoder reranker on 👍/👎 feedback, then promote it only if it is better.

- Always trains from the shipped base model (settings.rerank_model) on ALL feedback collected so far, so
  repeated rounds do not drift or forget.
- 20% of feedback queries are held out. Promotion needs: held-out MRR higher than the live reranker, and
  golden-set recall / MRR not lower by more than TOLERANCE.
- Plain PyTorch loop over the Hugging Face model (no extra dependencies). Saved to storage/models/reranker-vN.
"""
from __future__ import annotations

import random

from concrete_rag.config import get_settings
from concrete_rag.learn import data, state
from concrete_rag.store import docstore

TOLERANCE = 0.01


def build_pairs(examples: list[dict]) -> list[tuple[str, str, float]]:
    from concrete_rag.retrieval.hybrid import _rerank_text
    ids = {c for ex in examples for c in ex["pos"] | ex["neg"]}
    chunks = docstore.get_chunks(sorted(ids))
    pairs = []
    for ex in examples:
        for cid, label in [(c, 1.0) for c in ex["pos"]] + [(c, 0.0) for c in ex["neg"]]:
            if cid in chunks:
                pairs.append((ex["query"], _rerank_text(chunks[cid]), label))
    return pairs


def train(pairs: list[tuple[str, str, float]], out_dir: str, epochs: int = 2, lr: float = 1e-5,
          batch_size: int = 4, max_length: int = 384, log=print) -> None:
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    from concrete_rag.embed.embedder import _device
    base = get_settings().rerank_model
    dev = _device()
    tok = AutoTokenizer.from_pretrained(base)
    model = AutoModelForSequenceClassification.from_pretrained(base).to(dev)
    model.train()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=0.01)
    loss_fn = torch.nn.BCEWithLogitsLoss()
    rng = random.Random(13)
    for ep in range(epochs):
        rng.shuffle(pairs)
        total = 0.0
        for i in range(0, len(pairs), batch_size):
            b = pairs[i:i + batch_size]
            enc = tok([q for q, _, _ in b], [d for _, d, _ in b], truncation=True, max_length=max_length,
                      padding=True, return_tensors="pt").to(dev)
            logits = model(**enc).logits.view(-1)
            loss = loss_fn(logits, torch.tensor([y for _, _, y in b], device=dev))
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            opt.zero_grad()
            total += loss.item() * len(b)
        log(f"  epoch {ep + 1}/{epochs}: loss {total / max(len(pairs), 1):.4f}")
    model.eval()
    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    # release the training copy and optimizer state (several GB) before evaluation; otherwise the before/after
    # comparison runs under memory pressure and reports the candidate ~3x slower than it really is
    import gc
    del model, opt
    gc.collect()
    if dev == "mps":
        torch.mps.empty_cache()
    elif dev.startswith("cuda"):
        torch.cuda.empty_cache()


def run(min_queries: int = 30, epochs: int = 2, promote: bool = True, log=print) -> dict:
    examples = data.retrieval_examples()
    if len(examples) < min_queries:
        return {"trained": False, "reason": f"only {len(examples)} queries with a 👍 source; need {min_queries}. "
                                            "Keep rating search results (or lower --min-queries)."}
    train_ex, hold = data.split(examples)
    if not hold:
        train_ex, hold = examples[:-max(1, len(examples) // 5)], examples[-max(1, len(examples) // 5):]
    pairs = build_pairs(train_ex)
    log(f"[rerank] {len(train_ex)} training queries -> {len(pairs)} pairs "
        f"({sum(1 for p in pairs if p[2] > 0)} positive); {len(hold)} held out")
    v = state.next_version("reranker")
    out = state.models_dir() / f"reranker-v{v}"
    train(pairs, str(out), epochs=epochs, log=log)

    log("[rerank] evaluating live reranker ...")
    before = {"golden": data.golden_scores(), "holdout_mrr": data.feedback_mrr(hold)}
    log(f"  live:      {before}")
    with state.override(reranker=str(out)):
        after = {"golden": data.golden_scores(), "holdout_mrr": data.feedback_mrr(hold)}
    log(f"  candidate: {after}")
    g0, g1 = before["golden"], after["golden"]
    no_regression = g1["recall"] >= g0["recall"] - TOLERANCE and g1["mrr"] >= g0["mrr"] - TOLERANCE
    improves = (after["holdout_mrr"] or 0) > (before["holdout_mrr"] or 0)
    rel = out.relative_to(get_settings().root) if out.is_relative_to(get_settings().root) else out
    result = {"trained": True, "version": v, "path": str(rel), "before": before, "after": after,
              "no_regression": no_regression, "improves_holdout": improves, "promoted": False}
    if no_regression and improves and promote:
        state.promote("reranker", {"version": v, "path": str(out), "metrics": {"before": before, "after": after},
                                   "n_train_queries": len(train_ex), "n_pairs": len(pairs)})
        result["promoted"] = True
    log(f"[rerank] promoted={result['promoted']} (no golden regression={no_regression}, "
        f"better on held-out feedback={improves})")
    return result
