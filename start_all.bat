@echo off
setlocal EnableExtensions
chcp 65001 >nul
title TTS API + Cloudflare Tunnel
cd /d "%~dp0"

echo ========================================
echo   TTS API + Cloudflare Tunnel
echo   Public: https://tts-origin.liveyt.pro
echo ========================================
echo.

set "ROOT=%CD%"
set "APP=%ROOT%\tts-api"
set "VENV_PY=%APP%\.venv\Scripts\python.exe"
set "CF_CFG=%APP%\cloudflared-config.yml"
set "CF_JSON=%USERPROFILE%\.cloudflared\09b4bc94-43c3-4b4a-919b-16d680f927fd.json"
set "TUNNEL_ID=09b4bc94-43c3-4b4a-919b-16d680f927fd"
set "LOG=%ROOT%\start_all_log.txt"

echo start_all %DATE% %TIME% > "%LOG%"
echo ROOT=%ROOT%>> "%LOG%"

if not exist "%APP%\server\main.py" (
  echo [ERROR] Khong thay %APP%\server\main.py
  echo Can layout: C:\TTS\tts-api\tts-api\server\main.py
  goto :END
)

if not exist "%VENV_PY%" (
  echo [ERROR] Chua co venv: %VENV_PY%
  echo Chay: powershell -ExecutionPolicy Bypass -File "%ROOT%\bootstrap.ps1"
  goto :END
)

if not exist "%APP%\config\proxies.json" (
  copy /Y "%APP%\config\proxies.example.json" "%APP%\config\proxies.json" >nul
)

if not exist "%APP%\.env" (
  (
    echo TTS_ADMIN_PASSWORD=30102002
    echo TTS_PORT=8787
    echo TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
  ) > "%APP%\.env"
)

REM === Tunnel credential BAT BUOC (nhu ban dau) ===
if not exist "%USERPROFILE%\.cloudflared" mkdir "%USERPROFILE%\.cloudflared"

if not exist "%CF_JSON%" (
  echo [ERROR] Thieu file tunnel credential:
  echo   %CF_JSON%
  echo.
  echo Copy tu Mac 1 lan:
  echo   Mac:  ~/.cloudflared/09b4bc94-43c3-4b4a-919b-16d680f927fd.json
  echo   Win:  %USERPROFILE%\.cloudflared\
  echo.
  echo Khong co file nay thi KHONG co https://tts-origin.liveyt.pro
  echo ERROR missing CF_JSON>> "%LOG%"
  goto :END
)

where cloudflared >nul 2>&1
if errorlevel 1 (
  echo [ERROR] cloudflared chua cai / khong co trong PATH
  echo   winget install Cloudflare.cloudflared
  echo   roi MO LAI CMD moi
  echo ERROR no cloudflared>> "%LOG%"
  goto :END
)

REM Viet config tunnel (API local 8787 -^> domain public)
(
  echo tunnel: %TUNNEL_ID%
  echo credentials-file: %CF_JSON%
  echo.
  echo ingress:
  echo   - hostname: tts-origin.liveyt.pro
  echo     service: http://127.0.0.1:8787
  echo   - service: http_status:404
) > "%CF_CFG%"
echo Wrote %CF_CFG%>> "%LOG%"

echo [0/2] Pin playwright ...
"%VENV_PY%" -m pip install "playwright>=1.48.0,<1.61.0" -q

echo [1/2] Start API 0.0.0.0:8787  (tunnel se forward domain -^> day)
if not exist "%APP%\run_api_window.bat" (
  echo [ERROR] Thieu run_api_window.bat - git pull
  goto :END
)
set "TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro"
start "TTS-API-Server" /D "%APP%" cmd /k "set TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro&& call run_api_window.bat"

ping -n 4 127.0.0.1 >nul 2>&1

echo [2/2] Start Cloudflare Tunnel -^> https://tts-origin.liveyt.pro
if not exist "%APP%\run_tunnel_window.bat" (
  echo [ERROR] Thieu run_tunnel_window.bat - git pull
  goto :END
)
start "TTS-Cloudflare-Tunnel" /D "%APP%" cmd /k call run_tunnel_window.bat

echo.
echo ========================================
echo  Da mo 2 cua so:
echo    1) TTS-API-Server     = API port 8787
echo    2) TTS-Cloudflare-Tunnel = public domain
echo.
echo  API LUON bind local 8787 — DO LA DUNG.
echo  Tunnel lay 8787 day ra:
echo    https://tts-origin.liveyt.pro/admin/
echo    https://tts-origin.liveyt.pro/v1/health
echo.
echo  Cua so tunnel phai hien Connected / registered.
echo  Neu tunnel do ngay: thieu credential hoac cloudflared.
echo ========================================
echo.
echo OK started both>> "%LOG%"

:END
echo.
echo Log: %LOG%
echo Nhan phim bat ky de dong cua so nay (2 cua so API/Tunnel van chay)...
pause >nul
endlocal
