"""Create a dedicated local review environment, install dependencies, build React."""
import argparse
import json
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
    windows = sys.platform == "win32"
    setup_command = r"py -3.12 .\setup_local.py" if windows else "python3.12 setup_local.py"
    python_command = r".\.venv\Scripts\python.exe" if windows else ".venv/bin/python"
    start_command = r".\run.cmd" if windows else "./run.sh"
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(f"이 프로젝트는 Python 3.12로 검증했습니다. {setup_command}를 사용하세요.")
    npm = shutil.which("npm.cmd" if windows else "npm")
    if not npm:
        raise SystemExit("Node.js 22.12 이상과 npm을 먼저 설치하세요.")
    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if windows else "bin/python")
    if not python.is_file():
        if environment.exists():
            raise SystemExit(
                f"기존 .venv에서 실행 가능한 Python을 찾지 못했습니다: {python}\n"
                "다른 운영체제에서 만든 환경이거나 설치가 불완전할 수 있습니다.\n"
                "기존 .venv를 다른 이름으로 보관한 뒤 설치를 다시 실행하세요. 자동으로 삭제하지 않습니다."
            )
        venv.EnvBuilder(with_pip=True).create(environment)
    try:
        details = json.loads(subprocess.run(
            [str(python), "-c", "import json, sys; print(json.dumps({'version': list(sys.version_info[:2]), 'prefix': sys.prefix, 'base_prefix': sys.base_prefix}))"],
            cwd=ROOT, check=True, capture_output=True, text=True, encoding="utf-8",
        ).stdout)
        version = details["version"]
        prefix = Path(details["prefix"]).resolve()
        base_prefix = Path(details["base_prefix"]).resolve()
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"기존 .venv의 Python을 실행하지 못했습니다: {python}\n{exc}") from None
    if prefix != environment.resolve() or prefix == base_prefix:
        raise SystemExit(
            f".venv Python이 프로젝트 가상환경을 사용하지 않습니다: {prefix}\n"
            "기존 .venv를 다른 이름으로 보관한 뒤 다시 설치하세요. 외부 Python 환경에는 패키지를 설치하지 않습니다."
        )
    if version != [3, 12]:
        raise SystemExit(
            f"기존 .venv는 Python {'.'.join(map(str, version))}입니다. 이 프로젝트에는 Python 3.12가 필요합니다.\n"
            "기존 .venv를 다른 이름으로 보관한 뒤 Python 3.12로 설치를 다시 실행하세요. 자동으로 덮어쓰지 않습니다."
        )
    commands = [
        [str(python), "-m", "pip", "install", "-r", "requirements-dev.txt"],
        [npm, "--prefix", "frontend", "ci"],
        [npm, "--prefix", "frontend", "run", "build"],
    ]
    if args.download_models:
        commands.append([str(python), "-m", "backend.app.ocr_runtime", "--download-models"])
    for command in commands:
        print(f"실행: {subprocess.list2cmdline(command)}", flush=True)
        try:
            subprocess.run(command, cwd=ROOT, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            raise SystemExit(
                f"설치가 중단되었습니다: {subprocess.list2cmdline(command)}\n"
                f"{exc}\n위 오류를 해결한 뒤 같은 설치 명령을 다시 실행하세요."
            ) from None
    print(f"설치 완료. 저장소 루트에서 다음 명령으로 실행하세요.\n{start_command}")
    if not args.download_models:
        print(f"OCR 모델이 없다면 다음 명령으로 다운로드하세요.\n{python_command} -m backend.app.ocr_runtime --download-models")


if __name__ == "__main__":
    main()
