#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ -n "${CHECKER_PYTHON:-}" ]; then
  checker_python="$CHECKER_PYTHON"
elif [ -n "${CONDA_PREFIX:-}" ] && [ -x "$CONDA_PREFIX/bin/python" ]; then
  checker_python="$CONDA_PREFIX/bin/python"
else
  checker_python=".venv/bin/python"
fi
if [ ! -x "$checker_python" ]; then
  echo '실행할 Python이 없습니다. Conda 환경을 활성화하거나 README.md의 .venv 설치 단계를 실행하세요.' >&2
  exit 1
fi
printf '사용할 Python: %s\n' "$checker_python"
"$checker_python" manage.py prepare_local_db
exec "$checker_python" -m uvicorn config.asgi:application --host 127.0.0.1 --port "${CHECKER_PORT:-8000}"
