"""Which learned components are live: storage/learned/active.json.

{"reranker": {"version": 2, "path": "storage/models/reranker-v2", "promoted_on": ..., "metrics": {...}},
 "fusion":   {"version": 1, "weights": {"dense": 1.25, "bm25": 1.0}, ...},
 "history":  [every promotion / rollback, newest last]}

A missing component means the shipped default (settings.rerank_model, equal RRF weights). Retrieval reads
this file on every query (cached by mtime), so a promotion or rollback takes effect without a restart.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
from pathlib import Path

from concrete_rag.config import get_settings

DEFAULT_WEIGHTS = {"dense": 1.0, "bm25": 1.0}
_cache: dict = {"mtime": None, "data": {}}
_override: dict = {}


def learned_dir() -> Path:
    d = get_settings().storage_dir / "learned"
    d.mkdir(parents=True, exist_ok=True)
    return d


def models_dir() -> Path:
    d = get_settings().storage_dir / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path() -> Path:
    return learned_dir() / "active.json"


def active() -> dict:
    p = _path()
    if not p.exists():
        return {}
    m = p.stat().st_mtime
    if _cache["mtime"] != m:
        _cache["data"], _cache["mtime"] = json.loads(p.read_text()), m
    return _cache["data"]


def _save(data: dict) -> None:
    p = _path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(p)


def reranker_path() -> str | None:
    """Path of the promoted fine-tuned reranker, or None for the shipped model."""
    if "reranker" in _override:
        return _override["reranker"]
    return (active().get("reranker") or {}).get("path")


def fusion_weights() -> dict[str, float]:
    if "fusion" in _override:
        return _override["fusion"]
    return (active().get("fusion") or {}).get("weights") or DEFAULT_WEIGHTS


@contextlib.contextmanager
def override(**components):
    """Evaluate a candidate without promoting it, e.g. override(reranker="storage/models/reranker-v3")."""
    old = dict(_override)
    _override.update(components)
    try:
        yield
    finally:
        _override.clear()
        _override.update(old)


def next_version(component: str) -> int:
    hist = [h for h in active().get("history", []) if h["component"] == component]
    return max([h.get("version", 0) for h in hist if isinstance(h.get("version"), int)] + [0]) + 1


def promote(component: str, entry: dict) -> dict:
    data = dict(active())
    entry = {**entry, "promoted_on": dt.datetime.now().isoformat(timespec="seconds")}
    data[component] = entry
    data.setdefault("history", []).append({"component": component, "action": "promote", **entry})
    _save(data)
    return entry


def rollback(component: str) -> dict | None:
    """Return to the previously promoted version (or the shipped default if there was none)."""
    data = dict(active())
    cur = data.get(component)
    if not cur:
        return None
    promos = [h for h in data.get("history", []) if h["component"] == component and h["action"] == "promote"]
    earlier = [h for h in promos if h.get("version", 0) < cur.get("version", 0)]
    prev = {k: v for k, v in earlier[-1].items() if k not in ("component", "action")} if earlier else None
    if prev:
        data[component] = prev
    else:
        data.pop(component, None)
    data.setdefault("history", []).append({"component": component, "action": "rollback",
                                           "from_version": cur.get("version"),
                                           "to_version": prev.get("version") if prev else "default",
                                           "on": dt.datetime.now().isoformat(timespec="seconds")})
    _save(data)
    return prev or {"version": "default"}
