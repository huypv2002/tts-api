@echo off
chcp 65001 >nul
title Stop TTS API + Tunnel
echo Dang tat uvicorn / cloudflared lien quan TTS...

REM Kill cloudflared for this tunnel
taskkill /FI "WINDOWTITLE eq TTS-Cloudflare-Tunnel*" /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq TTS-API-Server*" /F >nul 2>&1

REM Fallback by image name (careful: kills all cloudflared + matching python uvicorn)
for /f "tokens=2 delims=," %%p in ('tasklist /FI "IMAGENAME eq cloudflared.exe" /FO CSV /NH 2^>nul') do (
  taskkill /PID %%~p /F >nul 2>&1
)

echo.
echo Done. Neu con process, mo Task Manager tat python.exe / cloudflared.exe.
pause
