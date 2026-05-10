#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ubuntu/Dashboard"
APP_DIR="$REPO_DIR/Newtest/Breaker_Based"
PYTHON_BIN="/home/ubuntu/.venv/bin/python"
ENV_FILE="/home/ubuntu/.breaker_fvg_env"

cd "$REPO_DIR"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

git pull --rebase --autostash origin main

cd "$APP_DIR"
"$PYTHON_BIN" breaker_fvg_dashboard_export.py --period-days 30 --interval 1h
"$PYTHON_BIN" breaker_fvg_scan.py

cd "$REPO_DIR"
"$PYTHON_BIN" Newtest/Breaker_Based/stage_breaker_fvg_outputs.py

if git diff --cached --quiet; then
  echo "No generated output changes to commit."
  exit 0
fi

git commit -m "Update Breaker FVG dashboard data"
git push origin main
