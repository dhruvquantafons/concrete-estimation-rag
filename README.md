# Concrete Estimator

A tool for concrete works in India. It does three things:

1. **Finds answers in the standards.** Search IS 456, IS 10262, IS 1200, IS 2386 and the CPWD Delhi Schedule of
   Rates (DSR) 2023. Every answer shows where it came from, down to the clause and the scanned page.
2. **Does the engineering calculations.** Mix design, RCC design aids (beam, slab, column, footing), quantities and
   bar bending schedules, following the IS codes.
3. **Prepares cost estimates.** Turns a list of building elements into a priced BOQ using DSR rates, with Excel
   and PDF export.

It runs on your own computer. Other software can use it through a secure API.

> **The one rule:** the AI model never calculates and never makes up a code value or a rate. Every number comes
> from the standard itself, from a checked table, or from a tested calculator.

---

## Start it

**First time only** (needs Python 3.12 and Tesseract: `brew install tesseract`):

```bash
make setup                     # install
cp .env.example .env           # settings; to turn on the AI answers, see "LLM choice" in docs/technical.md
crag ingest                    # read all PDFs        (~10 min the first time)
crag index                     # build search          (~4 min)
crag apikey create your-name --scopes retrieve,generate,calc,estimate,admin
```

Keep the key it prints. You sign in with it.

**Every day:**

```bash
crag serve                     # then open http://127.0.0.1:8000/ui and sign in with your key
```

For **Ask** (written answers), [Ollama](https://ollama.com) must also be running. Everything else works without it.

---

## What you can do in the web app

| Screen | Use it to |
|---|---|
| **Search & Ask** | Search the standards and the DSR. **View page** opens the original scan. **Ask** writes a short answer with sources [S1], [S2]… |
| **Mix design** | Get kg/m³ of cement, water and aggregates (IS 10262), with every step and clause shown |
| **RCC design** *(design aid only)* | Size a beam, one-way slab, column or footing (IS 456) and see which checks pass. Not a checked structural design: a qualified structural engineer must review it |
| **Estimate / BOQ** | Enter elements → quantities (IS 1200) → priced BOQ, cement statement, Excel / PDF |
| **DSR rates** | Look up any of the 2,955 DSR items by word or code |
| **Code tables** | See the IS values the calculators use, compare them with the scan, sign them off |
| **Review queue** *(admin)* | Fix DSR rows the scanner was unsure about, next to the scanned page |
| **Learning** *(admin)* | Approve or discard ratings, and see what the system has learned |

What you see depends on your key's permissions (scopes).

---

## How it works

There are two stages. **Preparing** happens once (and again whenever a document changes). **Using** happens
every time someone searches, designs or estimates.

```
PREPARING (crag ingest, crag index)

 PDFs ─▶ read text ─▶ fix reading order ─▶ split into clauses, ─▶ add checked ─▶ build the
         (or OCR)     (two columns)        tables, DSR items      equations     search index
                                           + tags                 and values

USING (web app or API)

 question ─▶ find the right sources ─▶ written answer with [S1] sources ─▶ 👍 / 👎
 element sizes ─▶ quantities (IS 1200) ─▶ DSR rates ─▶ priced BOQ ─▶ Excel / PDF
 grade, exposure ─▶ mix design (IS 10262 + IS 456 limits) ─▶ kg per m³
```

### 1. Preparing the documents

1. **Register.** Every PDF is listed in `config/sources.yaml` with its edition and amendments. A fingerprint of
   each file is stored, so a changed file is noticed.
2. **Read the text.** Digital PDFs are read directly. Scanned pages are read with OCR (Tesseract), using the
   picture already inside the PDF. Results are saved, so the slow OCR runs only once.
3. **Read the DSR rates three times.** Each rate is read from the English page, read again digits-only, and
   read from the Hindi page. The readings vote. A rate without at least two agreeing readings goes to the
   **Review queue** for a person to check.
4. **Fix the reading order.** IS codes are printed in two columns. The left column is read before the right,
   and full-width tables, captions and "label : value" rows are kept whole.
5. **Split into pieces ("chunks").** One chunk per clause, table, annex clause, amendment or DSR item. Each
   chunk is tagged with its code, clause or table number, page, material, work item, concrete grade, and the
   clauses it refers to.
6. **Apply checked fixes.** About 770 checked corrections to DSR rows (`config/corrections/`) are applied. For
   scanned codes whose printed formulas or tables OCR cannot read, the values checked against the page
   (`config/equations/`, `config/code_tables/`) are added beside the OCR text.
7. **Build the search index.** Each chunk gets a meaning vector (bge-m3) and a keyword vector (BM25), stored
   in Qdrant. The original text and data stay in the database (`storage/docstore.db`).

### 2. Searching and asking (Search & Ask)

1. **Exact references first.** "cl. 26.4.2", "Table 16" or "DSR 5.33.2.1" are looked up directly.
2. **Two searches.** A meaning search and a keyword search each return their best 50 chunks. The two lists are
   combined.
3. **Re-rank.** A second model reads the top 24 next to the question and puts the most useful first.
4. **Add context.** Clauses a result refers to, and amendments that change it, are attached.
5. **Show sources.** Every result shows its code, clause and page, a **View page** link to the scan, and a
   warning if its OCR is unchecked.
6. **Ask (written answer).** The top sources are numbered [S1], [S2]… and given to the local AI model with the
   question. Answers approved by an admin for similar questions are shown to it as examples. The model must
   answer only from the sources and cite them.
7. **Number check.** Every number in the answer is searched for in the sources. Any number not found is flagged
   as "ungrounded" and shown to you. It is never silently corrected.

### 3. Mix design (IS 10262)

1. **Target strength** = grade + 1.65 × standard deviation, or grade + X, whichever is higher (Tables 1 and 2).
2. **Water-cement ratio** from the Fig. 1 curve for your cement, capped by the IS 456 Table 5 limit for the
   exposure.
3. **Water** from Table 4 for the aggregate size, adjusted for slump, aggregate shape and admixture.
4. **Cement** = water ÷ water-cement ratio, then checked against the IS 456 minimum and the 450 kg/m³ maximum.
5. **Coarse aggregate share** from Table 5 for the sand zone, corrected for the actual water-cement ratio.
6. **Volumes to weights.** Air, cement, water and admixture volumes are subtracted from 1 m³, and the rest is
   split into fine and coarse aggregate.

Every step shows its formula, its value and the clause or table it came from.

### 4. RCC design (design aid only)

Size a beam, one-way slab, short column or isolated footing to IS 456. Each check (bending, shear, deflection,
minimum and maximum steel, spacing, cover) shows pass or fail with its clause. Every result is labelled
**Design aid only**: a qualified structural engineer must review it.

### 5. Estimate / BOQ

1. **Enter elements.** Type, sizes in metres, count, concrete grade, and below or above plinth.
2. **Quantities (IS 1200).**
   - Concrete volume: dimensions rounded as the standard says, and openings up to 0.1 m² not deducted.
   - Formwork contact area: openings up to 0.4 m² not deducted.
   - Steel weight: concrete volume × a kg-per-m³ figure. You can give your own; otherwise the learned value
     (once approved) or the default from `config/cost/estimation_defaults.yaml` is used, and it is listed in the
     estimate's assumptions. (A bar bending schedule can be worked out separately with `/v1/bbs`. It is not yet
     fed into the estimate.)
3. **Match DSR items.** Each quantity is mapped to its DSR 2023 item (`config/cost/boq_mapping.yaml`). For
   example, M25 design mix above plinth maps to 5.33.2.1, and slab formwork to 5.9.3.
4. **Price.** Quantity × DSR rate × location index (Delhi = 100; other cities are refused until their index is
   added). A rate the OCR check could not confirm is flagged.
5. **Totals.** Subtotal + contingency (3% by default) = grand total. DSR rates already include contractor's
   profit, overheads and GST.
6. **Extras.** A cement consumption statement from the DSR coefficients, and a rough split into materials
   versus labour, equipment and overheads.
7. **Save and export.** Every estimate is saved, with its assumptions and warnings, and can be downloaded as
   Excel (with working formulas) or PDF.

### 6. Keeping it correct

- **Review queue:** DSR rows the scanner was unsure about. A person fixes them next to the scanned page, and the
  fix is re-applied on every ingest.
- **Code tables:** the IS values the calculators use. Each is compared with the scanned page, and a person
  signs it off.
- **Learning:** 👍 / 👎 on results and answers. An admin can discard careless ratings, which are then never used.
  A learned change goes live only if it scores better on the 50 test questions (see below).
- **Tests and scores:** `pytest -q` checks the code, and `crag evaluate` scores search, answers and
  calculators. Results are in [docs/technical.md](docs/technical.md).

---

## How it keeps learning

Every search result and answer has **👍 / 👎** buttons. 👎 on an answer lets you type the correct answer.

The system uses these ratings to improve, but **it never changes itself blindly.** Each improvement is tested
against 50 known questions first, and it goes live only if it scores better. Every version is saved and can be
undone.

| What improves | Learns from | Command |
|---|---|---|
| Order of search results | 👍 / 👎 on sources | `crag learn fusion`, then `crag learn rerank` |
| Written answers | Approved answers and corrections | automatic once an admin approves them |
| Steel estimate (kg/m³) | Actual costs of finished projects | `crag learn calibrate` |

**Never learned:** IS code values, DSR rates and calculator formulas. Only a person changes those (Review queue,
Code tables).

So far nothing has been learned. It needs about 100 carefully rated questions; see step 2 below.

---

## Command cheat sheet

| Command | What it does |
|---|---|
| `crag serve` | Start the web app and API |
| `crag search "your question"` | Search from the terminal |
| `crag ingest` / `crag index` | Re-read the PDFs / rebuild search (stop `crag serve` first) |
| `crag evaluate` | Score search, answers and calculators → `eval/reports/` |
| `crag learn status` | What has been learned, how many ratings exist |
| `crag learn rerank` | Train better search ranking from ratings (stop `crag serve` first) |
| `crag learn rollback reranker` | Undo the last learning step |
| `crag apikey create / list / revoke` | Manage API keys |
| `pytest -q` | Run the tests |

The search index can be used by **one program at a time.** Stop `crag serve` before `crag ingest`, `crag index` or
`crag learn fusion|rerank`.

---

## Tech stack

Everything runs on your own computer. No document, question or answer is sent to an outside service.

**Application**

| Part | What it uses |
|---|---|
| Language | Python 3.12 |
| API and web server | FastAPI + Uvicorn; API keys with scopes, rate limit (slowapi), audit log |
| Web app | Plain HTML, CSS and JavaScript served by the API (no framework) |
| Database | SQLite through SQLAlchemy (Postgres also works; see `docker-compose.yml`) |
| Calculators | Plain Python with exact decimal rounding; `pint` for units |
| Excel / PDF export | openpyxl (Excel keeps live formulas), reportlab (PDF) |
| Command line | Typer + Rich (`crag …`) |
| Tests | pytest |
| Optional server setup | Docker Compose: Qdrant server, Postgres and the API |

**RAG (search and answers)**

| Step | What it uses |
|---|---|
| Read PDFs | PyMuPDF for digital PDFs. Tesseract 5 OCR for scans, run on the PDF's own embedded image. The DSR is read three times and the readings vote |
| Reading order | Own two-column layout code (`ingest/layout.py`) that keeps full-width tables and captions together |
| Split into chunks | One chunk per clause, table, annex clause, amendment or DSR item (6,927 chunks), tagged with code, clause, table, page, material, work item and more |
| Checked additions | Equations (`config/equations/`) and table values (`config/code_tables/`), read from the pages, are added beside the OCR text |
| Meaning search | `BAAI/bge-m3` embeddings (sentence-transformers, runs on the Mac's GPU) |
| Keyword search | BM25 with its own tokenizer that keeps codes like `26.5.1.1`, `M25` and `5.33.2.1` whole |
| Vector database | Qdrant (built-in local mode by default): meaning and keyword vectors in one collection |
| Combining results | Exact-reference lookup first ("cl. 26.4.2", "Table 16", "DSR 5.33.2.1"), then reciprocal-rank fusion of the two searches (top 50 each) |
| Re-ranking | `BAAI/bge-reranker-v2-m3` cross-encoder on the top 24 |
| Answer writing | Local Qwen2.5-3B-Instruct (4-bit) through Ollama. Answers must cite sources [S1], [S2]…, and every number is checked against them |
| Learning from ratings | Fusion weights tuned on rated questions; reranker fine-tuned with sentence-transformers. Each change is tested on the 50 test questions before it goes live |
| Measuring quality | Own evaluation (`crag evaluate`): Recall, Hit, MRR and nDCG for search; expected fact, number check, refusals and an LLM judge for answers |

More detail: [docs/technical.md](docs/technical.md) and [docs/scope.md](docs/scope.md) (why each choice was made).

---

## Where things stand

- **Data:** complete for this project: IS 456, IS 10262, IS 1200, IS 2386 and both DSR 2023 volumes. No more
  documents are planned. The review queue is empty, and about 770 scanner errors were checked against the scanned
  pages and fixed. Rates are for **Delhi** (DSR base); other locations are refused rather than guessed.
- **Search quality:** the right source is in the top 8 results for 49 of 50 test questions, and first for most of
  them. The one miss still finds the worked examples that quote the right table.
- **Calculators:** reproduce the IS 10262 worked example exactly. All IS 456 values were checked against the
  scanned pages.
- **Tests:** 40 pass.
- **Not measured yet:** how close estimates are to real costs (needs your project data).

More detail: [docs/data-notes.md](docs/data-notes.md) (what was read, fixed and still known to be wrong) and
[docs/technical.md](docs/technical.md) (how it works, API, settings, test scores, LLM choice).

---

## Your Next Steps (Owner Actions)

In order of priority. Only you can do or decide these.

### 1. Right now
- [ ] **Restart `crag serve`** so it drops the removed `/v1/agent` endpoint. If a downstream system called it,
      switch it to the calculator and `/v1/estimate` endpoints. The Claude (`anthropic`) LLM option is gone too;
      local Ollama is the only LLM.
- [ ] **Run `crag serve` again**: the search index was rebuilt on 2026-10-22 with readable equations.
- [ ] **Re-run saved estimates that use these items.** Their rates were wrong and are now fixed: 11.1.2, 8.2.3.2,
      8.3.2, 8.14.5.2 and basic rate 7773 (details in [docs/data-notes.md](docs/data-notes.md)).
- [ ] **Re-run RCC designs saved before 2026-10-20** (beams, slabs, footings). The deflection and footing checks
      were corrected that day.

### 2. Rate search results while you work
- [ ] 👍 only the source that actually answers the question. 👎 misleading results near the top. On a wrong
      answer, 👎 and type the correct one. Rating everything 👍 teaches it nothing.
- [ ] Aim for about **100 rated questions** across IS 456, 10262, 1200, 2386 and the DSR (30 are rated so far).
- [ ] Once a week, open **Learning** and approve good corrections and discard careless ratings.
- [ ] Every 100 or so new ratings: stop `crag serve`, run `crag learn fusion` then `crag learn rerank`, and start
      it again. Each command prints before / after scores and only switches if the new version is better.

### 3. Spot-check the automatic fixes
Many fixes were made by reading the scans with AI vision and are tagged `claude-vision`.
- [ ] Open **Code tables**. Every table now shows **AI-checked, needs human sign-off**. Press **Compare with
      page**, and sign the ones you have compared in your own name. Only then do they count as verified.
- [ ] Open `data/processed/fig1_overlay.png` and compare it with IS 10262 **Fig. 1** in your printed copy. The
      coloured rings are the stored points; each should sit on its curve.
- [ ] Spot-check a few equations in `config/equations/is456_2000.yaml` against your copy of IS 456 (for example
      cl. 26.2.1, 39.3, 40.4 and Annex G).
- [ ] Look through a few DSR fixes in `config/corrections/cpwd_dsr_2023.yaml` against the printed DSR.

### 4. Give it real test data
- [ ] **Actual costs of finished projects:** copy `eval/datasets/actual_costs_template.csv` to
      `actual_costs.csv`, one row per BOQ item. For steel rows, also fill `element_type` and `concrete_m3`. Then
      run `crag evaluate` (shows how far estimates were off) and `crag learn calibrate`.
- [ ] More test questions from your engineers: grow `eval/datasets/retrieval_golden.jsonl` from 50 to about 150.
      `generation_golden.jsonl` now has 26 (each with its source page); add your own the same way.
- [ ] Worked examples you trust (SP 16, your past mix designs) as tests in `tests/unit/test_calculators.py`.

### 5. Before other people use it
- [ ] One API key per person or system, with only the scopes they need. Revoke with `crag apikey revoke <name>`.
- [ ] Keep `CRAG_API_BOOTSTRAP_ADMIN_KEY` empty, and put the server behind HTTPS.
- [ ] Back up `storage/`, `config/` and `data/processed/` regularly.
- [ ] The IS PDFs are licensed by BIS. Keep `data/` out of git (it already is), and only share excerpts with people
      entitled to them.

### 6. Decisions I need from you
1. Default contingency (now 3%), and any extra overhead on top of DSR rates? (DSR rates already include 15%
   contractor's profit and overheads and 18% GST.)
2. Default steel per m³ of concrete (`config/cost/estimation_defaults.yaml`), or always require a BBS?
3. Design mix (now the default, DSR 5.33.x) or nominal mix for estimates?
