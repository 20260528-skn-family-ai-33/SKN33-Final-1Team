# DINOv2-S/B 비교 — 기존 RunPod 환경

기존 `.venv`와 승인된 train113/dev37/test37 패킷을 사용합니다. 설치 스크립트나 기존 학습을 다시 실행하지 않습니다.
코드는 로컬에서 작성하고 문법·CLI만 확인했습니다. 실제 모델 다운로드·학습·CUDA 검증은 아래 명령으로 RunPod에서 수행합니다.

## 비교 내용

- `facebook/dinov2-small`(ViT-S/14, 특징384)과 `facebook/dinov2-base`(ViT-B/14, 특징768)를 비교합니다.
- 두 DINOv2 본체는 고정하고 라벨별 Logistic Regression 8개만 새로 학습합니다. 본체 파인튜닝은 포함하지 않습니다.
- CLS / CLS+평균 패치 특징을 비교합니다. train/dev 특징을 한 번 추출하고 두 pooling을 각각 L2 정규화합니다.
- C=0.01/0.1/1/10, class_weight=None/balanced, 라벨별 임계값은 개발 F1 최대(0~1, 0.05 간격)입니다.
  head와 pooling 선택은 개발 Macro F1 → Macro AP → 작은 C → CLS 순입니다. 같은 pooling/C 동률은 기존 실행기의 unbalanced 우선 규칙을 따릅니다.
- S/B 중 사전 추천 후보는 개발 정확도 → Macro F1 → Macro AP → Small 순으로 고정합니다.
  두 모델 모두 평가하되 평가 결과로 다시 선택·학습하지 않습니다.
- 본체·분류층·전처리·임계값을 고정한 기존 `runs/siglip-full-fp32`의 전체 파인튜닝 모델을 대조군으로 재측정합니다.
  대조군의 평가 판정이 과거와 다르면 중단합니다. 기존 frozen 모델도 다시 학습하지 않습니다.
- DINOv2는 패치14 배수인 **392×392** 전체 이미지 letterbox, ImageNet mean/std입니다.
  공식 기본 center crop224와 다릅니다. SigLIP2는 기존384×384를 유지하므로 해상도 차이를 포함한 후보 파이프라인 비교입니다.
- FP32/TF32 off, batch1, 예열20회, 37장×10회입니다. 원본 디코딩·EXIF·letterbox·정규화·전송·본체·분류층·출력 동기화를 포함합니다.
  OCR/네트워크/DB/모델 로딩/학습/파일 저장은 시간에서 제외합니다.
- 목표는 **알려진282개 라벨 판정 정확도≥90%, p95≤50ms**입니다. 달성을 보장하지 않습니다.
- 같은 공개된 평가37장을 재사용한 후속 비교입니다. 새 일반화 정확도 추정이 아닙니다.
- 기존 소스는 과거 프로토콜에 해시로 고정되어 있으므로 수정하지 않습니다. 환경·데이터·모델·소스가 바뀌면 기존 run에 이어 쓰지 않습니다.

공식 자료: [Small](https://huggingface.co/facebook/dinov2-small), [Base](https://huggingface.co/facebook/dinov2-base),
[Transformers DINOv2](https://huggingface.co/docs/transformers/model_doc/dinov2),
[Meta 선형 평가 구현](https://github.com/facebookresearch/dinov2/blob/main/dinov2/eval/linear.py).

## 1. 코드 반영

다른 브랜치에서 pull이 갈라졌던 RunPod를 위해 새 파일 세 개만 가져옵니다. 브랜치 전환이나 병합이 필요 없습니다.
기존 의존 코드 `run_experiment.py`, `encoders.py`, `finetune_siglip.py`, `../poster_benchmark/benchlib.py`는 완료된 실험에서 사용한 그대로 필요합니다.

```bash
cd /workspace/SKN33-Final-1Team
git fetch origin dev/12_runpod
git restore --source=origin/dev/12_runpod --worktree -- \
  experiments/visual_classifier_benchmark/dinov2_compare.py \
  experiments/visual_classifier_benchmark/run_dinov2.sh \
  experiments/visual_classifier_benchmark/DINOV2_COMPARE.md
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
```

## 2. 새 모델만 다운로드

```bash
bash run_dinov2.sh download
```

기존 benchmark `.venv/bin/python`의 Hugging Face 라이브러리를 사용합니다. pip 명령은 없습니다.
공식 두 저장소에서 config/preprocessor/safetensors와 README/라이선스를 받습니다.
`/workspace/models/dinov2-small`, `/workspace/models/dinov2-base`에 저장합니다.
첫 실행에서 Hub commit SHA를 고정하고 완료 후 파일 SHA256을 기록합니다.
다운로드 중단 후 재실행은 같은 revision으로 이어받고, 완료 모델은 해시 확인 후 건너뜁니다.
학습 실행 중에는 Hub 접속이 차단되고 로컬 파일만 사용합니다.

## 3. 학습·평가

```bash
bash run_dinov2.sh run --resume
```

기본 결과 경로는 `runs/dinov2-heads-fp32`입니다. 완료된 모델의 분류층 학습과 평가는 재실행하지 않습니다.
중단 시 완료된 모델 단위로 재사용합니다. 아직 완료되지 않은 모델 단계만 다시 수행합니다.
동일 폴더에 다른 설정·코드·GPU·환경을 적용하면 중단하므로 실험 조건 변경 시 `--run-dir runs/새이름`을 지정합니다.
원래 SigLIP2 대조군 환경과 다르면 재설치하지 말고 표시된 차이를 확인합니다.

## 4. 결과 확인

```bash
bash run_dinov2.sh show-results
```

- `comparison.csv`: 기존 전체 파인튜닝 SigLIP2 / DINOv2-S / DINOv2-B 정확도, Macro F1, 평균·중앙값·p95, 두 목표 달성 여부
- `per_label_comparison.csv`: 대조군 대비 DINOv2 각각의 TP/FP/FN/TN과 FP/FN 증감
- `error_changes.json`: 회복·퇴보한 이미지와 라벨
- `comparison_summary.json`: 개발셋에서 고른 DINOv2 후보와 모델별 목표 달성 여부
- `selection_frozen.json`, `protocol.json`: 평가 전 고정한 설정·분류층과 데이터/모델/소스 해시
- 각 모델의 `test_predictions.json`, `test_metrics.json`, `timing_raw.csv`, `timing_summary.json`

기존 3종 실험용 결과 내보내기 도구에는 이 새 run이 자동 포함되지 않습니다. 다운로드할 때 위 run 폴더를 별도로 묶습니다.

```bash
tar -czf /workspace/dinov2-heads-fp32-results.tar.gz -C runs dinov2-heads-fp32
```

코드/가중치 다운로드, 환경 조회, 데이터/해시 확인은 전처리 단계이며 성능 측정에 포함되지 않습니다.
로컬 GPU 학습이나 RunPod 원격 실행을 개발자가 대신 수행하지 않았습니다.
