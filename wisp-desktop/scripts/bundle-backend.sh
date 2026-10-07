#!/usr/bin/env bash
# Build wisp-desktop/build/backend: a RELOCATABLE Python with wisp and its dependencies installed, so the packaged
# Wisp.app needs nothing from the machine it runs on (no system Python, no virtualenv, no repository checkout).
#
#   build/backend/python/bin/python3   a python-build-standalone CPython (relocatable: finds its stdlib relative to itself)
#   build/backend/BUNDLE.txt           what went in: interpreter, wisp commit, pinned dependency list
#
# electron-builder copies build/backend to Wisp.app/Contents/Resources/backend (see electron-builder.yml). The app then
# starts `Resources/backend/python/bin/python3 -c "from wisp.server import main ..."` (src/main/backend.ts).
#
# Needs: uv (https://docs.astral.sh/uv/) and network for the interpreter (~40 MB) and the wheels. macOS only.
# Usage: scripts/bundle-backend.sh            # Python 3.12, the version wisp is tested on
#        WISP_BUNDLE_PYTHON=3.13 scripts/bundle-backend.sh
set -euo pipefail

DESKTOP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO="$(cd "$DESKTOP/.." && pwd)"
OUT="$DESKTOP/build/backend"
PYVER="${WISP_BUNDLE_PYTHON:-3.12}"

[[ "$(uname -s)" == "Darwin" ]] || { echo "bundle-backend: macOS only" >&2; exit 1; }
command -v uv >/dev/null || { echo "bundle-backend: uv is required (brew install uv)" >&2; exit 1; }
[[ -f "$REPO/pyproject.toml" && -d "$REPO/wisp" && -d "$REPO/agent" ]] || { echo "bundle-backend: $REPO is not the wisp repository" >&2; exit 1; }

echo "==> interpreter: python-build-standalone $PYVER (uv-managed)"
uv python install --managed-python "$PYVER"
SRC_PY="$(uv python find --managed-python "$PYVER")"
# the interpreter's home: .../cpython-3.12.x-macos-aarch64-none (parent of bin/)
SRC_HOME="$(cd "$(dirname "$SRC_PY")/.." && pwd -P)"
[[ -x "$SRC_HOME/bin/python3" ]] || { echo "bundle-backend: no bin/python3 under $SRC_HOME" >&2; exit 1; }

echo "==> fresh copy into $OUT/python"
rm -rf "$OUT"
mkdir -p "$OUT"
cp -a "$SRC_HOME" "$OUT/python"
PY="$OUT/python/bin/python3"
# The copy is ours to modify: a managed interpreter ships an EXTERNALLY-MANAGED marker that stops pip, which is right for the user's
# copy and wrong for this private one.
find "$OUT/python" -name EXTERNALLY-MANAGED -delete

echo "==> installing wisp and its dependencies into the bundled interpreter"
# Non-editable, from the repository: pyproject's package list ships both `wisp` and the top-level `agent` package wisp imports.
uv pip install --python "$PY" --no-cache --compile-bytecode "$REPO"

echo "==> pruning what a running app never uses"
SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
LIB="$(dirname "$SITE")"
rm -rf "$LIB/test" "$LIB/idlelib" "$LIB/tkinter" "$LIB/turtledemo" "$LIB/lib2to3" "$LIB/ensurepip" "$LIB/turtle.py"
find "$SITE" -type d \( -name tests -o -name test -o -name testing \) -prune -exec rm -rf {} + 2>/dev/null || true
# pip and setuptools are only needed to build; the app never installs anything.
rm -rf "$SITE"/pip "$SITE"/pip-*.dist-info "$SITE"/setuptools "$SITE"/setuptools-*.dist-info "$SITE"/_distutils_hack "$SITE"/pkg_resources 2>/dev/null || true
rm -rf "$OUT/python/share" "$OUT/python/include" 2>/dev/null || true

echo "==> smoke: import from the bundle, isolated, from a directory that is not the repository"
(
  cd /
  # The app starts the server with WISP_WORKSPACE set; importing wisp.server creates that directory at import time (and defaults to
  # /workspace, which a Mac cannot create), so the smoke check supplies a scratch one exactly as the app does.
  SCRATCH="$(mktemp -d)"
  trap 'rm -rf "$SCRATCH"' EXIT
  WISP_WORKSPACE="$SCRATCH" BUNDLE_ROOT="$OUT" "$PY" -I -c '
import sys, os
import wisp, agent, fastapi, uvicorn, pydantic, numpy, tiktoken, cryptography, yaml, requests
from wisp.server import main
root = os.path.realpath(os.environ["BUNDLE_ROOT"])
for mod in (wisp, agent, fastapi, numpy):
    where = os.path.realpath(mod.__file__)
    assert where.startswith(root), f"{mod.__name__} resolved outside the bundle: {where}"
print("ok:", sys.version.split()[0], "wisp from", os.path.dirname(os.path.realpath(wisp.__file__)))
'
) || { echo "bundle-backend: the bundled interpreter cannot import wisp" >&2; exit 1; }

{
  echo "python:  $("$PY" -c 'import sys; print(sys.version.split()[0])') (python-build-standalone via uv)"
  echo "wisp:    $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo unknown)"
  echo "built:   $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "--- packages ---"
  uv pip freeze --python "$PY"
} > "$OUT/BUNDLE.txt"

echo "==> done: $(du -sh "$OUT" | cut -f1) in $OUT"
