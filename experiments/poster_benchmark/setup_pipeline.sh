#!/usr/bin/env bash
# Add OCR without installing anything into the completed training runtime.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
poster_visual_venv="${POSTER_VENV:-$PWD/.venv}"
poster_ocr_venv="${POSTER_OCR_VENV:-$PWD/.ocr-venv}"
poster_python="${POSTER_PYTHON:-python3}"
if [[ ! -x "$poster_visual_venv/bin/python" ]]; then
  echo 'Existing visual venv missing. Keep the environment used by the completed training run.' >&2
  exit 1
fi
"$poster_visual_venv/bin/python" -B pipeline_eval.py --check-source "$@"
"$poster_python" -c 'import sys; assert sys.version_info[:2] == (3,12), "Python3.12 required"'
if [[ "$(realpath -m "$poster_visual_venv")" == "$(realpath -m "$poster_ocr_venv")" ]]; then
  echo 'POSTER_OCR_VENV must differ from POSTER_VENV' >&2
  exit 1
fi
"$poster_python" -m venv "$poster_ocr_venv"
"$poster_ocr_venv/bin/python" -m pip install --upgrade pip
"$poster_ocr_venv/bin/python" -m pip install paddlepaddle-gpu==3.3.1 --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/
"$poster_ocr_venv/bin/python" -m pip install -r requirements-ocr.txt
"$poster_ocr_venv/bin/python" -m pip check
"$poster_ocr_venv/bin/python" -c 'import paddle; assert paddle.is_compiled_with_cuda(), "GPU Paddle required"; paddle.set_device("gpu:0"); print(paddle.__version__, paddle.get_device())'
"$poster_python" ../../scripts/prepare_paddle.py --include-detection
echo 'pipeline_runtime_ready (existing visual venv/checkpoints preserved; no training)'
