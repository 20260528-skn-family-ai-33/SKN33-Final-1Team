@echo off
setlocal
cd /d "%~dp0"
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
if not exist "%PROJECT_PYTHON%" (
  echo Project .venv is missing. Run this from the repository root:
  echo py -3.12 setup_local.py --download-models
  exit /b 1
)
"%PROJECT_PYTHON%" "%~dp0start.py" %*
exit /b %errorlevel%
