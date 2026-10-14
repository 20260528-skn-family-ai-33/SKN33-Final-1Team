@echo off
setlocal
cd /d "%~dp0"
set "PROJECT_PYTHON=%~dp0.venv\Scripts\python.exe"
rem Python이 있으면 공통 검사를 사용하고, 설치 명령은 안내만 합니다.
if exist "%PROJECT_PYTHON%" goto project_python
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 goto system_python
echo [STOP] Project .venv Python is missing. Run these commands manually from this folder:
echo Preserve an existing .venv under another name before creating a Windows environment.
echo If uv is missing: winget install --id astral-sh.uv --exact
echo uv venv --python 3.12 --seed .venv
echo .\run.cmd --check-only
exit /b 1
:system_python
py -3.12 "%~dp0start.py" %*
exit /b %errorlevel%
:project_python
"%PROJECT_PYTHON%" "%~dp0start.py" %*
exit /b %errorlevel%
