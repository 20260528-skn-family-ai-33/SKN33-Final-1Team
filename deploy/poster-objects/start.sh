#!/usr/bin/env bash
# 포스터 Qwen Pod의 OWLv2 포스터 객체 워커를 띄운다. 설치는 setup.sh가 한 번만 한다.
# Pod를 다시 켤 때마다 이 스크립트만 실행한다:
#   tmux new -d -s owl bash /opt/creative-checker-owl/repository/deploy/poster-objects/start.sh
# 127.0.0.1:8791에만 열고, worker EC2의 qwen-tunnel이 18791로 전달한다(deploy/agent-framework/runpod-tunnel.sh).
set -euo pipefail

OWL_ROOT="${OWL_ROOT:-/opt/creative-checker-owl}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PORT="${OWL_PORT:-8791}"
# RunPod은 SSH 터널로 붙으므로 127.0.0.1. AWS GPU EC2처럼 worker가 VPC 사설 IP로 직접 붙을 때는
# OWL_HOST=<사설 IP>로 그 주소에만 연다(보안 그룹은 worker EC2 → 8791만 허용, 토큰 인증 유지).
HOST="${OWL_HOST:-127.0.0.1}"

test -x "$OWL_ROOT/venv/bin/python" || { echo "venv가 없습니다. 먼저 setup.sh를 실행하세요." >&2; exit 1; }
test -s "$OWL_ROOT/worker-token" || { echo "워커 토큰이 없습니다. 먼저 setup.sh를 실행하세요." >&2; exit 1; }

export HF_HOME="$OWL_ROOT/hf-cache"
export HF_HUB_OFFLINE=1                      # setup.sh가 받아 둔 가중치만 쓴다
export VIDEO_LOGO_WORKER_ROLE=poster-objects  # poster_objects 작업만 받는다
VIDEO_LOGO_WORKER_TOKEN="$(cat "$OWL_ROOT/worker-token")"
export VIDEO_LOGO_WORKER_TOKEN

cd "$REPO"
exec "$OWL_ROOT/venv/bin/python" -m uvicorn backend.app.video_logo_worker:app \
  --host "$HOST" --port "$PORT" >> "$OWL_ROOT/worker.log" 2>&1
