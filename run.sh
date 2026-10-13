#!/usr/bin/env bash
# One-command launcher for macOS/Linux: creates a virtual environment, installs deps, starts the app.
set -e
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 was not found. Install it from https://www.python.org/downloads/macos/ and run this again."
  exit 1
fi

if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  echo "Python 3.9 or newer is required. You have: $(python3 --version)"
  echo "Install a newer one from https://www.python.org/downloads/macos/ and run this again."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (.venv)..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --quiet --upgrade pip || true
python -m pip install --quiet -r requirements.txt

python server.py
