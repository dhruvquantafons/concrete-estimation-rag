"""SOR item-code normalization and OCR repair.

Scanned DSR pages frequently lose the dots in item codes ("414" for 4.1.4, "5.12" for 5.1.2), read a
suffix letter as a digit ("4.1.54" for 4.1.5A) or confuse digits ("543" for 5.1.3). Codes inside a
sub-head are almost sequential, so we decode the whole sequence of raw codes at once (Viterbi over
candidate readings) choosing the readings that form the most plausible code sequence.
"""
from __future__ import annotations

import itertools
import re

CODE_RE = re.compile(r"^(\d+(?:\.\d+)*)([A-Z]?)$")
_DIGIT_AS_LETTER = {"4": "A", "8": "AB", "6": "B", "0": "C"}
_CONFUSABLE = {"1": "47", "4": "1", "7": "1", "3": "8", "8": "36", "5": "6", "6": "58", "0": "8"}
SUBST_PENALTY = 1.5
INFIX_RE = re.compile(r"^(\d{1,2}\.\d{1,2})([A-H])\.?(\d{1,2}(?:\.\d{1,2})?)$")   # 5.22A1 -> 5.22A.1
REVIEW_COST = 2.5


def canonical_key(sor_name: str, year: int, code: str) -> str:
    return f"{sor_name}-{year}:{code}"


def parse_code(code: str) -> tuple[tuple[int, ...], str] | None:
    m = CODE_RE.match(code)
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split(".")), m.group(2)


def parent_code(code: str) -> str | None:
    p = parse_code(code)
    if not p:
        # letter inside the code (5.22A.6 -> 5.22A, 9.147.F1.1 -> 9.147.F1): drop the last component
        parts = code.split(".")
        return ".".join(parts[:-1]) if len(parts) > 1 and all(parts) else None
    nums, letter = p
    if letter:  # 4.1.5A's parent is 4.1
        return ".".join(map(str, nums[:-1])) if len(nums) > 1 else None
    return ".".join(map(str, nums[:-1])) if len(nums) > 1 else None


def code_sort_key(code: str) -> tuple:
    p = parse_code(code)
    return (p[0], p[1]) if p else ((999,), code)


def _clean(raw: str) -> str:
    s = raw.strip().upper().replace(",", ".").replace(" ", ".")
    s = re.sub(r"\.+", ".", s)
    if re.fullmatch(r"[\dOIL.]+[A-Z]?", s):
        body, last = (s[:-1], s[-1]) if s[-1].isalpha() and s[-1] not in "OIL" else (s, "")
        s = body.replace("O", "0").replace("I", "1").replace("L", "1") + last
    return s.strip(".")


def _dot_splits(body: str) -> list[list[str]]:
    segs = body.split(".")
    gaps = [(si, ci) for si, seg in enumerate(segs) for ci in range(1, len(seg))]
    out = []
    for mask in itertools.product([False, True], repeat=min(len(gaps), 7)):
        insert = {g for g, on in zip(gaps, mask) if on}
        pieces = []
        for si, seg in enumerate(segs):
            buf = seg[0]
            for ci in range(1, len(seg)):
                if (si, ci) in insert:
                    pieces.append(buf)
                    buf = ""
                buf += seg[ci]
            pieces.append(buf)
        out.append(pieces)
    return out


def candidates(raw: str, head: int | None) -> dict[str, float]:
    """Plausible readings of an OCR'd code string -> intrinsic penalty (0 = as read)."""
    s = _clean(raw)
    variants: list[tuple[str, str, float]] = []
    m = re.fullmatch(r"([\d.]+?)\.?([A-Z])", s)
    if m:
        variants.append((m.group(1), m.group(2), 0.0))
    elif re.fullmatch(r"[\d.]+", s or "x"):
        variants.append((s, "", 0.0))
        if s[-1] in _DIGIT_AS_LETTER and len(s.replace(".", "")) >= 3:
            for k, letter in enumerate(_DIGIT_AS_LETTER[s[-1]]):
                variants.append((s[:-1], letter, 0.3 + 0.3 * k))
    # single-digit confusions (never in the leading head number)
    subs = []
    for body, letter, pen in variants:
        hl = len(str(head)) if head is not None else 1
        for i, ch in enumerate(body):
            if i < hl or ch not in _CONFUSABLE:
                continue
            for alt in _CONFUSABLE[ch]:
                subs.append((body[:i] + alt + body[i + 1:], letter, pen + SUBST_PENALTY))
        # a doubled digit read once ("51" for 5.11)
        for i in range(hl, len(body)):
            if body[i].isdigit():
                subs.append((body[:i] + body[i] + body[i:], letter, pen + SUBST_PENALTY))
    out: dict[str, float] = {}
    for body, letter, pen in variants + subs:
        body = body.strip(".")
        if not body:
            continue
        for pieces in _dot_splits(body):
            if any(p == "" or (len(p) > 1 and p.startswith("0")) for p in pieces):
                continue
            if any(int(p) > 99 for p in pieces[1:]) or len(pieces) > 4:
                continue
            if head is not None and pieces[0] != str(head):
                continue
            cand = ".".join(pieces) + letter
            out[cand] = min(out.get(cand, 99.0), pen)
    return out


def successor_cost(prev: str | None, cand: str, has_rate: bool | None = None,
                   prev_has_rate: bool | None = None) -> float:
    """Lower is more plausible. 0 = the obvious next code after `prev`.

    Moves allowed (with increasing cost): first child (5.1 -> 5.1.1), sibling increment
    (5.1.2 -> 5.1.3), climbing up and incrementing an ancestor (5.1.4 -> 5.2 / 5.2.1),
    letter suffix variants (4.1.3 -> 4.1.3A -> 4.1.3B). Skipped numbers (deleted items) cost extra.
    """
    pc = parse_code(cand)
    if pc is None:
        return 99.0
    cn, cl = pc
    # rows without a rate are usually group headings (X.Y); rows with a rate are usually leaves
    shape = 0.0
    if has_rate is False and (len(cn) > 2 or cl):
        shape += 0.8
    if has_rate is True and len(cn) == 1:
        shape += 2.0
    if prev is None:
        return 1.0 + 0.5 * len(cn) + 0.1 * sum(x - 1 for x in cn[1:]) + shape
    pp = parse_code(prev)
    if pp is None:
        return 5.0 + shape
    pn, pl = pp
    best = 50.0
    if cn == pn:
        if cl and ((not pl and cl == "A") or (pl and ord(cl) == ord(pl) + 1)):
            best = 0.2 if prev_has_rate is not False else 4.0   # a heading has no "A" variant
        return best + shape
    # descendant of prev: 5.1 -> 5.1.1 (cheap), 5.1 -> 5.1.2 (first child deleted)
    if len(cn) > len(pn) and cn[: len(pn)] == pn and not cl:
        extra = cn[len(pn):]
        best = min(best, 0.1 * len(extra) + 0.6 * sum(x - 1 for x in extra))
    # increment at level j (0-based), possibly followed by first-children
    for j in range(min(len(cn), len(pn))):
        if cn[:j] != pn[:j]:
            break
        step = cn[j] - pn[j]
        if step < 1 or step > 8:
            continue
        climb = 0.3 * (len(pn) - 1 - j)
        tail = cn[j + 1:]
        cost = climb + 0.6 * (step - 1) + 0.1 * len(tail) + 0.6 * sum(x - 1 for x in tail) + (0.4 if cl else 0)
        best = min(best, cost)
    return best + shape


def decode_sequence(raws: list[str], head: int | None, has_rates: list[bool | None] | None = None,
                    prev: str | None = None) -> list[tuple[str, float]]:
    """Viterbi decoding of a run of raw OCR codes. Returns [(code, local_cost)] per row.
    local_cost >= REVIEW_COST means the reading is doubtful and must be reviewed."""
    if not raws:
        return []
    has_rates = has_rates or [None] * len(raws)
    infix = [INFIX_RE.match(_clean(r)) for r in raws]
    if any(infix):
        keep = [i for i, m in enumerate(infix) if not m]
        sub = decode_sequence([raws[i] for i in keep], head, [has_rates[i] for i in keep], prev)
        out: list[tuple[str, float]] = [("", 0.0)] * len(raws)
        for i, r in zip(keep, sub):
            out[i] = r
        for i, m in enumerate(infix):
            if m:
                out[i] = (f"{m.group(1)}{m.group(2)}.{m.group(3)}", 0.5)
        return out
    lattice = []
    for raw in raws:
        c = candidates(raw, head)
        lattice.append(c or {_clean(raw): 10.0})
    # dp[i][cand] = (total, back)
    dp: list[dict[str, tuple[float, str | None]]] = []
    first = {}
    for cand, pen in lattice[0].items():
        first[cand] = (pen + successor_cost(prev, cand, has_rates[0]), None)
    dp.append(first)
    for i in range(1, len(raws)):
        layer = {}
        for cand, pen in lattice[i].items():
            best = None
            for pc, (tot, _) in dp[i - 1].items():
                v = tot + pen + successor_cost(pc, cand, has_rates[i], has_rates[i - 1])
                if best is None or v < best[0]:
                    best = (v, pc)
            layer[cand] = best
        dp.append(layer)
    # backtrack
    cand = min(dp[-1], key=lambda k: dp[-1][k][0])
    path = [cand]
    for i in range(len(raws) - 1, 0, -1):
        cand = dp[i][cand][1]
        path.append(cand)
    path.reverse()
    out = []
    for i, code in enumerate(path):
        p = path[i - 1] if i else prev
        local = lattice[i][code] + successor_cost(p, code, has_rates[i], has_rates[i - 1] if i else None)
        out.append((code, local))
    return out
