"""Start the repository's React build and OCR API on the same local origin."""
import argparse
import importlib
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    windows = sys.platform == "win32"
    npm = "npm.cmd" if windows else "npm"
    setup_command = r"py -3.12 .\setup_local.py" if windows else "python3.12 setup_local.py"
    start_command = r".\run.cmd" if windows else "./run.sh"
    build_command = (
        f"{npm} --prefix frontend ci; if ($? -and ($LASTEXITCODE -eq 0)) {{ {npm} --prefix frontend run build }}"
        if windows else f"{npm} --prefix frontend ci\n{npm} --prefix frontend run build"
    )
    recovery_command = (
        f"{setup_command} --download-models; if ($? -and ($LASTEXITCODE -eq 0)) {{ {start_command} }}"
        if windows else f"{setup_command} --download-models\n{start_command}"
    )
    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if windows else "bin/python")
    if not python.is_file():
        raise SystemExit(
            f"프로젝트 .venv의 Python이 없습니다: {python}\n"
            f"저장소 루트에서 설치를 완료한 뒤 다시 실행하세요.\n{setup_command} --download-models"
        )
    if Path(sys.prefix).resolve() != environment.resolve():
        if os.environ.get("BARO_REVIEW_PROJECT_ENV") == str(environment.resolve()):
            raise SystemExit(
                f".venv Python이 프로젝트 가상환경을 사용하지 않습니다: {sys.prefix}\n"
                "기존 .venv를 다른 이름으로 보관한 뒤 다시 설치하세요. 반복 실행을 중단합니다.\n"
                f"{setup_command} --download-models"
            )
        print(f"프로젝트 .venv Python으로 실행합니다: {python}", flush=True)
        child_env = dict(os.environ, BARO_REVIEW_PROJECT_ENV=str(environment.resolve()))
        try:
            child = subprocess.Popen([str(python), str(ROOT / "start.py"), *sys.argv[1:]], cwd=ROOT, env=child_env)
        except OSError as exc:
            raise SystemExit(f"프로젝트 .venv Python을 실행하지 못했습니다: {python}\n{exc}") from None
        try:
            returncode = child.wait()
        except KeyboardInterrupt:
            # The shared console delivers Ctrl+C to the server too. Let it finish
            # graceful shutdown; a second Ctrl+C explicitly requests termination.
            try:
                child.wait()
            except KeyboardInterrupt:
                child.terminate()
                child.wait()
            raise SystemExit(130) from None
        raise SystemExit(returncode)
    if sys.version_info[:2] != (3, 12):
        raise SystemExit(
            "프로젝트 .venv에는 Python 3.12가 필요합니다. 기존 환경을 다른 이름으로 보관한 뒤 다시 설치하세요.\n"
            f"{setup_command} --download-models"
        )
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise SystemExit(
            "React 빌드가 없습니다. 저장소 루트에서 다음 명령을 실행하세요.\n"
            f"{build_command}"
        )
    try:
        uvicorn = importlib.import_module("uvicorn")
        for dependency in ("fastapi", "PIL.Image", "multipart"):
            importlib.import_module(dependency)
    except (ImportError, OSError) as exc:
        raise SystemExit(
            "프로젝트 .venv에서 서버 의존성을 불러오지 못했습니다.\n"
            f"현재 Python: {sys.executable}\n오류: {exc}\n"
            "저장소 루트에서 설치를 완료한 뒤 다시 실행하세요.\n"
            f"{recovery_command}"
        ) from None
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    print(f"\n브라우저 주소: http://{args.host}:{args.port}", flush=True)
    print("실행 중에는 터미널에 입력 프롬프트가 돌아오지 않는 것이 정상입니다.", flush=True)
    print("이 터미널을 유지하고 브라우저를 여세요. Ctrl+C는 서버 종료입니다.\n", flush=True)
    uvicorn.run("backend.app.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
