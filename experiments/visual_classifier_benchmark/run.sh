#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
visual_venv="${VISUAL_CANDIDATE_VENV:-$PWD/.venv}"
if [[ ! -x "$visual_venv/bin/python" ]]; then
  echo 'Run bash setup_runpod.sh first.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
exec "$visual_venv/bin/python" -B run_experiment.py "$@"
