#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
visual_venv="${VISUAL_CANDIDATE_VENV:-$PWD/.venv}"
if [[ ! -x "$visual_venv/bin/python" ]]; then
  echo "Existing runtime missing: $visual_venv/bin/python. Set VISUAL_CANDIDATE_VENV to the existing benchmark venv; this script installs nothing." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
exec "$visual_venv/bin/python" -B finetune_siglip_focused.py "$@"
