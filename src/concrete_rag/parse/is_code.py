"""Indian Standard (IS code) structure parser.

Turns layout lines into structural units:
  * clauses     (numbered body clauses, e.g. 26.4.2.1, with heading path and SECTION)
  * annex clauses (e.g. B-2.1.1 under ANNEX B)
  * tables      (title, referencing clauses, rows split into cells, NOTES)
  * amendment entries ("(Page 33, clause 21.3) - Substitute ...") with their target references
  * foreword text

Front matter (BIS disclosure pages, contents, committee composition) is skipped.
Clause numbers are only accepted if they are a plausible successor of the previous clause
(reusing the sequence model from normalize.item_codes), which filters out OCR noise such as
"20 mm" or numbered NOTES being mistaken for clauses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from concrete_rag.ingest.layout import Line, has_columns, page_lines
from concrete_rag.normalize.item_codes import successor_cost
from concrete_rag.schemas import PageText, SourceDoc

# ---------------------------------------------------------------- patterns
HEADER_RE = re.compile(r"^(?:[IT1l][S5]|IS)[\s.;:,]*\d{3,5}\b.*?(?:[:;]|-)\s*\d{4}\b.{0,20}$")
AMEND_HEADER_RE = re.compile(r"^Amend\w*\s+N[o0]\.?\s*(\d+)\s+t[oe]\s+", re.I)
AMEND_TITLE_RE = re.compile(r"AMENDMENT\s*N?[O0]?\.?\s*[%#]?\s*(\d+|[I|l])\b", re.I)
PAGE_NO_RE = re.compile(r"^[\[\](){}|.,\s]*[0-9ivxlIL|]{1,4}[\[\](){}|.,\s]*$")
WATERMARK_RE = re.compile(r"Free Standard provided by BIS|BSB Edge|Supplied by Book Supply Bureau|"
                          r"^Srinagar\(|@blumargins|Reprography Unit|^\(CED \d+\)$", re.I)
SECTION_RE = re.compile(r"^SECTION\s+(\d+)\s*(.*)$")
ANNEX_RE = re.compile(r"^ANNEX\s+([A-Z])\b\s*(.*)$")
CLAUSE_RE = re.compile(r"^(\d{1,2}(?:\s?[.,]\s?\d{1,2}){0,5})[.,]?\s+(.*)$|^(\d{1,2}(?:\.\d{1,2}){0,5})[.,]?$")
ANNEX_CLAUSE_RE = re.compile(r"^([A-Z])\s?[-–—]\s?(\d{1,2}(?:\s?[.,]\s?\d{1,2}){0,4})[.,]?\s*(.*)$")
TABLE_RE = re.compile(r"^(?:Tab[l1I|]e|TAB[L1I|]E)\s+(\d{1,2}[A-Z]?|[IVXLTfl1]{1,6})\b\.?\s*(.*)$")
CAPTION_RE = re.compile(r"^\(\s*(?:Clauses?|Clause»|Foreword)\b(.*)\)?\s*$", re.I)
NOTES_RE = re.compile(r"^NOTES?\b\s*[-—–:]*\s*(.*)$")
AMEND_ENTRY_RE = re.compile(r"\(\s*Page\s*[\dIl]+\s*[,.]\s*([^)]{1,120})\)")
STD_REF_RE = re.compile(r"\bIS\s?:?\s?(\d{3,5})(?:\s*\(\s*Part\s*([0-9IVX]+)\s*\))?")
EQUATION_HINT = re.compile(r"[=√∑≤≥±×÷]|\b(?:where|i\.e\.)\b.*=")

BOILERPLATE_MARKERS = ("Disclosure to Promote the Right", "BLANK PAGE", "PROTECTED BY COPYRIGHT",
                       "Knowledge is such a treasure", "BIS is a statutory institution",
                       "Bureau of Indian Standards BIS is a statutory", "Copyright BIS has the copyright")
COMMITTEE_MARKERS = ("COMMITTEE COMPOSITION", "Representing", "( Continued from page", "(Continued from page",
                     "Sectional Committee", "Members")


@dataclass
class Unit:
    kind: str                  # clause | annex_clause | table | amendment | foreword
    number: str | None         # clause/table number, annex clause "B-2.1"
    title: str | None
    lines: list[str] = field(default_factory=list)
    page_start: int = 0
    page_end: int = 0
    section: str | None = None
    annex: str | None = None
    annex_title: str | None = None
    heading_path: list[str] = field(default_factory=list)
    amendment_no: int | None = None
    targets: list[str] = field(default_factory=list)       # amendment targets / table "(Clauses ...)"
    table_rows: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    min_conf: float = 100.0

    @property
    def text(self) -> str:
        return _dehyphenate(self.lines)


def _dehyphenate(lines: list[str]) -> str:
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if out.endswith("-") and ln[:1].islower():
            out = out[:-1] + ln
        elif out:
            out += "\n" + ln if re.match(r"^(?:[a-z]\)|\(?[ivx]+\)|\d+\)|NOTE)", ln) else " " + ln
        else:
            out = ln
    return out


def _is_titleish(s: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", s)]
    if not words or len(s) > 80:
        return False
    if s.isupper():
        return True
    small = {"of", "and", "the", "for", "in", "to", "on", "a", "an", "or", "with", "by", "at", "from", "under"}
    caps = [w for w in words if w.lower() not in small]
    return bool(caps) and sum(w[0].isupper() for w in caps) / len(caps) >= 0.6 and not s.rstrip().endswith(
        (",", ";")) and s[0].isupper()


def classify_page(lines: list[Line]) -> str:
    head = " ".join(ln.text for ln in lines[:12])
    full = " ".join(ln.text for ln in lines)
    if any(m in full for m in BOILERPLATE_MARKERS) and len(full) < 4000:
        return "boilerplate"
    if not lines:
        return "empty"
    if AMEND_HEADER_RE.search(head) or re.search(r"AMENDMENT\s*N", head[:200], re.I):
        if sum(full.count(m) for m in ("Members", "Representing", "Alternate")) >= 3:
            return "committee"
        return "amendment"
    if "COMMITTEE COMPOSITION" in full or (("Members" in full or "Representing" in full)
                                             and full.count("Alternate") >= 2):
        return "committee"
    if "CONTENTS" in head:
        return "contents"
    # continuation of a contents list: most lines end with a page number
    toc_like = sum(1 for ln in lines if re.search(r"\s\d{1,3}$", ln.text) and len(ln.text) < 75)
    if len(lines) > 10 and toc_like / len(lines) > 0.55 and not any(TABLE_RE.match(ln.text) for ln in lines):
        return "contents"
    if re.search(r"\bFOREWORD\b", head):
        return "foreword"
    return "body"


def clean_lines(lines: list[Line]) -> tuple[list[Line], int | None]:
    """Drop running headers, page numbers, watermarks. Returns (lines, amendment_no from header)."""
    out, amend = [], None
    for i, ln in enumerate(lines):
        t = ln.text.strip()
        if not t or WATERMARK_RE.search(t):
            continue
        m = AMEND_HEADER_RE.match(t)
        if m and i < 4:
            amend = int(m.group(1))
            continue
        if i < 4 and HEADER_RE.match(t) and len(t) < 45:
            continue
        if PAGE_NO_RE.match(t) and (i >= len(lines) - 3 or i < 2):
            continue
        if ln.mean_conf < 35 and len(t) < 60:          # OCR of figures/drawings
            continue
        out.append(ln)
    return out, amend


def _norm_clause(raw: str) -> str:
    return re.sub(r"\s", "", raw).replace(",", ".").strip(".")


class ISCodeParser:
    def __init__(self, doc: SourceDoc, two_column: bool = True):
        self.doc = doc
        self.two_column = two_column
        self.units: list[Unit] = []
        self.section: str | None = None
        self.annex: str | None = None
        self.annex_title: str | None = None
        self.prev_clause: str | None = None
        self.prev_annex_clause: dict[str, str] = {}
        self.titles: dict[str, str] = {}              # clause no -> title (for heading paths)
        self.cur: Unit | None = None
        self.in_notes = False
        self.amendment_no: int | None = None
        self.started = False                           # body started (first clause seen)
        self.page_kinds: dict[int, str] = {}

    # ------------------------------------------------------------ helpers
    def _close(self):
        if self.cur and (self.cur.lines or self.cur.table_rows or self.cur.kind == "table"):
            self.units.append(self.cur)
        self.cur = None
        self.in_notes = False

    def _heading_path(self, num: str) -> list[str]:
        parts = num.split(".")
        path = []
        for i in range(1, len(parts)):
            key = ".".join(parts[:i])
            if key in self.titles:
                path.append(f"{key} {self.titles[key]}")
        return path

    def _start(self, kind, number, title, page, **kw):
        self._close()
        self.cur = Unit(kind=kind, number=number, title=title, page_start=page, page_end=page,
                        section=self.section, annex=self.annex, annex_title=self.annex_title,
                        amendment_no=kw.pop("amendment_no", None), **kw)

    def _append(self, text: str, page: int, conf: float = 100.0):
        if self.cur is None:
            return
        self.cur.lines.append(text)
        self.cur.page_end = page
        self.cur.min_conf = min(self.cur.min_conf, conf)

    # ------------------------------------------------------------ clause detection
    def _try_clause(self, text: str) -> tuple[str, str] | None:
        m = CLAUSE_RE.match(text)
        if not m:
            return None
        raw = m.group(1) or m.group(3)
        rest = (m.group(2) or "").strip()
        num = _norm_clause(raw)
        if not re.fullmatch(r"\d{1,2}(?:\.\d{1,2}){0,5}", num):
            return None
        if rest[:1] in ("%", "/", "x", "×") or re.match(r"^(mm|cm|m|kg|N|MPa|percent|per|to|and|or|days?|months?|years?|h|hours?|min|times)\b", rest):
            return None
        cost = successor_cost(self.prev_clause, num)
        letters = [c for c in rest if c.isalpha()]
        upper_ratio = sum(c.isupper() for c in letters) / len(letters) if letters else 0
        if "." not in num:
            # top-level clauses are always upper-case headings ("8 DURABILITY OF CONCRETE")
            if upper_ratio < 0.5 or len(rest) > 90:
                return None
            if self.prev_clause is not None:
                step = int(num) - int(self.prev_clause.split(".")[0])
                if not 1 <= step <= 3:
                    return None
        if self.prev_clause is None:
            ok = num in ("0", "1", "1.1", "0.1")
        else:
            ok = cost <= 2.0 or (cost <= 3.5 and "." not in num)
        return (num, rest) if ok else None

    def _try_annex_clause(self, text: str) -> tuple[str, str] | None:
        m = ANNEX_CLAUSE_RE.match(text)
        if not m or self.annex is None or m.group(1) != self.annex:
            return None
        num = _norm_clause(m.group(2))          # OCR "G-1,1" -> G-1.1
        prev = self.prev_annex_clause.get(self.annex)
        if prev is not None and successor_cost(prev, num) > 2.5:
            return None
        return f"{self.annex}-{num}", m.group(3).strip()

    # ------------------------------------------------------------ main loop
    def feed_page(self, page: PageText):
        lines = page_lines(page, allow_columns=self.two_column)
        kind = classify_page(lines)
        self.page_kinds[page.page] = kind
        lines, hdr_amend = clean_lines(lines)
        if kind in ("boilerplate", "contents", "committee", "empty"):
            self._close()
            return
        if kind == "amendment":
            self._feed_amendment(lines, page.page, hdr_amend)
            return
        self.amendment_no = None
        if kind == "foreword" or (not self.started and self.cur and self.cur.kind == "foreword"):
            if not self.cur or self.cur.kind != "foreword":
                self._start("foreword", None, "Foreword", page.page)
        for ln in lines:
            self._feed_line(ln, page.page)

    def _feed_amendment(self, lines: list[Line], page: int, hdr_amend: int | None):
        text = "\n".join(ln.text for ln in lines)
        m = AMEND_TITLE_RE.search(text[:300])
        if m:
            g = m.group(1)
            self.amendment_no = int(g) if g.isdigit() else 1
        elif hdr_amend:
            self.amendment_no = hdr_amend
        for ln in lines:
            t = ln.text.strip()
            if AMEND_ENTRY_RE.match(t) or (t.startswith("(") and re.match(r"^\(\s*Page\s*\d", t)):
                targets = []
                em = AMEND_ENTRY_RE.match(t)
                if em:
                    targets = _amend_targets(em.group(1))
                self._start("amendment", None, f"Amendment No. {self.amendment_no}", page,
                            amendment_no=self.amendment_no, targets=targets)
                self._append(t, page, ln.mean_conf)
            elif self.cur and self.cur.kind == "amendment":
                if re.match(r"^\(\s*[A-Z]{2,4}\s*\d+\s*\)$", t):   # committee ref like (CED 2) closes entry
                    self._close()
                    continue
                self._append(t, page, ln.mean_conf)

    def _feed_line(self, ln: Line, page: int):
        t = ln.text.strip()
        if not t:
            return
        # SECTION headings
        sm = SECTION_RE.match(t)
        if sm and len(t) < 90:
            self._close()
            self.section = f"Section {sm.group(1)} {sm.group(2).title()}".strip()
            return
        # ANNEX headings
        am = ANNEX_RE.match(t)
        if am and self.started:
            self._close()
            self.annex = am.group(1)
            self.annex_title = am.group(2).strip() or None
            self.section = None
            return
        if self.annex and self.annex_title is None and self.cur is None:
            if CAPTION_RE.match(t) or t.startswith("("):
                return
            if t.isupper():
                self.annex_title = t.title()
                return
        if self.annex and self.cur is None and self.annex_title and t.isupper() and len(t) < 80:
            self.annex_title = f"{self.annex_title} {t.title()}"
            return
        # Tables
        tm = TABLE_RE.match(t)
        if tm and self.started and self._is_table_title(tm.group(2).strip(), ln):
            title = tm.group(2).strip()
            if title.lower().startswith(("(concluded", "(continued", "( concluded", "( continued")) and \
                    self.cur and self.cur.kind == "table":
                return
            self._start("table", _roman_fix(tm.group(1)), title, page)
            return
        if self.cur and self.cur.kind == "table":
            if self._feed_table_line(ln, t, page):
                return
        # numbered clauses
        if self.annex:
            ac = self._try_annex_clause(t)
            if ac:
                num, rest = ac
                self.prev_annex_clause[self.annex] = num.split("-", 1)[1]
                self._start_clause(num, rest, page, ln, kind="annex_clause")
                return
        else:
            c = self._try_clause(t)
            if c:
                num, rest = c
                self.prev_clause = num
                self.started = True
                self._start_clause(num, rest, page, ln, kind="clause")
                return
        if self.cur is None:
            return
        nm = NOTES_RE.match(t)
        if nm and self.cur.kind != "table":
            self.in_notes = True
        self._append(t, page, ln.mean_conf)

    def _is_table_title(self, rest: str, ln: Line) -> bool:
        """'Table 5 Minimum Cement Content ...' is a caption; 'Table 20 gives the ...' is prose."""
        if not rest or rest.startswith("("):
            return True
        if rest[0].islower() or re.match(r"^(gives|shall|is|are|may|for|of|and|to|or|as|in)\b", rest, re.I):
            return False
        return _is_titleish(rest[:70].rsplit(" ", 1)[0] if len(rest) > 70 else rest) or rest.isupper()

    def _start_clause(self, num: str, rest: str, page: int, ln: Line, kind: str):
        title = None
        body = rest
        if rest and _is_titleish(rest):
            title, body = rest.rstrip(" .—-"), ""
        elif rest:
            # "8.2.5.4 Alkali-aggregate reaction" style title followed by body on the same line is rare;
            # "26.4.1 Nominal Cover — Nominal cover is ..." split at an em-dash
            m = re.match(r"^([A-Z][^—]{2,60}?)\s*[—–]\s*(.+)$", rest)
            if m and _is_titleish(m.group(1)):
                title, body = m.group(1).strip(), m.group(2)
        key = num
        if title:
            self.titles[key] = title
        self._start(kind, num, title, page)
        self.cur.heading_path = self._heading_path(num.split("-", 1)[-1]) if kind == "clause" else (
            [f"Annex {self.annex} {self.annex_title or ''}".strip()] + [
                f"{num.split('-')[0]}-{h}" for h in self._heading_path(num.split("-", 1)[1])])
        if kind == "annex_clause" and title:
            self.titles[num.split("-", 1)[1]] = title
        if body:
            self._append(body, page, ln.mean_conf)

    def _feed_table_line(self, ln: Line, t: str, page: int) -> bool:
        """Returns True if the line was consumed by the current table."""
        tbl = self.cur
        cap = CAPTION_RE.match(t)
        if cap and not tbl.table_rows and not tbl.targets:
            tbl.targets = re.findall(r"[A-Z]?-?\d+(?:\.\d+)+|[A-Z]-\d+|\b\d{1,2}\b(?=\s*(?:,|and|\)|$))", cap.group(1))
            tbl.lines.append(t)
            return True
        toks = t.split()
        numeric = sum(1 for x in toks if re.fullmatch(r"[\d.,:/%()-]+", x))
        if not tbl.table_rows and not tbl.targets and ln.kind in ("full", "single") and len(t) > 25 \
                and numeric / max(len(toks), 1) < 0.2 and len(tbl.lines) < 3 and t[:1].isalpha() \
                and not NOTES_RE.match(t) and not t.startswith(("Sl", "SI", "S1")):
            tbl.title = f"{tbl.title} {t}".strip()       # wrapped table title
            return True
        nm = NOTES_RE.match(t)
        if nm:
            self.in_notes = True
            if nm.group(1):
                tbl.notes.append(nm.group(1))
            return True
        # table ends at a valid clause start in column text, or at long prose after the notes
        if ln.kind != "full" or not self.two_column:
            if self._try_clause(t) if not self.annex else self._try_annex_clause(t):
                return False
        if ln.kind in ("left", "right") and self.in_notes and len(t) > 40 and not re.match(r"^\d\s", t):
            return False
        if self.in_notes:
            if re.match(r"^\d{1,2}\s+\S", t) or not tbl.notes:
                tbl.notes.append(t)
            else:
                tbl.notes[-1] += " " + t
        else:
            tbl.table_rows.append(ln.cells())
        tbl.lines.append(t)
        tbl.page_end = page
        tbl.min_conf = min(tbl.min_conf, ln.mean_conf)
        return True

    def finish(self) -> list[Unit]:
        self._close()
        return self.units


def _amend_targets(s: str) -> list[str]:
    """'clause 21.3, line 3' -> ['cl:21.3'] ; 'Table 9' -> ['table:9'] ; 'B-2.1.1, informal table' -> ['cl:B-2.1.1']"""
    out = []
    for m in re.finditer(r"Table\s+(\d+[A-Z]?)", s, re.I):
        out.append(f"table:{m.group(1)}")
    for m in re.finditer(r"(?:clause|cl\.)\s*([A-Z]-\d+(?:\.\d+)*|\d+(?:\.\d+)+|\d+)", s, re.I):
        out.append(f"cl:{m.group(1)}")
    for m in re.finditer(r"\b([A-Z]-\d+(?:\.\d+)*)", s):
        if f"cl:{m.group(1)}" not in out:
            out.append(f"cl:{m.group(1)}")
    for m in re.finditer(r"Annex\s+([A-Z])", s, re.I):
        out.append(f"annex:{m.group(1)}")
    return out


def _roman_fix(num: str) -> str:
    """OCR'd roman table numbers: 'IIT' -> 'III', 'If' -> 'II', 'V' stays."""
    if re.fullmatch(r"\d{1,2}[A-Z]?", num):
        return num
    return num.replace("T", "I").replace("f", "I").replace("l", "I").replace("1", "I")


def parse_document(doc: SourceDoc, pages: list[PageText]) -> tuple[list[Unit], dict[int, str]]:
    # document-level column decision: legacy single-column codes have tables that look like gutters
    body = [p for p in pages if len(p.words) > 150]
    two_col = bool(body) and sum(has_columns(p) for p in body) / len(body) >= 0.5
    p = ISCodeParser(doc, two_column=two_col)
    for pg in pages:
        p.feed_page(pg)
    return p.finish(), p.page_kinds


# ---------------------------------------------------------------- cross references
XREF_CLAUSE_RE = re.compile(r"(?<![\d.])(\d{1,2}\.\d{1,2}(?:\.\d{1,2}){0,4})(?![\d.]*\d)")
XREF_TABLE_RE = re.compile(r"\bTables?\s+(\d{1,2}[A-Z]?)(?:\s*(?:,|and|to)\s*(\d{1,2}[A-Z]?))?")
XREF_ANNEX_RE = re.compile(r"\bAnnex\s+([A-Z])\b")
XREF_FIG_RE = re.compile(r"\bFig\.?\s*(\d{1,2})")
OF_IS_RE = re.compile(r"^\s*(?:of|in|under|as per)\s+IS\s*:?\s*(\d{3,5})", re.I)


def extract_xrefs(text: str, code_no: str, known_clauses: set[str], own: str | None = None) -> list[str]:
    """Normalized dependency refs: 'IS 456:cl:26.5.1', 'IS 456:table:16', 'IS 456:annex:B', 'IS 383'."""
    refs: list[str] = []

    def add(r):
        if r not in refs:
            refs.append(r)

    def target_code(end: int) -> str | None:
        """'Table 5 of IS 456' / '26.4 of IS 456' -> 'IS 456' (reference into another standard)."""
        m2 = OF_IS_RE.match(text[end:end + 30])
        return f"IS {m2.group(1)}" if m2 else None

    for m in XREF_CLAUSE_RE.finditer(text):
        c = m.group(1)
        other = target_code(m.end())
        if other and other != code_no:
            add(f"{other}:cl:{c}")
        elif c in known_clauses and c != own:
            add(f"{code_no}:cl:{c}")
    for m in XREF_TABLE_RE.finditer(text):
        other = target_code(m.end())
        for g in m.groups():
            if g:
                add(f"{other or code_no}:table:{g}")
    for m in XREF_ANNEX_RE.finditer(text):
        add(f"{code_no}:annex:{m.group(1)}")
    for m in STD_REF_RE.finditer(text):
        std = f"IS {m.group(1)}"
        if std != code_no:
            add(std + (f" (Part {m.group(2)})" if m.group(2) else ""))
    return refs
