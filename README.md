# 바로검수 · SKN33 Final 1Team

포스터와 요구사항을 입력하고, 검수 기준을 확정한 뒤 OCR 근거를 확인하는 React·Vite + FastAPI 개발 프로젝트입니다. 기존 샘플의 실행 코드를 이 저장소로 통합했습니다.

## 실행

설치가 끝났다면 저장소 루트에서 사용하는 운영체제에 맞는 명령을 실행합니다.

**macOS / Linux · Bash**

```bash
./run.sh
```

**Windows · PowerShell**

```powershell
.\run.cmd
```

[http://127.0.0.1:8000](http://127.0.0.1:8000)을 열고 **샘플로 체험하기 → 검수 항목 확인 → 기준 확정 → 검수 시작** 순서로 진행합니다.

서버가 실행되는 동안 터미널을 유지하세요. 입력 프롬프트가 돌아오지 않는 것이 정상입니다. `Ctrl+C`는 서버 종료이며, 가상환경의 `activate`만 실행해서는 서버가 시작되지 않습니다. `frontend/index.html`을 파일로 열지 마세요.

8000번 포트를 다른 앱이 사용 중이면 다음 명령으로 실행하고 출력된 주소를 엽니다.

```bash
# macOS / Linux
./run.sh --port 8001
```

```powershell
.\run.cmd --port 8001
```

## 새 컴퓨터에서 설치

Python **3.12**, Node.js **22.12 이상**을 사용합니다. Node 20은 20.19 이상도 지원합니다. macOS / Linux에서는 Python 3.12를 먼저 준비하고, Windows에서는 아래 절차로 Python이 없을 때 자동 다운로드합니다.

모든 명령은 저장소 루트에서 실행합니다. [Node.js](https://nodejs.org/en/download)를 설치한 뒤 터미널을 새로 여세요. Windows에서는 `npm.cmd --version`으로 npm이 실행되는지 확인합니다.

**macOS / Linux · Bash**

```bash
python3.12 setup_local.py --download-models
./run.sh
```

**Windows · PowerShell**

사용자별 Python 절대경로나 `py` 실행기를 지정하지 않고 **uv → 프로젝트 `.venv` → 설치·실행** 순서로 진행합니다. 각 블록은 한 줄입니다. 순서대로 실행하고, 오류가 나면 해결한 뒤 다음 단계로 진행하세요.

1. **uv 확인·설치** — 이미 있으면 버전을 표시하고, 없으면 WinGet으로 다운로드·설치합니다.

```powershell
if (Get-Command uv -ErrorAction SilentlyContinue) { uv --version } else { winget install --id astral-sh.uv --exact }
```

uv를 새로 설치했다면 **PowerShell을 닫고 새로 연 뒤 저장소 루트로 돌아오세요.** `uv --version`이 실행되어야 다음 단계로 진행할 수 있습니다. `winget`도 없다면 [uv 공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)에서 Windows 설치 방법을 확인하세요.

2. **Python 3.12와 `.venv` 준비** — `.venv`가 없을 때만 생성합니다. 사용할 수 있는 Python 3.12가 없으면 uv가 다운로드합니다. 인터넷 연결이 필요합니다.

```powershell
if (-not (Test-Path ".\.venv")) { uv venv --python 3.12 --seed .venv }
```

`--seed`는 설치 스크립트가 사용하는 `pip`도 가상환경에 넣습니다. 기존 `.venv`는 이 단계에서 변경하지 않고 다음 단계에서 검증합니다. Python 원본 설치 경로는 uv가 찾으므로 사용자 이름이나 `cpython-3.12.x-...` 경로를 직접 입력하지 않습니다. [Python 자동 다운로드](https://docs.astral.sh/uv/pip/environments/#creating-a-virtual-environment) · [uv의 `--seed` 옵션](https://docs.astral.sh/uv/reference/cli/#uv-venv--seed)

3. **패키지 설치·React 빌드·OCR 모델 다운로드 후 실행** — 프로젝트 상대경로의 Python을 사용하며 설치가 성공한 경우에만 서버를 실행합니다.

```powershell
.\.venv\Scripts\python.exe .\setup_local.py --download-models; if ($? -and ($LASTEXITCODE -eq 0)) { .\run.cmd }
```

프로젝트 가상환경은 모든 운영체제에서 **`.venv`**로 통일합니다. 설치 스크립트는 `.venv` 생성, Python 패키지 설치, `npm ci`, React 빌드를 수행합니다. 기존 `.venv`가 Python 3.12이면 재사용하며 요구 패키지를 설치합니다. Python 버전이 다르거나 다른 운영체제에서 만든 환경이면 자동으로 덮어쓰지 않고 중단합니다. 이 경우 기존 환경을 다른 이름으로 보관한 뒤 다시 설치하세요. 가상환경은 경로를 포함하므로 다른 이름의 환경 폴더를 `.venv`로 이름만 바꿔 재사용하지 마세요.

`--download-models`를 지정한 경우에만 OCR 모델을 다운로드합니다. `python start.py`도 프로젝트의 `.venv` Python을 선택하므로, 다른 가상환경이 활성화되어 있어도 서버는 프로젝트 환경에서 실행합니다.

위 PowerShell 명령은 설치가 성공한 경우에만 서버를 실행합니다. 코드 블록은 실제 줄바꿈 없는 한 줄이며, 화면에서 길게 접혀 보여도 블록 전체를 복사하면 됩니다. 터미널의 `PS C:\...>` 프롬프트는 붙이지 마세요. 설치가 오류로 끝나면 해당 오류를 해결한 뒤 다시 실행하세요. Windows에서도 가상환경의 Python을 직접 호출하므로 `Activate.ps1` 실행은 필요 없습니다. PowerShell 예시는 `npm.ps1` 대신 `npm.cmd`를 사용하며 실행 정책을 변경할 필요가 없습니다. 실행·설치 스크립트는 운영체제에 맞는 명령을 안내합니다.

<details>
<summary>수동 설치 명령 보기</summary>

자동 설치 대신 직접 설치할 때만 다음 명령을 순서대로 사용합니다.

**macOS / Linux · Bash**

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
npm --prefix frontend ci
npm --prefix frontend run build
.venv/bin/python -m backend.app.ocr_runtime --download-models
.venv/bin/python start.py
```

**Windows · PowerShell**

위 Windows 설치 안내의 uv 준비를 먼저 완료하세요. 각 블록이 한 줄 명령입니다. 순서대로 실행하고, 오류가 나면 해결한 뒤 다음 단계로 진행하세요. 기존 `.venv`는 현재 Windows에서 만든 Python 3.12 환경일 때만 재사용합니다.

1. 가상환경 생성

```powershell
if (-not (Test-Path ".\.venv")) { uv venv --python 3.12 --seed .venv }
```

2. Python 패키지 설치

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

3. 프런트엔드 패키지 설치

```powershell
npm.cmd --prefix frontend ci
```

4. 프런트엔드 빌드

```powershell
npm.cmd --prefix frontend run build
```

5. OCR 모델 다운로드

```powershell
.\.venv\Scripts\python.exe -m backend.app.ocr_runtime --download-models
```

6. 서버 실행

```powershell
.\run.cmd
```

</details>

NVIDIA 환경에서는 해당 CUDA와 호환되는 PyTorch wheel 설치가 필요합니다. 로컬 요구 버전은 `torch 2.10.0` / `torchvision 0.25.0`입니다.

### Windows PowerShell에서 실행이 안 될 때

**`CommandNotFoundException`과 Python 절대경로가 함께 나오는 경우**는 PowerShell이 지정된 위치의 실행 파일을 찾거나 실행하지 못한 것입니다. `setup_local.py`가 실행되기 전의 오류입니다. 다른 PC의 사용자 이름, uv 저장 위치 또는 Python 세부 버전을 포함한 경로를 복사했을 수 있습니다. `&`와 따옴표로 감싼 절대경로 자체는 올바른 PowerShell 문법이지만, 존재하지 않는 파일을 실행해 주지는 않습니다.

절대경로를 상대경로로 바꾸는 것만으로 Python이 설치되지는 않습니다. **위 Windows 설치의 1~3단계**를 따라 uv를 준비하고, 필요한 Python을 다운로드해 `.venv`를 만든 뒤 `.\.venv\Scripts\python.exe`로 실행하세요. Python 3.12를 찾지 못하는 `py -3.12`의 `No suitable Python runtime found` 오류도 같은 절차로 해결할 수 있습니다. 이 절차는 `py` 실행기에 의존하지 않습니다.

먼저 저장소 루트에서 프로젝트 Python이 있는지 확인합니다. `run.cmd`는 다른 환경이 활성화되어 있어도 이 실행 파일을 선택합니다.

```powershell
Test-Path ".\.venv\Scripts\python.exe"
```

`True`이고 설치를 이미 완료했다면 아래 명령을 그대로 실행합니다. `True`는 파일이 있다는 뜻이며 Python 버전이나 패키지 설치까지 보장하지는 않습니다.

```powershell
.\run.cmd
```

`False`라면 위 Windows 설치의 1~3단계로 돌아갑니다. `.venv` 폴더는 있지만 Python 파일이 없거나 실행되지 않는다면 다른 운영체제에서 만든 환경이거나 설치가 불완전할 수 있습니다. 기존 폴더를 다른 이름으로 보관한 뒤 2단계부터 다시 생성하세요.

Python은 실행되지만 의존성 오류가 나면 다음 명령으로 설치를 다시 완료합니다. Node.js와 npm도 실행 가능해야 합니다.

```powershell
.\.venv\Scripts\python.exe .\setup_local.py --download-models
```

설치가 성공한 뒤 `.\run.cmd`를 다시 실행하세요. 경로를 직접 지정하려면 다음 명령도 사용할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe .\start.py
```

경로의 앞부분은 `..venv`가 아니라 `.\.venv`입니다. `.\`는 현재 폴더이고 `.venv`는 가상환경 폴더 이름입니다. `(django_env)` 등 다른 환경에 서버 패키지를 따로 설치할 필요는 없습니다. 오류 안내에는 실제 사용한 Python 경로와 실패 원인이 표시됩니다.

`No module named pip`이면 기존 `.venv`가 `--seed` 없이 만들어졌을 수 있습니다. 정상적으로 실행되는 Python 3.12 가상환경인지 확인한 뒤 아래 명령으로 해당 환경에만 pip를 설치하고 설치 명령을 다시 실행하세요.

```powershell
uv pip install --python .\.venv\Scripts\python.exe pip
```

프롬프트가 `>>`로 바뀌면 아직 명령을 입력받는 중입니다. `Ctrl+C`로 취소하고, 닫히지 않은 따옴표·괄호나 줄바꿈이 들어가지 않도록 위 한 줄 블록을 다시 붙여넣으세요. `PS ...>`나 `>>` 자체는 명령에 포함하지 않습니다.

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
start.py / run.sh / run.cmd     통합 서버 실행 (macOS·Linux / Windows)
setup_local.py                 전용 환경 설치와 React 빌드
```

`.runtime/`에는 OCR 모델과 업로드·작업 기록이, `artifacts/`에는 재생성 가능한 전처리·평가 결과가 저장됩니다. 두 폴더와 `.venv`, `node_modules`, `dist`는 Git에서 제외합니다. 모델 가중치·가상환경·과거 샘플 ZIP은 커밋하지 않습니다. `frontend/package-lock.json`은 Git에 포함하고, `frontend/node_modules`와 `frontend/dist`는 설치·빌드로 다시 생성합니다.

샘플 이미지는 기존 `data/img/2026_김천_김밥축제.jpg`를 재사용합니다. 파일명의 NFC/NFD 차이를 처리하며, 확장자와 달리 실제 포맷이 PNG인 원본도 읽습니다. 원본 이미지를 변형하거나 덮어쓰지 않습니다.

## OCR과 검수 정책

- **Apple Vision을 사용하지 않습니다.** EasyOCR 한국어·영어 모델을 사용합니다.
- 로컬과 RunPod Pod에서는 **CUDA → MPS → CPU** 순서로 장치를 선택합니다. 명확한 GPU 오류만 다음 장치로 전환하고, 입력·모델 오류는 실패로 표시합니다.
- 요구사항을 항목별로 편집·선택한 뒤 기준 버전을 고정합니다. 입력이나 기준을 바꾸면 이전 결과를 무효화합니다.
- 충분한 OCR 근거가 있는 문구를 충족으로 표시합니다. OCR 누락을 위반으로 단정하지 않으며 로고·색상·잘림 등은 확인 필요로 남깁니다.
- 판정 결과는 최종 승인을 대신하지 않습니다. OCR 신뢰도는 정답 확률이 아닙니다.

김천 포스터의 장식 글꼴에서 EasyOCR 인식 오류가 확인됐습니다. 기존 샘플의 MPS 실험에서는 원본·RGB 2배·회색조·CLAHE·이진화 모두 실행됐지만, 행사명·시작일·종료일·전체 장소 문구의 완전 일치는 **0/4**였습니다. 라벨 초안은 사람이 확정한 학습 정답이 아닙니다. [데이터 파이프라인 안내](docs/data-pipeline.md)를 참고하세요.

## 개발과 검증

아래 명령도 모두 저장소 루트에서 실행합니다. API와 화면 개발 서버는 각각 별도 터미널을 사용합니다.

### 화면 개발

**터미널 1 — API 개발 서버**

macOS / Linux · Bash:

```bash
.venv/bin/python -m uvicorn backend.app.api:app --host 127.0.0.1 --port 8000 --reload
```

Windows · PowerShell:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.app.api:app --host 127.0.0.1 --port 8000 --reload
```

**터미널 2 — React·Vite 개발 서버**

macOS / Linux · Bash:

```bash
npm --prefix frontend run dev
```

Windows · PowerShell:

```powershell
npm.cmd --prefix frontend run dev
```

Vite가 출력한 주소(기본 [http://127.0.0.1:5173](http://127.0.0.1:5173))를 엽니다. 화면의 `/api` 상대 경로 요청은 개발 서버가 기본 `http://127.0.0.1:8000`으로 전달합니다.

### API 포트 변경

API 서버를 `--port 8001`로 실행했다면 Vite 실행 시 대상 주소도 맞춥니다.

macOS / Linux · Bash:

```bash
API_PROXY_TARGET=http://127.0.0.1:8001 npm --prefix frontend run dev
```

Windows · PowerShell:

```powershell
$env:API_PROXY_TARGET = "http://127.0.0.1:8001"; npm.cmd --prefix frontend run dev
```

PowerShell에서 `Ctrl+C`로 개발 서버를 종료한 뒤 기본 포트로 되돌릴 때:

```powershell
Remove-Item Env:API_PROXY_TARGET -ErrorAction SilentlyContinue
```

PowerShell의 `$env:` 설정은 현재 터미널과 그 터미널에서 실행한 프로세스에 적용됩니다. 환경 변수를 바꾼 뒤에는 Vite를 다시 실행하세요.

`API_PROXY_TARGET`은 Vite 개발·미리보기 프록시에서만 사용합니다. 빌드한 화면은 FastAPI와 같은 출처에서 제공하며, 프록시 주소나 RunPod 비밀키를 브라우저 번들에 넣지 않습니다.

### 테스트와 빌드

macOS / Linux · Bash:

```bash
.venv/bin/python -m pytest
npm --prefix frontend test
npm --prefix frontend run build
```

Windows · PowerShell — Python 테스트:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Windows · PowerShell — React 테스트 성공 시에만 프로덕션 빌드:

```powershell
npm.cmd --prefix frontend test; if ($? -and ($LASTEXITCODE -eq 0)) { npm.cmd --prefix frontend run build }
```

프런트엔드 상태 테스트는 기준 확정·잠금, 변경 후 재확정, 이전 결과 무효화, 늦은 응답 차단, 원본 이미지 좌표 표시를 확인합니다. 전체 업로드·OCR 흐름은 API를 실행한 상태에서 확인합니다. 빌드 후 통합 화면은 위 [실행](#실행) 명령으로 확인합니다.

### 자동 검증과 확인 범위

GitHub Actions는 Ubuntu와 Windows에서 `requirements-test.txt`로 OCR 모델 다운로드와 유료 호출 없이 Python 테스트를 실행하고, React 테스트·빌드를 수행하도록 구성했습니다. Windows 작업에는 `.venv` 생성, `run.cmd` 실행 확인, 실제 서버의 `/api/health`·React 화면 응답 확인이 포함됩니다. 워크플로 추가 자체가 Windows 검증 통과를 뜻하지는 않으며, PR 또는 main 푸시 후 해당 실행 결과를 확인해야 합니다. 실제 MPS/CUDA 추론과 RunPod 연결은 각 실행 환경에서 따로 확인합니다.

2026-10-12 통합 검증: Python 56개·React 8개 테스트, React 빌드, 프로젝트 경로의 실제 MPS OCR(RGB 2배), 산출물 검사 190개를 통과했습니다. 서버의 HTML·MPS 준비 상태·원본 이미지 바이트와 MIME도 확인했습니다. GitHub 원격 Actions 실행과 CUDA/RunPod 실제 실행은 이 로컬 검증에 포함되지 않습니다.

## RunPod

RunPod Pod에서 앱 전체를 실행할 때는 `RUN_TARGET=local`로 CUDA를 사용합니다. 기존 Serverless endpoint를 연결하려면 서버 환경에서 설정합니다.

**macOS / Linux · Bash**

```bash
export RUN_TARGET=runpod
export RUNPOD_ENDPOINT_ID='기존 endpoint ID'
export RUNPOD_API_KEY='서버 측 API 키'
./run.sh
```

**Windows · PowerShell**

```powershell
$env:RUN_TARGET = "runpod"; $env:RUNPOD_ENDPOINT_ID = "기존 endpoint ID"; $env:RUNPOD_API_KEY = "서버 측 API 키"; .\run.cmd
```

로컬 실행으로 돌아가려면 서버를 종료한 뒤 Bash에서는 `export RUN_TARGET=local`, PowerShell에서는 `$env:RUN_TARGET = "local"`로 설정하고 서버를 다시 실행합니다.

`.env.example`은 설정 예시이며 자동으로 로드하지 않습니다. API 키를 React 코드나 `VITE_*` 변수에 넣지 마세요. 원격 실행을 선택하면 이미지는 지정 endpoint로 전송됩니다.

다음 Docker 빌드 명령은 Bash와 PowerShell에서 같습니다. Docker가 설치·실행된 환경에서 사용합니다.

```text
# 저장소 루트를 Docker build context로 사용
docker build -f backend/Dockerfile.runpod -t baro-review-worker .
```

worker 예시는 CUDA 기반 PyTorch 2.6 / torchvision 0.21 이미지를 사용합니다. 로컬 설치와 버전이 다르며, Docker 빌드와 실제 RunPod 배포·과금 호출은 아직 검증하지 않았습니다. Serverless는 전처리된 OCR 입력 파일이 7MiB 이하일 때 지원합니다. 더 큰 입력은 이미지 크기를 줄이거나 로컬/Pod 경로를 사용하세요.

## Git 협업

[CONTRIBUTING.md](CONTRIBUTING.md)의 Issue → 작업 브랜치 → 커밋 → PR → 리뷰 절차를 따릅니다. 이번 로컬 통합은 자동으로 원격 Issue·PR을 만들거나 커밋·푸시하지 않습니다.
