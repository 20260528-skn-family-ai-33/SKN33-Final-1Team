# RunPod 포스터 모델 비교 실험

승인한 포스터187장으로 기존 ResNet18 본체 파인튜닝, 고정 EG2+분류기, EG2 본체 파인튜닝을 같은 CUDA GPU에서 비교합니다. `train`은 EG2를 다시 학습한 뒤 세 모델을 평가하며, `evaluate`는 전달한 기존 세 모델을 평가합니다. ResNet18 재학습은 포함하지 않습니다.

## 완료한 학습 체크포인트로 PaddleOCR 포함 평가

**RunPod에서 이미 `train`을 완료했다면 아래 명령만 사용합니다. 재학습하지 않습니다.** `run_pipeline.sh`는 완료한 CUDA train 폴더의 개발셋 선택 EG2 체크포인트·임계값과 기존 ResNet18·고정 EG2를 그대로 읽습니다. 기존 `run.sh evaluate`는 bundle의 과거 EG2 체크포인트를 사용하므로 이번 후속 평가에는 `run_pipeline.sh`를 사용합니다.

```bash
git -C /workspace/SKN33-Final-1Team pull --ff-only
bash /workspace/SKN33-Final-1Team/experiments/poster_benchmark/setup_pipeline.sh
bash /workspace/SKN33-Final-1Team/experiments/poster_benchmark/run_pipeline.sh --source-run latest
```

각 명령이 성공한 뒤 다음 명령으로 진행합니다. 설치는 별도 `POSTER_OCR_VENV`(기본 이 폴더의 `.ocr-venv/`)에서만 수행하고 기존 시각 모델 venv에는 패키지를 설치하지 않습니다. `setup_pipeline.sh`는 원본 train/체크포인트를 먼저 검증하고 Paddle GPU3.3.1/cu126, PaddleOCR3.7.0/PaddleX3.7.2 및 고정 해시의 OCR 모델을 준비합니다. GPU wheel은 [Paddle 공식 cu126 인덱스](https://www.paddlepaddle.org.cn/packages/stable/cu126/paddlepaddle-gpu/)를 사용합니다.

- `latest`는 `POSTER_RUN_DIR` 또는 기본 `runs/`의 **가장 최근 train 실행**을 고릅니다. 그 실행이 완료되지 않았다면 실패하며, 과거 체크포인트로 자동 대체하지 않습니다. 직접 지정할 때는 두 스크립트에 동일한 `--source-run /workspace/.../train-실행폴더`를 전달합니다.
- 기존 한국어 OCR 탐지·인식 모델(`PP-OCRv5_server_det`, `korean_PP-OCRv5_mobile_rec`)과 backend의 탐지/인식 설정을 유지합니다. OCR 입력은 원본 해상도이며, 시각 모델의448px letterbox와 구분합니다. [PaddleOCR 모델·파라미터 안내](https://www.paddleocr.ai/v3.1.1/en/version3.x/pipeline_usage/OCR.html)
- 기본 OCR 장치는 **`gpu:0`**, 시각 모델은 동일 GPU의 `cuda:0`입니다. 기존 로컬 OCR의 CPU 실행 조건을 사용할 때는 `run_pipeline.sh --source-run latest --ocr-device cpu`를 사용합니다. 모든 비교 arm에는 같은 OCR 장치를 적용하며, GPU를 요청했을 때 CPU로 자동 전환하지 않습니다.
- 같은37장, 모름14개 판정 제외/282개 알려진 판정, 기존 임계값·정밀도·시각 모델 환경을 유지합니다. 모델별3회 예열 후37장×3회 실행하며, OCR도 **매번 실제 수행**합니다. OCR과 분류는 순차 실행합니다.
- `ocr_ms`: 원본 디코딩·PaddleOCR·작업자 IPC/결과 직렬화. `model_ms`: 시각 입력 디코딩·letterbox·processor·추론·분류·GPU 동기화. `total_ms`: OCR 요청 직전부터 분류 완료까지 **연속 wall-clock 실측**입니다. 각 구간 평균·중앙값·p95를 저장합니다. 다운로드·모델 로딩·예열·진단 파일 저장은 별도입니다.
- OCR은 별도 장기 실행 프로세스에 한 번 로드합니다. 각 시각 모델도 로딩 후 반복 사용합니다. 전체 시간에는 실제 IPC가 포함됩니다. 이미지 디코딩을 공유하지 않는 현재 평가 경로의 측정이며, 웹 업로드·DB/API 시간은 포함하지 않습니다.
- 시각 분류 확률·판정과 입력 픽셀을 기존 완료 결과와 비교하며, 일치하지 않으면 실패합니다. OCR 실패나 timeout도 건너뛰지 않습니다. 원본 train/체크포인트·라벨은 수정하지 않고 새 `pipeline-*` 폴더에 저장합니다.
- OCR 원문·좌표·점수도 저장합니다. OCR 정답 텍스트는 이 라벨셋에 없으므로 CER 등 OCR 인식 정확도는 산출하지 않습니다. 표시되는 정확도는 계속 인물·사물 분류 정확도이며, OCR 텍스트를 시각 분류기에 새로 입력하지 않습니다.

마지막 `pipeline_complete`와 새 폴더의 `status.json`에 `complete`, `PaddleOCR_executed: true`, `training_executed: false`가 있어야 완료입니다. 주요 결과는 `comparison_pipeline.csv`, `pipeline_summary.json`, 모델별 `timing_raw.csv`·`test_metrics.json`·`ocr_raw/`입니다. `protocol.json`, `environment.json`, `ocr_environment.json`, `paddle_worker.log`에 실행 조건과 모델 식별자를 남깁니다.

```bash
bash /workspace/SKN33-Final-1Team/experiments/poster_benchmark/run_pipeline.sh --show-results
```

전체 복사 가능한 SSH·업로드·재개 명령은 저장소 루트의 [런팟_실행명령어.md](../../런팟_실행명령어.md)에 정리했습니다. 이 문서의 이후 `train`/`evaluate` 속도는 기존 **OCR 제외** 평가를 설명합니다. OCR 포함 결과와 구분하세요.

## RunPod에서 시작

Python **3.12**, CUDA12.8 호환 드라이버, 시스템 RAM32GB 이상인 GPU Pod가 필요합니다. 현재 선택한 A40 48GB가 후보이며 실제 사용 메모리는 smoke에서 검사합니다. `/workspace`에 **60GB 이상(권장80GB)**의 영속 볼륨을 연결합니다. GPU Pod 생성이나 과금 종료는 이 코드에서 자동 수행하지 않습니다.

RunPod Environment Variables에 `HF_TOKEN={{ RUNPOD_SECRET_SKN_33_HF_TOKEN2 }}`를 설정합니다. 해당 Secret의 실제 Hugging Face 토큰과 모델 접근 권한이 필요합니다. 기존 `HF_HOME`, `POSTER_VENV` 환경변수도 그대로 사용할 수 있습니다. [환경변수 목록](.env.example)은 빈 토큰을 가진 참고 파일이며 `.env`를 자동 로드하지 않습니다. 이번 실험은 OpenAI 키를 사용하지 않습니다.

기존 `runpod-poster-benchmark-2026-10-22.tar`를 SSH로 `/workspace`에 업로드한 뒤 Pod 터미널에서 실행합니다.

```bash
cd /workspace
git clone --depth 1 --branch dev/12_runpod --single-branch https://github.com/20260528-skn-family-ai-33/SKN33-Final-1Team.git
cd SKN33-Final-1Team
bash experiments/poster_benchmark/start.sh train
```

먼저 아래의 기존 압축파일을 SSH로 `/workspace`에 올립니다. 한 실행 명령으로 업로드 자산 해시 확인·전개 → 전용 venv 설치 → CPU 계약 검사 → 고정 revision의 EG2 다운로드 → 학습 이미지 smoke → EG2 학습·세 모델 평가를 진행합니다. 기존 모델들만 먼저 평가하려면 마지막 인수를 `evaluate`로 바꿉니다. 패키지 설치와 기본 모델 다운로드에는 인터넷이 필요합니다. `python3`이3.12가 아니고 `python3.12`가 있다면 실행 전에 `export POSTER_PYTHON=python3.12`를 지정합니다.

FP32/microbatch1/effective batch8이 기본입니다. smoke 성공은 실제 GPU 용량 검사이며 CUDA 실측 성능 결과는 완료된 실행 폴더에서만 확인합니다. 기존 웹 앱·PaddleOCR·DB 설정은 건드리지 않습니다.

## 데이터·체크포인트 연결

코드와 작은 무결성 명세만 Git에 저장합니다. 이미지·체크포인트는 사용자가 SSH로 직접 전달합니다. GitHub Release나 Git LFS를 사용하지 않습니다.

- 파일명: `runpod-poster-benchmark-2026-10-22.tar` (기존에 전달한 압축파일)
- 크기: **1,840,670,720 bytes** / SHA256: `b707d50868a66d3e7fb824f9f022d42f556fb9c3d9866fd422ba27c5d5c8a134`
- 기본 업로드 위치: `/workspace/runpod-poster-benchmark-2026-10-22.tar`
- [전체 archive SHA256/크기](asset_manifest.json), [206개 payload 파일 SHA256](bundle_manifest.json)
- 이미지187장, 승인 라벨·분할, ResNet18/EG2 기존 checkpoint, 고정 EG2 분류층, 기존 예측·선택 기록과 모델 파일 해시를 포함합니다.

기본 경로는 이 실험 폴더의 `assets/`, `.venv/`, `runs/`이며 모두 Git에서 제외합니다. clone을 `/workspace`에 하면 이 경로들도 볼륨 안에 있습니다. 저장 위치를 바꿀 때는 다음 환경변수를 사용합니다.

| 변수 | 용도 |
|---|---|
| `POSTER_BUNDLE_ARCHIVE` | SSH로 올린 원본 `.tar` 경로 |
| `POSTER_BUNDLE_DIR` | 이미지·기존 체크포인트 설치 위치 |
| `POSTER_VENV` | 전용 Python 가상환경 |
| `POSTER_RUN_DIR` | 학습/평가 결과 기본 상위 폴더 |
| `HF_HOME` | 기본 EG2 Hub 캐시 |

SSH 전송이 완료된 뒤 실행하세요. archive 크기와 SHA256이 일치하기 전에는 전개하지 않으며, 전개 중 실패하면 임시 폴더를 제거합니다. 압축파일에 있던 옛 코드는 설치하지 않고 **clone한 브랜치의 코드만** 사용합니다. 기존에 잘못된 `assets/`가 있으면 덮어쓰지 않으므로 보존한 뒤 다른 위치를 지정합니다.

경로를 직접 지정하거나 이미 전개한 기존 bundle을 연결할 수도 있습니다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/poster_benchmark
python3 assets.py fetch --archive /workspace/runpod-poster-benchmark-2026-10-22.tar
bash start.sh evaluate
```

다른 경로에 올렸다면 `export POSTER_BUNDLE_ARCHIVE=/다른/경로/파일.tar`를 지정합니다. 압축을 이미 풀었다면 `POSTER_BUNDLE_DIR=/workspace/runpod-poster-benchmark-2026-10-22`처럼 `bundle_manifest.json`이 있는 폴더를 지정하세요. 연결할 폴더도 Git 명세와 206개 파일 해시를 모두 검증합니다. 외부 위치에 연결된 데이터·체크포인트를 수정하지 않습니다.

## 별도 실행·BF16·재개

`start.sh`는 매번 새 실행 폴더를 만듭니다. 한 번 준비한 뒤에는 필요한 단계만 실행합니다.

```bash
cd /workspace/SKN33-Final-1Team/experiments/poster_benchmark
bash run.sh validate
bash run.sh evaluate --run-dir runs/eval-fp32
bash run.sh train --run-dir runs/train-fp32
# 중단된 동일 실행: 코드·GPU·환경·인수를 그대로 사용
bash run.sh train --run-dir runs/train-fp32 --resume
# FP32와 다른 별도 실험. FP16은 지원하지 않습니다.
bash run.sh smoke --precision bf16 --microbatch 4 --run-dir runs/smoke-bf16-b4
bash run.sh train --precision bf16 --microbatch 4 --run-dir runs/train-bf16-b4
```

재개는 마지막 완료 epoch의 모델과 AdamW 상태부터 수행합니다. 후보별 `latest.pt`가 약5.3GB이므로 학습 중 디스크 사용량을 고려합니다. 같은 실행 폴더에 변경된 코드·GPU·분할·인수로 재개하지 않습니다.

## 유지한 평가 조건과 결과

- `google/embeddinggemma-2` revision `914f7f89142e33e77833254d9c9b90c3cef7303b`. 이미지·텍스트 본체438,760,448개와 분류층6,152개를 학습하며 오디오 경로는 로드하지 않습니다.
- 사람이 채팅에서 승인한 AI 보조 라벨 초안187장, train113/dev37/test37과 그룹을 유지합니다. 별도의 독립 사람 재라벨링 결과는 아닙니다.
- 모름은 학습43/dev13/test14개를 라벨별 제외합니다. 평가 정확도의 분모는 알려진282개 판정입니다. 모름 없는27장의 완전 일치율도 따로 표시합니다.
- 공통448×448 letterbox, 학습에만 ColorJitter, EG2 이미지280토큰·프롬프트 없음. 개발셋으로 학습률·checkpoint·임계값을 고정한 뒤 평가셋을 읽습니다.
- EG2 본체 LR 후보1e-6/3e-6, 분류층1e-3, weight decay1e-4, 최대4epoch/patience2. 후보 checkpoint는 dev Macro AP, 후보 간 선택은 dev Macro F1→AP→작은 LR. 라벨별 임계값은 dev F1 최대로 고릅니다.
- **이미 결과를 확인한 기존37장 평가셋의 후속 비교**입니다. 독립 일반화 성능이나90% 달성을 보장하지 않습니다. 기존 ResNet18과 새 EG2의 학습 탐색 예산도 다릅니다.
- 속도는 동일 GPU/정밀도/batch1/예열3회, 동일 이미지 순서37장×3회, 매회 CUDA 동기화로 측정합니다. 디코딩·전처리·processor·추론·전송·분류층을 포함하고 OCR·다운로드·로딩·학습·DB/API는 제외합니다.

완료 폴더의 `comparison.csv`, `comparison_summary.json`, `status.json`이 주 결과입니다. `status.json`이 `complete`일 때만 전체 완료입니다. `environment.json`, `selection_frozen.json`, 이미지별 예측과 원시 타이밍도 저장합니다. Mac의 기존 MPS 시간은 CUDA 결과로 재사용하지 않습니다.

## 로컬 확인·유지보수

```bash
python3 assets.py verify
python3 -m unittest discover -s tests -p 'test_assets.py' -v
# NumPy/sklearn/PyTorch/torchvision 환경에서는 모든 CPU 계약 검사
python3 -m unittest discover -s tests -v
```

로컬 검사는 RunPod CUDA 성공을 뜻하지 않습니다. 실제 키는 RunPod 환경변수 또는 별도로 관리하는 파일에만 보관합니다. `.env`, `.env.*`, `.env_*`, `*.env` 파일과 실험용 `assets/`·`runs/`·venv는 Git에서 제외하며, 빈 `.env.example`만 커밋합니다.

EG2 수정 checkpoint에는 [NOTICE](NOTICE.txt)와 [Gemma 약관 전문](GEMMA_TERMS.txt)이 적용됩니다. 기본 모델은 Hugging Face에서 별도로 받으며 사용 계정의 접근 권한을 확인합니다.
