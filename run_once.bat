@echo off
chcp 65001 >nul
title TTS Once
cd /d "%~dp0"

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [ERROR] Chay install_tool.ps1 truoc
  pause
  exit /b 1
)

set "TEXT=%~1"
if "%TEXT%"=="" set "TEXT=Hello from fast HSW TTS on Windows."

echo TTS once: %TEXT%
"%VENV_PY%" -u "%ROOT%\fast_tts.py" "%TEXT%" --proxyxoay -o "%ROOT%\fast_tts_out.mp3"
echo Exit: %ERRORLEVEL%
pause
