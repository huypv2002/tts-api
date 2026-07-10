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

if not exist "%APP%\server\main.py" (
  echo [ERROR] Khong tim thay %APP%\server\main.py
  echo Hay dat start_all.bat o thu muc clone: C:\tts-api\
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
    echo TTS_ADMIN_PASSWORD=admin123
    echo TTS_PORT=8787
    echo TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
  ) > "%APP%\.env"
)

REM Ensure cloudflared credentials folder
if not exist "%USERPROFILE%\.cloudflared" mkdir "%USERPROFILE%\.cloudflared"

if not exist "%CF_JSON%" (
  echo [ERROR] Thieu file tunnel credential:
  echo   %CF_JSON%
  echo.
  echo Copy tu Mac:
  echo   ~/.cloudflared/09b4bc94-43c3-4b4a-919b-16d680f927fd.json
  echo vao:
  echo   %USERPROFILE%\.cloudflared\
  echo.
  pause
  exit /b 1
)

REM Write / refresh cloudflared config for this machine
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
  echo [ERROR] cloudflared chua co trong PATH.
  echo Cai: winget install Cloudflare.cloudflared
  pause
  exit /b 1
)

set "PYTHONPATH=%APP%;%ROOT%"
set "TTS_PORT=8787"
set "TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro"

echo [1/2] Start API server :8787 ...
start "TTS-API-Server" cmd /k "cd /d "%APP%" && set PYTHONPATH=%APP%;%ROOT% && set TTS_PORT=8787 && "%VENV_PY%" -m uvicorn server.main:app --host 0.0.0.0 --port 8787"

timeout /t 3 /nobreak >nul

echo [2/2] Start Cloudflare Tunnel ...
start "TTS-Cloudflare-Tunnel" cmd /k "cloudflared tunnel --config "%CF_CFG%" run 09b4bc94-43c3-4b4a-919b-16d680f927fd"

echo.
echo ========================================
echo  Da mo 2 cua so:
echo    - TTS-API-Server
echo    - TTS-Cloudflare-Tunnel
echo.
echo  Local : http://127.0.0.1:8787/admin/
echo  Public: https://tts-origin.liveyt.pro/admin/
echo.
echo  Dung Ctrl+C trong tung cua so de tat.
echo  (Mac dang chay tunnel thi hay tat Mac truoc)
echo ========================================
echo.
pause
