"""Document store - the source of truth for chunks, SOR items, basic rates, API keys, audit log and
saved estimates. SQLAlchemy Core: SQLite by default, PostgreSQL via CRAG_DOCSTORE_URL."""
from __future__ import annotations

import datetime as dt
import json
from functools import lru_cache
from typing import Any, Iterable

from sqlalchemy import (JSON, Boolean, Column, DateTime, Float, Integer, MetaData, String, Table, Text, create_engine,
                        delete, func, insert, or_, select, update)
from sqlalchemy.engine import Engine

from concrete_rag.config import get_settings
from concrete_rag.schemas import BasicRate, Chunk, SorItem

md = MetaData()

documents = Table("documents", md,
                  Column("doc_id", String(80), primary_key=True),
                  Column("data", JSON), Column("sha256", String(64)),
                  Column("ingested_at", DateTime), Column("stats", JSON))

chunks = Table("chunks", md,
               Column("chunk_id", String(200), primary_key=True),
               Column("doc_id", String(80), index=True),
               Column("chunk_type", String(30), index=True),
               Column("code_no", String(40), index=True),
               Column("clause_no", String(40), index=True),
               Column("table_no", String(20), index=True),
               Column("item_code", String(40), index=True),
               Column("text", Text), Column("embed_text", Text),
               Column("meta", JSON), Column("table_json", JSON),
               Column("content_hash", String(32)),
               Column("needs_review", Boolean, default=False))

sor_items = Table("sor_items", md,
                  Column("item_key", String(120), primary_key=True),
                  Column("sor_name", String(40), index=True), Column("sor_year", Integer, index=True),
                  Column("code", String(40), index=True), Column("unit", String(20)),
                  Column("rate", Float), Column("description", Text), Column("full_description", Text),
                  Column("sub_head", String(120)), Column("page", Integer), Column("doc_id", String(80)),
                  Column("needs_review", Boolean), Column("data", JSON))

basic_rates = Table("basic_rates", md,
                    Column("key", String(120), primary_key=True),
                    Column("sor_name", String(40)), Column("sor_year", Integer),
                    Column("category", String(20), index=True), Column("code", String(20), index=True),
                    Column("description", Text), Column("unit", String(20)), Column("rate", Float),
                    Column("needs_review", Boolean), Column("data", JSON))

cement_coefficients = Table("cement_coefficients", md,
                            Column("id", Integer, primary_key=True, autoincrement=True),
                            Column("sor_name", String(40)), Column("sor_year", Integer),
                            Column("code", String(40), index=True), Column("unit", String(20)),
                            Column("cement_quintal_per_unit", Float), Column("data", JSON))

api_keys = Table("api_keys", md,
                 Column("id", Integer, primary_key=True, autoincrement=True),
                 Column("name", String(80), unique=True), Column("key_hash", String(64), unique=True),
                 Column("prefix", String(12)), Column("scopes", JSON),
                 Column("created_at", DateTime), Column("revoked", Boolean, default=False))

audit_log = Table("audit_log", md,
                  Column("id", Integer, primary_key=True, autoincrement=True),
                  Column("ts", DateTime), Column("key_name", String(80)), Column("method", String(10)),
                  Column("path", String(200)), Column("status", Integer), Column("ms", Float),
                  Column("client", String(64)))

estimates = Table("estimates", md,
                  Column("id", String(40), primary_key=True),
                  Column("created_at", DateTime), Column("project", String(200)),
                  Column("data", JSON))


@lru_cache
def engine() -> Engine:
    eng = create_engine(get_settings().docstore_uri, future=True)
    md.create_all(eng)
    return eng


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------ writes
def replace_document(doc_id: str, doc_data: dict, sha: str | None, stats: dict, doc_chunks: list[Chunk],
                     items: Iterable[SorItem] = (), basics: Iterable[BasicRate] = (), coeffs: Iterable[dict] = ()):
    """Idempotent re-ingest of one document."""
    with engine().begin() as c:
        for t, col in ((chunks, chunks.c.doc_id), (sor_items, sor_items.c.doc_id)):
            c.execute(delete(t).where(col == doc_id))
        c.execute(delete(basic_rates).where(basic_rates.c.key.in_(
            select(basic_rates.c.key).where(basic_rates.c.data["doc_id"].as_string() == doc_id))))
        c.execute(delete(cement_coefficients).where(cement_coefficients.c.data["doc_id"].as_string() == doc_id))
        c.execute(delete(documents).where(documents.c.doc_id == doc_id))
        c.execute(insert(documents).values(doc_id=doc_id, data=doc_data, sha256=sha, ingested_at=_now(), stats=stats))
        if doc_chunks:
            c.execute(insert(chunks), [{
                "chunk_id": ch.chunk_id, "doc_id": doc_id, "chunk_type": ch.chunk_type,
                "code_no": ch.metadata.code_no, "clause_no": ch.metadata.clause_no, "table_no": ch.metadata.table_no,
                "item_code": ch.metadata.item_code, "text": ch.text, "embed_text": ch.embed_text,
                "meta": ch.metadata.model_dump(), "table_json": ch.table, "content_hash": ch.content_hash,
                "needs_review": ch.metadata.needs_review} for ch in doc_chunks])
        items = list(items)
        if items:
            c.execute(insert(sor_items), [{
                "item_key": it.item_key, "sor_name": it.sor_name, "sor_year": it.sor_year, "code": it.code,
                "unit": it.unit, "rate": it.rate, "description": it.description,
                "full_description": it.full_description, "sub_head": it.sub_head, "page": it.page,
                "doc_id": it.doc_id, "needs_review": it.needs_review, "data": it.model_dump()} for it in items])
        basics = list(basics)
        if basics:
            existing = {r[0] for r in c.execute(select(basic_rates.c.key))}
            rows = []
            for b in basics:
                key, n = b.key, 1
                while key in existing:
                    n += 1
                    key = f"{b.key}#{n}"
                existing.add(key)
                rows.append({"key": key, "sor_name": b.sor_name, "sor_year": b.sor_year, "category": b.category,
                             "code": b.code, "description": b.description, "unit": b.unit, "rate": b.rate,
                             "needs_review": b.needs_review, "data": b.model_dump()})
            c.execute(insert(basic_rates), rows)
        coeffs = list(coeffs)
        if coeffs:
            c.execute(insert(cement_coefficients), [{
                "sor_name": x["sor_name"], "sor_year": x["sor_year"], "code": x["code"], "unit": x["unit"],
                "cement_quintal_per_unit": x["cement_quintal_per_unit"], "data": x} for x in coeffs])


# ------------------------------------------------------------------ reads
def _chunk_row(r) -> dict[str, Any]:
    return {"chunk_id": r.chunk_id, "chunk_type": r.chunk_type, "text": r.text, "embed_text": r.embed_text,
            "metadata": r.meta, "table": r.table_json, "content_hash": r.content_hash}


def all_chunks() -> list[dict[str, Any]]:
    with engine().connect() as c:
        return [_chunk_row(r) for r in c.execute(select(chunks))]


def get_chunks(ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    with engine().connect() as c:
        return {r.chunk_id: _chunk_row(r) for r in c.execute(select(chunks).where(chunks.c.chunk_id.in_(ids)))}


def find_clause(code_no: str, clause: str, part: str | None = None, limit: int = 6) -> list[dict]:
    """Exact clause lookup incl. children (26.5 -> 26.5, 26.5.1, ...)."""
    with engine().connect() as c:
        q = select(chunks).where(chunks.c.code_no == code_no).where(
            or_(chunks.c.clause_no == clause, chunks.c.clause_no.like(f"{clause}.%"))).order_by(chunks.c.clause_no)
        rows = [_chunk_row(r) for r in c.execute(q)]
    if part:
        rows = [r for r in rows if (r["metadata"].get("part") or "") == part] or rows
    rows.sort(key=lambda r: (r["metadata"]["clause_no"] != clause, len(r["metadata"]["clause_no"] or "")))
    return rows[:limit]


def find_table(code_no: str, table_no: str, part: str | None = None) -> list[dict]:
    with engine().connect() as c:
        q = select(chunks).where(chunks.c.code_no == code_no, chunks.c.table_no == table_no,
                                 chunks.c.chunk_type == "table")
        rows = [_chunk_row(r) for r in c.execute(q)]
    if part:
        rows = [r for r in rows if (r["metadata"].get("part") or "") == part] or rows
    return rows


def amendments_for(code_no: str, refs: list[str]) -> list[dict]:
    """Amendment chunks whose `supersedes` mention any of refs (e.g. 'IS 456:cl:26.4.2')."""
    if not refs:
        return []
    with engine().connect() as c:
        rows = [_chunk_row(r) for r in c.execute(select(chunks).where(chunks.c.chunk_type == "amendment",
                                                                      chunks.c.code_no == code_no))]
    want = set(refs)
    return [r for r in rows if want & set(r["metadata"].get("supersedes") or [])]


def get_sor_item(code: str, sor_name: str = "CPWD-DSR", year: int | None = None) -> dict | None:
    with engine().connect() as c:
        q = select(sor_items).where(sor_items.c.code == code, sor_items.c.sor_name == sor_name)
        if year:
            q = q.where(sor_items.c.sor_year == year)
        r = c.execute(q.order_by(sor_items.c.sor_year.desc())).first()
        return dict(r.data) if r else None


def search_sor_items(text: str | None = None, prefix: str | None = None, unit: str | None = None,
                     limit: int = 50) -> list[dict]:
    with engine().connect() as c:
        q = select(sor_items)
        if prefix:
            q = q.where(or_(sor_items.c.code == prefix, sor_items.c.code.like(f"{prefix}.%")))
        if unit:
            q = q.where(sor_items.c.unit == unit)
        if text:
            for w in text.split():
                q = q.where(sor_items.c.full_description.ilike(f"%{w}%"))
        return [dict(r.data) for r in c.execute(q.limit(limit))]


def get_basic_rate(code: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(select(basic_rates).where(basic_rates.c.code == code)).first()
        return dict(r.data) if r else None


def search_basic_rates(text: str, category: str | None = None, limit: int = 20) -> list[dict]:
    with engine().connect() as c:
        q = select(basic_rates)
        if category:
            q = q.where(basic_rates.c.category == category)
        for w in text.split():
            q = q.where(basic_rates.c.description.ilike(f"%{w}%"))
        return [dict(r.data) for r in c.execute(q.limit(limit))]


def get_cement_coefficient(code: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(select(cement_coefficients).where(cement_coefficients.c.code == code)).first()
        return dict(r.data) if r else None


def review_queue() -> dict[str, list[dict]]:
    with engine().connect() as c:
        items = [dict(r.data) for r in c.execute(select(sor_items).where(sor_items.c.needs_review.is_(True)))]
        basics = [dict(r.data) for r in c.execute(select(basic_rates).where(basic_rates.c.needs_review.is_(True)))]
        chs = [_chunk_row(r) for r in c.execute(select(chunks).where(chunks.c.needs_review.is_(True),
                                                                    chunks.c.chunk_type.in_(["table", "clause"])))]
    return {"sor_items": items, "basic_rates": basics, "chunks": chs}


def counts() -> dict[str, int]:
    with engine().connect() as c:
        return {t.name: c.execute(select(func.count()).select_from(t)).scalar_one()
                for t in (documents, chunks, sor_items, basic_rates, cement_coefficients)}


def list_documents() -> list[dict]:
    with engine().connect() as c:
        return [{"doc_id": r.doc_id, **(r.data or {}), "ingested_at": str(r.ingested_at), "stats": r.stats}
                for r in c.execute(select(documents))]


# ------------------------------------------------------------------ api keys / audit / estimates
def add_api_key(name: str, key_hash: str, prefix: str, scopes: list[str]):
    with engine().begin() as c:
        c.execute(insert(api_keys).values(name=name, key_hash=key_hash, prefix=prefix, scopes=scopes,
                                          created_at=_now(), revoked=False))


def lookup_api_key(key_hash: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(select(api_keys).where(api_keys.c.key_hash == key_hash, api_keys.c.revoked.is_(False))).first()
        return {"name": r.name, "scopes": r.scopes} if r else None


def revoke_api_key(name: str) -> bool:
    with engine().begin() as c:
        return c.execute(update(api_keys).where(api_keys.c.name == name).values(revoked=True)).rowcount > 0


def list_api_keys() -> list[dict]:
    with engine().connect() as c:
        return [{"name": r.name, "prefix": r.prefix, "scopes": r.scopes, "created_at": str(r.created_at),
                 "revoked": r.revoked} for r in c.execute(select(api_keys))]


def write_audit(key_name: str | None, method: str, path: str, status: int, ms: float, client: str | None):
    with engine().begin() as c:
        c.execute(insert(audit_log).values(ts=_now(), key_name=key_name, method=method, path=path, status=status,
                                           ms=ms, client=client))


def save_estimate(est_id: str, project: str, data: dict):
    with engine().begin() as c:
        c.execute(delete(estimates).where(estimates.c.id == est_id))
        c.execute(insert(estimates).values(id=est_id, created_at=_now(), project=project, data=json.loads(
            json.dumps(data, default=str))))


def get_estimate(est_id: str) -> dict | None:
    with engine().connect() as c:
        r = c.execute(select(estimates).where(estimates.c.id == est_id)).first()
        return dict(r.data) if r else None


def list_estimates(limit: int = 50) -> list[dict]:
    with engine().connect() as c:
        rows = c.execute(select(estimates).order_by(estimates.c.created_at.desc()).limit(limit)).all()
    out = []
    for r in rows:
        d = r.data or {}
        out.append({"id": r.id, "project": r.project, "created": str(r.created_at), "location": d.get("location"),
                    "grand_total": (d.get("summary") or {}).get("grand_total"), "lines": len(d.get("boq") or []),
                    "warnings": len(d.get("warnings") or [])})
    return out
