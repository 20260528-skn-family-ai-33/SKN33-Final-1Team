# SigLIP2 악기 전용 Attention·LoRA 비교

기존 교정 데이터 부분 파인튜닝에서 개발셋으로 선택한 모델을 공통 출발점으로 사용합니다.
현재 제공된 기준 결과는 정확도84.40%, p95 약34ms입니다. 이 문서는 새 후보의 성능을 측정했다는 뜻이 아닙니다.
이번 개발에서는 로컬 학습·추론·테스트·컴파일·문법 검사와 RunPod 직접 실행을 수행하지 않았습니다.
실행기는 설치·모델 다운로드·서비스 교체·Git 변경을 자동 수행하지 않습니다.

## 비교 경로

| 모델 | 학습 범위 | 추론 |
| --- | --- | --- |
| `siglip2_previous_partial` | 기존 완료 모델, 다시 학습하지 않음 | 기존 비전 본체·8라벨 분류기 |
| `siglip2_instrument_attention` | 복제한 MAP pooler의 query와 악기용 선형 분류기만 | 본체1회 + 기존 pooler + 악기 pooler |
| `siglip2_lora` | 마지막4개 블록 Q/V의 rank4 LoRA 및8라벨 분류기 | LoRA 병합 후 기존 구조 |
| `siglip2_lora_preserve` | 동일 LoRA 설정 + 다른 라벨 예측 보존 손실 | 교사 없이 병합 모델만 |

악기 경로는 기존 본체·원래8라벨 분류기·나머지7개 라벨의 임계값을 고정합니다.
기존 비전 출력의 `last_hidden_state` 패치 특징을 읽는 별도 MAP query를 학습합니다.
복제한 pooler의 attention/MLP/norm 가중치는 고정하며, 원래 모델의 pooler도 수정하지 않습니다.
초기 query와 악기 선형층은 기존 가중치를 복사하고, 개발/평가에서 다른7개 판정이 같아야 완료 처리합니다.
다중 크롭·두 번째 비전 본체·OCR·텍스트 모델은 사용하지 않습니다. 작은 악기의 정보가384px에서 소실되면
query만 바꿔서 복원할 수 없다는 한계가 있습니다. 추가 pooler의 지연은 실행 후 p95로 확인해야 합니다.

## 기본 학습 설정

- 공통 입력:384px whole-poster letterbox, 원래 정규화, FP32, 원래 mild ColorJitter.
- 데이터: 승인된 `training_packets/siglip-ai-reviewed-v1`을 그대로 검증·사용합니다. 새로운 라벨/이미지를 만들지 않습니다.
- 원본 train113/dev37/test37 분할과 현재 패킷의 known/unknown 마스크를 유지합니다.
- optimizer: AdamW, weight_decay1e-4, gradient clipping1, 유효 배치8, microbatch2.
- 최대8epoch, patience2, 고정 seed20261022.
- 악기 경로: query와 악기 선형층 LR1e-3, 알려진 악기 라벨이 있는 train 행만 일반 BCE로 학습합니다.
  다른 라벨은 학습 손실로 사용하지 않습니다. 악기 unknown 행은 제외합니다.
- LoRA: 마지막4개 블록 Q/V, rank4, alpha4, LoRA LR1e-4, 분류층 LR1e-4, 일반 masked BCE.
  본체 원래 가중치·bias·norm은 고정합니다. B=0 초기화이므로 초기 예측은 기준 모델과 같아야 합니다.
- 보존 경로: 같은 초기화/학습률/데이터 순서에 `0.1 × mean Bernoulli KL`만 추가합니다.
  교사는 고정된 기준 모델이며 같은 증강 train 입력을 봅니다. 악기를 제외한 알려진 라벨 셀만 보존 손실에 넣습니다.
  교사 출력을 새 정답 라벨로 저장하거나 unknown을 교사 예측으로 채우지 않습니다.
- 학습 후 LoRA를 Q/V의 일반 weight에 병합합니다. 병합 전후 개발 확률 차이와 변경된 native parameter를 기록합니다.
  평가에 쓰는 임계값은 실제 병합 모델의 개발 확률로 선택합니다.

이 값들은 첫 비교용 설정이며, SigLIP2에서 더 좋은 정확도를 보장하는 최적값이 아닙니다.
기준 모델의 악기 오류9셀만 모두 고치고 다른 라벨이 그대로라면238/282→247/282(87.59%)이므로
악기 경로 하나만으로90% 목표를 달성할 수는 없습니다.

## 개발셋 선택

- 악기 경로 checkpoint: 개발 악기 AP, LoRA 경로 checkpoint: 개발 Macro AP. 동점이면 이른 epoch.
- 임계값 후보:0~1, 간격0.05 및 기존 임계값. 악기는 기존 개발 FP 이하에서 FN을 최소화합니다.
- 차량은 기존 개발 FP와 FN을 모두 넘지 않는 후보 중 정확도를 최대화합니다.
  만족하는 후보가 없으면 정확도 기준 결과를 보고하되 최종 선택 guard에서 탈락합니다.
- 나머지 라벨은 개발 정확도, 동점이면 F1과 기존 임계값과의 거리를 사용합니다.
- 최종 후보는 개발 정확도·Macro F1가 기준 이상이어야 합니다. 차량·악기 FP/FN과 음식·동물·식물 FP도
  기준보다 늘면 제외합니다. 만족하는 새 후보가 없으면 기존 모델을 유지합니다.
- 탈락한 경로도 비교표에 보고합니다. 후보별 탈락 사유와 라벨별 임계값 감사표를 저장합니다.
- 선택과 파일 해시를 고정한 뒤 같은 exposed37 평가셋을 읽습니다. 평가 점수로 설정을 다시 고르지 않습니다.
  이 결과는 새 독립 일반화 평가가 아닙니다.

## RunPod에서 사용자가 실행할 명령

**RunPod 터미널**에서 새 파일4개를 가져옵니다. 이전처럼 다른 브랜치와 갈라진 환경에서도
브랜치 전환 없이 반영할 수 있습니다. 기존 소스·가상환경·모델·실험 폴더는 유지합니다.

```bash
cd /workspace/SKN33-Final-1Team &&
git fetch origin dev/12_runpod &&
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/finetune_siglip_targeted.py \
  experiments/visual_classifier_benchmark/siglip_targeted_models.py \
  experiments/visual_classifier_benchmark/run_targeted_finetune.sh \
  experiments/visual_classifier_benchmark/TARGETED_FINETUNE.md
```

이전 corrected-v2 실험에서 사용한 `finetune_siglip_corrected.py`, `siglip_corrected_results.py`와
기존 benchmark 소스는 그대로 필요합니다. 누락되면 실행기가 파일 경로를 알려주고 중단합니다.
RunPod가 이미 `dev/12_runpod`이고 로컬 변경이 없다면 `git pull --ff-only origin dev/12_runpod`도 가능합니다.

학습·평가 실행:

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_targeted_finetune.sh train --resume
```

wrapper는 기존 `.venv/bin/python`만 사용합니다. 다른 기존 Python 실행파일은 `BENCH_PYTHON`으로 지정할 수 있습니다.
소스 파일/패킷/완료 기준 실험이 없거나 환경이 다르면 중단하며 자동 재설치하지 않습니다.

| 옵션 | 기본 경로 |
| --- | --- |
| `--baseline` | `runs/heads-fp32` |
| `--prior-run` | `runs/siglip-full-fp32` |
| `--previous-run` | `runs/siglip-ai-reviewed-v1-fp32` |
| `--packet` | `training_packets/siglip-ai-reviewed-v1` |
| `--model-dir` | `/workspace/models/siglip2-base-384` |
| `--run-dir` | `runs/siglip-targeted-v1-fp32` |

완료된 후보·평가는 `--resume`으로 재사용합니다. 중간에 끊긴 후보는 그 후보의 초기화부터 다시 실행하며
optimizer 상태를 복원하는 epoch 단위 재개는 지원하지 않습니다. 설정/데이터/코드가 바뀌면 새 run 폴더를 사용합니다.

완료 후 조회:

```bash
bash run_targeted_finetune.sh show-results
```

- `comparison.csv`: 기준과3개 후보의 정확도·Macro F1/AP·p95·총FP/FN.
- `per_label_comparison.csv`, `error_changes.json`: 기준 대비 라벨별 오류 및 바뀐 판정.
- `dev_candidates.csv`, `dev_per_label.csv`: 개발 선택/탈락 사유와 라벨별 점수.
- `candidates/*/threshold_audit.csv`: 개발셋 임계값별 FP/FN/정확도와 선택 지점.
- `candidates/*/history.json`, `parameter_audit.json`: 실제 최적화 범위·보존 손실·LoRA 병합 기록.
- `selection_frozen.json`, `selection_commit.json`, `protocol.json`: 평가 전 선택과 코드/데이터 해시.

속도는 기존과 같은 batch1 디코딩·전처리·전송·비전·분류기·출력 동기화 범위입니다.
OCR/네트워크/DB/모델 로딩/학습/파일 저장 시간은 제외합니다. 목표는 라벨 정확도90% 이상 및 p95 50ms 이하입니다.
