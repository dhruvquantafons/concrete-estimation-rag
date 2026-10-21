"""`crag` command line interface."""
from __future__ import annotations

import json
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Concrete works RAG + estimation toolkit", no_args_is_help=True)
keys_app = typer.Typer(help="Manage API keys")
app.add_typer(keys_app, name="apikey")
learn_app = typer.Typer(help="Learning loop: improve retrieval, answers and estimates from feedback", no_args_is_help=True)
app.add_typer(learn_app, name="learn")
console = Console()


@app.command()
def ingest(only: Optional[list[str]] = typer.Option(None, "--doc", help="doc_id(s) from config/sources.yaml"),
           force_ocr: bool = typer.Option(False, help="ignore the OCR cache"),
           fast: bool = typer.Option(False, help="skip the second (digits-only) OCR reading of SOR rates")):
    """OCR/parse/chunk sources into the docstore and write review CSVs to data/processed/."""
    from concrete_rag.pipeline import ingest as run
    run(only, force_ocr=force_ocr, alt_read=not fast, log=console.print)
    from concrete_rag.store.docstore import counts
    console.print(counts())


@app.command()
def index():
    """Embed all chunks and (re)build the Qdrant hybrid index."""
    from concrete_rag.pipeline import index as run
    run(log=console.print)


@app.command("export-review")
def export_review():
    """Re-write data/processed/*.csv (review queue) from the docstore."""
    from concrete_rag.pipeline import export_processed
    console.print(export_processed())


@app.command()
def search(query: str, k: int = 8, mode: str = "hybrid", no_rerank: bool = False,
           code: Optional[str] = typer.Option(None, help="filter, e.g. 'IS 456'"),
           doc_type: Optional[str] = typer.Option(None, help="is_code | sor | is_amendment")):
    """Run retrieval and print cited results."""
    from concrete_rag.retrieval.hybrid import retrieve
    filters = {k2: v for k2, v in {"code_no": code, "doc_type": doc_type}.items() if v}
    pack = retrieve(query, top_k=k, filters=filters or None, mode=mode, use_rerank=not no_rerank)
    t = Table(show_lines=True)
    for col in ("#", "score", "via", "citation", "text"):
        t.add_column(col)
    for i, c in enumerate(pack.chunks, 1):
        t.add_row(str(i), f"{c.score:.3f}", ",".join(x for x in c.retrieval if not x.startswith("from:")),
                  c.citation.ref + ("" if c.citation.verified else " ⚠"), c.text[:300])
    console.print(t)
    for w in pack.warnings:
        console.print(f"[yellow]warning:[/] {w}")


@app.command()
def status():
    """Docstore / index status."""
    from concrete_rag.store import docstore, vector
    console.print(docstore.counts())
    console.print(vector.collection_info())
    for d in docstore.list_documents():
        console.print(f"  {d['doc_id']}: {json.dumps(d.get('stats'))[:200]}")


@app.command("verify-tables")
def verify_tables():
    """Cross-check curated code tables (config/code_tables) against the OCR'd source text."""
    from concrete_rag.calc.code_tables import verify_all
    rep = verify_all()
    for name, r in rep.items():
        console.print(f"[bold]{name}[/] status={r['status']} matched={r['matched']}/{r['checked']}")
        for m in r["mismatches"][:10]:
            console.print(f"   [yellow]not found in source text:[/] {m}")


@app.command()
def evaluate(k: int = 8, out: Optional[str] = None, quick: bool = False):
    """Retrieval evaluation on eval/datasets/retrieval_golden.jsonl (+ calculator checks)."""
    from concrete_rag.eval.run_eval import run
    rep = run(k=k, quick=quick, log=console.print)
    if out:
        open(out, "w").write(json.dumps(rep, indent=2))


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False):
    """Run the API server."""
    import uvicorn
    uvicorn.run("concrete_rag.api.main:app", host=host, port=port, reload=reload)


@keys_app.command("create")
def key_create(name: str, scopes: str = typer.Option("retrieve,generate,calc,estimate",
                                                     help="comma list: retrieve,generate,calc,estimate,admin")):
    from concrete_rag.api.auth import create_key
    key = create_key(name, [s.strip() for s in scopes.split(",") if s.strip()])
    console.print(f"API key for [bold]{name}[/] (shown once, store it securely):\n{key}")


@keys_app.command("list")
def key_list():
    from concrete_rag.store.docstore import list_api_keys
    for k in list_api_keys():
        console.print(k)


@keys_app.command("revoke")
def key_revoke(name: str):
    from concrete_rag.store.docstore import revoke_api_key
    console.print("revoked" if revoke_api_key(name) else "not found")


@learn_app.command("status")
def learn_status():
    """Feedback collected, live reranker / fusion versions, estimate calibration."""
    from concrete_rag.learn import calibrate, data, state
    console.print(data.summary())
    act = state.active()
    console.print({"reranker": act.get("reranker", {"version": "default"}).get("version"),
                   "fusion": act.get("fusion", {"version": "default"}).get("version"),
                   "fusion_weights": state.fusion_weights()})
    cal = calibrate.load()
    console.print({"calibration": cal.get("status") if cal else "none",
                   "steel_kg_per_m3": {k: v["value"] for k, v in (cal.get("steel_kg_per_m3") or {}).items()}})
    for h in act.get("history", [])[-10:]:
        console.print(f"  {h.get('promoted_on') or h.get('on')}  {h['component']}  {h['action']}  "
                      f"v{h.get('version', h.get('to_version'))}")


@learn_app.command("fusion")
def learn_fusion(no_promote: bool = typer.Option(False, help="report only, do not promote")):
    """Tune the dense / BM25 fusion weights on golden set + 👍 feedback; promote if better.
    Needs the search index: stop `crag serve` first (embedded Qdrant)."""
    from concrete_rag.learn.fusion import tune
    tune(promote=not no_promote, log=console.print)


@learn_app.command("rerank")
def learn_rerank(min_queries: int = typer.Option(30, help="minimum rated queries before training"),
                 epochs: int = 2, no_promote: bool = typer.Option(False, help="train + evaluate only")):
    """Fine-tune the reranker on 👍/👎 feedback; promote only if it beats the live one on held-out feedback
    without losing golden-set quality. Stop `crag serve` first."""
    from concrete_rag.learn.reranker import run
    r = run(min_queries=min_queries, epochs=epochs, promote=not no_promote, log=console.print)
    if not r.get("trained"):
        console.print(f"[yellow]{r['reason']}[/]")


@learn_app.command("rollback")
def learn_rollback(component: str = typer.Argument(..., help="reranker | fusion")):
    """Return a component to its previous version (or the shipped default)."""
    from concrete_rag.learn.state import rollback
    r = rollback(component)
    console.print(f"{component}: now {r['version'] if r else 'default (nothing to roll back)'}")


@learn_app.command("calibrate")
def learn_calibrate(csv: Optional[str] = typer.Option(None, help="default eval/datasets/actual_costs.csv")):
    """Propose steel densities from actual project costs -> config/cost/learned_adjustments.yaml (proposed)."""
    from pathlib import Path

    from concrete_rag.learn.calibrate import calibrate
    r = calibrate(Path(csv) if csv else None)
    for et, v in r["steel_kg_per_m3"].items():
        console.print(f"  {et}: {v['default']} -> [bold]{v['value']}[/] kg/m3 (observed {v['observed']} over "
                      f"{v['projects']} project(s))")
    if not r["steel_kg_per_m3"]:
        console.print("[yellow]no reinforcement rows with element_type + concrete_m3 - nothing to learn[/]")
    console.print("Review config/cost/learned_adjustments.yaml, then: crag learn approve-calibration --by <name>")


@learn_app.command("approve-calibration")
def learn_approve(by: str = typer.Option(..., help="your name")):
    """Let the BOQ use the proposed learned steel densities."""
    from concrete_rag.learn.calibrate import approve
    approve(by)
    console.print("approved - new estimates use the learned values (shown in each estimate's assumptions)")


@learn_app.command("export-preferences")
def learn_export_preferences():
    """Write approved 👎 answer + correction pairs to data/processed/preferences.jsonl (for DPO fine-tuning)."""
    from concrete_rag.learn.exemplars import export_preferences
    console.print(export_preferences())


if __name__ == "__main__":
    app()
