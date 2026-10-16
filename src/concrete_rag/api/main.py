"""Secure REST API exposing retrieval, generation, calculators and estimation to downstream AI models.

Auth: X-API-Key header (scopes: retrieve, generate, calc, estimate, admin). Rate limited per key/IP.
Every request is written to the audit log. OpenAPI docs at /docs.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from concrete_rag.api.auth import require
from concrete_rag.calc import rates, rcc
from concrete_rag.calc.code_tables import verify_all
from concrete_rag.calc.mix_design import MixDesignInput, design_mix
from concrete_rag.calc.quantities import Element, takeoff
from concrete_rag.calc.steel import BarMark, bbs
from concrete_rag.config import get_settings
from concrete_rag.estimate.boq import EstimateRequest, build_estimate
from concrete_rag.estimate.export import to_excel, to_pdf
from concrete_rag.store import docstore

MAX_BODY = 2_000_000


def _key_or_ip(request: Request) -> str:
    return request.headers.get("x-api-key", "")[:16] or get_remote_address(request)


limiter = Limiter(key_func=_key_or_ip, default_limits=[get_settings().api_rate_limit])
app = FastAPI(title="Concrete Works RAG & Estimation API", version="0.1.0",
              description="Citation-backed retrieval over IS 456 / 10262 / 1200 / 2386 and CPWD DSR 2023, "
                          "plus deterministic IS-compliant calculators and BOQ estimation.")
app.state.limiter = limiter

origins = [o.strip() for o in get_settings().api_cors_origins.split(",") if o.strip()]
if origins:
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                       allow_headers=["X-API-Key", "Content-Type"])


@app.exception_handler(RateLimitExceeded)
def _rl(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"detail": f"rate limit exceeded: {exc.detail}"}, status_code=429)


@app.exception_handler(ValueError)
def _ve(request: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(KeyError)
def _ke(request: Request, exc: KeyError):
    return JSONResponse({"detail": str(exc).strip("'\"")}, status_code=404)


@app.middleware("http")
async def guard_and_audit(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return JSONResponse({"detail": "request body too large"}, status_code=413)
    t = time.perf_counter()
    response = await call_next(request)
    try:
        docstore.write_audit(getattr(request.state, "key_name", None), request.method, request.url.path,
                             response.status_code, (time.perf_counter() - t) * 1000,
                             request.client.host if request.client else None)
    except Exception:  # noqa: BLE001 - auditing must never break a request
        pass
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.on_event("startup")
def _warm_models():
    """Load the embedding + reranker models in the background so the first search is not slow."""
    import threading

    def warm():
        try:
            from concrete_rag.embed.embedder import embed_query
            embed_query("warm up")
            if get_settings().rerank_enabled:
                from concrete_rag.retrieval.rerank import rerank
                rerank("warm up", ["warm up"])
        except Exception:  # noqa: BLE001 - warm-up is best effort
            pass
    threading.Thread(target=warm, daemon=True).start()


@app.get("/health")
def health():
    from concrete_rag.store import vector
    return {"status": "ok", "docstore": docstore.counts(), "index": vector.collection_info(),
            "llm_provider": get_settings().llm_provider}


v1 = APIRouter(prefix="/v1")


# ------------------------------------------------------------------ retrieval / generation
class RetrieveRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=2000)
    top_k: int = Field(8, ge=1, le=30)
    filters: dict[str, Any] | None = Field(None, description="e.g. {'code_no': 'IS 456'} or {'doc_type': 'sor'}")
    mode: str = Field("hybrid", pattern="^(hybrid|dense|bm25)$")
    rerank: bool = True


@v1.post("/retrieve", tags=["retrieval"])
@limiter.limit(get_settings().api_rate_limit)
def retrieve_ep(request: Request, body: RetrieveRequest, _=Depends(require("retrieve"))):
    from concrete_rag.retrieval.hybrid import retrieve
    return retrieve(body.query, body.top_k, body.filters, body.mode, body.rerank).model_dump()


class GenerateRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=4000)
    top_k: int = Field(6, ge=1, le=20)
    filters: dict[str, Any] | None = None


@v1.post("/generate", tags=["generation"])
@limiter.limit("20/minute")
def generate_ep(request: Request, body: GenerateRequest, _=Depends(require("generate"))):
    from concrete_rag.agents.generate import answer
    return answer(body.query, body.top_k, body.filters)


class AgentRequest(BaseModel):
    task: str = Field(..., min_length=5, max_length=8000)
    max_steps: int = Field(12, ge=1, le=30)


@v1.post("/agent", tags=["generation"])
@limiter.limit("10/minute")
def agent_ep(request: Request, body: AgentRequest, _=Depends(require("generate"))):
    from concrete_rag.agents.generate import agent
    return agent(body.task, body.max_steps)


# ------------------------------------------------------------------ calculators
@v1.post("/mix-design", tags=["calculators"])
def mix_ep(body: MixDesignInput, _=Depends(require("calc"))):
    return design_mix(body).model_dump()


@v1.post("/design/beam", tags=["calculators"])
def beam_ep(body: rcc.BeamInput, _=Depends(require("calc"))):
    return rcc.design_beam(body).model_dump()


@v1.post("/design/slab", tags=["calculators"])
def slab_ep(body: rcc.SlabInput, _=Depends(require("calc"))):
    return rcc.design_slab_one_way(body).model_dump()


@v1.post("/design/column", tags=["calculators"])
def column_ep(body: rcc.ColumnInput, _=Depends(require("calc"))):
    return rcc.design_column_axial(body).model_dump()


@v1.post("/design/footing", tags=["calculators"])
def footing_ep(body: rcc.FootingInput, _=Depends(require("calc"))):
    return rcc.design_footing_square(body).model_dump()


class TakeoffRequest(BaseModel):
    elements: list[Element] = Field(..., max_length=500)


@v1.post("/quantity", tags=["calculators"])
def quantity_ep(body: TakeoffRequest, _=Depends(require("calc"))):
    return takeoff(body.elements)


class BBSRequest(BaseModel):
    marks: list[BarMark] = Field(..., max_length=2000)


@v1.post("/bbs", tags=["calculators"])
def bbs_ep(body: BBSRequest, _=Depends(require("calc"))):
    return bbs(body.marks)


@v1.get("/code-tables/status", tags=["calculators"])
def tables_ep(_=Depends(require("calc"))):
    return verify_all()


# ------------------------------------------------------------------ rates / estimates
@v1.get("/rates/{item_code}", tags=["estimation"])
def rate_ep(item_code: str, location: str = "delhi", _=Depends(require("estimate"))):
    return rates.sor_rate(item_code, location=location)


@v1.get("/rates", tags=["estimation"])
def rate_search_ep(q: str = "", unit: str | None = None, limit: int = 50, _=Depends(require("estimate"))):
    import re as _re
    q = q.strip()
    if _re.fullmatch(r"\d{1,2}(\.[\dA-Z]{1,3}){0,3}\.?", q):          # looks like an item code -> prefix search
        return docstore.search_sor_items(prefix=q.rstrip("."), unit=unit, limit=min(limit, 200))
    return docstore.search_sor_items(q or None, unit=unit, limit=min(limit, 200))


@v1.post("/rate-analysis/{template}", tags=["estimation"])
def rate_analysis_ep(template: str, allow_example: bool = False, _=Depends(require("estimate"))):
    return rates.rate_analysis(template, allow_example)


@v1.post("/estimate", tags=["estimation"])
@limiter.limit("30/minute")
def estimate_ep(request: Request, body: EstimateRequest, _=Depends(require("estimate"))):
    return build_estimate(body)


@v1.get("/estimate/{est_id}", tags=["estimation"])
def estimate_get(est_id: str, _=Depends(require("estimate"))):
    e = docstore.get_estimate(est_id)
    if not e:
        raise HTTPException(404, "estimate not found")
    return e


@v1.get("/estimate/{est_id}/export", tags=["estimation"])
def estimate_export(est_id: str, format: str = "xlsx", _=Depends(require("estimate"))):
    e = docstore.get_estimate(est_id)
    if not e:
        raise HTTPException(404, "estimate not found")
    if format == "xlsx":
        p = to_excel(e)
        mt = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format == "pdf":
        p, mt = to_pdf(e), "application/pdf"
    else:
        raise HTTPException(400, "format must be xlsx or pdf")
    return FileResponse(p, media_type=mt, filename=p.name)


# ------------------------------------------------------------------ admin
@v1.get("/admin/documents", tags=["admin"])
def docs_ep(_=Depends(require("admin"))):
    return docstore.list_documents()


@v1.get("/admin/review-queue", tags=["admin"])
def review_ep(_=Depends(require("admin"))):
    q = docstore.review_queue()
    return {k: len(v) for k, v in q.items()} | {"sor_items_sample": q["sor_items"][:50]}


@v1.post("/admin/ingest", tags=["admin"])
def ingest_ep(doc_id: str | None = None, reindex: bool = True, _=Depends(require("admin"))):
    from concrete_rag.pipeline import index, ingest
    rep = ingest([doc_id] if doc_id else None, log=lambda *a: None)
    n = index(log=lambda *a: None) if reindex else None
    return {"ingested": rep, "indexed": n}


app.include_router(v1)

# ------------------------------------------------------------------ web UI
from pathlib import Path  # noqa: E402

from fastapi.responses import RedirectResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from concrete_rag.api.ui_routes import router as ui_router  # noqa: E402

app.include_router(ui_router)
app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "ui", html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/")
