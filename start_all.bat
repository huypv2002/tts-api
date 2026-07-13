@echo off
chcp 65001 >nul
title TTS API + Cloudflare Tunnel
cd /d "%~dp0"

echo ========================================
echo   TTS API + Cloudflare Tunnel
echo ========================================
echo.

REM Resolve paths (repo root = this .bat folder)
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "APP=%ROOT%\tts-api"
set "VENV_PY=%APP%\.venv\Scripts\python.exe"
set "CF_CFG=%APP%\cloudflared-config.yml"
set "CF_JSON=%USERPROFILE%\.cloudflared\09b4bc94-43c3-4b4a-919b-16d680f927fd.json"
set "RUN_TUNNEL=1"

if not exist "%APP%\server\main.py" (
  echo [ERROR] Khong tim thay %APP%\server\main.py
  echo Hay dat start_all.bat o thu muc clone: C:\TTS\tts-api\
  pause
  exit /b 1
)

if not exist "%VENV_PY%" (
  echo [ERROR] Chua co venv. Chay bootstrap.ps1 truoc:
  echo   powershell -ExecutionPolicy Bypass -File "%ROOT%\bootstrap.ps1"
  pause
  exit /b 1
)

if not exist "%APP%\config\proxies.json" (
  echo [WARN] Thieu config\proxies.json - copy tu example...
  copy /Y "%APP%\config\proxies.example.json" "%APP%\config\proxies.json" >nul
)

if not exist "%APP%\.env" (
  echo [WARN] Thieu .env - tao mac dinh...
  (
    echo TTS_ADMIN_PASSWORD=30102002
    echo TTS_PORT=8787
    echo TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
  ) > "%APP%\.env"
)

REM --- Tunnel credential (optional if START_API_ONLY=1) ---
if /I "%START_API_ONLY%"=="1" set "RUN_TUNNEL=0"

if not exist "%USERPROFILE%\.cloudflared" mkdir "%USERPROFILE%\.cloudflared"

if not exist "%CF_JSON%" (
  echo [WARN] Thieu tunnel credential:
  echo   %CF_JSON%
  echo.
  echo Se chi start API local (khong co https://tts-origin.liveyt.pro).
  echo.
  echo De bat tunnel, copy file tu Mac:
  echo   Mac:  ~/.cloudflared/09b4bc94-43c3-4b4a-919b-16d680f927fd.json
  echo   Win:  %USERPROFILE%\.cloudflared\
  echo.
  echo Hoac chay: start_api_only.bat
  echo.
  set "RUN_TUNNEL=0"
)

if "%RUN_TUNNEL%"=="1" (
  (
    echo tunnel: 09b4bc94-43c3-4b4a-919b-16d680f927fd
    echo credentials-file: %CF_JSON%
    echo.
    echo ingress:
    echo   - hostname: tts-origin.liveyt.pro
    echo     service: http://127.0.0.1:8787
    echo   - service: http_status:404
  ) > "%CF_CFG%"

  where cloudflared >nul 2>&1
  if errorlevel 1 (
    echo [WARN] cloudflared chua co trong PATH — chi start API.
    echo Cai: winget install Cloudflare.cloudflared
    set "RUN_TUNNEL=0"
  )
)

set "PYTHONPATH=%APP%;%ROOT%"
set "TTS_PORT=8787"
if "%RUN_TUNNEL%"=="1" (
  set "TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro"
) else (
  set "TTS_PUBLIC_BASE_URL=http://127.0.0.1:8787"
)

echo [0/2] Check playwright version...
"%VENV_PY%" -m pip install "playwright>=1.48.0,<1.61.0" -q

echo [1/2] Start API server :8787 ...
start "TTS-API-Server" cmd /k "cd /d "%APP%" && set PYTHONPATH=%APP%;%ROOT% && set TTS_PORT=8787 && "%VENV_PY%" -m uvicorn server.main:app --host 0.0.0.0 --port 8787"

timeout /t 3 /nobreak >nul

if "%RUN_TUNNEL%"=="1" (
  echo [2/2] Start Cloudflare Tunnel ...
  echo [NOTE] Neu Mac van dang chay tunnel cung ID, hay TAT tunnel tren Mac truoc.
  start "TTS-Cloudflare-Tunnel" cmd /k "cloudflared tunnel --config "%CF_CFG%" run 09b4bc94-43c3-4b4a-919b-16d680f927fd"
) else (
  echo [2/2] SKIP tunnel — API chi local.
)

echo.
echo ========================================
echo  TTS-API-Server dang mo
if "%RUN_TUNNEL%"=="1" (
  echo  + TTS-Cloudflare-Tunnel
  echo.
  echo  Local : http://127.0.0.1:8787/admin/
  echo  Public: https://tts-origin.liveyt.pro/admin/
) else (
  echo.
  echo  Local only: http://127.0.0.1:8787/admin/
  echo  Health:     http://127.0.0.1:8787/v1/health
  echo.
  echo  Muon public domain: copy credential cloudflared (xem TOOL_WINDOWS.md)
)
echo ========================================
echo.
pause
