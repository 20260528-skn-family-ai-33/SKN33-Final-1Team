#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
bench_python="${BENCH_PYTHON:-$PWD/.venv/bin/python}"
if [[ "$bench_python" != */* ]]; then
  bench_python="$(command -v "$bench_python" || true)"
fi
if [[ -z "$bench_python" || ! -x "$bench_python" ]]; then
  echo "Existing benchmark Python missing. Set BENCH_PYTHON; nothing is installed." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
command_name="${1:-prepare}"
if [[ $# -gt 0 ]]; then shift; fi
case "$command_name" in
  prepare) exec "$bench_python" -B prepare_poster_augment.py "$@" ;;
  train|show-results) exec "$bench_python" -B finetune_siglip_poster_mix.py "$command_name" "$@" ;;
  *) echo "Usage: bash run_poster_augment.sh {prepare|train|show-results} [options]" >&2; exit 2 ;;
esac
