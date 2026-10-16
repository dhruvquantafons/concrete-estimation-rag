"""Rule-based metadata enrichment from controlled vocabularies (config/vocab/enrichment.yaml)."""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from concrete_rag.config import get_settings

FACETS = ("material_type", "work_item", "element_type", "applicability")


@lru_cache
def _vocab() -> dict[str, dict[str, list[re.Pattern]]]:
    raw = yaml.safe_load((get_settings().root / "config" / "vocab" / "enrichment.yaml").read_text())
    return {facet: {term: [re.compile(p, re.I) for p in pats] for term, pats in terms.items()}
            for facet, terms in raw.items()}


def tag(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for facet, terms in _vocab().items():
        out[facet] = [term for term, pats in terms.items() if any(p.search(text) for p in pats)]
    return out


GRADE_RE = re.compile(r"\bM\s?(\d{2,3})\b")


def grades(text: str) -> list[str]:
    return sorted({f"M{g}" for g in GRADE_RE.findall(text) if 5 <= int(g) <= 100}, key=lambda s: int(s[1:]))
