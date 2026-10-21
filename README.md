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
  store/    docstore.py (SQLite/Postgres)  vector.py (Qdrant dense + BM25 sparse)  review.py (UI corrections)
  embed/    bge-m3 dense, domain-aware BM25 tokenizer
  retrieval/ router (exact refs) -> hybrid RRF -> cross-encoder rerank -> x-ref/amendment expansion
  calc/     mix_design (IS 10262), rcc (IS 456), steel (Ld, laps, BBS), quantities (IS 1200), rates
  estimate/ boq.py (BOQ, cement statement, cost heads)  export.py (Excel with formulas, PDF)
  llm/      provider interface: fake (offline) | anthropic (Claude) | openai_compat (Ollama/vLLM)
  agents/   tools for LLMs + cited answer generation with number-grounding verification
  api/      FastAPI app, API keys with scopes, rate limiting, audit log
  eval/     retrieval metrics, generation checks, calculator reproduction, estimate variance
  learn/    learning loop: feedback -> fusion weights, fine-tuned reranker, answer examples, estimate calibration
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
| CPWD DSR 2023 Vol 1+2 (804 pp, 96-dpi bilingual scan) | **2,949 items**, 2,437 basic rates (hire/labour/materials), 345 cement-consumption coefficients. Review queue cleared; 764 entries in `config/corrections/cpwd_dsr_2023.yaml`. A dry re-parse reproduces the database exactly |
| Index | 6,904 chunks (dense bge-m3 + BM25 sparse) in embedded Qdrant |

OCR quality controls:
- **Rates:** each DSR rate is read three times: English-page OCR, a digits-only re-OCR of the cell, and the
  matching Hindi page aligned over the whole volume. The readings are majority-voted. About 95% of item
  rates are confirmed by at least two readings. The rest are flagged `needs_review` and land in
  `data/processed/review_*.csv` and the UI review queue. All 712 flagged rows have been checked against the
  scanned pages (see step 3 below).
- **Item codes:** codes such as `414` → 4.1.4 or `5.224` → 5.2.2A are decoded as a sequence (Viterbi).
  Uncertain decodes are flagged. A few mis-split codes (e.g. 18.83.6 read as 18.8.3.6) are not flagged,
  because the rate and unit look fine (see step 3).
- **Units:** stored as canonical codes (`m3`, `10_nos`, `each_set` …) and always shown in the UI as printed
  in the DSR (cum, 10 Nos, each set …). A leading count ("10 Nos", "100 metre") is never dropped.
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
| DSR rates | browse/search 2,949 items by keyword or code prefix, OCR readings, source page | estimate |
| Review queue | flagged DSR items and basic rates next to the scanned page: correct the code, unit (shown as printed) or rate, then **Save** (or **Not an item — remove**). Writes `config/corrections/cpwd_dsr_2023.yaml` and applies it immediately | admin |
| Code tables | values used by the calculators, OCR cross-check result, **Compare with page**, sign-off button | calc (sign-off: admin) |
| Learning | ratings and corrections from users: **Approve** / **Discard**; live reranker and fusion versions, promotion history, estimate calibration | admin |

Every search result, answer and answer source has 👍 / 👎 buttons. 👎 on an answer asks for the answer an engineer
would have given. These ratings feed the learning loop (below).

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
| `POST /v1/feedback` | retrieve / generate | rate a result (`kind: chunk`) or an answer (`kind: answer`, optional `correction`) using the `query_id` returned by `/v1/retrieve` and `/v1/generate` |
| `/v1/admin/*` | admin | documents, review queue, re-ingest, `feedback` (approve / discard), `learning` (status) |

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
  FA 648.6 (648), CA 1234.6 (1234) kg/m³. An M25 mix, the demo BOQ (quantities and every DSR rate used)
  and the IS 456 tables were also checked by hand on 2026-10-20 (see step 3). All 29 unit tests pass. The 4
  integration tests need the search index, which `crag serve` locks, so stop the server before running them.
- **Code tables:** every IS 456 table and limit used by the calculators was checked value by value against the
  scanned pages on 2026-10-21 and signed off as `claude-vision`. All matched; the automatic OCR check had flagged
  them only because the scan's OCR misreads digits (e.g. Table 5 "Extreme 280" as 480). IS 10262 Fig. 1 is now
  digitized, so mix design no longer needs `w_c_ratio` as an input. The IS 10262 tables are `ocr_confirmed`.
- **Generation and estimate accuracy:** these are not measured yet. They need an LLM choice and actual
  project costs (see below).

## Learning loop (how the system keeps improving)

The system learns from use, on the reinforcement-learning principle of **action → feedback → improvement**.
Textbook RL (a model updating itself by trial and error) is not used: it has no safe place to practise, the
real reward (actual cost) arrives months later, and an estimating tool must stay auditable. Instead, each
improvement is **proposed from feedback, tested against the golden evaluation sets, and promoted only if it is
better**. Every version is kept and can be rolled back.

| What learns | From | How | Command |
|---|---|---|---|
| Hybrid search weighting (dense vs BM25) | 👍 sources + golden set | Tries 6 weightings on logged queries (an offline bandit) and keeps the best | `crag learn fusion` |
| Reranker (bge-reranker-v2-m3) | 👍 / 👎 sources | Fine-tuned from the base model on all feedback. 20% of rated queries are held out. Promoted only if it ranks better on those and the golden set does not get worse | `crag learn rerank` |
| Answers | Approved 👍 answers and engineers' corrections | Used as style examples when a similar question comes in. Numbers must still come from the sources (the number check flags anything copied from an example). Also exported as preference pairs for a later DPO fine-tune | automatic once approved; `crag learn export-preferences` |
| Steel kg/m³ thumb rules | Actual project costs | Blended with the default (the default counts as 3 projects). Used only after approval | `crag learn calibrate`, `crag learn approve-calibration` |

**Never learned:** IS code values, DSR rates and calculator formulas. These change only through the review
queue, `config/corrections/` and code-table sign-off.

Live versions are in `storage/learned/active.json`, and fine-tuned models in `storage/models/`. The server
picks up a promotion or rollback without a restart. `crag learn status` shows what is live, and
`crag learn rollback reranker|fusion` undoes the last promotion.

---

## Your Next Steps (Owner Actions)

The pipeline is built and runs on your data. These are the things only you can do or decide, in
priority order.

### 1. Environment (once)
- [ ] `brew install tesseract` (already present on this Mac), Python 3.12, then `make setup`.
- [ ] `cp .env.example .env`.
- [ ] Optional, for a server deployment: install/start Docker Desktop, then run `make up` (Qdrant server +
      Postgres + API) and set `CRAG_QDRANT_URL` / `CRAG_DOCSTORE_URL`.

### 2. LLM: local Qwen2.5-3B-Instruct (≈2 GB) via Ollama (configured)
`.env`: `CRAG_LLM_PROVIDER=openai_compat`, `CRAG_LLM_BASE_URL=http://localhost:11434/v1`,
`CRAG_LLM_MODEL=hf.co/bartowski/Qwen2.5-3B-Instruct-GGUF:Q4_K_M`. The GGUF comes from Hugging Face and
Ollama must be running.

Benchmark on this Mac (6 golden questions answered from the IS codes / DSR, plus 2 multi-step agent tasks):

| Model (4-bit GGUF) | Size | Correct fact | Numbers grounded | Avg time | Agent tasks |
|---|---|---|---|---|---|
| **Qwen2.5-3B-Instruct Q4_K_M** (selected) | 1.9 GB | **6/6** | **6/6** | **10 s** | 0/2 |
| Qwen3-4B-Instruct-2507 IQ3_M | 2.0 GB | 6/6 | 5/6 (cited cl. 5.1.8(a) for (b)) | 18 s | 0/2 (wrote tool calls as text) |
| Llama-3.2-3B-Instruct Q4_K_M | 2.0 GB | 4/6 (replied only "[S7]") | 6/6 | 9 s | 0/2 (hallucinated code) |
| Qwen2.5-7B-Instruct Q4_K_M (reference) | 4.7 GB | 6/6 | 6/6 | 17 s | 0/2 |

- [ ] **Use the LLM for Ask (cited answers).** Design, quantities and costing come from the deterministic
      calculator and estimate screens, which don't need the LLM.
- [ ] **No model in this size range completes the multi-step agent tasks** (they skip a requested rate or
      cite the wrong clause). Treat `/v1/agent` as experimental.
- [ ] Six questions is a small test. Add 20–30 questions to `eval/datasets/generation_golden.jsonl` and re-run
      `crag evaluate` before relying on the model.
- [ ] To switch models, change `CRAG_LLM_MODEL` and restart `crag serve`. For Qwen3 models on Ollama, also set
      `CRAG_LLM_REASONING_EFFORT=none`, otherwise their answers come back empty.

### 3. Review queue and data checks (queue cleared 2026-10-20)
The review queue is at **0 DSR items and 0 basic rates**. All 712 flagged rows were read against the scanned
pages; 709 were fixed and tagged `checked_by: claude-vision` in `config/corrections/cpwd_dsr_2023.yaml`,
and the rest were saved by you in the UI. Rows flagged by a future re-ingest appear in **Review queue**
(`/ui`, `admin` key).

**What to do:**
- [ ] **Restart `crag serve`.** The running server still has the old code loaded. Until it restarts, the UI and
      API give the old footing and deflection results and miss the new unit labels.
- [ ] **Spot-check the `claude-vision` corrections.** Start with the ones where the printed value differs
      from every OCR reading: `grep "OCR misread\|dropped" config/corrections/cpwd_dsr_2023.yaml`. Delete a
      line to undo a correction.
- [ ] **Add the rows OCR merged or dropped** (they aren't in the database, so they never reach the queue):
      DSR 1.2.4–1.2.15, 1.2.16.3/.4, 3.1, 3.11, 4.9A (₹900.15), 5.4 (₹8821.65), 6.11 (₹608.15 sqm),
      8.21, 9.147.F1.1 (₹12496.05), 11.41A.2.1, 11.41A.2.3, 11.54.1, 15.16.1, 17.59.1, 18.95A.10,
      15.15.1 (₹589.55 cum per metre span), 19.2.2, 19.10.2, 19.15.1, 19.17.1, 19.22.1, 21.16.3, 24.4, and
      basic rates 2226 (₹824.64) and 2228 (₹1413.67). The notes on neighbouring rows give the printed
      rates. Either fix the parser or add them by hand. (8.7.1, 11.6.1 and 7771 were recovered on 2026-10-21.)
- [ ] **Codes OCR read as letters (fixed 2026-10-21).** The scan sometimes prints a code's leading number as a
      letter ("n2" = 11.2, "a3" = 8.3, "TTT1" = 7771), so the row was glued onto the item above and **that item
      showed the next item's rate**, with all three readings agreeing, so it never reached the review queue. Fixed
      and checked against the pages: 11.1.2 (₹792.05 → **965.55**), 8.2.3.2 (₹298.60 → **3848.70**), 8.3.2
      (₹568.55 → **510.95**), 8.14.5.2 (₹714.40 → **3119.20**), basic rate 7773 paver block (₹10.00 → **450.00**);
      recovered 11.2, 11.6.1, 11.7, 8.3.1, 8.4, 8.7.1, 8.15, 9.71.1, basic rates 7771 and 7774. "n" = 11 is
      repaired automatically; ambiguous cases are listed under `code_tokens` in
      `config/corrections/cpwd_dsr_2023.yaml` (page + token → printed code). If you spot another item whose rate
      belongs to the next line, add an entry there and re-ingest.
- [ ] **209 child items still lack their parent description** (was 314), so search sees only their short tail
      (e.g. "1:6 (1 cement : 6 coarse sand)"). Rates are unaffected. Their heading rows were misread; fix them as found
      by adding a `description` to the item's correction.
- [ ] The cement-consumption coefficients page for sub-head 11 (V2 p.342) is poorly read ("11.1.2 = 612.85",
      skirting under code "16"). The BOQ uses coefficients only for concrete items (sub-heads 4/5), so estimates are
      not affected.
- [ ] **Percentage items have no ₹ rate on purpose:** 2.24.1 (+20%), 2.24.2 (+25% per metre depth) and
      5.9.14 (+20% of the shuttering item). The percentage is in the description and note. A BOQ line using
      one shows ₹0 with a warning, so add that extra by hand.
- [ ] **Re-check rate-analysis templates** that use count-unit basic rates. Before 2026-10-20 the parser
      stored "10 Nos"/"100 Nos" as "each", so 122 basic rates looked 10×/100× too high per piece ("1000
      litre", "100 metre", "pair" and "set" were affected too). This is fixed in `parse/sor.py` and the database.
      Bricks BR:1986 were stored per brick; they are per 1000 Nos (OCR read "74000 Nos").
- [ ] **Check item codes on rows that were never flagged.** OCR sometimes splits a code wrongly while the rate
      and unit look fine, so the row never reached the queue. Seven were found and fixed (18.83.6/7/9/11,
      18.80.4, 18.93A.13, 11.11.1). `18.5.0.1` (p.142, probably 18.50.1) is unconfirmed. A systematic pass
      comparing each page's code sequence is still to do.
- [ ] **Re-run RCC designs saved before 2026-10-20** (beams, slabs, footings), because of the fixes below.
- [ ] Run `crag index` once with `crag serve` stopped, so search results pick up the corrected units.
      Re-ingesting (`crag ingest --doc CPWD_DSR_2023_V1 --doc CPWD_DSR_2023_V2 && crag index`) re-applies all
      corrections and is fast because OCR is cached.

**What changed on 2026-10-20:**
- **Units are stored and shown as printed.** New units added to `normalize/units.py` and the review dropdown:
  - measured quantities: `100m`, `cm`, `cm_per_m`, `m_depth`, `m3_m_depth`, `cm_depth_100m`,
    `cm_depth_cm_width_m`, `m_cm_girth`, `m_per_m_height`, `m_per_m_span`, `m3_per_m_span`,
    `letter_cm_height`, `dm3` (cudm), `10_dm3` (10 cudm)
  - counts and packs: `each_set`, `one_set`, `set`, `10_sets`, `pair`, `10_nos`, `100_nos`, `750_ml`,
    `1000_l`, `roll`, `bundle`, `cartridge`
  - per occurrence: `each_job`, `each_cut`, `each_hole`, `joint`, `wheel`, `test`
- **Correction writer fixed.** Saving a row twice used to leave stray lines in the corrections file (making it
  invalid YAML), and it dropped an earlier code, unit, description or note fix. Entries are now one line each,
  and a re-save keeps the earlier fields. Junk rows excluded by code are removed in every copy.
- **Basic-rate codes can be corrected** (Review → Basic rates → Item code). OCR had misread 1543 as 1643
  (flush pipe, p.48), and 7692/7694 as 7682/7684 (K-9 flanged DI pipe, p.112). When OCR gives two rows the
  same code, the second row is stored and corrected as `BR:<code>#2`.
- **Calculator audit.** BOQ quantities, every DSR rate the estimator uses (4.1.8, 5.9.x, 5.22/5.22A,
  5.33.x) and the IS 10262 mix design were checked against the printed pages and a hand calculation, and
  are correct. Three fixes:
  - Above-plinth steel is now priced as **5.22A.6** instead of 5.22.6 (same rate, ₹107.85/kg).
  - Beam and slab **deflection checks** now apply the IS 456 Fig. 4/5 modification factors. They used to
    assume the maximum factor (2.0), which let failing sections pass.
  - **Footings under rectangular columns** now use the longer cantilever and the punching factor ks. The old
    code understated Mu by about 30% for a 230×600 column.
- Known over-measurement in the BOQ: slab soffit shuttering doesn't deduct the strip resting on beams
  (about 1–2% of formwork). Column height should be measured to the slab soffit, not floor-to-floor.

### 4. Sign off the curated code tables (reviewed 2026-10-21)
All IS 456 tables and limits used by the calculators were compared with the scanned pages, and every value
matched: Tables 5, 16, 19 (all 78 values), 20 and 18 (load factor), cl. 26.2.1.1 bond stress, the 38.1 xu,max/d
note, and the detailing limits (26.3.3, 26.5.1.1, 26.5.1.5, 26.5.1.6, 26.5.2.1/2, 26.5.3.1, 23.2.1, 25.4, 39.3).
They show as **Signed off by claude-vision** on the Code tables page.
- [ ] **Spot-check two or three of them yourself** with **Compare with page**, and if you like, re-sign them in
      your own name (set `verified_by` in `config/code_tables/is456_2000.yaml`).
- [ ] **IS 10262 Fig. 1 was digitized from the PDF image** (`fig_1_wc_vs_strength` in
      `config/code_tables/is10262_2019.yaml`, accurate to about ±0.3 N/mm²). Mix design now picks w/c from it
      when you leave `w_c_ratio` empty. It follows the drawn curve, which gives slightly lower w/c than the
      standard's rounded worked examples (0.35 against 0.36 for 48.25 N/mm² on curve 2). Durability limits from
      IS 456 Table 5 still cap the result, and a target outside the curve warns. Check a few points against your
      printed copy of the figure. For PPC/PSC the calculator uses curve 2, as Fig. 1 Note 2 allows; if your
      cement's tested strength is known, choose the matching cement type.

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

### 7. Run the learning loop (added 2026-10-21)
Nothing has been learned yet. The loop needs your engineers' feedback first.
- [ ] **Restart `crag serve`** to load the 👍 / 👎 buttons and the **Learning** page (Account menu → Learning).
- [ ] **Ask engineers to rate results while they work.** 👍 the source that answered the question, 👎 the
      misleading ones, and 👎 + a correction on wrong answers. About 30 rated questions are needed before the first
      reranker training. More is better, and a spread across IS 456, 10262, 1200, 2386 and DSR helps most.
- [ ] **Review feedback weekly** on the Learning page. **Approve** good answers and corrections (approved
      answers become examples for similar questions). **Discard** careless or wrong ratings.
- [ ] **Monthly, with `crag serve` stopped** (the embedded index allows one process):
      `crag learn fusion`, then `crag learn rerank`. Each prints before/after scores and promotes only if
      better. Training the reranker takes a few minutes on the M4. Then run `crag evaluate` and restart `crag serve`.
- [ ] **When projects finish:** add their actual costs to `eval/datasets/actual_costs.csv`. For the
      reinforcement rows, fill `element_type` (column, beam, slab, footing …) and `concrete_m3` (the concrete in
      those elements). Then run `crag learn calibrate`, check `config/cost/learned_adjustments.yaml`, and
      `crag learn approve-calibration --by <name>`.
- [ ] **Later (optional):** once there are a few hundred approved corrections, `crag learn export-preferences`
      writes `data/processed/preferences.jsonl` for a DPO fine-tune of the local Qwen model. Ask me when you
      get there.
- [ ] If a promotion makes results worse in practice: `crag learn rollback reranker` (or `fusion`).

### 8. Go-live checklist
- [ ] Create one API key per consumer, with least-privilege scopes: `crag apikey create <name> --scopes ...`.
      Revoke keys with `crag apikey revoke <name>`.
- [ ] Leave `CRAG_API_BOOTSTRAP_ADMIN_KEY` empty in production. Set `CRAG_API_CORS_ORIGINS` and put the API
      behind HTTPS (a reverse proxy).
- [ ] Back up `storage/` (or the Postgres and Qdrant volumes; this includes the feedback, `storage/learned/` and
      `storage/models/`), `config/` and `data/processed/`.
- [ ] Licensing: the IS PDFs are BIS-licensed and carry purchaser watermarks, which the pipeline strips from
      chunks. Keep `data/` out of git (already in `.gitignore`), and make sure API consumers are entitled
      to the excerpts.

### 9. Decisions I need from you
1. ~~Which LLM~~: decided, local Qwen2.5-3B, about 2 GB (see step 2). The agent workflow is not reliable at this size.
2. Which State PWD SORs, and which years?
3. Default contingency % (currently 3%), and whether estimates should add any extra overhead on top of DSR
   rates (DSR item rates already include CP&OH 15% and GST 18%).
4. Default reinforcement densities in `config/cost/estimation_defaults.yaml`, or require a BBS for every
   estimate.
5. Nominal-mix vs design-mix as the default for estimates (currently design mix, DSR 5.33.x).
