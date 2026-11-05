# 환경 설정과 Python 의존성

Issue #54부터 루트의 환경 예시는 세 개만 유지한다. 개인 설정은 직접 복사하고 실행 명령에서 선택한다. 기존 개인 파일·DB·모델 환경은 자동으로 옮기거나 덮어쓰지 않는다.

| 예시 파일 | 개인 파일 예 | 용도 |
| --- | --- | --- |
| `.env.example` | `.env.local`, `.env.staging` | API·worker·MCP 실행에 필요한 공통 설정 |
| `.env.admin.example` | `.env.admin.local`, `.env.admin.staging` | 빈 DB 초기화, 마이그레이션, 계정 관리, 백업·복구 |
| `.env.test.example` | `.env.test` | 삭제 가능한 전용 DB의 통합 검사 |

실제 값이 있는 파일은 Git에 넣지 않는다. 예시의 비밀번호·토큰은 직접 바꾸고, 실행 파일과 관리 파일에서 같은 DB를 가리키는 값은 일치시킨다. 파일을 바꿔도 기존 DB 볼륨의 비밀번호가 바뀌지는 않는다.

공통 파일에서 사용하지 않는 주석 예시는 [선택 설정 참고](environment-options.md)로 분리했다. 활성 Qwen·DB·인증 설정은 유지한다. 이 문서에는 실행·관리·시험 역할별 사용처와 정리·원본 백업 확인도 기록했다.

## 로컬 개발

새 작업 폴더에서 다음과 같이 준비한다. 파일이 이미 있으면 복사 대신 필요한 항목만 반영한다.

```powershell
Copy-Item .env.example .env.local
Copy-Item .env.admin.example .env.admin.local
uv sync --locked --group dev
npm --prefix frontend ci
```

Python 설치 정본은 `pyproject.toml`과 `uv.lock`이다. 새 공통 환경은 `.venv`를 사용한다. 기존 CUDA/OCR 패키지를 따로 설치한 `.venv`가 있다면 그대로 보존하고, 새 터미널에서 `$env:UV_PROJECT_ENVIRONMENT = '.venv-common'`을 지정해 동기화한다. 이때 실행도 `uv run --locked --no-sync python ...`을 사용한다. `run.cmd`·`run.sh`와 `setup_local.py`는 기존 계약대로 `.venv`를 사용한다. 새 공통 환경에서 legacy 실행기가 필요한 경우에는 기존 환경을 먼저 다른 이름으로 보관한다.

재구축 업무는 각 터미널에서 같은 파일을 지정한다. worker는 접수된 작업을 처리하는 별도 Python 프로세스이고, MCP는 모델 호출 등 도구를 제공하며, projection은 DB의 변경을 그래프에 반영한다.

```powershell
uv run --locked --no-sync python start.py api --env-file .env.local
uv run --locked --no-sync python start.py mcp --env-file .env.local
uv run --locked --no-sync python start.py worker --env-file .env.local
uv run --locked --no-sync python start.py projection --env-file .env.local
npm --prefix frontend run dev:rebuild
```

이 명령은 의존성 설치·DB 초기화·모델 다운로드를 하지 않는다. DB 준비와 계정 발급은 [재구축 실행가이드](rebuild/original-execution-guide.md)를 따른다. 기존 M008 DB와 재구축 DB는 서로 다른 스키마이므로 같은 DB로 합치지 않는다.

기존 실행 경로도 공통 파일을 받을 수 있다.

| 실행 경로 | 명령 |
| --- | --- |
| 기존 시제품 | `python start.py --env-file .env.local` |
| 기존 PostgreSQL v1 API | `python -m scripts.run_v1 --env-file .env.local` |
| 기존 v1 OCR worker | `python -m backend.app.worker --env-file .env.local` |
| 프레임워크 API | `python -m backend.agent_framework postgres-serve --env-file .env.local --config examples/agent_framework/team_config.synthetic.json --port 8012` |
| 프레임워크 worker | `python -m backend.agent_framework worker --env-file .env.local --config examples/agent_framework/team_config.synthetic.json` |

각 Python 명령은 준비한 가상환경에서 실행한다. 프레임워크 API를 사용할 때는 `AUTH_ALLOWED_ORIGINS`에 실제 접속 주소(예: `http://localhost:8012`)를 지정한다. 예시의 M008 설정은 실제 DB·계정·`STORAGE_ROOT`에 맞춘다. 기존 파일 저장형 시제품은 `PERSISTENCE_MODE=legacy_json`를 선택한다.

`--env-file`을 생략하면 프로세스 환경변수만 읽는다. `.env.local`이나 `.env.staging`을 자동 탐색하지 않는다. **기존 프로세스 환경변수가 선택한 파일보다 우선**하므로 다른 환경으로 바꿀 때는 이전 설정을 export한 터미널을 재사용하지 않는다. 파일 값은 기존 파서대로 문자열이며 `${변수}` 치환이나 셸 실행은 지원하지 않는다.

공통 파일은 실행 역할에 필요한 값만 전달한다. API에 Qwen 인증키·MCP 토큰을, worker에 브라우저 세션 비밀을 전달하지 않는다. 관리용 비밀번호가 있는 파일이나 프로세스 환경은 서비스 실행기가 거부하고, 테스트 파일도 서비스 설정으로 선택할 수 없다. `uv run --env-file ...`로 파일 전체를 미리 export하는 대신 **Python 실행 명령 뒤의 `--env-file`**을 사용한다. 관리 CLI에는 기존 `uv run --env-file .env.admin.local ...` 방식이 필요하다. 프레임워크 worker는 모델을 직접 호출하므로 `CC_QWEN_*`와 기존 `A04_A05_MODEL_*` 설정을 받는다.

v1 API와 OCR worker는 기본값이 다르므로 공통 파일에서 `REVIEW_PIPELINE`을 생략한다. API는 `disabled`, OCR worker는 `paddle-only`를 선택하며 worker의 `REVIEW_EXECUTOR=postgres_worker`, `RUN_TARGET=local`, `LOCAL_ADMIN_LOGIN=0` 조건을 유지한다. 명시적으로 잘못 지정한 값은 기존 검증에서 거부한다.

프런트엔드는 상대 경로 `/api`와 기존 Vite 프로필을 사용하므로 별도 환경 예시를 만들지 않는다. 다른 API 주소가 필요하면 프런트 실행 터미널에 `API_PROXY_TARGET`을 지정한다. `.env.local`의 서버 비밀번호를 Vite의 환경변수나 `VITE_*`에 복사하지 않는다.

## 배포 테스트

`.env.example`에서 별도 `.env.staging`을 준비하고 대상 DB·저장소·모델 endpoint를 지정한다. HTTPS 환경은 `CC_COOKIE_SECURE=true`, TLS 인증서 경로를 설정한다. 서비스 Compose도 환경 파일 전체를 컨테이너에 전달하지 않고 역할별 변수를 명시한다.

```powershell
docker compose --env-file .env.staging -f deploy/compose.v1.yaml config --quiet
docker compose --env-file .env.staging -f deploy/compose.worker.yaml config --quiet
```

이 명령은 구성 확인만 수행한다. 실제 이미지 빌드·기동은 대상 환경에서 별도로 수행한다. 두 호스트 배포에는 같은 이미지와 공유 저장소(S3)가 필요하다. 기존 배포 설명은 [재구축 실행가이드](rebuild/original-execution-guide.md#배포-구성)를 따른다.

DB 초기화 Compose(`compose.yaml`, `compose.rebuild.yaml`)에는 `.env.admin.local` 또는 `.env.admin.staging`을 명시한다. 삭제한 `.env.deploy.example`의 `CC_WEB_ENV_FILE`·`CC_WORKER_ENV_FILE`·이미지/네트워크 선택 변수는 현재 재구축 Compose에서 사용하지 않는다. TLS 디렉터리는 현재 변수인 `CC_TLS_DIRECTORY`로 지정한다. 마이그레이션·백업·복구 명령의 기존 확인 절차를 유지한다. 기존의 `--app-env-file`·`--worker-env-file`에는 같은 실행 설정 파일을 지정할 수 있고, owner 입력에는 관리 파일을 지정한다.

## 테스트와 GitHub Actions

기본 설치에는 PyTorch·EasyOCR·PaddleOCR·모델 가중치를 포함하지 않는다. GitHub Actions도 `uv sync --locked --group dev`만 사용하며 requirements를 추가 설치해 잠금 버전을 덮어쓰지 않는다. CI 전용 환경 예시는 만들지 않고 각 job의 `env`와 Secrets를 사용한다. 통합 검사 job의 테스트용 서비스 설정은 개발·배포 설정과 분리한다.

로컬 일반 검사는 다음과 같다.

```powershell
# Windows에서 기존 UTF-8 자료를 읽는 검사에 적용한다.
$env:PYTHONUTF8 = "1"
uv run --locked --no-sync pytest -q
```

DB 통합 검사는 `.env.test.example`을 별도 복사하고 **폐기 가능한 전용 인스턴스**를 지정한 뒤 실행한다. 기본 `TEST_DB_ALLOW_CREATE=0`은 M008 검사 DB 생성을 막는다. 해당 검사를 의도적으로 실행할 때만 `1`로 바꾸고 기존 검사 명령에 `TEST_DB_ENV_FILE`을 지정한다. 재구축 검사는 같은 테스트 파일의 주석 처리된 `CC_INTEGRATION_DATABASE_URL`·`CC_TEST_NEO4J_*`를 전용 인스턴스에 맞춰 활성화한다. DB·실모델 검사는 기본 pytest 실행에서 제외된다.

## 분리 유지하는 모델 의존성

| 파일 또는 옵션 | 유지 이유 |
| --- | --- |
| `uv sync --locked --group dev --extra legacy-ocr` | 기존 EasyOCR 실행을 선택했을 때만 PyTorch·torchvision·EasyOCR 설치 |
| `requirements-paddle.txt` | 기존 `.runtime/paddle-env`의 Paddle 전용 Python 환경 보존 |
| `requirements-logo.txt` | 공통 NumPy 2.5.3과 다른 NumPy 2.2.6 및 모델 패키지 조합을 사용하는 별도 환경 |
| `experiments/` 아래 requirements·환경 예시 | CUDA·학습·벤치마크의 재현 조건 보존 |

루트 requirements는 7개에서 모델용 예외 2개로 줄였다. 공통·DB·개발·테스트·v1 의존성은 pyproject/lock으로 합쳤다. 기존 통합 requirements의 고정 버전을 우선 반영했으며 패키지 일괄 업그레이드는 하지 않았다. CUDA 전용 wheel을 설치한 환경에는 공통 sync를 실행하지 않는다. RunPod 이미지의 CUDA 설치 절차도 별도 유지한다.

기존 개인 파일 이름은 그대로 사용할 수 있지만 삭제된 예시 파일과 requirements를 새로 복사·설치하는 명령은 이 문서의 방식으로 바꾼다. `share/`, 검증 기록, 배포 시점 HTML 등 과거 산출물의 기록은 당시 증거로 보존한다.
