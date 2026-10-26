# 악기 양성 점수 하락에 대한 손실·선택 기준 비교

기존 v2와 12장 보강 실행의 평가 악기 양성 7장은 모두 점수가 내려갔다. 누락 P052/P064/P093/P128/P130/P148은 동일하고 P171만 양성 판정을 유지했다. 이 결과는 단순 임계값 상승에 의한 현상이 아니다. 다만 하나의 반복 평가셋 결과로 클래스 불균형이나 특정 보강 이미지가 원인이라고 확정하지 않는다.

| 누락 이미지 | 보강 Attention 점수 / 임계값 | 여유값 | 보강 LoRA 점수 / 임계값 | 여유값 |
| --- | --- | --- | --- | --- |
| P052 | .235499 / .60 | -.364501 | .092089 / .55 | -.457911 |
| P064 | .422796 / .60 | -.177204 | .158501 / .55 | -.391499 |
| P093 | .075766 / .60 | -.524234 | .044992 / .55 | -.505008 |
| P128 | .219233 / .60 | -.380767 | .094141 / .55 | -.455859 |
| P130 | .208330 / .60 | -.391670 | .077461 / .55 | -.472539 |
| P148 | .550493 / .60 | -.049507 | .343580 / .55 | -.206420 |

임계값이 모두 .10 내려갔지만 양성 점수 자체도 하락했다. P148 Attention은 경계에 비교적 가깝고, 나머지 Attention 누락은 임계값에서 .177~.524 떨어져 있다. LoRA의 P064 점수는 .509292→.158501, P148은 .587571→.343580이다. 이 여섯 ID와 점수는 원인 분석 자료이며 아래 학습·선택 코드에 반영하지 않는다.

승인된 보강 패킷의 악기 학습 라벨은 양성31/음성88/모름6이다. 기존 v2는 일반 BCE와 개발 평균 BCE로 체크포인트를 골랐다. 이번 비교는 다른 7개 라벨 출력을 유지하면서 악기 양성과 음성의 기여를 맞추는 방식이 도움이 되는지 검증한다.

기존 수정 패킷은 양성28/음성79/모름6으로 클래스 비율 자체는 보강 전후 거의 같다. 이번 비교는 보강이 클래스 불균형을 크게 악화시켰다는 가정이 아니라, 기존 일반 BCE의 클래스별 기여 및 평균 BCE 중심 선택을 점검하는 대조 실험이다.

## 비교 대상

| 모델 | 현재 실행에서 학습 | 역할 |
| --- | --- | --- |
| `siglip2_previous_partial` | 없음 | 기존 84.40% 개발 선택 모델 |
| `siglip2_instrument_attention_v2` | 없음 | 완료된 `siglip-augment-v1-fp32` Attention을 가져와 재평가 |
| `siglip2_instrument_ap_control` | 일반 BCE | 새 체크포인트 선택 규칙만 바꾼 대조군 |
| `siglip2_instrument_balanced` | 클래스 균형 BCE | 같은 선택 규칙과 초기 가중치로 손실 가중치 효과 비교 |

새 학습 후보 두 개는 각각 고정16epoch를 받는다. AP 정체나 평균 BCE만으로 조기 종료하지 않는다. 매 epoch 개발셋에서 기존 보호 조건 통과 여부, 악기 FN, FP, AP, 클래스 균형 BCE 순으로 체크포인트를 고른다. epoch0을 명시적 대체 후보로 두며 불리한 학습 결과를 억지로 선택하지 않는다.

균형 가중치는 학습셋만으로 계산한다.

`w_pos = N_known / (2 * N_positive)`, `w_neg = N_known / (2 * N_negative)`

현재 패킷에서는 약 `1.9194 / 0.6761`, 상대 양성 가중치는 약 `2.8387`이다. 각 이미지의 BCE에 해당 가중치를 곱하고 실제 누적 배치 크기로 나눈다. 미검토 라벨이나 모름 라벨을 양성/음성으로 채우지 않는다. 오버샘플링·crop·추가 패키지·외부 다운로드를 사용하지 않는다.

원래 비전 본체와 8개 분류층은 고정한다. 기존 악기 전용 MAP probe와 악기 분류층만 학습한다. 추론은 기존과 같은 비전 본체 1회 및 추가 pooler 구조다. 개발·평가의 다른 7개 라벨 점수와 판정이 유지되는지 실행 중 확인한다. 실제 p95 충족 여부는 RunPod 측정으로 판단한다.

임계값은 기존 개발셋 기준으로만 고른다. 악기 개발 FP는 기준 모델 이하로 유지하며 그 안에서 FN을 최소화한다. 평가 이미지 ID나 평가 점수는 학습·체크포인트·임계값 선택에 사용하지 않는다. 모든 선택을 고정한 다음 같은 평가37장을 재사용한다. 새 독립 일반화 성능 추정은 아니다.

악기 한 라벨만 고치는 실험이므로 전체 정확도90%를 보장하지 않는다. 나머지 라벨 오류의 개선은 별도 데이터·실험이 필요하다.

## RunPod에서 Git으로 코드 반영

`dev/12_runpod`의 새 파일 세 개를 반영한다. 이전에 SCP로 받은 파일이 untracked 상태일 수 있으므로 이 세 파일만 stash로 보관한다. `.venv`, 모델, 학습 패킷, 실행 결과는 stash 대상에 넣지 않는다.

```bash
cd /workspace/SKN33-Final-1Team

git stash push --include-untracked -m "instrument-balanced-before-pull" -- \
  experiments/visual_classifier_benchmark/finetune_siglip_instrument_balanced.py \
  experiments/visual_classifier_benchmark/run_instrument_balanced.sh \
  experiments/visual_classifier_benchmark/INSTRUMENT_BALANCED.md

if ! git pull --ff-only origin dev/12_runpod; then
  git fetch origin dev/12_runpod &&
  git restore --source=origin/dev/12_runpod --worktree -- \
    experiments/visual_classifier_benchmark/finetune_siglip_instrument_balanced.py \
    experiments/visual_classifier_benchmark/run_instrument_balanced.sh \
    experiments/visual_classifier_benchmark/INSTRUMENT_BALANCED.md
fi
```

fast-forward가 가능하면 일반 pull로 반영한다. 기존 RunPod 체크아웃이 다른 브랜치에서 분기되어 pull이 실패하면, fetch한 원격 브랜치의 세 파일만 반영한다. 이 대체 경로는 브랜치를 합치거나 강제로 초기화하지 않는다. 보관한 이전 소스를 자동으로 stash apply하지 않는다.

## RunPod에서 실행

기존 `.venv`와 모델, 승인 패킷, 기존 실행 결과를 사용한다. 로컬에서는 문법검사·테스트·학습·추론을 실행하지 않았다. 아래 실행과 결과는 사용자가 RunPod에서 수행한 것이다.

사용자 제공 로그에서 `siglip-instrument-balanced-v1-fp32`는 이미 `complete`이다. Git으로 같은 코드를 반영한 뒤에는 우선 아래 결과 조회만 수행한다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_instrument_balanced.sh show-results \
  --run-dir runs/siglip-instrument-balanced-v1-fp32
```

미완료 실행을 이어갈 때는 다음 명령을 사용한다. 같은 입력·코드의 완료 실행에서는 `already_complete`로 종료하고 재학습하지 않는다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_instrument_balanced.sh train \
  --packet training_packets/siglip-augment-v1 \
  --source-run runs/siglip-augment-v1-fp32 \
  --run-dir runs/siglip-instrument-balanced-v1-fp32 \
  --resume
```

소스 v2 Attention은 재학습하지 않는다. 새 일반 BCE 대조군과 균형 BCE 후보만 학습한다. 중단된 실행의 완료 후보는 동일 입력·코드일 때 재사용하고, 미완료 후보는 동일 초기 가중치에서 시작한다.

`dev_candidates.csv`는 양성/음성별 BCE와 평균 점수·선택 epoch를 포함한다. `comparison.csv`와 `per_label_comparison.csv`는 정확도·p95·오탐·누락을 비교한다. `instrument_positive_scores.csv`는 모든 비교 모델의 실제 악기 양성 점수·임계값·여유값·누락 ID를 저장한다. 테스트 양성 수나 특정 ID를 코드에 고정하지 않았다. 모델은 자동 배포하지 않는다.

## 사용자 제공 RunPod 결과

완료 프로토콜은 `4add7cf5c4e16eac61cb852687d2378d3db0170e0092b72778289d2d1070be00`이다. 로컬에서 재현하거나 RunPod에 직접 접속해 확인한 결과는 아니다.

| 모델 | 개발 선택 epoch | 개발 악기 AP | 평가 정확도 | 평가 악기 FP/FN | p95 ms |
| --- | --- | --- | --- | --- | --- |
| 기존 partial | 7 | .821313 | 84.3972% | 3 / 6 | 33.692 |
| 보강 v2 Attention | 16 | .840402 | 84.3972% | 3 / 6 | 35.701 |
| AP 선택 대조군 | 15 | .837185 | 84.3972% | 3 / 6 | 34.446 |
| 균형 BCE | 10 | .837185 | 84.3972% | 3 / 6 | 34.015 |

개발셋에서 선택된 모델은 `siglip2_instrument_ap_control`이다. 두 새 후보는 개발 악기 FP를 1→0으로 줄였지만 평가 정확도·악기 누락은 개선하지 못했다. 균형 BCE의 개발 양성 평균 점수는 .6941로 일반 BCE 대조군 .6494보다 높았고, 임계값도 .65→.70으로 높아졌다. 정확도90% 목표는 미달, p95 50ms 목표는 충족이다.
