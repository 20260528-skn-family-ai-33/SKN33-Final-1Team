# Qwen3.5-4B 요구사항 추출 개선

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
