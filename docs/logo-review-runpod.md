# 로고 딥러닝 검수 — RunPod GPU Pod 실행 가이드

보고서 「로고 검수 모델 비교」(2026-10-26)의 **결론 = guided(지정 로고 1:1) + 이중검수** 운영 파이프라인과,
그 전 단계인 모델 벤치(2026-10-23 계획)를 RunPod GPU Pod에서 돌리기 위한 코드·절차입니다. 담당 D(worker·통합).

**운영 조합**: OWLv2(탐지) + **CLIP**(1차 대조; 정확도 우선이면 SigLIP2) + **LightGlue**(애매 구간 기하 이중검수).
DINOv2·pHash·ORB·YOLO-World는 벤치 비교군이라 레지스트리에만 남기고 운영 조합에는 넣지 않습니다.

> 로컬 PC에는 GPU가 없어 **모델 추론은 Pod에서만** 합니다. 아래 명령은 모두 **Pod 터미널**에서 실행합니다.

## 구성 파일

| 파일 | 역할 |
| --- | --- |
| `requirements-logo.txt` | Pod 설치 의존성(transformers·huggingface_hub·pillow·numpy·**kornia**·runpod) |
| `backend/app/logo_models.py` | 모델 레지스트리(운영 조합 A/B + 비교군)와 캐시 경로 |
| `scripts/prepare_logo_models.py` | HF 모델 다운로드 → `.runtime/logo-models/<key>/` + manifest |
| `backend/app/logo_pipeline.py` | OWLv2 탐지 · CLIP/SigLIP2 임베딩 · **LightGlue 검증기** · 기준 인덱스 |
| `backend/app/logo_review.py` | **guided + 이중검수 오케스트레이터**(§8): 탐지→1차 대조→2차 검증→판정 |
| `backend/app/logo_runtime.py` | **로컬(Pod)↔RunPod 원격 경계**(§9). 서버는 이 모듈만 호출 |
| `backend/app/logo_runpod_worker.py` | **RunPod Serverless handler**(§9). Pod GPU로 프레임 배치 판정 |
| `scripts/run_logo_guided.py` | **운영 검수 CLI**: 영상/이미지/프레임 1건 guided 검수(JSON + 판정 요약) |
| `scripts/precompute_logo_embeddings.py` | 기준 로고 142개 임베딩 → `.runtime/logo-index/<matcher>/`(blind 식별용) |
| `scripts/run_logo_review.py`, `scripts/run_logo_video.py` | 벤치기 단건 검수 도구(초기 guided/blind) |
| `scripts/bench_logo_models.py`, `scripts/report_logo_bench.py` | 매처 비교 벤치 + 비교 문서 생성 |

## 사전 준비

- RunPod에서 **GPU Pod**(예: RTX A5000/4090, CUDA 12.x, PyTorch 템플릿)를 띄웁니다.
- Pod 터미널(SSH 또는 웹 터미널/Jupyter)에 접속합니다.

## 1. 저장소를 Pod에 올리기

```bash
git clone <이 저장소 URL> creativechecker
cd creativechecker
git checkout feat/27-short-form-video-review   # 또는 이 코드가 머지된 브랜치
```

> `data/logos/regions_logos/` 의 기준 로고 142개가 저장소에 함께 들어옵니다(별도 업로드 불필요).

## 2. 의존성 설치

```bash
# torch/torchvision 이 Pod 이미지에 이미 있으면 그대로 두세요(재설치 금지).
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
pip install -r requirements-logo.txt
```

`torch.cuda.is_available()` 가 `True` 여야 GPU를 씁니다. `False` 면 CPU로 떨어집니다(느림).

## 3. 모델 다운로드 (운영 조합)

```bash
python scripts/prepare_logo_models.py --combo B   # owlv2 + clip (+ lightglue는 kornia가 실행 시 받음)
```

`.runtime/logo-models/owlv2/`, `.runtime/logo-models/clip/` 에 받고 각 폴더에 `manifest.json`(실제 커밋 해시 포함)을 남깁니다.
LightGlue는 HF 준비 절차가 없어 자동으로 건너뛰며, 최초 로드 때 kornia가 DISK+LightGlue 가중치를 받습니다.

- 정확도 우선(SigLIP2)까지: `python scripts/prepare_logo_models.py --models owlv2,clip,siglip2`
- 비교군(DINOv2 등)까지: `python scripts/prepare_logo_models.py --models owlv2,clip,siglip2,dinov2`
- `dinov3` 는 **게이트 모델**입니다. HF에서 라이선스 동의 후 토큰이 필요합니다:
  ```bash
  export HF_TOKEN=hf_xxx            # 값은 출력/커밋하지 마세요
  python scripts/prepare_logo_models.py --models dinov3
  ```

## 4. 기준 로고 142개 임베딩 선계산 (blind 식별 전용)

운영 검수(guided)는 쿼리 로고 1:1 대조라 이 인덱스가 **필요 없습니다**. blind 식별(어느 브랜드인지 추정)을 쓸 때만 돌립니다.

```bash
python scripts/precompute_logo_embeddings.py --matcher clip
```

`.runtime/logo-index/clip/embeddings.npy` (142×D) + `brands.json` 을 만듭니다. 매처를 바꾸면 매처별로 다시 돌립니다.

## 5. 검수 실행

**(A) 특정 로고가 들어갔는지 찾기** — 발주처가 올린 로고 1개를 프레임에서 탐지 + 동일성 확인:

```bash
python scripts/run_logo_review.py \
  --image frame.jpg \
  --query-logo data/logos/regions_logos/lg__경기도__고양시/logo.png \
  --threshold 0.9
```

출력(요약):
```json
{ "mode": "guided",
  "boxes": [ { "xyxy": [x0,y0,x1,y1], "detect_score": 0.93,
               "query_cosine": 0.91,
               "match": { "brand": "lg__경기도__고양시", "cosine": 0.88 } } ] }
```

`query_cosine` 은 올린 쿼리 로고와의 1:1 동일성, `match` 는 142개 기준 중 최근접 브랜드입니다.

**(B) 어느 브랜드 로고인지 식별** — 위치 없이 142개 중 최근접:

```bash
python scripts/run_logo_review.py --image frame.jpg
```

**(C) 영상 프레임 폴더 일괄** — 기존 동영상 파이프라인이 뽑아 둔 프레임에 적용:

```bash
python scripts/run_logo_review.py \
  --frames-dir .runtime/video-reviews/<검수ID>/frames \
  --query-logo <로고.png> --out logo_review.json
```

## 6. 동영상 1편 검수

Pod에 `ffmpeg` 가 없으면 `apt-get install -y ffmpeg` 하거나 `pip install imageio-ffmpeg`(자동 탐색), 또는 `FFMPEG_BIN` 으로 경로를 지정합니다.

**guided(권장)** — 찾을 로고를 알 때. 프레임마다 OWLv2 텍스트 탐지로 '로고 같은' 영역을 제안하고,
각 영역을 올린 로고와 1:1 코사인으로 대조합니다(OWLv2 image-guided 탐지는 영상에서 노이즈가 많아 쓰지 않음):

```bash
python scripts/run_logo_video.py --video clip.mp4 \
  --query-logo data/logos/regions_logos/lg__서울특별시/logo.png \
  --fps 1 --min-cosine 0.6 --out logo_video.json
```

요약 출력: `N장 중 M장에서 발견(코사인≥0.6), 최고 0.xx` + 발견 시각(초) 목록.

**blind** — `--query-logo` 없이 실행하면 탐지 영역을 142개 기준과 대조해 브랜드별 등장 횟수·시각을 요약합니다.
유사 로고가 많으면 변별이 약하므로 참고용입니다.

| 옵션 | 기본 | 의미 |
| --- | --- | --- |
| `--fps` | 1.0 | 초당 샘플 프레임 수 |
| `--det-threshold` | 0.1 | OWLv2 텍스트 탐지 임계값(낮을수록 후보↑) |
| `--min-cosine` | 0.6 | 발견/식별로 셀 코사인 하한 |
| `--max-boxes` | 10 | 프레임당 대조할 상위 탐지 수 |

## 7. 모델 성능 비교(전 모델 일괄)

조합 A를 포함한 모든 매처를 **같은 입력**으로 돌립니다. 프레임 추출과 OWLv2 후보 영역은 한 번만 구해 모든 매처가 공유하므로,
차이는 매처 성능에서만 나옵니다.

```bash
export HF_TOKEN=hf_xxx                       # dinov3(게이트)까지 받을 때만. 값은 출력/커밋 금지
python scripts/prepare_logo_models.py --models owlv2,dinov2,dinov3,siglip2,clip
python scripts/bench_logo_models.py \
  --video <조합A에 쓴 동영상> --image <조합A에 쓴 이미지> \
  --query-logo data/logos/regions_logos/lg__서울특별시/서울시_로고.png \
  --expect-brand lg__서울특별시
python scripts/report_logo_bench.py          # 최신 결과 → docs/logo-model-benchmark.md
```

- 결과 JSON: `.runtime/logo-bench/<시각>/results.json`. 여러 실행을 한 문서로 묶으려면 경로를 여러 개 넘깁니다.
- 준비 안 된 모델(게이트 등)은 건너뛰고 문서의 「건너뛴 모델」에 이유가 남습니다.
- `--max-frames 4` 로 짧게 돌려 동작부터 확인할 수 있습니다.
- `--dump-hits <폴더>` 를 주면 발견 영역 크롭을 저장합니다. 정답/오탐을 눈으로 판정할 때 씁니다.
- 2026-10-24 측정 결과는 `docs/logo-bench/`(결과 JSON·기존 A 실행·요약)에 있고, 비교 문서는 아래로 다시 만듭니다:
  ```bash
  python scripts/report_logo_bench.py docs/logo-bench/r*.json \
    --baseline docs/logo-bench/baseline/*.json --summary docs/logo-bench/summary.md \
    --out docs/logo-model-benchmark.md
  ```

## 재현성·운영 메모

- 모델은 받은 뒤 `manifest.json` 의 `resolved_commit` 으로 버전이 고정됩니다. 실험을 재현할 때 같은 커밋을 씁니다.
- 실행 중 임의 다운로드를 막으려면 `export HF_HUB_OFFLINE=1` 후 돌립니다(모델·임베딩 준비가 끝난 뒤).
- 임계값(`--threshold`)·매처(`--matcher`)를 바꿔 가며 성능검토 계획 §5의 지표를 측정합니다.

## 8. 운영 파이프라인 — guided + 이중검수 (`run_logo_guided.py`)

보고서 §6의 운영 파이프라인입니다. 지정 로고 1장을 쿼리로, 프레임마다:

1. **탐지(OWLv2)** — '로고 같은' 영역을 텍스트 탐지로 제안(image-guided는 영상에서 노이즈가 많아 미사용).
2. **1차 대조(CLIP/SigLIP2)** — 각 영역 크롭을 지정 로고와 코사인 대조.
3. **2차 이중검수(LightGlue)** — 코사인이 **애매한 구간만** 구조·위치 기하 검증.
4. **판정** — 코사인·매칭 개수로 `확실 / 확인 필요 / 미검출`.

| 코사인(1차) | 처리 | 판정 |
| --- | --- | --- |
| ≥ 0.85 | 자동 검출(단일 종결) | `detected` 확실 |
| 0.60 ~ 0.85 | LightGlue 2차 → 매칭 ≥ 20이면 확정, 아니면 보류 | `detected` 또는 `review` 확인 필요 |
| < 0.60 | — | `absent` 미검출 |

임계값은 환경변수로 덮어씁니다(라벨셋 보정 전 잠정값): `LOGO_COSINE_CERTAIN`(0.85) · `LOGO_COSINE_PRESENT`(0.60) ·
`LOGO_LIGHTGLUE_MIN`(20) · `LOGO_DET_THRESHOLD`(0.10) · `LOGO_MAX_BOXES`(10) · `LOGO_VERIFY_BUDGET`(3, 프레임당 2차 횟수 상한).

```bash
# 영상 1편(권장). 애매 구간만 LightGlue 2차로 확인:
python scripts/run_logo_guided.py --video clip.mp4 \
  --query-logo data/logos/regions_logos/lg__서울특별시/서울시_로고.png --fps 1 --out guided.json

python scripts/run_logo_guided.py --video clip.mp4 --query-logo 로고.png --matcher siglip2   # 정확도 우선
python scripts/run_logo_guided.py --image frame.jpg --query-logo 로고.png --no-verify         # 조합 A(2차 생략)
```

요약 출력: `판정: 확실(detected)` + 최고 코사인 + 확실/확인필요 프레임 수·시각(초).

## 9. RunPod Serverless 배포 (서버에서 원격 호출)

서비스 배포 시에는 무거운 모델을 **RunPod Serverless 엔드포인트**에 올리고, FastAPI 서버는 프레임만 보내 판정을 받습니다
(OCR의 `ocr_runtime.py` ↔ `runpod_worker.py` 구조와 동일). 서버는 `backend/app/logo_runtime.py`만 호출합니다.

**추론 위치 — `RUN_TARGET`:**

| 값 | 동작 | 쓰는 곳 |
| --- | --- | --- |
| `local`(기본) | 이 프로세스에서 모델을 올려 직접 판정 | Pod 터미널 CLI, Pod 안 서버, 로컬 점검 |
| `runpod` | 프레임·쿼리 로고만 엔드포인트로 전송 | 모델을 Pod에만 둔 서버 배포 |

**서버리스 워커 띄우기(Pod/컨테이너 안):**

```bash
pip install -r requirements-logo.txt           # runpod·kornia 포함
python scripts/prepare_logo_models.py --combo B # owlv2·clip 캐시(이미지에 구워 두면 콜드스타트↓)
LOGO_MATCHER=clip LOGO_VERIFY=1 python -m backend.app.logo_runpod_worker
```

입력 `{query_logo_base64, frames_base64:[...≤32], overrides?}` → 출력 `{frames:[판정...], config}`.
매처·검증 여부는 워커 환경변수(`LOGO_MATCHER`·`LOGO_VERIFY`)가 고정합니다(요청이 바꾸지 않음).

**서버(원격 호출) 환경변수:**

| 변수 | 의미 |
| --- | --- |
| `RUN_TARGET=runpod` | 원격 호출 켜기 |
| `RUNPOD_API_KEY` | RunPod API 키(서버 전용, 출력·커밋 금지) |
| `LOGO_RUNPOD_ENDPOINT_ID` | 로고 엔드포인트 ID(OCR과 **별도** 배포) |
| `RUNPOD_TIMEOUT_SECONDS` / `RUNPOD_POLL_SECONDS` | 작업 제한·폴링 간격(OCR 경로와 공유) |

호출 예(서버 코드):

```python
from backend.app.logo_runtime import review_video
result = review_video("clip.mp4", "로고.png", fps=1.0)   # RUN_TARGET에 따라 로컬/원격 자동 분기
# result["summary"] = {verdict, verdict_label, detected_times, review_times, best_cosine, ...}
```

## 10. 동영상 검수 서비스 연동 (`logo_match`)

동영상 검수(`video_review.py`)에 로고 검수를 **추가 축**으로 붙였습니다. 기존 OCR 검수 로직은 그대로입니다.

- 업로드 때 **지정 로고(쿼리) 1장**을 선택 첨부(`logo` 멀티파트 필드, 이미지·8MB 이하). 안 올리면 기존과 동일하게 동작합니다.
- 파이프라인이 이미 뽑아 둔 **base 표본 프레임**(1초 격자, 중복 제거)을 `logo_runtime`으로 guided 대조해,
  결과 JSON에 `logo_match` 블록(`status`·`verdict`·`summary`·`per_frame`)과 `logo.json`을 남깁니다.
- 로고 런타임이 미준비이거나 실패해도 **OCR 검수 결과는 유지**하고 경고만 남깁니다(`logo_match.status` = `skipped`/`failed`).
- `GET /api/video-reviews/status` 의 `logo` 필드로 로고 런타임 준비 상태를 알립니다(전체 `ready`는 막지 않음).
- 추론 위치는 §9와 동일하게 `RUN_TARGET`을 따릅니다(서버 배포 시 RunPod). 주입점: `create_app(video_logo_review=..., video_logo_readiness=...)`.

## 아직 안 된 것(다음 작업)

- **프런트엔드**: SPA 업로드 화면에 로고 첨부 UI와 `logo_match` 결과 표시가 필요(백엔드 연동은 §10로 완료).
- **평가셋·지표 자동화**: 성능검토 계획 S1~S4(mAP·Top-1·FAR 등)와 임계값(0.85·0.60·LightGlue 20) 정밀 보정은 별도.
- **조합 C/D**(YOLO-World·YOLO11 학습 탐지기)는 레지스트리 확장 후 추가. 조합 B(LightGlue)는 §8~§10으로 운영 반영 완료.
