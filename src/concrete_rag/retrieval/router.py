"""Query understanding: pull explicit references out of the query so they can be served by exact
lookup ("cl. 26.4.2 of IS 456", "Table 5", "DSR item 5.33.1.1") before semantic search runs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CODE_RE = re.compile(r"\bIS\s*:?\s*(\d{3,5})(?:\s*\(\s*Part\s*([0-9IVX]+)\s*\))?", re.I)
CLAUSE_RE = re.compile(r"\b(?:clause|cl\.?|para)\s*([A-Z]-)?(\d{1,2}(?:\.\d{1,2}){0,5})\b", re.I)
TABLE_RE = re.compile(r"\btable\s+(\d{1,2}[A-Z]?|[IVX]{1,5})\b", re.I)
ANNEX_RE = re.compile(r"\bannex(?:ure)?\s+([A-Z])\b", re.I)
ITEM_RE = re.compile(r"\b(?:item|dsr|sor|code)\s*(?:no\.?|code|number)?\s*[:#]?\s*(\d{1,2}(?:\.\d{1,3}){1,3}[A-Z]?)\b", re.I)
SOR_HINT = re.compile(r"\b(rate|cost|dsr|sor|schedule of rates|cpwd|per cum|per sqm|₹|rs\.?|price|estimate)\b", re.I)
_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6", "VII": "7", "VIII": "8"}


@dataclass
class ParsedQuery:
    text: str
    code_no: str | None = None
    part: str | None = None
    clauses: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    annexes: list[str] = field(default_factory=list)
    items: list[str] = field(default_factory=list)
    wants_rates: bool = False

    @property
    def has_exact(self) -> bool:
        return bool(self.clauses or self.tables or self.items)


def parse_query(q: str) -> ParsedQuery:
    pq = ParsedQuery(text=q)
    m = CODE_RE.search(q)
    if m:
        pq.code_no = f"IS {m.group(1)}"
        if m.group(2):
            pq.part = _ROMAN.get(m.group(2).upper(), m.group(2))
    for m in CLAUSE_RE.finditer(q):
        pq.clauses.append(f"{m.group(1) or ''}{m.group(2)}")
    for m in TABLE_RE.finditer(q):
        pq.tables.append(m.group(1).upper() if not m.group(1)[0].isdigit() else m.group(1))
    pq.annexes = [m.group(1).upper() for m in ANNEX_RE.finditer(q)]
    pq.items = [m.group(1).upper() for m in ITEM_RE.finditer(q)]
    pq.wants_rates = bool(SOR_HINT.search(q))
    return pq
