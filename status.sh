#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

echo "Starting Exampulse status..."

PYTHON="./.venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "ERROR: Project virtual environment was not found."
  echo
  echo "Run these setup commands from the project root:"
  echo "  python3 -m venv .venv"
  echo "  ./.venv/bin/python -m pip install -U pip"
  echo "  ./.venv/bin/python -m pip install -e ."
  exit 1
fi

if ! "$PYTHON" -c "import typer" >/dev/null 2>&1; then
  echo "Dependencies are missing. Installing Exampulse into .venv..."
  "$PYTHON" -m pip install -U pip
  "$PYTHON" -m pip install -e .
fi

run_exampulse() {
  "$PYTHON" -m app.cli.main "$@"
}

echo
echo "[1/3] Syncing WHOOP data..."
run_exampulse sync --days 30

echo
echo "[2/3] Today's status..."
run_exampulse today --compact

echo
echo "[3/3] Full report..."
run_exampulse report --compact

echo
echo "Done."
