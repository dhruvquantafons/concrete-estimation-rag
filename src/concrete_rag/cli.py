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


if __name__ == "__main__":
    app()
