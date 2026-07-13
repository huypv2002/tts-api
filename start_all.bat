@echo off
setlocal EnableExtensions
chcp 65001 >nul
title TTS start_all
cd /d "%~dp0"

echo ========================================
echo   TTS API + Cloudflare Tunnel
echo ========================================
echo.
echo Working dir: %CD%
echo.

set "ROOT=%CD%"
set "APP=%ROOT%\tts-api"
set "VENV_PY=%APP%\.venv\Scripts\python.exe"
set "CF_JSON=%USERPROFILE%\.cloudflared\09b4bc94-43c3-4b4a-919b-16d680f927fd.json"
set "RUN_TUNNEL=1"
set "LOG=%ROOT%\start_all_log.txt"

echo start_all %DATE% %TIME% > "%LOG%"
echo ROOT=%ROOT%>> "%LOG%"
echo APP=%APP%>> "%LOG%"

if not exist "%APP%\server\main.py" (
  echo [ERROR] Khong thay %APP%\server\main.py
  echo Clone/path sai? Can co: C:\TTS\tts-api\tts-api\server\main.py
  echo ROOT hien tai: %ROOT%
  echo.>> "%LOG%" & echo ERROR no main.py>> "%LOG%"
  goto :END
)

if not exist "%VENV_PY%" (
  echo [ERROR] Chua co venv:
  echo   %VENV_PY%
  echo.
  echo Chay 1 lan:
  echo   powershell -ExecutionPolicy Bypass -File "%ROOT%\bootstrap.ps1"
  echo.>> "%LOG%" & echo ERROR no venv>> "%LOG%"
  goto :END
)

if not exist "%APP%\config\proxies.json" (
  echo [WARN] Copy proxies.example.json
  copy /Y "%APP%\config\proxies.example.json" "%APP%\config\proxies.json" >nul
)

if not exist "%APP%\.env" (
  echo [WARN] Tao .env mac dinh
  (
    echo TTS_ADMIN_PASSWORD=30102002
    echo TTS_PORT=8787
    echo TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
  ) > "%APP%\.env"
)

if not exist "%USERPROFILE%\.cloudflared" mkdir "%USERPROFILE%\.cloudflared" 2>nul

if not exist "%CF_JSON%" (
  echo [WARN] Thieu tunnel credential - chi start API local
  echo   Can: %CF_JSON%
  set "RUN_TUNNEL=0"
)

where cloudflared >nul 2>&1
if errorlevel 1 (
  if "%RUN_TUNNEL%"=="1" (
    echo [WARN] cloudflared khong co trong PATH - chi start API
    echo   winget install Cloudflare.cloudflared
    set "RUN_TUNNEL=0"
  )
)

echo [0/2] Pin playwright ...
"%VENV_PY%" -m pip install "playwright>=1.48.0,<1.61.0" -q
if errorlevel 1 (
  echo [WARN] pip playwright fail - van thu start API
)

echo [1/2] Mo cua so TTS-API-Server ...
if not exist "%APP%\run_api_window.bat" (
  echo [ERROR] Thieu %APP%\run_api_window.bat - git pull lai
  goto :END
)
start "TTS-API-Server" /D "%APP%" cmd /k call run_api_window.bat
echo     OK started API window >> "%LOG%"

REM short wait (timeout may be disabled by policy - ignore error)
ping -n 4 127.0.0.1 >nul 2>&1

if "%RUN_TUNNEL%"=="1" (
  echo [2/2] Mo cua so TTS-Cloudflare-Tunnel ...
  start "TTS-Cloudflare-Tunnel" /D "%APP%" cmd /k call run_tunnel_window.bat
  echo     OK started tunnel window >> "%LOG%"
) else (
  echo [2/2] SKIP tunnel
  echo     SKIP tunnel >> "%LOG%"
)

echo.
echo ========================================
echo  Da goi start (xem 1-2 cua so moi)
echo.
echo  Local admin: http://127.0.0.1:8787/admin/
echo  Health:      http://127.0.0.1:8787/v1/health
if "%RUN_TUNNEL%"=="1" (
  echo  Public:      https://tts-origin.liveyt.pro/admin/
)
echo.
echo  Log: %LOG%
echo  Neu cua so API do ngay: doc loi trong cua so do.
echo ========================================
echo.

:END
echo.
echo Nhan phim bat ky de dong cua so nay...
pause >nul
endlocal
