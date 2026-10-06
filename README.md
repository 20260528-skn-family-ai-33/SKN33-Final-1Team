# 동작그만 밑장빼기냐

광고 납품 검수 업무자동화 프로젝트의 첫 실행 버전입니다. **로컬 단일 사용자용**이며, 현재는 조건 확정 → 이미지 접수 → 문자 검사 → 근거·결과·이력 조회를 구현합니다.

현재 실행 구조는 **Django 5.2.17 + FastAPI**입니다. Django가 화면·ORM·마이그레이션·관리자 기능을 맡고, FastAPI가 기존 검수 API와 OCR·MCP 호출을 담당합니다. 한 ASGI 서버에서 같은 주소로 실행합니다.

## 실행

### 공통 준비

Python 3.12 환경과 `uv`를 사용합니다. 이번 설치 안내의 기준은 **Windows x64 또는 macOS 13 이상 Apple Silicon**이며, 기본 CPU 방식에는 CUDA나 별도 GPU가 필요하지 않습니다. 아래 명령은 모두 **프로젝트 폴더(`manage.py`와 `requirements.lock.txt`가 있는 위치)** 에서 실행하세요. `.venv`는 운영체제마다 새로 만들며 다른 팀원의 폴더를 복사해서 사용하지 않습니다. 가상환경의 Python을 직접 호출하므로 별도의 활성화 명령은 필요하지 않습니다.

Intel Mac에서는 현재 고정 의존성 중 `opencv-python==5.0.0.93`이 macOS 14 이상 wheel을 제공하고, `cryptography==50.0.2`는 Intel Mac wheel이 없어 별도 소스 빌드 환경과 추가 검증이 필요합니다. 따라서 이번 기본 설치 확인 범위에 Intel Mac은 포함하지 않았습니다. [OpenCV 배포 파일](https://pypi.org/project/opencv-python/5.0.0.93/#files), [cryptography 배포 파일](https://pypi.org/project/cryptography/50.0.2/#files)

`uv`가 없으면 [공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)에 따라 설치하세요. Homebrew가 설치된 Mac에서는 `brew install uv`, Windows에서는 `winget install --id=astral-sh.uv -e`를 사용할 수 있습니다. 설치 후 새 터미널에서 `uv --version`으로 확인하세요. 아래 `uv venv --python 3.12` 명령은 Python 3.12가 없으면 내려받아 환경을 만듭니다. [가상환경 안내](https://docs.astral.sh/uv/pip/environments/)

| 기능 | macOS | Windows |
| --- | --- | --- |
| Django 화면·FastAPI·이미지 접수·저장·이력 조회 | 실행 확인 완료 | 아래 PowerShell 실행 절차 제공, 실제 Windows 환경 검증 필요 |
| 실제 문자 검사 | Apple Silicon에서 공통 엔진의 실제 OCR·MCP 검증 완료 | 같은 공통 엔진으로 실행하도록 구현, 실제 Windows 검증 필요 |
| 딸기 개수·음료 외관 검사 | 자동 검사 미구현 | 자동 검사 미구현 |

기존 Apple Vision OCR은 Windows에서 실행할 수 없습니다. 기본 OCR은 **RapidOCR 3.9.0**, CPU는 **ONNX Runtime 1.23.2**, GPU는 **PyTorch 2.10.0**을 사용합니다. 장치를 자동 선택하고 GPU 준비·실행에 실패하면 CPU로 전환합니다. CPU도 실행에 실패하면 검사 실패로 기록합니다. Windows·CUDA·Intel Mac 실기 검증은 아직 완료하지 않았습니다.

| 환경 | 기본 장치 선택 (`auto`) |
| --- | --- |
| Windows·Linux에서 실행 가능한 NVIDIA CUDA 환경 | CUDA |
| Windows·Linux에서 CUDA를 사용할 수 없음 | CPU |
| Intel Mac | CPU |
| Apple Silicon Mac에서 MPS를 사용할 수 있음 | MPS |
| Apple Silicon Mac에서 MPS를 사용할 수 없음 | CPU |

GPU 탑재 여부만으로 선택하지 않고 `torch.cuda.is_available()` 또는 `torch.backends.mps.is_available()`을 확인합니다. GPU 모델 누락·초기화 실패·메모리 부족·추론 오류도 CPU 전환 대상으로 처리합니다. 결과의 검사 방법과 실행 기록에는 **실제로 사용한 장치와 전환 사유**를 남깁니다. `/api/health`는 준비 상태와 예정 장치를 보여주며 실제 추론 성공을 보장하지 않습니다.

### macOS · 터미널(zsh/bash)

기본 RapidOCR에는 Xcode·Swift 컴파일러가 필요하지 않습니다. 기존 Apple Vision은 아래의 선택 설정으로 사용할 수 있습니다.

**최초 설치**

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r requirements.lock.txt
.venv/bin/python scripts/prepare_ocr.py
```

**서버 실행 · 재실행**

```sh
./scripts/run.sh
```

실행 권한 오류가 나면 `sh scripts/run.sh`로 실행할 수 있습니다. 포트를 바꾸려면 다음 명령을 사용합니다.

```sh
CHECKER_PORT=8001 sh scripts/run.sh
```

### Windows · PowerShell

64비트 Python 환경을 사용합니다. ONNX Runtime의 Windows 실행에는 Visual C++ 런타임이 필요합니다. 설치되지 않았거나 `DLL load failed`가 발생하면 [ONNX Runtime 공식 설치 안내](https://onnxruntime.ai/docs/install/)의 Visual C++ 런타임 링크에서 최신 x64 패키지를 설치하세요.

**최초 설치**

```powershell
uv venv --python 3.12 .venv
uv pip install --python .\.venv\Scripts\python.exe -r requirements.lock.txt
.\.venv\Scripts\python.exe scripts\prepare_ocr.py
```

**서버 실행 · 재실행**

PowerShell에서는 `.sh` 스크립트 대신 데이터베이스 준비와 서버 실행 명령을 직접 사용합니다. 아래 블록은 데이터베이스 준비가 성공했을 때 서버를 실행합니다.

```powershell
.\.venv\Scripts\python.exe manage.py prepare_local_db
if ($LASTEXITCODE -eq 0) {
    .\.venv\Scripts\python.exe -m uvicorn config.asgi:application --host 127.0.0.1 --port 8000
}
```

다른 포트를 사용하려면 위 명령의 `--port 8000`을 `--port 8001`로 변경하세요. `Activate.ps1`을 실행하지 않으므로 가상환경 활성화를 위한 실행 정책 변경은 필요하지 않습니다.

### 접속과 종료 · 공통

- 기본 화면: <http://127.0.0.1:8000/>
- API 문서: <http://127.0.0.1:8000/docs>
- 관리자: <http://127.0.0.1:8000/admin/>
- 서버 종료: 서버를 실행한 터미널에서 `Ctrl+C`

포트를 8001로 바꿨다면 접속 주소도 `http://127.0.0.1:8001/`로 바꿉니다. 포트만 변경하면 저장 데이터는 공유합니다. 테스트 데이터를 분리하려면 실행 전에 `LATTE_RUNTIME_DIR`를 별도로 지정하세요. 기존 데이터베이스를 처음 이전할 때는 다른 서버를 종료하고 한 번만 실행합니다.

두 운영체제 모두 데이터베이스를 준비한 뒤 `config.asgi:application`을 Uvicorn으로 실행합니다. Django의 `runserver` 대신 위 명령을 사용해야 FastAPI 검수 API도 함께 제공됩니다.

1. **딸기라떼 / 페스티벌 / 크루아상 v2** 버튼으로 주제를 선택합니다.
2. 샘플 또는 등록된 발주 조건을 선택하고, 다섯 기준을 확인·확정합니다. 크루아상 샘플은 해당 발주의 기대 수량을 함께 불러오며 직접 업로드할 때도 발주를 먼저 선택합니다.
3. **검수 실행**을 누르고 실제 OCR 관찰·근거·항목별 상태를 확인합니다.
4. 실행 이력에서 이전 파일·기준 버전의 결과를 다시 엽니다.

### OCR 모델 준비와 선택

`scripts/prepare_ocr.py`는 최초 한 번 실행합니다. PP-OCRv5 mobile 문자 검출·한국어 문자 인식 모델과 PP-OCRv4 방향 분류 모델을 내려받아 **SHA-256 해시를 확인**합니다. 한국어 인식 모델로 한글과 영문·숫자를 함께 읽습니다. 기본 저장 위치는 `.runtime/ocr-models/`이며 Git에 포함하지 않습니다.

자동 선택 장치가 CUDA·MPS면 GPU용 모델과 사전도 준비합니다. CPU 전환에 필요한 ONNX 모델 3개는 항상 준비합니다. GPU 경로에는 **PP-OCRv5 검출 + PP-OCRv4 한국어·영어 인식**을 사용합니다. 공식 Torch 릴리스에 한국어 v5 인식 모델이 없어 CPU와 GPU의 인식 모델은 다릅니다. GPU에서는 한국어 모델로 먼저 읽고, 관찰 결과가 영문 ASCII로만 구성된 문구는 영문 전용 모델로 다시 읽습니다. 기준 문구나 정답을 인식에 주입하지 않습니다. 모델 파일 4개·사전 2개를 GPU용으로 추가 준비하며 사전도 고정 해시로 검증합니다. [공식 모델 목록](https://github.com/RapidAI/RapidOCR/blob/v3.9.0/python/rapidocr/default_models.yaml), [RapidOCR MPS 사용 안내](https://github.com/RapidAI/RapidOCRDocs/blob/main/docs/install_usage/rapidocr/how_to_use_infer_engine.md)

```sh
# GPU 장비로 옮기기 전에 모든 모델을 미리 준비할 경우
.venv/bin/python scripts/prepare_ocr.py --profile all
```

Windows에서는 같은 옵션을 `.\.venv\Scripts\python.exe scripts\prepare_ocr.py --profile all` 명령에 적용합니다. `--profile onnx`는 CPU 모델만 준비합니다.

NVIDIA GPU를 쓰는 팀원은 [PyTorch 공식 설치 선택기](https://pytorch.org/get-started/locally/)에서 운영체제·드라이버에 맞는 **CUDA 빌드**를 선택해야 합니다. CPU 전용 PyTorch 설치나 호환되지 않는 드라이버에서는 자동으로 CPU를 사용합니다. `onnxruntime-gpu`는 필요하지 않습니다. CUDA 검출용 명령은 `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"`이며 가상환경의 Python으로 실행하세요. Mac M 시리즈는 requirements 설치의 PyTorch로 MPS를 사용합니다. Intel Mac은 Torch 의존성을 설치하지 않고 ONNX CPU를 사용하며 위의 소스 빌드 제약을 따릅니다.

입력 이미지는 글자가 바르게 선 방향을 기준으로 합니다. 한국어 할인 문구를 잘못 뒤집는 사례가 확인되어 기본 자동 180도 방향 보정은 껐습니다. 방향 분류 모델 파일은 RapidOCR 초기화에 필요해 함께 준비하지만 기본 추론에는 적용하지 않습니다. 서로 가까운 같은 줄의 문구는 좌표를 기준으로 합치고, 숫자·%·할인 형식으로 읽히지 않은 할인 문구는 확정 오류 대신 확인 필요로 남깁니다.

EXIF 회전·반전 정보가 있는 이미지는 브라우저 표시 방향과 OCR 근거 좌표가 어긋나지 않도록 검사를 중단합니다. 이미지 편집기에서 방향을 적용한 PNG로 다시 저장해 제출하세요. 자동 EXIF 정규화는 아직 구현하지 않았습니다.

모델 준비 단계에는 인터넷이 필요합니다. 이후 검사에서는 준비된 로컬 파일만 읽으며 **검수 이미지나 문서를 외부로 전송하지 않습니다**. 누락되거나 손상된 모델은 검사 중 자동으로 내려받지 않습니다. 서버를 다시 시작하기 전에 모델 준비 명령을 실행하세요. `/api/health`에서 선택한 OCR과 모델 준비 상태를 확인할 수 있습니다.

| 환경변수 | 기본값 | 용도 |
| --- | --- | --- |
| `LATTE_OCR_BACKEND` | `rapidocr` | 공통 OCR. Mac에서 `apple`을 지정하면 기존 Apple Vision 사용 |
| `LATTE_OCR_DEVICE` | `auto` | `auto`, `cpu`, `cuda`, `mps`. 명시한 GPU를 사용할 수 없어도 CPU 전환 사유를 기록 |
| `LATTE_OCR_MODEL_DIR` | 프로젝트의 `.runtime/ocr-models` | 모델 준비 스크립트와 OCR이 함께 읽는 모델 폴더 |

모델 폴더를 변경할 때는 모델 준비와 서버 실행 **양쪽에 같은 환경변수**를 적용합니다. 예를 들어 macOS에서는 `export LATTE_OCR_MODEL_DIR="/원하는/모델/경로"`, PowerShell에서는 `$env:LATTE_OCR_MODEL_DIR = "C:\원하는\모델\경로"`를 먼저 실행합니다.

CPU로 비교하려면 macOS/Linux는 `export LATTE_OCR_DEVICE=cpu`, Windows PowerShell은 `$env:LATTE_OCR_DEVICE = "cpu"`를 실행하고 서버를 재시작합니다. 자동 선택으로 돌아가려면 값을 `auto`로 변경합니다. 모델 파일을 추가하거나 PyTorch를 변경한 뒤에도 서버를 재시작하세요.

Mac에서 Apple Vision으로 비교하려면 Xcode Command Line Tools를 설치하고 `swiftc --version`을 확인한 뒤 다음 명령으로 서버를 실행하세요. 이 선택만 Mac 전용입니다.

```sh
LATTE_OCR_BACKEND=apple sh scripts/run.sh
```

기본 RapidOCR로 돌아가려면 해당 환경변수를 제거하거나 `rapidocr`로 지정하고 서버를 재시작합니다. 엔진이 바뀌면 인식 결과도 달라질 수 있으므로 이전 검수 이력과 새 실행 결과를 구분해 확인하세요.

## Django와 FastAPI의 역할

| 구성 | 역할 | 주요 파일 |
| --- | --- | --- |
| Django | 설정·메인 화면·관리자 페이지 | `config/settings.py`, `config/urls.py`, `inspections/views.py` |
| Django 템플릿 | 기존 검수 화면과 CSRF 토큰 제공 | `inspections/templates/inspections/index.html` |
| Django ORM | 기준 확정·접수 이미지·검수 이력 저장 | `inspections/models.py`, `app/store.py` |
| FastAPI | `/api/criteria`, `/api/assets`, `/api/runs` 등 기존 검수 API | `app/main.py` |
| ASGI 진입점 | 두 앱을 한 포트에서 실행 | `config/asgi.py` |
| 정적 파일 | 화면 JavaScript·CSS 및 Django 관리자 리소스 제공 | `web/`, ASGI StaticFiles |
| MCP·OCR | 별도 도구 프로세스로 실제 문자 검사 수행 | `app/mcp_client.py`, `app/mcp_server.py`, `app/inspector.py`, `app/ocr.py` |

관리자 계정이 필요하면 데이터베이스 준비 후 아래 명령으로 직접 생성한 뒤 `/admin/`에 접속합니다.

| 운영체제 | 관리자 계정 생성 |
| --- | --- |
| macOS | `.venv/bin/python manage.py createsuperuser` |
| Windows PowerShell | `.\.venv\Scripts\python.exe manage.py createsuperuser` |

기본 관리자 계정은 만들지 않습니다. 검수 기록은 관리자에서 **조회 전용**이며 변경은 검수 API 흐름으로 처리합니다. 관리자 로그인은 검수 API의 다중 사용자 인증을 의미하지 않습니다.

API의 변경 요청에는 Django CSRF 쿠키와 `X-CSRFToken` 헤더가 필요합니다. 화면과 샘플 검증 스크립트는 이를 자동 처리합니다. 별도 클라이언트는 먼저 `GET /api/csrf`를 호출하고, 받은 쿠키를 유지한 채 응답의 `csrf_token`을 POST 요청 헤더에 넣습니다. Swagger `/docs`에서도 같은 브라우저로 토큰을 발급받은 뒤 **Authorize → DjangoCSRF**에 넣어 사용할 수 있습니다.

## 현재 검사 범위

- 딸기라떼 상품명·할인율·브랜드, 크루아상 상품명·가격·브랜드, 페스티벌 행사명·날짜·시간·장소·가격: 기본 RapidOCR로 실제 판독한 원문을 조건과 비교합니다. Mac에서는 Apple Vision을 선택할 수도 있습니다.
- 딸기·크루아상 개수, 빵 크기 구분, 음료·접시 외관: 자동 검사기를 연결하지 않아 **확인 필요 / 미실행**으로 표시합니다.
- 새 주제의 OCR 영역은 샘플 포스터 배치에 맞춘 설정입니다. 임의 레이아웃의 광고에는 영역을 새로 정의하거나 문서·이미지 이해 기능을 추가해야 합니다. 가격은 천 단위 쉼표, 날짜는 구분자, 시간은 대시 차이를 허용하며 숫자와 읽은 원문은 보존합니다. OCR 오독은 실제 내용 오류와 다를 수 있으므로 근거를 확인하세요.
- OCR 실패는 실제 실행 실패로 표시합니다. 보이지 않는 텍스트를 임의로 정답으로 채우지 않습니다.
- API 키 없이 실행하며 이미지를 외부 모델에 전송하지 않습니다.
- 실제 MCP 프로세스와의 도구 조회·호출을 추적합니다. 현재 SDK에서 협상된 프로토콜은 2025-11-25입니다. 설계한 8개 에이전트·RAG·의도 분류 학습·보완/최종 승인 흐름은 후속 구현 범위입니다.

## 데이터와 한계

현재 샘플은 **3주제 × 6장 = 18장**입니다. 모두 AI로 제작한 가상 광고이며, 개발·시연용 변형 자료입니다. 일반화 성능 평가용 독립 18건이 아닙니다. 실제 규격은 1254×1254 PNG이며 기존 기획의 1080×1080 규격 검사와 별개입니다.

| 주제 | 검사 기준 파일 | 이미지 폴더 |
| --- | --- | --- |
| 딸기라떼 | `data/criteria.json` | `data/samples/` |
| 페스티벌 v1 | `data/topics/festival-v1/criteria.json` | `data/topics/festival-v1/images/` |
| 크루아상 v2 | `data/topics/croissant-v2/criteria.json` | `data/topics/croissant-v2/images/` |

크루아상 C04는 `sample_conditions`의 **사전에 정한 기대 수량**을 적용합니다. 이미지에서 수량을 읽어 검사 기준을 만들지 않습니다. 샘플 선택은 해당 발주 ID를 연결하며, 직접 올리는 파일은 선택한 발주 ID를 사용합니다. 업로드 파일명을 발주나 정답으로 해석하지 않습니다. 주제·발주별로 기준을 확정하고 파일 접수에 연결하며, 서로 다른 주제·발주 버전의 실행은 거부합니다. 이력에는 실행 당시 기준·기대 수량을 보존합니다.

`GET /api/topics`로 주제 목록을 읽고 `/api/criteria`, `/api/samples`의 `topic_id`로 선택합니다. 크루아상 기준 조회·확정은 `condition_id`도 지정합니다. 기존 API 요청에서 주제를 생략하면 딸기라떼로 동작합니다.

평가 정답·생성 프롬프트를 실행 데이터 폴더에 복사하지 않았습니다. 파일명이나 정답키로 검사 결과를 선택하지 않습니다. SQLite와 접수 파일은 `data/runtime/`에 저장되며 Git에서 제외합니다. OCR 모델과 빌드 캐시도 제외합니다. 해당 런타임 폴더를 지우면 접수·확정·실행 이력이 사라집니다.

기존 SQLite 파일이 있으면 최초 실행 시 `data/runtime/backups/latte-before-django-*.sqlite3`로 백업하고 기존 테이블을 Django 마이그레이션에 연결합니다. 이미지 파일·ID·요청 키·JSON 결과와 근거는 보존합니다. 시간순 정렬을 위해 DB 날짜 열의 저장 형식만 Django 형식으로 정규화하며 JSON 안의 원래 시간은 유지합니다. 예상과 다른 테이블 구조는 자동 이전하지 않습니다. 이전 중에는 기존 서버를 먼저 종료하고 새 서버 한 개만 실행하세요.

`LATTE_RUNTIME_DIR`로 별도 런타임 디렉터리를 지정할 수 있습니다. Django 세션용 로컬 비밀 키도 해당 폴더에 저장하며, `DJANGO_SECRET_KEY` 환경변수로 대체할 수 있습니다. MySQL은 기존 설계의 후속 전환 대상이고 이번 변경의 데이터베이스는 SQLite입니다.

## 테스트

서버를 실행한 뒤 전체 18장에 실제 OCR·MCP 검사를 수행할 수 있습니다. 개발 DB에 접수·확정·이력을 기록하며 정답키를 읽지 않습니다. 결과는 `.runtime/all-topic-execution-report.json`에 저장합니다. 기본 포트가 8001이면 Mac에서는 앞에 `CHECKER_PORT=8001`, PowerShell에서는 `$env:CHECKER_PORT='8001'`을 지정하세요.

```sh
# macOS
.venv/bin/python scripts/verify_samples.py --all-topics
```

```powershell
# Windows
.\.venv\Scripts\python.exe scripts\verify_samples.py --all-topics
```

`--topic festival-v1` 또는 `--topic croissant-v2`로 한 주제만 실행할 수도 있습니다. 인자 생략 시 기존 딸기라떼 6장을 실행합니다.

macOS:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

자동 테스트는 API 계약·검사 로직·OCR 연결의 예외 처리를 검증합니다. 실제 모델과 MCP까지 확인하려면 모델 준비 후 다음 명령도 실행하세요. 별도 서버 실행 없이 개발 샘플 2장으로 공통 OCR을 점검하며 결과는 `.runtime/ocr-smoke-report.json`에 저장합니다. 이 결과는 개발용 샘플 검증이며 범용 OCR 정확도 평가가 아닙니다.

macOS:

```sh
.venv/bin/python scripts/ocr_smoke.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts\ocr_smoke.py
```

Windows 설치·실행은 아직 실제 Windows 환경에서 검증하지 않았습니다. `.github/workflows/ocr-compatibility.yml`에는 Windows·macOS에서 설치 → 모델 준비 → 자동 테스트 → Django 검사 → 실제 OCR·MCP 검사를 수행하는 **수동 실행** 워크플로를 제공합니다. 저장소에 반영한 뒤 GitHub Actions의 **OCR compatibility → Run workflow**로 실행하고, `ocr-smoke-*` 결과물을 확인할 수 있습니다. 워크플로 파일을 추가한 사실과 실제 실행 통과는 구분합니다.

HTTP 인터페이스: 실행 중 <http://127.0.0.1:8000/docs>에서 확인할 수 있습니다.

## 설계 자료

- [스탠드업 미팅용 간단 정리 · 2026-10-06 기준](docs/planning/organized/스탠드업미팅.html)

- [종류별 문서 모음 · 기획서 / 기능정의서 / 화면정의서 / 기술스펙정의서](docs/planning/organized/index.html)
  - [기획서 · 배경·범위·일정·산출물](docs/planning/organized/기획서.html)
  - [기능정의서 · 33개 기능과 69개 요구사항](docs/planning/organized/기능정의서.html)
  - [화면정의서 · 10개 논리 화면과 상태·전이](docs/planning/organized/화면정의서.html)
  - [기술스펙정의서 · 8개 에이전트·MCP·18개 기술](docs/planning/organized/기술스펙정의서.html)

종류별 문서는 2026-10-05에 기존 자료를 분리한 설계본입니다. 아래 통합·이전 문서는 원본으로 보존하며, 최신 역할·검색·팀 실행 환경은 종류별 문서의 적용 기준을 따릅니다.

- [MCP 기반 8개 에이전트 기획서 v2 · 기술스펙·일정·산출물](docs/planning/presentation-plan-v2.html)

- [발표용 간략 기획서 · 6개 주제](docs/planning/presentation-plan-v1.html)

- [기능정의서 v3 · 육하원칙과 배경→문제→해결책→기대효과](docs/planning/functional-definition-v3.html)

- [기능정의서 v2 · 33개 기능과 69개 원 요구사항](docs/planning/functional-definition-v2.html)

- [교육 기준 반영 실행계획 v2 · 산출물/개발 순서](docs/planning/education-aligned-plan-v2.html)

- [업무 흐름 정의서](docs/planning/workflow-definition-v1.html)
- [검수 개발 계획·69개 요구사항 추적](docs/planning/strawberry-inspection-plan.html)
- [요구사항 추적 CSV](docs/planning/requirements-trace.csv)

## 구현 참고

- [RapidOCR](https://github.com/RapidAI/RapidOCR)
- [ONNX Runtime 설치 및 Windows 런타임 요구사항](https://onnxruntime.ai/docs/install/)
- [Apple Vision 문자 인식 요청](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Django ASGI 실행](https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/)
- [FastAPI 하위 애플리케이션 연결](https://fastapi.tiangolo.com/advanced/sub-applications/)

이 앱은 개발용 로컬 서버입니다. 서비스 배포 전에는 다중 사용자 인증·발주별 권한·외부 전송 동의·운영 정책을 추가하고 해당 요구사항을 별도로 검증해야 합니다.

## 공통 OCR 전환 검증 · 2026-10-06

### 세 주제 샘플 확장 검증

자동 테스트 **105개** 통과. 주제·발주별 기준 확정과 접수 연결, 다른 기준 버전의 실행 거부, 저장 당시 기대 수량 보존을 확인했습니다. 새 이미지 12장은 원본 해시가 일치하며 정답키·생성 프롬프트는 서버에 복사하지 않았습니다.

Apple Silicon의 기본 `auto` 설정에서 **18장 모두 실제 MPS OCR·MCP·이력 저장**을 실행했습니다. 페스티벌은 문구 5항목을 실행해 `completed`, 딸기라떼·크루아상은 문구 3항목과 객체·외관 2항목 미실행으로 `partial`을 기록했습니다. `completed`는 검사를 실행했다는 뜻이며 모든 조건 충족이나 최종 승인을 뜻하지 않습니다. `.runtime/all-topic-execution-report.json`은 개발 샘플의 관찰 기록이며 독립 성능 평가가 아닙니다.

새 주제 12장은 ONNX CPU로도 직접 판독했습니다. 이번 페스티벌 샘플에서 MPS의 한국어 v4 인식 모델이 큰 장식 글꼴 제목을 일부 오독해 정상 제목도 미충족으로 표시했습니다. CPU 한국어 v5 경로는 해당 제목을 읽었지만 크루아상 샘플 06의 겹친 제목 영역에서는 중복 글자가 발생해 확인 필요로 남겼습니다. 모델·장치별 결과 차이가 있어 판정 원문과 근거 확인이 필요합니다. GPU/CPU 결과를 정답에 맞춰 보정하거나 범용 정확도로 집계하지 않았습니다.

브라우저에서 크루아상 주제 → 샘플 04 → 발주 04의 큰 2개·미니 3개 → 기준 확정 → 실제 OCR 검사 → 브랜드 `BUTTER MARKET` 불일치 근거 표시를 확인했습니다. 객체 수량과 접시는 확인 필요로 유지합니다.

### 자동 장치 선택 추가 검증

최신 자동 테스트 **94개** 통과, Django 시스템·마이그레이션 검사와 의존성 충돌 검사 통과. Mac Apple Silicon에서 **실제 ONNX CPU와 실제 PyTorch MPS**를 각각 실행해 샘플 2건의 상품명·할인율·브랜드, 한글 파일 경로, MCP 자식 프로세스를 검증했습니다. MPS 검사에서는 CUDA·MPS 실패 후 CPU로 전환한 결과를 성공으로 취급하지 않도록 `--expect-device mps`로 실제 장치를 강제 확인했습니다. 초기 영문 브랜드 오인식은 GPU 영어 인식 모델을 추가해 보완했으며 일반 정확도나 속도 개선을 보장하는 결과는 아닙니다.

장치 선택 분기·Intel CPU 정책·GPU 모델 미준비·GPU 초기화/추론 실패 후 CPU 전환·실제 장치 표시·사전 해시·영문 인식 라우팅은 자동 테스트로 확인했습니다. Windows/CUDA 및 Intel Mac 실기는 미검증입니다. 수동 GitHub Actions는 Windows CPU·Mac CPU·Mac MPS 조합을 제공하며 아직 실행하지 않았습니다.

로컬 웹 화면의 기본 `auto` 설정에서도 샘플 02를 실행해 `torch-mps-v1` 검사 방법, `MPS` 실행 기록, 할인율 미충족 판정과 근거 표시를 확인했습니다.

```sh
LATTE_OCR_DEVICE=cpu .venv/bin/python scripts/ocr_smoke.py --expect-device cpu
LATTE_OCR_DEVICE=mps .venv/bin/python scripts/ocr_smoke.py --expect-device mps
```

Windows CUDA 실기 확인은 PowerShell에서 `$env:LATTE_OCR_DEVICE = "cuda"`를 설정하고 `.\.venv\Scripts\python.exe scripts\ocr_smoke.py --expect-device cuda`로 실행합니다. GPU가 실제로 쓰이지 않으면 검증에 실패하므로 CPU 전환과 GPU 성공을 구분할 수 있습니다. 최신 검증 명령은 `.runtime/ocr-smoke-report.json`을 쓰며 이 장비에서 각각 확인한 보고서는 `.runtime/ocr-smoke-report-cpu.json`, `.runtime/ocr-smoke-report-mps.json`에 보존했습니다.

### 이전 CPU 전환 검증 기록

자동 테스트 **67개** 통과, Django 시스템 검사 및 누락 마이그레이션 검사 통과. Mac Apple Silicon에서 새 RapidOCR를 직접 실행해 샘플 01의 `30% 할인`은 충족, 샘플 02의 `20% 할인`은 미충족으로 구분했습니다. 상품명 `딸기라떼`와 영문 브랜드 `MORNING CUP`도 읽었습니다. 추론 중 모델·사전 다운로드 함수를 차단한 상태에서 성공했고, 한글·공백 파일 경로를 실제 MCP 자식 프로세스에 전달해 검사했습니다. 결과는 `.runtime/ocr-smoke-report.json`에 기록합니다. 웹 화면에서도 샘플 02를 실행해 할인율 판정·근거 영역·실제 공통 엔진 표시를 확인했습니다.

모델 파일 3개의 해시 확인·손상 모델 거부·다운로드 실패 정리·빈 판독과 낮은 신뢰도·문구 병합·좌표 변환·MCP 설정 전달을 자동 테스트로 확인했습니다. 개수·외관 2항목은 후속 구현 범위입니다. Windows는 공통 CPU 경로를 구현하고 OS 분기 테스트를 했으나 실제 Windows 장비 및 GitHub Actions 실행은 아직 완료하지 않았습니다.

## Django·FastAPI 전환 검증 · 2026-10-06

아래는 공통 OCR 도입 전 Apple Vision으로 수행한 검증 기록입니다.

자동 테스트 15개가 통과했습니다. 기존 API 계약·업로드·중복 요청 처리에 더해 Django 화면·관리자 경로·정적 파일·CSRF·기존 DB 이전·동시 중복 요청·런타임별 데이터 분리를 확인했습니다. Django 시스템 검사와 누락 마이그레이션 검사도 통과했습니다.

실제 기존 데이터의 기준 확정 1건·이미지 7개·검수 이력 7건을 이전하고 JSON 결과 및 이미지 파일이 동일함을 비교했습니다. 백업 위치와 비교 결과는 `.runtime/django-migration-report.json`에 기록합니다.

별도 테스트 런타임에서 브라우저로 기준 확정·이미지 선택·검수를 실행했습니다. 샘플 01은 문자 3항목 충족, 샘플 02는 20% 할인 문구를 읽어 할인율 미충족으로 표시했습니다. 두 실행 모두 실제 MCP 초기화·도구 조회·OCR 호출이 성공했습니다. 실행 기록은 `.runtime/django-fastapi-smoke-report.json`, 확인 화면은 `.runtime/django-fastapi-proof.jpg`에 보관합니다.

## 이전 OCR 실행 검증

아래는 기존 Apple Vision 시제품의 검증 기록입니다. API·검사 로직 테스트 10개 통과. 실제 서버에서 샘플 6장 모두 OCR·MCP를 실행했습니다. 20% 할인과 말차라떼 표기를 각각 조건 불일치로 검출했고, 브랜드 미검출은 OCR 누락 가능성 때문에 확인 필요로 보존했습니다. 개수·외관 2항목은 미실행입니다. 전체 실행 관찰은 `.runtime/sample-execution-report.json`에 저장됩니다. 이는 개발용 샘플 실행 결과이며 범용 정확도 평가가 아닙니다.

현재 선택된 OCR로 샘플 6장을 실행하려면 서버를 켠 상태에서 별도 터미널을 열고 다음 명령을 사용합니다. 이 명령은 로컬 개발 기준을 확정하고 새 접수·실행 이력을 생성합니다. Windows에서도 모델을 준비한 뒤 기본 RapidOCR로 실행합니다. 엔진이 다르면 아래 과거 기록과 인식 결과가 일치하지 않을 수 있습니다.

macOS:

```sh
.venv/bin/python scripts/verify_samples.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe scripts/verify_samples.py
```

8001번 서버를 검사하려면 검증 명령 앞에 macOS에서는 `CHECKER_PORT=8001`을 붙이고, PowerShell에서는 먼저 `$env:CHECKER_PORT = "8001"`을 실행하세요. PowerShell에서 기본 포트로 돌아가려면 `$env:CHECKER_PORT = "8000"`으로 지정합니다.

macOS Vision이 제한된 샌드박스에서 Foundation 오류를 반환할 경우 일반 로컬 터미널에서 서버를 실행하세요. 실제 검사 실패도 UI에 그대로 표시됩니다.
