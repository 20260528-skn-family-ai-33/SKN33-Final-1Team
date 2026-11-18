#!/usr/bin/env bash
# 포스터 Qwen Pod에 OWLv2 포스터 객체 워커를 한 번만 설치한다. 다시 실행해도 이미 있는 venv·토큰·모델은 그대로 둔다.
# 실행: 포스터 RunPod Terminal에서 이 저장소를 받은 뒤
#   bash deploy/poster-objects/setup.sh
# venv·모델 캐시·토큰은 Pod를 다시 켜도 남는 /workspace 아래에 둔다(컨테이너 디스크 /opt·/root는 지워질 수 있다).
# Qwen(vLLM)의 /opt/creative-checker-a02 venv와 저장소는 건드리지 않는다.
set -euo pipefail

OWL_ROOT="${OWL_ROOT:-/workspace/creative-checker-owl}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="$OWL_ROOT/venv"
export HF_HOME="$OWL_ROOT/hf-cache"

umask 077
mkdir -p "$OWL_ROOT" "$HF_HOME"

if [ ! -x "$VENV/bin/python" ]; then
  if python3 -c 'import sys, torch, torchvision; sys.exit(0 if torch.__version__.startswith("2.8.0") and torch.cuda.is_available() else 1)' 2>/dev/null; then
    echo "[setup] Pod 기본 torch(CUDA)를 재사용하는 venv를 만든다"
    python3 -m venv --system-site-packages "$VENV"
  else
    echo "[setup] Pod 기본 torch가 없거나 버전이 달라 cu128 torch를 venv에 설치한다"
    python3 -m venv "$VENV"
    "$VENV/bin/python" -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cu128
  fi
else
  echo "[setup] 기존 venv 재사용: $VENV"
fi

"$VENV/bin/python" -m pip install -r "$REPO/deploy/poster-objects/requirements.txt"

if [ ! -s "$OWL_ROOT/worker-token" ]; then
  openssl rand -hex 32 > "$OWL_ROOT/worker-token"
  echo "[setup] 워커 토큰을 새로 만들었다: $OWL_ROOT/worker-token (값은 출력하지 않는다. worker EC2 env의 VIDEO_LOGO_WORKER_TOKEN에 같은 값을 넣는다)"
fi
chmod 600 "$OWL_ROOT/worker-token"

echo "[setup] OWLv2 가중치를 $HF_HOME 에 받아 둔다(이미 있으면 건너뜀)"
cd "$REPO"
"$VENV/bin/python" - <<'PY'
from transformers import AutoProcessor, Owlv2ForObjectDetection
from backend.app.video.logos.models import OWL_MODEL
AutoProcessor.from_pretrained(OWL_MODEL); Owlv2ForObjectDetection.from_pretrained(OWL_MODEL)
import torch
print("[setup] torch", torch.__version__, "cuda", torch.cuda.is_available())
PY

echo "[setup] 완료. 실행: tmux new -d -s owl bash $REPO/deploy/poster-objects/start.sh"
