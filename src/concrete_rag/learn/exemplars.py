"""Answers learn from approved feedback, without retraining the LLM.

1. Exemplars: answers an admin approved on the Learning page (a 👍 answer, or an engineer's correction of a
   👎 one) are shown to the LLM as examples when a new question is similar. They steer style and level of
   detail only: facts still have to come from the retrieved sources, and verify_numbers() checks every
   number against those sources, so a number copied from an exemplar is flagged as ungrounded.
2. Preference pairs: 👎 answers with an approved correction are exported as {prompt, chosen, rejected}
   (data/processed/preferences.jsonl) for a later DPO fine-tune of the local model.
"""
from __future__ import annotations

import json
from functools import lru_cache

from concrete_rag.config import get_settings
from concrete_rag.store import docstore

MIN_SIMILARITY = 0.80
MAX_EXEMPLARS = 2


def approved() -> list[dict]:
    out = []
    for f in docstore.list_feedback(status="approved", kind="answer", limit=5000):
        text = f["correction"] or (f["answer"] if f["rating"] > 0 else None)
        if text and f["query"]:
            out.append({"id": f["id"], "query": f["query"], "answer": text})
    return out


@lru_cache(maxsize=8)
def _matrix(key: tuple):
    import numpy as np

    from concrete_rag.embed.embedder import embed_texts
    m = np.asarray(embed_texts([q for _, q in key]), dtype="float32")
    return m / np.linalg.norm(m, axis=1, keepdims=True)


def similar(query: str, k: int = MAX_EXEMPLARS) -> list[dict]:
    ex = approved()
    if not ex:
        return []
    import numpy as np

    from concrete_rag.embed.embedder import embed_query
    m = _matrix(tuple((e["id"], e["query"]) for e in ex))
    q = np.asarray(embed_query(query), dtype="float32")
    sims = m @ (q / np.linalg.norm(q))
    order = [i for i in np.argsort(-sims)[:k] if sims[i] >= MIN_SIMILARITY]
    return [{**ex[i], "similarity": round(float(sims[i]), 3)} for i in order]


def prompt_block(exemplars: list[dict]) -> str:
    if not exemplars:
        return ""
    parts = [f"Q: {e['query']}\nA: {e['answer']}" for e in exemplars]
    return ("### EXAMPLES OF ANSWERS AN ENGINEER APPROVED (follow their style and level of detail only - "
            "take every fact and number from the SOURCES below, never from these examples)\n"
            + "\n\n".join(parts) + "\n\n")


def export_preferences(path=None) -> dict:
    path = path or get_settings().processed_dir / "preferences.jsonl"
    rows = []
    for f in docstore.list_feedback(status="approved", kind="answer", limit=100_000):
        if f["rating"] < 0 and f["correction"] and f["answer"]:
            rows.append({"query": f["query"], "source_chunk_ids": f["results"], "chosen": f["correction"],
                         "rejected": f["answer"], "feedback_id": f["id"]})
    with open(path, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return {"pairs": len(rows), "path": str(path)}
