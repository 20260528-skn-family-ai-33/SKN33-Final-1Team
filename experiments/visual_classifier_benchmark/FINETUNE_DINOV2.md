# DINOv2-S/B 본체 전체 파인튜닝 비교

기존 benchmark `.venv`, 다운로드한 DINOv2-S/B와 SigLIP2 가중치, 완료된 세 실험의 파일을 그대로 사용합니다.
재설치·재다운로드·기존 모델 재학습 명령은 없습니다. 개발자가 로컬 학습 또는 RunPod 원격 실행을 대신 수행하지 않습니다.

필수 완료 폴더는 `runs/heads-fp32`, `runs/siglip-full-fp32`, `runs/dinov2-heads-fp32`입니다.
모델·환경·데이터·이전 소스 해시가 다르면 학습 전에 중단합니다. 해시로 고정된 기존 Python/Bash 소스는 수정하지 않았습니다.

## 무엇을 학습하는가

- 각 DINOv2의 원래 pretrained 본체와 완료된 frozen 실험의 개발셋 선택 분류층으로 시작합니다.
- **본체 전체 12개 Transformer 블록, 이미지 임베딩, 마지막 정규화층, 8라벨 분류층을 함께 학습**합니다.
  마스킹 입력 없이 쓰지 않는 pretrained mask token은 gradient가 없어도 정상이며 별도로 기록합니다.
- 기존 개발셋에서 고른 CLS 또는 CLS+평균 패치 pooling과392px letterbox를 유지합니다. 새 pooling 탐색은 없습니다.
- S/B 각각 학습률 `1e-5`, `3e-5` × BCE 양성 가중치 `none`, `train_balanced`를 비교합니다. 총8개 후보입니다.
- FP32, TF32 off, AdamW, 분류층 LR0.001, weight_decay0.0001, gradient clip1.0입니다.
  microbatch2를8장 단위로 누적하고 마지막 불완전 묶음도 실제 알려진 라벨 수로 정규화합니다.
- train113장만 optimizer에 넣고 모름 라벨은 손실에서 제외합니다. 양성 가중치는 학습 라벨만으로 계산합니다.
- 밝기·대비±0.08, 채도±0.05의 약한 증강만 사용합니다. 잘라내기/뒤집기 없이 전체 포스터를 보존합니다.
- 최대10epoch, 개발 Macro AP 개선이3epoch 없으면 조기 종료합니다. epoch≥1 중 최고 AP, 동률은 이른 epoch를 고릅니다.
- 체크포인트 선택 후 라벨별 임계값은 개발 F1 최대(0~1, 0.05 간격)입니다.
  각 크기의 후보는 개발 Macro F1 → 정확도 → Macro AP → 작은 LR → 무가중 순으로 선택합니다.
- S/B 추천은 개발 정확도 → Macro F1 → Macro AP → Small 순으로 고정합니다.
  본체 파인튜닝 모델을 실제 평가하며 epoch0 고정 본체로 자동 대체하지 않습니다.
- 첫 optimizer 단계에서 patch projection, 첫/마지막 블록, 마지막 norm, 분류층의 유한한 양수 gradient를 확인합니다.
  선택된 체크포인트에서는 12개 블록 각각과 임베딩·norm·분류층의 파라미터 해시 변화를 확인합니다.
- 모든 S/B 설정·체크포인트·임계값 선택을 고정한 뒤 같은 평가37장/알려진282개 라벨을 읽습니다.
  이미 노출된37장의 후속 비교이며 독립적인 새 일반화 정확도가 아닙니다.

## 1. 새 코드 반영

현재 RunPod는 이전에 다른 브랜치와 갈라졌으므로 필요한 새 파일 세 개만 가져옵니다.
완료된 실험에서 사용하는 `dinov2_compare.py`, `run_experiment.py`, `encoders.py`, `finetune_siglip.py`,
`../poster_benchmark/benchlib.py`는 기존 그대로 필요하며 새 소스가 추가로 요구하는 외부 패키지는 없습니다.

```bash
cd /workspace/SKN33-Final-1Team
git fetch origin dev/12_runpod
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/finetune_dinov2.py \
  experiments/visual_classifier_benchmark/run_dinov2_finetune.sh \
  experiments/visual_classifier_benchmark/FINETUNE_DINOV2.md
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
```

## 2. 학습·평가

```bash
bash run_dinov2_finetune.sh run --resume
```

`runs/dinov2-full-fp32`에만 새 결과를 씁니다. 기존 가상환경을 사용하고 Hub 접속은 차단합니다.
완료된 후보와 평가 모델은 해시 확인 후 재사용합니다. 중단된 후보는 해당 후보만 초기 가중치부터 다시 시작합니다.
완료된 run을 같은 명령으로 재실행하면 학습·추론을 반복하지 않습니다.
옵션/코드/환경 변경은 기존 run에 덮어쓸 수 없으며 새 `--run-dir`를 지정해야 합니다.
메모리 부족이 실제 발생했다면 다른 설정은 유지하고 `--microbatch 1 --run-dir runs/dinov2-full-mb1-fp32 --resume`으로 별도 실행합니다.

## 3. 결과 확인

```bash
bash run_dinov2_finetune.sh show-results
```

`comparison.csv`는 아래5개 모델의 정확도·Macro F1·평균/중앙값/p95·목표 달성 여부를 담습니다.

1. `siglip2_full_finetune`
2. `dinov2_small_frozen`
3. `dinov2_small_full_finetune`
4. `dinov2_base_frozen`
5. `dinov2_base_full_finetune`

다섯 모델 모두 이번 run에서 속도를 다시 측정합니다. 고정 DINOv2와 SigLIP2의 판정이 과거와 다르면 중단합니다.
batch1, 예열20회,37장×10회, 원본 디코딩/EXIF/letterbox/정규화/전송/본체/분류층/출력 동기화를 포함합니다.
OCR/네트워크/DB/모델 로딩/학습/저장은 시간에서 제외합니다. DINOv2는392px, SigLIP2는384px입니다.
목표는 알려진 라벨 정확도≥90%, p95≤50ms이며 보장하지 않습니다.

- `per_label_comparison.csv`: 크기별 frozen 대비 full, SigLIP2 대비 각 full의 TP/FP/FN/TN과 FP/FN 증감
- `error_changes.json`: 각 비교에서 회복/퇴보한 이미지·라벨
- `comparison_summary.json`: 개발셋에서 고른 후보·epoch·크기와 모델별 목표 달성 여부
- `selection_frozen.json`, `selection_commit.json`, `protocol.json`: 평가 전 선택과 입력 해시
- `candidates/<후보>/history.json`, `parameter_audit.json`: epoch별 개발 AP·손실, 실제 본체 업데이트 증거
- 각 평가 모델의 `test_predictions.json`, `test_metrics.json`, `timing_raw.csv`, `timing_summary.json`

로컬에서는 Python/Bash 문법과 CLI만 확인하며 모델 학습·CUDA 수치 검증은 수행하지 않습니다.
기존 결과 내보내기 도구에 이 새 run은 자동 포함되지 않습니다. 별도 다운로드가 필요하면 완료 후 다음처럼 묶습니다.

```bash
tar -czf /workspace/dinov2-full-fp32-results.tar.gz -C runs dinov2-full-fp32
```
