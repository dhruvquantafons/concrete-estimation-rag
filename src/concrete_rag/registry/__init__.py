"""Source registry: loads config/sources.yaml and keeps a hash manifest so every
processed artefact can be traced back to the exact file version it came from."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

import yaml

from concrete_rag.config import get_settings
from concrete_rag.schemas import SourceDoc


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load_sources(only: list[str] | None = None) -> list[SourceDoc]:
    s = get_settings()
    cfg = yaml.safe_load(s.sources_file.read_text())
    defaults = cfg.get("defaults", {})
    docs = []
    for entry in cfg["documents"]:
        doc = SourceDoc(**{**defaults, **entry})
        if only and doc.doc_id not in only:
            continue
        docs.append(doc)
    return docs


def manifest_path() -> Path:
    return get_settings().processed_dir / "manifest.json"


def load_manifest() -> dict:
    p = manifest_path()
    return json.loads(p.read_text()) if p.exists() else {}


def update_manifest(docs: list[SourceDoc]) -> dict:
    """Record sha256 + size of each source; flags files that changed since last ingest."""
    s = get_settings()
    man = load_manifest()
    for d in docs:
        path = s.root / d.path
        if not path.exists():
            raise FileNotFoundError(f"{d.doc_id}: {path} not found (check config/sources.yaml)")
        digest = sha256_file(path)
        d.sha256 = digest
        prev = man.get(d.doc_id)
        history = prev.get("history", []) if prev else []
        if not prev or prev["sha256"] != digest:
            history.append({"sha256": digest, "seen": dt.datetime.now().isoformat(timespec="seconds")})
        man[d.doc_id] = {
            "path": d.path,
            "sha256": digest,
            "bytes": path.stat().st_size,
            "revision_year": d.revision_year,
            "history": history,
        }
    manifest_path().write_text(json.dumps(man, indent=2))
    return man
