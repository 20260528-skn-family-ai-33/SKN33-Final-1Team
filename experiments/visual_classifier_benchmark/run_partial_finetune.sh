#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
visual_venv="${VISUAL_CANDIDATE_VENV:-$PWD/.venv}"
if [[ ! -x "$visual_venv/bin/python" ]]; then
  echo "Existing benchmark Python missing: $visual_venv/bin/python. Set VISUAL_CANDIDATE_VENV to the existing environment; no packages are installed." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
command="${1:-}"
if [[ "$command" == "review" || "$command" == "build-data" ]]; then
  exec "$visual_venv/bin/python" -B prepare_partial_data.py "$@"
elif [[ "$command" == "train" ]]; then
  shift
  exec "$visual_venv/bin/python" -B finetune_siglip_partial.py "$@"
else
  echo "Usage: bash run_partial_finetune.sh {review|build-data|train} [options]" >&2
  exit 2
fi
