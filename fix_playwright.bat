@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Fix Camoufox/Playwright isMobile error on Windows ===
echo.

REM Prefer root tool venv, then tts-api venv
set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0tts-api\.venv\Scripts\python.exe" set "PY=%~dp0tts-api\.venv\Scripts\python.exe"

if not defined PY (
  echo ERROR: khong thay .venv
  echo Chay: powershell -ExecutionPolicy Bypass -File install_tool.ps1
  echo   hoac bootstrap.ps1 cho API
  pause
  exit /b 1
)

echo Using: %PY%
echo [1/3] git pull...
git pull

echo [2/3] pin playwright less than 1.61 ...
"%PY%" -m pip install -U "playwright>=1.48.0,<1.61.0" camoufox tls-client

echo [3/3] camoufox fetch ...
"%PY%" -m camoufox fetch

echo.
echo Done. Chay lai run_loop.bat hoac start_all.bat
pause
