"""Keep app and GPU worker installation profiles consistent and separate."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_common_export_matches_lock(tmp_path):
    if not shutil.which("uv"):
        pytest.skip("uv is required to check the lock export")
    env = {**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")}
    result = subprocess.run(
        [sys.executable, "scripts/export_requirements.py", "--check"],
        cwd=ROOT, env=env, text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    content = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pywin32==312 ; sys_platform == 'win32'" in content
    assert "imageio-ffmpeg==0.7.0" in content
    assert "paddlepaddle" not in content
    assert "torch==" not in content


def test_video_profiles_reuse_shared_ocr_without_mixing_paddle_runtimes():
    cpu = (ROOT / "requirements-paddle.txt").read_text(encoding="utf-8")
    gpu = (ROOT / "deploy/video-worker/requirements-paddle-gpu.txt").read_text(encoding="utf-8")
    gpu_ocr = (ROOT / "deploy/video-worker/requirements-paddle-gpu-ocr.txt").read_text(encoding="utf-8")
    shared = (ROOT / "requirements-paddle-common.txt").read_text(encoding="utf-8")
    worker = (ROOT / "deploy/video-worker/requirements-worker.txt").read_text(encoding="utf-8")
    assert "paddlepaddle==3.3.1" in cpu
    assert "paddlepaddle-gpu==3.3.1" in gpu
    assert "paddlepaddle-gpu" not in cpu
    assert "paddlepaddle==" not in gpu
    assert "requirements-paddle-common.txt" in cpu and "requirements-paddle-common.txt" in gpu_ocr
    assert "--index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/" in gpu
    assert "paddleocr==3.7.0" in shared and "paddlex==3.7.2" in shared
    assert "requirements-logo.txt" in worker and "requirements-paddle.txt" in worker
    assert "constraints-cu128.txt" in worker
