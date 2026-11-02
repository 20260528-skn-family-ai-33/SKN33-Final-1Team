#!/usr/bin/env bash
# Fresh clone -> SSH assets -> isolated CUDA runtime -> smoke -> chosen experiment.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
task_command="${1:-train}"
case "$task_command" in train|evaluate) ;; *) echo 'Usage: bash start.sh [train|evaluate]' >&2; exit 2;; esac
if [[ $# -gt 1 ]]; then echo 'Use run.sh for custom options or --resume; start.sh runs the standard protocol.' >&2; exit 2; fi
task_python="${POSTER_PYTHON:-python3}"
"$task_python" -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required; set POSTER_PYTHON=python3.12 if available"'
"$task_python" assets.py fetch
bash setup_runpod.sh
bash run.sh download
task_runs="${POSTER_RUN_DIR:-$PWD/runs}"
task_stamp="$(date -u +%Y%m%dT%H%M%SZ)-$$"
bash run.sh smoke --run-dir "$task_runs/smoke-$task_stamp"
bash run.sh "$task_command" --run-dir "$task_runs/$task_command-$task_stamp"
