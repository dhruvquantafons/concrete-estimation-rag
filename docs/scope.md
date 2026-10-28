# Scope & requirements (Step 1)

## Corpus (ingested)

| Source | Edition / amendments | Form | Use |
|---|---|---|---|
| IS 456 Plain & reinforced concrete | 2000 (reaffirmed 2005), Amendments 1–3 bound in | legacy scan, re-OCR'd | durability, design, detailing |
| IS 10262 Concrete mix proportioning | 2019 | digital | mix design |
| IS 1200 Method of measurement, Parts 1, 2, 5, 8, 22 | 2026 / 2025 / 2025 / 1993 (reaff. 2022, Amd 1) / 2025 | digital (+ a few OCR pages) | quantity take-off |
| IS 2386 Methods of test for aggregates, Parts 1–8 | 1963 + amendment slips (P3 Amd 1 2018, P7 Amd 3 2015, bound slips) | legacy scan, re-OCR'd | material testing |
| CPWD Delhi Schedule of Rates (Civil) Vol 1 & 2 | 2023 (w.e.f. 16.10.2023, corrections to 10.11.2023) | 96-dpi bilingual scan | item rates, basic rates, cement coefficients |
| CPWD Delhi Analysis of Rates (Civil) Vol I & II | 2023 (14.09.2023, all correction slips) | digital; Vol II sub-head 17 image-only (OCR'd) | 2,989 item analyses (material / labour / hire rows, build-up), 2,472 basic rates; cross-check of DSR rates |

**Not yet in the corpus but referenced by the system:** IS 383 (aggregates), IS 1786 (bars), IS 269 (OPC),
SP 16 / SP 34 (design aids / detailing), IS 875 (loads),
State PWD SORs, market rate feeds, historical project costs.

## Outputs

1. Citation-backed retrieval (clause / table / annex / amendment / SOR item level).
2. Concrete mix proportioning — IS 10262:2019 Section 2 with IS 456 durability limits.
3. RCC element design aids (labelled **Design aid only** in the API and UI) — beams (flexure, shear), one-way slabs, short axial columns, isolated footings (IS 456).
4. Quantity take-off — IS 1200 Parts 2/5/8 rules (rounding, deductions, formwork contact area, steel mass).
5. Rate lookup + location cost index; the CPWD DAR analysis of every item (cost-head split, re-pricing with other
   basic rates such as market rates); DAR-style rate analysis for work with no DAR analysis.
6. BOQ, cost summary, cement consumption statement, indicative cost-head split; Excel/PDF export.
7. Evaluation: retrieval P/R/MRR/nDCG, generation faithfulness, calculator reproduction, estimate variance.

## Technology choices

| Concern | Choice | Why |
|---|---|---|
| PDF text / layout | PyMuPDF + own word-box column reconstruction | deterministic, fast; handles 2-column IS layout and full-width tables |
| OCR | Tesseract 5 (LSTM) on the **native** embedded image | measured: 23/23 DSR labour rates vs 3/19 when re-rendered at 300 dpi |
| Rate reliability | clean copy of the DSR used; scan (English OCR + digits-only crop OCR) and DAR as cross-checks | every rate checked code by code; unconfirmed differences and scan-only rows → review queue |
| Docstore | SQLAlchemy (SQLite default, Postgres via URL) | source of truth for chunks, rates, keys, audit, estimates |
| Vector DB | Qdrant (embedded local or server), named vectors dense + BM25-sparse (IDF modifier) | native hybrid, payload filters |
| Embeddings / rerank | BAAI/bge-m3, BAAI/bge-reranker-v2-m3 (local, MPS) | multilingual, strong on technical text, no data leaves the machine |
| LLM | provider interface: `fake` (offline), `openai_compat` (Ollama/vLLM) | local Qwen2.5-3B via Ollama; no data leaves the machine |
| Numbers | deterministic Python calculators + curated, verified code tables | the LLM never does arithmetic or supplies code values |
| API | FastAPI, API keys with scopes, slowapi rate limit, audit log | secure service for downstream AI models |
| Estimation libs | pandas/NumPy-free core (Decimal, pint), openpyxl, reportlab | exact money arithmetic, live-formula Excel |

## Known limitations (by design, surfaced to users)

* IS 10262 Fig. 1 (w/c vs strength curves) cannot be extracted from text; w/c is an input until the curves are digitized.
* Curated code tables are `ai_checked` (compared with the page by AI vision) until a person signs them off.
* Cost heads come from DAR 2023 (Delhi). At other locations the cost index scales every head alike until local
  basic / market rates are supplied for re-pricing. Delhi PWD works on the CPWD DSR, so there is no separate
  Delhi State PWD SOR.
* Equations in scanned codes: the OCR text is kept, and 46 key equations (IS 456, IS 2386) read from the pages are
  added beside it from `config/equations/` (plain text + LaTeX). Others remain OCR text only.
* RCC calculators are design aids only (no slender or biaxially loaded columns, two-way slabs, or flanged beams).
  Deflection uses the span/depth ratios with the Fig. 4/5 modification factors (cl. 23.2.1), not a calculated deflection.
