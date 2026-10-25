#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
visual_venv="${VISUAL_CANDIDATE_VENV:-$PWD/.venv}"
if [[ ! -x "$visual_venv/bin/python" ]]; then
  echo "Existing benchmark Python missing: $visual_venv/bin/python. Set VISUAL_CANDIDATE_VENV to the existing environment; no packages are installed." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
if [[ "${1:-}" == "download" ]]; then
  export HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0
else
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
fi
exec "$visual_venv/bin/python" -B dinov2_compare.py "$@"
