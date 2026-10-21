# Technical reference

For developers and anyone connecting another system. For the overview, see the [README](../README.md);
for the data history, see [data-notes.md](data-notes.md); for the original scope and technology choices, see
[scope.md](scope.md).

## Folder map

```
config/
  sources.yaml            every source document (edition, amendments, OCR settings)
  sor/cpwd_dsr_2023.yaml  DSR sub-heads, used by the SOR parser
  code_tables/*.yaml      IS 456 / IS 10262 values used by the calculators, with sign-off
  cost/*.yaml             BOQ → DSR item mapping, location cost indices, rate analysis, estimate defaults,
                          learned_adjustments.yaml (estimate calibration, once approved)
  corrections/*.yaml      checked fixes to OCR'd DSR rows (re-applied on every ingest)
  vocab/enrichment.yaml   word lists for tagging chunks (material, work item, element)
src/concrete_rag/
  ingest/     read PDF pages: text layer or OCR (cached), two-column reading order
  parse/      is_code.py (clauses, tables, annexes, amendments, cross-references)
              sor.py (DSR items, basic rates, cement coefficients)
  normalize/  units, ₹ / lakh / crore, DSR item-code repair
  chunking/   split into clause / table / item chunks and tag them
  store/      docstore.py (SQLite or Postgres: the source of truth), vector.py (Qdrant), review.py (UI fixes)
  embed/      bge-m3 embeddings, BM25 tokenizer that keeps codes like 26.5.1.1 and M25 whole
  retrieval/  exact-reference router → hybrid search → reranker → cross-references and amendments
  calc/       mix design (IS 10262), RCC design (IS 456), steel / BBS, quantities (IS 1200), rates
  estimate/   BOQ, cement statement, cost split, Excel and PDF export
  llm/        LLM providers: fake (offline), anthropic (Claude), openai_compat (Ollama, vLLM)
  agents/     cited answers with a number check; tools for the experimental agent
  learn/      learning loop (feedback → tuning → test → promote / roll back)
  api/        FastAPI app, API keys with scopes, rate limit, audit log, web UI
  eval/       search scores, answer checks, calculator checks, estimate variance
eval/datasets/   retrieval_golden.jsonl (50 questions), generation_golden.jsonl, actual_costs_template.csv
storage/         docstore.db, qdrant/ (search index), learned/ (live versions), models/ (fine-tuned rerankers)
```

## How a search works

1. **Exact references first.** "cl. 26.4.2", "Table 16" or "DSR 5.33.2.1" are looked up directly.
2. **Hybrid search.** Meaning-based search (bge-m3) and keyword search (BM25) each return 50 candidates, merged
   by reciprocal-rank fusion. The weighting between the two is tuned by `crag learn fusion`.
3. **Reranking.** A cross-encoder (bge-reranker-v2-m3, or a fine-tuned version) re-orders the top 24.
4. **Expansion.** Clauses the result refers to, and amendments that change it, are attached.

Every result carries a citation (code, clause / table / item, PDF page) and a flag if its OCR is unchecked.

**Answers (Ask)** are written by the LLM from the numbered sources only, and must cite them as [S1], [S2]…
Every number in the answer is checked against the sources. A number that is not found is reported as
"ungrounded", never silently fixed.

## Settings

All settings are environment variables starting with `CRAG_`, read from `.env`. The full list is in
`src/concrete_rag/config.py`. The important ones:

| Setting | Default | Meaning |
|---|---|---|
| `CRAG_LLM_PROVIDER` | `fake` | `fake` (no LLM), `anthropic`, or `openai_compat` (Ollama) |
| `CRAG_LLM_MODEL`, `CRAG_LLM_BASE_URL` | | model name and server address for the LLM |
| `CRAG_RERANK_ENABLED` | `true` | turn the reranker off for speed (0.13 s instead of 1.9 s per search) |
| `CRAG_RERANK_POOL` | `24` | how many candidates the reranker looks at |
| `CRAG_DOCSTORE_URL`, `CRAG_QDRANT_URL` | empty | empty means local files in `storage/`; set them for Postgres / a Qdrant server |
| `CRAG_API_BOOTSTRAP_ADMIN_KEY` | empty | a fixed admin key for development only; keep empty in production |
| `CRAG_API_CORS_ORIGINS` | empty | websites allowed to call the API from a browser |

The local search index (embedded Qdrant) can be used by **one process at a time.** Stop `crag serve` before
running `crag ingest`, `crag index`, `crag learn fusion|rerank` or the integration tests, or run a Qdrant server
(`make up`, then set `CRAG_QDRANT_URL`).

## API

Every call needs an `X-API-Key` header. Each key has scopes; `admin` can do everything. Interactive docs are at
`http://127.0.0.1:8000/docs` while the server runs.

| Endpoint | Scope | What it does |
|---|---|---|
| `POST /v1/retrieve` | retrieve | search; returns cited chunks, tables as JSON, DSR rates, and a `query_id` |
| `POST /v1/generate` | generate | cited answer with number check; returns a `query_id` |
| `POST /v1/agent` | generate | LLM with tools (search, calculators, estimate). **Experimental:** small local models get multi-step tasks wrong |
| `POST /v1/mix-design` | calc | IS 10262 mix proportioning |
| `POST /v1/design/{beam,slab,column,footing}` | calc | IS 456 design with checks and clause references |
| `POST /v1/quantity`, `POST /v1/bbs` | calc | IS 1200 quantities, bar bending schedule |
| `GET /v1/rates/{code}?location=` | estimate | DSR rate × location index, with citation and OCR check status |
| `POST /v1/estimate` | estimate | BOQ, cost summary, cement statement |
| `GET /v1/estimate/{id}/export?format=xlsx\|pdf` | estimate | Excel (with live formulas) or PDF |
| `GET /v1/code-tables/status` | calc | sign-off status of the code tables |
| `POST /v1/feedback` | retrieve / generate | rate a result (`kind: chunk`) or an answer (`kind: answer`, optional `correction`), using the `query_id` |
| `/v1/admin/*` | admin | documents, review queue, re-ingest, feedback approve / discard, learning status |

Example estimate:

```bash
curl -s -X POST localhost:8000/v1/estimate -H "X-API-Key: $KEY" -H 'Content-Type: application/json' -d '{
  "project": "Block A frame", "location": "delhi", "mix_type": "design_mix",
  "elements": [
    {"id":"F1","type":"footing","length_m":1.6,"width_m":1.6,"depth_m":0.45,"count":8,"level":"below_plinth","grade":"M25"},
    {"id":"C1","type":"column","length_m":3.2,"width_m":0.30,"depth_m":0.45,"count":8,"grade":"M25"},
    {"id":"B1","type":"beam","length_m":4.55,"width_m":0.23,"depth_m":0.33,"count":10,"grade":"M25"},
    {"id":"S1","type":"slab","length_m":10,"width_m":8,"depth_m":0.125,"grade":"M25"}]}'
```

## Test results

`crag evaluate` writes a full report to `eval/reports/`. Search is scored on 50 questions written against the real
documents, looking at the top 8 results.

| Search method | Recall@8 | Hit@8 | MRR | nDCG@8 | Seconds per search (M4) |
|---|---|---|---|---|---|
| Keyword only (BM25) | 0.92 | 0.94 | 0.800 | 0.825 | 0.08 |
| Meaning only (bge-m3) | 0.93 | 0.96 | 0.790 | 0.813 | 0.25 |
| Hybrid | 0.97 | 1.00 | 0.807 | 0.840 | 0.13 |
| **Hybrid + reranker (default)** | **0.97** | **1.00** | **0.823** | **0.861** | 1.9 |

- *Recall*: share of the right sources found in the top 8. *Hit*: at least one right source in the top 8.
  *MRR / nDCG*: how high the right source ranks (1.0 = always first).
- **Calculators:** the IS 10262 Annex A worked example is reproduced (cement 412, water 148, fine aggregate 648.6,
  coarse aggregate 1234.6 kg/m³). An M25 mix, the demo BOQ and the IS 456 tables were also checked by hand.
- **Tests:** 29 unit tests and 4 integration tests pass (`pytest -q`; the integration tests need the server stopped).
- **Answers:** the 6-question answer set passes the number check 6/6; 5/6 contain the expected fact. The
  "judge" score (a small LLM rating faithfulness) is unreliable at 3B size; treat it as a rough signal.
- **Estimate accuracy** is not measured yet. It needs actual project costs in `eval/datasets/actual_costs.csv`.

## LLM choice

Local **Qwen2.5-3B-Instruct** (Q4_K_M, 1.9 GB) through Ollama. In `.env`:

```
CRAG_LLM_PROVIDER=openai_compat
CRAG_LLM_BASE_URL=http://localhost:11434/v1
CRAG_LLM_MODEL=hf.co/bartowski/Qwen2.5-3B-Instruct-GGUF:Q4_K_M
```

Benchmark on this Mac (6 questions answered from the IS codes / DSR, plus 2 multi-step agent tasks):

| Model (4-bit) | Size | Correct fact | Numbers found in sources | Average time | Agent tasks |
|---|---|---|---|---|---|
| **Qwen2.5-3B-Instruct** (selected) | 1.9 GB | 6/6 | 6/6 | 10 s | 0/2 |
| Qwen3-4B-Instruct-2507 | 2.0 GB | 6/6 | 5/6 | 18 s | 0/2 |
| Llama-3.2-3B-Instruct | 2.0 GB | 4/6 | 6/6 | 9 s | 0/2 |
| Qwen2.5-7B-Instruct (reference) | 4.7 GB | 6/6 | 6/6 | 17 s | 0/2 |

No model of this size completes the agent tasks, so `/v1/agent` stays experimental. To switch models, change
`CRAG_LLM_MODEL` and restart the server. Qwen3 models on Ollama also need `CRAG_LLM_REASONING_EFFORT=none`.

## Learning loop details

| What learns | From | How it is tested before going live |
|---|---|---|
| Search weighting (meaning vs keyword) | 👍 sources and the golden questions | 6 weightings are tried on logged questions (an offline "bandit"). The best is kept only if it beats the current one and finds at least as many golden sources |
| Reranker | 👍 / 👎 sources | Retrained from the original model on all feedback (at least 30 rated questions). 20% of rated questions are held back. It goes live only if it ranks those better and the golden scores do not drop by more than 0.01 |
| Answers | approved 👍 answers and engineers' corrections | No training: approved answers are shown to the LLM as examples for similar questions (similarity ≥ 0.80). Numbers must still come from the sources |
| Steel kg/m³ for estimates | actual project costs | learned = (3 × default + n × observed) / (3 + n), where n is the number of projects. Used only after `approve-calibration` |

- Live versions: `storage/learned/active.json`. Fine-tuned rerankers: `storage/models/reranker-vN`.
  A promotion or rollback works without restarting the server.
- Discarded feedback is never used. Ratings come with the API key that gave them (`key_name`).
- `crag learn export-preferences` writes `data/processed/preferences.jsonl` (question, rejected answer, corrected
  answer, source ids), ready for a later DPO fine-tune of the local model. That training step is not built yet.
- Textbook reinforcement learning is not used on purpose: there is no safe place to practise, the real reward
  (actual cost) arrives months later, and an estimating tool must stay auditable.
