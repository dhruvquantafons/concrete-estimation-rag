# Concrete Works RAG & Estimation

Retrieval-Augmented Generation over **Indian Standards** (IS 456, IS 10262, IS 1200, IS 2386) and the
**CPWD Delhi Schedule of Rates 2023**. It also provides **deterministic, IS-compliant calculators** for mix
design, RCC design, quantity take-off and cost estimation. A secure REST API serves all of it to other AI
models and applications.

> **Core rule:** the LLM never does arithmetic and never supplies code values or rates. Every number comes
> from a cited source chunk, a curated code table, or a unit-tested calculator. Every OCR-derived value
> carries a verification status.

---

## What's in the box

```
config/
  sources.yaml            registry of every source document (edition, amendments, OCR policy)
  sor/cpwd_dsr_2023.yaml  DSR structure (sub-heads) used by the SOR parser
  code_tables/*.yaml      curated IS 456 / IS 10262 values used by calculators (+ verification status)
  cost/*.yaml             BOQ -> DSR item mapping, cost indices, rate-analysis settings, estimation defaults
  corrections/*.yaml      human corrections to OCR'd SOR rows (applied on re-ingest)
  vocab/enrichment.yaml   controlled vocabularies for metadata (material, work item, element, applicability)
src/concrete_rag/
  ingest/   pages.py (text layer vs OCR, native-image OCR, cache)  layout.py (2-column reading order)
  parse/    is_code.py (clauses, tables, annexes, amendments, x-refs)  sor.py (DSR items, basic rates, coefficients)
  normalize/ units, currency (₹, lakh/crore), SOR item-code OCR repair (Viterbi)
  chunking/ clause/table/item chunks + metadata enrichment
  store/    docstore.py (SQLite/Postgres)  vector.py (Qdrant dense + BM25 sparse)
  embed/    bge-m3 dense, domain-aware BM25 tokenizer
  retrieval/ router (exact refs) -> hybrid RRF -> cross-encoder rerank -> x-ref/amendment expansion
  calc/     mix_design (IS 10262), rcc (IS 456), steel (Ld, laps, BBS), quantities (IS 1200), rates
  estimate/ boq.py (BOQ, cement statement, cost heads)  export.py (Excel with formulas, PDF)
  llm/      provider interface: fake (offline) | anthropic (Claude) | openai_compat (Ollama/vLLM)
  agents/   tools for LLMs + cited answer generation with number-grounding verification
  api/      FastAPI app, API keys with scopes, rate limiting, audit log
  eval/     retrieval metrics, generation checks, calculator reproduction, estimate variance
eval/datasets/  retrieval_golden.jsonl (50 queries), generation_golden.jsonl, actual_costs_template.csv
docs/scope.md   Step 1 scope, corpus, tech-stack decisions and known limitations
```

## Current state of the data (as ingested)

| Document | Result |
|---|---|
| IS 456 : 2000 (114 pp, legacy scan → re-OCR) | 441 clauses, 67 annex clauses, 28 tables, 21 amendment entries (Amd 1–3) |
| IS 10262 : 2019 (digital) | 77 clauses, 84 annex clauses (worked examples A–F), 15 tables |
| IS 1200 Parts 1, 2, 5, 8, 22 | 207 clauses (+ Part 8 Amendment 1) |
| IS 2386 Parts 1–8 + 2 amendment slips | 447 clauses, 15 tables, 29 amendment entries |
| CPWD DSR 2023 Vol 1+2 (804 pp, 96-dpi bilingual scan) | **2,955 items**, 2,444 basic rates (hire/labour/materials), 345 cement-consumption coefficients |
| Index | 6,904 chunks (dense bge-m3 + BM25 sparse) in embedded Qdrant |

OCR quality controls:
- **Rates:** each DSR rate is read three times: English-page OCR, a digits-only re-OCR of the cell, and the
  matching Hindi page aligned over the whole volume. The readings are majority-voted. About 95% of item
  rates are confirmed by at least two readings. The rest are flagged `needs_review` and land in
  `data/processed/review_*.csv`.
- **Item codes:** codes such as `414` → 4.1.4 or `5.224` → 5.2.2A are decoded as a sequence (Viterbi).
  Uncertain decodes are flagged.
- **Curated code tables:** each table is cross-checked against the OCR text (`crag verify-tables`). For
  example, IS 456 Table 5 "Extreme" OCRs as 480 instead of 280, and is reported as a mismatch.

## Quick start

```bash
make setup                 # venv + deps (Python 3.12, Tesseract must be installed: brew install tesseract)
cp .env.example .env
crag ingest                # OCR (cached) + parse + chunk + docstore + review CSVs   (~10 min first time)
crag index                 # embeddings + hybrid index                                (~4 min on Apple M4)
crag search "nominal cover for severe exposure"
crag apikey create my-name --scopes retrieve,generate,calc,estimate,admin
crag serve                 # web UI: http://127.0.0.1:8000/ui   ·   API docs: /docs
pytest -q                  # unit + integration tests
crag evaluate              # writes eval/reports/report_<ts>.md
```

> The embedded Qdrant store allows **one process at a time**. Stop `crag serve` before running
> `crag ingest/index` from another terminal, or run a Qdrant server (`make up`, `CRAG_QDRANT_URL`).

### Web UI (`http://127.0.0.1:8000/ui`)

Served by the same server, with nothing extra to install. Sign in with an API key. Each screen appears only
if your key has the scope it needs.

| Screen | What it does | Scope |
|---|---|---|
| Dashboard | corpus stats, review-queue size, code-table status, recent estimates | any |
| Search & Ask | hybrid search with citations; **View page** opens the original scanned page; Ask = cited answer | retrieve / generate |
| Mix design | IS 10262 form → kg/m³ (SSD and field), compliance checks, step-by-step working with clause refs | calc |
| RCC design | beam / one-way slab / column / footing forms → bars, checks, working | calc |
| Estimate / BOQ | editable element table → IS 1200 quantities → DSR-priced BOQ, cement statement, cost heads, Excel/PDF/print, saved estimates | estimate |
| DSR rates | browse/search 2,955 items by keyword or code prefix, OCR readings, source page | estimate |
| Review queue | flagged DSR rows next to the scanned page: click an OCR reading or type the value, then **Save** (or **Not an item — remove**). Writes `config/corrections/cpwd_dsr_2023.yaml` and applies it immediately | admin |
| Code tables | values used by the calculators, OCR cross-check result, **Compare with page**, sign-off button | calc (sign-off: admin) |

Corrections made in the UI update the database and the search results straight away. The embeddings are
refreshed on the next `crag index`.

### API overview (`X-API-Key` header)

| Endpoint | Scope | Purpose |
|---|---|---|
| `POST /v1/retrieve` | retrieve | hybrid search → cited chunks, tables (JSON), SOR rate items |
| `POST /v1/generate` | generate | cited answer with `[S#]` sources + number-grounding verification |
| `POST /v1/agent` | generate | LLM with tools (retrieval + calculators + estimate) |
| `POST /v1/mix-design` | calc | IS 10262 mix proportioning |
| `POST /v1/design/{beam,slab,column,footing}` | calc | IS 456 design aids with checks and clause refs |
| `POST /v1/quantity`, `POST /v1/bbs` | calc | IS 1200 take-off, bar bending schedule |
| `GET /v1/rates/{code}?location=` | estimate | DSR rate × cost index, citation, OCR check status |
| `POST /v1/estimate`, `GET /v1/estimate/{id}/export?format=xlsx\|pdf` | estimate | BOQ + cost summary + exports |
| `GET /v1/code-tables/status` | calc | verification status of curated tables |
| `/v1/admin/*` | admin | documents, review queue, re-ingest |

Example:
```bash
curl -s -X POST localhost:8000/v1/estimate -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "project": "Block A frame", "location": "delhi", "mix_type": "design_mix",
  "elements": [
    {"id":"F1","type":"footing","length_m":1.6,"width_m":1.6,"depth_m":0.45,"count":8,"level":"below_plinth","grade":"M25"},
    {"id":"C1","type":"column","length_m":3.2,"width_m":0.30,"depth_m":0.45,"count":8,"grade":"M25"},
    {"id":"B1","type":"beam","length_m":4.55,"width_m":0.23,"depth_m":0.33,"count":10,"grade":"M25"},
    {"id":"S1","type":"slab","length_m":10,"width_m":8,"depth_m":0.125,"grade":"M25"}]}'
```

## Evaluation (latest run)

Run `crag evaluate`. The full report is written to `eval/reports/`. Golden set: 50 queries written
against the real corpus (IS 456/10262/1200/2386 + DSR), k = 8.

| config | P@8 | Recall@8 | Hit@8 | MRR | nDCG@8 | s/query (M4) |
|---|---|---|---|---|---|---|
| BM25 only | 0.275 | 0.92 | 0.94 | 0.800 | 0.825 | 0.08 |
| dense only (bge-m3) | 0.243 | 0.93 | 0.96 | 0.790 | 0.813 | 0.25 |
| hybrid (RRF) | 0.258 | 0.97 | 1.00 | 0.807 | 0.840 | 0.13 |
| **hybrid + rerank** (default) | 0.258 | **0.97** | **1.00** | **0.823** | **0.861** | 1.9 |

P@8 is low by construction, because most queries have only 1–2 relevant chunks among the 8 returned.
To trade a little ranking quality for speed, set `CRAG_RERANK_ENABLED=false` (0.13 s/query) or lower
`CRAG_RERANK_POOL`.

- **Calculators:** the IS 10262:2019 Annex A worked example is reproduced: cement 412, water 148,
  FA 648.6 (648), CA 1234.6 (1234) kg/m³. All 19 tests pass.
- **Code tables:** the IS 10262 tables and IS 456 Table 16 are `ocr_confirmed`. IS 456 Tables 5 and 19 are
  `partially_confirmed`; the source scan misreads, e.g. Table 5 "Extreme 280" as 480. Table 20, bond stress
  and the detailing limits await human sign-off.
- **Generation and estimate accuracy:** these are not measured yet. They need an LLM choice and actual
  project costs (see below).

---

## Your Next Steps (Owner Actions)

The pipeline is built and runs on your data. These are the things only you can do or decide, in
priority order.

### 1. Environment (once)
- [ ] `brew install tesseract` (already present on this Mac), Python 3.12, then `make setup`.
- [ ] `cp .env.example .env`.
- [ ] Optional, for a server deployment: install/start Docker Desktop, then run `make up` (Qdrant server +
      Postgres + API) and set `CRAG_QDRANT_URL` / `CRAG_DOCSTORE_URL`.

### 2. LLM: local Qwen2.5-7B-Instruct via Ollama (configured)
`.env` is set to `CRAG_LLM_PROVIDER=openai_compat`, `CRAG_LLM_BASE_URL=http://localhost:11434/v1`,
`CRAG_LLM_MODEL=hf.co/bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M`. The model comes from Hugging Face; Qwen's
official Q4 file is sharded, which Ollama can't pull, so this uses the single-file conversion of the same
weights. Ollama must be running (it starts with the app).

Measured on this Mac (6 golden questions, cited **Ask** answers):

| Model | Correct key fact | Cites sources | Avg time |
|---|---|---|---|
| **Qwen2.5-7B-Instruct Q4** | **6/6** | 6/6 | ~10 s (first answer ~25 s) |
| llama3 8B (already installed) | 5/6 (gave the 0.4 m² formwork rule for concrete openings) | 6/6 | ~10 s |

- [ ] **Use Qwen 7B for Ask (cited answers): it is reliable there.**
- [ ] **Don't rely on `/v1/agent` (multi-step tool use) with a 7B model.** In tests it skipped a requested DSR
      rate and once cited the wrong clause (26.2.1.1 instead of 26.2.5.1), although the numbers were right. For
      design and costing, use the calculator and estimate screens directly; they are deterministic and don't need
      the LLM. If you want the agent, try a 14B model (`ollama pull hf.co/bartowski/Qwen2.5-14B-Instruct-GGUF:Q4_K_M`,
      ~9 GB) and compare it with `crag evaluate`.
- [ ] To switch models, change `CRAG_LLM_MODEL` in `.env` and restart `crag serve`.

### 3. Clear the review queue (most important for estimate accuracy)
**Easiest:** open **Review queue** in the web UI (`/ui`, needs an `admin` key). Each flagged row is shown
next to its scanned page; type or click the correct value and press Enter. Use **Not an item — remove**
for OCR junk rows such as table headers. Filter by sub-head 4 and 5 (concrete, RCC) first.

Alternatively, work from the CSV files in `data/processed/`:
- [ ] `review_sor_items.csv` (~540 rows): DSR items whose rate readings disagreed or whose code decode was
      uncertain. Check each one against the printed DSR page (the `pdf_page` column), then add the fix to
      `config/corrections/cpwd_dsr_2023.yaml`:
      `"CPWD-DSR-2023:5.5": {rate: 12205.85, checked_by: "<name>"}`.
      Prioritise sub-heads 4 (Concrete), 5 (RCC) and 2 (Earth work), which the estimator uses most.
- [ ] `review_basic_rates.csv` (~210 rows): labour/material/hire basic rates.
- [ ] Confirm the one inferred correction already in the corrections file (5.33.2.4 = 10313.30).
- [ ] Re-run `crag ingest --doc CPWD_DSR_2023_V1 --doc CPWD_DSR_2023_V2 && crag index`. OCR is cached, so this is fast.

### 4. Sign off the curated code tables
- [ ] In the UI, open **Code tables**, click **Compare with page** for each table, and if the values match
      your licensed copy, click **sign off**. Or run `crag verify-tables`, and for each table in `config/code_tables/is456_2000.yaml` and
      `is10262_2019.yaml`, compare the values with your licensed copy, then fill in `verified_by` and
      `verified_on`. Mismatches listed by the command are usually OCR errors in the source scan, but check them.
- [ ] **Digitize IS 10262 Fig. 1** (free w/c vs 28-day strength, curves 1/2/3) into
      `fig_1_wc_vs_strength.curves` as `[[strength, w/c], ...]` points. Until then, mix design needs
      `w_c_ratio` as an input.

### 5. Add the data that is still missing
Add each file under `data/` and register it in `config/sources.yaml` (copy an existing entry and set
`doc_type`, `revision_year`, `ocr`). Then run `crag ingest --doc <id> && crag index`.
- [ ] **CPWD DAR 2023** (Delhi Analysis of Rates). Without it the labour/T&P split is indicative. Fill
      `config/cost/rate_analysis.yaml` templates from it, and confirm the water, CP&OH and GST percentages
      (`verified_by`).
- [ ] **State PWD SORs** for the states you price in. A new SOR layout needs a `config/sor/<name>_<year>.yaml`
      (sub-head list) and may need parser tuning. Ping me with a sample.
- [ ] **Location cost indices** (CPWD OMs) in `config/cost/cost_indices.yaml`. Only Delhi = 100 is set now,
      and other locations are refused rather than guessed.
- [ ] Other referenced codes: IS 383, IS 1786, IS 269/IS 1489, SP 16, SP 34, IS 875 (Parts 1–2).
- [ ] Market-rate sheets (Excel/CSV), if you want rates other than DSR.

### 6. Build the real evaluation sets
- [ ] Extend `eval/datasets/retrieval_golden.jsonl` from 50 to ~150 queries written by your engineers.
      Each target is a reference, e.g. `{"code_no": "IS 456", "clause_no": "26.5.1.1"}`, not a chunk id.
- [ ] Add 20–30 Q&A items with expected key facts to `generation_golden.jsonl`.
- [ ] Add worked examples you trust (e.g. SP 16 design examples, your past mix designs) as unit tests in
      `tests/unit/test_calculators.py`.
- [ ] **Actual project costs:** copy `eval/datasets/actual_costs_template.csv` to `actual_costs.csv`,
      with one row per BOQ item (estimated vs actual qty and amount). `crag evaluate` then reports
      variance and MAPE.

### 7. Go-live checklist
- [ ] Create one API key per consumer, with least-privilege scopes: `crag apikey create <name> --scopes ...`.
      Revoke keys with `crag apikey revoke <name>`.
- [ ] Leave `CRAG_API_BOOTSTRAP_ADMIN_KEY` empty in production. Set `CRAG_API_CORS_ORIGINS` and put the API
      behind HTTPS (a reverse proxy).
- [ ] Back up `storage/` (or the Postgres and Qdrant volumes), `config/` and `data/processed/`.
- [ ] Licensing: the IS PDFs are BIS-licensed and carry purchaser watermarks, which the pipeline strips from
      chunks. Keep `data/` out of git (already in `.gitignore`), and make sure API consumers are entitled
      to the excerpts.

### 8. Decisions I need from you
1. ~~Which LLM~~: decided, local Qwen2.5-7B (see step 2). Decide whether the agent workflow is needed (it needs a 14B+ model).
2. Which State PWD SORs, and which years?
3. Default contingency % (currently 3%), and whether estimates should add any extra overhead on top of DSR
   rates (DSR item rates already include CP&OH 15% and GST 18%).
4. Default reinforcement densities in `config/cost/estimation_defaults.yaml`, or require a BBS for every
   estimate.
5. Nominal-mix vs design-mix as the default for estimates (currently design mix, DSR 5.33.x).
