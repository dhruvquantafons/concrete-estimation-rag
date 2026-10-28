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
3. **Read the DSR from the clean copy, check it against the scan.** The clean copy (`data/CPWD SOR HQ`) is read
   for every item, basic rate, unit and description; what it prints is used. The scan's English pages are read
   twice (normal and digits-only) as a cross-check; its Hindi pages are ignored. A rate that no second source
   (a scan reading or the DAR analysis) confirms, or a scan row the clean copy does not have, goes to the
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

Every step shows its formula, its value and the clause or table it came from, with the code edition
(e.g. "IS 456 : 2000 (incl. Amd 1-3), Table 5"). The result also lists the code versions used and a
**confidence score** (high / medium / low, 0-1) with the factors behind it: how well each code value was
checked, where the w/c ratio came from, and whether every code check passed. A failed check always makes it low.

**Concrete-mix report (`POST /v1/concrete-mix`).** Give the mix inputs and your elements, and get everything in one
result: a mix design for each grade used (the most severe exposure among its elements), element volumes (IS 1200),
reinforcement per element and in total, wastage factors, materials per m³ and to procure (cement bags, sand and
aggregate in m³, admixture in litres), IS clause citations, code versions and a confidence score. Wastage comes
from `config/cost/wastage.yaml`; see [Wastage: where each value comes from](#wastage-where-each-value-comes-from).

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
   example, M25 design mix above plinth maps to 5.33.2.1, and slab formwork to 5.9.3. Work with no mapped item
   (for example M20 design mix, which DSR 2023 does not have) is matched by search over the DSR. The line is
   marked **searched**, with alternatives, and you should confirm it. Quantities are converted to the item's unit
   (e.g. m² to "per 10 m²").
4. **Price.** Quantity × DSR rate × location index (Delhi = 100; other cities are refused until their index is
   added). Each DSR rate is checked against its CPWD DAR 2023 analysis (a digital PDF): "✓ DAR" means they are
   equal. A rate the DAR disagrees with, or the OCR check could not confirm, is flagged.
5. **Totals.** Subtotal + contingency (3% by default) = grand total. DSR rates already include contractor's
   profit, overheads and GST.
6. **Extras.** A cement consumption statement from the DSR coefficients, and the **cost heads** of every line from
   its DAR analysis: material, labour, equipment (hire and running of plant), carriage, sundries, water charges
   (1%), GST (factor 0.2127), contractor's profit and overheads (15%) and cess (1%). BOQ quantities stay net,
   because IS 1200 measures net work and DAR quantities already allow for wastage.
7. **Confidence.** A score from how the rates were confirmed (equal to DAR is best), how items were chosen, where
   the steel figures came from and the location index. A searched item limits it to medium.

**DAR analyses (`GET /v1/rates/{code}/analysis`, `POST /v1/rates/{code}/reprice`).** See how CPWD built any DSR
rate (every material, labour and hire row with quantity × rate), or re-price it with other basic rates, such as
market rates: same quantities and the same build-up, new rate.
8. **Save and export.** Every estimate is saved, with its assumptions and warnings, and can be downloaded as
   Excel (with working formulas) or PDF.

#### Rates: DSR, cost index and market rates

**Market rates are not needed for CPWD-method estimates for Delhi.**

- **DSR 2023 is still the current civil schedule.** CPWD's 2025 releases were the E&M and the Horticulture &
  Landscaping schedules. No civil DSR 2025 was found (checked 2026-10-27).
- **Old rates are brought up to date with a cost index, not market rates.** CPWD (and e.g. Indian Railways,
  Railway Board letter of 03.07.2025) applies DSR 2023 everywhere through a *building cost index*:
  rate = DSR rate × index / 100. The index goes in `config/cost/cost_indices.yaml` (only Delhi = 100, the DSR base,
  is filled in).
- **The number worth getting is the current CPWD cost index for Delhi** over the DSR 2023 base, with its office
  memorandum number and date, from cpwd.gov.in. It is one number per location.
- **Market rates are only needed for real current prices**, e.g. a contractor's bid or items not in the DSR.
  - Materials: written quotes from local suppliers (cement dealer, RMC plant, TMT stockist, sand and aggregate
    suppliers).
  - Labour: DSR 2023 uses Delhi wages w.e.f. 01.04.2023; the Delhi Government's later minimum-wage notifications
    are the official update.
  - How they are used: give them as basic-rate codes to `POST /v1/rates/{code}/reprice`. The item's DAR analysis
    is re-priced with the same quantities and the same build-up (water 1 %, GST factor 0.2127, CPOH 15 %,
    cess 1 %).

#### Wastage: where each value comes from

IS 456, IS 10262 and IS 1200 do not prescribe wastage. Each value in `config/cost/wastage.yaml` records its source:

| Material | Wastage | Source |
|---|---|---|
| Cement | 2 % | CPWD: ±2 % permissible variation over theoretical cement consumption (works above ₹25 lakh) |
| Steel reinforcement | 5 % | CPWD DAR 2023 item 5.22.6: 1.05 quintal of bars per quintal of reinforcement |
| Fly ash | 2 % | taken equal to cement |
| Sand (fine aggregate) | 5 % | site practice: handling, bulking and spillage; no CPWD figure |
| Coarse aggregate | 3 % | site practice; no CPWD figure |
| Admixture | 1 % | site practice |
| Binding wire | 5 % | site practice (DAR 5.22.6 prices binding wire as a lump sum) |
| Water | 0 % | mix water only; curing water is separate |

- **What wastage changes:** only the "materials to buy" figures of the concrete-mix report.
- **What it never changes:** BOQ quantities or costs. IS 1200 measures net work, and DSR / DAR rates already
  allow for wastage, so adding it there would count it twice.
- **Effect on confidence:** CPWD-sourced values score higher than site-practice ones. When a site engineer or QS
  confirms the file and writes their name in `approved_by`, the wastage factor counts as fully confirmed.

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

- **DSR = the clean copy (full comparison 2026-10-28):** `data/CPWD SOR HQ` is a clean copy of the *original*
  DSR 2023 printing (Aug 2023); `data/CPWD SOR` is a 96-dpi scan of the corrected edition. By your decision
  **whatever the clean copy prints is used** (code, description, unit, rate), for items and basic rates; the scan's
  Hindi pages are no longer read. Every DSR item and basic rate in the database now equals the clean copy
  (checked code by code). Compared on every ingest; per-row list in `data/processed/dsr_scan_vs_clean.csv`:
  - **Items (3,200):** 2,905 agree with the scan; 278 only the clean copy has (the scan's OCR missed them or
    misread their codes); 17 differ, the scan misread (in 15 its own digit re-read agrees with the clean copy;
    the rest checked on the page). 315 scan codes were misread and renamed (e.g. "13.10.0.1" is 13.100.1); 31
    duplicate scan rows were dropped.
  - **Basic rates (2,478):** now read from the clean copy too: 2,334 agree with the scan, 92 the scan missed, 52
    the scan misread (DAR confirms the clean copy every time). Carriage codes 2200-2399 are 0.00 as the
    clean copy's materials list (and DAR) print them; their 5 km carriage rate is in the description.
  - **Carriage (sub-head 1):** the rotated lead tables are typed in from the clean copy in
    `config/sor/cpwd_dsr_2023_carriage.yaml` (73 items; rate = first lead column, all leads in the description).
  - Reader fixes found by the comparison: codes printed "4.9 A" / "26.42.1 A" / "2.25(a)" / 5th-level
    (9.27.2.1.1), rates at the end of a "Note:-" inside an item (24.2, 25.2, 26.52), units printed on several
    lines ("per cm depth per cm width per metre length").
  - Corrections checked before 2026-10-28 were read off the scan: where they disagree with the clean copy, the
    clean copy wins (listed in `data/processed/ingest_report.json`). Corrections dated 2026-10-28 or later win.
  DAR vs DSR (reviewed 2026-10-28): 2,966 DAR analyses equal their DSR rate; only 13.67.1 differs (below). The 27
  other differences were DAR reading errors, now fixed: an analysis without a "Say" line is the cost rounded to
  0.05 as the DSR prints it (9.123: 696.02 -> 696.00); codes printed "9.147.A1.2", "2.25(a)", "4.9 A", "17.75,",
  "Sub A/R - 2"; a heading wrapped onto a line starting "7.5"; OCR marks after scanned numbers ("81.70]"). DAR
  9.134.1.2.2 prints Say 3764.55 against its own cost 3964.53 (a DAR misprint, recorded in
  `config/corrections/cpwd_dar_2023.yaml`). 10.3 / 10.4, detached on 2026-10-27 when the scan misread them, match
  again and are re-attached. Backups: `storage/docstore.backup-2026-10-28-before-dar-review.db`,
  `storage/docstore.backup-2026-10-28.db` (before this comparison),
  `storage/docstore.backup-2026-10-28-before-clean-priority.db`, `storage/docstore.backup-2026-10-27-before-clean-copy.db`.
- **Data:** IS 456, IS 10262, IS 1200, IS 2386, both DSR 2023 volumes and both DAR 2023 volumes are in. Delhi PWD
  uses the CPWD DSR, so for Delhi there is no separate State PWD SOR. DAR: 2,993 analyses read; 2,966 equal their
  DSR rate exactly, 1 disagrees (13.67.1), and 171 are flagged for arithmetic the OCR could not check (mostly the
  scanned sanitary sub-head 17; their rates still equal the DSR). Re-pricing reproduces the DAR rate for 2,619 of 2,620 clean analyses. Market rates are not needed
  for CPWD-method estimates (see [Rates](#rates-dsr-cost-index-and-market-rates)). The DSR and basic-rate review
  queues are empty. Rates are for **Delhi** (DSR base); other locations are refused rather than guessed.
- **Search quality:** the right source is in the top 8 results for 49 of 50 test questions, and first for most of
  them. The one miss still finds the worked examples that quote the right table.
- **Formula check (2026-10-28):** every formula and code value was checked against IS 10262, IS 456, IS 1200,
  IS 2386 and CPWD DAR. Fixed: wet-aggregate field adjustment now as IS 10262 Annex B (SSD x (1 + free
  moisture)); compression-steel stress in doubly reinforced beams from IS 456 Fig. 23A (was elastic-plastic,
  slightly unsafe); continuous one-way slabs use Table 12 / 13 (support next to end support, 0.6 wL shear;
  was wL2/10, 0.5 wL); the DSR "extra above floor V" item counts each storey above floor V once per four floors
  (was all above-plinth concrete x ceil(n/4)); counted units convert (25 each = 2.5 x 10 Nos).
- **Calculators:** reproduce the IS 10262 worked examples (Annex A, and Annex B's wet-aggregate adjustment) exactly. All IS 456 values were checked against the
  scanned pages.
- **Tests:** 62 pass.
- **Not measured yet:** how close estimates are to real costs (needs your project data).

More detail: [docs/data-notes.md](docs/data-notes.md) (what was read, fixed and still known to be wrong) and
[docs/technical.md](docs/technical.md) (how it works, API, settings, test scores, LLM choice).

---

## Your Next Steps (Owner Actions)

In order of priority. Only you can do or decide these.

### 1. Right now
- [ ] **Start `crag serve`** in your own terminal (the server run from the Claude session was stopped on
      2026-10-27), then hard-refresh `/ui`.
- [ ] **Wastage (optional):** cement 2 % and steel 5 % come from CPWD; sand, aggregate, admixture and binding wire
      are site practice (table under [Wastage](#wastage-where-each-value-comes-from)). When convenient, ask a site
      engineer / QS to look over `config/cost/wastage.yaml` and write their name in `approved_by`.
- [ ] **Confirm any "searched" BOQ lines.** When an estimate shows a line marked *searched*, check the item in the
      DSR and add the right code to `config/cost/boq_mapping.yaml`, so it is mapped next time.
- [x] ~~Restart, rebuild the search index, refresh saved estimates~~ (done 2026-10-27: all 34 saved estimates were
      re-priced with DAR cost heads and confidence; quantities unchanged; backup in
      `data/processed/estimates_backup_2026-10-27.json`).
- [ ] **Stop `crag serve`, run `crag index`, start `crag serve` again** (about 7 minutes). The rates are already
      live; this refreshes the search text after the full clean-copy comparison of 2026-10-28 (about 280 items
      added, units and descriptions now as printed in the clean copy, 73 carriage items).
- [x] ~~Compare the whole DSR scan with the clean copy~~ (done 2026-10-28, see *Where things stand*).
- [x] ~~Check the DSR rates where the clean copy differs from the scan~~ (done 2026-10-28, `claude-vision`: every
      row checked on the clean copy and signed off at its printed rate; **Review → DSR items** is empty). Almost
      all scan readings were the neighbouring row's rate (OCR row shift), not correction slips.
- [ ] **Decide 13.67.1** (glue sizing with copal varnish): the clean copy prints **233.80** (now used), but the scan
      of the corrected edition clearly prints **236.10** and DAR agrees, so a correction slip probably changed it.
      To use 236.10, change `rate: 233.8` to `236.1` on its line in `config/corrections/cpwd_dsr_2023.yaml` and
      re-ingest the DSR (`crag ingest --doc CPWD_DSR_2023_V1 --doc CPWD_DSR_2023_V2`, about 20 s).
      (26.42.1 / 26.42.1 A differ too: clean copy and DAR 3580.65 / 2821.65, scan 3903.35 / 2901.80. DAR agrees
      with the clean copy there, so the clean copy stays unless you have a slip issued after Sep 2023.)
- [ ] **Optional:** CPWD's correction slips for DSR 2023 issued between Aug and 10.11.2023 (cpwd.gov.in), or a
      clean copy of the corrected edition. Put it in `data/` and tell me.
- [x] ~~Check the DSR rates that disagree with DAR~~ (done 2026-10-28: 27 of 28 were DAR reading errors, fixed;
      **Review → DAR vs DSR** now lists only 13.67.1, decided in the step above). Note *DAR is right* on that tab
      overrides the printed clean-copy rate (a correction dated after 2026-10-28); use it only for a deliberate
      change.
- [ ] **Get the current CPWD cost index for Delhi** (over the DSR 2023 base) with its office memorandum number
      and date from cpwd.gov.in, and add it to `config/cost/cost_indices.yaml`. This is what brings DSR 2023 rates
      to today; market rates are not needed (see [Rates](#rates-dsr-cost-index-and-market-rates)).
- [ ] **Only if you want real current prices** (a bid, or items not in the DSR): a sheet with `basic_rate_code,
      description, unit, rate, location, date, source`, using the DAR/DSR basic-rate codes (e.g. 0367 OPC-43
      cement per tonne, 0982 coarse sand per m³, 0295 20 mm aggregate per m³, 1005 TMT Fe-500D per quintal, 0114
      Beldar per day). Each DSR item can then be re-priced from its DAR analysis (`/v1/rates/{code}/reprice`).
- [ ] **Optional: the 304 flagged DAR analyses** are listed in `data/processed/dar_analyses.csv` (`needs_review`).
      Only 5 are in sub-heads 3 to 5 (5.1.3A, 5.9.15, 5.13A, 5.26, 5.27). The rest are mostly the scanned
      sanitary pages, which concrete estimates do not use.
- [ ] **Look at the new web app design.** Restart `crag serve`, open `/ui` and hard-refresh (Cmd+Shift+R) so the
      browser loads the new styles. The app is now blue and white only (no dark mode). Long lists (DSR rates,
      saved estimates, learning feedback, review queue) have page numbers at the bottom. Click through each screen
      and tell us if anything looks off. Only the look changed: every screen, button and number works as before.
      The rate location picker is now in the header, and API docs and Verification are in the account menu.
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
- [x] ~~Sign off the IS code tables.~~ 10 of 13 signed off on 2026-10-23 (IS 10262 Tables 1–5; IS 456 Tables 5,
      16, 19, 20 and bond stress cl. 26.2.1.1).
- [ ] Sign off the last 3 on **Code tables**:
  - **IS 456 cl. 38.1** (limiting xu,max/d): compare the three values with cl. 38.1 in your copy.
  - **IS 456 detailing limits** (min/max steel, bar spacing, cover, span/depth): check each line against the
    clause it lists (26.5.1.1, 26.5.1.5, 26.5.2.1, 26.5.3.1, 23.2.1, 25.4, 39.3, 36.4.2).
  - **IS 10262 Fig. 1** (w/c vs strength curves): open `data/processed/fig1_overlay.png` and compare it with
    Fig. 1 in your printed copy. The coloured rings are the stored points; each should sit on its curve.
- [ ] **Commit `config/code_tables/`** after signing, so the sign-offs are kept and backed up. They are saved in
      those YAML files, not in the database.
- [ ] **When all sign-offs are done, refresh search** so results show "signed off" instead of "AI-checked" on
      those tables. Stop `crag serve`, run `crag ingest --doc IS456_2000 --doc IS10262_2019` (uses the OCR cache,
      so it's quick), then `crag index` (~4 min), then start `crag serve` again. The calculators already use the
      signed-off values; this only changes the label shown in search.
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
