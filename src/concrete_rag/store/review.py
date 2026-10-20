"""Human review workflow used by the UI.

Corrections are (1) persisted to config/corrections/<sor>_<year>.yaml so they survive re-ingest, and
(2) applied immediately to the docstore (and the search-index payload), so the API serves the corrected
value without waiting for a re-ingest. Code-table sign-offs edit `verified_by` / `verified_on` in
config/code_tables/*.yaml in place (comments are preserved: only those two lines change)."""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import yaml
from sqlalchemy import func, select, update

from concrete_rag.config import get_settings
from concrete_rag.store import docstore
from concrete_rag.store.docstore import basic_rates, chunks, engine, sor_items


# ------------------------------------------------------------------ review queue
def list_sor_review(q: str | None = None, head: int | None = None, offset: int = 0, limit: int = 50,
                    include_done: bool = False) -> dict:
    with engine().connect() as c:
        cond = [] if include_done else [sor_items.c.needs_review.is_(True)]
        if head:
            cond.append(sor_items.c.code.like(f"{head}.%"))
        if q:
            for w in q.split():
                cond.append(sor_items.c.full_description.ilike(f"%{w}%") | sor_items.c.code.like(f"{w}%"))
        total = c.execute(select(func.count()).select_from(sor_items).where(*cond)).scalar_one()
        rows = c.execute(select(sor_items).where(*cond).order_by(sor_items.c.doc_id, sor_items.c.page)
                         .offset(offset).limit(limit)).all()
    return {"total": total, "rows": [dict(r.data) for r in rows]}


def list_basic_review(q: str | None = None, category: str | None = None, offset: int = 0, limit: int = 50) -> dict:
    with engine().connect() as c:
        cond = [basic_rates.c.needs_review.is_(True)]
        if category:
            cond.append(basic_rates.c.category == category)
        if q:
            for w in q.split():
                cond.append(basic_rates.c.description.ilike(f"%{w}%") | basic_rates.c.code.like(f"{w}%"))
        total = c.execute(select(func.count()).select_from(basic_rates).where(*cond)).scalar_one()
        rows = c.execute(select(basic_rates).where(*cond).order_by(basic_rates.c.code).offset(offset).limit(limit)).all()
    return {"total": total, "rows": [{**dict(r.data), "db_key": r.key} for r in rows]}


# ------------------------------------------------------------------ corrections file
def corrections_path(sor_name: str = "CPWD-DSR", year: int = 2023) -> Path:
    return get_settings().root / "config" / "corrections" / f"{sor_name.lower().replace('-', '_')}_{year}.yaml"


def _write_correction(key: str, entry: dict, path: Path) -> None:
    """Insert or replace one `"key": {...}` line under `items:` without touching other lines/comments."""
    text = path.read_text() if path.exists() else "items:\n"
    if not re.search(r"^items:\s*$", text, re.M):
        text = text.rstrip("\n") + "\nitems:\n"
    # one physical line per entry (no wrapping), so a later replace never leaves continuation lines behind
    flow = yaml.safe_dump(entry, default_flow_style=True, sort_keys=False, allow_unicode=True, width=2**31 - 1).strip()
    line = f'  "{key}": {flow}'
    # the entry may span several lines if it was wrapped by an older writer or by hand: replace them all
    pat = re.compile(rf'^[ \t]*"{re.escape(key)}"[ \t]*:.*(?:\n[ \t]{{4,}}\S.*)*$', re.M)
    if pat.search(text):
        text = pat.sub(lambda _: line, text, count=1)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text)


def _prior_entry(key: str, path: Path) -> dict:
    """Value fields of an existing correction, so a re-save that changes only the rate keeps an earlier code /
    unit / description fix (the row already shows the corrected values, so they would not look 'changed')."""
    items = ((yaml.safe_load(path.read_text()) or {}).get("items") or {}) if path.exists() else {}
    return {k: v for k, v in (items.get(key) or {}).items() if k in ("rate", "unit", "code", "description", "note")}


def list_corrections() -> dict:
    p = corrections_path()
    return (yaml.safe_load(p.read_text()) or {}).get("items", {}) if p.exists() else {}


def apply_sor_correction(item_key: str, checked_by: str, rate: float | None = None, unit: str | None = None,
                         code: str | None = None, note: str | None = None, exclude: bool = False,
                         description: str | None = None) -> dict:
    with engine().connect() as c:
        row = c.execute(select(sor_items).where(sor_items.c.item_key == item_key)).first()
    if not row:
        raise KeyError(f"SOR item {item_key} not found")
    data = dict(row.data)
    if exclude:   # OCR artefact (table header, note fragment) - not a real SOR item
        entry = {"exclude": True, "checked_by": checked_by, "checked_on": dt.date.today().isoformat()}
        if note:
            entry["note"] = note
        _write_correction(item_key, entry, corrections_path(data["sor_name"], data["sor_year"]))
        _delete_item(row.doc_id, item_key)
        return {**data, "excluded": True}
    path = corrections_path(data["sor_name"], data["sor_year"])
    entry: dict = _prior_entry(item_key, path)
    if rate is not None:
        entry["rate"] = round(float(rate), 2)
    if unit:
        entry["unit"] = unit
    if code and code != data["code"]:
        entry["code"] = code
    if description:
        entry["description"] = description
    entry["checked_by"] = checked_by
    entry["checked_on"] = dt.date.today().isoformat()
    if note:
        entry["note"] = note
    _write_correction(item_key, entry, path)

    if entry.get("description"):
        data["full_description"] = " ".join(filter(None, [data.get("parent_description"), entry["description"]]))
    data.update({k: v for k, v in (("rate", entry.get("rate")), ("unit", unit), ("code", entry.get("code")),
                                   ("description", entry.get("description"))) if v})
    data.update({"needs_review": False, "review_reasons": [], "corrected_by": checked_by,
                 "corrected_on": entry["checked_on"]})
    with engine().begin() as c:
        c.execute(update(sor_items).where(sor_items.c.item_key == item_key).values(
            code=data["code"], unit=data["unit"], rate=data["rate"], needs_review=False, data=data))
    _refresh_item_chunk(row.doc_id, item_key, data)
    return data


def _delete_item(doc_id: str, item_key: str) -> None:
    from sqlalchemy import delete
    cid = f"{doc_id}:item:{item_key.split(':', 1)[1]}"
    with engine().begin() as c:
        c.execute(delete(sor_items).where(sor_items.c.item_key == item_key))
        c.execute(delete(chunks).where(chunks.c.chunk_id == cid))
    try:
        from qdrant_client import models

        from concrete_rag.store import vector
        vector.client().delete(get_settings().qdrant_collection,
                               points_selector=models.PointIdsList(points=[vector.point_id(cid)]))
    except Exception:  # noqa: BLE001
        pass


def _refresh_item_chunk(doc_id: str, item_key: str, it: dict) -> None:
    cid = f"{doc_id}:item:{item_key.split(':', 1)[1]}"
    rate_txt = f"₹ {it['rate']:,.2f} per {it.get('unit') or it.get('unit_raw')}" if it.get("rate") is not None \
        else "rate not available"
    text = (f"{it['sor_name']} {it['sor_year']} item {it['code']} ({it.get('sub_head') or ''})\n"
            f"{it['full_description']}\nUnit: {it.get('unit') or it.get('unit_raw')} | Rate: {rate_txt}")
    with engine().begin() as c:
        r = c.execute(select(chunks.c.meta).where(chunks.c.chunk_id == cid)).first()
        if not r:
            return
        meta = dict(r.meta)
        meta.update({"rate": it["rate"], "unit": it.get("unit"), "item_code": it["code"], "needs_review": False,
                     "review_reasons": []})
        c.execute(update(chunks).where(chunks.c.chunk_id == cid).values(text=text, meta=meta, needs_review=False))
    try:   # keep the search-index payload in sync (dense/BM25 vectors are unchanged until the next `crag index`)
        from concrete_rag.store import vector
        vector.client().set_payload(get_settings().qdrant_collection,
                                    payload={**meta, "text": text, "chunk_id": cid},
                                    points=[vector.point_id(cid)])
    except Exception:  # noqa: BLE001
        pass


def apply_basic_correction(db_key: str, checked_by: str, rate: float | None = None, unit: str | None = None,
                           note: str | None = None, exclude: bool = False, code: str | None = None,
                           description: str | None = None) -> dict:
    with engine().connect() as c:
        row = c.execute(select(basic_rates).where(basic_rates.c.key == db_key)).first()
    if not row:
        raise KeyError(f"basic rate {db_key} not found")
    data = dict(row.data)
    # the parser looks corrections up by the row's OCR key ("#n" for repeated codes), which stays the same after
    # a code correction renames the row
    corr_key = data.get("ocr_key") or db_key
    if exclude:
        _write_correction(corr_key, {"exclude": True, "checked_by": checked_by,
                                     "checked_on": dt.date.today().isoformat()},
                          corrections_path(data["sor_name"], data["sor_year"]))
        from sqlalchemy import delete
        with engine().begin() as c:
            c.execute(delete(basic_rates).where(basic_rates.c.key == db_key))
        return {**data, "excluded": True}
    path = corrections_path(data["sor_name"], data["sor_year"])
    entry: dict = _prior_entry(corr_key, path)
    if rate is not None:
        entry["rate"] = round(float(rate), 2)
    if unit:
        entry["unit"] = unit
    if description:
        entry["description"] = description
    code = (code or "").strip()
    if code and code != data["code"]:
        if not code.isdigit():
            raise ValueError(f"basic-rate code must be digits, got {code!r}")
        entry["code"] = code
    if data.get("ocr_key") and "code" not in entry:   # a renamed row keeps its corrected code on re-ingest
        entry["code"] = data["code"]
    entry["checked_by"] = checked_by
    entry["checked_on"] = dt.date.today().isoformat()
    if note:
        entry["note"] = note
    _write_correction(corr_key, entry, path)
    data.update({k: v for k, v in (("rate", entry.get("rate")), ("unit", entry.get("unit")),
                                   ("description", entry.get("description"))) if v})
    data.update({"needs_review": False, "review_reasons": [], "corrected_by": checked_by})
    new_key = db_key
    if entry.get("code") and entry["code"] != data["code"]:      # rename the row only when its code changes
        new_key = f"{data['sor_name']}-{data['sor_year']}:BR:{entry['code']}"
        with engine().connect() as c:
            if c.execute(select(basic_rates.c.key).where(basic_rates.c.key == new_key)).first():
                new_key += "#2"
        data.update({"code": entry["code"], "key": new_key.split("#")[0], "ocr_key": corr_key})
    with engine().begin() as c:
        c.execute(update(basic_rates).where(basic_rates.c.key == db_key).values(
            key=new_key, code=data["code"], description=data["description"], rate=data["rate"], unit=data["unit"],
            needs_review=False, data=data))
    return {**data, "db_key": new_key}


# ------------------------------------------------------------------ code tables sign-off
def code_tables_overview() -> list[dict]:
    from concrete_rag.calc import code_tables as ct
    ct.tables.cache_clear()
    ver = ct.verify_all()
    out = []
    for name, t in ct.tables().items():
        src = t.get("source", {})
        page = None
        if src.get("chunk"):
            ch = docstore.get_chunks([src["chunk"]]).get(src["chunk"])
            if ch:
                page = {"doc_id": ch["metadata"]["doc_id"], "page": ch["metadata"]["page_start"]}
        out.append({"name": name, "title": t.get("title"), "source": src, "source_page": page,
                    "verified_by": t.get("verified_by"), "verified_on": t.get("verified_on"),
                    "verification": ver.get(name, {}),
                    "values": {k: v for k, v in t.items() if k not in ("source", "title", "verified_by", "verified_on")}})
    return out


def sign_off_table(name: str, verified_by: str) -> dict:
    from concrete_rag.calc import code_tables as ct
    folder = get_settings().root / "config" / "code_tables"
    for f in sorted(folder.glob("*.yaml")):
        text = f.read_text()
        m = re.search(rf"^{re.escape(name)}:\s*$", text, re.M)
        if not m:
            continue
        nxt = re.search(r"^\S", text[m.end():], re.M)
        end = m.end() + (nxt.start() if nxt else len(text) - m.end())
        block = text[m.end():end]
        block = re.sub(r"^(\s+)verified_by:.*$", lambda g: f'{g.group(1)}verified_by: "{verified_by}"', block,
                       count=1, flags=re.M)
        block = re.sub(r"^(\s+)verified_on:.*$", lambda g: f'{g.group(1)}verified_on: "{dt.date.today().isoformat()}"',
                       block, count=1, flags=re.M)
        f.write_text(text[:m.end()] + block + text[end:])
        ct.tables.cache_clear()
        ct._verification.cache_clear()
        return {"name": name, "verified_by": verified_by, "file": f.name}
    raise KeyError(f"code table '{name}' not found")
