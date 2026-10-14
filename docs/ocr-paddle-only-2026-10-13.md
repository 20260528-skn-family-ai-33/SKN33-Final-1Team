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

## 2026-10-14 검증 부록 — 프로젝트 소개 README의 지표 근거

이 부록의 성능 비교값은 **기존 실험 산출물을 2026-10-14에 다시 읽고 계산한 기록**이다. 본문의 2026-10-13 비교표와 당시 사용자 검수 결과는 역사적 기록으로 유지한다. README 화면 확인을 위해 2026-10-14에 별도로 실행한 김천 사례 1건은 아래 ‘실제 화면 재실행’에 구분해 기록하며, 기존 성능 지표에 합산하지 않는다.

### 같은 조건의 최종 CPU 실험

아래 세 실행의 `report.json`과 각 포스터의 `evaluation.json`을 대조했다. 매니페스트·분할·전처리·반복 수·사례 ID가 같으며, 포스터 5장의 원본·라벨 스냅샷·OCR 입력 파일 해시도 세 실행에서 모두 일치했다.

| 처리 경로 | 원시 실험 ID | 핵심 문자 오류 수 | 완전 일치 필드 |
|---|---|---:|---:|
| EasyOCR 기준선 | `20261013T085419Z-4af69d0d` | 44/199 | 1/19 |
| EasyOCR + Paddle / 판정 수정 후 | `20261013T085445Z-5d8fd30d` | 12/199 | 9/19 |
| Paddle 단독 | `20261013T085522Z-a3fb9fad` | 7/199 | 12/19 |

공통 조건은 Mac M4 Pro, macOS ARM64, Python 3.12.14, 실제 CPU 실행, `tuning` 포스터 5장, RGB 2배 입력, 경로별 이미지당 1회다. 시간·메모리는 본문의 최종 CPU 비교값을 사용한다. 앞선 MPS 실험의 추론 전용 시간과 이 실험의 전체 처리 시간을 한 지표로 합치지 않는다.

### 문자 오류 감소율과 필드 오류 수정률

| 지표 | EasyOCR + Paddle | Paddle 단독 |
|---|---:|---:|
| 기준선 대비 문자 오류 순감소율 | `(44 - 12) / 44` = **72.7%** | `(44 - 7) / 44` = **84.1%** |
| 기준선에서 틀린 필드의 수정률 | `8 / 18` = **44.4%** | `11 / 18` = **61.1%** |
| 기준선에서 맞았던 필드 유지 | 1/1 | 1/1 |
| 기준선 정답 필드의 회귀 | 0/1 | 0/1 |

- **핵심 문자 오류율(CER)**은 라벨 영역별 편집 거리 합을 참조 문자 199자로 나눈 값이다. 공백·NFC만 정규화하며 문장부호는 유지한다. 전체 포스터의 모든 글자를 채점한 정확도가 아니다.
- **문자 오류 순감소율**은 기준선의 편집 오류 수 44에 대한 상대 감소량이다. 84.1%는 OCR 정확도가 아니며, 개별 오류 37개가 모두 같은 위치에서 교정됐다는 의미도 아니다.
- **필드 오류 수정률**은 같은 `(포스터 ID, 필드 이름)`끼리 대응하여, 기준선에서 틀렸던 18개 중 개선 경로에서 완전히 일치한 개수를 센 값이다. 혼합 경로는 8개, 단독 경로는 11개를 수정했고 기준선의 정답 1개도 유지했다. 따라서 단독 경로의 전체 필드 완전 일치는 1/19(5.3%)에서 12/19(63.2%)로 늘었다.
- 필드는 행사명·시작일·종료일·장소를 평가한다. 춘천 장소가 미확정이어서 5장 × 4필드 중 19개만 분모에 포함했다. `null`인 추출 누락과 문구가 틀린 필드는 모두 완전 일치 실패이지만, 누락 수는 따로 기록한다.
- 평가용 판정 기준 17개는 정상 문구 기준 14개, 고의 위반 기준 1개, 시각 확인 기준 2개다. 올바른 충족·오통과·오불충족·확인 필요는 각각 분모가 다르다. 판정 정책도 변경됐으므로 충족 증가를 모델 효과만으로 설명하지 않는다.

라벨 5개는 모두 `human_verified=false`인 초안이며 승인 라벨로 평가한 실행은 0건이다. 최종 확인용 p06~p08 3장은 미추론·미라벨 상태다. 위반 기준 1개의 오통과가 0이 된 결과를 일반적인 안전성 검증으로 해석하지 않는다. 반복 측정 평균·p95와 실제 Windows·CUDA·RunPod OCR 성능은 이 표의 검증 범위에 포함되지 않는다.

### 기존 저장 결과로 확인한 김천 사용자 기준 7개

2026-10-14에 `.runtime/reviews/db455d5b-4e0d-401d-b76b-3f58042921cb/result.json`을 다시 확인했다. 이는 앞서 실행되어 저장된 실제 API 결과이며, 이번 README 작성 중 새로 추론한 결과가 아니다. 실행 엔진은 `paddleocr`, 파이프라인은 `paddle_full_v1`, 장치는 `cpu`, 기준 버전은 1이다. 이미지 SHA-256은 `505650744b32f644c6820a817506a7d9e4ecc67c7468fc01fff840f4be050924`다.

| 사용자 기준 | 본문에 기록된 로고 정책 적용 전 | 로고 문구 정책 적용 후 저장 결과 |
|---|---|---|
| 김천김밥축제 | 충족 | 충족 |
| 2026.10.23 ~ 10.25 | 충족 | 충족 |
| 직지문화공원 | 충족 | 충족 |
| 사명대사공원 | 충족 | 충족 |
| 친환경생태공원 | 충족 | 충족 |
| 김천시 로고가 표시되어 있어야 합니다. | 확인 필요 | 충족 — `logo_text_proxy` |
| 문구와 이미지가 잘리지 않아야 합니다. | 확인 필요 | 확인 필요 |
| **합계** | **충족 5개 / 미충족 0개 / 확인 필요 2개** | **충족 6개 / 미충족 0개 / 확인 필요 1개** |

추가 충족 1개는 OCR 모델 변경이 아니라 **‘김천시’ 글자를 확인하면 해당 로고 기준을 충족으로 처리하는 임시 정책**에 따른 것이다. 심벌 형태·색상·공식 로고 원본과의 동일성을 검증한 결과가 아니다. 잘림 기준은 여전히 담당자 확인이 필요하다.

사용자 기준 7개와 성능 평가용 기준 17개는 별도의 목록이며 합산하지 않는다. 평가용 로고 문구는 `김천시 로고 포함`, 사용자 문구는 `김천시 로고가 표시되어 있어야 합니다.`로 다르다. 2026-10-14 코드의 로고 문구 정책은 후자의 특정 기준을 대상으로 적용된다.

### 2026-10-14 실제 화면 재실행

브라우저에서 `admin / admin` 로그인 → 김천 샘플 선택 → 7개 기준 확정 → Paddle 단독 검수 1회를 실행했다. 새 결과는 `.runtime/reviews/0870311a-5f25-4e8e-be89-3c908e1e68be/result.json`에 저장됐다.

| 항목 | 실제 기록 |
|---|---|
| 검수 ID | `0870311a-5f25-4e8e-be89-3c908e1e68be` |
| 생성 시각 | 2026-10-14 10:44:59 KST (`2026-10-14T01:44:59.132309+00:00`) |
| 완료 결과 저장 시각 | 2026-10-14 10:45:10 KST (`2026-10-14T01:45:10.039080+00:00`) |
| 실행 경로 | `paddleocr` / `paddle_full_v1` / `local` / `cpu` |
| 상태 | `completed`, 오류 없음 |
| 기준 버전·결과 | 버전 1, **충족 6개 / 미충족 0개 / 확인 필요 1개** |
| 원본 SHA-256 | `505650744b32f644c6820a817506a7d9e4ecc67c7468fc01fff840f4be050924` |
| OCR 입력 PNG SHA-256 | `5b45c56f95c2f7877f36b471989915bf7f2ca3b9ee72f42bc0dd4488aa043977` |
| 정규화 원본 / OCR 입력 | RGB 600 × 848 / RGB 1200 × 1696 |

원본 파일의 실제 SHA-256이 결과 JSON 및 기존 김천 사례와 일치하며, OCR 입력 PNG도 최종 Paddle 단독 실험의 p01 입력과 같은 해시임을 확인했다. 기준 7개의 ID·문구·유형·원문도 기존 저장 결과와 같다. 새 결과의 로고 항목은 `logo_text_proxy`로 충족이며, 잘림 항목은 확인 필요다. OCR 근거 18개의 박스·다각형은 2배 입력 좌표에서 원본 좌표로 정확히 한 번 복원됐고 모든 박스가 이미지 경계 안에 있다. 판정에 연결된 근거 7개의 문구·박스도 해당 OCR ID의 기록과 일치했다.

실제 화면 캡처는 [입력 화면](assets/readme/01-input.jpg), [기준 확인 화면](assets/readme/02-criteria.jpg), [검수 결과 화면](assets/readme/03-results.jpg)에 보존했다. 이 1회 실행은 현재 화면에서 저장 결과까지 이어지는 동작을 확인한 사례다. **기존 5장 성능 비교·199자·19필드·17개 기준의 분모나 처리 시간 중앙값에 추가하지 않는다.** 생성·저장 시각 차이도 OCR 추론 전용 시간으로 해석하지 않는다.

### 기록 시점과 재현 범위

본문의 ‘현재 실험 코드와 소스 스냅샷 해시가 일치한다’는 설명은 **2026-10-13 해당 실험 당시**의 검증 기록이다. 2026-10-14 확인에서는 세 최종 실험 스냅샷과 현재 `backend/app/review_rules.py`의 SHA-256이 다르다.

| 판정 코드 | SHA-256 |
|---|---|
| 최종 CPU 실험 3개의 공통 스냅샷 | `64f7aae863c6be37a3c8df549b0f81d538986b139512039a46bf10f189daf050` |
| 2026-10-14 확인한 작업 트리 | `93af80de903c86bac9e76748ec82cd8a663b274f42ce29b35bdc2ba9a24c5dee` |

따라서 소개 README는 대표 성능 표를 **2026-10-13 실험 스냅샷의 측정값**으로 소개하고, 로고 문구 정책 적용 후 사용자 사례를 별도로 표시한다. 원시 산출물은 `artifacts/ocr-benchmarks/<실험 ID>/`와 `.runtime/reviews/<검수 ID>/`에 보존되어 있으며 Git 제외 대상이다. GitHub에서 공유할 근거는 이 문서와 [비교 도구 안내](ocr-benchmark.md)를 사용한다.
