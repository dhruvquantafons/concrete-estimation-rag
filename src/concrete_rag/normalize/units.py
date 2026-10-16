"""Unit normalization for SOR/IS text. Maps the many spellings used in Indian schedules
(cum, cu.m, m3, sqm, Rmt, MT, qtl, bag ...) to canonical symbols, and converts quantities via pint."""
from __future__ import annotations

import re
from functools import lru_cache

import pint

# canonical symbol -> aliases (lower-case, punctuation stripped)
UNIT_ALIASES: dict[str, list[str]] = {
    "m3": ["cum", "cu m", "cum.", "cu.m", "cubic metre", "cubic meter", "m3", "m³", "cmt"],
    "m2": ["sqm", "sq m", "sq.m", "sq. m", "square metre", "square meter", "m2", "m²", "smt"],
    "m": ["metre", "meter", "m", "rm", "rmt", "running metre", "rm.", "mtr", "metres", "meters", "lm"],
    "kg": ["kg", "kgs", "kilogram", "kilograms"],
    "t": ["tonne", "tonnes", "mt", "metric tonne", "ton", "tons", "t"],
    "quintal": ["quintal", "qtl", "qtls"],
    "l": ["litre", "liter", "ltr", "l", "litres"],
    "ml": ["ml", "millilitre"],
    "each": ["each", "no", "nos", "no.", "nos.", "number", "numbers", "per no", "pair", "set"],
    "day": ["day", "days", "per day"],
    "hour": ["hour", "hr", "hrs", "per hour"],
    "month": ["month", "per month"],
    "km": ["km", "kilometre"],
    "bag": ["bag", "bags"],               # cement bag = 50 kg
    "job": ["job", "ls", "lump sum", "l.s."],
    "point": ["point", "points"],
    "sqm_per_coat": ["sqm per coat"],
    "100m2": ["100 sqm", "per 100 sqm"],
    "10m2": ["10 sqm"],
    "1000_nos": ["1000 nos", "per 1000"],
    "kl": ["kl", "kilolitre"],
}

_LOOKUP = {alias: canon for canon, aliases in UNIT_ALIASES.items() for alias in aliases}
# frequent OCR corruptions seen in the scanned DSR
_OCR_FIX = {"mi": "ml", "sgm": "sqm", "sqrn": "sqm", "cun": "cum", "curn": "cum", "kq": "kg", "eaeh": "each",
            "eacn": "each", "rnetre": "metre", "metr": "metre", "sam": "sqm", "cuim": "cum"}


def normalize_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    s = raw.strip().lower().replace("per ", "").strip(" .,;:")
    s = _OCR_FIX.get(s, s)
    s = re.sub(r"\s+", " ", s)
    if s in _LOOKUP:
        return _LOOKUP[s]
    s2 = s.replace(".", "").replace(" ", "")
    for alias, canon in _LOOKUP.items():
        if s2 == alias.replace(".", "").replace(" ", ""):
            return canon
    return None


KNOWN_UNIT_TOKENS = sorted({a for a in _LOOKUP} | set(_OCR_FIX), key=len, reverse=True)


@lru_cache
def ureg() -> pint.UnitRegistry:
    u = pint.UnitRegistry()
    u.define("quintal = 100 * kilogram = qtl")
    u.define("bag = 50 * kilogram")   # cement bag
    return u


def convert(value: float, from_unit: str, to_unit: str) -> float:
    """Convert between canonical units (e.g. 'kg' -> 't', 'm3' -> 'l')."""
    u = ureg()
    canon = {"m3": "meter**3", "m2": "meter**2", "m": "meter", "t": "tonne", "l": "liter", "ml": "milliliter",
             "kl": "kiloliter"}
    return (value * u(canon.get(from_unit, from_unit))).to(canon.get(to_unit, to_unit)).magnitude
