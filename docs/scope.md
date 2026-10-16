# Scope & requirements (Step 1)

## Corpus (ingested)

| Source | Edition / amendments | Form | Use |
|---|---|---|---|
| IS 456 Plain & reinforced concrete | 2000 (reaffirmed 2005), Amendments 1–3 bound in | legacy scan, re-OCR'd | durability, design, detailing |
| IS 10262 Concrete mix proportioning | 2019 | digital | mix design |
| IS 1200 Method of measurement, Parts 1, 2, 5, 8, 22 | 2026 / 2025 / 2025 / 1993 (reaff. 2022, Amd 1) / 2025 | digital (+ a few OCR pages) | quantity take-off |
| IS 2386 Methods of test for aggregates, Parts 1–8 | 1963 + amendment slips (P3 Amd 1 2018, P7 Amd 3 2015, bound slips) | legacy scan, re-OCR'd | material testing |
| CPWD Delhi Schedule of Rates (Civil) Vol 1 & 2 | 2023 (w.e.f. 16.10.2023, corrections to 10.11.2023) | 96-dpi bilingual scan | item rates, basic rates, cement coefficients |

**Not yet in the corpus but referenced by the system:** IS 383 (aggregates), IS 1786 (bars), IS 269 (OPC),
SP 16 / SP 34 (design aids / detailing), IS 875 (loads), CPWD Delhi Analysis of Rates (DAR) 2023,
State PWD SORs, market rate feeds, historical project costs.

## Outputs

1. Citation-backed retrieval (clause / table / annex / amendment / SOR item level).
2. Concrete mix proportioning — IS 10262:2019 Section 2 with IS 456 durability limits.
3. RCC element design aids — beams (flexure, shear), one-way slabs, short axial columns, isolated footings (IS 456).
4. Quantity take-off — IS 1200 Parts 2/5/8 rules (rounding, deductions, formwork contact area, steel mass).
5. Rate lookup + location cost index; DAR-style rate analysis engine (templates to be filled from DAR).
6. BOQ, cost summary, cement consumption statement, indicative cost-head split; Excel/PDF export.
7. Evaluation: retrieval P/R/MRR/nDCG, generation faithfulness, calculator reproduction, estimate variance.

## Technology choices

| Concern | Choice | Why |
|---|---|---|
| PDF text / layout | PyMuPDF + own word-box column reconstruction | deterministic, fast; handles 2-column IS layout and full-width tables |
| OCR | Tesseract 5 (LSTM) on the **native** embedded image | measured: 23/23 DSR labour rates vs 3/19 when re-rendered at 300 dpi |
| Rate reliability | 3 independent readings (English OCR, digits-only crop OCR, Hindi page) + vote | ~95 % of DSR item rates confirmed by ≥2 readings; rest → review queue |
| Docstore | SQLAlchemy (SQLite default, Postgres via URL) | source of truth for chunks, rates, keys, audit, estimates |
| Vector DB | Qdrant (embedded local or server), named vectors dense + BM25-sparse (IDF modifier) | native hybrid, payload filters |
| Embeddings / rerank | BAAI/bge-m3, BAAI/bge-reranker-v2-m3 (local, MPS) | multilingual, strong on technical text, no data leaves the machine |
| LLM | provider interface: `fake` (offline), `anthropic` (Claude), `openai_compat` (Ollama/vLLM) | decision deferred |
| Numbers | deterministic Python calculators + curated, verified code tables | the LLM never does arithmetic or supplies code values |
| API | FastAPI, API keys with scopes, slowapi rate limit, audit log | secure service for downstream AI models |
| Estimation libs | pandas/NumPy-free core (Decimal, pint), openpyxl, reportlab | exact money arithmetic, live-formula Excel |

## Known limitations (by design, surfaced to users)

* IS 10262 Fig. 1 (w/c vs strength curves) cannot be extracted from text; w/c is an input until the curves are digitized.
* Curated IS 456 tables are `ocr_confirmed` / `partially_confirmed` until a human signs them off.
* Indicative cost-head split uses basic rates × mix quantities; a full labour/T&P split needs DAR 2023.
* Equations in scanned codes are kept as OCR text (flag `has_equation`), not LaTeX.
* RCC calculators are design aids (no slenderness/biaxial columns, two-way slabs, or deflection modification factors).
