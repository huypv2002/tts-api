@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Reset TTS admin password

set "APP=%~dp0tts-api"
set "ENV=%APP%\.env"
set "SET=%APP%\config\settings.json"
set "PW=30102002"

echo ========================================
echo  Reset admin password = %PW%
echo  App: %APP%
echo ========================================
echo.

if not exist "%APP%\server\main.py" (
  echo [ERROR] Khong thay %APP%\server\main.py
  pause
  exit /b 1
)

if not exist "%APP%\config" mkdir "%APP%\config"

REM 1) .env  (load_settings uu tien TTS_ADMIN_PASSWORD)
(
  echo TTS_ADMIN_PASSWORD=%PW%
  echo TTS_PORT=8787
  echo TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
) > "%ENV%"
echo [OK] Wrote %ENV%

REM 2) settings.json via python (neu co venv)
set "PY=%APP%\.venv\Scripts\python.exe"
if exist "%PY%" (
  "%PY%" -c "import json,pathlib; p=pathlib.Path(r'%SET%'); d=json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}; d['admin_password']='%PW%'; d.setdefault('public_base_url','https://tts-origin.liveyt.pro'); p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+chr(10),encoding='utf-8'); print('OK settings.json')"
) else (
  echo [WARN] Chua co venv — chi ghi .env. Restart API van du.
)

echo.
echo ========================================
echo  XONG. BAT BUOC:
echo   1) Tat cua so TTS-API-Server ^(Ctrl+C^)
echo   2) Chay lai start_all.bat
echo   3) Login admin: %PW%
echo ========================================
echo.
pause
