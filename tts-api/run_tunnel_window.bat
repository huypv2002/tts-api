@echo off
chcp 65001 >nul
title TTS-Cloudflare-Tunnel
cd /d "%~dp0"

set "CF_CFG=%~dp0cloudflared-config.yml"
set "CF_JSON=%USERPROFILE%\.cloudflared\09b4bc94-43c3-4b4a-919b-16d680f927fd.json"
set "TUNNEL_ID=09b4bc94-43c3-4b4a-919b-16d680f927fd"

if not exist "%CF_JSON%" (
  echo [ERROR] Missing credential:
  echo   %CF_JSON%
  pause
  exit /b 1
)

(
  echo tunnel: %TUNNEL_ID%
  echo credentials-file: %CF_JSON%
  echo.
  echo ingress:
  echo   - hostname: tts-origin.liveyt.pro
  echo     service: http://127.0.0.1:8787
  echo   - service: http_status:404
) > "%CF_CFG%"

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo [ERROR] cloudflared not in PATH
  echo Install: winget install Cloudflare.cloudflared
  pause
  exit /b 1
)

echo Starting tunnel %TUNNEL_ID% ...
echo Config: %CF_CFG%
echo.
cloudflared tunnel --config "%CF_CFG%" run %TUNNEL_ID%
echo.
echo Tunnel exited code %ERRORLEVEL%
pause
