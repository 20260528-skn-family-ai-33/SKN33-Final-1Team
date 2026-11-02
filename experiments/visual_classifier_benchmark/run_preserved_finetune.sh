#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
bench_python="${BENCH_PYTHON:-$PWD/.venv/bin/python}"
if [[ "$bench_python" != */* ]]; then
  bench_python="$(command -v "$bench_python" || true)"
fi
if [[ -z "$bench_python" || ! -x "$bench_python" ]]; then
  echo 'Existing benchmark Python missing; no installation was performed.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
if [[ $# -eq 0 || "${1:-}" == -* ]]; then
  set -- train "$@"
fi
exec "$bench_python" -B finetune_siglip_preserved.py "$@"
