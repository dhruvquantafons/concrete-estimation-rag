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
    "750_ml": ["750 ml"],                           # basic rate 1254 PU foam can
    "each": ["each", "no", "nos", "no.", "nos.", "number", "numbers", "per no"],
    "set": ["set", "sets"],
    "10_nos": ["10 nos", "10 no"],
    "100_nos": ["100 nos", "100 no"],
    "10_sets": ["10 sets"],
    "1000_l": ["1000 litre", "1000 litres"],
    "pair": ["pair", "pairs"],                      # a pair is 2 pieces - never "each"
    "day": ["day", "days", "per day"],
    "hour": ["hour", "hr", "hrs", "per hour"],
    "month": ["month", "per month"],
    "km": ["km", "kilometre"],
    "cm": ["cm", "centimetre"],                     # DSR 10.22 welding, per cm of weld
    "bag": ["bag", "bags"],               # cement bag = 50 kg
    "job": ["job", "ls", "lump sum", "l.s."],
    "point": ["point", "points"],
    "sqm_per_coat": ["sqm per coat"],
    "100m2": ["100 sqm", "per 100 sqm"],
    "10m2": ["10 sqm"],
    "1000_nos": ["1000 nos", "per 1000"],
    "100m": ["100 m", "100 metre", "100 metres"],   # DSR 1.2.16.x carriage of S.W. pipes
    "m_depth": ["metre depth", "m depth"],          # DSR 2.24.x: a % extra per metre depth, not a ₹ rate
    "m3_m_depth": ["cum per metre depth", "cum metre depth"],          # DSR 4.15: qty = cum x depth of centre of gravity
    "each_set": ["each set", "eachset", "per set"],            # DSR 5.10.x tie bolt + spring coil + cone
    "one_set": ["one set", "oneset"],               # DSR 9.115 telescopic drawer channels
    "cm_depth_100m": ["cm depth 100m", "cm depth 100 m"],
    "cm_depth_cm_width_m": ["cm depth cm width metre length"],   # DSR 16.45 pre-moulded joint filler   # DSR 5.28 fibre board in expansion joints
    "kl": ["kl", "kilolitre", "kilo litre"],
    "cm_per_m": ["cm metre"],                       # DSR 13.28-13.30 mortar bands: per cm (width) per metre
    "letter_cm_height": ["letter cm height"],       # DSR 13.71/13.105 lettering: per letter per cm height
    "wheel": ["wheel"],
    "bundle": ["bundle", "bundles"],                # basic rate 8691 concertina coil
    "roll": ["roll", "each roll"],                  # basic rates 7018 / 7026 joint tape
    "cartridge": ["cartridge", "cartridges"],      # basic rate 2628 weather sealant
    "dm3": ["cudm", "cu dm", "cubic decimetre"],
    "10_dm3": ["10 cudm", "10 cu dm"],              # DSR basic rates for timber/stone blocks    # DSR 26.6 bamboo door frame (1 cudm = 0.001 cum)
    "each_hole": ["each hole", "eachhole"],          # DSR 22.25.2 tie rod holes
    "test": ["test", "pertest"],                    # DSR 20.6.x pile load tests
    "joint": ["joint", "joints"],                   # DSR 18.70.x push-on joints
    "each_cut": ["each cut", "eachcut"],            # DSR 18.83.x cutting C.I. pipes
    "m_per_m_height": ["metre metre height"],       # DSR 15.16.2 extra per metre height
    "m3_per_m_span": ["cum metre span"],            # DSR 15.15.1
    "m_per_m_span": ["metre metre span"],           # DSR 15.15.2
    "m_cm_girth": ["metre cm girth (60cm deep)", "metre cm girth"],   # DSR 7.22 mouldings in cornices
    "each_job": ["each job", "eachjob"],            # DSR 14.93/14.94 security guard (one job = 8 h duty)                             # DSR 14.22.x
}

_LOOKUP = {alias: canon for canon, aliases in UNIT_ALIASES.items() for alias in aliases}
# frequent OCR corruptions seen in the scanned DSR
_OCR_FIX = {"mi": "ml", "750 mi": "750 ml", "sgm": "sqm", "sqrn": "sqm", "cun": "cum", "curn": "cum", "kq": "kg", "eaeh": "each",
            "eacn": "each", "rnetre": "metre", "metr": "metre", "sam": "sqm", "cuim": "cum"}


def normalize_unit(raw: str | None) -> str | None:
    if not raw:
        return None
    s = re.sub(r"^[^a-z0-9]+", "", raw.strip().lower())          # leading OCR junk: "_", "‘", "—", "|="
    s = re.sub(r"(^|\s)per\s*", r"\1", s).strip(" .,;:")           # "per cm", glued "percm" / "pertest"
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
