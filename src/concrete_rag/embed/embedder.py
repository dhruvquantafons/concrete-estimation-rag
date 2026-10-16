"""Dense embeddings (sentence-transformers, local; bge-m3 by default) and a domain-aware BM25
sparse encoder whose vectors are stored in Qdrant with the IDF modifier (server-side IDF).

The BM25 tokenizer keeps engineering tokens intact - clause numbers (26.5.1.1), grades (M25, Fe500),
mix ratios (1:1.5:3), code references (IS 456 -> is456) and SOR item codes (5.33.1.1) - which a
generic tokenizer would shred."""
from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

from concrete_rag.config import get_settings

STOP = set("""a an the of and or to in on for by with at from as is are be shall should may can this that these those
which where when than then into its it such any all not no per under upto up""".split())

_TOKEN_RE = re.compile(
    r"is\s?:?\s?\d{3,5}"                 # IS 456 / IS:456
    r"|\d+(?:\.\d+){1,5}[a-z]?"          # 26.5.1.1, 5.33.1.1, 0.45, 4.1.5a
    r"|\d+(?::\d+(?:\.\d+)?){1,3}"       # 1:1.5:3
    r"|[a-z]+\d+[a-z]*|\d+[a-z]+"        # m25, fe500, 20mm, 16a
    r"|[a-z]+(?:'[a-z]+)?|\d+",
    re.I,
)


def tokenize(text: str) -> list[str]:
    out = []
    for m in _TOKEN_RE.finditer(text.lower()):
        t = m.group(0)
        if re.match(r"is\s?:?\s?\d", t):          # "is 456" / "is:456" -> "is456"
            t = re.sub(r"[\s:]", "", t)
        if t in STOP or len(t) == 1 and not t.isdigit():
            continue
        if t.isalpha() and len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def _hid(tok: str) -> int:
    return int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=4).digest(), "little") & 0x7FFFFFFF


class BM25Encoder:
    """Document side: BM25 term-frequency saturation + length normalisation; IDF is applied by Qdrant."""

    def __init__(self, k1: float = 1.2, b: float = 0.75, avgdl: float | None = None):
        self.k1, self.b, self.avgdl = k1, b, avgdl

    def fit(self, texts: list[str]) -> "BM25Encoder":
        lens = [len(tokenize(t)) for t in texts]
        self.avgdl = sum(lens) / max(len(lens), 1)
        return self

    def encode_doc(self, text: str) -> tuple[list[int], list[float]]:
        toks = tokenize(text)
        tf = Counter(toks)
        dl = len(toks) or 1
        avg = self.avgdl or dl
        idx, val = [], []
        merged: dict[int, float] = {}
        for t, f in tf.items():
            w = f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / avg))
            h = _hid(t)
            merged[h] = merged.get(h, 0) + w
        for h, w in merged.items():
            idx.append(h)
            val.append(float(w))
        return idx, val

    @staticmethod
    def encode_query(text: str) -> tuple[list[int], list[float]]:
        merged: dict[int, float] = {}
        for t in tokenize(text):
            merged[_hid(t)] = 1.0
        return list(merged), list(merged.values())

    def save(self, path: Path):
        path.write_text(json.dumps({"k1": self.k1, "b": self.b, "avgdl": self.avgdl}))

    @classmethod
    def load(cls, path: Path) -> "BM25Encoder":
        d = json.loads(path.read_text()) if path.exists() else {}
        return cls(**d) if d else cls()


def _device() -> str:
    s = get_settings()
    if s.device:
        return s.device
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@lru_cache
def dense_model():
    from sentence_transformers import SentenceTransformer
    s = get_settings()
    m = SentenceTransformer(s.embed_model, device=_device())
    m.max_seq_length = s.embed_max_seq_len
    return m


def embed_texts(texts: list[str], batch_size: int | None = None, show_progress: bool = False) -> list[list[float]]:
    s = get_settings()
    vecs = dense_model().encode(texts, batch_size=batch_size or s.embed_batch_size, normalize_embeddings=True,
                                show_progress_bar=show_progress, convert_to_numpy=True)
    return vecs.tolist()


def embed_query(q: str) -> list[float]:
    return embed_texts([q])[0]


def dense_dim() -> int:
    return dense_model().get_sentence_embedding_dimension()
