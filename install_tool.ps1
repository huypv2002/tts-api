# Install local TTS tool on Windows (HSW farm + loop)
# Run from repo root after git clone / pull:
#   powershell -ExecutionPolicy Bypass -File .\install_tool.ps1

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Root) { $Root = (Get-Location).Path }
Set-Location $Root

Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  TTS Local Tool - Windows install' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host "Root = $Root"

# --- Python ---
$Py = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Py = 'py' }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Py = 'python' }
else {
    Write-Host 'ERROR: Python 3.11+ not found. Install from python.org and tick Add to PATH.' -ForegroundColor Red
    exit 1
}

# --- venv at repo root ---
$VenvPy = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPy)) {
    Write-Host '[1/5] Creating venv...' -ForegroundColor Yellow
    if ($Py -eq 'py') {
        & py -3 -m venv .venv
    } else {
        & python -m venv .venv
    }
} else {
    Write-Host '[1/5] venv already exists' -ForegroundColor Green
}

if (-not (Test-Path $VenvPy)) {
    Write-Host 'ERROR: failed to create .venv' -ForegroundColor Red
    exit 1
}

Write-Host '[2/5] pip install requirements-tool.txt ...' -ForegroundColor Yellow
& $VenvPy -m pip install -U pip
$ReqFile = Join-Path $Root 'requirements-tool.txt'
& $VenvPy -m pip install -r $ReqFile

# Quote carefully: Windows PowerShell treats < as an operator inside double quotes
Write-Host '[3/5] Pin playwright below 1.61 for Camoufox on Windows ...' -ForegroundColor Yellow
$PlaywrightPin = 'playwright>=1.48.0,<1.61.0'
& $VenvPy -m pip install -U $PlaywrightPin camoufox tls-client

Write-Host '[4/5] camoufox fetch (download browser, may take a few minutes) ...' -ForegroundColor Yellow
& $VenvPy -m camoufox fetch

# --- proxy config ---
$ProxyCfg = Join-Path $Root '.proxyxoay.json'
$ProxyEx = Join-Path $Root 'proxyxoay.example.json'
if (-not (Test-Path $ProxyCfg)) {
    if (Test-Path $ProxyEx) {
        Copy-Item $ProxyEx $ProxyCfg
        Write-Host '[5/5] Copied proxyxoay.example.json to .proxyxoay.json' -ForegroundColor Yellow
        Write-Host '      EDIT .proxyxoay.json (api_key / user / pass / host / port)' -ForegroundColor Yellow
        try { notepad $ProxyCfg } catch { }
    } else {
        Write-Host '[5/5] Missing proxyxoay.example.json - create .proxyxoay.json manually' -ForegroundColor Red
    }
} else {
    Write-Host '[5/5] .proxyxoay.json already exists - keep as is' -ForegroundColor Green
}

# --- outdir ---
$OutDir = Join-Path $Root 'tts_loop_out'
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Write-Host ''
Write-Host '========================================' -ForegroundColor Green
Write-Host '  INSTALL OK' -ForegroundColor Green
Write-Host '========================================' -ForegroundColor Green
Write-Host 'Run test:'
Write-Host '  .\run_loop.bat'
Write-Host '  or:'
Write-Host '  .\.venv\Scripts\python.exe -u fast_tts_loop.py --count 5 --workers 4 --hsw-workers 3'
Write-Host ''
Write-Host 'Docs: TOOL_WINDOWS.md'
