"""First-run download of the files the desktop build needs, with one combined progress figure.

Everything is pinned to a Hugging Face commit so every install gets identical files. The large files are
checked against their SHA-256. Partial downloads resume (HTTP Range). User-facing text never names what is
being fetched: the screen only says "Downloading Resources"; names and URLs go to the log file only.

Layout under <CRAG_HOME>/resources:
    engine.gguf   answer model  (hf.co/bartowski/Qwen2.5-3B-Instruct-GGUF:Q4_K_M, same file Ollama pulls)
    search/       embedding model  (BAAI/bge-m3)
    rank/         reranker         (BAAI/bge-reranker-v2-m3)
    ready.json    written when everything is present and verified
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("buildmore.resources")

HF = "https://huggingface.co"
MANIFEST_VERSION = 1


@dataclass(frozen=True)
class Item:
    repo: str
    revision: str
    file: str            # path inside the repo
    dest: str            # path under resources/
    size: int
    sha256: str = ""     # checked for the large files

    @property
    def url(self) -> str:
        return f"{HF}/{self.repo}/resolve/{self.revision}/{self.file}"


# Answer model: change these three lines (and the size / sha256) to ship a different GGUF.
ENGINE_REPO = "bartowski/Qwen2.5-3B-Instruct-GGUF"
ENGINE_REV = "f302c64a2269a69fb27b2f9473b362f5bb8e78d8"
ENGINE_FILE = "Qwen2.5-3B-Instruct-Q4_K_M.gguf"

_M3, _M3_REV, _M3_ST_REV = "BAAI/bge-m3", "5617a9f61b028005a4858fdac845db406aefb181", "9a0624b896d81da7492a910ffa53731274b6cf3d"
_RR, _RR_REV = "BAAI/bge-reranker-v2-m3", "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"

ITEMS: tuple[Item, ...] = (
    Item(ENGINE_REPO, ENGINE_REV, ENGINE_FILE, "engine.gguf", 1929903264,
         "9c9f56a391a3abbd5b89d0245bf6106081bcc3173119d4229235dd9d23253f94"),
    # search (sentence-transformers folder)
    Item(_M3, _M3_ST_REV, "model.safetensors", "search/model.safetensors", 2271064456,
         "993b2248881724788dcab8c644a91dfd63584b6e5604ff2037cb5541e1e38e7e"),
    Item(_M3, _M3_REV, "1_Pooling/config.json", "search/1_Pooling/config.json", 191),
    Item(_M3, _M3_REV, "config.json", "search/config.json", 687),
    Item(_M3, _M3_REV, "config_sentence_transformers.json", "search/config_sentence_transformers.json", 123),
    Item(_M3, _M3_REV, "modules.json", "search/modules.json", 349),
    Item(_M3, _M3_REV, "sentence_bert_config.json", "search/sentence_bert_config.json", 54),
    Item(_M3, _M3_REV, "sentencepiece.bpe.model", "search/sentencepiece.bpe.model", 5069051),
    Item(_M3, _M3_REV, "special_tokens_map.json", "search/special_tokens_map.json", 964),
    Item(_M3, _M3_REV, "tokenizer.json", "search/tokenizer.json", 17098108),
    Item(_M3, _M3_REV, "tokenizer_config.json", "search/tokenizer_config.json", 444),
    # rank (cross-encoder folder)
    Item(_RR, _RR_REV, "model.safetensors", "rank/model.safetensors", 2271071852,
         "d9e3e081faff1eefb84019509b2f5558fd74c1a05a2c7db22f74174fcedb5286"),
    Item(_RR, _RR_REV, "config.json", "rank/config.json", 795),
    Item(_RR, _RR_REV, "sentencepiece.bpe.model", "rank/sentencepiece.bpe.model", 5069051),
    Item(_RR, _RR_REV, "special_tokens_map.json", "rank/special_tokens_map.json", 964),
    Item(_RR, _RR_REV, "tokenizer.json", "rank/tokenizer.json", 17098273),
    Item(_RR, _RR_REV, "tokenizer_config.json", "rank/tokenizer_config.json", 1173),
)

CHUNK = 1 << 20
ATTEMPTS = 4
FRIENDLY_ERROR = "Couldn't download resources. Check your internet connection and try again."


class Downloader:
    """Downloads ITEMS into `root`. Thread-safe `status()` for the setup screen; `retry()` after an error."""

    def __init__(self, root: Path):
        self.root = root
        self._lock = threading.Lock()
        self._retry = threading.Event()
        self._state = {"phase": "preparing", "done": 0, "total": sum(i.size for i in ITEMS),
                       "speed": 0.0, "message": ""}
        self._samples: list[tuple[float, int]] = []

    # ------------------------------------------------------------ status
    def ready(self) -> bool:
        try:
            marker = json.loads((self.root / "ready.json").read_text())
        except (OSError, ValueError):
            return False
        return marker.get("version") == MANIFEST_VERSION and all((self.root / i.dest).is_file() for i in ITEMS)

    def status(self) -> dict:
        with self._lock:
            return dict(self._state)

    def set_phase(self, phase: str, message: str = "") -> None:
        with self._lock:
            self._state.update(phase=phase, message=message)

    def retry(self) -> None:
        self._retry.set()

    # ------------------------------------------------------------ download
    def run_until_ready(self) -> None:
        """Blocks until every item is present and verified. On failure shows the error and waits for retry()."""
        while not self.ready():
            try:
                self._download_all()
            except Exception:  # noqa: BLE001 - any failure is shown as one friendly message
                log.exception("resource download failed")
                self.set_phase("error", FRIENDLY_ERROR)
                self._retry.wait()
                self._retry.clear()

    def _download_all(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._state.update(phase="downloading", message="", done=self._bytes_on_disk(), speed=0.0)
            self._samples = []
        with httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0, read=120.0),
                          headers={"User-Agent": "BuildMoreAI-Desktop"}) as http:
            for item in ITEMS:
                if (self.root / item.dest).is_file():
                    continue
                for attempt in range(1, ATTEMPTS + 1):
                    try:
                        self._fetch(http, item)
                        break
                    except (httpx.HTTPError, OSError) as e:
                        log.warning("attempt %d/%d for %s failed: %s", attempt, ATTEMPTS, item.dest, e)
                        if attempt == ATTEMPTS:
                            raise
                        time.sleep(2 * attempt)
        self.set_phase("verifying")
        for item in ITEMS:
            if item.sha256:
                self._verify(item)
        (self.root / "ready.json").write_text(json.dumps({"version": MANIFEST_VERSION, "at": time.time()}))
        log.info("resources ready")

    def _bytes_on_disk(self) -> int:
        n = 0
        for item in ITEMS:
            final, part = self.root / item.dest, self.root / (item.dest + ".part")
            n += item.size if final.is_file() else (part.stat().st_size if part.is_file() else 0)
        return n

    def _fetch(self, http: httpx.Client, item: Item) -> None:
        final = self.root / item.dest
        part = final.with_name(final.name + ".part")
        part.parent.mkdir(parents=True, exist_ok=True)
        have = part.stat().st_size if part.is_file() else 0
        if have == item.size:                  # finished last time but not yet renamed
            part.replace(final)
            return
        if have > item.size:
            have = self._restart(part, have)
        headers = {"Range": f"bytes={have}-"} if have else {}
        log.info("fetching %s -> %s (resume at %d)", item.url, item.dest, have)
        with http.stream("GET", item.url, headers=headers) as r:
            if r.status_code == 416:          # stale/oversized part file
                have = self._restart(part, have)
                raise httpx.HTTPError("range not satisfiable; restarting file")
            r.raise_for_status()
            if have and r.status_code != 206:  # server ignored Range - start the file again
                have = self._restart(part, have)
            with open(part, "ab" if have else "wb") as f:
                for chunk in r.iter_bytes(CHUNK):
                    f.write(chunk)
                    self._advance(len(chunk))
        if part.stat().st_size != item.size:
            raise OSError(f"{item.dest}: got {part.stat().st_size} bytes, expected {item.size}")
        part.replace(final)

    def _restart(self, part: Path, have: int) -> int:
        part.unlink(missing_ok=True)
        self._advance(-have)
        return 0

    def _advance(self, n: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._state["done"] += n
            self._samples.append((now, self._state["done"]))
            while len(self._samples) > 2 and now - self._samples[0][0] > 5:   # 5 s moving window
                self._samples.pop(0)
            (t0, b0), (t1, b1) = self._samples[0], self._samples[-1]
            if t1 - t0 > 0.5:
                self._state["speed"] = max(b1 - b0, 0) / (t1 - t0)

    def _verify(self, item: Item) -> None:
        path = self.root / item.dest
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(8 * CHUNK):
                h.update(chunk)
        if h.hexdigest() != item.sha256:
            log.error("checksum mismatch for %s", item.dest)
            path.unlink(missing_ok=True)
            with self._lock:
                self._state["done"] -= item.size
            raise OSError(f"checksum mismatch for {item.dest}")


def progress_text(st: dict) -> dict:
    """Numbers for the setup screen: percent, 'x GB of y GB', speed and time left."""
    done, total, speed = st.get("done", 0), st.get("total", 0) or 1, st.get("speed", 0.0)
    speed = speed if speed >= 50_000 else 0.0          # first samples / stalls: show nothing rather than "10 h left"
    left = (total - done) / speed if speed > 0 else None
    return {
        "percent": round(100 * min(done, total) / total, 1),
        "amount": f"{_gb(done)} of {_gb(total)}",
        "speed": f"{speed / 1e6:.1f} MB/s" if speed > 0 else "",
        "eta": _eta(left) if left is not None else "",
    }


def _gb(n: int) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.0f} MB"


def _eta(s: float) -> str:
    if s < 60:
        return "less than a minute left"
    m = round(s / 60)
    if m < 60:
        return f"about {m} min left"
    return f"about {m // 60} h {m % 60} min left"
