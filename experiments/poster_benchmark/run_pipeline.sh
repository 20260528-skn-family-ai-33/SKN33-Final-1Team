#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
poster_visual_venv="${POSTER_VENV:-$PWD/.venv}"
if [[ ! -x "$poster_visual_venv/bin/python" ]]; then
  echo 'Existing visual venv missing; use the runtime of the completed train run.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
exec "$poster_visual_venv/bin/python" -B pipeline_eval.py "$@"
