# 고해상도 포스터 학습: 업로드부터 결과 확인까지

작성 시점: 2026-10-28. 이 문서는 사용자가 직접 실행하는 절차다. 준비 단계에서 RunPod 접속, 패키지 재설치, 학습, 모델 추론은 수행하지 않았다. 아래 경로와 기존 체크포인트는 이전 작업 기록을 바탕으로 했으며 현재 Pod에 남아 있는지는 사전 점검으로 확인한다.

## 0. 어떤 실험인지

- 187장을 모두 보관하고, 같은 포스터의 중복본 `NEW017` 1장은 제외한다. 기존 train 8장을 포함한 학습 130장 / 새 개발 26장 / 새 시험 26장 / 기존 dev 3장 / 기존 test 1장이다. 기존 dev/test 4장은 최종 관찰에만 사용한다.
- `labels.csv`는 **Codex AI 검토 초안**이고 사람의 독립 승인 전이다. `?` 12칸은 손실과 평가에서 제외한다. `review.html`은 이미지·판정·이유를 보여준다. 이전에 승인한 12장 중 4장, 6칸에서 차이가 있어 `legacy_label_differences.csv`에서 비교할 수 있다. 이용 권리는 미확인이다.
- 새 시험 26장은 그룹 분할된 잠정 비교용이다. 원격에 남은 모든 옛 이미지 및 행사와의 중복, 독립성과 대표성은 검증하지 않았다. 불꽃 양성은 개발과 시험에 각 1장뿐이다. 결과를 독립 검증 성능으로 발표하지 않는다.
- 출발점은 기존 `siglip-ai-reviewed-v1-fp32`에서 선택된 `tail4-lr1e-06` 가중치다. 이전의 472장 패킷을 다시 섞지는 않는다. 따라서 고해상도 130장만 추가 학습하면서 기존 데이터 성능이 떨어질 수 있다. 최종 평가는 새 시험 26장과 이 패킷의 기존 dev 3장/test 1장만이며 기존 시험 37장 전체 평가는 포함하지 않는다.
- 고해상도 원본을 384×384와 768×768로 비율을 유지해 각각 리사이즈·회색 여백 처리한다. 384는 비교 기준이고 768은 위치 임베딩 보간을 쓰는 실험이다. 기존 SigLIP 모델의 고정 384 체크포인트를 768에서 쓰는 것이지, 원본 해상도 그대로 모델에 넣는 것은 아니다. [Hugging Face SigLIP 문서](https://huggingface.co/docs/transformers/main/model_doc/siglip)는 위치 임베딩 보간 옵션을 설명한다. 실제 설치 버전과 GPU 메모리는 사전 점검 및 실행 시 확인이 필요하다.
- 두 크기 각각에서 시작 가중치(epoch 0)와 최대 8 epoch 부분학습을 개발셋의 masked BCE로 비교하고, 둘 중 개발 손실이 가장 낮은 것을 고른다. 하위 4개 비전 블록·pooling·분류 헤드만 학습한다. 기존 모델의 임계값을 유지하고, 개발셋 고정 후에만 시험셋의 정확도·macro F1·라벨별 TP/FP/FN/TN을 출력한다. 성능 목표 달성이나 GPU 실행 성공을 보장하지 않는다.
- 이전 모델, 원본 패킷, 프로젝트의 다른 변경 내용은 덮어쓰지 않는다. 캐시·실행 결과는 새 폴더만 허용한다. 실패하면 기존 폴더를 삭제하거나 재사용하지 말고 다른 이름의 새 실행 폴더를 사용한다.

## 1. Mac에서 파일 준비와 RunPod 업로드

Mac 터미널에서 아래 파일이 있는지 확인한다. `RP_HOST`, `RP_PORT`, `RP_USER`는 **현재 Pod의 SSH 접속 안내에서 확인한 값**으로 바꾼다. SSH 키가 필요한 경우 본인 접속 방식의 `-i` 옵션을 `ssh`, `scp` 양쪽에 추가한다.

```bash
ARCHIVE='/Users/choejiheum/Documents/Codex/2026-09-25/x20-x20/outputs/모닝컵_가상납품검수_예제/output/highres-poster-training-2026-10-27-v1.tar.gz'
ls -lh "$ARCHIVE" "$ARCHIVE.sha256"
RP_HOST='여기에_현재_Pod_호스트'
RP_PORT='여기에_현재_SSH_포트'
RP_USER='root'
RUN_TAG="highres-$(date +%Y%m%d-%H%M%S)"
printf 'RUN_TAG=%s\n' "$RUN_TAG"
ssh -p "$RP_PORT" "$RP_USER@$RP_HOST" "mkdir -- /workspace/$RUN_TAG"
scp -P "$RP_PORT" "$ARCHIVE" "$ARCHIVE.sha256" "$RP_USER@$RP_HOST:/workspace/$RUN_TAG/"
ssh -p "$RP_PORT" "$RP_USER@$RP_HOST"
```

새 폴더 생성이 실패하면 업로드를 진행하지 말고 원인을 확인한다. `RUN_TAG`는 Mac 터미널에 남겨둔다. SSH 연결 뒤 나오는 프롬프트는 Pod 터미널이다.

## 2. RunPod 터미널에서 압축 확인·해제

아래 첫 줄의 `highres-...`를 **1단계에서 생성된 실제 폴더 이름**으로 바꾼다. `ls /workspace`로 확인할 수 있다.

```bash
ROOT='/workspace/여기에_RUN_TAG_전체값'
cd "$ROOT"
sha256sum -c highres-poster-training-2026-10-27-v1.tar.gz.sha256
tar -xzf highres-poster-training-2026-10-27-v1.tar.gz
P="$ROOT/packet"
CACHE="$ROOT/cache-v1"
OUT="$ROOT/run-001"
PY='/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/.venv/bin/python'
MODEL='/workspace/models/siglip2-base-384'
REF='/workspace/SKN33-Final-1Team/experiments/visual_classifier_benchmark/runs/siglip-ai-reviewed-v1-fp32'
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
```

`sha256sum`이 실패하면 압축을 풀거나 실행하지 말고 업로드를 다시 확인한다. 압축 해제는 방금 만든 빈 폴더에서만 한다.

## 3. 기존 환경과 입력 검증

```bash
"$PY" -B "$P/code/run.py" verify --packet "$P"
"$PY" -B "$P/code/run.py" preflight --packet "$P" --model-dir "$MODEL" --reference "$REF"
```

첫 명령은 원본 187장의 SHA-256, 그룹 분리, 분할과 중복 제외를 확인한다. 두 번째는 기존 가상환경의 라이브러리·CUDA, 선택 확정 메타데이터, 기존 체크포인트와 기본 모델 파일의 SHA-256, 768 입력을 위한 보간 지원 여부를 확인한다. 어느 하나라도 실패하면 여기서 멈춰 출력 내용을 확인한다. 코드는 모델이나 라이브러리를 내려받거나 설치하지 않는다.

768 보간이 지원되지 않으면 두 번째 명령에 `--sizes 384`를 붙여 384 입력만 점검할 수 있다. 이후 train과 evaluate에도 `--sizes 384`를 붙여야 한다. 이 경우 **고해상도 원본을 384 입력으로 사용한 대조 실험만** 수행한다.

## 4. 모델 입력용 캐시 생성

```bash
"$PY" -B "$P/code/run.py" cache --packet "$P" --cache "$CACHE"
```

원본은 유지하고 별도 폴더에 384·768 PNG를 생성한다. 최대 1억 2천만 픽셀 이상인 원본도 있어 디코딩에 CPU 메모리·시간과 추가 디스크 공간이 필요하다. 중단된 캐시 폴더는 재사용하지 말고 `CACHE="$ROOT/cache-v2"`처럼 새 이름을 지정해 다시 실행한다.

## 5. 학습과 개발셋 선택

```bash
"$PY" -B "$P/code/run.py" train --packet "$P" --cache "$CACHE" --model-dir "$MODEL" --reference "$REF" --output "$OUT"
```

각 epoch의 개발 손실이 출력된다. 완료되면 `$OUT/selection.json`, `selection_commit.json`, `selected.safetensors`, 크기별 학습 이력이 생성된다. 원본 체크포인트는 읽기만 한다. 768 학습은 384보다 GPU 메모리를 많이 사용하며 microbatch 1에서도 GPU에 따라 OOM이 날 수 있다. 이 경우 기존 `run-001`을 지우지 말고 `OUT="$ROOT/run-384-only"`로 바꾸어 명령 끝에 `--sizes 384`를 붙여 다시 실행할 수 있다.

## 6. 확정된 모델의 시험 결과 확인

```bash
"$PY" -B "$P/code/run.py" evaluate --packet "$P" --cache "$CACHE" --model-dir "$MODEL" --reference "$REF" --output "$OUT"
cat "$OUT/status.json"
cat "$OUT/evaluation.json"
```

384만 학습했다면 evaluate 명령 끝에도 `--sizes 384`를 붙인다. 이 단계는 선택 파일·입력·기준 모델·실행 코드의 해시를 재확인한 뒤 이전 384 모델과 선택된 모델을 같은 시험 이미지로 비교한다. `evaluation.json`은 각 항목별 양성 수와 혼동 행렬을 포함하고, `predictions.json`은 이미지별 확률을 담는다. 기존 dev/test 4장의 라벨은 이번 새 초안 기준이므로 기존 승인 라벨로 측정한 옛 결과와 완전히 같은 평가가 아니다. 학습과 평가는 모델을 서비스에 배포하지 않는다.

## 7. 결과를 Mac으로 가져오기

Pod 세션을 종료한 뒤 **1단계의 같은 Mac 터미널**에서 실행한다.

```bash
scp -P "$RP_PORT" -r "$RP_USER@$RP_HOST:/workspace/$RUN_TAG/run-001" "$HOME/Downloads/${RUN_TAG}-results"
```

384만 재실행해 `run-384-only`를 썼다면 위의 원격 폴더명도 바꾼다. `evaluation.json`, `selection.json`, `history-*.json`, `status.json`, `predictions.json`, 선택된 safetensors 파일을 함께 보관한다. 데이터 이용 권리와 라벨·유사 행사 중복을 확인하기 전에는 결과를 독립 검증이나 배포 성능으로 표현하지 않는다.
