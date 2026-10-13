"""프로젝트 전용 로컬 Ollama를 명시적으로 실행하거나 Qwen 모델을 준비한다."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    """공식 설치된 CLI 또는 프로젝트 CLI로 로컬 serve/pull만 실행한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "pull"))
    args = parser.parse_args()
    local = ROOT / ".runtime/ollama/bin" / ("ollama.exe" if sys.platform == "win32" else "ollama")
    binary = str(local) if local.is_file() else shutil.which("ollama")
    if not binary:
        parser.error("Ollama를 먼저 공식 https://ollama.com/download 에서 설치하세요.")
    env = {**os.environ, "OLLAMA_HOST": "127.0.0.1:11435", "OLLAMA_MODELS": str(ROOT / ".runtime/ollama/models"),
           "OLLAMA_NO_CLOUD": "1", "OLLAMA_NUM_PARALLEL": "1"}
    command = [binary, "serve"] if args.action == "serve" else [binary, "pull", "qwen3-vl:4b-instruct"]
    try:
        return subprocess.call(command, env=env, cwd=ROOT)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
