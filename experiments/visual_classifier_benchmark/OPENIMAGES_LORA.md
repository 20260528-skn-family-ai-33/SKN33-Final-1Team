# Open Images 보강 후 SigLIP2 본체 LoRA 비교

사용자 제공 로그에서 악기 전용 Attention·분류층 학습은 16epoch를 완료했고 3epoch가 선택됐다.
일반 BCE는 84.7518% / p95 34.3689ms / 악기 FP2·FN6, 균형 BCE는 84.3972% / p95 35.5243ms / FP3·FN6이다.
개발셋 선택은 기존 12장 보강 Attention이었다. 이 수치는 사용자 제공 실행 결과이며 로컬에서 재현한 값이 아니다.

이번 실행은 새 패킷을 유지하면서 본체 마지막2개 블록의 q_proj/v_proj에 rank8 LoRA를 학습한다.
이미 완료한 악기 전용 Attention은 다시 학습하지 않는다. 기존 소스·완료 실행·환경을 바꾸지 않는 새 실행기다.

## 비교 대상

| 모델 | 이번 실행의 학습 | 역할 |
| --- | --- | --- |
| `siglip2_previous_partial` | 없음 | 기존84.40% 모델 |
| `siglip2_instrument_attention` | 없음 | 완료한 Open Images 헤드 실험에서 **개발셋으로 선택한** 모델을 가져옴 |
| `siglip2_lora` | LoRA와8라벨 분류층 | 새 데이터에 대한 본체 적응 효과 |
| `siglip2_lora_preserve` | 같은 LoRA·분류층과 예측 보존 손실 | 다른 라벨 회귀 억제 효과 |

평가 정확도가 가장 높았던 일반 BCE 모델을 자동 선택하지 않는다.
`--head-run`의 `selection_frozen.json`에 개발 선택으로 기록된 모델을 대조군으로 사용한다.
이전 실험에서 학습한 LoRA를 이어 학습하는 것은 아니다. 두 새 후보는 동일한 기존84.40% native 체크포인트에서 시작한다.

## 학습과 선택

- 데이터: `training_packets/siglip-openimages-v1`. 사용자 생성 결과는 추가327장(기존12장+새315장), 기존 수정32셀이다.
- 예상 구성: 학습440장 / 개발37장. 평가37장은 학습에 포함하지 않는다.
- 384px 전체 시야 letterbox와 기존 약한 색상 증강, FP32, microbatch2 / effective batch8을 유지한다.
- 기본 LoRA는 마지막2개 블록 q/v projection, rank8 / alpha8. LoRA와 분류층 학습률은 각각3e-5이다.
- 기본 최대·최소 epoch는 모두16이므로 두 후보 모두16epoch를 수행한다. 개발셋으로 선택한 epoch는 그보다 이르거나 명시적 epoch0 대체가 될 수 있다.
- 체크포인트는 기존 v2의 알려진 전체 개발 라벨 BCE 최소, AP 동률 해소 규칙을 따른다.
- 분류 손실은 기존 v2의 알려진 라벨만 사용하는 masked BCE다. 새315장의 미확인7라벨을0으로 채우지 않는다.
- 예측 보존 후보는 기존 모델에 대한 독립 Bernoulli KL을 가중치1.0으로 적용한다.
  **정답이 알려진 비악기 학습 셀에만 적용**하며 신규 이미지의 미확인 셀을 교사 정답으로 채우지 않는다.
- 다른7개 라벨의 점수와 판정은 LoRA로 변할 수 있다. 개발 정확도·F1, 차량·악기 FP/FN 및 음식·동물·식물 FP의 기존 보호 조건으로 선택한다.
- 임계값과 최종 모델은 개발셋으로 고정하고 그 후 기존 평가37장으로 정확도·AP·p95와 라벨별 오류를 측정한다.
- LoRA를 원래 projection 가중치로 병합한 뒤 병합 전후 개발 점수 일치와 변경 허용 파라미터를 확인한다.
  p95는 실제 런팟 측정값으로 판단한다. 새 독립 일반화 정확도 또는90% 달성을 보장하지 않는다.

## 작업 PC에서 Git 반영

신규 파일3개를 기존 `dev/12_runpod` 흐름으로 반영한다. 아래는 사용자가 실행하는 명령이다.

```bash
cd /Users/choejiheum/SK_AI/SKN33-Final-1Team &&
git add \
  experiments/visual_classifier_benchmark/finetune_siglip_openimages_lora.py \
  experiments/visual_classifier_benchmark/run_openimages_lora.sh \
  experiments/visual_classifier_benchmark/OPENIMAGES_LORA.md &&
git commit -m "feat(infra): Open Images 보강 SigLIP2 본체 LoRA 비교 추가" &&
git push origin dev/12_runpod
```

## RunPod 코드 반영

이전에 브랜치가 갈라졌으므로 새 파일3개만 가져온다. 기존 가상환경·데이터·완료 실험은 이 명령의 대상이 아니다.

```bash
cd /workspace/SKN33-Final-1Team &&
git fetch origin dev/12_runpod &&
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/finetune_siglip_openimages_lora.py \
  experiments/visual_classifier_benchmark/run_openimages_lora.sh \
  experiments/visual_classifier_benchmark/OPENIMAGES_LORA.md
```

## RunPod 학습·평가

완료된 `siglip-openimages-instrument-v1-fp32`와 동일 패킷을 입력한다. 추가 설치와 모델 다운로드는 없다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark &&
bash run_openimages_lora.sh train \
  --packet training_packets/siglip-openimages-v1 \
  --head-run runs/siglip-openimages-instrument-v1-fp32 \
  --run-dir runs/siglip-openimages-lora-v1-fp32 \
  --epochs 16 --min-epochs 16 \
  --tail-blocks 2 --lora-rank 8 --lora-alpha 8 \
  --lora-lr 3e-5 --head-lr 3e-5 \
  --preserve-weight 1.0 \
  --microbatch 2 --resume
```

```bash
bash run_openimages_lora.sh show-results \
  --run-dir runs/siglip-openimages-lora-v1-fp32
```

동일 코드·입력·설정이면 완료 후보를 재사용하고 완료 실행에서는 `already_complete`로 종료한다.
**중단된 후보의 optimizer 상태를 이어받지는 않으며, 해당 후보만 같은 초기 가중치에서 재시작한다.**
기존16epoch 실행을 반복하거나 기존 실행 폴더에 덮어쓰지 않는다.

## 결과

- `dev_candidates.csv`: 현재 실행 학습 여부·학습/선택 epoch·전체 개발 BCE·악기 BCE/AP·개발 FP/FN·선택 사유.
- `comparison.csv`: 정확도·Macro F1/AP·p95·전체 FP/FN·차량/악기 FP/FN.
- `per_label_comparison.csv`, `error_changes.json`: 기존84.40% 모델 대비 라벨별 변화와 변경 이미지.
- `instrument_positive_scores.csv`: 평가 악기 양성별 점수·임계값·여유값·TP/FN.
- 각 새 후보의 `history.json`, `checkpoint_selection.json`, `parameter_audit.json`: epoch별 BCE/AP·교사 KL·변경량·LoRA 병합 검증.

대조군은 현재 실행에서 학습0epoch로 기록한다. 과거의 선택 epoch는 원본 기록을 유지한다.
본체 학습이 수행됐더라도 개발셋이 epoch0을 선택할 수 있으므로 `selected_checkpoint_source`와 병합 변경 기록도 함께 본다.

## 실행 확인 상태

기존 코드와 계약을 읽어 새 실행기를 작성했다.
사용자 요청에 따라 새 코드의 로컬 학습·추론·구문 검사·테스트와 RunPod 직접 실행은 하지 않았다.
실제 GPU 실행 확인은 사용자가 위 명령을 실행한 로그로 확인한다.
