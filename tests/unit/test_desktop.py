import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from concrete_rag.api import auth
from concrete_rag.desktop import launcher, resources
from concrete_rag.desktop.resources import Downloader, Item, progress_text


def test_crag_home_relocates_every_path(tmp_path):
    code = "from concrete_rag.config import Settings; s = Settings(); print(s.root, s.storage_dir, s.sources_file, sep='|')"
    out = subprocess.run([sys.executable, "-c", code], env={**os.environ, "CRAG_HOME": str(tmp_path)},
                         capture_output=True, text=True, check=True).stdout.strip().split("|")
    assert [Path(p) for p in out] == [tmp_path, tmp_path / "storage", tmp_path / "config" / "sources.yaml"]


def _req():
    return SimpleNamespace(state=SimpleNamespace())


def test_desktop_mode_signs_in_without_key(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(desktop_mode=True, api_bootstrap_admin_key=""))
    p = auth.principal(_req(), key=None)
    assert p["desktop"] is True and set(p["scopes"]) == auth.SCOPES


def test_server_mode_still_needs_key(monkeypatch):
    monkeypatch.setattr(auth, "get_settings", lambda: SimpleNamespace(desktop_mode=False, api_bootstrap_admin_key=""))
    with pytest.raises(HTTPException) as e:
        auth.principal(_req(), key=None)
    assert e.value.status_code == 401


def test_progress_text():
    t = progress_text({"done": 1_800_000_000, "total": 6_500_000_000, "speed": 10e6})
    assert t["percent"] == 27.7
    assert t["amount"] == "1.80 GB of 6.50 GB"
    assert t["speed"] == "10.0 MB/s"
    assert t["eta"] == "about 8 min left"
    assert progress_text({"done": 0, "total": 10, "speed": 0})["eta"] == ""


def _ranged_server(payload: bytes, seen: list):
    def handle(req: httpx.Request) -> httpx.Response:
        rng = req.headers.get("range")
        seen.append(rng)
        if rng:
            start = int(re.match(r"bytes=(\d+)-", rng).group(1))
            return httpx.Response(206, content=payload[start:])
        return httpx.Response(200, content=payload)
    return httpx.Client(transport=httpx.MockTransport(handle))


def test_download_resumes_from_part_file(tmp_path):
    payload = bytes(range(256)) * 40
    item = Item("org/repo", "rev", "f.bin", "sub/f.bin", len(payload))
    part = tmp_path / "sub" / "f.bin.part"
    part.parent.mkdir()
    part.write_bytes(payload[:1000])
    seen = []
    dl = Downloader(tmp_path)
    dl._fetch(_ranged_server(payload, seen), item)
    assert seen == ["bytes=1000-"]
    assert (tmp_path / "sub" / "f.bin").read_bytes() == payload and not part.exists()


def test_checksum_mismatch_deletes_file(tmp_path, monkeypatch):
    item = Item("org/repo", "rev", "f.bin", "f.bin", 3, sha256="0" * 64)
    monkeypatch.setattr(resources, "ITEMS", (item,))
    (tmp_path / "f.bin").write_bytes(b"abc")
    with pytest.raises(OSError):
        Downloader(tmp_path)._verify(item)
    assert not (tmp_path / "f.bin").exists()


def test_ready_needs_marker_and_files(tmp_path, monkeypatch):
    item = Item("org/repo", "rev", "f.bin", "f.bin", 1)
    monkeypatch.setattr(resources, "ITEMS", (item,))
    dl = Downloader(tmp_path)
    (tmp_path / "f.bin").write_bytes(b"x")
    assert not dl.ready()
    (tmp_path / "ready.json").write_text('{"version": %d}' % resources.MANIFEST_VERSION)
    assert dl.ready()


def test_seed_copies_once_and_keeps_user_edits(tmp_path, monkeypatch):
    src, home = tmp_path / "bundle", tmp_path / "home"
    for rel in ("config/a.yaml", "storage/docstore.db", "storage/docstore.backup-1.db", "storage/models/x.bin",
                "data/IS 456/is456.pdf", "data/interim/p1.txt"):
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(rel)
    monkeypatch.setattr(launcher, "seed_dir", lambda: src)
    (home / "config").mkdir(parents=True)
    (home / "config" / "a.yaml").write_text("edited")
    launcher.seed(home)
    assert (home / "config" / "a.yaml").read_text() == "edited"
    assert (home / "storage" / "docstore.db").is_file() and (home / "data" / "IS 456" / "is456.pdf").is_file()
    assert not (home / "storage" / "docstore.backup-1.db").exists()
    assert not (home / "storage" / "models").exists() and not (home / "data" / "interim").exists()


def test_setup_screen_never_names_the_ai():
    html = (Path(launcher.__file__).with_name("setup.html")).read_text()
    visible = re.sub(r"<style>.*?</style>|<script>.*?</script>|<[^>]+>", " ", html, flags=re.S)
    script_strings = " ".join(re.findall(r'"([^"]*)"', re.search(r"const TEXT = \{.*?\};", html, re.S).group(0)))
    for text in (visible, script_strings, resources.FRIENDLY_ERROR, launcher.STARTUP_ERROR):
        assert not re.search(r"\b(LLM|[Mm]odels?|Qwen|bge|GGUF|llama)\b", text), text
    assert "Downloading Resources" in html
