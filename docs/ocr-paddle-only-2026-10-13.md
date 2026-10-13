# PaddleOCR 단독 탐지·인식 및 항목별 판정 비교

## 선택 경로

`OCR_PIPELINE=paddle-only`는 `PP-OCRv5_server_det`로 글자 위치를 탐지하고 `korean_PP-OCRv5_mobile_rec`로 문구를 읽는다. EasyOCR 기준선을 먼저 실행하지 않는다. 두 모델은 `.runtime/paddle-env`의 CPU 작업자에서 실행하며, 문서 방향 분류·문서 왜곡 보정·글자 줄 방향 분류는 꺼 둔다. 모델 경로를 명시하고 추론 중 다운로드하지 않는다.

기존 `paddle`은 EasyOCR 탐지 후 최대 8개 영역을 Paddle로 다시 읽는 혼합 경로다. 기존 결과와 비교할 수 있도록 유지한다. `baseline`은 기존 EasyOCR이며 기본값도 유지한다. 이 문서는 전체 프로젝트의 일반 정확도를 확정하는 자료가 아니다.

| 설정 | 위치 탐지 | 글자 읽기 | EasyOCR 필요 |
|---|---|---|---|
| `baseline` | EasyOCR | EasyOCR | 필요 |
| `paddle` | EasyOCR | EasyOCR + 선택 영역 Paddle 재인식 | 필요 |
| `paddle-only` | PP-OCRv5_server_det | 한국어 PP-OCRv5 | 불필요 |

단독 경로는 탐지 누락을 EasyOCR에 의존하지 않으며, 최대 8개라는 혼합 재인식 제한도 적용하지 않는다. 탐지·인식이 모두 성공해야 추론 완료로 기록한다. 실패했을 때 다른 엔진이나 유료 서비스로 자동 전환하지 않는다.

## 실행

현재 서버를 실행한 터미널에서 종료한 뒤 저장소 루트에서 실행한다.

```bash
OCR_PIPELINE=paddle-only ./run.sh --check-only
OCR_PIPELINE=paddle-only ./run.sh
```

Windows PowerShell:

```powershell
$env:OCR_PIPELINE = "paddle-only"
.\run.cmd --check-only
.\run.cmd
```

기존 저장 결과는 바꾸지 않는다. 서버를 다시 시작한 뒤 새 검수를 실행해야 새 모델 결과가 생긴다. 공개 API의 `runtime.engine`은 `paddleocr`, `runtime.pipeline`은 `paddle_full_v1`이다. `easyocr+paddleocr`가 표시되면 기존 혼합 경로다.

### 최초 준비

실행 스크립트는 누락 항목과 명령만 안내하며 설치하지 않는다. 정상적인 Python 3.12 프로젝트 `.venv`가 이미 있다는 전제의 macOS/Linux 명령이다. 기존 환경을 삭제하거나 다른 버전 환경 위에 덮어쓰지 않는다.

```bash
# 서버 패키지 준비. 이미 설치되어 있으면 생략한다.
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install -r requirements-test.txt

# Paddle 전용 환경이 없을 때만 생성한다.
.venv/bin/python -m venv .runtime/paddle-env
.runtime/paddle-env/bin/python -m ensurepip --upgrade
.runtime/paddle-env/bin/python -m pip install -r requirements-paddle.txt

# 공식 탐지·인식 모델을 명시적으로 준비한다.
.venv/bin/python -m scripts.prepare_paddle --include-detection
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install -r requirements-test.txt
# Paddle 전용 환경이 없을 때만 생성한다.
.\.venv\Scripts\python.exe -m venv .runtime/paddle-env
.\.runtime\paddle-env\Scripts\python.exe -m ensurepip --upgrade
.\.runtime\paddle-env\Scripts\python.exe -m pip install -r requirements-paddle.txt
.\.venv\Scripts\python.exe -m scripts.prepare_paddle --include-detection
```

`requirements-test.txt`는 EasyOCR/PyTorch가 없는 서버·검증 공통 패키지 목록이다. 이미 전체 개발 환경이 준비되어 있으면 설치를 반복하지 않아도 된다. 가상환경·모델·이미지별 실행 결과는 Git에 넣지 않으며, 다른 PC는 최초 준비가 필요하다. 프런트엔드 빌드가 있으면 실행에 Node.js/npm은 필요하지 않다.

## 판정 변경

이전 혼합 경로는 새 모델이 다시 읽은 줄 전체에 `requires_review=True`를 붙였다. 한 장소의 오탈자가 바뀌어도 같은 줄에 있던 기존 정상 장소까지 충족을 잃었다.

변경 후에는 기존 문구·점수·모델·보류 여부를 보존하고, **요청한 문구 부분**이 기존 충분한 근거와 재인식 결과에 모두 있는지 확인한다. 기존 근거가 이미 보류였거나 재인식이 실패·기권한 경우에는 이 규칙으로 통과시키지 않는다. 새 후보에만 있는 문구와 서로 다른 후보는 확인 대상으로 남는다. 단순한 모델 간 일치가 정답의 증명은 아니다.

Paddle 단독 결과는 EasyOCR 점수와 섞지 않고 별도 운영 기준을 적용한다.

- 문구가 정확히 일치하고 Paddle 인식 점수가 0.90 이상이면 충족 후보로 판정한다.
- 제목·날짜는 기존의 유일한 후보·실제 날짜·요일 검증도 통과해야 한다.
- 구조화된 제목·날짜의 불일치는 0.97 이상의 근거가 있어야 미충족으로 판정한다.
- 낮은 점수, 미검증 후보, 인식 결과 간 충돌, 유일한 값 미추출은 원인을 구분하여 확인 필요로 남긴다.
- 로고·잘림 등 시각 항목은 계속 확인 필요다.

0.90/0.97은 이번 개발용 판정 정책의 초기값이다. 제조사가 보장한 정확도·확률이나 승인된 데이터로 보정한 임계값이 아니다. 높은 점수에서도 오인식이 가능하며 자동 판정과 담당자 승인은 분리한다.

## 실제 비교 결과

Mac M4 Pro / macOS ARM64 / Python 3.12.14에서 같은 포스터 5장, 같은 RGB 2배 입력·라벨 해시로 각각 한 번씩 실행했다. 이번 비교에서는 세 경로 모두 실제 CPU로 실행됐다. 이전 보고서의 EasyOCR MPS 측정 시간·메모리와 직접 합치지 않는다. PaddlePaddle 3.3.1, PaddleOCR 3.7.0, PaddleX 3.7.2를 사용했다.

| 경로 | 완료 | 필드 완전 일치 | 필드 누락 | 핵심 문자 오류율 | 초안 기준 올바른 충족 | 확인 필요 | 전체 처리 중앙값 |
|---|---:|---:|---:|---:|---:|---:|---:|
| EasyOCR 기준선 | 5/5 | 1/19 | 14 | 22.1% (44/199) | 1/14 | 13/17 | 3.63초 |
| EasyOCR + Paddle / 판정 수정 후 | 5/5 | 9/19 | 6 | 6.0% (12/199) | 2/14 | 15/17 | 5.47초 |
| Paddle 단독 | 5/5 | 12/19 | 5 | **3.5% (7/199)** | **11/14** | 6/17 | 6.90초 |

기준선의 오통과는 1/1, 오불충족은 2/14였으며 혼합·단독 경로에서는 각각 0/1, 0/14였다. 위반 기준이 단 1개뿐이므로 오통과 0건을 충분한 안전성 검증으로 해석하지 않는다. 판정 보류 정책도 바뀌었으므로 충족 증가를 인식 모델 효과만으로 설명하지 않는다. 인식 변화는 문자 오류율·필드 추출 지표로 별도 비교한다.

작업자 최대 RSS 중앙값은 EasyOCR 약 6.23GiB, 혼합 약 6.23GiB, Paddle 단독 약 **8.15GiB**였다. 이는 라이브러리·모델 등을 포함한 프로세스 최대 상주 메모리이며 시스템 전체/GPU 사용량이 아니다. 이번 서버 탐지 모델은 인식 품질이 개선된 대신 CPU 처리 시간과 메모리 비용이 증가했다. 반복 측정 평균이나 p95는 측정하지 않았다.

| 포스터 | 기존 EasyOCR 필드 | 혼합 필드 | 단독 필드 | 남은 제한 |
|---|---:|---:|---:|---|
| 김천 | 0/4 | 3/4 | 3/4 | 제목·날짜·장소 문구는 읽었으나 여러 영역의 전체 장소 필드 결합은 실패 |
| 춘천 | 0/3 | 2/3 | 2/3 | 제목 오인식·미추출 |
| 구미 | 1/4 | 1/4 | 3/4 | 날짜·장소 개선, 제목은 여전히 부정확 |
| 단양 | 0/4 | 2/4 | 3/4 | 문구는 읽었으나 전체 장소 필드 추출 실패 |
| 안동 | 0/4 | 1/4 | 1/4 | 제목 인식 성공, 날짜 추출·전체 장소 결합은 미완료 |

라벨은 모두 `human_verified=false`인 초안이다. 5장 진단 결과로 일반적인 포스터 정확도를 확정하지 않는다. 최종 확인용 p06~p08 3장은 추론하지 않았고 기존 라벨·이미지·결과도 변경하지 않았다. 운영 기본값을 자동으로 바꾸지 않고 `paddle-only`를 명시적으로 선택하도록 했다.

### 사용자 김천 검수 7개 기준

최신 사용자 검수와 이미지 SHA-256·7개 기준을 그대로 유지한 새 API 검수를 실행했다. **충족 5개, 미충족 0개, 확인 필요 2개**다. 위 표의 평가용 17개 기준과 사용자 화면의 7개 기준은 서로 다른 목록이다.

| 항목 | 이전 EasyOCR 실제 결과 | Paddle 단독 실제 API 결과 |
|---|---|---|
| 김천김밥축제 | 확인 필요 | 충족 — `김천김밥축제`, 점수 0.9979 |
| 2026.10.23 ~ 10.25 | 확인 필요 | 충족 — `2026.10.23금 ~ 10.25일`, 점수 0.9652 |
| 직지문화공원 | 충족 | 충족 |
| 사명대사공원 | 충족 | 충족 |
| 친환경생태공원 | 확인 필요 | 충족 — 장소 줄 점수 0.9966 |
| 김천시 로고 | 확인 필요 | 확인 필요 |
| 잘림 | 확인 필요 | 확인 필요 |

이전 혼합 경로의 0개 충족은 전체 줄 보류 처리에 의한 결과였다. 수정된 혼합 정책은 요청 문구별로 기존 근거를 재사용해 직지문화공원·사명대사공원 2개를 유지한다. Paddle 단독의 5개 충족은 자체 탐지·인식 결과에 별도 점수 기준과 내용 비교를 적용한 결과이며, 담당자의 최종 승인은 아니다.

### 산출물 및 검증

- 최종 EasyOCR: `artifacts/ocr-benchmarks/20261013T085419Z-4af69d0d/`
- 최종 혼합: `artifacts/ocr-benchmarks/20261013T085445Z-5d8fd30d/`
- 최종 Paddle 단독: `artifacts/ocr-benchmarks/20261013T085522Z-a3fb9fad/`
- 비교표: `artifacts/ocr-benchmarks/paddle-only-final-comparison.md`
- 실제 API 검수: `.runtime/paddle-only-api-smoke/1db40341-95b9-4be8-a906-bf9738dffc86/`
- API 검증 요약: `.runtime/paddle-only-api-smoke/smoke-summary.json`

세 최종 실행의 소스 스냅샷 13개 해시가 현재 실험 코드와 일치하고, 입력·라벨 해시도 일치한다. 최초 진단 실행 3개와 첫 단일 이미지 추론은 별도 폴더에 유지했으며 최종 지표에 중복 합산하지 않았다.

검증 결과:

- Python 전체: **285 passed, 7 subtests passed**.
- 프런트엔드 단위 테스트: **18 passed**. 이번 변경은 화면 코드를 바꾸지 않았다.
- 실제 `OCR_PIPELINE=paddle-only ./run.sh --check-only`: 종료 코드 0.
- 실제 업로드 API: 전처리 → Paddle 추론 → 좌표 복원 → 검수 결과 저장·조회 → 이미지 응답 확인.
- API 프로세스에서 EasyOCR·Torch·Torchvision 불러오기를 차단한 상태로 실제 추론 성공. Paddle 격리 환경에도 세 패키지가 설치되어 있지 않다.
- 브라우저 HTML 요청 헤더로 로그인 화면 제공 확인. 새 화면의 브라우저 시각 검증과 실제 Windows 추론은 이번에 수행하지 않았다.
- Windows CI에 Paddle 환경 누락 시 종료 코드 1, 명시적 설치·모델 준비 안내, 자동 설치·EasyOCR 요구 없음 검사를 추가했다. Windows 실행 결과는 CI에서 확인해야 한다.
- 누락 모델, 변조 해시, import 오류, 시간 초과, 잘못된 배열·좌표, 엔진 출처 유실, 보류 우회, 기존 정상 문구 유지에 대한 회귀 테스트 통과.

주요 변경 파일: `backend/app/paddle_ocr.py`, `backend/app/paddle_full_worker.py`, `scripts/prepare_paddle.py`, `backend/app/review_rules.py`, `backend/app/model_ocr.py`, `backend/app/api.py`, `start.py`, `scripts/benchmark_ocr.py`, 관련 테스트·실행 문서·Windows CI. 새 함수와 주요 분기에 간단한 한국어 설명을 추가했다.

## 재현

동일한 이미지·전처리·라벨로 각각 새 실행 폴더를 만든다.

```bash
.venv/bin/python -m scripts.benchmark_ocr --pipeline baseline --variants rgb_2x --timeout 240
.venv/bin/python -m scripts.benchmark_ocr --pipeline paddle --variants rgb_2x --timeout 240
.venv/bin/python -m scripts.benchmark_ocr --pipeline paddle-only --variants rgb_2x --timeout 240
```

`scripts.compare_benchmarks`에 각 실행의 `report.json`을 전달하면 입력·라벨 해시 일치부터 검사한다. `code.snapshot/`에는 실행 당시 코드를 보존한다. 모델 입력에는 이미지 픽셀만 전달하며 기준의 기대 문구·정답 라벨·원래 파일명을 추론 입력으로 전달하지 않는다. 좌표는 OCR 입력 픽셀에서 정규화 원본 픽셀로 한 번만 복원한다.

## 공식 근거

- [PaddleOCR 탐지·인식 파이프라인](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)
- [PP-OCRv5_server_det 모델](https://huggingface.co/PaddlePaddle/PP-OCRv5_server_det)
- [한국어 PP-OCRv5 인식 모델](https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec)
