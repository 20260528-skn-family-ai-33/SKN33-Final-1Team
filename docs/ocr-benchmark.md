# OCR 기준선 비교 도구

여러 포스터를 같은 조건에서 실행해 OCR·행사 추출·검수 규칙을 비교한다. 최초 실측은 EasyOCR 전처리 비교이며 [기준선 보고서](ocr-baseline-2026-10-13.md)에 보존했다. 이후 여러 줄 결합·영역 재인식·추가 모델·Paddle 단독 탐지와 인식을 선택 경로로 추가했다.

## 실행

처리 경로는 `--pipeline baseline|multiline|adaptive|paddle|paddle-only|qwen`으로 선택한다. 기본값은 `baseline`이며 같은 전처리로 각 경로를 비교하려면 `--variants rgb_2x`를 사용한다. `paddle`은 EasyOCR 탐지 후 Paddle 재인식, `paddle-only`는 Paddle 자체 탐지와 인식이다. [여러 줄 결합·재인식 비교](ocr-improvement-2026-10-13.md), [혼합 모델 비교](ocr-model-comparison-2026-10-13.md), [Paddle 단독 비교](ocr-paddle-only-2026-10-13.md)에 실측과 실행 명령이 있다. `scripts.compare_benchmarks`는 이미지·입력·라벨 스냅샷·분할·반복 조건이 같은 보고서만 비교한다. 새 실행은 `code.snapshot/`에도 실험 코드를 보존한다. 판정 규칙이 달라진 실행은 인식 지표와 판정 지표를 분리해 해석한다.

저장소 루트에서 선택한 경로의 패키지와 모델을 준비한 환경으로 실행한다. `paddle-only`는 EasyOCR 없이 사용할 수 있다. 실행기는 자동으로 모델을 다운로드하지 않고, `RUN_TARGET` 값과 관계없이 로컬에서만 비교한다. 모델이 없으면 README의 명시적 설치·다운로드 명령을 먼저 사용한다.

macOS / Linux:

```bash
.venv/bin/python -m scripts.benchmark_ocr
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m scripts.benchmark_ocr
```

기본값은 `data/benchmarks/posters-v1.json`의 `tuning` 5장 × 전처리 3종 × 1회다. 웹과 같은 `rgb_2x`, 정규화 RGB 원래 크기의 `rgb_1x`, 회색조 확대 `gray_2x`를 비교한다. EXIF를 적용하지 않은 저장 픽셀 원본과 `rgb_1x`는 다르다. 모델·인식 설정은 모두 동일하다.

```bash
# 반복 측정: 각 이미지·입력 조합을 세 번, 각 작업에 최대 180초
.venv/bin/python -m scripts.benchmark_ocr --repeats 3 --timeout 180

# 다른 포스터 목록 또는 전처리 일부만 측정
.venv/bin/python -m scripts.benchmark_ocr --manifest data/benchmarks/posters-v1.json --variants rgb_2x rgb_1x
```

모든 추론과 결과 검증이 성공하면 종료 코드 0, 추론 실패·시간 초과·계약 위반이 있으면 1이다. 잘못된 CLI 인자나 매니페스트는 실행 전 코드 2로 중단한다. `completed`는 실행 완료이며 정확도 목표 달성이나 사람 승인을 뜻하지 않는다.

최종용 3장은 기본 명령으로 추론하지 않는다. 모델·설정 선택을 끝내고 최종 확인을 할 때만 `--split holdout`을 명시한다. 현재 최종용 라벨은 미작성 상태라 해당 분할을 실행해도 품질 점수는 나오지 않는다.

## 입력과 라벨

`data/benchmarks/posters-v1.json`은 이미지 경로·SHA-256·분할·선정 이유·라벨 경로를 고정한다. 경로는 프로젝트 상대경로이고 한국어 NFC/NFD 파일명을 처리한다. 다른 목록도 같은 스키마로 만들 수 있다. 원본 바이트 해시가 달라지거나 선택 이미지끼리 정규화 픽셀이 같으면 실행 전에 거절한다. 단순한 해시 중복 검사로 유사 디자인·같은 행사의 모든 중복을 찾아내는 것은 아니다.

`data/benchmarks/labels/*.json`에는 다음을 분리해서 보존한다.

- `regions`: 이미지에서 읽은 문자와 정규화 원본 픽셀 좌표. OCR 후 평가에만 쓴다.
- `fields`: 행사명·시작일·종료일·장소와 근거 영역 ID. `visible`은 초안상 판독 가능, `unresolved`는 미확정, `absent`는 실제 부재로 명시한 경우다. `unresolved`를 부재 정답으로 채점하지 않는다.
- `criteria`: OCR 이후 검수할 문구와 참조 판정. 오통과를 확인할 위반 기준도 별도로 둘 수 있다.
- `human_verified`, `review_status`, `reviewed_by`, `reviewed_at`: 라벨 승인 상태. 사람이 검토한 뒤에만 승인 상태를 변경한다. 승인된 라벨에는 확인자와 확인 시각이 필요하다.

현재 5장 라벨은 OCR을 실행하기 전 이미지로 작성한 **AI 시각 판독 초안**이다. `human_verified=false`, `needs_human_review`, `use_for_training=false`를 유지한다. 사람은 실행 폴더의 `label-preview.png`, 원본, 라벨 JSON을 대조해 문구·좌표·가시성·판정을 검토할 수 있다. 읽기 어려운 정보를 추측으로 채우거나 실행 결과에 맞춰 정답을 수정하지 않는다.

작업 프로세스는 불투명한 사례 ID 아래의 `input.png` 경로만 받는다. 행사명이 들어간 원본 파일명, 라벨 문자, 근거 영역, 기준은 OCR 호출에 전달하지 않는다. 라벨 박스를 탐지나 재인식 crop으로 사용하지 않는다. 비교 단계에서만 추출 결과와 라벨을 대조하고 검수 기준을 적용한다.

## 산출물

매 실행은 새 `artifacts/ocr-benchmarks/<UTC시각>-<임의ID>/`를 만든다. 이전 실행이나 웹 검수 이력을 덮어쓰지 않는다.

```text
report.json / report.md      실행 목록, 분할, 설정, 패키지·코드·모델 해시, 지표
p01/
  source.bin                원본 바이트 사본 (실제 포맷은 source.json 참고)
  source.json               원본 해시·크기·정규화 정보
  normalized.png            웹 화면과 같은 방향·흰 배경의 RGB 원본
  label.snapshot.json       해당 실행에서 사용한 라벨
  label-preview.png         사람 검토용 박스 미리보기
  rgb_2x-1/
    input.png               이번 OCR에 전달한 픽셀
    worker.json / worker.log 실행 시간·장치·실패 원인과 런타임 로그
    ocr.raw.json            입력 좌표 기준의 실제 원시 결과
    ocr.normalized.json     정규화 원본 좌표로 복원한 박스·다각형
    evaluation.json         문자·필드·검수 판정의 사후 대조
```

실패 시 생성할 수 없는 OCR·평가 파일은 생략한다. 시간 초과는 작업 프로세스를 종료하고 다음 조합으로 진행한다. 실패 횟수는 보고서에 남기며 정확한 값이나 통과로 대체하지 않는다. `.runtime`과 `artifacts`는 Git 제외 상태를 유지하고, 재현 도구·목록·초안·요약 보고서를 Git으로 공유한다.

## 지표 해석

| 지표 | 의미와 주의점 |
|---|---|
| 핵심 CER | 참조 영역별 편집 거리 합 / 참조 글자 수 합. 낮을수록 초안 문자와 가깝다. 공백·NFC만 정규화하며 문장부호는 유지한다. 삽입이 많으면 100%를 넘을 수 있다. |
| 영역 대응 | OCR 박스 중심점이 라벨 박스 안에 있으면 대응한다. 겹치는 라벨은 가장 작은 한 영역만 선택한다. 줄 분리·합침·주변 글자가 점수에 영향을 준다. |
| 필드 일치·누락 | 추출기의 4개 핵심 필드 중 `visible`만 분모에 넣는다. 추출 결과가 null이면 누락이다. 문자를 잘 읽어도 추출 규칙이 해석하지 못하면 필드 일치가 낮을 수 있다. |
| 오통과 | 참조 판정이 fail인 기준을 자동 pass로 처리한 수 / 참조 fail 기준 수. 위반 사례가 1개라면 일반적인 오통과율로 해석하지 않는다. |
| 확인 필요 | 자동 review 기준 수 / 전체 기준 수. 로고·잘림처럼 원래 사람이 확인할 항목도 포함한다. |
| 시간 | 조합마다 새 프로세스로 실행하며 모델 import·로드와 추론 시간을 분리한다. 추론값은 로드 후 첫 호출의 시간이다. 서버의 반복 요청 평균이나 p95가 아니다. |
| 메모리 | 해당 작업 프로세스의 최대 RSS. macOS/Linux에서는 기록하고 `resource`를 지원하지 않는 Windows에서는 null이다. 전체 GPU 메모리·시스템 최대 사용량과 다르다. |

초안과 승인 라벨의 지표를 따로 집계한다. 실패 실행과 미라벨 입력은 품질 지표에서 제외하고 성공/실패 수와 평가 실행 수를 함께 표시한다. `--repeats`를 늘리면 필드·문자 분모도 **실행 횟수만큼** 늘어난다. 모델 선택에는 같은 사례·동일 라벨 스냅샷·동일 반복 수와 실제 장치를 맞춰야 한다.

EasyOCR 설정은 [공식 API 문서](https://jaided.ai/easyocr/documentation/)와 현재 런타임을 확인했다. 현재 기준선의 decoder는 기존 기본값인 greedy다. `beamsearch`, 영역 재인식, PaddleOCR, Qwen은 이 첫 측정의 비교 대상에 포함하지 않았다.
