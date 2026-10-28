"""CPWD Delhi Analysis of Rates (DAR) parser.

DAR pages are ruled tables with fixed columns (Code | Description | Unit | Quantity | Rate | Amount). Vol. I
starts with the basic rates (01 hire charges, 0.2 labour, 03 materials); every sub-head after that holds
analyses:

    5.33.2.1  Concrete of M25 grade ...                  <- item heading (parents 5.33, 5.33.2 above it)
    Details of cost for 1.00 cum                          <- per quantity
    MATERIAL / LABOUR                                     <- group labels
    0295  Stone Aggregate ... 20 mm   cum 0.57 1425.00 812.25
    3.8   Rate as per Item Number 3.8 of SH: Mortars ...  <- sub-analysis
    9999  Sundries  L.S. 13.00 2.27 29.51
          Extra cost ... = 100% of P            250.00    <- uncoded row referring to marker P
    TOTAL 7105.56 W, Add 1 % Water charges on "W", TOTAL X, Add GST on "X" (multiplying factor 0.2127),
    TOTAL Y, Add 15% CPOH on "Y", TOTAL Z, Add Cess @ 1% on "Z", Cost per 1.00 cum, Say 9860.40

Rows are rebuilt from word boxes (text layer, or OCR words on image-only pages) using the column positions of
each page's header row. Every analysis is checked arithmetically (quantity x rate = amount, lines sum to W, each
build-up step) and its "Say" rate is compared with the DSR item rate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from concrete_rag.normalize.units import normalize_unit
from concrete_rag.schemas import BasicRate, DarAnalysis, DarLine, PageText, SourceDoc

ROW_TOL = 5.0
CODE4 = re.compile(r"^\d{4}$")
ITEM_CODE = re.compile(r"^\d{1,2}(?:\.(?:\d{1,3}[A-Z]?|[A-Z]\d{1,2})){1,4}(?:\([a-z]\))?$")   # 9.147.A1.2, 2.25(a)
SUB_AR = re.compile(r"^A/?R-?\s*(\d+)$")       # DAR-internal sub-analysis "Sub AR-2" / "Sub A/R-2" (not a DSR item)
NUM = re.compile(r"^-?\d[\d,]*(?:\.\d{1,4})?$")
STEP = re.compile(r"^(TOTAL|Total|Add|Deduct|Less|Cost|Say)\b")
# a step whose amount may sit on the next row; other amount-less "Add wastage 5% = 0.4 sqm" rows are workings
COST_STEP = re.compile(r"^(TOTAL|Total)\s*[A-Z]?$|^Say$|^Cost\b.*\b(of|per|for)\b|^Add\b.*\bon\s*[“\"']")
DETAILS = re.compile(r"^Details?\s+of\s+cost\s+for\s+(.*)$", re.I)
WORD_NUM = {"one": 1, "ten": 10, "hundred": 100}
BASIC_CATS = (("hire", "hire_charges"), ("labour", "labour"), ("material", "materials"))
# observed column positions (points, 581 pt wide pages) - used when a page's header row was not read
DEFAULT_COLS = {"code": 58.0, "unit": 312.0, "qty": 346.0, "rate": 404.0, "amount": 458.0}


@dataclass
class Row:
    y: float
    words: list[tuple[float, str, float]]   # (x0, text, x1)

    @property
    def text(self) -> str:
        return " ".join(w[1] for w in self.words)


def rows_of(page: PageText) -> list[Row]:
    ws = sorted(((w.y0 + w.y1) / 2, w.x0, w.text, w.x1) for w in page.words if w.text.strip())
    rows: list[Row] = []
    for yc, x, t, x1 in ws:
        if rows and abs(yc - rows[-1].y) <= ROW_TOL:
            rows[-1].words.append((x, t, x1))
        else:
            rows.append(Row(yc, [(x, t, x1)]))
    for r in rows:
        r.words.sort()
    return rows


def _num(t: str) -> float | None:
    t = t.replace(",", "").strip("`₹").rstrip("]}|")   # OCR of a table rule after a scanned number: "81.70]"
    return float(t) if NUM.match(t) else None


def _round05(x: float) -> float:
    return round(round(x / 0.05) * 0.05, 2)


def _close(a: float | None, b: float | None, rel: float = 0.0005, abs_: float = 0.02) -> bool:
    return a is not None and b is not None and abs(a - b) <= max(abs_, rel * abs(b))


def _join_unit(toks: list[str]) -> str:
    out = ""
    for t in toks:
        out = out[:-1] + t if out.endswith("-") else (f"{out} {t}" if out else t)
    return out.strip()


def _per(text: str) -> tuple[float, str]:
    """'1.00 cum' -> (1, 'cum'); 'one no.' -> (1, 'no.'); '10 sqm' -> (10, 'sqm')."""
    m = re.match(r"^\s*([\d.]+|one|ten|hundred)\s*(.*)$", text.strip(), re.I)
    if not m:
        return 1.0, text.strip()
    q = m.group(1).lower()
    return float(WORD_NUM.get(q, q)) if q not in WORD_NUM else float(WORD_NUM[q]), m.group(2).strip(" .")


def line_category(code: str | None, description: str, group: str | None) -> str:
    if code and (ITEM_CODE.match(code) or "Sub AR" in code):
        return "sub_analysis"
    if code == "9999":       # L.S. rows: "Hire and running charges of mechanical mixer" is equipment
        return "equipment" if re.search(r"\bhire\b|mixer|vibrator|machine|plant", description, re.I) else "sundries"
    if description.lower().startswith("carriage"):
        return "carriage"
    if code and CODE4.match(code):
        n = int(code)
        return "equipment" if n < 100 else "labour" if n < 200 else "material"
    g = (group or "").lower()
    return "labour" if "labour" in g else "material" if "material" in g else "other"


def excluded_markers(label: str) -> list[str]:
    """'Add GST on “X-A”' -> ['A']: rows marked A (sub-analyses that already carry overheads) are left out."""
    m = re.search(r"on\s*[“\"']?\s*[A-Za-z]\s*((?:-\s*[A-Z]\s*)+)", label)
    return re.findall(r"[A-Z]", m.group(1)) if m else []


def excluded_amount(label: str, lines: list[DarLine]) -> float:
    ms = excluded_markers(label)
    return sum(ln.amount for ln in lines if ln.marker in ms) if ms else 0.0


# ------------------------------------------------------------------ page columns
def _header(rows: list[Row]) -> tuple[int, dict[str, float]] | None:
    for i, r in enumerate(rows[:12]):
        names = {t.strip("`₹ ").lower(): x for x, t, _ in r.words}
        if "code" in names and "description" in names:
            # "Unit Quantity Rate Amount" can sit on the next row (2 pt lower)
            nxt = {t.strip("`₹ ").lower(): x for x, t, _ in rows[i + 1].words} if i + 1 < len(rows) else {}
            allw = {**nxt, **names}
            cols = {"code": allw["code"]}
            for k, src in (("unit", "unit"), ("qty", "quantity"), ("rate", "rate"), ("amount", "amount"),
                           ("basic", "basic")):
                if src in allw:
                    cols[k] = allw[src]
            return i, cols
    return None


@dataclass
class Cells:
    code: str | None = None
    desc: list[str] = field(default_factory=list)
    unit: list[str] = field(default_factory=list)
    qty: float | None = None
    rate: float | None = None
    amount: float | None = None
    marker: str | None = None

    @property
    def text(self) -> str:
        return " ".join(self.desc)

    @property
    def has_numbers(self) -> bool:
        return any(v is not None for v in (self.qty, self.rate, self.amount))


def cells(row: Row, cols: dict[str, float]) -> Cells:
    c = Cells()
    code_max, unit_x = cols["code"] + 25, cols["unit"] - 12          # a wrapped heading starts at code + 40
    words = list(row.words)
    if words and words[0][0] < code_max and words[0][1] == "Sub" and len(words) > 1 and SUB_AR.match(words[1][1]):
        words = words[1:]
    if words and words[0][0] < code_max and SUB_AR.match(words[0][1]):
        c.code = "Sub AR-" + SUB_AR.match(words[0][1]).group(1)
        words = words[1:]
    if len(words) > 1 and words[0][0] < code_max and ITEM_CODE.match(words[0][1]) and \
            re.fullmatch(r"[A-H]", words[1][1]) and words[1][0] - words[0][2] < 8 and \
            (len(words) == 2 or words[2][0] - words[1][2] >= 8):
        (x, t, _), (_, letter, x1) = words[:2]           # "4.9 A", "4.1.5 B": the suffix printed after a space
        words = [(x, t + letter, x1)] + words[2:]
    for i, (x, t, x1) in enumerate(words):
        v = _num(t)
        mid = (x + x1) / 2                       # numbers are right-aligned: place them by their centre
        if i == 0 and c.code is None and x < code_max and (CODE4.match(t.rstrip(",.;:_")) or
                                                            ITEM_CODE.match(t.rstrip(",.;:_"))):
            c.code = t.rstrip(",.;:_")                    # OCR: "17.75,"
        elif x >= cols["amount"] + 48 and re.fullmatch(r"[A-Z]", t):
            c.marker = t
        elif mid >= cols["amount"] and v is not None:
            c.amount = v
        elif mid >= cols["rate"] - 8 and v is not None:
            c.rate = v
        elif mid >= cols["qty"] and v is not None:
            c.qty = v
        elif x >= unit_x and x < cols["qty"]:
            c.unit.append(t)
        else:
            c.desc.append(t)
    return c


# ------------------------------------------------------------------ basic rates (Vol. I, section 0)
def parse_basic_rates(doc: SourceDoc, pages: list[PageText]) -> list[BasicRate]:
    out: list[BasicRate] = []
    cat = "unknown"
    for p in pages:
        if re.search(r"SUB\s*HEAD\s*:\s*1\b", p.text):
            break
        head = re.search(r"BASIC\s+RATES\s*:?\s*(.*)", p.text, re.I)
        txt = (head.group(1) if head else "") + " " + p.text[:300]
        for k, v in BASIC_CATS:
            if k in txt.lower():
                cat = v
                break
        rows = rows_of(p)
        h = _header(rows)
        if not h or "basic" not in h[1]:
            continue
        start, cols = h
        # the unit column is left-aligned under a centred header: "sqm of door area" starts ~16 pt left of "Unit"
        unit_x, rate_x = cols.get("unit", 420) - 20, cols["basic"] - 6
        cur: dict | None = None
        for r in rows[start + 1:]:
            if r.y > p.height * 0.925:
                break
            x0, t0, _ = r.words[0]
            if x0 < cols["code"] + 40 and CODE4.match(t0):
                if cur:
                    out.append(cur["br"])
                desc = [t for x, t, _ in r.words[1:] if x < unit_x]
                unit = [t for x, t, x1 in r.words[1:] if unit_x <= x and (x + x1) / 2 < rate_x]
                rate = next((_num(t) for x, t, x1 in r.words if (x + x1) / 2 >= rate_x and _num(t) is not None), None)
                ur = _join_unit(unit)
                br = BasicRate(key=f"{doc.sor_name}-{doc.revision_year}:BR:{t0}", sor_name=doc.sor_name or "CPWD-DAR",
                               sor_year=doc.revision_year, category=cat, code=t0, description=" ".join(desc),
                               unit=normalize_unit(ur), unit_raw=ur or None, rate=rate,
                               rate_raw=f"{rate:.2f}" if rate is not None else None, page=p.page, doc_id=doc.doc_id)
                cur = {"br": br}
            elif re.match(r"^\*?\s*Note\b", t0, re.I):          # page footnote, not part of the last row
                if cur:
                    out.append(cur["br"])
                cur = None
            elif cur and all(x < rate_x for x, _, _ in r.words):   # wrapped description / unit ("... door" / "area")
                b = cur["br"]
                b.description += " " + " ".join(t for x, t, _ in r.words if x < unit_x)
                more = [t for x, t, _ in r.words if x >= unit_x]
                if more:
                    b.unit_raw = _join_unit(([b.unit_raw] if b.unit_raw else []) + more)
                    b.unit = normalize_unit(b.unit_raw)
        if cur:
            out.append(cur["br"])
    revision = doc.title.split(" - ", 1)[1] if " - " in doc.title else str(doc.revision_year)
    for b in out:
        b.description = b.description.strip()
        b.region, b.revision = doc.region, revision
        if b.rate is None:
            b.needs_review, b.review_reasons = True, ["no rate read"]
        if b.unit is None:
            b.needs_review = True
            b.review_reasons.append(f"unknown unit '{b.unit_raw}'")
    return out


# ------------------------------------------------------------------ analyses
class _State:
    def __init__(self, doc: SourceDoc):
        self.doc = doc
        self.headings: dict[str, str] = {}
        self.last_heading: str | None = None
        self.sub_head: str | None = None
        self.cur: dict | None = None
        self.pending: dict | None = None       # line being read (code row seen, amount not yet)
        self.loose: list[str] = []             # uncoded text rows inside an analysis
        self.await_step: dict | None = None    # step row whose amount is on the next row
        self.group: str | None = None
        self.out: list[DarAnalysis] = []

    def scoped(self, code: str) -> str:
        """'Sub AR-2' -> '5/Sub AR-2': sub-analysis numbers are local to a sub-head."""
        return f"{(self.sub_head or '?').split()[0]}/{code}" if code.startswith("Sub AR") else code

    def is_heading_code(self, code: str | None) -> bool:
        return bool(code) and (bool(ITEM_CODE.match(code)) or code.startswith("Sub AR"))

    # --- headings
    def heading(self, code: str, text: str):
        code = self.scoped(code)
        self.headings[code] = text.strip()
        self.last_heading = code

    def full_description(self, code: str) -> tuple[str, str | None]:
        parts, parent = [], None
        bits = code.split(".")
        for i in range(2, len(bits)):
            pc = ".".join(bits[:i])
            if pc in self.headings:
                parts.append(self.headings[pc])
                parent = pc
        parts.append(self.headings.get(code, ""))
        return " ".join(p for p in parts if p), parent

    # --- analysis lifecycle
    def start(self, per_text: str, page: int):
        if self.cur:
            self.finish(page, reason="analysis ended without a 'Say' row")
        code = self.last_heading
        if not code:
            return
        q, u = _per(per_text)
        full, parent = self.full_description(code)
        self.cur = {"code": code, "desc": self.headings.get(code, ""), "full": full, "parent": parent,
                    "per_q": q, "per_u": u, "lines": [], "steps": [], "p0": page, "p1": page,
                    "sub_head": self.sub_head}
        self.pending, self.loose, self.group, self.await_step = None, [], None, None

    def add_line(self, c: Cells, page: int):
        p = self.pending
        desc = " ".join(p["desc"]).strip()
        note = None
        if self.loose and p["code"] is None:
            desc = " ".join(self.loose + [desc]).strip()
            self.loose = []
        ur = _join_unit(p["unit"])
        sub = re.search(r"Sub\s*AR-?\s*(\d+)", desc) if p["code"] is None else None
        if sub:                                   # "Rate as per Sub AR-1" with the code wrapped onto the next row
            p["code"] = f"Sub AR-{sub.group(1)}"
        ln = DarLine(code=self.scoped(p["code"]) if p["code"] else None, description=desc, unit=normalize_unit(ur), unit_raw=ur or None,
                     quantity=p["qty"], rate=p["rate"], amount=p["amount"], group=self.group, marker=p["marker"],
                     note=note)
        ln.category = line_category(ln.code, ln.description, self.group)
        if ln.code is None:
            m = re.search(r"of\s+([A-Z])\b", ln.description)
            ref = next((x for x in self.cur["lines"] if m and x.marker == m.group(1)), None)
            if ref:
                ln.category = ref.category
        self.cur["lines"].append(ln)
        self.cur["p1"] = page
        self.pending = None

    def flush_loose(self, next_is_coded: bool):
        """Uncoded text before a coded row: '(...)' explains the previous line, anything else is a group label."""
        if not self.loose:
            return
        txt = " ".join(self.loose).strip()
        working = txt.startswith("(") or "=" in txt or sum(ch.isdigit() for ch in txt) > 4
        if working and self.cur["lines"]:
            prev = self.cur["lines"][-1]
            prev.note = (prev.note + " " if prev.note else "") + txt
        elif next_is_coded and not working:
            self.group = txt
        self.loose = []

    def finish(self, page: int, reason: str | None = None):
        c = self.cur
        self.cur = None
        if not c or not c["lines"]:
            return
        reasons = [reason] if reason else []
        lines: list[DarLine] = c["lines"]
        for ln in lines:
            if ln.quantity is not None and ln.rate is not None and not _close(ln.quantity * ln.rate, ln.amount):
                reasons.append(f"{ln.code or ln.description[:30]}: {ln.quantity} x {ln.rate} != {ln.amount}")
        steps = c["steps"]
        base = round(sum(ln.amount for ln in lines), 2)
        w = next((s["amount"] for s in steps if s["label"].upper().startswith("TOTAL")), None)
        if w is None and not any(s["label"].lower().startswith("add") for s in steps):     # e.g. mortars
            w = next((s["amount"] for s in steps if s["label"].lower().startswith("cost")), None)
        if not _close(base, w, abs_=0.06):
            reasons.append(f"lines sum to {base} but TOTAL W is {w}")
        factors = {"water": None, "gst": None, "cpoh": None, "cess": None}
        running, added = None, 0.0
        cost_unit = None
        for s in steps:
            lab, amt = s["label"], s["amount"]
            if amt is None:
                reasons.append(f"no amount read for '{lab}'")
                continue
            low = lab.lower()
            if low.startswith("total"):
                if running is not None and not _close(running + added, amt):
                    reasons.append(f"'{lab}' {amt} != {round(running + added, 2)}")
                running, added = amt, 0.0
            elif low.startswith(("add", "deduct", "less")):
                base_amt = running - excluded_amount(lab, lines) if running is not None else None
                fm = re.search(r"factor\s*([\d.]+)", lab)
                pm = re.search(r"([\d.]+)\s*%", lab)
                pct = float(fm.group(1)) if fm else float(pm.group(1)) / 100 if pm else None
                if "water" in low:
                    factors["water"] = pct * 100 if pct is not None else None
                elif "gst" in low:
                    factors["gst"] = pct
                elif "cpoh" in low or "profit" in low:
                    factors["cpoh"] = pct * 100 if pct is not None else None
                elif "cess" in low:
                    factors["cess"] = pct * 100 if pct is not None else None
                if pct is not None and base_amt is not None and not _close(base_amt * pct, amt):
                    reasons.append(f"'{lab}' {amt} != {round(base_amt * pct, 2)}")
                added += -amt if low.startswith(("deduct", "less")) else amt
            elif low.startswith("cost"):
                if running is not None and cost_unit is None and not _close(running + added, amt):
                    if not any(_close((running + added) / k, amt) for k in (c["per_q"], 10, 100)):
                        reasons.append(f"'{lab}' {amt} != {round(running + added, 2)}")
                cost_unit = (lab, amt)
            elif low.startswith("say") and cost_unit and abs(_round05(cost_unit[1]) - amt) > 0.051:
                reasons.append(f"Say {amt} is not {cost_unit[1]} rounded to 0.05")
        say = next((s["amount"] for s in reversed(steps) if s["label"].lower().startswith("say")), None)
        unit_raw = None
        if cost_unit:
            m = re.search(r"(?:of|per)\s+(?:[\d.]+\s*)?(.+)$", cost_unit[0], re.I)
            unit_raw = m.group(1).strip(" .") if m else None
        unit_raw = unit_raw or c["per_u"]
        if say is None and cost_unit:                   # no 'Say' row: the DSR rate is the cost rounded to 0.05
            say = _round05(cost_unit[1])               # (9.123: cost 696.02 -> DSR 696.00)
        elif say is None:
            reasons.append("no 'Say' rate and no cost per unit")
        d = self.doc
        n = sum(1 for a in self.out if a.code == c["code"])
        self.out.append(DarAnalysis(
            key=f"{d.sor_name}-{d.revision_year}:{c['code']}" + (f"#{n + 1}" if n else ""),
            sor_name=d.sor_name or "CPWD-DAR", sor_year=d.revision_year, doc_id=d.doc_id, code=c["code"],
            description=c["desc"], full_description=c["full"], parent_code=c["parent"], sub_head=c["sub_head"],
            per_quantity=c["per_q"], per_unit=normalize_unit(c["per_u"]) or c["per_u"],
            unit=normalize_unit(unit_raw), unit_raw=unit_raw, lines=lines, steps=steps, base_total=w if w else base,
            water_percent=factors["water"], gst_factor=factors["gst"], cpoh_percent=factors["cpoh"],
            cess_percent=factors["cess"], rate=say, region=d.region,
            revision=d.title.split(" - ", 1)[1] if " - " in d.title else str(d.revision_year),
            page_start=c["p0"], page_end=max(c["p1"], page), needs_review=bool(reasons), review_reasons=reasons))

    # --- one row
    def row(self, r: Row, cols: dict[str, float], page: int):
        c = cells(r, cols)
        text = c.text
        if self.cur is None:                                      # between analyses: headings and notes
            dm = DETAILS.match(text)
            if dm:
                self.start(dm.group(1), page)
            elif self.is_heading_code(c.code):
                self.heading(c.code, text)
            elif self.last_heading and text and not c.has_numbers:
                self.headings[self.last_heading] += " " + text
            return
        if self.await_step is not None and c.amount is not None and not c.code and not STEP.match(text):
            if text:                                 # label wrapped: 'Add GST on "X-A-P-R" (multiplying factor' / '0.2127)'
                self.await_step["label"] += " " + text
            self.await_step["amount"], self.await_step["marker"] = c.amount, c.marker or self.await_step["marker"]
            self.await_step = None
            if self.cur["steps"][-1]["label"].lower().startswith("say"):
                self.finish(page)
            return
        dm = DETAILS.match(text)
        if dm and not c.has_numbers and self.cur["lines"] and self.last_heading == self.cur["code"] \
                and not (self.pending and self.is_heading_code(self.pending["code"])):
            self.loose.append(text)                      # a working inside the analysis (16.15.1: "Details of cost
            return                                       # for 9.88 sqm" of the plaster), not a new analysis
        if dm and not c.has_numbers:
            if self.pending and self.is_heading_code(self.pending["code"]):
                self.heading(self.pending["code"], " ".join(self.pending["desc"]))   # a heading, not a line
                self.pending = None
            self.start(dm.group(1), page)
            return
        wrapped = self.pending and self.pending["amount"] is None and c.qty is not None and c.rate is not None
        if not c.code and STEP.match(text) and "=" not in text and (c.amount is not None or COST_STEP.match(text)) \
                and not wrapped:                         # "Say 204 | each | 204.00 | 9.00" ends a line, not the analysis
            if self.pending:
                self.finish(page, reason=f"row '{' '.join(self.pending['desc'])[:40]}' has no amount")
                return
            self.flush_loose(next_is_coded=False)
            step = {"label": text, "amount": c.amount, "marker": c.marker}
            self.cur["steps"].append(step)
            self.cur["p1"] = page
            self.await_step = step if c.amount is None else None
            if text.lower().startswith("say") and c.amount is not None:
                self.finish(page)
            return
        if c.code:
            if self.pending and self.pending["amount"] is None:
                if self.is_heading_code(self.pending["code"]):      # heading inside: analysis had no Say
                    self.heading(self.pending["code"], " ".join(self.pending["desc"]))
                    self.pending = None
                    self.finish(page, reason="analysis ended without a 'Say' row")
                    self.heading(c.code, text) if self.is_heading_code(c.code) and not c.has_numbers else None
                    return
            self.flush_loose(next_is_coded=True)
            self.pending = {"code": c.code, "desc": list(c.desc), "unit": list(c.unit), "qty": c.qty,
                            "rate": c.rate, "amount": c.amount, "marker": c.marker}
        elif self.pending:
            p = self.pending
            p["desc"] += c.desc
            p["unit"] += c.unit
            p["qty"] = p["qty"] if c.qty is None else c.qty
            p["rate"] = p["rate"] if c.rate is None else c.rate
            p["amount"] = c.amount
            p["marker"] = c.marker or p["marker"]
        elif c.amount is not None:                                # uncoded line, e.g. "= 100% of P"
            self.pending = {"code": None, "desc": list(c.desc), "unit": list(c.unit), "qty": c.qty, "rate": c.rate,
                            "amount": c.amount, "marker": c.marker}
        elif text == "Sub":
            return
        elif text:
            if text.isupper() and len(text) <= 40 and not self.loose:
                self.group = text.title()
            else:
                self.loose.append(text)
            return
        if self.pending and self.pending["amount"] is not None:
            self.add_line(c, page)


def _join_sub_ar(rows: list[Row], cols: dict[str, float]) -> list[Row]:
    """'Sub' with 'A/R - 2' (or 'A/R 2', 'A/R-3') on the row below it: one 'Sub AR-2' code on the heading row."""
    for r, nxt in zip(rows, rows[1:]):
        if not (r.words and r.words[0][1] == "Sub" and r.words[0][0] < cols["code"] + 25):
            continue
        lead = [w for w in nxt.words if w[0] < cols["code"] + 40][:3]
        m = re.fullmatch(r"A/?R-?(\d+)", "".join(t for _, t, _ in lead))
        if m:
            x, _, x1 = r.words[0]
            r.words = [r.words[0], (x1, f"AR-{m.group(1)}", x1)] + r.words[1:]
            nxt.words = nxt.words[len(lead):]
    return [r for r in rows if r.words]


def parse_analyses(doc: SourceDoc, pages: list[PageText]) -> list[DarAnalysis]:
    st = _State(doc)
    cols = dict(DEFAULT_COLS)
    started = False
    for p in pages:
        foot = re.search(r"SUB\s*HEAD\s*:\s*(\d+)\.?\d*\s+([A-Z][A-Z &,()-]+)", p.text)
        if foot:
            started = True
            st.sub_head = f"{foot.group(1)} {foot.group(2).strip().title()}"
        if not started:
            continue
        rows = rows_of(p)
        h = _header(rows)
        if h and "basic" in h[1]:
            continue
        if h and all(k in h[1] for k in ("unit", "qty", "rate", "amount")):
            cols = {k: h[1][k] for k in DEFAULT_COLS}
        # rows above the table header matter too: an item heading often starts a page
        for r in _join_sub_ar(rows, cols):
            if r.y > p.height * 0.925 or r.y < 35:           # an item heading can start at y 39.5 (26.46)
                continue
            t = r.text
            if (t.startswith("Code") and "Description" in t) or ("Quantity" in t and "Amount" in t) \
                    or re.fullmatch(r"[`₹\s]+|No\.|\d{1,4}|(Sub\s+)?AR-?\s*\d+", t) or re.match(r"^SUB\s*HEAD\b", t):
                continue        # header / page number / a "Sub AR-1" reference wrapped onto its own row
            st.row(r, cols, p.page)
    if st.cur:
        st.finish(pages[-1].page, reason="analysis ended without a 'Say' row")
    return st.out


@dataclass
class DarParseResult:
    analyses: list[DarAnalysis]
    basic_rates: list[BasicRate]
    stats: dict


def crosscheck_dsr(analyses: list[DarAnalysis], dsr_sor: str = "CPWD-DSR") -> dict:
    """Compare each DAR 'Say' rate with the DSR item rate read from the DSR scans. Analyses a reviewer detached
    from their DSR item (config/corrections/cpwd_dar_2023.yaml, `not_for_dsr`) are left out."""
    from concrete_rag.parse.sor import load_corrections
    from concrete_rag.store import docstore
    decided = load_corrections("CPWD-DAR", analyses[0].sor_year) if analyses else {}
    match, differ, dar_only = 0, [], []
    for a in analyses:
        d = decided.get(a.key) or {}
        if d.get("rate") is not None:                   # a misprinted 'Say', checked against the analysis' own cost
            a.rate, a.checked_by = float(d["rate"]), d.get("checked_by")
            a.review_reasons = [r for r in a.review_reasons if not r.startswith("Say ")]
            a.needs_review = bool(a.review_reasons)
        if d.get("not_for_dsr"):
            a.not_for_dsr, a.checked_by = True, d.get("checked_by")
            dar_only.append(a.code)
            continue
        it = None if "/" in a.code or "#" in a.key else docstore.get_sor_item(a.code, dsr_sor, a.sor_year)
        if not it or it.get("rate") is None:
            dar_only.append(a.code)
            continue
        a.dsr_rate = it["rate"]
        a.dsr_match = _close(a.rate, it["rate"], rel=0, abs_=0.011)
        if a.dsr_match:
            match += 1
        else:
            differ.append({"code": a.code, "dar": a.rate, "dsr": it["rate"], "dsr_needs_review": it["needs_review"]})
    return {"dsr_match": match, "dsr_differ": len(differ), "dsr_differences": differ, "not_in_dsr": len(dar_only)}


def parse_dar(doc: SourceDoc, pages: list[PageText]) -> DarParseResult:
    basics = parse_basic_rates(doc, pages)
    analyses = parse_analyses(doc, pages)
    stats = {"pages": len(pages), "ocr_pages": sum(p.method == "ocr" for p in pages), "basic_rates": len(basics),
             "analyses": len(analyses), "needs_review": sum(a.needs_review for a in analyses)}
    try:
        stats |= crosscheck_dsr(analyses)
    except Exception as ex:   # noqa: BLE001 - DSR not ingested yet
        stats["dsr_crosscheck"] = f"skipped: {ex}"
    return DarParseResult(analyses, basics, stats)
