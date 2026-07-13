@echo off
chcp 65001 >nul
title TTS API only (no Cloudflare Tunnel)
cd /d "%~dp0"

echo ========================================
echo   TTS API ONLY  (local :8787)
echo   Khong can cloudflared credential
echo ========================================
echo.

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "APP=%ROOT%\tts-api"
set "VENV_PY=%APP%\.venv\Scripts\python.exe"

if not exist "%APP%\server\main.py" (
  echo [ERROR] Khong tim thay %APP%\server\main.py
  pause
  exit /b 1
)

if not exist "%VENV_PY%" (
  echo [ERROR] Chua co venv.
  echo   powershell -ExecutionPolicy Bypass -File "%ROOT%\bootstrap.ps1"
  echo   hoac: powershell -ExecutionPolicy Bypass -File "%ROOT%\install_tool.ps1"
  pause
  exit /b 1
)

if not exist "%APP%\config\proxies.json" (
  echo [WARN] Copy proxies.example.json -^> proxies.json
  copy /Y "%APP%\config\proxies.example.json" "%APP%\config\proxies.json" >nul
)

if not exist "%APP%\.env" (
  (
    echo TTS_ADMIN_PASSWORD=30102002
    echo TTS_PORT=8787
    echo TTS_PUBLIC_BASE_URL=http://127.0.0.1:8787
  ) > "%APP%\.env"
)

set "PYTHONPATH=%APP%;%ROOT%"
set "TTS_PORT=8787"

echo [0/1] Pin playwright ...
"%VENV_PY%" -m pip install "playwright>=1.48.0,<1.61.0" -q

echo [1/1] Start API http://0.0.0.0:8787 ...
echo.
echo  Admin local: http://127.0.0.1:8787/admin/
echo  Health:      http://127.0.0.1:8787/v1/health
echo  Ctrl+C de tat.
echo.

cd /d "%APP%"
set PYTHONPATH=%APP%;%ROOT%
"%VENV_PY%" -m uvicorn server.main:app --host 0.0.0.0 --port 8787
pause
