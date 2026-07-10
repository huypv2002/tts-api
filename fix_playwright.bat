@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Fix Camoufox/Playwright isMobile error on Windows ===
echo.

set "APP=%~dp0tts-api"
if not exist "%APP%\.venv\Scripts\python.exe" (
  echo ERROR: run bootstrap.ps1 first
  pause
  exit /b 1
)

echo [1/3] git pull...
git pull

echo [2/3] pin playwright less than 1.61 ...
"%APP%\.venv\Scripts\python.exe" -m pip install -U "playwright>=1.48.0,<1.61.0" camoufox tls-client

echo [3/3] done. Restart with start_all.bat
echo.
echo If still error, also run:
echo   "%APP%\.venv\Scripts\python.exe" -m camoufox fetch
echo.
pause
