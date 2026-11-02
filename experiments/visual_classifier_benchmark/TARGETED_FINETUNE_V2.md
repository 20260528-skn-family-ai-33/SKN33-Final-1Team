# SigLIP2 targeted v2: AP 정체에 의한 조기 종료 개선

v1은 epoch의 개발 AP가 `best_ap`보다 엄격히 커져야 체크포인트를 갱신하고 대기 횟수를 초기화합니다.
동점·하락은 모두 대기에 포함되며 기본 patience2라서 첫 epoch 이후 두 번 개선되지 않으면3epoch에서 종료합니다.
제공된 로그의 Attention은 AP가 같고 train BCE는0.13557→0.13207로 감소했으며,
LoRA는 AP가 소폭 하락했습니다. 이 로그는 기존 종료 조건을 충족했다는 증거입니다.
학습 train BCE 감소만으로 개발 성능도 개선 중이거나 더 오래 학습하면 좋아진다고 단정할 수는 없습니다.

## 변경한 판단 기준

| 항목 | v1 | v2 기본값 |
| --- | --- | --- |
| 최소 학습 예산 | 없음 | 8epoch |
| 최대 학습 예산 | 8epoch | 16epoch |
| 조기 종료 기준 | 개발 AP, patience2 | 개발 masked BCE, patience4 |
| 종료용 최소 개선 | 엄격한 AP 증가 | 개발 BCE가1e-5 이상 감소 |
| 체크포인트 | 최대 AP, 동점은 이전 epoch | 최소 개발 BCE,1e-7 이내 동점에서 AP 우선 |
| 초기 가중치 | 별도 후보 없이 첫 학습 epoch부터 선택 | epoch0 명시적 후보 포함 |

Attention은 알려진 개발 악기 셀의 BCE, LoRA 두 경로는 알려진 전체 개발 라벨 셀의 BCE를 사용합니다.
개발 평가는 증강 없이 수행하고 teacher KL은 개발 BCE에 넣지 않습니다.
FP32 확률을 FP64로 변환한 뒤 `[1e-7, 1-1e-7]`로 잘라 log loss를 계산합니다.
AP는 계속 기록하지만 더 이상 조기 종료를 결정하지 않습니다.

초기 가중치보다 나은 개발 BCE가 없으면 `best_epoch=0`, `selected_checkpoint_source=initialization`으로 기록합니다.
이때 학습이 실행됐더라도 선택된 가중치는 초기화 상태입니다. 학습 개선으로 보고하지 않습니다.
LoRA B의 마지막 norm, 마지막 epoch와 선택 epoch의 점수 변화, 초기/선택 개발 BCE를 별도로 저장합니다.

모델 구조·초기화·교정 데이터·학습률·LoRA rank·보존 손실 계수·증강·개발셋 임계값 정책과
최종 차량/악기 오류 guard는 v1과 같습니다. 보존 KL이 작았다는 이유로 계수를 동시에 바꾸지 않습니다.
이번 비교는 학습 예산과 개발 모니터 변경의 영향을 확인하기 위한 것입니다.
다른 라벨의 성능이 하락하는 새 모델을 자동 채택하지 않으며 기존 서비스도 교체하지 않습니다.

## 과거 실험 보존

완료된 `finetune_siglip_targeted.py`, 모델 모듈, v1 wrapper는 수정하지 않았습니다.
새 Python 실행기·wrapper·이 문서만 추가하므로 기존 실험의 소스 해시와 출력 파일을 보존합니다.
기존 v1 run에 `--resume`하지 말고 기본 새 경로 `runs/siglip-targeted-v2-fp32`를 사용합니다.
v2도 완료된 후보·평가는 재사용하지만, 중단된 후보의 optimizer 상태를 복원하는 epoch 단위 재개는 지원하지 않습니다.

## RunPod 코드 반영

다음은 **사용자가 RunPod 터미널에서 실행할 명령**입니다. 개발 과정에서는 로컬 검증·학습·추론·테스트·문법 검사나
RunPod 직접 실행을 수행하지 않았습니다. 설치와 새 모델 다운로드는 필요하지 않습니다.

```bash
cd /workspace/SKN33-Final-1Team &&
git fetch origin dev/12_runpod &&
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/finetune_siglip_targeted_v2.py \
  experiments/visual_classifier_benchmark/run_targeted_finetune_v2.sh \
  experiments/visual_classifier_benchmark/TARGETED_FINETUNE_V2.md
```

기존 v1 소스와 그 의존 파일은 그대로 필요합니다. 이전처럼 다른 브랜치와 갈라져 있어도
위 명령은 브랜치 전환·병합 없이 새 파일만 가져옵니다.

## 학습·평가

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_targeted_finetune_v2.sh train --resume
```

기존 `.venv/bin/python`, 모델 `/workspace/models/siglip2-base-384`,
교정 패킷 `training_packets/siglip-ai-reviewed-v1`, 시작 모델 `runs/siglip-ai-reviewed-v1-fp32`를 그대로 사용합니다.
v1에서 평가 점수가 높았던 모델을 골라 새 초기화로 사용하지 않습니다.
wrapper는 기존 `BENCH_PYTHON` 실행파일 override도 지원하며 설치하지 않습니다.

위 예산을 명시하려면 다음과 같이 실행할 수 있습니다. 기본 명령과 같은 설정입니다.

```bash
bash run_targeted_finetune_v2.sh train \
  --epochs 16 --min-epochs 8 --patience 4 \
  --min-delta 0.00001 --checkpoint-min-delta 0.0000001 \
  --run-dir runs/siglip-targeted-v2-fp32 --resume
```

개발셋으로 선택을 고정하고 해시를 저장한 뒤 기존37장 평가셋에서 정확도·p95·라벨별 오류를 측정합니다.
동일한 노출 평가셋의 후속 비교이며 독립 일반화 정확도를 뜻하지 않습니다.

## 결과 조회

```bash
bash run_targeted_finetune_v2.sh show-results
```

- `dev_candidates.csv`: 실제 학습 epoch, 선택 epoch, 선택 가중치 출처, 개발 BCE, 종료 사유와 최종 선택/탈락 사유.
- `candidates/*/history.json`: epoch별 train BCE/KL/가중 KL, 개발 BCE/AP, 대기 횟수, checkpoint 갱신과 점수 변화.
- `candidates/*/checkpoint_selection.json`: 초기·선택 개발 BCE, 중단 사유, 마지막/선택 epoch의 점수 변화.
- `initial_dev_probabilities.npy`, `last_dev_probabilities.npy`, `dev_probabilities.npy`: 초기·마지막·선택 상태의 개발 확률.
- `comparison.csv`, `per_label_comparison.csv`, `error_changes.json`: 기존84.40% 모델과 각 후보의 정확도·p95·차량 오탐·악기 누락.

목표는 정확도90% 이상, p95 50ms 이하입니다. 더 오래 학습하거나 모니터를 바꿨다는 사실만으로 목표 달성을 주장하지 않습니다.
