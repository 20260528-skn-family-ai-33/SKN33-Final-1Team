# RunPod 시각 분류 후보 비교

**검토 포스터22장 보강·기존32셀/추가327장 상속·출처 균형 학습은 [POSTER_AUGMENT_V2.md](POSTER_AUGMENT_V2.md)를 따릅니다. 이미지와 CSV도 Git에 포함하며 재설치하지 않습니다.**

**DINOv2-S/B와 기존 SigLIP2 전체 파인튜닝 모델 비교는 [DINOV2_COMPARE.md](DINOV2_COMPARE.md)를 따릅니다. 기존 환경을 사용합니다.**

**DINOv2-S/B 본체 전체 파인튜닝과 고정 본체·SigLIP2의 5종 비교는 [FINETUNE_DINOV2.md](FINETUNE_DINOV2.md)를 따릅니다. 추가 설치는 없습니다.**

**SigLIP2 저장 확률의 무학습 임계값·순위 오류 진단은 [SIGLIP_THRESHOLD_AUDIT.md](SIGLIP_THRESHOLD_AUDIT.md)를 따릅니다. 모델 추론과 로컬 실행 검수는 하지 않습니다.**

이미 내려받은 SigLIP 2 Base/384와 ConvNeXt V2 Tiny/384를 같은 CUDA GPU에서 비교합니다.
**본체는 고정하고, 이미지 특징 위의 라벨별 Logistic Regression 8개만 학습합니다.**
본체 파인튜닝·DINOv3·OCR·TensorRT는 이번 실행기에 포함하지 않습니다.

## 입력과 측정 기준

- 기존 승인된 AI 보조 라벨 패킷 187장을 재사용합니다. 새로운 독립 사람 라벨링 결과가 아닙니다.
- 학습113 / 개발37 / 평가37, 그룹·파일 SHA256·라벨 순서·모름 마스크를 기존 검증기로 확인합니다.
- 모름은 학습·개발·평가에서 해당 라벨만 제외합니다. 평가 분모는 알려진282개입니다.
- RGB/EXIF 보정 후384×384 회색 letterbox, bicubic, 모델별 mean/std, 전체 시야 보존입니다.
  기존 EG2/ResNet의448px 실험과 입력 해상도가 다릅니다. 과거 속도를 새 CUDA 측정값으로 복사하지 않습니다.
- SigLIP은 로컬 전체 가중치를 CPU에서 로드한 뒤 비전 타워만 GPU에 올립니다. 텍스트 타워는 사용하지 않습니다.
- ConvNeXt는 로컬 safetensors를 strict=True로 로드하고 ImageNet 분류층을 제거합니다.
- train/dev 특징으로 C=0.01/0.1/1/10, class_weight=None/balanced를 비교합니다.
  라벨별 임계값은 개발 F1 최대(0~1,0.05 간격), 후보는 개발 Macro F1 → Macro AP → 작은 C → unbalanced 순으로 선택합니다.
- 요청한 모든 모델의 선택을 파일·해시로 고정한 후 평가셋을 읽습니다.
- 기존37장은 이미 확인한 평가셋입니다. 후속 비교용이며 새 일반화 정확도 주장에는 별도 평가셋이 필요합니다.
- 목표: 라벨별 정확도≥90%, batch1 중앙값≤30ms, p95≤50ms. 달성을 보장하지 않습니다.
- 속도: 원본 디코딩·EXIF·letterbox·정규화·H2D·인코더·8개 분류층·D2H·동기화까지 매번 측정합니다.
  기본20회 예열,37장×10회=370회입니다. OCR·원격 API·DB·모델 로딩·학습·파일 저장은 제외합니다.
- FP32 기준부터 실행하고 FP16/BF16은 별도 실행 폴더에서 비교합니다. 분류층은 항상FP32입니다.
- ConvNeXt 해당 가중치는CC-BY-NC-4.0입니다. 비상업 비교 실험용입니다. SigLIP은Apache-2.0입니다.

## Git으로 코드 업데이트 — 기존 환경 사용

기존 가상환경과 모델을 그대로 사용합니다. 이미 설치했다면 `setup_runpod.sh`를 다시 실행하지 않습니다.

```bash
cd /workspace/SKN33-Final-1Team
git pull --ff-only origin dev/12_runpod
cd experiments/visual_classifier_benchmark
```

이전에 SCP로 받은 코드가 untracked 파일로 남아 있다면 pull 전에 **코드 파일만** 별도 stash에 보존합니다.
`.venv`와 `runs`는 아래 pathspec에 포함하지 않습니다. 실행 중인 학습이 종료된 후 수행하세요.

```bash
cd /workspace/SKN33-Final-1Team
git stash push --include-untracked -m "visual-code-before-git-pull" -- \
  ':(glob)experiments/visual_classifier_benchmark/*.py' \
  ':(glob)experiments/visual_classifier_benchmark/*.sh' \
  ':(glob)experiments/visual_classifier_benchmark/*.md' \
  experiments/visual_classifier_benchmark/requirements.txt \
  experiments/visual_classifier_benchmark/tests
git pull --ff-only origin dev/12_runpod
```

이 stash를 바로 pop하지 않습니다. Git의 코드로 실행하고, 이전 SCP 파일은 필요할 때 stash에서 확인합니다.

**SigLIP2 본체 파인튜닝은 [FINETUNE_SIGLIP.md](FINETUNE_SIGLIP.md)를 따릅니다.**

## Qwen3.5-9B 무학습 8라벨 비교

기존 환경에서 Qwen 모델만 다운로드하고, 개발셋에서 선택된 SigLIP2의 저장 결과와 비교하는 절차는
[QWEN_ZERO_SHOT.md](QWEN_ZERO_SHOT.md)를 따른다. 기존 데이터·학습 결과를 보존하며 재설치·재학습은 하지 않는다.
미판정/JSON 실패는 정확도에서 오답으로 계산하고, SigLIP의 과거 p95와 Qwen의 새 측정값을 구분한다.

## 요구조건 JSON 추출 초기 비교

요구조건 원문을 JSON으로 구조화하는2B·4B 비교는 [REQUIREMENTS_JSON_PILOT.md](REQUIREMENTS_JSON_PILOT.md)를 따른다.
이미지 분류와 다른 텍스트 실험이며, 사람 검수 전 합성40건의 초안 일치도를 표시한다. 환경 재설치·학습·운영 API 변경은 없다.
같은 안내에84.40% 원본·현재 비교 run·Qwen을 보존하는 과거 실험 가중치 정리 명령도 포함한다.

## 최초 수동 배치 참고

제공된 `visual-classifier-benchmark-2026-10-23.tar.gz`를 Jupyter에서 `/workspace`에 업로드합니다.
이 압축파일은 **새 실험 코드만** 포함하며 Git에 올리지 않은 코드도 전달할 수 있습니다.
기존 저장소·다운로드 모델·데이터는 그대로 사용합니다.

```bash
tar -xzf /workspace/visual-classifier-benchmark-2026-10-23.tar.gz -C /workspace/SKN33-Final-1Team/experiments
cd /workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark
bash setup_runpod.sh
```

Python3.12 및 torch2.10 CUDA12.8 호환 드라이버가 필요합니다. setup은 이 폴더의 새`.venv`에 설치합니다.
다운로드용·기존 EG2·OCR 가상환경에는 설치하지 않습니다. 이미 업로드한 기존 데이터 archive에서
`../poster_benchmark/assets.py fetch`로 데이터 패킷을 검증/준비합니다.
GPU Pod 생성·과금·종료·모델 다운로드는 자동 수행하지 않습니다.

기본 모델 경로:

```text
/workspace/models/siglip2-base-384
/workspace/models/convnextv2-tiny-384
```

## 1. 데이터·다운로드 파일 검증

```bash
bash run.sh validate
```

기존 데이터 패킷 무결성과 로컬 모델 파일 SHA256을 기록합니다. 원격 체크섬 일치/실제 로딩까지
증명하는 명령은 아닙니다. 모델 로딩은 다음 CUDA smoke에서 확인합니다.

## 2. CUDA 로딩 확인

```bash
bash run.sh smoke --run-dir runs/smoke-fp32
```

각 모델이 학습 이미지2장에서 유한한 특징을 생성하는지 확인합니다. 개발·평가셋 점수는 계산하지 않습니다.
`smoke_complete`가 확인되면 다음 단계로 진행합니다.

## 3. 분류층 학습·평가·속도 측정

```bash
bash run.sh run --run-dir runs/heads-fp32
```

모델 하나씩 실행할 수도 있습니다. 나머지 실험 조건은 동일하게 유지하세요.

```bash
bash run.sh run --models siglip2 --run-dir runs/siglip2-fp32
bash run.sh run --models convnextv2 --run-dir runs/convnextv2-fp32
```

## 4. 정밀도 비교

```bash
bash run.sh run --precision fp16 --run-dir runs/heads-fp16
bash run.sh run --precision bf16 --run-dir runs/heads-bf16
```

각 정밀도에서 train/dev 특징과 분류층을 새로 맞추는 별도 실험입니다. 같은 가중치에 정밀도만
변경한 순수 ablation으로 해석하지 않습니다. BF16 미지원 GPU에서는 자동 전환하지 않고 실패합니다.
FP16/BF16이 더 빠르거나 정확도가 동일하다고 가정하지 않습니다.

## 재개·결과 확인

```bash
bash run.sh run --run-dir runs/heads-fp32 --resume
bash run.sh show-results --run-dir runs/heads-fp32
```

최초 실행과 **모델 목록·정밀도·C·예열·반복·입력·코드·GPU·패키지**가 같아야 재개합니다.
완료한 모델의 분류층/평가는 해시 검증 후 재사용하고 미완료한 모델은 다시 실행합니다.
분류층 선택 파일 생성 도중 중단돼 selection_commit.json이 없다면 안전하게 실패하므로
원자료를 보존하고 새 run 폴더를 사용하세요.

`comparison.csv`와 `comparison_summary.json`이 주 결과입니다. 모델별 후보/개발 지표/선택 분류층,
test_predictions.json, timing_raw.csv, timing_summary.json을 저장합니다. GPU·정밀도·패키지 및
모델/코드 해시는protocol.json/environment.json에 있습니다.
`status.json`의complete가 최종 완료 증거입니다. 이전failure.json이 남으면 최종status를 먼저 봅니다.

## 로컬 검증

```bash
python -m unittest discover -s tests -v
```

테스트는 합성 이미지·라벨·무작위 소형 체크포인트로 학습/선택/재개/오프라인 로딩을 검증합니다.
실제 사전학습 가중치의 정확도, RunPod CUDA 실행,30~50ms 달성 증거가 아닙니다.

## 공식 자료

- https://huggingface.co/google/siglip2-base-patch16-384
- https://huggingface.co/timm/convnextv2_tiny.fcmae_ft_in22k_in1k_384
- https://huggingface.co/docs/timm/feature_extraction
