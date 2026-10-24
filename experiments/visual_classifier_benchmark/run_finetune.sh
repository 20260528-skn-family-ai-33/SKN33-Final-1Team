#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
visual_venv="${VISUAL_CANDIDATE_VENV:-$PWD/.venv}"
export PYTHONDONTWRITEBYTECODE=1 TOKENIZERS_PARALLELISM=false PYTHONUNBUFFERED=1
exec "$visual_venv/bin/python" -B finetune_siglip.py "$@"
