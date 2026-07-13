@echo off
setlocal EnableExtensions
chcp 65001 >nul
title TTS API only
cd /d "%~dp0"

echo ========================================
echo   TTS API ONLY  (local :8787)
echo ========================================
echo.

set "ROOT=%CD%"
set "APP=%ROOT%\tts-api"
set "VENV_PY=%APP%\.venv\Scripts\python.exe"

if not exist "%APP%\server\main.py" (
  echo [ERROR] Khong thay %APP%\server\main.py
  echo Path: %APP%
  goto :END
)

if not exist "%VENV_PY%" (
  echo [ERROR] Chua co venv: %VENV_PY%
  echo Chay bootstrap.ps1 hoac install_tool.ps1
  goto :END
)

if not exist "%APP%\config\proxies.json" (
  copy /Y "%APP%\config\proxies.example.json" "%APP%\config\proxies.json" >nul
)

if not exist "%APP%\.env" (
  (
    echo TTS_ADMIN_PASSWORD=30102002
    echo TTS_PORT=8787
    echo TTS_PUBLIC_BASE_URL=http://127.0.0.1:8787
  ) > "%APP%\.env"
)

if not exist "%APP%\run_api_window.bat" (
  echo [ERROR] Thieu run_api_window.bat - git pull
  goto :END
)

echo Mo cua so API (giu mo)...
start "TTS-API-Server" /D "%APP%" cmd /k call run_api_window.bat

echo.
echo Admin: http://127.0.0.1:8787/admin/
echo Health: http://127.0.0.1:8787/v1/health
echo.

:END
echo Nhan phim bat ky de dong...
pause >nul
endlocal
