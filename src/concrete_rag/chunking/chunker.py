"""Chunking at clause / table / SOR-item granularity, with metadata.

IS codes  -> one chunk per clause (long clauses split into parts), per table (rows kept as JSON),
             per amendment entry, plus the foreword.
SOR       -> one chunk per item (full description incl. parent text, unit, rate), per basic rate,
             and per sub-head note.
"""
from __future__ import annotations

import hashlib
import re

from concrete_rag.chunking.enrich import grades, tag
from concrete_rag.parse.is_code import Unit, extract_xrefs
from concrete_rag.parse.sor import SorParseResult
from concrete_rag.schemas import Chunk, ChunkMetadata, SourceDoc

MAX_CHARS = 1600


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def _split(text: str, limit: int = MAX_CHARS) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.;:])\s+(?=[A-Z(a-z]\)|[A-Z])", text):
        if len(cur) + len(sent) > limit and cur:
            parts.append(cur.strip())
            cur = ""
        cur += " " + sent
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _base_meta(doc: SourceDoc, **kw) -> ChunkMetadata:
    return ChunkMetadata(doc_id=doc.doc_id, code_no=doc.code_no, code_label=doc.code_label, doc_type=doc.doc_type,
                         revision_year=doc.revision_year, part=doc.part, is_current=doc.is_current, **kw)


def _enrich(meta: ChunkMetadata, text: str) -> None:
    t = tag(text)
    meta.material_type, meta.work_item = t["material_type"], t["work_item"]
    meta.element_type, meta.applicability = t["element_type"], t["applicability"]


def _merge_tables(units: list[Unit]) -> list[Unit]:
    """Same table number appearing several times (continuation pages, side-by-side tables,
    empty caption fragments) -> merge in reading order, keep the most informative title."""
    out: list[Unit] = []
    by_no: dict[str, Unit] = {}
    for u in units:
        if u.kind != "table":
            out.append(u)
            continue
        key = f"{u.annex or ''}:{u.number}"
        if key in by_no:
            t = by_no[key]
            if len(u.title or "") > len(t.title or ""):
                t.title = u.title
            t.lines += u.lines
            t.table_rows += u.table_rows
            t.notes += u.notes
            t.targets = list(dict.fromkeys(t.targets + u.targets))
            t.page_start, t.page_end = min(t.page_start, u.page_start), max(t.page_end, u.page_end)
            t.min_conf = min(t.min_conf, u.min_conf)
        else:
            by_no[key] = u
            out.append(u)
    return out


def chunk_is_code(doc: SourceDoc, units: list[Unit]) -> list[Chunk]:
    units = _merge_tables(units)
    known = {u.number for u in units if u.kind == "clause" and u.number}
    chunks: list[Chunk] = []
    amend_no = doc.amendment_no
    for idx, u in enumerate(units):
        if u.kind == "table":
            if not u.table_rows and not u.lines:
                continue
            header = f"Table {u.number} {u.title or ''}".strip()
            rows_txt = "\n".join(" | ".join(r) for r in u.table_rows)
            notes = "\n".join(f"NOTE {n}" for n in u.notes)
            text = "\n".join(x for x in (header, rows_txt, notes) if x)
            meta = _base_meta(doc, table_no=u.number, title=u.title, section=u.section, annex=u.annex,
                              page_start=u.page_start, page_end=u.page_end,
                              heading_path=[f"Annex {u.annex}"] if u.annex else [])
            meta.depends_on = [f"{doc.code_no}:cl:{t}" for t in u.targets if t in known or "-" in t]
            meta.depends_on += [r for r in extract_xrefs(text, doc.code_no, known) if r not in meta.depends_on]
            if u.min_conf < 75:
                meta.needs_review = True
                meta.review_reasons.append(f"low OCR confidence in table ({u.min_conf:.0f}) - verify numbers")
            _enrich(meta, text)
            embed = f"{doc.code_label} | Table {u.number}: {u.title or ''} | applies to clauses {', '.join(u.targets)}\n{text}"
            cid = f"{doc.doc_id}:table:{u.number}" + (f":{u.annex}" if u.annex else "")
            chunks.append(Chunk(chunk_id=cid, chunk_type="table", text=text, embed_text=embed, metadata=meta,
                                table={"title": u.title, "rows": u.table_rows, "notes": u.notes,
                                       "applies_to": u.targets}, content_hash=_hash(text)))
            continue
        body = u.text.strip()
        if not body:
            continue
        if u.kind == "amendment":
            meta = _base_meta(doc, amendment_no=u.amendment_no or amend_no, title=u.title,
                              page_start=u.page_start, page_end=u.page_end)
            target_code = doc.code_no
            meta.supersedes = [f"{target_code}:{t}" for t in u.targets]
            meta.depends_on = list(meta.supersedes)
            _enrich(meta, body)
            ref = ", ".join(u.targets) or "general"
            embed = f"{doc.code_label} | Amendment No. {meta.amendment_no} | modifies {ref}\n{body}"
            chunks.append(Chunk(chunk_id=f"{doc.doc_id}:amd{meta.amendment_no}:{idx}", chunk_type="amendment",
                                text=body, embed_text=embed, metadata=meta, content_hash=_hash(body)))
            continue
        if u.kind == "foreword":
            for pi, part in enumerate(_split(body)):
                meta = _base_meta(doc, title="Foreword", page_start=u.page_start, page_end=u.page_end)
                _enrich(meta, part)
                chunks.append(Chunk(chunk_id=f"{doc.doc_id}:foreword:{pi}", chunk_type="text", text=part,
                                    embed_text=f"{doc.code_label} | Foreword\n{part}", metadata=meta,
                                    content_hash=_hash(part)))
            continue
        # clauses / annex clauses
        num = u.number
        parts = _split(body)
        for pi, part in enumerate(parts):
            meta = _base_meta(doc, clause_no=num, title=u.title, section=u.section, annex=u.annex,
                              heading_path=u.heading_path, page_start=u.page_start, page_end=u.page_end)
            meta.has_equation = bool(re.search(r"[=√≤≥]", part))
            meta.depends_on = extract_xrefs(part, doc.code_no, known, own=num)
            if u.min_conf < 60:
                meta.needs_review = True
                meta.review_reasons.append(f"low OCR confidence ({u.min_conf:.0f})")
            _enrich(meta, " ".join(u.heading_path) + " " + (u.title or "") + " " + part)
            g = grades(part)
            if g:
                meta.applicability = sorted(set(meta.applicability + [f"grade:{x}" for x in g]))
            path = " > ".join(([u.section] if u.section else []) + u.heading_path)
            label = f"Clause {num}" if u.kind == "clause" else f"Annex clause {num}"
            embed = f"{doc.code_label} | {path} | {label} {u.title or ''}\n{part}"
            text = f"{num} {u.title + chr(10) if u.title else ''}{part}"
            suffix = f":p{pi + 1}" if len(parts) > 1 else ""
            chunks.append(Chunk(chunk_id=f"{doc.doc_id}:cl:{num}{suffix}",
                                chunk_type="clause" if u.kind == "clause" else "annex_clause",
                                text=text, embed_text=embed, metadata=meta, content_hash=_hash(text)))
    return _dedupe_ids(chunks)


def _dedupe_ids(chunks: list[Chunk]) -> list[Chunk]:
    """Same id twice (clause parsed twice, OCR'd code repeated on a page) -> suffix #n."""
    seen: dict[str, int] = {}
    for c in chunks:
        if c.chunk_id in seen:
            seen[c.chunk_id] += 1
            c.chunk_id = f"{c.chunk_id}#{seen[c.chunk_id]}"
        else:
            seen[c.chunk_id] = 0
    return chunks


def chunk_sor(doc: SourceDoc, res: SorParseResult) -> list[Chunk]:
    chunks: list[Chunk] = []
    for it in res.items:
        rate_txt = f"₹ {it.rate:,.2f} per {it.unit or it.unit_raw}" if it.rate is not None else "rate not available"
        text = (f"{it.sor_name} {it.sor_year} item {it.code} ({it.sub_head or ''})\n{it.full_description}\n"
                f"Unit: {it.unit or it.unit_raw} | Rate: {rate_txt}")
        meta = _base_meta(doc, item_code=it.code, title=it.description[:120], section=it.sub_head,
                          unit=it.unit, rate=it.rate, currency="INR", region=it.region, sor_year=it.sor_year,
                          page_start=it.page, page_end=it.page, heading_path=[x for x in (it.sub_head, it.group_heading) if x],
                          needs_review=it.needs_review, review_reasons=it.review_reasons)
        _enrich(meta, it.full_description)
        g = grades(it.full_description)
        if g:
            meta.applicability = sorted(set(meta.applicability + [f"grade:{x}" for x in g]))
        embed = f"CPWD DSR {it.sor_year} | {it.sub_head} | {it.group_heading or ''} | item {it.code}\n{it.full_description}\nunit {it.unit_raw}"
        chunks.append(Chunk(chunk_id=f"{doc.doc_id}:item:{it.item_key.split(':', 1)[1]}", chunk_type="sor_item",
                            text=text, embed_text=embed, metadata=meta, content_hash=_hash(text)))
    for b in res.basic_rates:
        text = f"{b.sor_name} {b.sor_year} basic rate ({b.category}) code {b.code}: {b.description} | Unit: {b.unit or b.unit_raw} | Rate: ₹ {b.rate if b.rate is not None else 'n/a'}"
        meta = _base_meta(doc, item_code=f"BR:{b.code}", title=b.description[:120], section=f"Basic rates - {b.category}",
                          unit=b.unit, rate=b.rate, currency="INR", region=doc.region, sor_year=b.sor_year,
                          page_start=b.page, page_end=b.page, needs_review=b.needs_review, review_reasons=b.review_reasons)
        _enrich(meta, b.description + " " + b.category)
        chunks.append(Chunk(chunk_id=f"{doc.doc_id}:basic:{b.code}:{b.page}", chunk_type="basic_rate", text=text,
                            embed_text=f"CPWD DSR {b.sor_year} basic rate {b.category}: {b.description} per {b.unit_raw}",
                            metadata=meta, content_hash=_hash(text)))
    for i, n in enumerate(res.notes):
        meta = _base_meta(doc, section=f"Sub head {n['head']}", title="Note", page_start=n["page"], page_end=n["page"],
                          region=doc.region, sor_year=doc.revision_year)
        _enrich(meta, n["text"])
        chunks.append(Chunk(chunk_id=f"{doc.doc_id}:note:{i}", chunk_type="sor_note", text=n["text"],
                            embed_text=f"CPWD DSR {doc.revision_year} sub head {n['head']} note\n{n['text']}",
                            metadata=meta, content_hash=_hash(n["text"])))
    return _dedupe_ids(chunks)
