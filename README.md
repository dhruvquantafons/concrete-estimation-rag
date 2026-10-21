# Concrete Estimator

A tool for concrete works in India. It does three things:

1. **Finds answers in the standards.** Search IS 456, IS 10262, IS 1200, IS 2386 and the CPWD Delhi Schedule of
   Rates (DSR) 2023. Every answer shows where it came from, down to the clause and the scanned page.
2. **Does the engineering calculations.** Mix design, RCC design (beam, slab, column, footing), quantities and
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
| **RCC design** | Design a beam, one-way slab, column or footing (IS 456) and see which checks pass |
| **Estimate / BOQ** | Enter elements → quantities (IS 1200) → priced BOQ, cement statement, Excel / PDF |
| **DSR rates** | Look up any of the 2,955 DSR items by word or code |
| **Code tables** | See the IS values the calculators use, compare them with the scan, sign them off |
| **Review queue** *(admin)* | Fix DSR rows the scanner was unsure about, next to the scanned page |
| **Learning** *(admin)* | Approve or discard ratings, and see what the system has learned |

What you see depends on your key's permissions (scopes).

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

## Where things stand

- **Data:** complete for this project: IS 456, IS 10262, IS 1200, IS 2386 and both DSR 2023 volumes. No more
  documents are planned. The review queue is empty, and about 770 scanner errors were checked against the scanned
  pages and fixed. Rates are for **Delhi** (DSR base); other locations are refused rather than guessed.
- **Search quality:** the right source is in the top 8 results for all 50 test questions, and first for most of
  them.
- **Calculators:** reproduce the IS 10262 worked example exactly. All IS 456 values were checked against the
  scanned pages.
- **Tests:** 33 pass.
- **Not measured yet:** how close estimates are to real costs (needs your project data).

More detail: [docs/data-notes.md](docs/data-notes.md) (what was read, fixed and still known to be wrong) and
[docs/technical.md](docs/technical.md) (how it works, API, settings, test scores, LLM choice).

---

## Your Next Steps (Owner Actions)

In order of priority. Only you can do or decide these.

### 1. Right now
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
- [ ] Open **Code tables**, press **Compare with page** on two or three tables, and sign them in your own name if
      you like.
- [ ] Check a few points of IS 10262 **Fig. 1** against your printed copy. It was measured from the PDF image.
- [ ] Look through a few DSR fixes in `config/corrections/cpwd_dsr_2023.yaml` against the printed DSR.

### 4. Give it real test data
- [ ] **Actual costs of finished projects:** copy `eval/datasets/actual_costs_template.csv` to
      `actual_costs.csv`, one row per BOQ item. For steel rows, also fill `element_type` and `concrete_m3`. Then
      run `crag evaluate` (shows how far estimates were off) and `crag learn calibrate`.
- [ ] More test questions from your engineers: grow `eval/datasets/retrieval_golden.jsonl` from 50 to about 150,
      and add 20–30 questions with expected answers to `generation_golden.jsonl`.
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
