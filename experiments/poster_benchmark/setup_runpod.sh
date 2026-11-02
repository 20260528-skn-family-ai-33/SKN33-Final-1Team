#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
task_python="${POSTER_PYTHON:-python3}"
task_venv="${POSTER_VENV:-$PWD/.venv}"
"$task_python" -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required; set POSTER_PYTHON=python3.12 if available"'
"$task_python" assets.py verify
"$task_python" -m venv "$task_venv"
"$task_venv/bin/python" -m pip install --upgrade pip
"$task_venv/bin/python" -m pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
"$task_venv/bin/python" -m pip install -r requirements.txt
"$task_venv/bin/python" -m pip check
"$task_venv/bin/python" -c 'import torch; assert torch.cuda.is_available(), "CUDA GPU/driver unavailable"; print(torch.__version__,torch.cuda.get_device_name(0))'
"$task_venv/bin/python" -B run_benchmark.py validate
"$task_venv/bin/python" -B -m unittest discover -s tests -v
