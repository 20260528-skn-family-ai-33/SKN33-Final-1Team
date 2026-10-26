# Qwen3.5-2B·4B 요구조건 JSON 추출 초기 비교

이미지8라벨 분류 다음에 진행하는 별도 텍스트 과제다. SigLIP2의84.40%와 여기서 나온 수치를 같은 정확도로 비교하지 않는다.
사용자 요구조건 원문에서 유형·필드·대상·조건·값·출처를 추출하고,2B와4B의 JSON 유효성·누락·불필요 추출·지연시간을 비교한다.
27B는 제외했다. 기존 환경·모델·데이터·이미지 실험 결과는 유지한다.

## 데이터와 해석 범위

현재 확인한 코드의 요구조건 처리는 `frontend/src/workflow.js`에서 줄 단위 분리와 `information/visual` 분류를 수행한다.
이번 실험은 그 웹 기능을 바꾸지 않는 별도 CLI다. 현재 backend의 `Item` 형식은 `id/text/type/source/selected`다.
조건·값 등의 추가 필드를 운영 API에 바로 저장하거나 기존 규칙 엔진이 실행할 수 있다고 가정하지 않는다.

`requirements_pilot_v1.json`은 이번에 작성한 **합성40건·AI 초안 정답**이다. 독립적인 사람 검수나 실제 사용자 정답셋이 아니다.
행사명·날짜·장소·주최/주관·문의·요금·문구,8개 객체 범주·로고·색·배치·화질, 금지·선택·수량·복합 조건·모호한 요청을 포함한다.
학습·임계값 보정·프롬프트 탐색은 하지 않는다. 이40건에는 독립적인 최종 평가셋이라는 의미의 train/dev/test 분할을 붙이지 않는다.

따라서 결과 이름은 `draft_exact_match_rate`, `draft_item_f1` 등의 **초안 일치도**다. 검증된 실제 정확도로 보고하지 않는다.
실행에는 이를 명시하는 `--allow-draft-labels`가 필요하다. 실제 한국어 요구조건과 사람이 승인한 정답으로 평가하는 것은 후속 단계다.
이번 결과만으로 자동 기준 확정이나 모델 배포를 하지 않는다.

## 조건과 평가

- 입력은 요구조건 텍스트만이다. 이미지·OCR·기존 정답은 모델에 전달하지 않는다.
- 출력은 `items`와 `needs_review`다. 각 조건은 `type/field/target/operator/value/source`를 가진다.
- `required/forbidden/optional`은 value가 null이고, 최소/최대/정확 수량은 정수다. 명시한 정보·문구는 원문 그대로의 문자열이다. 날짜를 임의의 ISO 형식이나 연도로 보완하지 않는다.
- source는 원문에 실제 존재하는 연속 인용이어야 한다. 문자열 target/value도 source 안에 있어야 한다. 한글 수사를 정수로 바꾸는 것은 허용하되 초안 정답으로 일치도를 평가한다.
- 모델 응답을 코드블록에서 꺼내거나 JSON을 수선하지 않는다. 중복 키·추가 필드·잘못된 유형·불완전 응답은 기록한다. 정답을 보고 재질문하지 않는다.
- JSON/스키마/출처 유효율은 전체40건을 분모로 계산한다. 실패 사례를 제외한 성공률만 보고하지 않는다.
- 항목 일치도는 유형·필드·대상·연산자·값의 multiset을 비교한다. 순서는 무관하지만 중복 조건은 불필요 추출로 계산한다. NFC/공백 이외의 동의어·의역·날짜/통화 정규화는 하지 않는 엄격한 문자 기준이다.
- 파싱/스키마 실패 문서는 완전 일치0이며 그 문서의 모든 초안 정답 항목이 누락이다. 출처가 틀린 항목도 정답으로 인정하지 않는다. 유효 항목을 식별할 수 없는 JSON에서 허위 FP 개수를 만들지는 않으므로 항목F1만 보지 말고 문서 일치율/스키마 유효율을 함께 본다.
- `draft_forbidden_recall`은 금지 조건의 전체 tuple 일치 재현율, `draft_count_recall`은 수량 조건의 전체 tuple 일치 재현율이다. 단순 부정어/숫자 인식 정확도와는 다르다.
- 미정/모호한 요청의 `needs_review`도 source·reason을 비교한다. 수치가 낮으면 원문과 초안 정답을 함께 사람이 확인한다.

## 자원과 기존 환경

공식 [2B 파일](https://huggingface.co/Qwen/Qwen3.5-2B/tree/main)은 약4.57GB,
[4B 파일](https://huggingface.co/Qwen/Qwen3.5-4B/tree/main)은 약9.34GB로 합계 약14GB다.
두 모델을 동시에 GPU에 올리지 않는다. BF16·SDPA·단일 GPU, 기존 benchmark `.venv/bin/python`을 사용한다.
2B는 여유 VRAM/RAM 각각8GiB,4B는 각각14GiB를 보수적인 사전 조건으로 확인한다. 이는 실제 peak 사용량 보장이 아니다.
처음 다운로드할 때 모델 볼륨에는 두 모델의 남은 예상 크기와 추가2GiB 여유가 필요하다.
설치·업그레이드·양자화·CPU offload·최적화 커널 설치는 하지 않는다. 자원이 부족하면 중단한다.

## 1. RunPod에서 코드 받기

```bash
cd /workspace/SKN33-Final-1Team &&
git fetch origin dev/12_runpod &&
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/evaluate_requirements_json.py \
  experiments/visual_classifier_benchmark/requirements_json_contract.py \
  experiments/visual_classifier_benchmark/requirements_json_schema.json \
  experiments/visual_classifier_benchmark/requirements_pilot_v1.json \
  experiments/visual_classifier_benchmark/run_requirements_json.sh \
  experiments/visual_classifier_benchmark/prune_visual_weights.py \
  experiments/visual_classifier_benchmark/REQUIREMENTS_JSON_PILOT.md
```

## 2. 요청했던 불필요한 실험 가중치 정리

디스크가 여전히 가득 찬 경우에 진행한다. 삭제 대상은 **완료된 다른 SigLIP/DINOv2 실험 run의 `.safetensors`**와
명시한 미사용 다운로드 폴더4개 안의 safetensors/pytorch_model.bin 파일뿐이다.
이미 정리가 끝나 필요한 여유 공간이 확보됐다면 이 단계를 건너뛰고3번으로 진행한다. plan/apply를 반복할 필요가 없다.

아래 두 run은 전체를 보존한다.

- `runs/siglip-ai-reviewed-v1-fp32`: 교정·부분 파인튜닝84.40% 원본. 선택된 가중치 해시와 정확도를 확인한다.
- `runs/siglip-poster-v2-mix-fp32`: 현재 Qwen 비교가 참조하는84.40% 모델 복사본과 전체 비교 근거.

`/workspace/models/siglip2-base-384`와`qwen3.5-9b`, 기존 Qwen run도 보존한다.
기존 환경·데이터·JSON·CSV·로그·npy/npz·작은 분류기 등의 비대상 파일은 삭제하지 않는다.
진행 중인 run의 잠금이 있으면 중단한다. 코드가 자동으로 학습을 종료하지 않는다.
Linux NFS에서 배타 잠금을 얻으려면 기존 `.lock`을 쓰기 가능한 `r+`로 열어야 한다. 읽기 전용 `r`는 EBADF가 날 수 있다.
기존 잠금 파일은 생성·초기화·내용 쓰기 없이 사용하며, 잠금을 생략하거나 무시하지 않는다.

먼저 목록·확보 예상량만 기록한다. 이 단계는 삭제하지 않는다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
.venv/bin/python -B prune_visual_weights.py plan \
  --plan reviews/weights-cleanup-v1/plan.json
```

보존 대상과 확보량이 표시된다. 기존 plan은 덮어쓰지 않으며 새 계획이면 다른 plan 경로를 지정한다.
요청한 범위의 가중치를 실제 삭제하려면 같은 plan으로 실행한다.

```bash
.venv/bin/python -B prune_visual_weights.py apply \
  --plan reviews/weights-cleanup-v1/plan.json

df -h /workspace
```

선택 모델/메타데이터/대상 파일이 계획과 달라지면 중단한다. 대상 파일만 unlink하고 plan·receipt에 크기·SHA256·삭제 목록을 남긴다.
중단 후 같은 apply는 receipt와 남은 파일을 확인해 재개할 수 있다. checksum 목록은 삭제된 가중치 자체의 백업이 아니다.
**삭제한 과거 모델의 재추론/학습 재개/전체 artifact hash 검증은 불가능할 수 있다.** 해당 CSV·JSON·로그는 그대로 남는다.
현재 SigLIP/Qwen 비교의 보존 폴더와 원본 모델은 유지하므로 이번 새 비교의 입력으로 사용한다.

## 3. 기존 환경 확인 후 모델만 다운로드

```bash
bash run_requirements_json.sh check-env &&
bash run_requirements_json.sh download --models 2b 4b
```

모델은 `/workspace/models/qwen3.5-2b`와`qwen3.5-4b`에 저장한다. 이미 완료한 다운로드는 SHA256 검증 후 재사용한다.
중단된 다운로드는 같은 명령으로 고정 revision을 이어받는다. 모델 디렉터리가 다른 경우 모든 명령에 같은 `--models-root`를 지정한다.
모델이 이미 존재하지만 이 실험 manifest가 없다면 임의로 채택하거나 덮어쓰지 않는다.

## 4.2B·4B 초기 비교

```bash
bash run_requirements_json.sh run \
  --models 2b 4b \
  --run-dir runs/qwen-requirements-json-pilot-v1 \
  --allow-draft-labels \
  --resume
```

모델별 예열2회 뒤40건×1회를 실행하고 GPU 모델을 해제한 후 다음 모델을 로딩한다.
최대 입력2048토큰·출력768토큰·non-thinking·greedy다. 입력은 자르지 않고 예산 초과 시 중단한다.
기본은 초기 비용을 줄인1회/건 측정이며 반복 안정성은 측정하지 않아 빈 값으로 기록한다.
반복 측정이 필요하면 `--repeats 3`과 별도 새 run-dir을 사용한다. 조건 변경 시 기존 결과를 덮어쓰지 않는다.
`--resume`는 동일 조건의 완료 요청/모델을 재사용한다. 학습과 optimizer는 없다.

## 5. 결과 확인

```bash
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen-requirements-json-pilot-v1
```

- `comparison.csv`: JSON/스키마/출처 유효율, 초안 문서·항목 일치도, 금지·수량 재현율, mean/median/p95.
- `<2b 또는4b>/per_case.csv`: 각 원문의 성공/실패와 누락/불필요 항목 수.
- `<model>/errors.json`: 원문·초안 정답·응답 원문·실제 구조·누락·불필요 추출 목록.
- `<model>/requests/*.json`: 각 요청의 원문 응답·토큰 수·파싱 오류·시간.
- `<model>/frontend_drafts.json`: 기존 Item 필드에 맞춘 **selected:false** 초안과 별도의 구조화 조건. 이 전체 파일을 운영 API 본문으로 바로 제출하지 않는다.
- `protocol.json`, `status.json`: 데이터·모델 revision/해시·프롬프트·환경·실제 완료 상태.

p95에는 토큰화·GPU 전송·전체 JSON 생성·디코딩·출처/형식 검증을 포함한다.
다운로드·모델 로딩·예열·정답 비교·파일 쓰기·OCR·네트워크·DB는 제외한다.
기존 이미지 분류50ms SLA를 이 JSON 생성 과제의 측정 완료 목표로 간주하지 않는다. 별도 실제 측정값으로 판단한다.

코드 제공 단계에서는 사용자 지시에 따라 로컬 구문 검사·테스트·모델 로딩·추론, RunPod 접속·삭제·다운로드·실행을 하지 않았다.
실제 정리량·GPU 호환·추출 품질은 위 RunPod 실행 결과로 확인한다. 코드·합성 정답의 실제 실행 검증도 아직 수행하지 않았다.
