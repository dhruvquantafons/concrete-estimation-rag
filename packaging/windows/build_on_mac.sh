#!/usr/bin/env bash
# Builds the Windows installer BuildMoreAI-Setup.exe on macOS - no Windows PC needed.
#
#   brew install uv makensis
#   packaging/windows/build_on_mac.sh            # -> build/win/BuildMoreAI-Setup.exe
#
# How: Python's official Windows "embeddable" package + Windows (win_amd64) wheels of every dependency, resolved
# by uv for the Windows platform and pinned to the versions in this repo's .venv (the tested set). Plus the
# llama.cpp Windows CPU build, the VC++ runtime DLLs, the app code and the data (config/, storage/, data/).
# NSIS (makensis) packs it into a per-user installer. Needs a finished `crag index` (storage/) and data/.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
PY_VER=3.12.10          # last 3.12 release with an embeddable zip
LLAMA_TAG=b11534        # pinned llama.cpp release
W=build/win
S=$W/stage/BuildMoreAI
SP=$S/python/Lib/site-packages
OUT=$W/BuildMoreAI-Setup.exe
step() { printf '\n==> %s\n' "$*"; }

for need in config storage/docstore.db storage/qdrant data .venv/bin/python; do
  [ -e "$need" ] || { echo "missing $need"; exit 1; }
done
command -v uv >/dev/null && command -v makensis >/dev/null || { echo "brew install uv makensis"; exit 1; }

rm -rf "$W/stage"; mkdir -p "$S" "$W/dl"

step "Windows Python $PY_VER (embeddable)"
[ -f "$W/dl/python-$PY_VER-embed.zip" ] || curl -fsSL -o "$W/dl/python-$PY_VER-embed.zip" \
  "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-amd64.zip"
unzip -q "$W/dl/python-$PY_VER-embed.zip" -d "$S/python"
# import paths: stdlib zip, site-packages, the app code; `import site` so .pth files (pywin32) work
printf 'python312.zip\r\n.\r\nLib\\site-packages\r\n..\\app\r\nimport site\r\n' > "$S/python/python312._pth"

step "Windows dependencies (pinned to .venv versions)"
.venv/bin/python - > "$W/requirements.in" <<'EOF'
import tomllib
for r in tomllib.load(open("pyproject.toml", "rb"))["project"]["dependencies"]:
    print(r)
print("pywebview>=5.3")
EOF
.venv/bin/python -m pip freeze --exclude-editable | grep "==" > "$W/constraints.txt"
uv pip compile -q "$W/requirements.in" -c "$W/constraints.txt" --python-platform x86_64-pc-windows-msvc \
  --python-version 3.12 -o "$W/requirements.txt"
# proxy-tools (pywebview helper) only ships as source; it is one pure-python file, copied below
grep -v '^proxy-tools' "$W/requirements.txt" > "$W/requirements-bin.txt"
uv pip install -q -r "$W/requirements-bin.txt" --target "$SP" --python-platform x86_64-pc-windows-msvc \
  --python-version 3.12 --only-binary :all: --no-deps
rm -rf "$W/dl/proxy"; .venv/bin/python -m pip download -q proxy-tools==0.1.0 --no-deps --no-binary :all: -d "$W/dl/proxy"
mkdir -p "$W/dl/proxy/x"; tar xzf "$W/dl/proxy"/proxy*.tar.gz -C "$W/dl/proxy/x"
cp -R "$W/dl/proxy/x"/proxy_tools-*/proxy_tools "$SP/"
if find "$SP" \( -name '*.so' -o -name '*.dylib' \) | grep -q .; then echo "macOS binaries leaked in"; exit 1; fi

step "VC++ runtime DLLs (torch, llama-server)"
rm -rf "$W/dl/msvc"; .venv/bin/python -m pip download -q msvc-runtime --platform win_amd64 --python-version 3.12 \
  --only-binary :all: --no-deps -d "$W/dl/msvc"
unzip -q "$W/dl/msvc"/*.whl -d "$W/dl/msvc/x"
cp "$W/dl/msvc/x"/msvc_runtime-*.data/data/Scripts/*.dll "$S/python/"

step "Answer engine: llama.cpp $LLAMA_TAG (Windows CPU x64)"
ZIP="$W/dl/llama-$LLAMA_TAG-win-cpu-x64.zip"
[ -f "$ZIP" ] || curl -fsSL -o "$ZIP" \
  "https://github.com/ggml-org/llama.cpp/releases/download/$LLAMA_TAG/llama-$LLAMA_TAG-bin-win-cpu-x64.zip"
rm -rf "$W/dl/llama"; unzip -q "$ZIP" -d "$W/dl/llama"
SRV="$(find "$W/dl/llama" -name llama-server.exe | head -1)"
[ -n "$SRV" ] || { echo "llama-server.exe not in release zip"; exit 1; }
mkdir -p "$S/engine"
cp "$SRV" "$(dirname "$SRV")"/*.dll "$S/engine/"
cp "$W/dl/msvc/x"/msvc_runtime-*.data/data/Scripts/*.dll "$S/engine/"
find "$(dirname "$SRV")" -maxdepth 1 -iname 'LICENSE*' -exec cp {} "$S/engine/" \;

step "App code and data"
rsync -a --exclude __pycache__ --exclude '*.pyc' src/concrete_rag "$S/app/"
rsync -a --exclude .DS_Store config "$S/"
rsync -a --exclude .DS_Store --exclude models --exclude 'docstore.backup-*' storage "$S/"
rsync -a --exclude .DS_Store --exclude interim data "$S/"
cp packaging/windows/buildmore.ico "$S/"

step "WebView2 bootstrapper"
[ -f "$W/dl/MicrosoftEdgeWebview2Setup.exe" ] || curl -fsSL -o "$W/dl/MicrosoftEdgeWebview2Setup.exe" \
  "https://go.microsoft.com/fwlink/p/?LinkId=2124703"

step "Installer (NSIS) - compressing, takes a while"
du -sh "$S"
makensis -V2 -DSTAGE="$REPO/$S" -DOUTFILE="$REPO/$OUT" -DWV2SETUP="$REPO/$W/dl/MicrosoftEdgeWebview2Setup.exe" \
  packaging/windows/installer.nsi
ls -lh "$OUT"
shasum -a 256 "$OUT"
