"""Shared pydantic models used across ingestion, retrieval, calculators and the API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ChunkType = Literal["clause", "table", "annex_clause", "amendment", "sor_item", "basic_rate", "sor_note", "dar_analysis",
                    "text"]


class SourceDoc(BaseModel):
    doc_id: str
    path: str
    doc_type: Literal["is_code", "is_amendment", "sor", "dar", "sor_reference"]
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
    reference_for: str | None = None   # sor_reference: the SOR doc_id this clean (earlier) printing cross-checks
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
    equations: list[str] = Field(default_factory=list)   # curated equation ids (config/equations)
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
    revision: str | None = None    # "w.e.f. 16.10.2023, corrections to 10.11.2023"
    source: str = "scan"           # scan | scan+clean (agree) | clean (only in the clean copy) | clean_over_scan (differ:
                                   # the clean copy's rate is used, owner's decision 2026-10-28)
    clean_rate: float | None = None     # rate in the clean, earlier printing (data/CPWD SOR HQ)
    scan_rate: float | None = None      # the scan's rate where it differs from the clean copy
    clean_doc_id: str | None = None
    clean_page: int | None = None
    material: list[str] = Field(default_factory=list)   # material tags (config/vocab/enrichment.yaml)
    min_conf: float | None = None
    rate_votes: int = 0            # independent readings that agree on the rate (3 OCR readings + the clean copy)
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
    region: str | None = None
    revision: str | None = None
    source: str = "scan"                # scan | scan+clean | clean | clean_over_scan, as for SorItem
    scan_rate: float | None = None      # the scan's rate where it differs from the clean copy
    clean_doc_id: str | None = None
    clean_page: int | None = None
    needs_review: bool = False
    review_reasons: list[str] = Field(default_factory=list)


class DarLine(BaseModel):
    """One row of a CPWD DAR analysis: a basic rate (material / labour / hire), a sub-analysis
    ("Rate as per Item Number 3.8"), sundries (9999) or an uncoded extra ("= 100% of P")."""
    code: str | None = None
    description: str
    unit: str | None = None
    unit_raw: str | None = None
    quantity: float | None = None
    rate: float | None = None
    amount: float
    group: str | None = None            # heading above the row in the analysis: MATERIAL / LABOUR / ...
    category: Literal["material", "labour", "equipment", "carriage", "sundries", "sub_analysis", "other"] = "other"
    marker: str | None = None           # letter after the amount (e.g. "P"), referred to by later rows
    note: str | None = None             # e.g. "(0.70 cum - 7.5% for voids i.e. 0.05 = 0.65 cum)"


class DarAnalysis(BaseModel):
    """CPWD Delhi Analysis of Rates: the build-up of one DSR item rate from basic rates."""
    key: str                            # "CPWD-DAR-2023:5.33.2.1"
    sor_name: str
    sor_year: int
    doc_id: str
    code: str
    description: str
    full_description: str
    parent_code: str | None = None
    sub_head: str | None = None
    per_quantity: float = 1.0           # "Details of cost for 10 sqm" -> 10
    per_unit: str | None = None
    unit: str | None = None             # unit of the final rate ("Cost of 1.00 sqm" / "Say")
    unit_raw: str | None = None
    lines: list[DarLine] = Field(default_factory=list)
    steps: list[dict[str, Any]] = Field(default_factory=list)   # TOTAL W, Add water, ..., Say: {label, amount, marker}
    base_total: float | None = None     # W = sum of lines
    water_percent: float | None = None
    gst_factor: float | None = None
    cpoh_percent: float | None = None
    cess_percent: float | None = None
    rate: float | None = None           # "Say" = the DSR item rate
    region: str | None = None
    revision: str | None = None
    page_start: int
    page_end: int
    dsr_rate: float | None = None       # DSR item rate parsed from the DSR scans, for cross-checking
    dsr_match: bool | None = None
    not_for_dsr: bool = False           # a reviewer found this analysis is not the one behind the DSR item
    checked_by: str | None = None
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
