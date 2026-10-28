"""Endpoints that support the web UI (/ui): identity, overview, source-page images, estimate history,
review queue corrections and code-table sign-off."""
from __future__ import annotations

import re
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from concrete_rag.api.auth import principal, require
from concrete_rag.config import get_settings
from concrete_rag.store import docstore, review

router = APIRouter(prefix="/v1")


@router.get("/whoami", tags=["ui"])
def whoami(p: dict = Depends(principal)):
    return p


@router.get("/overview", tags=["ui"])
def overview(p: dict = Depends(principal)):
    from concrete_rag.calc.code_tables import verify_all
    from concrete_rag.store import vector
    counts = docstore.counts()
    q = docstore.review_queue()
    items_total = counts["sor_items"] or 1
    tables = verify_all()
    st = {}
    for v in tables.values():
        st[v["status"]] = st.get(v["status"], 0) + 1
    return {
        "me": p,
        "counts": counts,
        "index": vector.collection_info(),
        "review": {"sor_items": len(q["sor_items"]), "basic_rates": len(q["basic_rates"]), "is_chunks": len(q["chunks"]),
                   "dar_dsr": review.list_dar_differences(limit=0)["total"]},
        "items_clean_pct": round(100 * (counts["sor_items"] - len(q["sor_items"])) / items_total, 1),
        "rates_vote_confirmed_pct": _vote_pct(),
        "code_tables": st,
        "llm_provider": get_settings().llm_provider,
        "locations": sorted((_cost_cfg().get("indices") or {}).keys()),
        "documents": [{"doc_id": d["doc_id"], "title": d.get("title"), "code_label": f"{d.get('code_no')}"
                       + (f" (Part {d['part']})" if d.get("part") else "") + f" : {d.get('revision_year')}",
                       "doc_type": d.get("doc_type"), "pages": (d.get("stats") or {}).get("pages"),
                       "chunks": (d.get("stats") or {}).get("chunks"), "ingested_at": d.get("ingested_at")}
                      for d in docstore.list_documents()],
        "recent_estimates": docstore.list_estimates(5),
    }


def _cost_cfg() -> dict:
    from concrete_rag.calc.rates import _cfg
    return _cfg("cost_indices")


def _vote_pct() -> float:
    from sqlalchemy import select

    from concrete_rag.store.docstore import engine, sor_items
    with engine().connect() as c:
        rows = [r.data for r in c.execute(select(sor_items.c.data))]
    ok = sum(1 for d in rows if (d.get("rate_votes") or 0) >= 2 or d.get("corrected_by"))
    return round(100 * ok / max(len(rows), 1), 1)


@lru_cache(maxsize=1)
def _doc_paths() -> dict[str, str]:
    from concrete_rag.registry import load_sources
    return {d.doc_id: str(get_settings().root / d.path) for d in load_sources()}


@lru_cache(maxsize=64)
def _render(doc_id: str, page: int, dpi: int) -> bytes:
    import pymupdf
    path = _doc_paths().get(doc_id)
    if not path:
        raise KeyError(f"unknown document {doc_id}")
    with pymupdf.open(path) as d:
        if not 1 <= page <= len(d):
            raise KeyError(f"page {page} out of range")
        return d[page - 1].get_pixmap(dpi=dpi).tobytes("png")


@router.get("/source/page", tags=["ui"], response_class=Response)
def source_page(doc_id: str, page: int, dpi: int = 110, _=Depends(require("retrieve"))):
    """Image of the original PDF page, so every citation can be checked against the source."""
    dpi = max(60, min(dpi, 200))
    return Response(_render(doc_id, page, dpi), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=3600"})


@router.get("/estimates", tags=["estimation"])
def estimates(limit: int = 50, _=Depends(require("estimate"))):
    return docstore.list_estimates(limit)


# ------------------------------------------------------------------ review (admin)
@router.get("/admin/review/sor", tags=["admin"])
def review_sor(q: str | None = None, head: int | None = None, offset: int = 0, limit: int = 50,
               _=Depends(require("admin"))):
    return review.list_sor_review(q, head, offset, min(limit, 200))


@router.get("/admin/review/basic", tags=["admin"])
def review_basic(q: str | None = None, category: str | None = None, offset: int = 0, limit: int = 50,
                 _=Depends(require("admin"))):
    return review.list_basic_review(q, category, offset, min(limit, 200))


@router.get("/admin/review/dar-dsr", tags=["admin"])
def review_dar_dsr(q: str | None = None, offset: int = 0, limit: int = 50, _=Depends(require("admin"))):
    return review.list_dar_differences(q, offset, min(limit, 200))


class DarDecision(BaseModel):
    key: str
    decision: str = Field(..., pattern="^(dar|dsr|other)$")
    checked_by: str = Field(..., min_length=2, max_length=80)
    rate: float | None = Field(None, ge=0)
    note: str | None = Field(None, max_length=300)


@router.post("/admin/review/dar-dsr", tags=["admin"])
def resolve_dar_dsr(body: DarDecision, _=Depends(require("admin"))):
    return review.resolve_dar_difference(body.key, body.decision, body.checked_by, body.rate, body.note)


class Correction(BaseModel):
    kind: str = Field(..., pattern="^(sor|basic)$")
    key: str
    checked_by: str = Field(..., min_length=2, max_length=80)
    rate: float | None = Field(None, ge=0)
    unit: str | None = None
    code: str | None = Field(None, pattern=r"^(\d{1,2}(\.\d{1,3}){0,3}[A-Z]?(\.\d{1,2})?|\d{3,5})$")  # DSR item | basic rate
    note: str | None = Field(None, max_length=300)
    exclude: bool = Field(False, description="row is an OCR artefact, not a real item - remove it")


@router.post("/admin/corrections", tags=["admin"])
def add_correction(body: Correction, _=Depends(require("admin"))):
    if body.kind == "sor":
        return review.apply_sor_correction(body.key, body.checked_by, body.rate, body.unit, body.code, body.note,
                                           body.exclude)
    return review.apply_basic_correction(body.key, body.checked_by, body.rate, body.unit, body.note, body.exclude,
                                         body.code)


@router.get("/admin/corrections", tags=["admin"])
def corrections(_=Depends(require("admin"))):
    return review.list_corrections()


@router.get("/admin/code-tables", tags=["admin"])
def code_tables(_=Depends(require("calc"))):
    return review.code_tables_overview()


class SignOff(BaseModel):
    verified_by: str = Field(..., min_length=2, max_length=80)


@router.post("/admin/code-tables/{name}/sign-off", tags=["admin"])
def sign_off(name: str, body: SignOff, _=Depends(require("admin"))):
    from concrete_rag.calc.code_tables import signed_status
    if signed_status(body.verified_by) != "verified":
        raise HTTPException(422, "sign off with your own name; AI checks are recorded separately")
    if not re.fullmatch(r"[a-z0-9_]+", name):
        raise HTTPException(400, "bad table name")
    return review.sign_off_table(name, body.verified_by)
