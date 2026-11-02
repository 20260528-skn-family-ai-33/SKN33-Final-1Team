# 교정 라벨로 SigLIP2 분류층·전체 본체 다시 학습하기

기존 RunPod 환경, 다운로드한 SigLIP2 Base/384, 승인된 교정 학습 패킷을 사용합니다.
설치·다운로드 스크립트는 없습니다. 개발 과정에서 로컬 학습·테스트·문법 검사·RunPod 직접 실행을 하지 않았습니다.
실제 CUDA 동작과 성능은 사용자가 아래 명령으로 실행한 결과에서 확인합니다.

## 이번 비교의 목적

현재 교정 데이터 부분 파인튜닝 결과는 알려진 평가282셀 중238개 정답(84.40%), Macro F1 약0.7744,
p95 약34.14ms입니다. 원래 전체 파인튜닝은237개 정답(84.04%)입니다.
1개 판정 차이만으로 교정 효과를 확정하거나 효과가 없다고 단정하지 않습니다.

현재 부분 파인튜닝은 기존 전체 파인튜닝 체크포인트에서 시작해 일부 블록만 갱신한 결과입니다.
이번에는 교정 라벨을 다음 두 단계에 다시 반영합니다.

1. 기존 전체 파인튜닝 비전 본체를 고정하고 교정 train으로 라벨별 Logistic Regression 분류층을 다시 맞춥니다.
2. 원본 pretrained 비전 본체에서도 교정 train으로 분류층을 다시 맞춘 뒤,
   이 본체와 분류층을 동일한 출발점으로 전체 파인튜닝과 L2-SP 파인튜닝을 비교합니다.

기존 전체 파인튜닝과 이전 교정 데이터 부분 파인튜닝을 대조군으로 포함합니다.
실제 결과 파일의 모델 이름으로 각 경로의 정확도·지연시간·라벨별 오류를 구분합니다.
원본 pretrained 분류층 재적합은 전체/L2-SP 경로의 초기화를 위한 단계이며 기존 분류층 가중치를 그대로 복사하지 않습니다.

## 데이터와 평가 기준

- 기존 train113 / dev37 / test37의 이미지·그룹·분할을 유지합니다.
- 이번 패킷은 신규 이미지0장, 학습 라벨32셀 교정입니다. 기존 known 라벨 변경17셀과 모름 해소15셀로 나뉩니다.
  학습 known은861→876, unknown은43→28이며 총 이미지 수113장은 같습니다.
- 교정안은 AI 독립 교차검토 후 사용자가 학습 사용을 승인한 자료입니다.
  독립적인 사람 재라벨링이나 새 일반화 평가셋을 만든 것으로 보고하지 않습니다.
- 학습 패킷의 승인·파일 해시·알려진 라벨 마스크와 기존 완료 실험의 선택·체크포인트를 확인합니다.
  preflight가 누락된 의존 파일을 표시하면 해당 파일 경로부터 확인하며 설치를 반복하지 않습니다.
- optimizer에는 교정 train만 넣습니다. 모름은 손실에서 제외하고 class balance는 교정 train에서만 계산합니다.
- 분류층 C, checkpoint epoch, λ, 임계값은 개발셋에서 선택합니다.
  모든 경로의 선택을 고정하고 해시를 남긴 뒤 평가셋을 읽습니다.
- 개발셋 정확도·Macro F1가 이전 partial 이상이고, 악기 FN + 음식/동물/식물 FP 합이
  이전 partial 이하인 후보만 최종 선택 대상으로 둡니다. 없으면 이전 partial을 유지합니다.
  방법별 결과는 이 조건에서 탈락해도 숨기지 않고 각각 비교하며, 탈락 사유와 라벨별 변화도 기록합니다.
  L2-SP의 두 λ 중 보고할 후보는 개발 정확도·대상 오류·F1·AP 순으로 선택합니다.
- 같은 평가37장은 이미 여러 번 노출된 후속 비교 자료입니다. 평가 점수로 λ·학습률·seed를 다시 선택하지 않습니다.
  새로운 독립 일반화 정확도로 해석하지 않습니다.
- 목표90%를 넘으려면282셀 중 최소254개 정답이 필요합니다. 현재238개에서 **16개 판정**을 추가로 맞혀야 합니다.
  이번 실험이 이를 달성한다고 보장하지 않습니다.

## 기본 학습 조건

| 항목 | 설정 |
| --- | --- |
| 입력 | 기존384px 전체 포스터 letterbox, RGB/EXIF 및 기존 정규화 |
| 정밀도 | FP32, 기존 CUDA 환경, TF32 off |
| 분류층 | 교정 train 특징 위 라벨별 Logistic Regression, C=0.1/1/10 |
| 본체 학습률 | 3e-5 |
| 분류층 학습률 | 1e-3 |
| optimizer weight_decay | 1e-4, 전체/L2-SP 경로에 동일 적용 |
| 손실 | 모름 제외 BCE, 양성 가중치는 교정 train의 알려진 음성/양성 수로 재계산 |
| 학습 예산 | 최대8epoch, patience2, microbatch2 |
| λ 후보 | 0 / 0.01 / 0.1 |

λ=0은 추가 pretrained anchor 항만 끈 전체 파인튜닝 대조 경로입니다.
λ>0 두 후보 중 개발셋에서 선택한 L2-SP 경로를 별도로 비교합니다.
L2-SP는 원본 pretrained 비전 본체 파라미터를 기준으로 다음 항을 손실에 더합니다.

```text
L = masked_balanced_BCE + (λ / 2) × Σ ||θ_backbone − θ_pretrained||²
```

분류층은 L2-SP 합에서 제외합니다. 일반적인 zero-centered L2/weight decay가 파라미터를0으로 당기는 것과 달리,
L2-SP는 pretrained 파라미터에서 멀어지는 정도를 제한합니다.
optimizer의 weight_decay는 기존 전체 파인튜닝과 같은1e-4를 두 경로에 동일하게 적용합니다.
다른 학습 조건을 유지하고 pretrained anchor의 추가 효과를 비교하므로 λ=0에서도 weight decay는 켜져 있습니다.
L2-SP가 기존 모델의 약점을 자동으로 해결하거나 정확도를 올린다는 뜻은 아닙니다.

## 1. RunPod에 필요한 새 코드 반영

이전에 다른 브랜치와 갈라져 pull이 중단된 환경이라면 새 파일5개만 가져옵니다.
기존 가상환경·모델·결과 폴더 및 과거 프로토콜에 해시로 고정된 소스는 덮어쓰지 않습니다.

```bash
cd /workspace/SKN33-Final-1Team
git fetch origin dev/12_runpod
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/finetune_siglip_corrected.py \
  experiments/visual_classifier_benchmark/siglip_corrected_training.py \
  experiments/visual_classifier_benchmark/siglip_corrected_results.py \
  experiments/visual_classifier_benchmark/run_corrected_finetune.sh \
  experiments/visual_classifier_benchmark/CORRECTED_FINETUNE.md
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
```

RunPod가 이미 `dev/12_runpod`이고 로컬 코드 변경이 없다면 `git pull --ff-only origin dev/12_runpod`로 가져올 수도 있습니다.
`--ff-only`가 중단된 환경에서 강제 reset이나 다른 브랜치 병합을 반복하지 말고 위의 필요한 파일 반영 방식을 사용합니다.

## 2. 기존 환경에서 실행

```bash
bash run_corrected_finetune.sh train --resume
```

명령을 생략한 `bash run_corrected_finetune.sh --resume`도 train으로 처리합니다.
wrapper는 활성화된 프롬프트 이름에 상관없이 이 실험 폴더의 `.venv/bin/python`을 사용합니다.
다른 기존 benchmark Python을 사용해야 한다면 실행파일 경로를 `BENCH_PYTHON`에 지정할 수 있습니다.
새 가상환경을 만들거나 패키지를 설치하지 않습니다. Hub 접속도 차단합니다.

기본 입력/출력은 다음과 같습니다.

| 옵션 | 기본 경로 |
| --- | --- |
| `--packet` | `training_packets/siglip-ai-reviewed-v1` |
| `--previous-run` | `runs/siglip-ai-reviewed-v1-fp32` |
| `--prior-run` | `runs/siglip-full-fp32` |
| `--baseline` | `runs/heads-fp32` |
| `--model-dir` | `/workspace/models/siglip2-base-384` |
| `--run-dir` | `runs/siglip-corrected-v2-fp32` |

앞의 상대경로는 이 실험 폴더 기준입니다. 다른 이름으로 저장한 승인 패킷이나 이전 교정 실험을 사용했다면
`--packet`, `--previous-run`에 실제 경로를 함께 지정합니다.
입력·설정·소스가 바뀌면 기존 run에 이어 쓰지 않고 새 `--run-dir`를 사용합니다.
완료된 후보와 평가 단계는 `--resume`으로 재사용합니다. 학습 중간에 끊긴 후보는 그 후보의 초기화부터
다시 실행합니다. optimizer 상태를 복원하는 epoch 단위 재개는 지원하지 않습니다.

## 3. 결과 확인

```bash
bash run_corrected_finetune.sh show-results
```

- `comparison.csv`: 대조군과 각 학습 경로의 정확도·Macro F1·평균/중앙값/p95 및 목표 달성 여부
- `per_label_comparison.csv`: 라벨별 TP/FP/FN/TN과 오탐·누락 변화
- `error_changes.json`: 회복/퇴보한 이미지와 라벨
- `dev_candidates.csv`, `dev_per_label.csv`: 개발셋 후보 점수와 라벨별 선택 근거
- `selection_frozen.json`, `selection_commit.json`, `protocol.json`: 평가 전에 고정한 선택과 입력 해시
- 학습 데이터 요약/승인·체크포인트·epoch 이력: 실제 사용한 교정 데이터와 본체 학습의 증거

속도는 batch1 원본 디코딩·전처리·전송·비전 본체·분류층·출력 동기화를 포함합니다.
OCR·네트워크·DB·모델 로딩·학습·결과 저장은 시간에서 제외합니다.
학습/평가 실행이 완료된 뒤에만 새 정확도와 p95를 보고합니다. 기존 서비스 모델은 자동 교체하지 않습니다.
