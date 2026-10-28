"""Hybrid retrieval: exact-reference lookup + dense + BM25 (RRF fusion) -> cross-encoder rerank ->
cross-reference / amendment expansion -> cited ContextPack."""
from __future__ import annotations

from typing import Any

from concrete_rag.config import get_settings
from concrete_rag.retrieval.router import ParsedQuery, parse_query
from concrete_rag.schemas import Citation, ContextPack, RetrievedChunk
from concrete_rag.store import docstore, vector

RRF_K = 60


def citation_for(ch: dict[str, Any]) -> Citation:
    m = ch["metadata"]
    t = ch["chunk_type"]
    label = m.get("code_label") or m.get("code_no")
    if t in ("sor_item",):
        ref = f"CPWD DSR {m.get('sor_year')}, item {m.get('item_code')}"
    elif t == "dar_analysis":
        ref = f"CPWD DAR {m.get('sor_year')}, analysis of item {m.get('item_code')}"
    elif t == "basic_rate":
        ref = f"CPWD DSR {m.get('sor_year')}, basic rate {m.get('item_code', '').replace('BR:', 'code ')}"
    elif t == "table":
        ref = f"{label}, Table {m.get('table_no')}" + (f" (Annex {m['annex']})" if m.get("annex") else "")
    elif t in ("clause", "annex_clause"):
        ref = f"{label}, cl. {m.get('clause_no')}"
    elif t == "amendment":
        ref = f"{label}, Amendment No. {m.get('amendment_no')}"
    elif t == "sor_note":
        ref = f"CPWD DSR {m.get('sor_year')}, {m.get('section')} note"
    else:
        ref = f"{label}, {m.get('title') or t}"
    pages = m.get("page_start"), m.get("page_end")
    if pages[0]:
        ref += f" [pdf p.{pages[0]}" + (f"-{pages[1]}" if pages[1] and pages[1] != pages[0] else "") + "]"
    return Citation(ref=ref, doc_id=m["doc_id"], chunk_id=ch["chunk_id"], page_start=pages[0], page_end=pages[1],
                    verified=not m.get("needs_review", False))


def _payload_to_chunk(p: dict) -> dict:
    meta = {k: v for k, v in p.items() if k not in ("text", "chunk_id", "chunk_type", "content_hash")}
    return {"chunk_id": p["chunk_id"], "chunk_type": p["chunk_type"], "text": p["text"], "metadata": meta}


def default_filters(pq: ParsedQuery, filters: dict | None) -> dict:
    f = {"is_current": True}
    if pq.code_no and not (filters and "code_no" in filters):
        f["code_no"] = pq.code_no
    f.update(filters or {})
    return {k: v for k, v in f.items() if v is not None}


def exact_hits(pq: ParsedQuery) -> list[dict]:
    hits: list[dict] = []
    codes = [pq.code_no] if pq.code_no else ["IS 456", "IS 10262", "IS 1200", "IS 2386"]
    for code in codes:
        for c in pq.clauses:
            hits += docstore.find_clause(code, c, pq.part, limit=3)
        for t in pq.tables:
            hits += docstore.find_table(code, t, pq.part)
        if hits and not pq.code_no:
            break
    for it in pq.items:
        rows = docstore.get_chunks([f"CPWD_DSR_2023_V1:item:{it}", f"CPWD_DSR_2023_V2:item:{it}",
                                    f"CPWD_DAR_2023_V1:dar:{it}", f"CPWD_DAR_2023_V2:dar:{it}"])
        hits += list(rows.values())
    seen, out = set(), []
    for h in hits:
        if h["chunk_id"] not in seen:
            seen.add(h["chunk_id"])
            out.append(h)
    return out


def candidate_lists(query: str, filters: dict | None, k: int, mode: str = "hybrid") -> list[tuple[str, list]]:
    lists = []
    if mode in ("hybrid", "dense"):
        lists.append(("dense", vector.search_dense(query, k, filters)))
    if mode in ("hybrid", "bm25"):
        lists.append(("bm25", vector.search_bm25(query, k, filters)))
    return lists


def fuse(lists: list[tuple[str, list]], weights: dict[str, float] | None = None) -> list[tuple[dict, float, list[str]]]:
    """Weighted reciprocal-rank fusion. Weights come from the learning loop (learn/fusion.py); default 1:1."""
    if weights is None:
        from concrete_rag.learn.state import fusion_weights
        weights = fusion_weights()
    fused: dict[str, list] = {}
    for name, res in lists:
        for rank, (payload, _score) in enumerate(res):
            cid = payload["chunk_id"]
            entry = fused.setdefault(cid, [payload, 0.0, []])
            entry[1] += weights.get(name, 1.0) / (RRF_K + rank + 1)
            entry[2].append(name)
    ranked = sorted(fused.values(), key=lambda e: -e[1])
    return [(_payload_to_chunk(p), s, src) for p, s, src in ranked]


def hybrid_candidates(query: str, filters: dict | None, k: int, mode: str = "hybrid") -> list[tuple[dict, float, list[str]]]:
    """mode: hybrid | dense | bm25. Returns [(chunk, fused_score, sources)]."""
    return fuse(candidate_lists(query, filters, k, mode))


def _rerank_text(ch: dict) -> str:
    m = ch["metadata"]
    head = " ".join(x for x in [m.get("code_label") or "", " > ".join(m.get("heading_path") or []), m.get("title") or ""] if x)
    return f"{head}\n{ch['text']}"[:1200]


def expand(chunks: list[RetrievedChunk], max_extra: int = 6) -> list[RetrievedChunk]:
    """Attach (1 hop) referenced clauses/tables and amendments that modify retrieved clauses."""
    have = {c.chunk_id for c in chunks}
    extra: list[RetrievedChunk] = []
    for c in chunks:
        if len(extra) >= max_extra:
            break
        m = c.metadata
        code = m.get("code_no")
        own_refs = []
        if m.get("clause_no"):
            own_refs.append(f"{code}:cl:{m['clause_no']}")
        if m.get("table_no"):
            own_refs.append(f"{code}:table:{m['table_no']}")
        related = docstore.amendments_for(code, own_refs) if code else []
        for dep in (m.get("depends_on") or [])[:3]:
            parts = dep.split(":")
            if len(parts) == 3 and parts[1] == "cl":
                related += docstore.find_clause(parts[0], parts[2], m.get("part"), limit=1)
            elif len(parts) == 3 and parts[1] == "table":
                related += docstore.find_table(parts[0], parts[2], m.get("part"))[:1]
        for r in related:
            if r["chunk_id"] in have or len(extra) >= max_extra:
                continue
            have.add(r["chunk_id"])
            src = "amendment" if r["chunk_type"] == "amendment" else "xref"
            extra.append(RetrievedChunk(chunk_id=r["chunk_id"], chunk_type=r["chunk_type"], text=r["text"],
                                        metadata=r["metadata"], score=0.0, retrieval=[src, f"from:{c.chunk_id}"],
                                        citation=citation_for(r), table=r.get("table")))
    return chunks + extra


def retrieve(query: str, top_k: int | None = None, filters: dict | None = None, mode: str = "hybrid",
             use_rerank: bool | None = None, use_expand: bool = True, use_exact: bool = True) -> ContextPack:
    s = get_settings()
    top_k = top_k or s.top_k
    use_rerank = s.rerank_enabled if use_rerank is None else use_rerank
    pq = parse_query(query)
    f = default_filters(pq, filters)
    warnings: list[str] = []

    exact = exact_hits(pq) if use_exact else []
    cands = hybrid_candidates(query, f, s.prefetch_k, mode)
    if not cands and f.get("code_no"):
        warnings.append(f"no results with code filter {f['code_no']}; retried without it")
        f.pop("code_no")
        cands = hybrid_candidates(query, f, s.prefetch_k, mode)
    pool = cands[: max(s.rerank_pool, top_k)]
    if use_rerank and pool:
        from concrete_rag.retrieval.rerank import rerank
        scores = rerank(query, [_rerank_text(c) for c, _, _ in pool])
        pool = [(c, sc, src + ["rerank"]) for (c, _, src), sc in zip(pool, scores)]
        pool.sort(key=lambda x: -x[1])
    results: list[RetrievedChunk] = []
    seen = set()
    for h in exact:
        seen.add(h["chunk_id"])
        results.append(RetrievedChunk(chunk_id=h["chunk_id"], chunk_type=h["chunk_type"], text=h["text"],
                                      metadata=h["metadata"], score=1e6, retrieval=["exact"],
                                      citation=citation_for(h), table=h.get("table")))
    for c, sc, src in pool:
        if len(results) >= top_k + len(exact):
            break
        if c["chunk_id"] in seen:
            continue
        seen.add(c["chunk_id"])
        results.append(RetrievedChunk(chunk_id=c["chunk_id"], chunk_type=c["chunk_type"], text=c["text"],
                                      metadata=c["metadata"], score=float(sc), retrieval=src,
                                      citation=citation_for(c)))
    # tables: hydrate JSON from the docstore
    tbl = docstore.get_chunks([r.chunk_id for r in results if r.chunk_type == "table" and r.table is None])
    for r in results:
        if r.chunk_id in tbl:
            r.table = tbl[r.chunk_id].get("table")
    if use_expand:
        results = expand(results)
    rate_items = []
    for r in results:
        if r.chunk_type == "sor_item":
            it = docstore.get_sor_item(r.metadata.get("item_code"), year=r.metadata.get("sor_year"))
            if it:
                rate_items.append(it)
    if any(r.metadata.get("needs_review") for r in results):
        warnings.append("some retrieved content is flagged for review (OCR uncertainty) - see citation.verified")
    return ContextPack(query=query, chunks=results, rate_items=rate_items, filters=f, warnings=warnings)
