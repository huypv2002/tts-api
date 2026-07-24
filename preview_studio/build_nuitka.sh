#!/usr/bin/env bash
# Build Preview Studio with Nuitka (onefile). No PyInstaller.
# Usage (from repo root or this dir):
#   bash preview_studio/build_nuitka.sh
#   OUT_DIR="/path/to/zip/parent" bash preview_studio/build_nuitka.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PY="${PYTHON:-python3.11}"
JOBS="${JOBS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)}"
OUT_PARENT="${OUT_DIR:-$ROOT/dist-release}"
DIST_DIR="$SCRIPT_DIR/dist-nuitka"
RELEASE_NAME="TTS-Studio-Nuitka"
RELEASE_DIR="$DIST_DIR/$RELEASE_NAME"

echo "=== Nuitka onefile build ==="
echo "Python: $PY"
echo "Root:   $ROOT"
echo "Jobs:   $JOBS"

cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

$PY -m pip install -U pip wheel setuptools >/dev/null
$PY -m pip install -U -r "$SCRIPT_DIR/requirements.txt" nuitka ordered-set zstandard PyJWT

# Verify imports
$PY - <<'PY'
import sys
mods = [
    "PySide6", "PySide6.QtWidgets", "PySide6.QtCore", "PySide6.QtGui",
    "httpx", "tls_client", "camoufox", "playwright", "jwt", "fast_tts",
]
err = []
for m in mods:
    try:
        __import__(m)
        print("  OK", m)
    except Exception as e:
        print("  FAIL", m, e)
        err.append(m)
if err:
    sys.exit(1)
print("imports ok")
PY

rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"

# Platform flags
EXTRA=()
case "$(uname -s)" in
  Darwin)
    # onefile binary (not .app bundle)
    BIN_NAME="TTS Studio"
    ;;
  MINGW*|MSYS*|CYGWIN*|Windows_NT)
    EXTRA+=(--windows-console-mode=disable)
    BIN_NAME="TTS Studio.exe"
    ;;
  *)
    BIN_NAME="TTS Studio"
    ;;
esac

echo "=== Compiling with Nuitka (--mode=app = onefile, except macOS .app) — may take 10–40 min ==="
$PY -m nuitka \
  --mode=app \
  --assume-yes-for-downloads \
  --enable-plugin=pyside6 \
  --output-dir="$DIST_DIR" \
  --output-filename="$BIN_NAME" \
  --include-module=fast_tts \
  --include-module=app_paths \
  --include-module=accounts_store \
  --include-module=local_tts \
  --include-module=gen_pipeline \
  --include-module=output_layout \
  --include-module=ffmpeg_tools \
  --include-module=multivoice \
  --include-module=user_safe \
  --include-module=version \
  --include-module=auto_updater \
  --include-package=ui \
  --include-package=camoufox \
  --include-package=playwright \
  --include-package=browserforge \
  --include-package=apify_fingerprint_datapoints \
  --include-package=language_tags \
  --include-package=ua_parser \
  --include-package=screeninfo \
  --include-package=platformdirs \
  --include-package=tls_client \
  --include-package-data=certifi \
  --include-package-data=camoufox \
  --include-package-data=playwright \
  --include-package-data=browserforge \
  --include-package-data=apify_fingerprint_datapoints \
  --include-package-data=language_tags \
  --include-package-data=tls_client \
  --include-data-files="$SCRIPT_DIR/silent_1s.mp3=silent_1s.mp3" \
  --include-data-files="$SCRIPT_DIR/silent_1_5s.mp3=silent_1_5s.mp3" \
  --include-data-files="$SCRIPT_DIR/silent_05s.mp3=silent_05s.mp3" \
  --nofollow-import-to=tkinter \
  --nofollow-import-to=_tkinter \
  --nofollow-import-to=matplotlib \
  --nofollow-import-to=pandas \
  --nofollow-import-to=scipy \
  --nofollow-import-to=IPython \
  --nofollow-import-to=jupyter \
  --nofollow-import-to=PyQt5 \
  --nofollow-import-to=PyQt6 \
  --nofollow-import-to=PySide2 \
  --jobs="$JOBS" \
  --lto=no \
  ${EXTRA[@]+"${EXTRA[@]}"} \
  "$SCRIPT_DIR/PreviewStudio.py"

# Locate binary or .app
BIN_PATH=""
APP_BUNDLE=""
if [[ -d "$DIST_DIR/${BIN_NAME}.app" ]]; then
  APP_BUNDLE="$DIST_DIR/${BIN_NAME}.app"
elif [[ -d "$DIST_DIR/PreviewStudio.app" ]]; then
  APP_BUNDLE="$DIST_DIR/PreviewStudio.app"
else
  APP_BUNDLE="$(find "$DIST_DIR" -maxdepth 2 -type d -name '*.app' | head -1 || true)"
fi
if [[ -f "$DIST_DIR/$BIN_NAME" ]]; then
  BIN_PATH="$DIST_DIR/$BIN_NAME"
elif [[ -f "$DIST_DIR/${BIN_NAME}.bin" ]]; then
  BIN_PATH="$DIST_DIR/${BIN_NAME}.bin"
else
  BIN_PATH="$(find "$DIST_DIR" -maxdepth 3 -type f \( -name "$BIN_NAME" -o -name 'PreviewStudio' -o -name 'TTS Studio' -o -name 'TTS Preview Studio' \) ! -path '*.build/*' 2>/dev/null | head -1 || true)"
fi
if [[ -z "${APP_BUNDLE:-}" && ( -z "${BIN_PATH:-}" || ! -e "$BIN_PATH" ) ]]; then
  echo "ERROR: Nuitka output not found under $DIST_DIR"
  ls -laR "$DIST_DIR" | head -100
  exit 1
fi

echo "App:    ${APP_BUNDLE:-n/a}"
echo "Binary: ${BIN_PATH:-n/a}"

# Assemble release folder (app/onefile + companion files)
rm -rf "$RELEASE_DIR"
mkdir -p "$RELEASE_DIR"
if [[ -n "${APP_BUNDLE:-}" && -d "$APP_BUNDLE" ]]; then
  cp -R "$APP_BUNDLE" "$RELEASE_DIR/"
fi
if [[ -n "${BIN_PATH:-}" && -f "$BIN_PATH" ]]; then
  cp -f "$BIN_PATH" "$RELEASE_DIR/"
fi
cp -f "$SCRIPT_DIR/silent_1s.mp3" "$SCRIPT_DIR/silent_1_5s.mp3" "$SCRIPT_DIR/silent_05s.mp3" "$RELEASE_DIR/" 2>/dev/null || true
# Do NOT ship requirements / BUILD docs / proxy examples (tech leak)

# Portable runtime (best-effort local build)
mkdir -p "$RELEASE_DIR/bin" "$RELEASE_DIR/runtime"
if command -v ffmpeg >/dev/null 2>&1; then
  cp -f "$(command -v ffmpeg)" "$RELEASE_DIR/bin/" 2>/dev/null || true
  command -v ffprobe >/dev/null 2>&1 && cp -f "$(command -v ffprobe)" "$RELEASE_DIR/bin/" 2>/dev/null || true
fi
# Copy browser runtime (neutral folder name)
RUNTIME_DST="$RELEASE_DIR/runtime" $PY - <<'PY' || true
import os, shutil
from pathlib import Path
try:
    from camoufox.pkgman import INSTALL_DIR
    src = Path(INSTALL_DIR)
    dst = Path(os.environ.get("RUNTIME_DST") or "runtime")
    if src.is_dir() and any(src.iterdir()):
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print("copied runtime", src, "->", dst)
    else:
        print("no local runtime cache")
except Exception as e:
    print("runtime copy skip:", e)
PY

cat > "$RELEASE_DIR/HUONG_DAN.txt" <<'EOF'
TTS Studio — Portable
=====================
1) Giai nen CA THU MUC
2) Chay "TTS Studio" / .exe
3) Dang nhap bang tai khoan do quan tri vien cap

Neu khong mo duoc: cai lai ban portable day du hoac lien he quan tri vien.
EOF

# Launcher bat (Windows users unzip on Windows)
cat > "$RELEASE_DIR/run-tts-studio.bat" <<'EOF'
@echo off
cd /d "%~dp0"
if exist "TTS Studio.exe" (
  start "" "TTS Studio.exe"
) else if exist "TTS Studio" (
  "TTS Studio"
) else if exist "TTS Preview Studio.exe" (
  start "" "TTS Preview Studio.exe"
) else (
  echo Khong tim thay ung dung. Lien he quan tri vien.
  pause
)
EOF

mkdir -p "$OUT_PARENT"
ZIP_PATH="$OUT_PARENT/${RELEASE_NAME}-$(uname -s | tr '[:upper:]' '[:lower:]')-$(uname -m).zip"
rm -f "$ZIP_PATH"
(
  cd "$DIST_DIR"
  if command -v zip >/dev/null 2>&1; then
    zip -r -y "$ZIP_PATH" "$RELEASE_NAME"
  else
    python3 - <<PY
import shutil
shutil.make_archive("${ZIP_PATH%.zip}", "zip", "$DIST_DIR", "$RELEASE_NAME")
print("zip ok")
PY
  fi
)

echo "=== DONE ==="
echo "Release: $RELEASE_DIR"
echo "ZIP:     $ZIP_PATH"
ls -lh "$ZIP_PATH"
