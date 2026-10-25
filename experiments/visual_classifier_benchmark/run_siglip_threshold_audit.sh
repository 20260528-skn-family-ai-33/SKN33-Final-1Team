#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
visual_venv="${VISUAL_CANDIDATE_VENV:-$PWD/.venv}"
if [[ ! -x "$visual_venv/bin/python" ]]; then
  echo "Existing benchmark Python missing: $visual_venv/bin/python. Set VISUAL_CANDIDATE_VENV to the existing environment; no packages are installed." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$visual_venv/bin/python" -B audit_siglip_thresholds.py "$@"
