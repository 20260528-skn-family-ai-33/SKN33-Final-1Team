"""프로젝트 전용 로컬 Ollama를 명시적으로 실행하거나 Qwen 모델을 준비한다."""
import argparse
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = 'http://127.0.0.1:11435'
DEFAULT_REQUIREMENTS_MODEL = 'qwen3.5:4b'


def requirements_model():
    return os.getenv('REQUIREMENTS_LLM_MODEL', DEFAULT_REQUIREMENTS_MODEL).strip()


def configuration():
    local = ROOT / '.runtime/ollama/bin' / ('ollama.exe' if sys.platform == 'win32' else 'ollama')
    binary = str(local) if local.is_file() else shutil.which('ollama')
    env = {**os.environ, 'OLLAMA_HOST': '127.0.0.1:11435',
           'OLLAMA_MODELS': str(ROOT / '.runtime/ollama/models'),
           'OLLAMA_NO_CLOUD': '1', 'OLLAMA_NUM_PARALLEL': '1'}
    return binary, env


def requirements_status():
    """추론·다운로드 없이 문서 조건 모델의 연결 및 설치 여부를 확인한다."""
    from backend.app.model_ocr import ollama_request
    try:
        models = ollama_request('/api/tags', timeout=2).get('models')
        if not isinstance(models, list):
            return 'unavailable'
        model = requirements_model()
        if not any(isinstance(m, dict) and m.get('name') == model for m in models):
            return 'missing_model'
        capabilities = ollama_request('/api/show', {'model': model}, timeout=3).get('capabilities', [])
        return 'ready' if 'vision' in capabilities else 'missing_vision'
    except Exception:
        return 'unavailable'


@contextmanager
def requirements_server():
    """기본 로컬 주소의 설치된 Ollama만 시작하고, 자신이 시작한 프로세스만 종료한다."""
    if (os.getenv('RUN_TARGET', 'local').strip().lower() != 'local'
            or os.getenv('LEGACY_READ_ONLY') == '1'
            or os.getenv('REQUIREMENTS_LLM_AUTOSTART', '1') != '1'):
        yield
        return
    process, log = None, None
    try:
        status = requirements_status()
        if status == 'unavailable' and os.getenv('OCR_OLLAMA_URL', DEFAULT_URL).rstrip('/') == DEFAULT_URL:
            binary, env = configuration()
            if binary:
                try:
                    log_path = ROOT / '.runtime/requirements-ollama.log'
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log = log_path.open('a', encoding='utf-8')
                    process = subprocess.Popen([binary, 'serve'], cwd=ROOT, env=env,
                                               stdout=log, stderr=subprocess.STDOUT)
                    deadline = time.monotonic() + 15
                    while process.poll() is None and time.monotonic() < deadline:
                        status = requirements_status()
                        if status != 'unavailable':
                            break
                        time.sleep(.2)
                except OSError:
                    status = 'unavailable'
        if status == 'ready':
            print('[PDF 모델] 로컬 요구조건 모델 연결 확인 완료.', flush=True)
        elif status == 'missing_model':
            print('[PDF 모델 준비 필요] 요구조건 모델이 없습니다. scripts/local_ollama.py pull로 기본 모델을 준비하거나 REQUIREMENTS_LLM_MODEL을 확인하세요.', flush=True)
        elif status == 'missing_vision':
            print('[PDF 모델 확인 필요] 페이지 이미지를 읽을 수 있는 vision 모델을 REQUIREMENTS_LLM_MODEL로 지정하세요.', flush=True)
        else:
            print('[PDF 모델 연결 안 됨] PDF 추출에는 로컬 Ollama가 필요합니다. 설치 후 scripts/local_ollama.py serve를 실행하세요. 포스터 검수는 계속 사용할 수 있습니다.', flush=True)
        yield
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log is not None:
            log.close()


def main():
    """공식 설치된 CLI 또는 프로젝트 CLI로 로컬 serve/pull만 실행한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("serve", "pull"))
    args = parser.parse_args()
    binary, env = configuration()
    if not binary:
        parser.error("Ollama를 먼저 공식 https://ollama.com/download 에서 설치하세요.")
    model = requirements_model()
    if not model or len(model) > 150 or model.startswith('-') or any(c.isspace() for c in model):
        parser.error('REQUIREMENTS_LLM_MODEL 설정을 확인하세요.')
    command = [binary, "serve"] if args.action == "serve" else [binary, "pull", model]
    try:
        return subprocess.call(command, env=env, cwd=ROOT)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
