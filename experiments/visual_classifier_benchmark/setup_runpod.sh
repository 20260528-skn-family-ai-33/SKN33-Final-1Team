#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
visual_python="${VISUAL_PYTHON:-python3}"
visual_venv="${VISUAL_CANDIDATE_VENV:-$PWD/.venv}"
"$visual_python" -c 'import sys; assert sys.version_info[:2] == (3,12), "Python 3.12 required"'
"$visual_python" -m venv "$visual_venv"
"$visual_venv/bin/python" -m pip install --upgrade pip
"$visual_venv/bin/python" -m pip install torch==2.10.0 torchvision==0.25.0 --index-url https://download.pytorch.org/whl/cu128
"$visual_venv/bin/python" -m pip install -r requirements.txt
"$visual_venv/bin/python" -m pip check
"$visual_venv/bin/python" -c 'import torch; assert torch.cuda.is_available(), "CUDA required"; print(torch.cuda.get_device_name(0))'
"$visual_python" ../poster_benchmark/assets.py fetch
echo 'visual_candidate_runtime_ready; models are read from /workspace/models'
