#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
task_venv="${POSTER_VENV:-$PWD/.venv}"
if [[ ! -x "$task_venv/bin/python" ]]; then
  echo 'Run bash setup_runpod.sh first, or set POSTER_VENV to your installed venv.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
exec "$task_venv/bin/python" -B run_benchmark.py "$@"
