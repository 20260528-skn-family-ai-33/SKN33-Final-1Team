@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run uv sync --locked first. Configure .env.rebuild from .env.rebuild.example.
  exit /b 1
)
uv run --no-sync --env-file .env.rebuild python start.py %*
exit /b %errorlevel%
