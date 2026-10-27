# Qwen3.5-4B 요구사항 추출 개선

## B-4: 정보 target·수량·중복 조건 보강

`--prompt-profile 4b-compact-v4`는 B-3의 프롬프트에 정보 문구 target과 value의
구분, 명시되지 않은 수량 금지, 중복 조건 방지, 선택·금지 표현의 지침과 별도
문장 예시를 추가한다. B-3에서 확인된 원시 출력에 기반한 개발 실험이다.

- 정보 필드의 target은 null, 표시 요청 문구는 value에 넣는다. 인용된 지시는
  실행하지 않는다. 인용부호를 실제로 표시해야 하는 경우의 정책은 사람 검토가 필요하다.
- 수량이 명시되지 않으면 at_least 1을 추정하지 않고, 같은 수량 조건의 포함 표현을
  별도 조건으로 중복 생성하지 않는다. 서로 독립적으로 명시된 조건은 유지한다.
- 시각 조건은 각각 원문에 근거한 대상을 선택하며, 불확실한 정보는 검토 대상으로 둔다.
- JSON 배열과 객체를 닫도록 지시하지만, 유효한 JSON 생성은 보장하지 않는다.

새 output_mode는 `compact_balanced_assembled`다. 기존 v1~v3 프로파일, 원시 응답,
파서의 엄격 검증 및 채점은 보존한다. 코드가 모델 출력을 자동 수정하거나 대상·수량을
추론하지 않는다. 기존 평가 문장과 case ID를 예시로 삽입하지 않는다.
입력 토큰과 p95가 달라질 수 있으므로 함께 기록한다. 기존 run-dir를 재사용하지 않는다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_requirements_json.sh run \
  --models 4b --prompt-profile 4b-compact-v4 \
  --ram-policy clean-unmapped-file --max-input-tokens 4096 \
  --run-dir runs/qwen4b-requirements-compact-v4 \
  --allow-draft-labels --resume &&
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen4b-requirements-compact-v4
```

완료 후 저장 응답 비교는 다음 명령으로 수행한다. 재추론은 하지 않는다.

```bash
.venv/bin/python -B diagnose_requirements_json.py \
  --run-dir runs/qwen4b-requirements-compact-v3 \
            runs/qwen4b-requirements-compact-v4 \
  --model 4b --out reviews/qwen4b-compact-comparison-v4
```

기존 합성 40문장은 반복 노출된 사람 미검토 개발 초안이다. 여기에서 90%가 나와도
독립적인 일반화 성능으로 해석하지 않는다. 사용자 요청에 따라 로컬 검증·모델 실행과
RunPod 직접 실행은 하지 않았다.

## B-3: 시각 대상 지정 보강

`--prompt-profile 4b-compact-v3`는 B-2의 고정 키와 기존 엄격 채점을 유지하면서
시각 항목의 대상 지정 지침과 별도 예시를 추가하는 실험이다. B-2 조립 실패 8건 중
7건에서 시각 target이 null이었으며, 나머지 1건은 정의되지 않은 연산자였다.

- 시각 target은 해당 source에서 고른 비어 있지 않은 대상 문자열이어야 한다.
- 위치·색상은 영향을 받는 객체, 수량은 개수를 세는 객체를 target으로 사용한다.
- 여러 시각 조건은 각 조건의 대상을 따로 고른다. 정보 항목의 target은 null이다.
- 필수 대상이나 위치가 모호하면 추측하지 않고 needs_review에 남긴다.
- 금지 대상, 색상, 배치, 수량, 모호한 위치를 보여주는 별도 예시를 추가했다.

모델 응답의 null을 코드에서 보정하거나 대상 사전으로 추정하지 않는다. 새 profile의
output_mode는 `compact_target_assembled`이며 B-1/B-2의 출력과 저장 결과는 보존한다.
생성·채점 설정, 모델 가중치, 데이터와 RAM 정책은 유지한다. 이번 실험은 프롬프트와
예시를 함께 변경하므로 각각의 독립적인 기여도를 분리하지 않는다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_requirements_json.sh run \
  --models 4b --prompt-profile 4b-compact-v3 \
  --ram-policy clean-unmapped-file --max-input-tokens 4096 \
  --run-dir runs/qwen4b-requirements-compact-v3 \
  --allow-draft-labels --resume &&
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen4b-requirements-compact-v3
```

완료 후 기존 B-2와 저장된 응답만 비교한다.

```bash
.venv/bin/python -B diagnose_requirements_json.py \
  --run-dir runs/qwen4b-requirements-compact-v2 \
            runs/qwen4b-requirements-compact-v3 \
  --model 4b --out reviews/qwen4b-compact-comparison-v3
```

target null·기타 조립 실패와 새 회귀를 확인한다. 40문장은 사람 미검토 합성 초안이며
이미 여러 차례 사용한 개발 자료다. 90%의 실제 일반화 성능은 미측정이다.
사용자 요청에 따라 로컬 검증·모델 실행과 RunPod 직접 실행은 하지 않았다.

## B-2: 항목 키 5개 고정

`--prompt-profile 4b-compact-v2`는 B-1의 조건별 키 생략을 없애는 비교 실험이다.
B-1의 엄격 전체 일치율은 26/40(65%), 조립 완료율은 29/40(72.5%)였고,
항목 키 오류 10문장 모두 시각 대상 target이 빠졌다. 이 중 R015에는 필드 오류도
함께 있었다. 키 누락을 고쳐도 의미 오류가 자동 해결되는 것은 아니다.

모든 items 항목에 `source_id`, `field`, `operator`, `target`, `value`를 출력한다.
정보 항목의 target은 null, 존재 연산자의 value는 null이며 해당 키도 생략하지 않는다.
시각 대상, 문자 값, 수량은 모델이 원문에 근거해 채운다. type/source는 이전처럼
코드가 결정·복사한다. 누락된 target을 사전이나 정답에서 추정해 넣지 않는다.

```json
{"items":[{"source_id":"S1","field":"musical_instrument","operator":"forbidden","target":"바이올린","value":null}],"needs_review":[]}
```

B-1의 의미 판단 규칙과 네 예시의 입력·판단은 유지하고 예시의 null 키만 명시한다.
기존 profile은 보존하며 새 output_mode는 `compact_fixed_assembled`다.
고정 키·null 규약을 위반하면 조립 실패로 기록하고 기존 엄격 채점을 적용한다.
생성 설정, 원문 구간 처리, RAM 정책, 모델 가중치와 합성 정답은 변경하지 않는다.

기존 환경과 다운로드한 모델로 새 run-dir에 평가한다. 이전 결과 디렉터리를 재사용하지 않는다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_requirements_json.sh run \
  --models 4b --prompt-profile 4b-compact-v2 \
  --ram-policy clean-unmapped-file --max-input-tokens 4096 \
  --run-dir runs/qwen4b-requirements-compact-v2 \
  --allow-draft-labels --resume &&
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen4b-requirements-compact-v2
```

완료 후 저장 응답으로 B-1/B-2를 비교한다. 아래 명령은 재추론하지 않는다.

```bash
.venv/bin/python -B diagnose_requirements_json.py \
  --run-dir runs/qwen4b-requirements-compact-v1 \
            runs/qwen4b-requirements-compact-v2 \
  --model 4b --out reviews/qwen4b-compact-comparison-v2
```

키 누락 10문장의 회복·새 회귀, 엄격 전체 일치율, 조립 완료율, 금지·수량 재현율,
p95와 생성 토큰 수를 함께 확인한다. 같은 공개 합성 40문장의 개발 비교이며
사람이 검토한 독립 평가셋에서 90%를 달성했다는 의미는 아니다.
사용자 요청에 따라 로컬 검증·모델 실행과 RunPod 직접 실행은 하지 않았다.

## B안: 축소 출력과 코드 조립

`--prompt-profile 4b-compact-v1`은 같은 4B로 의미 필드만 생성하는 별도 실험이다.
가중치 학습, 설치, 규칙 기반 정보 추출(C안), 정답별 예외 처리는 포함하지 않는다.
모델은 모든 조건의 field/operator/target/value와 source_id를 선택한다.
코드는 field에서 type을 결정하고, 정보 target과 존재 연산자 value를 null로 채운다.
source는 선택한 ID의 원문을 그대로 복사하며 json.dumps로 최종 JSON을 직렬화한다.

원문은 공백을 제외한 각 물리적 행에 ID를 부여한다. 원문 오프셋을 보존하며 날짜,
따옴표, 문장부호를 기준으로 절을 임의 분할하지 않는다. 한 구간에 여러 조건이 있으면
복수 항목이 같은 ID를 사용할 수 있다. 500자를 넘는 행은 100자씩 겹치는 500자
창을 제공한다. 창 경계로 조건이 나뉠 수 있으며, 이는 여전히 평가 대상 한계다.
전체 구간을 순서대로 한 번의 모델 호출에 전달한다. 입력 한도 초과 시 잘라내지 않고 중단한다.

축소 출력 예시:

```json
{"items":[{"source_id":"S1","field":"musical_instrument","operator":"forbidden","target":"바이올린"}],"needs_review":[]}
```

정보 항목에는 target, 존재 연산자에는 value를 출력하지 않는다. type/source도 출력하지 않는다.
수량은 정수여야 하며 유효 source_id가 필요하다. 불필요한 키/중복 키/잘못된 타입을
자동 복구하거나 문장 의미를 규칙으로 수정하지 않는다. 최종 출력은 기존 parse/evaluate로
동일하게 채점하며 실패 문장도 전체 분모에 포함한다. source ID가 올바른 조건을
가리키는지는 의미적으로 보장되지 않으므로, source-valid를 의미 정확도로 해석하지 않는다.

이 실험은 모델 단독 완성 JSON 성능이 아닌 **4B + 코드 조립 파이프라인** 성능이다.
출력 인터페이스에 맞게 프롬프트도 바뀌므로 코드 조립만의 독립 인과 효과로 해석하지 않는다.
v2의 저장 결과를 A 기준으로 사용하고, B의 정답 증가와 회귀를 함께 확인한다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_requirements_json.sh run \
  --models 4b --prompt-profile 4b-compact-v1 \
  --ram-policy clean-unmapped-file --max-input-tokens 4096 \
  --run-dir runs/qwen4b-requirements-compact-v1 \
  --allow-draft-labels --resume &&
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen4b-requirements-compact-v1
```

완료 후 재추론 없이 A/B 비교:

```bash
.venv/bin/python -B diagnose_requirements_json.py \
  --run-dir runs/qwen4b-requirements-prompt-v2-ramfix \
            runs/qwen4b-requirements-compact-v1 \
  --model 4b --out reviews/qwen4b-compact-comparison-v1
```

저장 증거와 지표:

- 요청 캐시와 predictions.json의 raw는 모델이 출력한 축소 JSON 문자열이다.
- compact는 원시 축소 출력 검증, assembled는 코드가 조립한 최종 객체다.
- source_segments와 assembly_trace는 원문 ID/오프셋/코드가 채운 필드를 기록한다.
- parsed/result는 최종 객체에 대한 기존 검증·엄격 채점이다. 원시 축소 JSON을
  완성 JSON 계약으로 직접 채점해 0점으로 표시하지 않는다.
- comparison.csv의 output_mode=compact_assembled로 파이프라인 점수임을 명시한다.
- compact_json_valid_rate, compact_schema_valid_rate는 원시 출력의 형식 준수율이다.
  assembly_completed_rate와 accepted_output_rate는 조립/최종 형식·원문 검증 통과율로,
  정답률이 아니다. 규칙 추출은 rule_extraction_executed=false다.
- 전체 p95에는 구간 생성/입력 직렬화/토큰화/추론/출력 조립/검증을 포함한다.
  모델 로딩·warmup·정답 채점·파일 저장은 이전과 같이 제외한다.
- mean_input_tokens, mean_generated_tokens, reached_token_limit_requests도 기록한다.
  기존 저장 run에 없는 토큰 지표는 timing_raw.csv에서 조회할 수 있다.

기존 완료 결과는 보존하고 새 run-dir를 사용한다. 스크립트 의존성에
requirements_compact.py가 추가되었으므로 runner 및 진단 파일과 함께 반영해야 한다.
로컬 검증/모델 실행은 하지 않았다. 개발셋 밖의 90% 달성 여부는 미확인이다.

## v3: 명시 값 추출과 원문 인용 회귀 수정

`--prompt-profile 4b-v3`는 baseline/v2 규칙 뒤에 내용을 추가하지 않는 독립 프롬프트다.
v2의 기존 정답 회귀 9건은 명시 값의 missing_value 오판 6건, 위치 필드 오류 1건,
원문 공백/이스케이프 변경 2건이었다. 이를 다음 규칙과 별도 문장 예시로 다룬다.

- 비어 있지 않은 `항목명: 값`은 완전한 조건으로 추출한다.
- information target:null과 존재 조건 value:null은 정상 형식이며 값 누락이 아니다.
- 일부 값만 없으면 그 부분만 needs_review로 보내고 완전한 항목은 추출한다.
- 로고 존재는 logo, 로고 위치는 layout으로 검사 속성에 따라 필드를 정한다.
- source의 단위 붙임/공백/따옴표를 유지한다. 예시 JSON은 json.dumps로 직렬화한다.

v2의 인용문 시연에는 raw 문자열 안의 중복 역슬래시 작성 오류가 있었다.
이 오류가 실제 회귀를 얼마나 유발했는지는 별도 측정하지 않았다.
과거 prompt 재현을 위해 v2 문자열은 보존하고 v3에서는 수동 이스케이프를 사용하지 않는다.
정답·엄격 채점·모델 가중치·생성 설정과 RAM 정책은 유지한다.
protocol에는 선택한 prompt_profile도 기록한다. 코드/protocol 변경 후에는 새 run-dir를 쓴다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_requirements_json.sh run \
  --models 4b --prompt-profile 4b-v3 \
  --ram-policy clean-unmapped-file --max-input-tokens 4096 \
  --run-dir runs/qwen4b-requirements-prompt-v3 \
  --allow-draft-labels --resume &&
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen4b-requirements-prompt-v3
```

완료 후 저장 결과만 비교하는 명령:

```bash
.venv/bin/python -B diagnose_requirements_json.py \
  --run-dir runs/qwen-requirements-json-pilot-v1 \
            runs/qwen4b-requirements-prompt-v2-ramfix \
            runs/qwen4b-requirements-prompt-v3 \
  --model 4b --out reviews/qwen4b-prompt-comparison-v3
```

v3는 로컬 검증/모델 실행을 하지 않은 코드다. 동일한 공개 합성 문장으로 개선 중이며
정확도 향상은 미확인이다. 새 사람이 검토한 평가셋의 일반화 성능으로 해석하지 않는다.

## 오류 18건 분류

서로 겹치지 않는 주 분류이며, 한 사례에 부가 오류가 있을 수 있다.

| 주 분류 | 사례 | 건수 |
|---|---|---:|
| 대상 표현만 다름 | R009 R011 R013 R016 R022 | 5 |
| 스키마 위반 | R008 R021 R025 R026 R037 | 5 |
| 필드 오분류 | R014 R015 R028 R040 | 4 |
| 원문에 없는 번역 target | R018 | 1 |
| 검토 사유/불완전 요구 처리 | R034 R035 R036 | 3 |

R025는 필드와 연산자도 틀렸고 R008/R037은 원문 인용에도 문제가 있다.
금지 조건은 target 완전 일치까지 요구하는 기존 지표만으로 의미 해석 실패라고 단정하지 않는다.
캐릭터/사진 차이는 실제 검수에 중요할 수 있다. 현 계약은 핵심 명사와 source를
보존하지만 표현 방식의 별도 구조화는 지원하지 않는다. 계약 확장은 별도 단계다.

## 이번 변경 범위

- baseline 프롬프트와 엄격 채점, 합성 정답 40개는 유지한다.
- `--models 4b --prompt-profile 4b-v2`에만 추가 규칙과 별도 문장 예시를 적용한다.
- 원시 출력 자동 수정, 정답 완화, 재학습, 설치는 하지 않는다.
- 공개된 오류로 개선했으므로 같은 40개 결과는 개발 결과다.
- 새 사람이 검토한 문장에 대한 일반화 성능은 아직 확인되지 않았다.
- 사용자 요청에 따라 로컬 검증/모델 실행은 하지 않았다.

## 이후 RunPod 평가 명령(현재 단계에서 실행하지 않음)

기존 환경과 다운로드한 4B를 사용하고 새 출력 폴더에 저장한다.
추가 예시로 입력이 길어지므로 입력 한도만 4096으로 늘린다. 한도 확대가
기존 2048 한도에서 성공한 문장을 자르거나 실제 입력을 늘리지는 않는다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_requirements_json.sh run \
  --models 4b --prompt-profile 4b-v2 \
  --ram-policy clean-unmapped-file \
  --max-input-tokens 4096 \
  --run-dir runs/qwen4b-requirements-prompt-v2-ramfix \
  --allow-draft-labels --resume &&
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen4b-requirements-prompt-v2-ramfix
```

코드 변경 후 기존 run-dir로 resume하지 않는다. 기존 결과는 그대로 보존한다.
작업 브랜치는 `feat/qwen4b-requirements-improvement`다.
## 저장된 응답 진단

`diagnose_requirements_json.py`는 표준 라이브러리와 기존 채점 모듈만 사용한다.
모델 로딩, 추론, 학습, 다운로드 없이 완료된 run의 저장 응답을 읽는다.
기존 runner 및 run 산출물에는 변경을 가하지 않는다. 진단 코드는 기존 추론
protocol의 source 목록에 추가하지 않아 진단 도구 추가만으로 resume를 막지 않는다.

진단 입력인 protocol/predictions/metrics 파일만 완료 기록의 SHA-256과 대조하고,
저장된 strict 판정과 현재 판정 및 합계가 같은지 확인한다. 모델 가중치나
전체 run의 무결성 검사를 대신하지 않는다. 출력은 원본 run 밖의 새 폴더만 허용한다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
.venv/bin/python -B diagnose_requirements_json.py \
  --run-dir runs/qwen-requirements-json-pilot-v1 \
  --model 4b --out reviews/qwen4b-diagnostics-v1
```

개선 프롬프트 평가가 끝난 뒤에는 두 run을 함께 지정할 수 있다.
문장 ID·원문·정답 초안이 모두 일치해야 비교한다. 첫 run이 기준이며
엄격 전체 일치율 차이는 퍼센트포인트로 표시한다.

```bash
.venv/bin/python -B diagnose_requirements_json.py \
  --run-dir runs/qwen-requirements-json-pilot-v1 runs/qwen4b-requirements-prompt-v2-ramfix \
  --model 4b --out reviews/qwen4b-prompt-comparison-v1
```

출력:

- `comparison.csv` / `summary.json`: 엄격 점수, 과거 p95, 진단 지표, 입력 해시와 정책.
- `01-cases.csv` / `01-cases.json`: 첫 run의 사례별 결과와 실제 항목·차이·원문.
- `02-cases.*`: 두 번째 run을 지정했을 때의 결과.

### 지표 해석

- 동일 항목과 동일 source를 먼저 대응시킨다. 이후 같은 원문 인용에 남은
  정답/응답이 각각 하나일 때만 짝을 정한다. 배열 순서나 의미 추정으로 연결하지 않는다.
- source를 바꾸거나 동일 source에 여러 항목이 남으면 미대응으로 표시한다.
  `alignment_coverage`와 `unpaired_gold_items`를 반드시 함께 확인한다.
- `field/operator/value/target_agreement_on_aligned`는 **대응된 항목만** 분모로 삼는다.
- `target_only_mismatches`는 다른 필드가 모두 같고 대상 문자열만 다른 수다.
  동의어로 승인하거나 의미상 정답으로 인정한 수가 아니다.
- 금지·수량 diagnostic recall은 **전체 정답 금지·수량 항목**을 분모로 삼는다.
  대상 문자열은 제외하지만 필드 포함/미포함 결과를 각각 표시한다.
- 스키마가 틀린 문서도 원시 항목 진단에는 포함한다. 해당 문서의 엄격 점수는
  원래대로 실패이며, 개별 항목 유효성을 JSON에 표시한다. 진단 점수를 운영 정확도로 쓰지 않는다.
- `needs_review`는 원래 엄격 TP/FP/FN과 실제/기대 검토 사유를 보존한다.
- `historical_p95_ms`는 저장된 값이며 진단 중 다시 측정하지 않는다.

사용자 요청에 따라 이 진단 코드는 로컬 실행/검증하지 않았다.

## 파일 캐시로 인한 RAM 검사 차단

`--ram-policy clean-unmapped-file`은 기본 inactive-file 방식과 달리 active 목록의
파일 캐시도 잠재 회수량 추정에 포함한다. 총 파일 캐시에서 shared memory,
mapped file, dirty/writeback, unevictable 양을 뺀다. 겹치는 카운터를 중복해서
빼므로 보수적인 추정이며, 필요한 카운터가 없으면 캐시를 여유량에 포함하지 않는다.
커널 slab이나 익명 메모리는 여유량에 더하지 않는다.

호스트 MemAvailable과 조회 가능한 컨테이너 hard limit을 계속 적용한다.
4B 14GiB RAM/VRAM 사전 기준은 동일하다. 추정치이므로 실제 모델 로딩 성공이나
일정한 peak RAM을 보장하지 않는다. 캐시 강제 삭제, 프로세스 종료, 제한값 변경은 없다.
로그에 두 방식의 캐시 추정량, 선택 정책, 실제 빈 공간, 요구 RAM을 함께 출력한다.

RAM 정책은 실행 protocol에 기록한다. 코드와 protocol이 변경되므로 앞서 검사에
실패한 `qwen4b-requirements-prompt-v2` 폴더를 재사용하지 않고 위 `-ramfix` 폴더를 쓴다.
기존 완료 결과와 실패 기록은 보존한다. 이 수정은 로컬에서 실행/검증하지 않았다.
