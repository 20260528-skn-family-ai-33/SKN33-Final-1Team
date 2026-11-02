# 조건별 Paddle 영역 재인식

로컬 `paddle-only` 포스터 검수는 전체 이미지 OCR 후, 조건 비교에서 확인되지 않은 제목·연도·영문 행사명에 한해 영역 재인식을 실행합니다. PDF 조건 추출 자체의 OCR에는 이 추가 단계를 적용하지 않습니다.

## 처리

1. 기존 전체 Paddle 탐지·한국어 인식을 그대로 실행하고 원시 결과를 보존합니다.
2. 미확인 조건의 항목 종류를 읽습니다. 제목은 큰 한글 OCR 영역, 연도는 크게 탐지된 4자리 숫자 영역을 선택합니다. 곡선 영문은 크게 탐지된 저점수 영문 영역과 흰 화소 성분의 원호 배치를 사용합니다.
3. 제목은 이웃 줄을 제거하고 흰 글자 마스크·세로 비율·획 보존을 적용합니다. 연도는 세로 비율을 보정합니다. 영문은 원호를 직선 띠로 펴서 한국어 모델과 구분된 영문 Paddle 인식 모델로 읽습니다.
4. 한 번의 격리 프로세스에서 언어별 모델을 한 번씩 로드하고 최대 8개 입력을 처리합니다. 현재 경로는 제목 3개·연도 2개·영문 2개로 최대 7회입니다. 작업자 제한 시간은 90초입니다.
5. 영역별 후보 중 인식 점수가 가장 높은 것을 표시합니다. 기대 문구와의 유사도로 후보를 선택하거나 글자를 보정하지 않습니다. 모델 점수는 정답 확률이 아닙니다. 실제 수원 탐색에서도 높은 점수의 오독이 있었습니다.
6. 원문·점수·좌표·변환·대안 후보를 보존하고 새 후보에는 `requires_review`를 설정합니다. 원본 대조 전 자동 확정하지 않습니다. 결과 화면에서 전후 문구와 원본 위치를 볼 수 있습니다. 원호 입력의 근거는 원본 픽셀의 곡선 다각형으로 복원합니다.

Paddle 작업자에 전달하는 manifest는 `id`, `path`, `language`만 허용합니다. 파일명은 일반 crop ID이며 조건의 기대 문자열을 전달하지 않습니다. 기준에 있는 연도를 읽지 못한 이미지의 관찰값으로 복사하지 않습니다.

## 준비 및 실행

기존 Paddle 환경·탐지·한국어 모델을 준비한 뒤 영문 인식 모델을 추가합니다. 런타임에서는 자동 다운로드하지 않습니다.

```sh
.venv/bin/python scripts/prepare_paddle.py --include-english
./run.sh
```

Windows는 `.venv\Scripts\python.exe`와 `run.cmd`를 사용합니다. 현재 작업 환경에는 영문 모델까지 준비했습니다. 영문 모델이 없는 환경에서는 해당 시도를 `missing_model`로 기록하며 준비된 한국어 재인식과 기존 OCR 결과는 유지합니다. 재인식이 실패·시간 초과하면 원래 OCR 기록으로 검수합니다.

기본 활성화이며 전체 이미지 기준선과 비교할 때만 서버 실행 전 `OCR_CONDITION_RETRY=0`을 설정합니다. 주입된 테스트 OCR은 명시적으로 재인식 함수를 지정하지 않으면 추가 모델을 실행하지 않습니다. 기존 `baseline`·`paddle`·`qwen` 경로의 의미를 바꾸지 않습니다.

## 실제 수원 검증

원본 2527×3572 JPEG의 전체 OCR부터 검수 API까지 실제 실행했습니다. 파일 SHA-256은 `e47812c5b322a5516b78618b2567e1c558b4bcca9f93006d599f8be89ba57c91`입니다.

| 대상 | 전체 OCR | 영역 재인식의 선택 결과 | Paddle 출력 점수 |
|---|---|---|---|
| 제목 | 수원재시게시밀 | 수원재즈페스티벌 | 0.979560 |
| 연도 | 2021 | 2026 | 0.961971 |
| 곡선 영문 | JAZ? / TESTIVA 등 조각 | 2026 SUWON JAZZ FESTIVAL | 0.969980 |

실제 전체 OCR 및 영역 재인식 7회까지 한 번 실행한 총 시간은 약 16.6초였습니다. 로컬 CPU·각 모델 4스레드이며 준비된 모델 로드 시간을 포함합니다. LLM은 사용하지 않았습니다. 기대 전사 파일은 추론 완료 후 평가 단계에서만 읽었습니다.

수원 포스터 1장에 맞춰 전처리 정책을 조정한 개발 검증입니다. 세 전사값이 맞았다는 결과를 일반 포스터 정확도나 자동 승인 성능으로 일반화할 수 없습니다. 흰 전경·원호형 배치, 기존 탐지 영역과 후보 글자 크기에 의존하므로 다른 배경색·복잡한 원근·임의 곡선·완전히 놓친 글자에는 적용되지 않을 수 있습니다. 여러 제목·여러 원호 중 일부만 선택될 수 있습니다. 기본 비교의 충족 6개·확인 필요 10개는 유지하며, 제목·기간·영문 항목에는 새 후보와 근거가 표시됩니다.

## 재현

아래 명령은 실제 모델을 실행하고 이미지·조건·코드 해시, 원시 OCR, crop, 변환, 결과를 기록합니다. `--expected`는 평가 전용이며 생략할 수 있습니다. 기존 실행 폴더를 덮지 않도록 새 출력 폴더를 사용하세요.

```sh
.venv/bin/python scripts/verify_paddle_conditions.py \
  output/pdf/수원재즈페스티벌_고해상도_새테스트_2026-10-29/01_수원재즈페스티벌_고해상도_2527x3572.jpg \
  --criteria .runtime/reviews/fb0f9054-9237-4f76-bbf3-c93ef76cb5cc/snapshot.json \
  --expected output/paddle-conditional-2026-10-29/expected-transcription.json \
  --output output/paddle-conditional-reproduction
```

검증 결과는 `output/paddle-conditional-2026-10-29/verification-final/transcription-checks.json`, 실제 API 응답은 같은 폴더의 `review-result.json`에 있습니다. `reviews/<id>/paddle-conditional/`에 모든 입력·계획·manifest·모델 식별자·원시 출력이 있습니다. 기존 사용자 검수 기록은 수정하지 않았습니다.

단위·API 테스트는 `backend/tests/test_paddle_conditions.py`에서 기대값 미주입, 후보 보류, 좌표·원호, 시간 초과 복구, 영문 모델 누락을 검사합니다. 이 테스트의 모의 모델 결과와 위 실제 추론 검증은 별개입니다.

공식 모델: [한국어 인식](https://huggingface.co/PaddlePaddle/korean_PP-OCRv5_mobile_rec), [영문 인식](https://huggingface.co/PaddlePaddle/en_PP-OCRv5_mobile_rec), [PaddleOCR Text Recognition](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/module_usage/text_recognition.en.md). 런타임은 `scripts/prepare_paddle.py`에 고정한 리비전·파일 해시로 검증합니다.
