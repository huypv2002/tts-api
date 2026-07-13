@echo off
chcp 65001 >nul
title TTS-API-Server
cd /d "%~dp0"

REM Parent repo root (contains fast_tts.py)
set "ROOT=%~dp0.."
for %%I in ("%ROOT%") do set "ROOT=%%~fI"
set "APP=%~dp0"
for %%I in ("%APP%") do set "APP=%%~fI"
set "VENV_PY=%APP%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [ERROR] Missing venv: %VENV_PY%
  echo Run bootstrap.ps1 or install_tool.ps1 first.
  pause
  exit /b 1
)

set "PYTHONPATH=%APP%;%ROOT%"
set "TTS_PORT=8787"
if "%TTS_PUBLIC_BASE_URL%"=="" set "TTS_PUBLIC_BASE_URL=http://127.0.0.1:8787"

echo ========================================
echo  TTS API Server
echo  APP = %APP%
echo  ROOT= %ROOT%
echo  URL = http://127.0.0.1:8787/admin/
echo ========================================
echo.

"%VENV_PY%" -m uvicorn server.main:app --host 0.0.0.0 --port 8787
echo.
echo API exited code %ERRORLEVEL%
pause
