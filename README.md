# 바로검수 · SKN33 Final 1Team

포스터와 요구사항을 입력하고, 검수 기준을 확정한 뒤 OCR 근거를 확인하는 React + FastAPI 개발 프로젝트입니다. 기존 샘플의 실행 코드를 이 저장소로 통합했습니다.

## 실행

이 컴퓨터에서는 프로젝트 폴더에서 다음 명령을 실행합니다.

```bash
./run.sh
```

[http://127.0.0.1:8000](http://127.0.0.1:8000)을 열고 **샘플로 체험하기 → 검수 항목 확인 → 기준 확정 → 검수 시작** 순서로 진행합니다.

서버가 실행되는 동안 터미널을 유지하세요. 입력 프롬프트가 돌아오지 않는 것이 정상입니다. `Ctrl+C`는 서버 종료이며, 가상환경의 `activate`만 실행해서는 서버가 시작되지 않습니다. `frontend/index.html`을 파일로 열지 마세요.

8000번 포트를 다른 앱이 사용 중이면 `./run.sh --port 8001`로 실행하고 출력된 주소를 엽니다.

## 새 컴퓨터에서 설치

Python **3.12**, Node.js **22.12 이상**을 준비합니다. Node 20은 20.19 이상도 지원합니다.

```bash
python3.12 setup_local.py --download-models
./run.sh
```

설치 스크립트는 `.venv-review` 가상환경 생성, Python 패키지 설치, `npm ci`, React 빌드를 수행합니다. `--download-models`를 지정한 경우에만 OCR 모델을 다운로드합니다. 기존 `.venv`는 수정하지 않습니다.

수동 설치가 필요하면 다음 명령을 사용합니다.

```bash
python3.12 -m venv .venv-review
.venv-review/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
.venv-review/bin/python -m backend.app.ocr_runtime --download-models
.venv-review/bin/python start.py
```

Windows에서는 `.venv-review/bin/python`을 `.venv-review/Scripts/python.exe`로 바꾸고 `run.sh` 대신 `python start.py`를 실행합니다. NVIDIA 환경에서는 해당 CUDA와 호환되는 PyTorch wheel 설치가 필요합니다. 로컬 요구 버전은 `torch 2.10.0` / `torchvision 0.25.0`입니다.

## 구성

```text
frontend/                      React 화면, 상태 관리, Vite, npm lock
backend/app/                   API, OCR 실행, 행사 정보 추출, 검수 규칙
backend/tests/                 API·OCR 장치 선택·RunPod 계약 테스트
backend/Dockerfile.runpod       RunPod Serverless worker 예시
scripts/                       라벨·전처리·OCR 비교·검증 CLI
tests/                         데이터 경로·원본 보존 테스트
data/img/                      기존 포스터 원본
data/labels/gimcheon_gimbap_2026/ 시각 판독 라벨 초안
docs/data-pipeline.md           라벨링·전처리 재현 방법
start.py / run.sh               통합 서버 실행
setup_local.py                 전용 환경 설치와 React 빌드
```

`.runtime/`에는 OCR 모델과 업로드·작업 기록이, `artifacts/`에는 재생성 가능한 전처리·평가 결과가 저장됩니다. 두 폴더와 `.venv-review`, `node_modules`, `dist`는 Git에서 제외합니다. 모델 가중치·가상환경·과거 샘플 ZIP은 커밋하지 않습니다.

샘플 이미지는 기존 `data/img/2026_김천_김밥축제.jpg`를 재사용합니다. 파일명의 NFC/NFD 차이를 처리하며, 확장자와 달리 실제 포맷이 PNG인 원본도 읽습니다. 원본 이미지를 변형하거나 덮어쓰지 않습니다.

## OCR과 검수 정책

- **Apple Vision을 사용하지 않습니다.** EasyOCR 한국어·영어 모델을 사용합니다.
- 로컬과 RunPod Pod에서는 **CUDA → MPS → CPU** 순서로 장치를 선택합니다. 명확한 GPU 오류만 다음 장치로 전환하고, 입력·모델 오류는 실패로 표시합니다.
- 요구사항을 항목별로 편집·선택한 뒤 기준 버전을 고정합니다. 입력이나 기준을 바꾸면 이전 결과를 무효화합니다.
- 충분한 OCR 근거가 있는 문구를 충족으로 표시합니다. OCR 누락을 위반으로 단정하지 않으며 로고·색상·잘림 등은 확인 필요로 남깁니다.
- 판정 결과는 최종 승인을 대신하지 않습니다. OCR 신뢰도는 정답 확률이 아닙니다.

김천 포스터의 장식 글꼴에서 EasyOCR 인식 오류가 확인됐습니다. 기존 샘플의 MPS 실험에서는 원본·RGB 2배·회색조·CLAHE·이진화 모두 실행됐지만, 행사명·시작일·종료일·전체 장소 문구의 완전 일치는 **0/4**였습니다. 라벨 초안은 사람이 확정한 학습 정답이 아닙니다. [데이터 파이프라인 안내](docs/data-pipeline.md)를 참고하세요.

## 개발과 검증

```bash
# API 개발 서버
.venv-review/bin/python -m uvicorn backend.app.api:app --host 127.0.0.1 --port 8000 --reload

# 별도 터미널: React 개발 서버
npm --prefix frontend run dev

# 테스트와 프로덕션 빌드
.venv-review/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run build
```

React 개발 서버의 `/api`는 기본 `http://127.0.0.1:8000`으로 연결됩니다. 다른 포트는 `API_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix frontend run dev`로 지정합니다. [프런트엔드 안내](frontend/README.md)에 상세 명령이 있습니다.

GitHub Actions는 `requirements-test.txt`로 OCR 모델 다운로드와 유료 호출 없이 Python 테스트를 실행하고, React 테스트·빌드를 수행하도록 구성했습니다. 실제 MPS/CUDA 추론과 RunPod 연결은 각 실행 환경에서 따로 확인합니다.

2026-10-12 통합 검증: Python 56개·React 8개 테스트, React 빌드, 프로젝트 경로의 실제 MPS OCR(RGB 2배), 산출물 검사 190개를 통과했습니다. 서버의 HTML·MPS 준비 상태·원본 이미지 바이트와 MIME도 확인했습니다. GitHub 원격 Actions 실행과 CUDA/RunPod 실제 실행은 이 로컬 검증에 포함되지 않습니다.

## RunPod

RunPod Pod에서 앱 전체를 실행할 때는 `RUN_TARGET=local`로 CUDA를 사용합니다. 기존 Serverless endpoint를 연결하려면 서버 환경에서 설정합니다.

```bash
export RUN_TARGET=runpod
export RUNPOD_ENDPOINT_ID='기존 endpoint ID'
export RUNPOD_API_KEY='서버 측 API 키'
./run.sh
```

`.env.example`은 설정 예시이며 자동으로 로드하지 않습니다. API 키를 React 코드나 `VITE_*` 변수에 넣지 마세요. 원격 실행을 선택하면 이미지는 지정 endpoint로 전송됩니다.

```bash
# 저장소 루트를 Docker build context로 사용
docker build -f backend/Dockerfile.runpod -t baro-review-worker .
```

worker 예시는 CUDA 기반 PyTorch 2.6 / torchvision 0.21 이미지를 사용합니다. 로컬 설치와 버전이 다르며, Docker 빌드와 실제 RunPod 배포·과금 호출은 아직 검증하지 않았습니다. Serverless는 전처리된 OCR 입력 파일이 7MiB 이하일 때 지원합니다. 더 큰 입력은 이미지 크기를 줄이거나 로컬/Pod 경로를 사용하세요.

## Git 협업

[CONTRIBUTING.md](CONTRIBUTING.md)의 Issue → 작업 브랜치 → 커밋 → PR → 리뷰 절차를 따릅니다. 이번 로컬 통합은 자동으로 원격 Issue·PR을 만들거나 커밋·푸시하지 않습니다.
