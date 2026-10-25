# 라벨 검토·승인 데이터 보강과 SigLIP2 부분 파인튜닝

이 실행기는 기존 SigLIP2 전체 파인튜닝의 **개발셋 선택 체크포인트**에서 시작합니다.
기존 `.venv`, `/workspace/models/siglip2-base-384`, 원본113/37/37 분할과 결과 폴더는 유지합니다.
설치·새 모델 다운로드·RunPod 원격 실행·서비스 모델 교체는 자동 수행하지 않습니다.
새 코드의 로컬 테스트는 합성 데이터/작은 CPU 모델 검증이며 실제 GPU 정확도90%·p95 50ms 달성을 뜻하지 않습니다.

## 1. RunPod에 코드 반영

아래 명령은 **기존 실험이 실행되던 RunPod 터미널**에서 사용합니다.
`dev/12_runpod` 브랜치라면 일반 `git pull --ff-only origin dev/12_runpod`로 반영할 수 있습니다.
앞선 실행처럼 다른 브랜치라면 아래 파일을 함께 가져오면 브랜치 전환 없이 실행할 수 있습니다.

```bash
cd /workspace/SKN33-Final-1Team
git fetch origin dev/12_runpod
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/prepare_partial_data.py \
  experiments/visual_classifier_benchmark/finetune_siglip_partial.py \
  experiments/visual_classifier_benchmark/run_partial_finetune.sh \
  experiments/visual_classifier_benchmark/resultlib.py \
  experiments/visual_classifier_benchmark/PARTIAL_FINETUNE.md
```

기존 `finetune_siglip.py`, `finetune_siglip_focused.py`, `run_experiment.py`, `encoders.py`,
`experiments/poster_benchmark/benchlib.py`와 기준 manifest는 이전 실험에서 사용한 동일 파일이어야 합니다.
새 도구는 이 파일들의 해시와 기존 환경을 검사하며 자동으로 교체·재설치하지 않습니다.

## 2. 검토 화면과 CSV 만들기 — 학습 시작 없음

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_partial_finetune.sh review --out reviews/partial-v1
```

- `reviews/partial-v1/train_review.html`: 원본 학습113장의 이미지와 현재 라벨입니다.
- `train_corrections.csv`: 원본 학습 라벨 수정용입니다. 처음에는 전부 `UNREVIEWED`입니다.
- `additional_labels.csv`: 새로운 원본 포스터를 추가하기 위한 빈 CSV입니다.
- `dev_review.html`, `dev_diagnostics.csv`: 기존 모델의 개발37장 오탐·누락 진단입니다. 학습 라벨 수정 양식과 분리합니다.
- `review_context.json`: 원본 묶음 SHA256과 라벨별 양성·음성·모름 수입니다.
- 평가37장의 라벨·이미지는 검토 화면에 넣지 않습니다. 기존 평가 파일을 학습으로 옮기지 않습니다.

HTML은 `previews/` 폴더의 이미지와 함께 열어야 합니다. 작업 PC에서 검토하려면 **PC 터미널**에서 폴더를 복사할 수 있습니다.

```bash
scp -r -P 22024 -i ~/.ssh/id_ed25519 \
  root@63.141.33.152:/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/reviews/partial-v1 \
  /Users/choejiheum/Downloads/
```

주소/포트는 마지막 제공된 RunPod SSH 정보입니다. 변경된 Pod라면 실제 접속 정보로 바꿉니다.

## 3. 사람이 CSV 검토·승인

CSV는 UTF-8 형식으로 저장하고 헤더 이름과 순서를 유지합니다.

| 파일/항목 | 작성 방법 |
| --- | --- |
| `train_corrections.csv`의 `original_*` | 원본 증거이므로 수정하지 않습니다. |
| `new_person` 등8개 `new_*` | 빈칸=기존 라벨 유지, `0`=없음, `1`=있음, `?`=모름으로 변경합니다. |
| 추가 CSV의8개 라벨 | `0`/`1`/`?`로 작성합니다. 빈칸도 모름으로 처리하며, 이미지마다 알려진 라벨이 하나 이상 있어야 합니다. |
| `approval` | 검토 완료 자료만 정확히 `APPROVED`. 제외는 `REJECTED`, 미검토는 `UNREVIEWED`입니다. |
| `reviewer`, `reason` | 승인한 사람과 수정/추가 근거를 반드시 기록합니다. |
| 추가 자료 `image_path` | CSV가 있는 폴더 기준 상대경로(예: `new_images/poster001.png`) 또는 RunPod의 실제 절대경로입니다. |
| `group_id`, `source_id` | 원본 포스터의 관계를 식별할 그룹과 고유 출처입니다. 개발·평가 그룹을 사용할 수 없습니다. |
| 추가 자료 `split` | 정확히 `train`으로 작성합니다. |

악기는 실제 악기 형태가 확인되는 사진·삽화를 검토합니다. 음표·마이크·스피커만 있는 이미지를 악기 양성으로 바꾸지 않습니다.
음식·동물·식물은 오탐 원본을 확인하고, 비슷하게 보이지만 해당 객체가 없는 **새로운 학습 사례**를 사람 검토 후 추가합니다.
모델의 예측을 정답으로 자동 복사하지 않습니다.

작업 PC에서 CSV를 편집했다면 **PC 터미널**에서 수정 파일을 다시 올립니다.

```bash
scp -P 22024 -i ~/.ssh/id_ed25519 \
  /Users/choejiheum/Downloads/partial-v1/train_corrections.csv \
  /Users/choejiheum/Downloads/partial-v1/additional_labels.csv \
  root@63.141.33.152:/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/reviews/partial-v1/
```

새 이미지가 있다면 `new_images/`도 같은 검토 폴더로 업로드합니다.

```bash
scp -r -P 22024 -i ~/.ssh/id_ed25519 \
  /Users/choejiheum/Downloads/partial-v1/new_images \
  root@63.141.33.152:/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/reviews/partial-v1/
```

## 4. 승인된 학습 패킷 생성 — 아직 학습 시작 없음

**RunPod 터미널:**

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_partial_finetune.sh build-data \
  --review-dir reviews/partial-v1 \
  --out training_packets/partial-v1
```

원본 데이터 파일은 수정하지 않고 승인된 수정/추가를 새 패킷에 저장합니다.
원본 개발37장의 ID·그룹·라벨은 그대로 유지하며 새 자료는 학습에만 들어갑니다.
파일 SHA256·RGB 픽셀·기본 지각 해시와 축소 영상으로 기존 자료/새 자료 사이의 복제·유사 복제를 검사합니다.
개발·평가 이미지도 **중복 지문 계산에만** 사용하며, 평가 정답은 읽거나 학습에 넣지 않습니다.
유사도 검사로 모든 변형을 완벽하게 구분할 수는 없으므로, 관련 이미지의 출처·그룹은 사람도 확인해야 합니다.
복제본이나 개발·평가 그룹 자료가 발견되면 패킷 생성을 중단합니다.

`approvals.json`과 `packet_manifest.json`에 승인 근거·원본/추가 파일 해시·수정 셀 수를 남깁니다.
미승인 수정, 승인자/사유 누락, 패킷의 사후 변경은 학습 전에 차단합니다.
기존 패킷은 덮어쓰지 않습니다. 다음 버전은 `partial-v2` 등 새 경로를 사용합니다.

## 5. 부분 파인튜닝과 같은 평가셋 비교

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash run_partial_finetune.sh train \
  --packet training_packets/partial-v1 \
  --run-dir runs/siglip-partial-v1-fp32 \
  --resume
```

승인된 추가 이미지와 실제 변경 라벨이 모두0이면 기본적으로 학습을 차단합니다.
동일 데이터에서 학습 범위만 비교하는 대조 실험을 의도할 때만 `--allow-unchanged-data`를 명시합니다.

기본 학습 조건:

- 기존 `runs/siglip-full-fp32`의 개발 선택 비전 타워/분류층에서 모든 후보를 동일하게 시작합니다.
- 분류층만 / 마지막2블록 / 마지막4블록을 비교합니다. 부분 본체 학습에서는 마지막 norm·pooler도 학습합니다.
- 분류층만 후보1개 + 본체 LR1e-6/3e-6 × 마지막2/4블록 = 총5개 후보입니다.
- 나머지 블록·embedding은 고정하며, 가중치가 실제로 동일하게 유지되는지 확인합니다.
- FP32, head LR1e-4, AdamW/weight decay1e-4, clip1, microbatch2/누적8장, 최대8epoch/개발 AP patience2입니다.
- 손실은 모름을 제외한 일반 BCE입니다. 이전 실험의 강한 대상 재가중은 사용하지 않습니다.
- 전체 포스터384px letterbox와 기존 약한 ColorJitter를 유지합니다. 임의 crop/oversampling은 없습니다.
- 후보 체크포인트는 개발 Macro AP, 임계값은 개발 라벨별 F1로 고릅니다.
- 최종 후보는 기존 개발 정확도/F1 이상·대상 오류 합 이하 조건을 통과해야 하며, 모두 실패하면 기존 모델을 유지합니다.
- 모든 선택을 고정한 뒤 같은 평가37장을 열고 기존 전체 파인튜닝과 선택 모델을 각각370회 측정합니다.
- 추론은 동일한 비전 타워+분류층384px 경로입니다. 정확도90%·p95 50ms 달성은 실행 후 확인합니다.
- 완료 후보/완료 평가를 `--resume`으로 재사용합니다. 미완료 후보는 원래 시작 가중치부터 다시 학습합니다.
- 서비스 모델은 자동 교체하지 않습니다. 개발셋 보호 조건도 평가셋의 개선을 보장하지 않습니다.

## 6. 결과 확인

```bash
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
cat runs/siglip-partial-v1-fp32/status.json
cat runs/siglip-partial-v1-fp32/training_data_summary.json
cat runs/siglip-partial-v1-fp32/comparison.csv
cat runs/siglip-partial-v1-fp32/per_label_comparison.csv
```

`selection_frozen.json`의 `source=prior`는 기존 모델 유지, `tail_blocks=0`은 분류층만 학습한 결과입니다.
`parameter_groups_changed`로 선택한 블록의 변경과 고정 블록의 보존을 확인할 수 있습니다.
실험 설정/데이터/코드를 바꾸면 새 `--run-dir`을 사용합니다.
평가37장은 이미 노출된 후속 비교이며 새로운 독립 일반화 추정치가 아닙니다.
기존3개 실험을 다루는 `export_results.py`는 기존 동작을 유지합니다. 이 부분 학습 실행의 결과는 위 새 폴더에 별도로 저장합니다.
