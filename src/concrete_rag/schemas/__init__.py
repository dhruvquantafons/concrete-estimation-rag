"""Shared pydantic models used across ingestion, retrieval, calculators and the API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ChunkType = Literal["clause", "table", "annex_clause", "amendment", "sor_item", "basic_rate", "sor_note", "text"]


class SourceDoc(BaseModel):
    doc_id: str
    path: str
    doc_type: Literal["is_code", "is_amendment", "sor"]
    code_no: str
    title: str
    revision_year: int
    part: str | None = None
    amendment_no: int | None = None
    amends: str | None = None
    amendments_bundled: list[int] = Field(default_factory=list)
    sor_name: str | None = None
    region: str | None = None
    cost_index_base: float | None = None
    ocr: Literal["auto", "force", "never"] = "auto"
    is_current: bool = True
    sha256: str | None = None

    @property
    def code_label(self) -> str:
        """Human label, e.g. 'IS 1200 (Part 2) : 2025'."""
        part = f" (Part {self.part})" if self.part else ""
        return f"{self.code_no}{part} : {self.revision_year}"


class Word(BaseModel):
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    conf: float = 100.0
    block: int = 0
    line: int = 0


class PageText(BaseModel):
    doc_id: str
    page: int                      # 1-based PDF page index
    method: Literal["text_layer", "ocr"]
    text: str                      # reading-order text
    alt_text: str = ""             # the *other* source (raw text layer when OCR was used) for cross-checks
    width: float = 0
    height: float = 0
    words: list[Word] = Field(default_factory=list)
    quality: float = 1.0           # 0..1 heuristic text quality
    mean_conf: float | None = None


class ChunkMetadata(BaseModel):
    doc_id: str
    code_no: str
    code_label: str
    doc_type: str
    revision_year: int
    part: str | None = None
    amendment_no: int | None = None
    section: str | None = None
    clause_no: str | None = None
    table_no: str | None = None
    annex: str | None = None
    title: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    applicability: list[str] = Field(default_factory=list)
    material_type: list[str] = Field(default_factory=list)
    work_item: list[str] = Field(default_factory=list)
    element_type: list[str] = Field(default_factory=list)
    unit: str | None = None
    rate: float | None = None
    currency: str | None = None
    region: str | None = None
    sor_year: int | None = None
    item_code: str | None = None
    page_start: int
    page_end: int
    depends_on: list[str] = Field(default_factory=list)   # normalized refs, e.g. "IS 456:cl:26.5.1"
    supersedes: list[str] = Field(default_factory=list)
    is_current: bool = True
    has_equation: bool = False
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    chunk_id: str
    chunk_type: ChunkType
    text: str                      # display / citation text
    embed_text: str                # text used for embeddings (includes heading context)
    metadata: ChunkMetadata
    table: dict[str, Any] | None = None   # {"columns": [...], "rows": [[...]], "notes": [...]}
    content_hash: str = ""


class SorItem(BaseModel):
    item_key: str                  # canonical, e.g. "CPWD-DSR-2023:5.1.2"
    sor_name: str
    sor_year: int
    doc_id: str
    code: str                      # e.g. "5.1.2"
    raw_code: str                  # as OCR'd
    sub_head: str | None = None    # e.g. "5.0 REINFORCED CEMENT CONCRETE"
    group_heading: str | None = None
    parent_code: str | None = None
    parent_description: str | None = None
    description: str
    full_description: str          # parent + own description
    unit: str | None = None
    unit_raw: str | None = None
    rate: float | None = None
    rate_raw: str | None = None
    page: int
    region: str | None = None
    min_conf: float | None = None
    rate_votes: int = 0            # how many independent OCR readings agree on the rate (max 3)
    rate_readings: list[str | None] = Field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class BasicRate(BaseModel):
    """CPWD 'Basic Rates' (01 hire charges, 02 labour, 03 materials) used by rate analysis."""
    key: str                       # "CPWD-DSR-2023:BR:0037"
    sor_name: str
    sor_year: int
    category: Literal["hire_charges", "labour", "materials", "unknown"]
    code: str
    description: str
    unit: str | None = None
    unit_raw: str | None = None
    rate: float | None = None
    rate_raw: str | None = None
    page: int
    doc_id: str
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    ref: str                       # "IS 456 : 2000, cl. 26.4.2" / "CPWD-DSR 2023, item 5.1.2"
    doc_id: str
    chunk_id: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    verified: bool | None = None


class RetrievedChunk(BaseModel):
    chunk_id: str
    chunk_type: str
    text: str
    metadata: dict[str, Any]
    score: float
    retrieval: list[str] = Field(default_factory=list)   # e.g. ["exact", "dense", "bm25", "rerank", "xref"]
    citation: Citation
    table: dict[str, Any] | None = None


class ContextPack(BaseModel):
    query: str
    chunks: list[RetrievedChunk]
    rate_items: list[dict[str, Any]] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def citations(self) -> list[Citation]:
        return [c.citation for c in self.chunks]
