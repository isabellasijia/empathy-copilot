#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

if [[ ! -x .venv/bin/python ]]; then
  echo "Missing .venv. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

export PYTHONPATH="$PROJECT_DIR/backend"
UVICORN_ARGS=(app.main:app --host 127.0.0.1 --port "${PORT:-4173}")
if [[ "${DEV_RELOAD:-0}" == "1" ]]; then
  UVICORN_ARGS+=(--reload)
fi
exec .venv/bin/python -m uvicorn "${UVICORN_ARGS[@]}"
