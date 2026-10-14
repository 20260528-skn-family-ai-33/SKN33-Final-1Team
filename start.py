"""실행 전 부족한 항목을 안내하고, 준비된 프로젝트 환경에서 검수 서버를 실행한다."""
import argparse
from html.parser import HTMLParser
import importlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

from backend.app.ocr_config import selected_pipeline
from backend.app.ocr_runtime import MODEL_FILES

ROOT = Path(__file__).resolve().parent
SERVER_PACKAGES = (("uvicorn", "uvicorn"), ("fastapi", "fastapi"),
                   ("Pillow", "PIL.Image"), ("python-multipart", "multipart"))
OCR_PACKAGES = (("torch", "torch"), ("torchvision", "torchvision"),
                ("easyocr", "easyocr"), ("numpy", "numpy"),
                ("opencv-python-headless", "cv2"))


def requirements_file():
    """원격·Paddle 단독은 EasyOCR 없이 실행 가능한 공통 패키지만 안내한다."""
    target = os.getenv("RUN_TARGET", "local").strip().lower()
    pipeline = selected_pipeline()
    return "requirements-test.txt" if target == "runpod" or pipeline == "paddle-only" else "requirements.txt"


def print_ocr_selection(windows):
    """선택한 OCR과 다른 경로로 실행하는 명령을 패키지 검사 전에 안내한다."""
    pipeline = selected_pipeline()
    target = os.getenv("RUN_TARGET", "local").strip().lower()
    source = "사용자 지정" if "OCR_PIPELINE" in os.environ else "기본값"
    print(f"[OCR 실행 설정] RUN_TARGET={target} · OCR_PIPELINE={pipeline} ({source})", flush=True)
    print("로컬 기본: paddle-only = PaddleOCR 단독 탐지·인식 / RunPod 기본: baseline", flush=True)
    print("선택 경로: baseline(EasyOCR), multiline(줄 결합), adaptive(영역 재인식), "
          "paddle(EasyOCR+Paddle), paddle-only(Paddle 단독), qwen(EasyOCR+Qwen)", flush=True)
    print("[다른 OCR로 실행] 아래 명령은 안내이며 자동 실행하지 않습니다.", flush=True)
    if windows:
        print(r'$env:OCR_PIPELINE = "baseline"; .\run.cmd', flush=True)
        print(r'$env:OCR_PIPELINE = "paddle"; .\run.cmd', flush=True)
        print(r'기본값으로 복귀: Remove-Item Env:OCR_PIPELINE -ErrorAction SilentlyContinue; .\run.cmd', flush=True)
    else:
        print("OCR_PIPELINE=baseline ./run.sh", flush=True)
        print("OCR_PIPELINE=paddle ./run.sh", flush=True)
        print("기본값으로 복귀: env -u OCR_PIPELINE ./run.sh", flush=True)
    print("adaptive·paddle·paddle-only·qwen은 RUN_TARGET=local에서 사용합니다.\n", flush=True)


# 빌드 HTML에서 실행에 필요한 스크립트와 스타일 경로만 읽는다.
class BuildAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script" and values.get("src"):
            self.urls.append(values["src"])
        if tag == "link" and set(values.get("rel", "").split()) & {"stylesheet", "modulepreload"}:
            if values.get("href"):
                self.urls.append(values["href"])


# 다른 운영체제의 환경은 보존하고 현재 운영체제에 맞는 생성 명령만 안내한다.
def environment_failure(reason, windows, preserve=False):
    print(f"[실행 중단] {reason}")
    print(f"작업 폴더: {ROOT}")
    if preserve:
        print("기존 .venv를 다른 이름으로 보관한 뒤 아래 명령을 실행하세요. 자동으로 삭제하지 않습니다.")
    print("[준비 명령] 각 명령이 성공하면 다음 명령을 실행하세요.")
    if windows:
        print("uv가 없다면 먼저 설치: winget install --id astral-sh.uv --exact")
        print("uv venv --python 3.12 --seed .venv")
    else:
        print("Python 3.12가 없다면 먼저 설치하세요.")
        print("python3.12 -m venv .venv")
    python = r".\.venv\Scripts\python.exe" if windows else ".venv/bin/python"
    print(f"{python} -m pip install -r {requirements_file()}")
    print("설치 후 다시 실행: " + (r".\run.cmd" if windows else "./run.sh"))
    raise SystemExit(1)


# 패키지 오류를 모두 수집하고 서버나 모델을 실행하지 않는다.
def package_problems(packages):
    problems = []
    modules = {}
    for package, module in packages:
        try:
            modules[module] = importlib.import_module(module)
        except Exception as error:
            missing = isinstance(error, ModuleNotFoundError) and error.name == module.split(".")[0]
            label = "누락" if missing else "불러오기 실패"
            problems.append(f"[{label}] {package}: {type(error).__name__}: {error}")
    return problems, modules


# index.html과 참조된 로컬 JS·CSS가 실제 빌드 폴더 안에 있는지 확인한다.
def frontend_problems():
    directory = (ROOT / "frontend" / "dist").resolve()
    entry = directory / "index.html"
    problems = []
    try:
        if not entry.is_file() or entry.stat().st_size == 0:
            return ["[누락] React 빌드: frontend/dist/index.html"]
        parser = BuildAssets()
        parser.feed(entry.read_text(encoding="utf-8"))
        for url in dict.fromkeys(parser.urls):
            parsed = urlsplit(url)
            if parsed.scheme or parsed.netloc:
                continue
            asset = (directory / unquote(parsed.path).lstrip("/")).resolve()
            if not asset.is_relative_to(directory) or not asset.is_file() or asset.stat().st_size == 0:
                problems.append(f"[누락] React 빌드 자산: {url}")
    except (OSError, UnicodeError, ValueError) as error:
        problems.append(f"[불러오기 실패] React 빌드: {error}")
    return problems


# 로컬 모델의 존재와 크기만 확인하며 모델 로드나 다운로드는 하지 않는다.
def model_problems():
    directory = Path(os.getenv("OCR_MODEL_DIR", str(ROOT / ".runtime" / "easyocr"))).expanduser()
    problems = []
    for name in MODEL_FILES:
        path = directory / name
        try:
            if not path.is_file() or path.stat().st_size == 0:
                problems.append(f"[누락] OCR 모델: {path}")
        except OSError as error:
            problems.append(f"[불러오기 실패] OCR 모델 {name}: {error}")
    return problems


def paddle_setup_commands(python, windows, *, include_detection=False):
    """기존 격리 환경을 덮어쓰지 않고 복구·설치·다운로드 명령만 반환한다."""
    relative_python = r".runtime\paddle-env\Scripts\python.exe" if windows else ".runtime/paddle-env/bin/python"
    environment = ROOT / ".runtime" / "paddle-env"
    executable = environment / ("Scripts/python.exe" if windows else "bin/python")
    warnings, commands = [], []
    if not executable.is_file():
        if environment.exists():
            warnings.append("[안내] 기존 .runtime/paddle-env에 현재 운영체제의 Python이 없습니다. 다른 이름으로 보관한 뒤 생성 명령을 실행하세요.")
        commands.append(f"{python} -m venv .runtime/paddle-env")
    # 기존 환경의 pip 유무와 무관하게 안전하게 복구한 후 패키지를 설치하도록 안내한다.
    commands.extend([f"{relative_python} -m ensurepip --upgrade",
                     f"{relative_python} -m pip install -r requirements-paddle.txt",
                     f"{python} -m scripts.prepare_paddle" + (" --include-detection" if include_detection else "")])
    return warnings, commands


# 선택한 실행 모드의 준비 상태와 필요한 복구 명령을 함께 반환한다.
def startup_checks(windows):
    python = r".\.venv\Scripts\python.exe" if windows else ".venv/bin/python"
    npm = "npm.cmd" if windows else "npm"
    target = os.getenv("RUN_TARGET", "local").strip().lower()
    pipeline = selected_pipeline()
    problems, modules = package_problems(SERVER_PACKAGES)
    package_errors = bool(problems)
    warnings = []
    commands = []
    pip_missing = importlib.util.find_spec("pip") is None
    if pip_missing:
        warnings.append("[안내] pip가 없습니다. 실행 의존성이 준비되어 있으면 서버 실행은 가능합니다.")
        commands.append(f"{python} -m ensurepip --upgrade")
    if target not in {"local", "runpod"}:
        problems.append("[설정 오류] RUN_TARGET은 local 또는 runpod이어야 합니다.")
        commands.append('$env:RUN_TARGET = "local"' if windows else "export RUN_TARGET=local")
    if pipeline not in {"baseline", "multiline", "adaptive", "paddle", "paddle-only", "qwen"}:
        problems.append("[설정 오류] OCR_PIPELINE은 baseline, multiline, adaptive, paddle, paddle-only, qwen 중 하나여야 합니다.")
    if target == "runpod" and pipeline in {"adaptive", "paddle", "paddle-only", "qwen"}:
        problems.append(f"[설정 오류] OCR_PIPELINE={pipeline}은 RUN_TARGET=local에서만 지원합니다.")
        commands.append('$env:RUN_TARGET = "local"' if windows else "export RUN_TARGET=local")
    if target == "runpod":
        if not os.getenv("RUNPOD_API_KEY", "").strip():
            problems.append("[설정 누락] RUNPOD_API_KEY")
        endpoint = os.getenv("RUNPOD_ENDPOINT_ID", "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", endpoint):
            problems.append("[설정 오류] RUNPOD_ENDPOINT_ID가 없거나 형식이 잘못되었습니다.")
        if any("[설정" in problem for problem in problems):
            warnings.append("[안내] 서버 터미널에 RunPod 설정을 지정하세요. .env.example은 자동으로 읽지 않습니다.")
        else:
            warnings.append("[안내] RunPod 연결 설정이 있습니다. 실제 worker 연결은 검수 요청 시 확인합니다.")
    elif pipeline != "paddle-only":
        ocr_errors, _ = package_problems(OCR_PACKAGES)
        problems.extend(ocr_errors)
        package_errors = package_errors or bool(ocr_errors)
    if package_errors:
        if pipeline == "paddle-only":
            warnings.append("[안내] requirements-test.txt는 EasyOCR·PyTorch 없이 서버 공통 패키지를 준비하는 데 사용합니다.")
        commands.append(f"{python} -m pip install -r {requirements_file()}")
    if target != "runpod" and pipeline != "paddle-only":
        missing_models = model_problems()
        problems.extend(missing_models)
        if missing_models:
            commands.append(f"{python} -m backend.app.ocr_runtime --download-models")
    # 새 모델도 실행 전에 점검하되 설치·모델 다운로드는 사용자가 명시적으로 실행한다.
    if pipeline == "paddle-only" and target == "local":
        # 단독 경로에서는 EasyOCR import·모델 검사 없이 Paddle 격리 환경만 점검한다.
        from backend.app.paddle_ocr import paddle_readiness
        ready, detail = paddle_readiness(check_import=True)
        if not ready:
            problems.append(f"[모델 준비 부족] {detail}")
            paddle_warnings, paddle_commands = paddle_setup_commands(python, windows, include_detection=True)
            warnings.extend(paddle_warnings)
            commands.extend(paddle_commands)
    elif pipeline in {"paddle", "qwen"} and target == "local":
        from backend.app.model_ocr import model_readiness
        ready, detail = model_readiness(pipeline, check_import=True)
        if not ready:
            problems.append(f"[모델 준비 부족] {detail}")
            if pipeline == "paddle":
                paddle_warnings, paddle_commands = paddle_setup_commands(python, windows)
                warnings.extend(paddle_warnings)
                commands.extend(paddle_commands)
            else:
                commands.extend([f"{python} -m scripts.local_ollama serve  # 별도 터미널에서 유지",
                                 f"{python} -m scripts.local_ollama pull"])
    build_errors = frontend_problems()
    problems.extend(build_errors)
    if build_errors:
        if not shutil.which(npm):
            warnings.append("[안내] 빌드를 만들려면 Node.js 22.12 이상과 npm을 먼저 설치하세요.")
        commands.extend([f"{npm} --prefix frontend ci", f"{npm} --prefix frontend run build"])
    return problems, warnings, commands, modules.get("uvicorn")


# 올바른 프로젝트 Python에서만 검사하고, 준비가 끝난 경우에만 서버를 시작한다.
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check-only", action="store_true", help="준비 상태만 확인하고 서버를 실행하지 않습니다.")
    args = parser.parse_args()
    windows = sys.platform == "win32"
    start_command = r".\run.cmd" if windows else "./run.sh"
    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if windows else "bin/python")
    if not python.is_file():
        environment_failure(f"프로젝트 .venv의 Python이 없습니다: {python}", windows, environment.exists())
    if Path(sys.prefix).resolve() != environment.resolve():
        if os.environ.get("BARO_REVIEW_PROJECT_ENV") == str(environment.resolve()):
            environment_failure(".venv Python이 프로젝트 가상환경을 사용하지 않습니다. 반복 실행을 중단합니다.", windows, True)
        print(f"프로젝트 .venv Python으로 실행합니다: {python}", flush=True)
        child_env = dict(os.environ, BARO_REVIEW_PROJECT_ENV=str(environment.resolve()))
        try:
            child = subprocess.Popen([str(python), str(ROOT / "start.py"), *sys.argv[1:]], cwd=ROOT, env=child_env)
        except OSError as error:
            environment_failure(f"프로젝트 Python을 실행하지 못했습니다: {type(error).__name__}", windows, True)
        try:
            returncode = child.wait()
        except KeyboardInterrupt:
            # 첫 Ctrl+C는 정상 종료를 기다리고 두 번째 입력에서만 자식 프로세스를 종료한다.
            try:
                child.wait()
            except KeyboardInterrupt:
                child.terminate()
                child.wait()
            raise SystemExit(130) from None
        raise SystemExit(returncode)
    if Path(sys.prefix).resolve() == Path(sys.base_prefix).resolve():
        environment_failure("프로젝트 Python이 가상환경을 사용하지 않습니다.", windows, True)
    if sys.version_info[:2] != (3, 12):
        environment_failure("프로젝트 .venv에는 Python 3.12가 필요합니다.", windows, True)
    print_ocr_selection(windows)
    print(f"[실행 전 검사] Python: {sys.executable}", flush=True)
    problems, warnings, commands, uvicorn = startup_checks(windows)
    for message in [*problems, *warnings]:
        print(message)
    if commands:
        print("[준비 명령] 저장소 루트에서 각 명령이 성공하면 다음 명령을 실행하세요.")
        for command in dict.fromkeys(commands):
            print(command)
    if problems:
        print(f"[실행 중단] 준비되지 않은 항목 {len(problems)}개가 있습니다. 설치·다운로드는 자동 실행하지 않습니다.")
        print(f"준비 후 다시 실행: {start_command}")
        raise SystemExit(1)
    print("[준비 완료] 패키지와 빌드 파일을 확인했습니다. OCR 모델 로드·추론은 실제 검수에서 확인합니다.")
    if args.check_only:
        return
    print(f"\n브라우저 주소: http://{args.host}:{args.port}", flush=True)
    print("실행 중에는 터미널에 입력 프롬프트가 돌아오지 않는 것이 정상입니다.", flush=True)
    print("이 터미널을 유지하고 브라우저를 여세요. Ctrl+C는 서버 종료입니다.\n", flush=True)
    uvicorn.run("backend.app.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
