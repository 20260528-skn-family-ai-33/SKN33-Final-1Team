#!/bin/sh
set -eu
umask 077
mkdir -p /run/ssh
cp /run/secrets/runpod_identity /run/ssh/identity
chmod 600 /run/ssh/identity
exec ssh -N -T \
  -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o UserKnownHostsFile=/run/secrets/runpod_known_hosts \
  -o ConnectTimeout=10 -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -i /run/ssh/identity \
  -L 0.0.0.0:18015:127.0.0.1:8015 \
  -p "$AF_RUNPOD_PORT" "root@$AF_RUNPOD_HOST"
