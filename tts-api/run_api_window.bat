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
if "%TTS_ADMIN_PASSWORD%"=="" set "TTS_ADMIN_PASSWORD=30102002"
if "%TTS_PUBLIC_BASE_URL%"=="" set "TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro"

echo ========================================
echo  TTS API Server  (origin cho Cloudflare Tunnel)
echo  APP  = %APP%
echo  ROOT = %ROOT%
echo  Bind = http://0.0.0.0:8787
echo  Public via tunnel:
echo         https://tts-origin.liveyt.pro/admin/
echo  Local:
echo         http://127.0.0.1:8787/admin/
echo ========================================
echo.
echo  LUU Y: uvicorn luon listen local 8787.
echo  cloudflared (cua so TTS-Cloudflare-Tunnel) moi la
echo  cai publish ra tts-origin.liveyt.pro
echo.

"%VENV_PY%" -m uvicorn server.main:app --host 0.0.0.0 --port 8787
echo.
echo API exited code %ERRORLEVEL%
pause
