#!/bin/sh
set -eu
umask 077
mkdir -p /run/ssh
cp /run/secrets/runpod_identity /run/ssh/identity
chmod 600 /run/ssh/identity
# 18015 → Qwen(vLLM) 8015, 18791 → 같은 Pod의 OWLv2 포스터 객체 워커 8791(deploy/poster-objects).
# OWLv2 워커가 없거나 Pod 키가 8791을 허용하지 않아도 터널은 유지되고, A04/A05는 OWLv2 후보만 건너뛴다.
exec ssh -N -T \
  -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/run/secrets/runpod_known_hosts \
  -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -i /run/ssh/identity \
  -L 0.0.0.0:18015:127.0.0.1:8015 \
  -L 0.0.0.0:18791:127.0.0.1:8791 \
  -p "$AF_RUNPOD_PORT" "root@$AF_RUNPOD_HOST"
