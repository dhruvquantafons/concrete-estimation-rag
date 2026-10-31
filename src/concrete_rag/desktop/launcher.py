"""Desktop entry point (BuildMoreAI.exe).

1. Point every data path at a per-user folder (CRAG_HOME) and copy the shipped data there on first run.
2. Open the app window on the "Downloading Resources" screen and fetch what is missing (resources.py).
3. Start the answer server (llama.cpp llama-server, hidden) and the API (uvicorn, in-process) on free local ports.
4. Switch the window to the normal UI, signed in as the local user (CRAG_DESKTOP_MODE).
Closing the window stops both servers.

Nothing from concrete_rag.config may be imported before _configure_env(): config.ROOT reads CRAG_HOME at import.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx

from concrete_rag.desktop.resources import Downloader, progress_text

APP_NAME = "BuildMoreAI"
TITLE = "BuildMore AI"
SEED_VERSION = "1"          # bump when the shipped data changes; new files are added, user edits are kept
LLAMA_CTX = 12288           # retrieved context (top_k chunks) + up to llm_max_tokens of answer
STARTUP_ERROR = "BuildMore AI couldn't start. Please close it and open it again."

# Installed layout (packaging/windows/build_on_mac.sh): <install>/app/concrete_rag/desktop/launcher.py next to
# <install>/python, engine, config, storage, data. From source, the same parents[3] is the repo root.
BUNDLE = Path(__file__).resolve().parents[3]
log = logging.getLogger("buildmore")


# ------------------------------------------------------------------ folders
def home_dir() -> Path:
    if os.environ.get("CRAG_HOME"):
        return Path(os.environ["CRAG_HOME"])
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def seed_dir() -> Path:
    return BUNDLE


def llama_server() -> Path | None:
    exe = "llama-server.exe" if os.name == "nt" else "llama-server"
    if (BUNDLE / "engine" / exe).is_file():
        return BUNDLE / "engine" / exe
    found = shutil.which("llama-server")
    return Path(found) if found else None


def _skip(rel: Path) -> bool:
    parts = rel.parts
    return (parts[:2] in {("storage", "models"), ("data", "interim")} or rel.name.startswith("docstore.backup-")
            or rel.name == ".DS_Store" or "__pycache__" in parts)


def seed(home: Path) -> None:
    """Copy config/, storage/ and data/ from the bundle. Existing files are never overwritten (user edits win)."""
    marker = home / ".seed_version"
    if marker.is_file() and marker.read_text().strip() == SEED_VERSION:
        return
    src = seed_dir()
    if src.resolve() == home.resolve():
        return
    log.info("seeding %s from %s", home, src)
    for top in ("config", "storage", "data"):
        for f in (src / top).rglob("*"):
            rel = f.relative_to(src)
            dst = home / rel
            if f.is_file() and not _skip(rel) and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dst)
    marker.write_text(SEED_VERSION)


# ------------------------------------------------------------------ environment
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _configure_env(home: Path, res: Path, llm_port: int) -> None:
    os.environ.update({
        "CRAG_HOME": str(home),
        "CRAG_DESKTOP_MODE": "1",
        "CRAG_DEVICE": os.environ.get("CRAG_DEVICE", "cpu"),
        "CRAG_EMBED_MODEL": str(res / "search"),
        "CRAG_RERANK_MODEL": str(res / "rank"),
        "CRAG_LLM_PROVIDER": "openai_compat",
        "CRAG_LLM_BASE_URL": f"http://127.0.0.1:{llm_port}/v1",
        "CRAG_LLM_MODEL": "engine",
        "CRAG_LLM_API_KEY": "",
        "CRAG_API_BOOTSTRAP_ADMIN_KEY": "",
        "CRAG_API_RATE_LIMIT": "600/minute",
        "HF_HUB_OFFLINE": "1",             # everything is loaded from resources/, never from the Hub
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "TOKENIZERS_PARALLELISM": "false",
    })


def _setup_logging(home: Path) -> None:
    logs = home / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(logs / "launcher.log", encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if sys.stdout is None:                 # pythonw has no console; libraries still print
        sys.stdout = sys.stderr = open(logs / "console.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115


def _single_instance(home: Path):
    """Hold an exclusive lock for the app's lifetime (embedded Qdrant allows one process). None if taken."""
    f = open(home / ".lock", "a+")  # noqa: SIM115 - must stay open
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


# ------------------------------------------------------------------ servers
class Servers:
    def __init__(self, res: Path, llm_port: int, api_port: int):
        self.res, self.llm_port, self.api_port = res, llm_port, api_port
        self.llama: subprocess.Popen | None = None
        self.api = None

    def start(self) -> None:
        exe = llama_server()
        if not exe:
            raise RuntimeError("llama-server executable not found")
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        out = open(home_dir() / "logs" / "engine.log", "ab")  # noqa: SIM115 - owned by the child process
        self.llama = subprocess.Popen(
            [str(exe), "-m", str(self.res / "engine.gguf"), "--host", "127.0.0.1", "--port", str(self.llm_port),
             "-c", str(LLAMA_CTX), "--alias", "engine"],
            stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags, cwd=exe.parent)
        _kill_with_parent(self.llama)

        import uvicorn

        from concrete_rag.api.main import app
        self.api = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.api_port, log_config=None,
                                                 access_log=False))
        threading.Thread(target=self.api.run, name="api", daemon=True).start()

        self._wait(f"http://127.0.0.1:{self.api_port}/ui/", "api", timeout=180)
        self._wait(f"http://127.0.0.1:{self.llm_port}/health", "engine", timeout=300)

    def _wait(self, url: str, what: str, timeout: float) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if what == "engine" and self.llama and self.llama.poll() is not None:
                raise RuntimeError(f"engine exited with code {self.llama.returncode}")
            try:
                if httpx.get(url, timeout=2).status_code == 200:
                    log.info("%s is up", what)
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"{what} did not start within {timeout:.0f}s")

    def stop(self) -> None:
        if self.api:
            self.api.should_exit = True
        if self.llama and self.llama.poll() is None:
            self.llama.terminate()
            try:
                self.llama.wait(10)
            except subprocess.TimeoutExpired:
                self.llama.kill()


_JOB = None


def _kill_with_parent(proc: subprocess.Popen) -> None:
    """Windows: put the engine in a Job Object that kills it when this process dies for any reason
    (crash, Task Manager), so it never lingers holding ~2 GB of RAM. Elsewhere the window-close path suffices."""
    global _JOB
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes

    class LIMITS(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("Basic", LIMITS), ("IoInfo", ctypes.c_uint64 * 6), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.OpenProcess.restype = wintypes.HANDLE
    try:
        job = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        info.Basic.LimitFlags = 0x2000                     # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info))
        handle = k32.OpenProcess(0x0101, False, proc.pid)  # PROCESS_SET_QUOTA | PROCESS_TERMINATE
        if not k32.AssignProcessToJobObject(wintypes.HANDLE(job), wintypes.HANDLE(handle)):
            log.warning("could not tie engine lifetime to the app (error %d)", ctypes.get_last_error())
        k32.CloseHandle(wintypes.HANDLE(handle))
        _JOB = job                                         # keep the handle open for our whole lifetime
    except Exception:  # noqa: BLE001 - best effort; normal shutdown still stops the engine
        log.exception("job object setup failed")


# ------------------------------------------------------------------ window
class Bridge:
    """Exposed to setup.html as window.pywebview.api (only public methods are exposed)."""

    def __init__(self, dl: Downloader):
        self._dl = dl

    def progress(self) -> dict:
        st = self._dl.status()
        return {**st, **progress_text(st), "retry": st.get("message") != STARTUP_ERROR}

    def retry(self) -> None:
        self._dl.retry()


def _run(window, dl: Downloader, servers: Servers) -> None:
    dl.run_until_ready()
    dl.set_phase("starting")
    try:
        servers.start()
    except Exception:  # noqa: BLE001
        log.exception("startup failed")
        dl.set_phase("error", STARTUP_ERROR)
        return
    window.load_url(f"http://127.0.0.1:{servers.api_port}/ui/")


def main() -> None:
    home = home_dir()
    home.mkdir(parents=True, exist_ok=True)
    _setup_logging(home)
    if os.name == "nt":   # own taskbar entry and icon instead of grouping under pythonw.exe
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("QuantaFONS.BuildMoreAI")
    import webview

    lock = _single_instance(home)
    if lock is None:
        webview.create_window(TITLE, html="<body style='font:15px Segoe UI,sans-serif;padding:24px;color:#15233d'>"
                                          "BuildMore AI is already open.</body>", width=380, height=160)
        webview.start()
        return

    seed(home)
    res = home / "resources"
    llm_port = free_port()
    while (api_port := free_port()) == llm_port:
        pass
    _configure_env(home, res, llm_port)
    log.info("home=%s llm_port=%d api_port=%d", home, llm_port, api_port)

    dl = Downloader(res)
    servers = Servers(res, llm_port, api_port)
    window = webview.create_window(TITLE, url=str(Path(__file__).with_name("setup.html")), js_api=Bridge(dl),
                                   width=1360, height=880, min_size=(980, 640))
    try:
        icon = BUNDLE / "buildmore.ico"
        webview.start(_run, (window, dl, servers), private_mode=False, storage_path=str(home / "webview"),
                      gui="edgechromium" if os.name == "nt" else None,   # never fall back to the IE engine
                      icon=str(icon) if icon.is_file() else None)
    finally:
        servers.stop()
        lock.close()


if __name__ == "__main__":
    main()
