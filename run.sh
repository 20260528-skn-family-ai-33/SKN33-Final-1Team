#!/usr/bin/env bash
set -euo pipefail
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_PYTHON="$PROJECT_DIR/.venv-review/bin/python"
if [[ ! -x "$PROJECT_PYTHON" ]]; then
  echo '검수용 환경이 없습니다. 저장소에서 python3.12 setup_local.py --download-models를 먼저 실행하세요.' >&2
  exit 1
fi
cd "$PROJECT_DIR"
exec "$PROJECT_PYTHON" "$PROJECT_DIR/start.py" "$@"
