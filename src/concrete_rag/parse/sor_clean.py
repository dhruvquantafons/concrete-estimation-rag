"""Reader for clean, single-language copies of the CPWD DSR (text layer, or OCR of sharp vector pages).

The old reader (parse/sor.py) was built for 96-dpi bilingual scans and needs three OCR readings per rate.
A clean copy is read directly from its columns, found on every page from the header row:

    Code No |            Description              | Unit |   Rate
    13.1     12 mm cement plaster of mix :                              <- parent (no rate)
             13.1.1   1:4 (1 cement: 4 fine sand)   sqm      347.05     <- item
    CEMENT PLASTER (IN FINE SAND)                                       <- group heading
    Note :- ...                                                         <- note

Codes sit at several indents; a code is the first word of a row, belongs to the page's sub-head and is followed
by a gap. Descriptions wrap over several rows and the rate sits on the last one. Items whose "rate" is a
percentage (e.g. 2.24.x: extra 20 % per metre depth) get no rupee rate; the percentage stays in the description.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace

import yaml

from concrete_rag.config import get_settings
from concrete_rag.normalize.units import normalize_unit
from concrete_rag.parse.dar import Row, rows_of
from concrete_rag.normalize.item_codes import parent_code
from concrete_rag.parse.sor import SorParseResult, _dmy, load_corrections, load_sor_config
from concrete_rag.schemas import BasicRate, PageText, SorItem, SourceDoc

CODE = re.compile(r"^\d{1,2}(?:\.(?:\d{1,3}[A-Z]?|[A-Z]\d{1,2})){1,4}$")       # 5.33A.2.1, 9.147.A1.1
BASIC_CODE = re.compile(r"^\d{4}$")
RATE = re.compile(r"^\d[\d,]*\.\d{2}$")
PCT = re.compile(r"^\d+(?:\.\d+)?\s*%$")
FOOT = re.compile(r"SUB\s*HEAD\s*:?\s*(\d{1,2})\s*\.?\s*0?\s+([A-Z][A-Z &,()./-]+)")
TITLE = re.compile(r"^(\d{1,2})\.0\s+[A-Z][A-Z &,()./-]+\n")


@dataclass
class Cols:
    code: float = 58.0
    unit: float = 437.0
    rate: float = 489.0
    remarks: float | None = None       # sub-head 1 (carriage) has a Remarks column right of the rate
    margin: float | None = None        # justified right edge of the description column


NOISE = {"|", "_", "—", "-", "–", "`", "'"}


def page_cols(rows: list[Row], prev: Cols) -> tuple[Cols, float]:
    """Column positions from the header row ("Code No | Description | Unit | Rate"), and the header's y."""
    for r in rows[:15]:
        names: dict[str, float] = {}
        for x, t, _ in r.words:                          # first occurrence: "... per unit quantity" repeats "unit"
            names.setdefault(t.strip(".:").lower(), x)
        if "code" in names and "unit" in names and ({"rate", "basic", "quantity"} & set(names)):
            val = names.get("rate", names.get("basic", names.get("quantity")))
            cols = Cols(code=names["code"], unit=names["unit"], rate=val, remarks=names.get("remarks"))
            return replace(cols, margin=_desc_margin(rows, cols)), r.y
    return replace(prev, margin=_desc_margin(rows, prev)), 0.0


def _desc_margin(rows: list[Row], cols: Cols) -> float | None:
    """Justified description text ends on one x: the most common word end just left of the unit column."""
    ends = Counter(round(x1) for r in rows for _, _, x1 in r.words if cols.unit - 60 < x1 < cols.unit - 5)
    return float(ends.most_common(1)[0][0]) if ends else None


@dataclass
class Rec:
    code: str
    page: int
    desc: list[str] = field(default_factory=list)
    unit: list[str] = field(default_factory=list)
    rate: str | None = None
    pct: str | None = None
    group: str | None = None


def _cells(r: Row, cols: Cols) -> tuple[list[str], list[str], str | None, str | None]:
    """(description words, unit words, rate, percentage) of one row (numbers placed by their centre).

    A unit cell may start only a few points right of the justified description ("compound | depth per"), so a word
    is in the unit column when it does not fit inside the description column. A note running across the page
    (a word crossing the description margin, or normal word spacing past it) stays description."""
    desc, unit, rate, pct = [], [], None, None
    margin = cols.margin or cols.unit - 20
    crosses = any(x < cols.unit - 30 and x1 > margin + 4 for x, _, x1 in r.words)
    prev_x1, prev_desc, running = None, False, crosses
    for x, t, x1 in r.words:
        mid = (x + x1) / 2
        if cols.remarks and x >= (cols.rate + cols.remarks) / 2 - 10:
            continue                                     # remarks column (its header word is centred)
        running = running or bool(prev_desc and x >= margin and x - prev_x1 < 5)
        in_desc = x1 <= margin + 1 and x < margin - 1
        is_desc = False
        if mid >= cols.rate - 15 and RATE.match(t.replace(",", "")):
            rate = t.replace(",", "")
        elif mid >= cols.unit - 25 and PCT.match(t):
            pct = t.replace(" ", "")
        elif not running and not in_desc and x >= min(margin - 2, cols.unit - 25):
            unit.append(t)
        else:
            desc.append(t)
            is_desc = True
        prev_x1, prev_desc = x1, is_desc
    return desc, unit, rate, pct


def _code_of(r: Row, cols: Cols, head: int | None) -> str | None:
    if not r.words:
        return None
    x, t, x1 = r.words[0]
    t = t.rstrip("._|")                                   # OCR: "5.33A.2.1_" before the table rule
    m = re.fullmatch(r"(\d{1,2})(\d{1,3}[A-H])", t)       # OCR dropped the dot of "4.9 A" -> "49A"
    if m and head is not None and m.group(1) == str(head) and x <= cols.code + 25:
        t = f"{m.group(1)}.{m.group(2)}"
    if not (CODE.match(t) or (re.fullmatch(r"(.+)\([a-z]\)", t) and CODE.match(t[:-3]))) \
            or x > cols.code + 185:                       # 5th-level codes (9.27.2.1.1) sit ~165 right of "Code";
        return None                                       # "2.25(a)" is printed with its letter in brackets
    if head is not None and t.split(".")[0] != str(head):
        return None
    if t.count(".") == 1 and x > cols.code + 25:         # X.Y codes live in the code column; "1.5 m" is text
        return None
    nxt = r.words[1] if len(r.words) > 1 else None
    if nxt and re.fullmatch(r"[A-H]", nxt[1]) and nxt[0] - x1 < 8 and CODE.match(t + nxt[1]):
        after = r.words[2] if len(r.words) > 2 else None   # "4.9 A", "26.42.1 A": suffix printed after a space
        if after is None or after[0] - nxt[2] >= 8:
            return t + nxt[1]
    return t if nxt is None or nxt[0] - x1 >= 8 else None


def _code_words(r: Row, code: str) -> int:
    """Words the code took: 2 for a suffix printed after a space ("4.9 A")."""
    return 2 if len(r.words) > 1 and code == r.words[0][1].rstrip("._|") + r.words[1][1] else 1


def parse_items(doc: SourceDoc, pages: list[PageText], skip_heads: frozenset[int] = frozenset()
                ) -> tuple[list[Rec], list[dict], dict[str, str]]:
    recs: list[Rec] = []
    notes: list[dict] = []
    group_desc: dict[str, str] = {}
    cols, head, group = Cols(), None, None
    cur: Rec | None = None
    owner: Rec | None = None          # item a "Note:-" interrupted before its rate (24.2: rate on the note's last line)
    for p in pages:
        foot = FOOT.search(p.text.upper())
        title = TITLE.match(p.text.strip())               # first page of a sub-head: "6.0 MASONRY WORK" on top
        if not foot and not title:
            continue                                     # basic rates, coefficients, preface: parsed elsewhere
        new_head = int((foot or title).group(1))
        if head != new_head:
            head, group = new_head, None
        if head in skip_heads:
            continue
        rows = rows_of(p)
        for r in rows:                                   # OCR reads table rules as "|" / "_"
            r.words = [w for w in r.words if w[1] not in NOISE]
        rows = [r for r in rows if r.words]
        cols, hy = page_cols(rows, cols)
        for r in rows:
            if r.y <= hy + 2 or r.y > p.height * 0.93 or r.y < 40:
                continue
            t = r.text
            if re.match(r"^\d{1,2}\.0\s+[A-Z]", t) or re.match(r"^SUB\s*HEAD", t, re.I):
                continue                                 # sub-head title line / footer
            code = _code_of(r, cols, head)
            if code:
                cur, owner = Rec(code=code, page=p.page, group=group), None
                recs.append(cur)
                d, u, rate, pct = _cells(Row(r.y, r.words[_code_words(r, code):]), cols)
            else:
                d, u, rate, pct = _cells(r, cols)
                if re.match(r"^Note\b", t, re.I):
                    notes.append({"head": head, "page": p.page, "text": t})
                    owner, cur = cur or owner, None
                elif t.isupper() and not rate and len(t) < 90 and not u:
                    group, cur, owner = t.title(), None, None
                    continue
                elif cur is None and notes and notes[-1]["page"] == p.page:
                    notes[-1]["text"] += " " + t         # note continues
                if cur is None:
                    if owner and (rate or pct):          # the interrupted item's rate ends the note
                        owner.unit += u
                        owner.rate, owner.pct, owner = rate, pct, None
                    continue
            cur.desc += d
            cur.unit += u
            cur.rate = rate or cur.rate
            cur.pct = pct or cur.pct
            if cur.rate or cur.pct:                         # the rate ends the item
                cur = None
    for rec in recs:
        if not rec.rate and not rec.pct:
            group_desc[rec.code] = " ".join(rec.desc)
    return recs, notes, group_desc


def parse_cement_coefficients(doc: SourceDoc, pages: list[PageText]) -> list[dict]:
    """'COEFFICIENTS FOR CEMENT CONSUMPTION' (quintals of cement per unit of item), up to the bitumen table."""
    out: list[dict] = []
    cols, head, active = Cols(), None, False
    cur: dict | None = None
    for p in pages:
        up = p.text.upper()
        if "COEFFICIENTS FOR CEMENT CONSUMPTION" in up:
            active = True
        if "COEFFICIENTS FOR BITUMEN" in up:
            break
        if not active:
            continue
        rows = [r for r in rows_of(p) if r.words]
        cols, hy = page_cols(rows, cols)
        for r in rows:
            if r.y <= hy + 12 or r.y > p.height * 0.93:
                continue
            m = re.match(r"^(\d{1,2})\.0\s+[A-Z]", r.text)
            if m:
                head, cur = int(m.group(1)), None
                continue
            code = _code_of(r, cols, head)
            if code:
                cur = {"code": code, "desc": [], "unit": [], "value": None, "page": p.page}
                d, u, v, _ = _cells(Row(r.y, r.words[_code_words(r, code):]), cols)
            elif cur is not None:
                d, u, v, _ = _cells(r, cols)
            else:
                continue
            cur["desc"] += d
            cur["unit"] += u
            if v:
                cur["value"] = float(v)
                ur = " ".join(cur["unit"]) or None
                out.append({"sor_name": doc.sor_name, "sor_year": doc.revision_year, "code": cur["code"],
                            "description": " ".join(cur["desc"]), "unit": normalize_unit(ur), "unit_raw": ur,
                            "cement_quintal_per_unit": cur["value"], "page": cur["page"], "doc_id": doc.doc_id,
                            "confirmed": True, "review_reasons": []})
                cur = None
    return out


BASIC_SECTIONS = (("HIRE CHARGES", "hire_charges"), ("LABOUR", "labour"), ("MATERIALS", "materials"),
                  ("CARRIAGE CODES", "carriage"))
LEADER = re.compile(r"^[^\w(]+")                              # OCR of the dash after a code: "~—", "=", "‘"


def parse_basic_rates(doc: SourceDoc, pages: list[PageText]) -> list[BasicRate]:
    """'BASIC RATES' (0.1 hire charges, 0.2 labour, 0.3 materials, carriage codes): a 4-digit code, a description
    that may wrap below, and the unit and rate on the code's (first) line. A row runs until the next code.

    The materials list prints carriage codes (2200-2399) at 0.00 ("taken as 0.00 for the material having rates
    F.O.R. at site"), as DAR uses them; their own list gives the carriage for a 5 km lead, which is added to the
    code's description. A carriage code only that list has is stored with its 5 km rate (filed as materials)."""
    out: list[BasicRate] = []
    by_code: dict[str, BasicRate] = {}
    cols, category = Cols(), None
    cur: dict | None = None

    def close():
        if cur and cur["rate"]:
            ur = " ".join(cur["unit"]).strip() or None
            desc = re.sub(r"\s+", " ", " ".join(cur["desc"])).strip(" ,;")
            unit = normalize_unit(ur)
            if cur["category"] == "carriage" and cur["code"] in by_code:
                b = by_code[cur["code"]]
                b.description += (f" (carriage for 5.0 km lead by mechanical transport incl. loading, unloading and "
                                  f"stacking: {cur['rate']} per {ur}, carriage code list p.{cur['page']})")
                return
            out.append(BasicRate(
                key=f"{doc.sor_name}-{doc.revision_year}:BR:{cur['code']}", sor_name=doc.sor_name or "CPWD-DSR",
                sor_year=doc.revision_year, code=cur["code"],
                category={"carriage": "materials", None: "unknown"}.get(cur["category"], cur["category"]),
                description=desc, unit=unit, unit_raw=ur, rate=float(cur["rate"]), rate_raw=cur["rate"],
                page=cur["page"], doc_id=doc.doc_id, region=doc.region, needs_review=unit is None,
                review_reasons=[] if unit else [f"unit not recognised ('{ur}')"]))
            by_code.setdefault(cur["code"], out[-1])

    for p in pages:
        if "BASIC RATE" not in p.text.upper():
            continue                                     # preface, sub-head items, coefficients
        rows = [r for r in rows_of(p) if r.words]
        for r in rows:
            for key, cat in BASIC_SECTIONS:
                if re.search(rf"\b(?:0\.\d\s+)?{key}\b", r.text.upper()) and len(r.text) < 60:
                    category = cat
        cols, hy = page_cols(rows, cols)
        cols = replace(cols, margin=None)                # ragged descriptions, wide gap before the unit column
        for r in rows:
            if r.y <= hy + 2 or r.y > p.height * 0.93:
                continue
            if re.match(r"^\*?Note\b", r.text, re.I):
                close()
                cur = None                               # a note under the table
                continue
            x, t, _ = r.words[0]
            t = t.rstrip(".*,")                          # "0155*" (an average rate, see note), "0552."
            if not BASIC_CODE.match(t) and r.y <= hy + 50:
                continue                                 # rest of the header: "No. | as on or just before 01.04.2023"
            if BASIC_CODE.match(t) and x <= cols.code + 25:
                close()
                cur = {"code": t, "category": category, "page": p.page, "desc": [], "unit": [], "rate": None}
                words = r.words[1:]
                while words and LEADER.fullmatch(words[0][1]):
                    words = words[1:]                    # a leader mark on its own
                if words:
                    words = [(words[0][0], LEADER.sub("", words[0][1]), words[0][2])] + words[1:]
                d, u, rate, _ = _cells(Row(r.y, words), cols)
            elif cur is not None:
                d, u, rate, _ = _cells(r, cols)
            else:
                continue
            cur["desc"] += d
            cur["unit"] += u
            cur["rate"] = cur["rate"] or rate
    close()
    return out


def to_items(doc: SourceDoc, recs: list[Rec], group_desc: dict[str, str], cfg: dict) -> list[SorItem]:
    from concrete_rag.chunking.enrich import tag
    corrections = load_corrections(doc.sor_name or "CPWD-DSR", doc.revision_year)
    revision = ", ".join(x for x in (f"w.e.f. {_dmy(cfg['effective_from'])}" if cfg.get("effective_from") else None,
                                     f"corrections to {_dmy(cfg['corrections_upto'])}" if cfg.get("corrections_upto")
                                     else None) if x) or None
    items, seen = [], set()
    for rec in recs:
        if not rec.rate and not rec.pct:
            continue
        head = int(rec.code.split(".")[0])
        unit_raw = " ".join(rec.unit).strip() or None
        unit = normalize_unit(unit_raw)
        desc = " ".join(rec.desc).strip()
        if rec.pct and rec.pct not in desc:
            desc = f"{desc} (rate: {rec.pct} - not a rupee rate)"
        parents, pc = [], parent_code(rec.code)
        while pc:
            if pc in group_desc:
                parents.insert(0, group_desc[pc])
            pc = parent_code(pc)
        key = f"{doc.sor_name}-{doc.revision_year}:{rec.code}"
        reasons = []
        if key in seen:
            reasons.append("duplicate item code")
            key = f"{key}#p{rec.page}"
        seen.add(key)
        if unit is None:
            reasons.append(f"unit not recognised ('{unit_raw}')")
        rate = float(rec.rate) if rec.rate else None
        items.append(SorItem(
            item_key=key, sor_name=doc.sor_name or "CPWD-DSR", sor_year=doc.revision_year, doc_id=doc.doc_id,
            code=rec.code, raw_code=rec.code,
            sub_head=f"{head}.0 {cfg.get('sub_heads', {}).get(head, '')}".strip(), group_heading=rec.group,
            parent_code=parent_code(rec.code), parent_description=" ".join(parents) or None, description=desc,
            full_description=" ".join(parents + [desc]).strip(), unit=unit, unit_raw=unit_raw, rate=rate,
            rate_raw=rec.rate or rec.pct, page=rec.page, region=doc.region, revision=revision,
            material=tag(" ".join(parents + [desc]))["material_type"], rate_votes=3 if rate is not None else 0,
            rate_readings=[rec.rate], needs_review=bool(reasons), review_reasons=reasons))
        # reviewer corrections made on the old scans are NOT applied here: the comparison decides which still hold
        _ = corrections
    return items


def carriage_recs(doc: SourceDoc) -> tuple[list[Rec], dict[str, str]]:
    """Sub-head 1 (carriage) of a clean copy, from its page-checked transcription in
    config/sor/<sor>_<year>_carriage.yaml: 1.1 / 1.2 are rotated lead tables the column reader cannot read. The
    rate is the first lead column; every lead rate goes into the description."""
    path = get_settings().root / "config" / "sor" / \
        f"{(doc.sor_name or 'CPWD-DSR').lower().replace('-', '_')}_{doc.revision_year}_carriage.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    if not data or data.get("doc_id") != doc.doc_id:
        return [], {}
    groups = data.get("groups") or {}
    recs = []
    for code, (desc, unit, page, leads, *remark) in data["items"].items():
        chain, pc = [], parent_code(code)
        while pc:
            chain.append(groups.get(pc) or {})
            pc = parent_code(pc)
        cols = next((g["lead_columns"] for g in chain if g.get("lead_columns")), None)
        text = desc.rstrip(".")
        if cols and len(leads) > 1:
            text += ". Rate by lead: " + "; ".join(f"{c} {v:.2f}" for c, v in zip(cols, leads))
        note = remark[0] if remark else next((g["remarks"] for g in chain if g.get("remarks")), None)
        if note:
            text += f". Remarks: {note}"
        recs.append(Rec(code=code, page=page, desc=[text], unit=[unit], rate=f"{leads[0]:.2f}"))
    return recs, {c: g["description"] for c, g in groups.items()}


def parse_sor_clean(doc: SourceDoc, pages: list[PageText]) -> SorParseResult:
    cfg = load_sor_config(doc.sor_name or "CPWD-DSR", doc.revision_year)
    carriage, carriage_groups = carriage_recs(doc)
    recs, notes, group_desc = parse_items(doc, pages, frozenset({1}) if carriage else frozenset())
    recs, group_desc = carriage + recs, {**carriage_groups, **group_desc}
    items = to_items(doc, recs, group_desc, cfg)
    basic = parse_basic_rates(doc, pages)
    coeffs = parse_cement_coefficients(doc, pages)
    stats = {"pages": len(pages), "ocr_pages": sum(p.method == "ocr" for p in pages), "items": len(items),
             "items_needing_review": sum(it.needs_review for it in items), "basic_rates": len(basic),
             "cement_coefficients": len(coeffs), "notes": len(notes)}
    return SorParseResult(items=items, basic_rates=basic, cement_coefficients=coeffs, notes=notes, stats=stats)
