#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
openimages_python="${BENCH_PYTHON:-$PWD/.venv/bin/python}"
if [[ "$openimages_python" != */* ]]; then
  openimages_python="$(command -v "$openimages_python" || true)"
fi
if [[ -z "$openimages_python" || ! -x "$openimages_python" ]]; then
  echo "Existing Python missing. Set BENCH_PYTHON to an existing Python 3.10+ executable; no packages are installed." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
if [[ $# -eq 0 || "${1:-}" == -* ]]; then
  set -- download "$@"
fi
exec "$openimages_python" -B download_openimages_instruments.py "$@"
