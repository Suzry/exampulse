#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

echo "Starting Exampulse status..."

PYTHON="./.venv/bin/python"

find_python() {
  local candidate
  for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 \
      && "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  return 1
}

if [ ! -x "$PYTHON" ]; then
  echo "Creating project virtual environment..."
  BASE_PYTHON="$(find_python)" || {
    echo "ERROR: Python 3.11 or newer is required."
    echo "Install it with: brew install python@3.11"
    exit 1
  }
  "$BASE_PYTHON" -m venv .venv
fi

if ! "$PYTHON" -c "import typer" >/dev/null 2>&1; then
  echo "Dependencies are missing. Installing Exampulse into .venv..."
  "$PYTHON" -m pip install -U pip
  "$PYTHON" -m pip install -e .
fi

if [ ! -f ".env" ]; then
  if [ -f ".env.example" ]; then
    echo "Creating .env from .env.example..."
    cp .env.example .env
  else
    echo "ERROR: .env was not found."
    exit 1
  fi
fi

run_exampulse() {
  "$PYTHON" -m app.cli.main "$@"
}

echo
echo "[1/4] Importing exams..."
if [ -f "exams.json" ]; then
  run_exampulse exams import exams.json
else
  echo "No exams.json found. Skipping exam import."
fi

"$PYTHON" - <<'PY'
from app.storage.db import get_session, init_db
from app.storage.repositories import has_demo_data, list_exams

init_db()
with get_session() as session:
    demo_exams = [
        exam for exam in list_exams(session)
        if "demo seeded" in (exam.notes or "")
    ]
    if has_demo_data(session) or demo_exams:
        print()
        print("WARNING: Demo data is still present in exampulse.db.")
        print("Reports may mix demo and real data until the local database is cleaned.")
PY

echo
echo "[2/4] Syncing WHOOP data..."
if ! run_exampulse sync --days 30; then
  echo
  echo "WHOOP sync failed. Continuing with local data."
  echo "If this is your first real sync, run: ./.venv/bin/python -m app.cli.main auth"
fi

echo
echo "[3/4] Today's status..."
run_exampulse today --compact

echo
echo "[4/4] Full report..."
run_exampulse report --compact

echo
echo "Done."
