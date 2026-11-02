#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
bench_python="${BENCH_PYTHON:-$PWD/.venv/bin/python}"
if [[ "$bench_python" != */* ]]; then bench_python="$(command -v "$bench_python" || true)"; fi
if [[ -z "$bench_python" || ! -x "$bench_python" ]]; then
  echo 'Existing benchmark Python missing. Nothing was installed.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$bench_python" -B prepare_preserved_poster_candidates.py "$@"
