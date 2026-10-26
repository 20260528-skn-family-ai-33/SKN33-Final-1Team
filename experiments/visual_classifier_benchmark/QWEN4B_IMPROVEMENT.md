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
  --max-input-tokens 4096 \
  --run-dir runs/qwen4b-requirements-prompt-v2 \
  --allow-draft-labels --resume &&
bash run_requirements_json.sh show-results \
  --run-dir runs/qwen4b-requirements-prompt-v2
```

코드 변경 후 기존 run-dir로 resume하지 않는다. 기존 결과는 그대로 보존한다.
작업 브랜치는 `feat/qwen4b-requirements-improvement`다.
추가 진단 지표는 이번 변경에 포함하지 않으며 다음 단계에서 진행한다.
