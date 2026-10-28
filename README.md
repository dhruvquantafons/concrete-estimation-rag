# Buildmore AI - Concrete and Costs

Buildmore AI helps engineers plan and price concrete work in India. It does three jobs:

1. **Looks things up in the standards.** You can search IS 456, IS 10262, IS 1200, IS 2386 and the CPWD Delhi
   Schedule of Rates (DSR) 2023. Each answer shows its source: the clause, the table or the DSR item, and the page.
2. **Does the engineering calculations.** It covers mix design (IS 10262), RCC design aids (IS 456),
   quantities (IS 1200) and bar bending schedules.
3. **Prices the work.** It turns a list of building elements into a priced Bill of Quantities (BOQ) using DSR
   rates. You can export it to Excel or PDF.

It runs on your own computer. Other software can use it through a secure API.

> **The one rule:** the AI never calculates a number and never makes up a code value or a rate. Every number
> comes from the standard, from a checked table, or from a tested calculator.

**Scope today:** rates are for **Delhi** only. Delhi PWD uses the CPWD DSR, so there is no separate state
schedule to add. The app refuses other cities until their cost index is added; it never guesses.

---

## Contents

- [Getting started](#getting-started)
- [What you can do in the web app](#what-you-can-do-in-the-web-app)
- [How it works](#how-it-works)
- [Where the numbers come from](#where-the-numbers-come-from)
- [How it learns](#how-it-learns)
- [Commands](#commands)
- [Windows desktop app](#windows-desktop-app)
- [Project layout and tech stack](#project-layout-and-tech-stack)
- [Current status](#current-status)
- [Your Next Steps (Owner Actions)](#your-next-steps-owner-actions)
- [More documentation](#more-documentation)

---

## Getting started

### What you need

- macOS or Linux with **Python 3.12**
- **Tesseract** (reads scanned pages): `brew install tesseract`
- **[Ollama](https://ollama.com)**, only for the *Ask* feature (written answers). Everything else works without it.
  The model used is set in `.env` (now `Qwen2.5-3B-Instruct`).
- The source PDFs in `data/`. They are not in git because the IS standards are licensed by BIS.

### First-time setup

```bash
make setup                 # create .venv and install everything
cp .env.example .env       # settings (all of them are listed in src/concrete_rag/config.py)
crag ingest                # read all the PDFs        (about 10 minutes the first time)
crag index                 # build the search index   (about 4 minutes)
crag apikey create your-name --scopes retrieve,generate,calc,estimate,admin
```

Keep the key that the last command prints. You sign in to the web app with it.

### Every day

```bash
crag serve                 # then open http://127.0.0.1:8000/ui and sign in with your key
```

Only one program can use the search index at a time. **Stop `crag serve` before you run `crag ingest`,
`crag index` or `crag learn fusion|rerank`.**

---

## What you can do in the web app

| Screen | What it is for |
|---|---|
| **Search & Ask** | Search the standards and the DSR. **View page** opens the original scan. **Ask** writes a short answer that cites its sources as [S1], [S2] … |
| **Mix design** | Get kg per m³ of cement, water, sand and aggregate (IS 10262), with every step and clause shown |
| **RCC design** | Size a beam, one-way slab, column or footing (IS 456) and see which checks pass. **This is a design aid only:** a qualified structural engineer must review it |
| **Estimate / BOQ** | Enter elements and get quantities, a priced BOQ, a cement statement, and Excel / PDF files |
| **DSR rates** | Look up any of the 3,200 DSR items by word or code |
| **Code tables** | See the IS values the calculators use, compare them with the scan, and sign them off |
| **Review queue** *(admin)* | Fix DSR rows the scanner was unsure about, side by side with the scanned page |
| **Learning** *(admin)* | Approve or discard ratings, and see what the system has learned |

Which screens you see depends on your key's permissions (called *scopes*).

---

## How it works

There are two stages. **Preparing** runs once, and again whenever a document changes. **Using** happens every
time someone searches, designs or estimates.

```
PREPARING  (crag ingest, then crag index)

  PDFs -> read text  -> fix reading order -> split into clauses,  -> add checked values -> build the
          (or OCR)      (two columns)        tables and DSR items    and equations         search index

USING  (web app or API)

  question      -> find sources -> written answer citing [S1], [S2] ... -> thumbs up / down
  element sizes -> quantities (IS 1200) -> DSR rates -> priced BOQ -> Excel / PDF
  grade, exposure -> mix design (IS 10262 + IS 456 limits) -> kg per m³
```

### 1. Preparing the documents

1. **Register.** Each PDF is listed in `config/sources.yaml` with its edition and amendments. The app stores a
   fingerprint of every file, so it notices when a file changes.
2. **Read.** Digital PDFs are read directly. Scanned pages are read with OCR. OCR results are saved, so the slow
   step runs only once.
3. **Read the DSR carefully.** The clean copy of the DSR (`data/CPWD SOR HQ`) gives every item code,
   description, unit and rate. The scanned DSR (`data/CPWD SOR`) and the CPWD rate analysis (DAR) are used to
   check it. A rate that nothing else confirms goes to the **Review queue** for a person to check.
4. **Fix the reading order.** IS codes are printed in two columns. The left column is read first, and tables
   and captions are kept whole.
5. **Split into pieces ("chunks").** One chunk per clause, table, DSR item or DAR analysis. Each chunk is tagged
   with its code, clause, page, material, work item, grade and so on.
6. **Apply checked fixes.** Hand-checked corrections (`config/corrections/`) and values read off the scanned
   pages (`config/equations/`, `config/code_tables/`) are added.
7. **Build the search index.** Each chunk gets a "meaning" vector (bge-m3) and a "keyword" vector (BM25), both
   stored in Qdrant. The full text and data stay in the database (`storage/docstore.db`).

### 2. Search and Ask

1. Exact references such as "cl. 26.4.2", "Table 16" or "DSR 5.33.2.1" are looked up directly.
2. Otherwise, a meaning search and a keyword search each find their best 50 chunks, and the two lists are
   merged.
3. A second model (the *reranker*) re-orders the top 24 so the most useful come first.
4. Clauses that a result refers to, and amendments that change it, are attached.
5. **Ask** gives the top sources to the local AI model. It must answer only from them and cite them.
6. **Number check:** every number in the answer is looked for in the sources. A number that is not found is
   shown to you as "ungrounded". It is never silently corrected.

### 3. Mix design (IS 10262)

1. **Target strength** = grade + 1.65 × standard deviation, or grade + X, whichever is higher.
2. **Water-cement ratio** from the Fig. 1 curve, capped by the IS 456 Table 5 limit for the exposure.
3. **Water** from Table 4, adjusted for slump, aggregate shape and admixture.
4. **Cement** = water ÷ w/c ratio, checked against the IS 456 minimum and the 450 kg/m³ maximum.
5. **Coarse aggregate share** from Table 5, corrected for the actual w/c ratio.
6. **Volumes to weights:** air, cement, water and admixture are taken out of 1 m³; the rest is split between
   sand and coarse aggregate.

Every step shows its formula, its value and its clause, with the code edition (for example "IS 456 : 2000 (incl.
Amd 1-3), Table 5"). Each result also has a **confidence score** (high / medium / low). A failed code check
always makes it low.

**All-in-one report (`POST /v1/concrete-mix`):** give the mix inputs and your elements, and get back a mix design
for each grade, element volumes, steel, wastage, the materials to buy (cement bags, sand and aggregate in m³,
admixture in litres), IS citations, code editions and a confidence score.

### 4. RCC design (design aid only)

Sizes a beam, one-way slab, short column or isolated footing to IS 456. Each check (bending, shear, deflection,
steel limits, spacing, cover) shows pass or fail with its clause. Every result is labelled **Design aid only**.

### 5. Estimate / BOQ

1. **Enter elements:** type, sizes in metres, count, grade, and whether below or above plinth.
2. **Quantities (IS 1200):** concrete volume, formwork area and steel weight. Steel uses, in this order: your own
   kg/m³ figure, a learned figure (once approved), or the default in `config/cost/estimation_defaults.yaml`.
   The figure used is listed in the estimate's assumptions.
3. **Match DSR items:** each quantity is mapped to a DSR item in `config/cost/boq_mapping.yaml` (for example M25
   above plinth → 5.33.2.1, slab formwork → 5.9.3). Work with no mapping (for example M20 design mix, which DSR
   2023 does not have) is matched by search. That line is marked **searched**, and you should confirm it.
   Quantities are converted to the item's own unit.
4. **Price:** quantity × DSR rate × location index (Delhi = 100). Each rate is checked against its DAR analysis;
   "✓ DAR" means they agree.
5. **Totals:** subtotal + contingency (3% by default) = grand total. DSR rates already include profit, overheads
   and GST.
6. **Extras:** a cement statement, and each line split into **cost heads** from its DAR analysis: material,
   labour, equipment, carriage, sundries, water charges, GST, contractor's profit and overheads, and cess.
7. **Confidence:** based on how rates were confirmed, how items were chosen, and where the steel figures came
   from.
8. **Save and export:** every estimate is saved with its assumptions and warnings, and can be downloaded as Excel
   (with working formulas) or PDF.

---

## Where the numbers come from

### Rates

- **DSR 2023** is still CPWD's current civil schedule (no civil DSR 2025 was found when checked on 2026-10-27).
- **Each DSR rate has a DAR analysis** behind it: the materials, labour and machinery that make up the rate.
  You can see it with `GET /v1/rates/{code}/analysis`.
- **To bring rates up to today, CPWD uses a cost index**, not market rates: rate = DSR rate × index ÷ 100. The
  index goes in `config/cost/cost_indices.yaml`. Only Delhi = 100 (the DSR base) is filled in.
- **Market rates are only needed for real current prices**, such as a contractor's bid or items that are not in
  the DSR. Give them as basic-rate codes to `POST /v1/rates/{code}/reprice`. The item is then re-priced from its
  DAR analysis with the same quantities and the same overheads.

### Wastage

IS 456, IS 10262 and IS 1200 do not set wastage. The values are in `config/cost/wastage.yaml`, each with its
source:

| Material | Wastage | Source |
|---|---|---|
| Cement | 2 % | CPWD: ±2 % permitted variation in cement use (works above ₹25 lakh) |
| Steel | 5 % | CPWD DAR 2023 item 5.22.6: 1.05 quintal of bars per quintal of reinforcement |
| Fly ash | 2 % | taken equal to cement |
| Sand | 5 % | site practice (handling, bulking, spillage) |
| Coarse aggregate | 3 % | site practice |
| Admixture | 1 % | site practice |
| Binding wire | 5 % | site practice |
| Water | 0 % | mix water only; curing water is separate |

- Wastage is added **only** to the "materials to buy" figures in the concrete-mix report.
- It is **not** added to BOQ quantities or costs. IS 1200 measures net work, and DSR rates already allow for
  wastage, so adding it there would count it twice.
- When a site engineer or QS confirms the file and writes their name in `approved_by`, the confidence score
  treats wastage as fully confirmed.

---

## How it learns

Every search result and answer has **👍 / 👎** buttons. On a wrong answer, 👎 lets you type the correct one.

The system improves from these ratings, but **never changes itself blindly.** Each change is first tested on
50 known questions and goes live only if it scores better. Every version is saved and can be undone.

| What improves | Learns from | Command |
|---|---|---|
| Order of search results | 👍 / 👎 on sources | `crag learn fusion`, then `crag learn rerank` |
| Written answers | Answers and corrections an admin approved | automatic, once approved |
| Steel estimate (kg/m³) | Actual costs of finished projects | `crag learn calibrate` |

**Never learned:** IS code values, DSR rates and calculator formulas. Only a person changes those, in the
Review queue or the Code tables screen.

Nothing has been learned yet. It needs about 100 carefully rated questions (36 so far).

---

## Commands

| Command | What it does |
|---|---|
| `crag serve` | Start the web app and API |
| `crag search "your question"` | Search from the terminal |
| `crag ingest` | Re-read the PDFs (`--doc <id>` for one document) |
| `crag index` | Rebuild the search index |
| `crag status` | Show what is in the database |
| `crag evaluate` | Score search, answers and calculators (report in `eval/reports/`) |
| `crag verify-tables` | Compare the code tables with the OCR text |
| `crag learn status` | What has been learned and how many ratings exist |
| `crag learn rerank` | Train better search ranking from ratings |
| `crag learn rollback reranker` | Undo the last learning step |
| `crag apikey create / list / revoke` | Manage API keys |
| `pytest -q` | Run the tests |

Remember: stop `crag serve` before `ingest`, `index` or `learn fusion|rerank`.

---

## Windows desktop app

A one-click Windows app for people who never use a terminal. It contains the same web app, already signed in,
in its own window.

**What the user sees**

1. They run `BuildMoreAI-Setup.exe`. No admin rights are needed.
2. The first time, a **"Downloading Resources"** screen downloads about 6.5 GB (the AI and search models), with a
   progress bar. If the connection drops, **Try again** carries on from where it stopped.
3. After that, the app opens straight to the home page and works offline.

**Where things go on the user's PC**

- The app: `%LOCALAPPDATA%\Programs\BuildMoreAI`
- Data and downloads: `%LOCALAPPDATA%\BuildMoreAI` (folders `resources\`, `storage\`, `config\`, `data\`, `logs\`)
- Uninstalling keeps the data folder, so a reinstall does not download again.

**Building it on this Mac** (needs `data/` and `storage/` with a finished `crag index`):

```bash
brew install uv makensis               # once
packaging/windows/build_on_mac.sh      # makes build/win/BuildMoreAI-Setup.exe
```

The script puts together a Windows app from Python's official Windows package, the Windows versions of every
library (same versions as `.venv`), llama.cpp's `llama-server` (it answers *Ask*, so Ollama is not needed),
the app code, `config/`, `storage/` and `data/`. NSIS then packs it into one installer. The models it downloads
are pinned and checked in `src/concrete_rag/desktop/resources.py`.

---

## Project layout and tech stack

```
config/          settings you can read and edit (sources, DSR mapping, corrections, code tables, wastage)
data/            source PDFs (not in git); data/processed/ has CSV exports for checking
src/concrete_rag/
  ingest/ parse/ chunking/   read PDFs and turn them into chunks
  embed/ store/ retrieval/   search index and database
  calc/                      calculators (mix design, RCC, quantities, steel, rates)
  estimate/                  BOQ, cost heads, Excel / PDF
  agents/ llm/               cited answers from the local AI model
  learn/                     learning loop
  api/                       API and web app
  desktop/                   Windows desktop launcher
storage/         database, search index, learned versions
eval/            test questions and scoring
tests/           unit and integration tests
packaging/       Windows installer build
```

A full folder map is in [docs/technical.md](docs/technical.md).

| Part | Uses |
|---|---|
| Language | Python 3.12 |
| API and web server | FastAPI + Uvicorn, API keys with scopes, rate limits, audit log |
| Web app | Plain HTML, CSS and JavaScript (no framework), blue and white |
| Database | SQLite |
| Reading PDFs | PyMuPDF for digital PDFs, Tesseract OCR for scans |
| Search | bge-m3 meaning search + BM25 keyword search in Qdrant, bge-reranker-v2-m3 to re-order |
| Answers | Local Qwen2.5-3B-Instruct through Ollama (llama.cpp in the desktop app) |
| Export | openpyxl (Excel), reportlab (PDF) |
| Command line | Typer (`crag …`) |
| Tests | pytest |

Nothing is sent to an outside service: documents, questions and answers stay on your computer.

---

## Current status

*Last checked 2026-10-28.*

**Data**

- In the database: IS 456, IS 10262, IS 1200 (Parts 1, 2, 5, 8, 22), IS 2386, the DSR 2023 (3,200 items and
  2,478 basic rates) and the DAR 2023 (3,070 analyses). The search index has 10,281 chunks.
- **Every DSR rate that has a DAR analysis (3,045 items) equals it.** Basic rates and their units equal the DAR
  basic-rate list. The 73 carriage items, typed in by hand, were checked against the basic carriage rates.
- The DSR and basic-rate review queues are empty.
- The history of how the data was read and fixed is in [docs/data-notes.md](docs/data-notes.md).

**Quality**

- Search: the right source is in the top 8 results for 49 of 50 test questions.
- Calculators reproduce the IS 10262 worked examples exactly. IS 456 values were checked against the scans.
- Tests: 74 pass.
- Not measured yet: how close estimates are to real costs (needs data from finished projects).

**Known gaps (for developers)**

- **Mix design is not linked to the BOQ.** When a mix needs more cement than the DSR item's minimum, the DSR pays
  the extra under item 5.35; the estimate does not add it yet.
- **The API outputs are not formal schemas.** `/v1/concrete-mix` and `/v1/estimate` return plain JSON without a
  version, and inputs silently ignore unknown fields (a typo such as `slump` instead of `slump_mm` falls back to
  the default).
- **"Revision" is not in the search index metadata.** It is stored on every DSR row in the database.
- **Some descriptions have OCR misreads** (rates and units are fine): "zone-lll" for "zone-III" (60 items), "½"
  read as "%" or "7" (13 items, for example 4.1.12 "1:2:374:9" = 1:2:3½:9), and stray "_—" at the start of 116
  basic-rate descriptions.
- **Market rates are not loaded.** Re-pricing with them already works (see [Rates](#rates)).
- The Windows app has not yet been run on a Windows PC.

---

## Your Next Steps (Owner Actions)

Only you can do or decide these. Most important first.

### 1. Decide

- [ ] **Estimate defaults:** contingency (now 3 %), any overhead on top of DSR rates, default steel per m³ (or
      always require a BBS), and design mix (now the default) or nominal mix.

### 2. Send me data

- [ ] **The current CPWD cost index for Delhi** over the DSR 2023 base, with its office memo number and date
      (cpwd.gov.in). It goes in `config/cost/cost_indices.yaml` and brings DSR 2023 rates up to today.
- [ ] **Actual costs of finished projects:** copy `eval/datasets/actual_costs_template.csv` to `actual_costs.csv`,
      one row per BOQ item. This lets us measure estimate accuracy (`crag evaluate`) and learn steel figures
      (`crag learn calibrate`).

### 3. Check

- [ ] **Restart after the DAR / M20 fix (2026-10-28):** stop `crag serve`, run `crag index` (the DAR is already
      re-ingested), start `crag serve` again, then commit.
- [ ] **Wastage:** ask a site engineer or QS to look over `config/cost/wastage.yaml` and write their name in
      `approved_by`.
- [ ] **Searched BOQ lines:** when an estimate shows a line marked *searched*, check the item in the DSR and add
      the right code to `config/cost/boq_mapping.yaml`.
- [ ] Click through the web app (hard-refresh with Cmd+Shift+R first) and tell me if anything looks wrong,
      including the footer contact details.
- [ ] Re-run saved estimates that use items 11.1.2, 8.2.3.2, 8.3.2, 8.14.5.2 or basic rate 7773, and RCC designs
      saved before 2026-10-20 (those rates and checks were corrected).

### 4. Keep doing

- [ ] Rate search results as you work: 👍 only the source that really answers the question, 👎 misleading results
      near the top, and type the correct answer on a wrong one. Aim for about 100 rated questions (36 so far).
- [ ] Once a week, open **Learning** and approve good corrections or discard careless ratings.
- [ ] After every 100 or so new ratings: stop `crag serve`, run `crag learn fusion` then `crag learn rerank`, and
      start it again. Each command only switches if the new version scores better.
- [ ] Add test questions from your engineers to `eval/datasets/retrieval_golden.jsonl` (now 50, aim for 150), and
      trusted worked examples (SP 16, past mix designs) to `tests/unit/test_calculators.py`.

### 5. Before other people use it

- [ ] Give each person or system its own API key with only the scopes it needs (`crag apikey revoke <name>` to
      remove one).
- [ ] Leave `CRAG_API_BOOTSTRAP_ADMIN_KEY` empty in `.env`, and put the server behind HTTPS.
- [ ] Back up `storage/`, `config/` and `data/processed/` regularly.

---

## More documentation

- [docs/technical.md](docs/technical.md): folder map, settings, full API list, test scores, LLM choice, learning
  loop details
- [docs/data-notes.md](docs/data-notes.md): what was read from each document, how it was checked, the fix
  history, and known data issues
- [docs/scope.md](docs/scope.md): the original scope and why each technology was chosen
- API docs: `http://127.0.0.1:8000/docs` while `crag serve` is running
