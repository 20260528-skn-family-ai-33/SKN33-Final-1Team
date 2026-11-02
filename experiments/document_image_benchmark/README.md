# 문서 조건과 납품 이미지의 검수 평가

기존 명세서에서 출처가 연결된 JSON 조건을 준비하고, 이미지에서 관측한 내용과 대조한다. 원래 문서·이미지·모델을 수정하거나 재학습하지 않는다. 설치·다운로드·HTTP 서버 실행은 포함하지 않는다.

## 입력과 평가 범위

- 모닝컵 기존 가상 의뢰서·접수 이미지 1쌍/8조건.
- 기존 합성 딸기라떼·크루아상 v2·페스티벌 각 6쌍/30조건.
- 합계 **19쌍, 98조건, 4개 원본 그룹**. 독립적인 실제 고객 납품 평가셋이 아니다.
- 모든 정답은 기존 에이전트 답안 또는 출처에 근거한 초안이다. **사람 확정 정답은 0개**다. `human_verified` 지표는 null이며, `draft_agreement`만 초안 정답과의 일치도로 계산한다.
- 문서 추출 모델의 성능은 이 실험에 포함하지 않는다. 원문과 인용문을 보존한 구조화 조건을 입력으로 고정하고 **조건 대조 단계**를 평가한다.
- 김천 계약의 참고 이미지와 실제 납품 이미지를 혼동하지 않도록 기본 패킷에서 제외했다. 크루아상 v1/v2는 동일 원본 계열이므로 v2만 포함한다.

`packet/`은 문서·이미지·조건, `reviewer/gold.json`은 평가자 전용 정답이다. 관측/판정 함수에 gold를 전달하지 않는다. 공개 문서에 섞인 ‘정상 1개·위반 5개’와 정답 경로 안내는 조건 투영에서 제외했다. QA 조건의 기대 수량은 정당한 입력이지만 실제 관찰 수량·정답은 입력하지 않는다.

## 두 실행 방식

| engine | 처리 | 한계 |
|---|---|---|
| `siglip_ocr` | 기존 PaddleOCR CPU + 보존한 SigLIP2 FP32 384 + JSON 규칙 | 8개 대분류, 문구 및 파일 속성. 종류·수량·색·배치가 필요한 조건은 확인 필요 |
| `qwen` | 기존 Qwen3.5-4B 원본에 이미지와 해당 문서의 조건을 함께 입력 | 모델의 시각 판단이며 정확성 보장 없음. 기존 8라벨 yes/no LoRA는 이 새 과제에 적용하지 않음 |

파일 목록/크기/형식은 모델 추측 대신 실제 파일 근거로 비교한다. 원본 19장의 픽셀은 보존한다. SigLIP 경로는 기존 선택 모델의 384 letterbox 전처리이며 고해상도 조각 모델을 사용했다는 뜻이 아니다. OCR은 원본 방향 보정 이미지를 읽지만 기존 검출기의 최대 변 길이 1280 정책을 유지한다. Qwen 경로의 실제 입력 크기는 실행 근거에 기록한다.

원문에 지정된 위치·표시 횟수까지 확인되지 않았는데 문구가 있다는 이유만으로 전체 조건을 통과시키지 않는다. 빈 OCR은 부재 확정이 아니다. 임계값(.90 OCR, .10 visual margin)은 초기 운영 정책이며 이번 정답을 보고 최적화한 값이 아니다. 조건의 여러 검사 중 명확한 불일치가 있으면 AND 조건은 위반이다. 다른 하위 검사가 오류여도 그 오류는 `checks`와 별도 오류 수에 보존한다.

## 로컬에서 확인한 것

문법·JSON 스키마·원문 인용문·파일 SHA·정답 ID 연결, 모델 없는 단위 테스트 및 저장 관측 재생 경로를 확인한다. 실제 GPU 모델 로딩/추론, Paddle venv 동작, RunPod 성능은 이 준비 작업에서 실행하지 않는다.

## RunPod 실행 순서

아래 경로는 업로드한 압축파일을 `/workspace`에 푼 상태다. 기존 환경을 재설치하지 않는다. 같은 출력 폴더는 재사용하지 않으며 재실행은 `002` 등 새 이름을 쓴다. 모델 경로가 달라졌다면 해당 인자만 바꾼다.

1. macOS 터미널에서 준비된 `.tar.gz`를 기존 접속 주소에 업로드한다. 현재 포트가 달라졌으면 RunPod 연결 화면의 값을 쓴다.
2. RunPod 웹 터미널에서 압축을 푼다.

```bash
test ! -e /workspace/document-image-review-2026-10-29-v1 && tar --no-same-owner --no-same-permissions -xzf /workspace/document-image-review-2026-10-29-v1.tar.gz -C /workspace
```

3. 데이터와 정답 연결을 확인한다. GPU 모델을 불러오지 않는다.

```bash
/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/.venv/bin/python -B /workspace/document-image-review-2026-10-29-v1/code/run.py validate
```

4. SigLIP/OCR 경로를 사전 점검한다. 모델 로딩 없이 파일·설치된 패키지·모델 해시를 확인한다. CUDA 실제 실행 성공은 이 단계에서 보장하지 않는다.

```bash
/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/.venv/bin/python -B /workspace/document-image-review-2026-10-29-v1/code/run.py preflight --engine siglip_ocr
```

기본 OCR Python은 `/workspace/venvs/poster-paddleocr/bin/python`, OCR 모델은 `/workspace/SKN33-Final-1Team/.runtime/paddle-models`이다. 없으면 설치하지 않고 점검에서 중단한다. 이미 있는 경로를 `--ocr-python`, `--ocr-models`로 지정한다. 고정된 코드 의존성은 업로드 묶음의 `code/dependencies/`에 포함해 기존 repository 파일을 덮어쓰지 않는다.

5. 사전 점검을 통과한 뒤 SigLIP/OCR 평가를 실행한다.

```bash
set -o pipefail
PYTHONUNBUFFERED=1 /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/.venv/bin/python -B /workspace/document-image-review-2026-10-29-v1/code/run.py run --engine siglip_ocr --output /workspace/document-image-review-siglip-001 2>&1 | tee /workspace/document-image-review-siglip-001.log
```

6. Qwen 원본으로 같은 조건을 비교할 때는 앞 실행이 종료된 뒤 순서대로 실행한다.

```bash
/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/.venv/bin/python -B /workspace/document-image-review-2026-10-29-v1/code/run.py preflight --engine qwen
```

```bash
set -o pipefail
PYTHONUNBUFFERED=1 /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/.venv/bin/python -B /workspace/document-image-review-2026-10-29-v1/code/run.py run --engine qwen --output /workspace/document-image-review-qwen-001 2>&1 | tee /workspace/document-image-review-qwen-001.log
```

두 경로 모두 본인이 만든 OCR 자식 프로세스만 정리한다. 기존 로고 서비스·모델·다른 PID를 종료하지 않는다. 실행 중 중단은 해당 터미널에서 Ctrl+C를 누른다. 부분 결과는 최종 완료 결과와 구분되며 자동 학습 재개 기능은 없다.

7. 결과를 표로 함께 본다.

```bash
/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/.venv/bin/python -B /workspace/document-image-review-2026-10-29-v1/code/run.py compare-runs --runs /workspace/document-image-review-siglip-001 /workspace/document-image-review-qwen-001
```

## 재접속 후 로그 확인

위 평가 명령은 `tee`로 터미널 출력과 오류를 `/workspace/document-image-review-qwen-001.log` 또는 `/workspace/document-image-review-siglip-001.log`에 저장한다. 브라우저를 닫았다가 다시 접속하면 새 웹 터미널에서 기존 로그의 마지막 80줄과 이후 추가되는 출력을 확인한다. 평가 명령을 다시 실행할 필요는 없다.

```bash
tail -n 80 -F /workspace/document-image-review-qwen-001.log
```

SigLIP 로그는 다음 명령으로 확인한다.

```bash
tail -n 80 -F /workspace/document-image-review-siglip-001.log
```

새 터미널의 `tail`에서 Ctrl+C를 누르면 로그 보기만 종료한다. 로그 확인 명령은 모델을 실행하거나 평가를 재시작하지 않는다. `tail`은 평가가 끝나도 새 출력을 기다릴 수 있으므로 마지막 `결과 저장:` 및 `status.json`으로 완료를 확인한다. 로그가 멈췄다는 사실만으로 평가 완료나 중단을 판단하지 않는다.

브라우저를 닫는 것과 Pod 자체를 Stop/종료하는 것은 다르다. Pod 자체를 중지하면 평가 프로세스도 중단되며, 저장된 로그만으로 평가가 자동 재개되지는 않는다. 이 로그 조회 절차는 이미 실행 중인 평가의 접속 종료 후 생존을 보장하는 기능은 아니다.

## 결과 파일과 해석

- `run_manifest.json`: 조건 패킷·정답·코드 해시와 판정 정책.
- `observations.json`: SigLIP/OCR 관측. Qwen은 별도 raw 응답 근거를 남긴다.
- `predictions.json`, `conditions.csv`: 조건마다 충족/위반/확인 필요, 실행 상태, 관측 근거.
- `metrics.json`: 사람 확정 지표 / 초안 일치도 / 미라벨 조건 구분.
- `status.json`: 실행 완료/실패. 완료는 모델 성능이나 납품 승인을 의미하지 않는다.

정확도와 Macro F1은 **조건의 pass/fail/review 3종 판정**이다. 이전 이미지의 8개 존재 라벨 F1과 직접 비교하지 않는다. 3개 클래스 중 지표가 정의되지 않는 클래스는 0으로 두며, 평가 대상이 0개인 구획은 null이다. 오류·누락은 분모에 남고 실제 정답 클래스의 FN으로 센다. 자동 판정 비율·보류율·오류율·위반 검출 재현율·위반을 잘못 통과시킨 비율·보류/오류 포함 위반 미검출 비율을 함께 본다.

사람 검증은 이미지와 문서 원문을 확인한 뒤 `reviewer/gold.json`의 각 항목에 `verdict`, `human_verified:true`, `verified_by`, `verified_at`를 직접 기록한다. 문서 조건의 승인과 이미지 정답의 확인은 별개다. 근거 없이 검증 플래그를 일괄 변경하지 않는다. 정답 버전을 바꾼 경우 기존 결과는 보존하고 `score --predictions 기존결과/predictions.json --gold 새정답.json --output 새폴더`로 다시 계산한다. 모델을 재실행할 필요는 없다.

새 자료를 추가할 때는 동일한 조건/관측/정답 스키마와 원본 그룹을 유지한다. 보강 자료나 임계값 결정에 사용한 사례를 새 독립 평가 사례로 다시 부르지 않는다. 스키마 파일은 `code/schemas/`에 있으며 런타임에서는 추가 설치 없이 핵심 계약·해시 검증을 수행한다.
