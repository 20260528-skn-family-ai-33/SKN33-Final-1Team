# 악기 누락·음식/동물/식물 오탐 개선 실험

기존 환경과 모델 파일을 그대로 사용합니다. 설치·다운로드 명령은 없습니다.
`finetune_siglip.py`, `encoders.py`, `run_experiment.py`, 승인 데이터와 기존 결과는 수정하지 않습니다.
이번 코드는 사용자가 RunPod에서 실행할 때 학습합니다. 코드 작성만으로 정확도90% 또는 p95 50ms 달성을 뜻하지 않습니다.

## 바뀐 학습 방식

- `runs/siglip-full-fp32/selection_frozen.json`이 가리키는 **개발셋 선택 체크포인트**에서 시작합니다. 평가 결과로 체크포인트를 다시 고르지 않습니다.
- 비전 타워 전체와8개 라벨 분류층을 이어 학습합니다. 텍스트 타워는 사용하지 않습니다.
- 미확정 라벨은 손실·임계값 선택·지표에서 제외합니다. 학습113장/개발37장/평가37장과 그룹 경계를 유지합니다.
- 기본 BCE는 모든 known 라벨에 유지합니다. 악기 양성은 `1 + boost*(1-p)^2`, 음식·동물·식물 음성은 `1 + boost*p^2` 가중치를 곱합니다. `p`의 가중치 계산 경로는 gradient에서 분리합니다.
- 따라서 놓치기 쉬운 악기와 잘못 검출하기 쉬운 음성의 gradient가 더 커집니다. 학습 minibatch의 현재 예측만 사용하며, 개발/평가 오답을 학습에 넣지 않습니다.
- mild boost2/strong boost4 × 본체LR1e-6/3e-6 =4개 후보입니다. 가중치는 각각1~3/1~5 범위입니다. head LR1e-4, FP32 AdamW, weight decay1e-4, clip1, microbatch2/누적8장입니다.
- 각 epoch에 학습 이미지를 한 번씩 봅니다. oversampling이나 자동 라벨 변경은 없습니다. 전체 포스터384px letterbox와 기존 약한 ColorJitter를 사용하며, 작은 악기가 잘려도 양성 라벨이 남는 crop은 사용하지 않습니다.
- 최대6epoch, 개발 Macro AP가2회 연속 개선되지 않으면 종료합니다. 후보별 체크포인트는 개발 Macro AP 최대로 고릅니다.
- 각 체크포인트의 개발 예측에서 기존 F1 임계값과 라벨별 F-beta 임계값을 비교합니다. 악기는 F2로 재현율, 음식·동물·식물은 F0.5로 정밀도를 중시하고 나머지는 F1을 사용합니다. F-beta 임계값 후보는.05~.95/.05 간격입니다.
- 기존 모델의 원래 임계값과 개발셋에서 다시 고른 임계값도 함께 후보로 둡니다. **개발 정확도·Macro F1이 기존 이상이고, 악기FN + 음식/동물/식물FP 합이 기존 이하인 후보만** 선택할 수 있습니다. 정확도 → 대상 오류 수가 적은 순 → Macro F1 → Macro AP 순으로 고르며, 동점이면 기존 모델/원래 임계값을 우선합니다. 모두 탈락하면 기존 모델을 유지합니다.
- 이것은 개발셋에서의 보호 조건입니다. 평가셋 개선을 보장하지 않으며, 평가셋의 개선/퇴행을 모두 저장합니다.
- 선택한 모델·임계값·SHA256을 고정한 뒤 같은37장 평가셋을 엽니다. 이전 파인튜닝 모델과 선택 모델을 같은 환경에서 각각 batch1, warmup20회,37장×10회로 비교합니다.
- 추론 구조는 그대로 단일384px 이미지 → 비전 타워 → 분류층입니다. 학습용 가중치/F-beta 계산은 추론에 들어가지 않습니다. p95는 이미지 decode·전처리·전송·모델·분류층 시간을 측정하며 OCR/API/네트워크/DB/모델 로딩은 제외합니다.

## 실행 전 필요한 기존 결과

`runs/heads-fp32`와 `runs/siglip-full-fp32`가 완료되어 있어야 합니다. 후자의 선택 체크포인트와 개발 예측 파일도 필요합니다.
스크립트는 원본 파일 SHA256, 기존 실행의 환경·모델·분할을 검사합니다. 불일치하면 중단하며 패키지를 자동 재설치하지 않습니다.

## RunPod 실행

사용자가 로컬 변경을 commit/push하고 RunPod에 해당 코드를 반영한 다음 실행합니다.
현재 RunPod가 다른 브랜치에 있었으므로 `git pull`만으로 브랜치 충돌이 해결된다고 가정하지 않습니다. 아래 실행에는 세 새 파일 `finetune_siglip_focused.py`, `run_focused_finetune.sh`, 이 문서가 기존 코드 옆에 있어야 합니다.
실행 스크립트는 프롬프트의 활성 venv와 관계없이 기존 벤치마크 `.venv/bin/python`을 사용합니다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_focused_finetune.sh --resume
```

다른 경로에 있는 **동일한 기존 벤치마크 환경**을 사용했다면 새 환경을 만들지 말고 경로만 지정합니다.

```bash
VISUAL_CANDIDATE_VENV=/기존/벤치마크/venv bash run_focused_finetune.sh --resume
```

기본 결과 경로는 `runs/siglip-focused-fp32`입니다. 이전 두 실행 폴더는 덮어쓰지 않습니다.
같은 코드/데이터/환경/옵션의 `--resume`은 완료 후보를 재사용합니다. 실행 전체가 완료됐으면 무결성 확인 후 종료합니다.
중간에 중단된 **미완료 후보는 epoch 중간부터가 아니라 원래 시작 체크포인트부터 다시 학습**합니다.
코드/설정을 바꾼 실험은 새 `--run-dir`을 지정해야 합니다. 같은 출력 폴더에서 두 프로세스가 동시에 실행되면 lock으로 중단합니다.

## 결과 확인

학습을 다시 실행하지 않는 읽기 명령입니다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
cat runs/siglip-focused-fp32/status.json
cat runs/siglip-focused-fp32/selection_frozen.json
cat runs/siglip-focused-fp32/comparison.csv
cat runs/siglip-focused-fp32/per_label_comparison.csv
cat runs/siglip-focused-fp32/siglip2_focused/test_metrics.json
```

- `selection_frozen.json`: `source=trained`면 새 학습 가중치를 선택한 것입니다. `source=prior`면 기존 가중치를 유지하며 `threshold_policy`로 원래/개발 재선택 임계값을 구분합니다. `source=prior`는 새 학습 모델의 성능 향상으로 보고하면 안 됩니다.
- `comparison.csv`: 이전 전체 파인튜닝과 선택 모델의 정확도·Macro F1·평균/중앙/p95 및90%/50ms 달성 여부입니다.
- `per_label_comparison.csv`: 각 라벨의 이전/선택 TP·FP·FN·TN과 차이입니다. `fp_delta`, `fn_delta`가 음수면 감소입니다.
- `error_changes.json`: 평가셋의 개선(`recovered`)과 퇴행(`regressed`) 이미지/라벨입니다. 이것으로 다음 설정을 고르면 같은 평가셋에 적응한 결과가 되므로, 독립 성능 검증에는 새 미노출 평가셋이 필요합니다.
- `candidates/prior/train_hard_cases.csv`: 기존 모델이 학습 이미지에서 놓친 악기 양성과 오탐한 대상 음성입니다. 사람의 라벨·작은 객체 확인에 쓸 수 있으며 라벨을 자동 수정하지 않습니다. 마이크·음표만 있는 이미지를 악기 양성으로 바꾸지 않습니다.
- `candidates/*/history.json`, `dev_probabilities.npy`, `candidate_complete.json`: 개발 선택 과정과 gradient/가중치 변화 검증입니다.

현재84.04%는282개 known 판정 중237개 정답입니다.90% 이상에는254개 이상 정답이 필요합니다.
같은 평가37장은 이미 노출되었으므로 후속 비교 수치이며 새로운 독립 일반화 추정치가 아닙니다.
