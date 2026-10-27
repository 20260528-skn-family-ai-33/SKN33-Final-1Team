#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
bench_python="${BENCH_PYTHON:-$PWD/.venv/bin/python}"
if [[ "$bench_python" != */* ]]; then bench_python="$(command -v "$bench_python" || true)"; fi
if [[ -z "$bench_python" || ! -x "$bench_python" ]]; then
  echo '기존 벤치마크 Python이 없습니다. 패키지 설치는 수행하지 않았습니다.' >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec "$bench_python" -B compare_poster_resolution.py "$@"
