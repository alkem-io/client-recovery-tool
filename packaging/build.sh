#!/usr/bin/env bash
# Build a standalone GUI app for the CURRENT OS (macOS -> .app, Linux -> binary).
# PyInstaller cannot cross-compile: run this ON each target OS (or use CI).
#
# The BUILD machine's Python must have Tkinter 8.6+ (the END USER needs nothing
# -- Tk is bundled into the app). On macOS do NOT use Apple's /usr/bin/python3:
# it ships Tk 8.5, which ABORTS at launch on macOS 26+. Use a Tk 8.6/9 Python:
#   brew install python-tk@3.13 && PYTHON=/opt/homebrew/bin/python3.13 ./packaging/build.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f target_hashes.txt ] || { echo "ERROR: target_hashes.txt missing. Run: python3 gen_hashes.py <csv>"; exit 1; }

PY="${PYTHON:-python3}"
TKV="$("$PY" -c "import tkinter; print(tkinter.TkVersion)" 2>/dev/null || true)"
if [ -z "$TKV" ]; then
  echo "ERROR: '$PY' has no Tkinter, which is needed to build the GUI."
  case "$(uname)" in
    Darwin) echo "  Fix: brew install python-tk@3.13 && PYTHON=/opt/homebrew/bin/python3.13 ./packaging/build.sh  (or python.org Python)";;
    Linux)  echo "  Fix: sudo apt-get install -y python3-tk, then retry";;
  esac
  exit 1
fi
if [ "$TKV" = "8.5" ]; then
  echo "ERROR: '$PY' uses Tk 8.5, which ABORTS at launch on recent macOS (macOS 26+)."
  echo "  Apple's /usr/bin/python3 ships the broken Tk 8.5 -- do NOT build with it."
  echo "  Fix: brew install python-tk@3.13 && PYTHON=/opt/homebrew/bin/python3.13 ./packaging/build.sh"
  exit 1
fi
echo "Build Python Tk version: $TKV"

"$PY" -m venv .buildenv
# shellcheck disable=SC1091
. .buildenv/bin/activate
pip install -q --upgrade pip pyinstaller

# --windowed -> no terminal window. --add-data sep is ':' on macOS/Linux.
pyinstaller --onefile --windowed --noconfirm --clean \
  --name alkemio-recover \
  --add-data "target_hashes.txt:." \
  alkemio_cache_recover.py

echo
echo "Built in dist/:"
ls -1 dist/
echo "macOS: double-click dist/alkemio-recover.app  |  Linux: run dist/alkemio-recover"
echo "Self-check: dist/alkemio-recover.app/Contents/MacOS/alkemio-recover --selftest  (macOS)"
