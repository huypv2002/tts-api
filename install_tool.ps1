# Install local TTS tool on Windows (HSW farm + loop)
# Run from repo root after git clone / pull:
#   powershell -ExecutionPolicy Bypass -File .\install_tool.ps1

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Root) { $Root = (Get-Location).Path }
Set-Location $Root

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  TTS Local Tool — Windows install" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "Root = $Root"

# --- Python ---
$Py = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Py = "py" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Py = "python" }
else {
    Write-Host "ERROR: chua cai Python 3.11+ (python.org, tick Add to PATH)" -ForegroundColor Red
    exit 1
}

# --- venv at repo root ---
$VenvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    Write-Host "[1/5] Tao venv..." -ForegroundColor Yellow
    if ($Py -eq "py") { & py -3 -m venv .venv } else { & python -m venv .venv }
} else {
    Write-Host "[1/5] venv da co" -ForegroundColor Green
}

if (-not (Test-Path $VenvPy)) {
    Write-Host "ERROR: khong tao duoc .venv" -ForegroundColor Red
    exit 1
}

Write-Host "[2/5] pip install requirements-tool.txt ..." -ForegroundColor Yellow
& $VenvPy -m pip install -U pip
& $VenvPy -m pip install -r (Join-Path $Root "requirements-tool.txt")

Write-Host "[3/5] Pin playwright < 1.61 (Camoufox Windows) ..." -ForegroundColor Yellow
& $VenvPy -m pip install -U "playwright>=1.48.0,<1.61.0" camoufox tls-client

Write-Host "[4/5] camoufox fetch (tai browser, co the mat vai phut) ..." -ForegroundColor Yellow
& $VenvPy -m camoufox fetch

# --- proxy config ---
$ProxyCfg = Join-Path $Root ".proxyxoay.json"
$ProxyEx = Join-Path $Root "proxyxoay.example.json"
if (-not (Test-Path $ProxyCfg)) {
    if (Test-Path $ProxyEx) {
        Copy-Item $ProxyEx $ProxyCfg
        Write-Host "[5/5] Da copy proxyxoay.example.json -> .proxyxoay.json" -ForegroundColor Yellow
        Write-Host "      HAY SUA file .proxyxoay.json (api_key / user / pass / host / port)" -ForegroundColor Yellow
        try { notepad $ProxyCfg } catch { }
    } else {
        Write-Host "[5/5] Thieu proxyxoay.example.json — tu tao .proxyxoay.json" -ForegroundColor Red
    }
} else {
    Write-Host "[5/5] .proxyxoay.json da co — giu nguyen" -ForegroundColor Green
}

# --- outdir ---
New-Item -ItemType Directory -Force -Path (Join-Path $Root "tts_loop_out") | Out-Null

Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  INSTALL OK" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host "Chay test:"
Write-Host "  .\run_loop.bat"
Write-Host "  hoac:"
Write-Host "  .\.venv\Scripts\python.exe -u fast_tts_loop.py --count 5 --workers 4 --hsw-workers 3"
Write-Host ""
Write-Host "Doc: TOOL_WINDOWS.md"
