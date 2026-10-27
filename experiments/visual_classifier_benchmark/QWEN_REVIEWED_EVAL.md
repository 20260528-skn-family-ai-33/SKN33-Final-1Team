# 사람이 검토한 72건 요구사항 평가

이 실행은 사람이 72건을 모두 검토한 JSON을 명시적으로 제출한 뒤에만 시작할 수 있다.
기존 합성 40건의 결과나 가중치는 변경하지 않고, 고정된 B-4 + 검증 실패 최대 1회
재요청 방식으로 별도 결과를 저장한다. 학습·설치·다운로드·배포는 하지 않는다.

## 검토 데이터 조건

- `qwen-eval-reviewed.json`의 72건이 모두 APPROVED여야 하고, 검토자와 각 승인 시각이 있어야 한다.
  이것은 제출자가 남긴 승인 기록이며 독립된 검토자 검증을 자동으로 보장하지 않는다.
- 코드에 동봉된 manifest의 문장별 해시, ID, 그룹과 원래 40건 해시를 대조한다. 문장 원문을
  바꾸면 새 검토와 manifest가 필요하다. NFC/공백 정규화 기준으로 원래 40건과 정확한 문장
  중복을 거부한다. 의미나 템플릿 유사성은 자동 판정하지 않는다.
- 검토자가 입력한 8개 라벨을 정답으로 쓴다. `?`/null은 불명으로 남기고 라벨 정확도와
  F1의 정답 분모에서 제외한다. 수정된 정답에 `label_counts` 같은 초안 집계가 남아 있어도
  신뢰하지 않고 다시 계산한다. 조건 구조·source·target·라벨 간 모순을 확인한다.
- UI에서 내보낸 `use_for_evaluation=false`는 자동 평가 미승인 상태를 뜻한다. `--confirm-reviewed`는
  사용자가 최종 검토한 파일을 이번 평가에 사용하겠다는 명시적인 실행 선택이다. 이 선택은
  미승인 문장이나 거짓 승인 기록을 허용하지 않는다.
- 72건은 기존 실험을 알고 작성한 합성 문장이다. 동일 문자열이 없다는 사실만으로 의미상 독립,
  실제 사용자 요청 대표성, 일반화 성능을 보장하지 않는다.

## 절차

작업 PC에서 `review.html`을 열어 라벨과 조건을 확인한 후 72건 모두 승인하고 검토자를 입력해
`qwen-eval-reviewed.json`을 다운로드한다. 화면에서 `?`로 승인한 항목은 계속 unknown으로 남는다.
파일은 사용자가 RunPod의 `/workspace/qwen-eval-reviewed.json`으로 업로드한다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_qwen_reviewed_eval.sh preflight \
  --review-file /workspace/qwen-eval-reviewed.json
```

preflight는 승인 기록·문장·정답 구조를 검사하지만 모델을 로드하거나 추론하지 않는다.
통과 후 별도 새 폴더에서 평가한다. 최초 추론을 모두 수집하고 나서 정답으로 점수를 계산한다.

```bash
bash run_qwen_reviewed_eval.sh run \
  --review-file /workspace/qwen-eval-reviewed.json \
  --reference-retry-run runs/qwen4b-requirements-retry-v1 \
  --run-dir runs/qwen4b-reviewed-72-v1 \
  --ram-policy clean-unmapped-file --max-input-tokens 4096 \
  --confirm-reviewed --resume &&
bash run_qwen_reviewed_eval.sh show-results \
  --run-dir runs/qwen4b-reviewed-72-v1
```

## 지표와 결과

- 8개 시각 라벨의 **요구 존재 여부**를 문장별로 비교한다. 금지·선택도 라벨 요구의 존재다.
  이미지에 실제 물체가 있는지 판단한 결과는 아니다.
- `label_accuracy`: 정답이 알려진 셀의 (TP+TN)/전체. 예측 미판정은 오답, 정답 `?`는 제외한다.
- `macro_f1_all_8`: 양성 정답이 미판정된 경우 FN으로 포함하고, 분모 0인 F1은 0으로 하여
  8개 라벨 평균을 낸다. 양성 정답이 있는 라벨만의 F1과 해당 라벨 수·제외 목록도 함께
  기록한다. 음성 미판정은 F1의 FP가 아니므로 coverage를 같이 봐야 한다.
- 필드별 TP/FP/FN/TN, 양성·음성·unknown 수, 미판정 수, covered_accuracy와 coverage를 보관한다.
  정답이 소수인 라벨은 수치가 불안정하다. 목표 표시는 정확도 90%와 8개 Macro F1 0.9 기준이며
  이 표시는 사용자 승인 기록에 대한 합성셋 결과일 뿐 실제 일반화 성능을 뜻하지 않는다.
- `comparison.csv`, `per_label.csv`, `per_case_label.csv`, `predictions.json`, `summary.json`을 저장한다.
  전체 조건 정확일치와 항목 F1도 보조로 표시하지만 8개 라벨 판정 정확도와 구별한다.
- 시간은 최초 추론·검증·필요한 재요청을 포함하고 모델 로딩·warmup·채점·파일 쓰기는 제외한다.
  평균·중앙·p95를 저장한다. 새 데이터 평가를 바탕으로 프롬프트를 바꾸면 같은 데이터는 더 이상
  독립 평가 용도로 사용할 수 없다.

사용자 요청에 따라 작성 중 로컬 검증·모델 실행·RunPod 직접 실행은 하지 않았다.
