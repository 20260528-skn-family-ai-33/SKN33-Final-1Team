> 현재 프로젝트의 공통 모델은 **Qwen3.5-4B**입니다. 아래 OCR 수치는 변경 전 비교 실험의 기록이며 현재 모델의 성능으로 다시 표시하지 않습니다.

# 한국어 PaddleOCR·Qwen 4B 연결 및 실제 비교

> 이 문서는 혼합 경로를 처음 연결했을 때의 실험 기록이다. 이후 추가된 Paddle 단독 탐지·인식과 항목별 판정 수정은 [후속 보고서](ocr-paddle-only-2026-10-13.md)에 기록한다. 아래 수치는 당시 코드와 결과를 그대로 보존한다.

## 적용 내용과 선택

기존 EasyOCR의 자동 탐지 영역을 **한국어 `korean_PP-OCRv5_mobile_rec`** 또는 **당시 로컬 Qwen OCR 비교 모델**로 다시 읽는 경로를 연결했다. 여러 줄 결합, 원문·후보 보존, API 좌표 복원, 결과 화면의 모델 이름 표시까지 연결했다. 새 함수에는 간단한 한국어 주석을 추가했다.

이 비교에서는 **Paddle 경로를 우선 시험할 수 있다.** 자동 판정 확대는 별도 검증이 필요하다. 사람 확인 전 초안 라벨 5장만 사용했으며 일반적인 포스터 정확도를 확정하지 않았다. 기존 `baseline` 기본값은 유지한다. 로고·잘림은 계속 담당자 확인 대상이다.

## 지금 이 Mac에서 실행하기

이미 모델과 분리 환경을 준비했다. 저장소 루트에서 기존 검수 서버를 중지하고 실행한다.

```bash
OCR_PIPELINE=paddle ./run.sh
```

Qwen을 시험하려면 첫 번째 터미널에서 로컬 모델 서버를 유지하고 두 번째 터미널에서 검수 서버를 실행한다.

```bash
# 터미널 1
.venv/bin/python -m scripts.local_ollama serve
```

```bash
# 터미널 2
OCR_PIPELINE=qwen ./run.sh
```

`scripts.local_ollama`는 포트 `127.0.0.1:11435`, 프로젝트의 `.runtime/ollama/models`, 클라우드 비활성화 설정을 사용한다. 이미 이 포트에 실행 중이면 중복 실행하지 않는다. 별도로 설치한 Ollama의 기본 포트 11434를 쓰려면 해당 서버에도 모델을 준비하고 `OCR_OLLAMA_URL=http://127.0.0.1:11434`를 함께 설정한다. 숫자 loopback 주소만 허용하며 프록시·redirect를 통한 외부 이미지 전송도 차단한다.

준비 상태만 확인:

```bash
OCR_PIPELINE=paddle ./run.sh --check-only
OCR_PIPELINE=qwen ./run.sh --check-only
```

Windows PowerShell에서는 `$env:OCR_PIPELINE = "paddle"` 또는 `"qwen"`을 지정한 뒤 `.\run.cmd`로 실행한다. Qwen 서버는 `.\.venv\Scripts\python.exe -m scripts.local_ollama serve`로 별도 터미널에서 유지한다. 이번 실제 추론 검증은 Mac에서 수행했다.

## 다른 PC에서 최초 준비

**Git에는 코드·React 빌드만 포함한다.** 가상환경·가중치·실험 포스터·실행 결과는 `.runtime/`, `artifacts/` 아래 Git 제외 대상이다. 따라서 pull만으로 모델 설치가 완료되지 않는다. `run.sh`와 `run.cmd`는 부족한 항목을 안내하고 중단하며 설치·다운로드를 실행하지 않는다.

### 한국어 PaddleOCR

macOS/Linux:

```bash
.venv/bin/python -m venv .runtime/paddle-env
.runtime/paddle-env/bin/python -m pip install -r requirements-paddle.txt
.venv/bin/python -m scripts.prepare_paddle
OCR_PIPELINE=paddle ./run.sh
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m venv .runtime/paddle-env
.\.runtime\paddle-env\Scripts\python.exe -m pip install -r requirements-paddle.txt
.\.venv\Scripts\python.exe -m scripts.prepare_paddle
$env:OCR_PIPELINE = "paddle"
.\run.cmd
```

PaddlePaddle 3.3.1, PaddleOCR 3.7.0, PaddleX 3.7.2를 별도 환경에 고정했다. 기존 `.venv` 패키지를 변경하지 않는다. 모델 준비 명령은 공식 Hugging Face revision `c02ecaf1f22bfd1c618cce154fd19185b47e663a`의 세 파일을 내려받고 SHA-256을 저장한다. 추론 작업자는 로드 전에 해시를 검사하며 다운로드를 하지 않는다. 현재 이 어댑터는 **CPU 4스레드**를 명시적으로 사용한다.

### Qwen 4B

[공식 Ollama](https://ollama.com/download)를 설치한다. 이 Mac에는 공식 v0.40.3 CLI를 프로젝트의 `.runtime/ollama/bin`에 준비했다. 일반 설치의 CLI도 사용할 수 있다.

```bash
# 터미널 1: 로컬 서버 유지
.venv/bin/python -m scripts.local_ollama serve
```

```bash
# 터미널 2: 한 번만 모델 준비 후 실행
.venv/bin/python -m scripts.local_ollama pull
OCR_PIPELINE=qwen ./run.sh
```

Windows에서는 위 Python 경로를 `.\.venv\Scripts\python.exe`로 바꾸고 환경 변수는 PowerShell 문법으로 설정한다. 모델은 약 3.3GB이며 실행 메모리는 별도로 필요하다. 기존 문서의 `qwen3-vl:4b`도 실제로 시험했으나 이 배포의 thinking 경로가 출력 한도에 도달했다. 최종 어댑터는 같은 크기의 명시적 `4b-instruct` 태그를 사용한다. 실행 결과에 실제 digest와 Ollama 버전을 저장한다.

## 처리와 제한

1. EasyOCR을 별도 프로세스에서 실행해 최초 문구·탐지 박스를 저장한다.
2. 기존과 같은 선정 규칙으로 핵심 정보·불확실한 영역을 최대 8개 선택한다. 원본의 기대 문구·라벨·파일명 정답을 모델에 전달하지 않는다.
3. 같은 여백·확대·회색조 crop을 Paddle 또는 Qwen에 전달한다. Paddle은 인식 전용 모델이므로 놓친 영역을 새로 탐지하지 못한다.
4. Qwen은 필수 정보가 미추출된 경우 전체 이미지도 최대 1회 검토한다. 입력의 긴 변은 최대 1536픽셀이고 원본 복원 변환을 기록한다. 생성된 위치가 기존 박스와 겹치지 않을 때만 별도 미확정 후보로 추가한다. 수치가 유효해도 실제 위치가 맞는지는 사람 확인이 필요하다.
5. 새 모델 문구는 **미확정 후보**로 선택한다. Paddle 점수를 EasyOCR과 비교해 우열을 정하지 않고, Qwen confidence는 `null`로 둔다. 같은 문구라도 새 모델의 검증되지 않은 점수만으로 자동 통과시키지 않는다.
6. 원문·후보·모델 출처·crop 해시·역변환·모델 해시·원시 응답·시도 실패를 비공개 실행 폴더에 보존한다. 공개 API는 허용한 엔진 이름과 좌표·문구·검토 상태만 노출한다.

EasyOCR 기준선 작업자 60초, Paddle 작업자 75초, Qwen HTTP 요청별 최대 60초 대기를 사용한다. 이미지별 시도 예산은 최대 240초다. 이 예산은 전처리·메타데이터 저장까지 포함한 엄격한 프로세스 상한을 뜻하지 않는다. Qwen의 HTTP 시간 초과 시 연결을 닫고 후속 추론 요청을 중단하며 공유 Ollama 서버를 강제 종료하지 않는다.

새 모델의 일부 호출이 실패하면 보존된 원문 또는 앞서 얻은 후보를 모두 확인 대상으로 남기고 `refinement_status=partial`을 기록한다. 기준선조차 실패하거나 선택 모델 준비가 안 됐으면 검수 실행을 실패로 처리한다. 로컬 실패를 유료 API·RunPod로 전환하지 않는다.

## 실제 비교 결과

비교 입력은 `posters-v1`의 tuning 5장, RGB 2배, 같은 라벨 스냅샷이다. 최종 확인용 3장은 추론하지 않았다. 초안의 19개 필드·199개 핵심 문자·17개 기준을 사용한다.

| 경로 | 처리 완료 | 필드 일치 | 필드 누락 | 문자 오류율 | 오통과 / 위반 기준 | 확인 필요 / 전체 | 전체 처리 중앙값 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 기존 EasyOCR | 5/5 | 1/19 | 14 | 22.1% | 1/1 | 13/17 | 4.31초 |
| 이전 결합 + EasyOCR 재인식 | 5/5 | 6/19 | 10 | 22.1% | 0/1 | 16/17 | 4.48초 |
| 결합 + 한국어 PaddleOCR | 5/5 | 9/19 | 6 | 6.0% | 0/1 | 17/17 | 5.25초 |
| 결합 + Qwen 4B Instruct | 5/5 | 9/19 | 9 | 59.8% | 0/1 | 17/17 | 20.49초 |

기존/이전 개선 경로의 올바른 자동 통과는 각각 1/14이고, Paddle/Qwen 경로는 각각 **0/14**다. 두 새 모델 모두 미검증 후보를 보류하므로 17개 기준 모두 확인 필요로 남았다. 오통과 0건을 자동 판정 정확도가 완성됐다는 의미로 해석하지 않는다. 기준선의 오불충족 2/14는 나머지 경로에서 0/14가 됐으며 불확실한 항목은 보류했다.

| 포스터 | 이전 결합·재인식 | Paddle | Qwen | 확인한 변화 |
|---|---:|---:|---:|---|
| 김천 | 0/4 | 3/4 | 3/4 | 두 모델에서 날짜 쌍·장소가 일치. 행사명은 여전히 실패 |
| 춘천 | 2/3 | 2/3 | 2/3 | 날짜 유지. Paddle의 제목은 `춘천임축제`로 부정확 |
| 구미 | 1/4 | 1/4 | 3/4 | Qwen이 날짜 쌍을 복원. Paddle은 `구미` 탐지 누락을 복구하지 못함 |
| 단양 | 3/4 | 2/4 | 0/4 | 새 모델에서 제목이 악화. Qwen은 날짜도 추출하지 못함 |
| 안동 | 0/4 | 1/4 | 1/4 | 제목 복원. 날짜·장소 전체는 일치하지 않음 |

Paddle의 핵심 문자 오류는 44/199 → **12/199**로 줄었다. Qwen은 **119/199**로 늘었다. 일부 큰 crop에서 제목 외 문구를 함께 출력하거나 같은 문구를 반복해 삽입 오류가 증가했다. 현재 crop·프롬프트 구성에서는 **Paddle을 우선 시험하고 Qwen의 주 운영 경로 채택은 보류**한다. 이 결과가 Qwen 모델 전체의 일반 성능을 뜻하지는 않는다.

메모리 기록의 프로세스 최대 RSS 중앙값은 기존 약 1.07GiB, 이전 개선 1.11GiB, Paddle 1.09GiB, Qwen의 Python 작업자 0.86GiB다. Qwen은 이와 별도로 Ollama `/api/ps`가 보고한 GPU 적재량 약 **3.94GiB**를 사용했다. RSS는 전체 시스템·GPU 최대 사용량이 아니며 통합 메모리 값과 단순 합산하지 않는다. Qwen의 최종 측정은 앞선 실험에서 로드된 모델·프롬프트 캐시를 사용할 수 있어 완전한 cold-start 비교가 아니다.

최종 실행은 4경로 × 5장 = **20건**이며 모두 완료됐다. Paddle 40개 crop, Qwen 40개 crop + 전체 이미지 5회도 모두 완료됐고 최종 새 모델 호출의 중단·부분 실패는 없었다. 각 실행의 코드 10개 해시·소스 스냅샷과 현재 코드가 일치하며 원본 이미지도 변경되지 않았다.

최종 실행 폴더:
- 기존 EasyOCR: `artifacts/ocr-benchmarks/20261013T082720Z-32451101/`
- 이전 결합 + EasyOCR 재인식: `artifacts/ocr-benchmarks/20261013T082743Z-5500eb16/`
- 결합 + 한국어 PaddleOCR: `artifacts/ocr-benchmarks/20261013T082808Z-c08003c0/`
- 결합 + Qwen 4B Instruct: `artifacts/ocr-benchmarks/20261013T082837Z-1ff370b5/`

초기 `qwen3-vl:4b` 실험 `20261013T081819Z-0b191547`에서는 5장 모두 일부 재인식이 출력 토큰 제한으로 중단됐다. 이를 성공한 Instruct 결과와 합산하지 않았다. 중단한 원시 응답과 실패 이유도 해당 실행 폴더에 보존했다.

문자 오류율(CER)은 라벨 영역의 OCR 문구를 대조하며 공백·Unicode만 정규화한다. 삽입된 글자가 많으면 100%를 넘을 수도 있다. 필드 일치와 자동 통과는 별도 지표다. 새 후보를 미확정으로 남기므로 잘못된 통과가 줄어도 올바른 자동 통과가 늘었다는 뜻은 아니다.

처리 시간은 각 이미지의 작업자 생성·모델 로드·추론을 포함한 전체 벽시계 시간이다. EasyOCR/Paddle은 이미지마다 작업자를 만들고 Qwen은 별도 Ollama 모델을 재사용한다. 1회씩 측정한 진단값으로 안정적인 평균·p95를 뜻하지 않는다. Mac M4 Pro에서 EasyOCR은 MPS, Paddle은 CPU, Qwen은 로컬 GPU를 사용했다. CPU/CUDA/Windows/RunPod 성능을 실측했다고 주장하지 않는다.

## 재측정과 산출물

```bash
.venv/bin/python -m scripts.benchmark_ocr --pipeline baseline --variants rgb_2x --timeout 240
.venv/bin/python -m scripts.benchmark_ocr --pipeline adaptive --variants rgb_2x --timeout 240
.venv/bin/python -m scripts.benchmark_ocr --pipeline paddle --variants rgb_2x --timeout 240
.venv/bin/python -m scripts.benchmark_ocr --pipeline qwen --variants rgb_2x --timeout 240
.venv/bin/python -m scripts.compare_benchmarks <첫번째/report.json> <두번째/report.json>
```

매번 새 폴더를 만들고 원본·라벨·과거 결과를 덮어쓰지 않는다. `code.snapshot/`에는 해당 실행의 소스도 고정한다. 비교 도구는 입력 해시·라벨 스냅샷·분할·반복 수가 다르면 비교를 거절한다.

## 검증과 변경 파일

- Python **190개 + 하위 테스트 7개** 통과. 모델 미준비, 제한시간, 미확정 후보의 통과 방지, 잘못된 VLM 좌표, 좌표 복원, 비공개 원시 응답, API 연결, Windows 실행 전 검사 분기를 포함한다.
- 프런트엔드 단위 테스트 **18개**, 모의 응답 기반 Chrome 테스트 **16개** 통과. 원문·Qwen·PaddleOCR 후보 이름과 근거 영역 표시를 확인했다.
- React 빌드 성공, Git에 포함되는 `frontend/dist` 갱신, `git diff --check` 통과.
- 실제 `OCR_PIPELINE=paddle/qwen` 실행 전 검사 통과. 패키지·모델 준비 검사 중 추가 다운로드·추론은 수행하지 않는다.
- 별도로 실제 Paddle 모델을 사용한 업로드 → OCR → API 결과 반환도 확인했다. 김천 장소의 `생대` 원문과 `생태` 후보가 함께 남으며 판정은 `review`다. 기록은 `.runtime/model-api-smoke/d4f6006b-dce7-4387-b5aa-6a11078b6954/`에 보존했다.

| 파일 | 역할 |
|---|---|
| `backend/app/model_ocr.py` | 모델 선택, 영역 후보·전체 문맥 검토, 로컬 연결과 시간·시도 제한 |
| `backend/app/paddle_worker.py` | 분리 환경의 한국어 모델 로드·인식·원시 결과 저장 |
| `backend/app/api.py` | 엔진 이름·후보 출처·원본 좌표 공개, 원시 결과 보존 |
| `start.py` | 선택 모델의 실행 전 검사와 준비 명령 안내 |
| `requirements-paddle.txt`, `scripts/prepare_paddle.py` | 독립 패키지 버전과 고정 모델 준비 |
| `scripts/local_ollama.py` | 프로젝트용 로컬 서버·모델 준비 명령 |
| `scripts/benchmark_ocr.py` | 새 모델 비교와 실행 소스 스냅샷 |
| `frontend/src/pages/review/ResultsPage.jsx`, `frontend/dist/` | 후보에 실제 모델 이름 표시·배포 빌드 |
| `backend/tests/test_model_ocr.py`, `tests/test_launchers.py`, `frontend/tests/e2e/login.spec.mjs` | 어댑터·API·실패·보류·준비 검사·브라우저 회귀 검증 |

실제 포스터 비교와 모의 응답 테스트는 별개다. 다음 단계는 라벨의 사람 확인, 표본 확대, 모델별 후보 선택·보류 기준 검증이다. 자동 검수 범위를 확대하기 전에 정상 항목의 추가 보류와 오인식 악화도 함께 확인해야 한다.

## 공식 자료

- [한국어 PP-OCRv5 모델](https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec)
- [PaddleOCR 인식 모듈](https://www.paddleocr.ai/latest/en/version3.x/module_usage/text_recognition.html)
- [PaddlePaddle macOS 설치](https://www.paddlepaddle.org.cn/documentation/docs/en/install/pip/macos-pip_en.html)
- [현재 공통 모델 Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)
- [Ollama 이미지 메시지 API](https://docs.ollama.com/api/chat)
