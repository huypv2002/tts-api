@echo off
chcp 65001 >nul
title TTS Local Loop
cd /d "%~dp0"

set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
  echo [ERROR] Chua co venv. Chay truoc:
  echo   powershell -ExecutionPolicy Bypass -File "%ROOT%\install_tool.ps1"
  pause
  exit /b 1
)

if not exist "%ROOT%\.proxyxoay.json" (
  echo [ERROR] Thieu .proxyxoay.json
  echo Copy proxyxoay.example.json -^> .proxyxoay.json va dien key
  pause
  exit /b 1
)

if not exist "%ROOT%\fast_tts_loop.py" (
  echo [ERROR] Thieu fast_tts_loop.py — git pull repo truoc
  pause
  exit /b 1
)

REM Defaults — override: run_loop.bat 20 6 3
set "COUNT=%~1"
set "WORKERS=%~2"
set "HSW=%~3"
if "%COUNT%"=="" set "COUNT=10"
if "%WORKERS%"=="" set "WORKERS=4"
if "%HSW%"=="" set "HSW=3"

set "OUTDIR=%ROOT%\tts_loop_out"
set "TEXT=%ROOT%\long_text.txt"
if not exist "%TEXT%" set "TEXT="

echo ========================================
echo   TTS Local Loop
echo   count=%COUNT% workers=%WORKERS% hsw=%HSW%
echo   out=%OUTDIR%
echo ========================================
echo.

if exist "%TEXT%" (
  "%VENV_PY%" -u "%ROOT%\fast_tts_loop.py" --count %COUNT% --workers %WORKERS% --hsw-workers %HSW% --outdir "%OUTDIR%" --text-file "%TEXT%" --lang en
) else (
  "%VENV_PY%" -u "%ROOT%\fast_tts_loop.py" --count %COUNT% --workers %WORKERS% --hsw-workers %HSW% --outdir "%OUTDIR%" --text "Hello from Windows TTS tool." --lang en
)

echo.
echo Exit code: %ERRORLEVEL%
pause
