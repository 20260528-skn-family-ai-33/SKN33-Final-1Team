"""Create a dedicated local review environment, install dependencies, build React."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download-models", action="store_true", help="명시적으로 EasyOCR 모델을 다운로드합니다.")
    args = parser.parse_args()
    if sys.version_info[:2] != (3, 12):
        raise SystemExit("이 프로젝트는 Python 3.12로 검증했습니다. python3.12 setup_local.py를 사용하세요.")
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("Node.js 22.12 이상과 npm을 먼저 설치하세요.")
    environment = ROOT / ".venv-review"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        venv.EnvBuilder(with_pip=True).create(environment)
    commands = [
        [str(python), "-m", "pip", "install", "-r", "requirements-dev.txt"],
        [npm, "--prefix", "frontend", "ci"],
        [npm, "--prefix", "frontend", "run", "build"],
    ]
    if args.download_models:
        commands.append([str(python), "-m", "backend.app.ocr_runtime", "--download-models"])
    for command in commands:
        subprocess.run(command, cwd=ROOT, check=True)
    print("설치 완료. macOS/Linux에서는 ./run.sh로 실행하세요.")
    if not args.download_models:
        print("OCR 모델이 없다면 .venv-review/bin/python -m backend.app.ocr_runtime --download-models를 실행하세요.")


if __name__ == "__main__":
    main()
