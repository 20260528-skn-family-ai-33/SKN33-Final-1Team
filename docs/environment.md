# 환경 설정과 Python 의존성

루트의 환경 예시는 실행용·관리용·시험용 세 개로 유지한다. 개인 설정은 직접 복사하고 실행 명령에서 선택한다. 기존 개인 파일·DB·모델 환경은 자동으로 옮기거나 덮어쓰지 않는다.

| 예시 파일 | 개인 파일 예 | 용도 |
| --- | --- | --- |
| `.env.example` | `.env`, `.env.local`, `.env.staging` | API·worker·MCP 및 로컬 영상 연결에 필요한 공통 설정 |
| `.env.admin.example` | `.env.admin.local`, `.env.admin.staging` | 빈 DB 초기화, 마이그레이션, 계정 관리, 백업·복구 |
| `.env.test.example` | `.env.test` | 삭제 가능한 전용 DB의 통합 검사 |

실제 값이 있는 파일은 Git에 넣지 않는다. 예시의 비밀번호·토큰은 직접 바꾸고, 실행 파일과 관리 파일에서 같은 DB를 가리키는 값은 일치시킨다. 파일을 바꿔도 기존 DB 볼륨의 비밀번호가 바뀌지는 않는다.

로컬 포스터·영상 구성은 `.env.example`을 `.env`로 복사하고 DB 앱 계정, 세션 비밀, Qwen 및 Neo4j 값을 채운다. 기존 파일이 있으면 복사하여 덮어쓰지 않는다. `--env-file .env`를 명시하면 포스터 프레임워크와 영상 API가 같은 앱 설정을 사용한다. `.env`는 Git에서 제외된다. `VIDEO_LOGO_WORKER_URL`은 실행 중인 SSH 터널의 로컬 주소이고, Qwen 서버의 키와 터널도 별도로 준비해야 한다. `FFMPEG_BIN`을 비우면 각 컴퓨터에서 PATH 또는 imageio-ffmpeg를 사용한다.

로컬에서 모든 값을 하나로 보관하려면 `.env`에 `CC_ENV_MODE=local_unified`, `APP_ENV=development`를 지정한다. DB 관리자 비밀번호·앱 계정·모델 키를 함께 넣어도 실행기가 서비스용 값에서 관리자 비밀번호를 제외한다. 이 방식은 로컬 개발용이다. 전체 파일을 `source`하거나 프로세스 환경에 일괄 export하지 않는다. 기존 `.env.admin.local`과 `.runtime` 설정 파일은 보존하며 기존 명령도 계속 사용할 수 있다.

DB Compose는 `docker compose --env-file .env -f compose.yaml up -d`로 명시적으로 관리자 값을 사용한다. 그래프 실행기는 `python scripts/framework_graph_runtime.py up --env-file .env --graph-env-file .env`로 같은 파일을 선택할 수 있다. `AF_NEO4J_PASSWORD`와 `CC_NEO4J_PASSWORD`에는 기존 개발 그래프의 동일한 비밀번호를 넣는다. 설정 파일 변경은 기존 DB의 비밀번호를 바꾸거나 초기화하지 않는다. 새 예시의 `CC_NEO4J_URI`는 프레임워크 개발 그래프 포트 57631이며 재구축 구성에서는 해당 그래프 주소로 변경해야 한다.

staging/production은 `CC_ENV_MODE=separate`로 바꾸고, 실행 파일에서 관리자 비밀번호를 제거해 관리 파일에 별도 보관한다. 기본 검사에서는 통합 표시가 없는 관리자 파일과 환경변수로 export된 관리자 비밀번호를 계속 거부한다.

Docker API·worker 실행기는 컨테이너에 마운트할 임시 파일을 만들기 전에 공통 역할 허용 목록을 적용한다. API에는 DB·세션 값을, 검수 worker에는 DB·모델·그래프 값을 담으며 다른 역할의 비밀값은 임시 파일에도 복사하지 않는다.

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

루트 `requirements.txt`는 같은 잠금 파일에서 생성한 **공통 앱 실행용** pip 설치 목록이다. Python 3.12 또는 3.13을 사용하는 Mac·Windows·Linux의 별도 가상환경에서 `python -m pip install -r requirements.txt`로 설치할 수 있다. 테스트 도구는 포함하지 않으므로 개발·CI는 기존 `uv sync --locked --group dev`를 사용한다. 프런트엔드 패키지는 별도이며 `npm --prefix frontend ci`로 설치한다.

공통 의존성을 변경할 때는 `pyproject.toml`과 `uv.lock`을 먼저 갱신하고 다음 명령으로 내보낸다. 내보내기는 lock을 변경하지 않으며, lock과 pyproject의 일치 여부는 `uv sync --locked`에서 확인한다.

```text
python scripts/export_requirements.py
python scripts/export_requirements.py --check
```

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

공통 파일은 실행 역할에 필요한 값만 전달한다. API에 Qwen 인증키·MCP 토큰을, worker에 브라우저 세션 비밀을 전달하지 않는다. 로컬 통합 표시가 없는 관리 파일이나 관리자 비밀번호를 export한 프로세스 환경은 서비스 실행기가 거부하고, 테스트 파일도 서비스 설정으로 선택할 수 없다. `uv run --env-file ...`로 파일 전체를 미리 export하는 대신 **Python 실행 명령 뒤의 `--env-file`**을 사용한다. 관리 CLI에는 기존 `uv run --env-file .env.admin.local ...` 방식이 필요하다. 프레임워크 worker는 모델을 직접 호출하므로 `CC_QWEN_*`와 기존 `A04_A05_MODEL_*` 설정을 받는다.

v1 API와 OCR worker는 기본값이 다르므로 공통 파일에서 `REVIEW_PIPELINE`을 생략한다. API는 `disabled`, OCR worker는 `paddle-only`를 선택하며 worker의 `REVIEW_EXECUTOR=postgres_worker`, `RUN_TARGET=local`, `LOCAL_ADMIN_LOGIN=0` 조건을 유지한다. 명시적으로 잘못 지정한 값은 기존 검증에서 거부한다.

프런트엔드는 상대 경로 `/api`와 기존 Vite 프로필을 사용한다. Vite는 루트 `.env`에서 세 프록시 주소만 읽고, 터미널에 지정한 값이 우선한다. 설정을 바꾼 뒤 Vite를 재시작한다. 서버 비밀번호를 `VITE_*`에 복사하지 않는다.

## 같은 네트워크에서 로컬 시연

로컬 시연을 다른 PC에서 볼 때는 서버 PC의 사설 IPv4 주소를 `AUTH_ALLOWED_HOSTS`에, `http://서버IP:5173`을 `AUTH_ALLOWED_ORIGINS`에 추가한다. `APP_ENV=development`, `LOCAL_ADMIN_LOGIN=0`을 유지하고, 수정한 API 이미지와 영상 API를 같은 env로 재실행한다. 프런트는 `npm --prefix frontend run dev:lan`으로 실행한다. 같은 네트워크의 PC에서는 출력된 Network 주소를 열고 기존 계정으로 로그인한다. `127.0.0.1`은 접속하는 PC 자신을 뜻하므로 공유 주소로 사용하지 않는다.

이 구성은 5173 프런트만 LAN에 바인딩한다. 포스터·영상 API와 GPU 터널은 서버 PC의 loopback에 유지하며 Vite가 요청을 중계한다. 인증된 영상 경로도 명시적으로 허용한 Host와 로컬 프록시 요청만 받는다. 개발자 우회 로그인·임의 HTTP 도메인·공개 IP 허용은 추가하지 않는다. 내부 IP가 바뀌면 env의 호스트·Origin도 갱신하고 재시작한다.

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
| `deploy/video-worker/requirements-worker.txt` | Linux 영상 로고 worker. 기존 `requirements-logo.txt`와 CPU Paddle 목록을 사용 |
| `deploy/video-worker/requirements-paddle-gpu*.txt` | Linux 영상 OCR GPU 프로세스용 별도 환경. CPU Paddle과 함께 설치하지 않음 |
| `deploy/qwen-a02/requirements-server.txt` | 포스터용 Qwen/vLLM 서버의 별도 Linux GPU 환경 |
| `experiments/` 아래 requirements·환경 예시 | CUDA·학습·벤치마크의 재현 조건 보존 |

공통 앱과 영상 worker는 각각 NumPy 2.5.3과 2.2.6을 사용한다. 하나의 가상환경에 합치거나 CUDA 전용 환경에 공통 sync를 실행하지 않는다. `requirements-paddle-common.txt`는 CPU·GPU가 공유하는 PaddleOCR 버전만 정의한다. PaddleOCR은 기존 **영상** 경로에만 쓰며 포스터 A02 추출 경로에 추가하지 않는다.

### RunPod 영상 worker 설치

다음은 Python 3.12, CUDA 12.8, `torch==2.8.0+cu128` 및 `torchvision==0.23.0+cu128`이 이미 설치된 Linux 이미지에서 확인한 구성이다. Pod 이미지마다 기본 설치가 다르므로 먼저 버전을 확인한다. 아래 경로는 새로운 환경의 예시이며 기존 환경이나 모델은 변경하지 않는다. `/opt`는 Pod 재생성 시 사라질 수 있다.

```bash
cd /opt/creative-checker-video/repository
python3 -c 'import torch, torchvision; print(torch.__version__, torchvision.__version__, torch.cuda.is_available())'
python3 -m venv --system-site-packages /opt/creative-checker-video/runtime/venv
/opt/creative-checker-video/runtime/venv/bin/python -m pip install -r deploy/video-worker/requirements-worker.txt
```

목록의 `constraints-cu128.txt`는 이 조합의 PyTorch 버전이 의도치 않게 바뀌지 않도록 제한한다. 베이스 이미지에 해당 버전이 없으면 먼저 그 이미지에 맞는 CUDA wheel을 별도로 준비해야 한다. 다른 CUDA 버전이나 Windows에서의 GPU 실행은 이 프로필로 검증하지 않았다.

GPU OCR은 CPU Paddle이 설치된 worker와 다른 가상환경에 설치한다. Paddle GPU wheel은 공급자의 CUDA 12.6 패키지 저장소를 추가로 사용하며, 아래 파일은 이번 CUDA 12.8 Pod에서 동작을 확인한 버전을 기준으로 한다.

```bash
python3 -m venv /opt/creative-checker-video/runtime/paddle-gpu
/opt/creative-checker-video/runtime/paddle-gpu/bin/python -m pip install -r deploy/video-worker/requirements-paddle-gpu.txt
/opt/creative-checker-video/runtime/paddle-gpu/bin/python -m pip install -r deploy/video-worker/requirements-paddle-gpu-ocr.txt
```

첫 명령은 공급자 저장소에서 CUDA wheel을 선택하고, 둘째 명령은 PyPI에서 OCR 패키지를 설치한다. 이 설치는 모델 다운로드나 기존 프로세스 설정 변경을 수행하지 않는다. 별도 Pod의 모델 파일·worker 주소·터널 설정은 [영상·포스터 로컬 병행 실행](agent-framework/영상_포스터_로컬_병행실행.md)을 참고한다. 영상 requirements는 직접 의존성 버전을 지정하지만 공통 `uv.lock`과 같은 완전한 하위 의존성 잠금은 아니다.

기존 개인 파일 이름은 그대로 사용할 수 있지만 삭제된 예시 파일과 requirements를 새로 복사·설치하는 명령은 이 문서의 방식으로 바꾼다. `share/`, 검증 기록, 배포 시점 HTML 등 과거 산출물의 기록은 당시 증거로 보존한다.
