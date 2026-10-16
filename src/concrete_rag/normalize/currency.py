"""Indian currency parsing/formatting (₹, Rs., INR, lakh, crore) and cost-index adjustment."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

_MULT = {"lakh": Decimal(100_000), "lakhs": Decimal(100_000), "lac": Decimal(100_000), "crore": Decimal(10_000_000),
         "crores": Decimal(10_000_000), "cr": Decimal(10_000_000), "thousand": Decimal(1000)}


def parse_inr(raw: str | None) -> Decimal | None:
    """'₹ 1,23,456.50' / 'Rs. 8340.85' / '2.5 lakh' -> Decimal. Returns None if no number."""
    if raw is None:
        return None
    s = str(raw).lower().replace("₹", " ").replace("rs.", " ").replace("rs", " ").replace("inr", " ")
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lac|crores?|cr|thousand)?", s)
    if not m:
        return None
    val = Decimal(m.group(1).replace(",", ""))
    if m.group(2):
        val *= _MULT[m.group(2)]
    return val


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_inr(value: float | Decimal) -> str:
    """Indian digit grouping: 12345678.9 -> '₹ 1,23,45,678.90'."""
    v = money(value)
    sign = "-" if v < 0 else ""
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        head = ",".join([head[max(0, i - 2):i] for i in range(len(head), 0, -2)][::-1])
        whole = f"{head},{tail}"
    return f"{sign}₹ {whole}.{frac}"


def apply_cost_index(rate: float, index: float, base: float = 100.0) -> float:
    """CPWD practice: rate at location = DSR rate x (location cost index / base)."""
    return rate * index / base
