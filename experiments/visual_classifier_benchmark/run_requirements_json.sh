#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
bench_python="${BENCH_PYTHON:-$PWD/.venv/bin/python}"
if [[ "$bench_python" != */* ]]; then bench_python="$(command -v "$bench_python" || true)"; fi
if [[ -z "$bench_python" || ! -x "$bench_python" ]]; then
  echo "Existing benchmark Python missing. Set BENCH_PYTHON; nothing is installed." >&2
  exit 1
fi
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
command_name="${1:-check-env}"
if [[ $# -gt 0 ]]; then shift; fi
case "$command_name" in
  download) unset HF_HUB_OFFLINE TRANSFORMERS_OFFLINE ;;
  check-env|run|show-results) export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 ;;
  *) echo "Usage: bash run_requirements_json.sh {check-env|download|run|show-results} [options]" >&2; exit 2 ;;
esac
exec "$bench_python" -B evaluate_requirements_json.py "$command_name" "$@"
