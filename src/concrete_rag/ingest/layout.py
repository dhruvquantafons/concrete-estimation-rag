"""Layout-aware line reconstruction from word boxes.

IS codes are typeset in two columns with full-width tables/figures in between. Neither the legacy
text layers nor Tesseract keep a reliable reading order on such pages, so we rebuild it ourselves:

1. cluster words into physical lines by vertical position;
2. find the column gutter (the x near the page centre crossed by the fewest words);
3. classify every line: `full` (crosses the gutter, or is a table row with several wide gaps),
   or `left` / `right` column fragments;
4. emit bands: consecutive column lines are read left column first, then right column;
   a full-width line closes the band and is emitted in place.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from concrete_rag.schemas import PageText, Word

WIDE_GAP = 12.0     # points; larger than normal word spacing, smaller than column gutter
TABLE_GAP = 20.0    # gaps this wide (away from the gutter) only occur between table columns


@dataclass
class Line:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    kind: str                      # full | left | right | single
    page: int
    words: list[Word] = field(default_factory=list)

    @property
    def min_conf(self) -> float:
        cs = [w.conf for w in self.words if w.conf >= 0]
        return min(cs) if cs else 100.0

    @property
    def mean_conf(self) -> float:
        cs = [w.conf for w in self.words if w.conf >= 0]
        return sum(cs) / len(cs) if cs else 100.0

    def cells(self, gap: float = WIDE_GAP) -> list[str]:
        """Split the line into table cells at wide horizontal gaps."""
        out, cur, last = [], [], None
        for w in self.words:
            if last is not None and w.x0 - last > gap:
                out.append(" ".join(cur))
                cur = []
            cur.append(w.text)
            last = w.x1
        if cur:
            out.append(" ".join(cur))
        return out


def _group_lines(words: list[Word]) -> list[list[Word]]:
    if not words:
        return []
    heights = [w.y1 - w.y0 for w in words if w.y1 > w.y0]
    tol = (statistics.median(heights) if heights else 8.0) * 0.55
    ws = sorted(words, key=lambda w: ((w.y0 + w.y1) / 2, w.x0))
    lines: list[list[Word]] = []
    centers: list[float] = []
    for w in ws:
        c = (w.y0 + w.y1) / 2
        if lines and abs(c - centers[-1]) <= tol:
            lines[-1].append(w)
            centers[-1] = sum((x.y0 + x.y1) / 2 for x in lines[-1]) / len(lines[-1])
        else:
            lines.append([w])
            centers.append(c)
    return [sorted(line, key=lambda w: w.x0) for line in lines]


def find_gutter(lines: list[list[Word]], width: float) -> float | None:
    """x position of a two-column gutter, or None for single-column pages."""
    if width <= 0 or len(lines) < 8:
        return None
    lo, hi = width * 0.40, width * 0.60
    best_x, best_cross = None, None
    for step in range(41):
        x = lo + (hi - lo) * step / 40
        cross = sum(1 for ln in lines for w in ln if w.x0 < x < w.x1)
        if best_cross is None or cross < best_cross:
            best_x, best_cross = x, cross
    # need text on both sides on many lines, and the gutter crossed rarely
    both = sum(1 for ln in lines if any(w.x1 < best_x for w in ln) and any(w.x0 > best_x for w in ln))
    if both < 0.25 * len(lines) or best_cross > 0.35 * len(lines):
        return None
    return best_x


def _join(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def has_columns(page: PageText) -> bool:
    words = [w if isinstance(w, Word) else Word(**w) for w in page.words]
    return find_gutter(_group_lines(words), page.width) is not None


def page_lines(page: PageText, allow_columns: bool = True) -> list[Line]:
    words = [w if isinstance(w, Word) else Word(**w) for w in page.words]
    raw = _group_lines(words)
    gutter = find_gutter(raw, page.width) if allow_columns else None
    out: list[Line] = []
    band_l: list[Line] = []
    band_r: list[Line] = []

    def mk(ws: list[Word], kind: str) -> Line:
        return Line(text=_join(ws), x0=min(w.x0 for w in ws), y0=min(w.y0 for w in ws),
                    x1=max(w.x1 for w in ws), y1=max(w.y1 for w in ws), kind=kind, page=page.page, words=ws)

    def flush():
        out.extend(band_l)
        out.extend(band_r)
        band_l.clear()
        band_r.clear()

    for ln in raw:
        if gutter is None:
            out.append(mk(ln, "single"))
            continue
        crosses = any(w.x0 < gutter < w.x1 for w in ln)
        gaps = [(ln[i + 1].x0 - ln[i].x1, (ln[i].x1 + ln[i + 1].x0) / 2) for i in range(len(ln) - 1)]
        wide_off_gutter = [g for g in gaps if g[0] > TABLE_GAP and abs(g[1] - gutter) > 25]
        wide_left = [g for g in wide_off_gutter if g[1] < gutter]
        wide_right = [g for g in wide_off_gutter if g[1] > gutter]
        left = [w for w in ln if w.x1 <= gutter]
        right = [w for w in ln if w.x0 >= gutter]
        # full-width table row: table-style gaps on *both* sides of the gutter -> keep the row intact.
        # (a column-width table beside prose has gaps on one side only and must be split.)
        if crosses or (wide_left and wide_right):
            flush()
            out.append(mk(ln, "full"))
        else:
            if left:
                band_l.append(mk(left, "left"))
            if right:
                band_r.append(mk(right, "right"))
    flush()
    return out
