#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PYTHON="$PROJECT_DIR/.venv/bin/python"
cd "$PROJECT_DIR"
# 가상환경이 없을 때도 Python이 있으면 공통 검사에서 생성 명령을 안내한다.
if [[ ! -x "$PROJECT_PYTHON" ]]; then
  if command -v python3.12 >/dev/null 2>&1; then
    exec python3.12 "$PROJECT_DIR/start.py" "$@"
  elif command -v python3 >/dev/null 2>&1; then
    exec python3 "$PROJECT_DIR/start.py" "$@"
  fi
  echo '[실행 중단] 프로젝트 Python이 없습니다. Python 3.12를 설치한 뒤 저장소에서 실행하세요.' >&2
  echo '기존 .venv가 있다면 다른 이름으로 보관하세요. 자동으로 삭제하거나 설치하지 않습니다.' >&2
  echo 'python3.12 -m venv .venv' >&2
  echo './run.sh --check-only' >&2
  exit 1
fi
exec "$PROJECT_PYTHON" "$PROJECT_DIR/start.py" "$@"
