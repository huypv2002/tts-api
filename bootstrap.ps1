# One-shot bootstrap on Windows Server (private GitHub repo)
# Run from repo root after clone:
#   cd C:\tts-api
#   powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1

$ErrorActionPreference = "Stop"
$Repo = "huypv2002/tts-api"

Write-Host "== TTS API bootstrap ==" -ForegroundColor Cyan

# Detect where we are
# Layout: <Root>\fast_tts.py  and  <Root>\tts-api\server\main.py
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $ScriptDir) { $ScriptDir = Get-Location }

$Root = $null
if (Test-Path (Join-Path $ScriptDir "tts-api\server\main.py")) {
    $Root = $ScriptDir
} elseif (Test-Path (Join-Path $ScriptDir "server\main.py")) {
    # script was placed inside tts-api/ subfolder by mistake
    $Root = Split-Path -Parent $ScriptDir
} elseif (Test-Path ".\tts-api\server\main.py") {
    $Root = (Resolve-Path ".").Path
} elseif (Test-Path ".\server\main.py") {
    $Root = (Resolve-Path "..").Path
}

if (-not $Root) {
    # Not inside a clone yet - clone to C:\tts-api
    $Root = "C:\tts-api"
    Write-Host "Cloning $Repo into $Root ..."
    New-Item -ItemType Directory -Force -Path (Split-Path $Root -Parent) | Out-Null
    if (Test-Path $Root) {
        Write-Host "Folder exists, trying git pull..."
        Push-Location $Root
        git pull
        Pop-Location
    } else {
        $hasGh = Get-Command gh -ErrorAction SilentlyContinue
        if ($hasGh) {
            gh auth status 2>&1 | Out-Null
            if ($LASTEXITCODE -ne 0) {
                Write-Host "Run: gh auth login" -ForegroundColor Yellow
                gh auth login
            }
            gh repo clone $Repo $Root
        } else {
            git clone "https://github.com/$Repo.git" $Root
        }
    }
}

$App = Join-Path $Root "tts-api"
$MainPy = Join-Path $App "server\main.py"
if (-not (Test-Path $MainPy)) {
    Write-Host "ERROR: cannot find $MainPy" -ForegroundColor Red
    Write-Host "Expected layout after clone:"
    Write-Host "  $Root\fast_tts.py"
    Write-Host "  $Root\tts-api\server\main.py"
    Write-Host "Current dir: $(Get-Location)"
    exit 1
}

Write-Host "Root = $Root"
Write-Host "App  = $App"
Set-Location $App

# Python launcher
$Py = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Py = "py" }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Py = "python" }
else {
    Write-Host "ERROR: Python not found. Install Python 3.11+ and re-run." -ForegroundColor Red
    exit 1
}

# venv
if (-not (Test-Path ".\.venv\Scripts\python.exe")) {
    Write-Host "Creating venv..."
    if ($Py -eq "py") { & py -3 -m venv .venv } else { & python -m venv .venv }
}
$VenvPy = Join-Path $App ".venv\Scripts\python.exe"
$VenvPip = Join-Path $App ".venv\Scripts\pip.exe"

Write-Host "Installing Python packages..."
& $VenvPy -m pip install -U pip
& $VenvPip install -r requirements.txt
& $VenvPip install camoufox tls-client
# CRITICAL: Playwright 1.61+ breaks Camoufox (isMobile viewport crash)
Write-Host "Pinning playwright less than 1.61 (Camoufox fix)..."
& $VenvPip install "playwright>=1.48.0,<1.61.0"
Write-Host "Fetching Camoufox browser (may take a few minutes)..."
& $VenvPy -m camoufox fetch

# .env
if (-not (Test-Path ".\.env")) {
    $chars = @()
    $chars += 48..57
    $chars += 65..90
    $chars += 97..122
    $pw = -join ($chars | Get-Random -Count 20 | ForEach-Object { [char]$_ })
    $envText = @"
TTS_ADMIN_PASSWORD=$pw
TTS_PORT=8787
TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
"@
    Set-Content -Path ".\.env" -Value $envText -Encoding ASCII
    Write-Host ""
    Write-Host "ADMIN PASSWORD (save it): $pw" -ForegroundColor Green
    Write-Host ""
}

if (-not (Test-Path ".\config\settings.json")) {
    Copy-Item ".\config\settings.example.json" ".\config\settings.json"
}
if (-not (Test-Path ".\config\proxies.json")) {
    Copy-Item ".\config\proxies.example.json" ".\config\proxies.json"
    Write-Host "Edit config\proxies.json with your proxy keys" -ForegroundColor Yellow
    if (Get-Command notepad -ErrorAction SilentlyContinue) {
        Start-Process notepad ".\config\proxies.json" -Wait
    }
}

$env:PYTHONPATH = "$App;$Root"
$env:TTS_PORT = "8787"

# load .env into process
Get-Content ".\.env" | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $i = $line.IndexOf("=")
        $k = $line.Substring(0, $i).Trim()
        $v = $line.Substring($i + 1).Trim()
        [System.Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
}

Write-Host ""
Write-Host "Starting API on http://0.0.0.0:8787" -ForegroundColor Cyan
Write-Host "Admin: https://tts-origin.liveyt.pro/admin/"
Write-Host "Press Ctrl+C to stop"
Write-Host ""

& $VenvPy -m uvicorn server.main:app --host 0.0.0.0 --port 8787
