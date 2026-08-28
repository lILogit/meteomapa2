#!/usr/bin/env bash
# Launch Meteomapa (dev/manual mode). Creates a venv and installs deps on first run.
set -euo pipefail
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found"; exit 1
fi

if [ ! -d ".venv" ]; then
  echo ">> creating virtualenv (.venv)"
  "$PY" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if ! .venv/bin/python -c "import fastapi, uvicorn, PIL, numpy, requests, pydantic_settings" 2>/dev/null; then
  echo ">> installing dependencies"
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f static/assets/blank.png ]; then
  echo ">> vending static assets (legend/borders)"
  .venv/bin/python scripts/fetch_assets.py
fi

PORT=${PORT:-8000}
echo ">> starting Meteomapa on http://localhost:${PORT}"
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
