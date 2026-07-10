# One-shot bootstrap on Windows Server (private GitHub repo)
# Prerequisites: Git, Python 3.11+, gh (GitHub CLI) optional but recommended
#
# Fastest (private repo):
#   winget install Git.Git GitHub.cli Python.Python.3.12
#   gh auth login
#   irm https://raw.githubusercontent.com/huypv2002/tts-api/main/bootstrap.ps1 | iex
#   OR after clone:  powershell -File .\bootstrap.ps1

$ErrorActionPreference = "Stop"
$Repo = "huypv2002/tts-api"
$Root = "C:\apps\tts-api"

Write-Host "== TTS API bootstrap (private) ==" -ForegroundColor Cyan

# 1) Clone if needed
if (-not (Test-Path "$Root\tts-api\server\main.py")) {
  New-Item -ItemType Directory -Force -Path C:\apps | Out-Null
  if (Get-Command gh -ErrorAction SilentlyContinue) {
    Write-Host "Cloning private repo via gh..."
    if (-not (gh auth status 2>$null)) {
      Write-Host "Login GitHub (browser once):" -ForegroundColor Yellow
      gh auth login
    }
    if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
    gh repo clone $Repo $Root
  } else {
    Write-Host "gh not found — using git (needs credential/PAT once)" -ForegroundColor Yellow
    if (Test-Path $Root) { Remove-Item -Recurse -Force $Root }
    git clone "https://github.com/$Repo.git" $Root
  }
} else {
  Write-Host "Repo exists — git pull"
  Push-Location $Root
  git pull
  Pop-Location
}

$App = Join-Path $Root "tts-api"
Set-Location $App

# 2) venv + deps
if (-not (Test-Path ".venv")) {
  py -3 -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
python -m pip install -U pip -q
pip install -r requirements.txt -q
Write-Host "Installing camoufox browser..."
pip install camoufox tls-client -q
python -m camoufox fetch

# 3) config if missing
if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  $pw = -join ((48..57 + 65..90 + 97..122 | Get-Random -Count 20 | ForEach-Object { [char]$_ }))
  @"
TTS_ADMIN_PASSWORD=$pw
TTS_PORT=8787
TTS_PUBLIC_BASE_URL=https://tts-origin.liveyt.pro
"@ | Set-Content .env -Encoding UTF8
  Write-Host "Admin password saved to .env : $pw" -ForegroundColor Green
  Write-Host ">>> COPY THIS PASSWORD <<<" -ForegroundColor Yellow
}
if (-not (Test-Path "config\settings.json")) {
  Copy-Item "config\settings.example.json" "config\settings.json"
}
if (-not (Test-Path "config\proxies.json")) {
  Copy-Item "config\proxies.example.json" "config\proxies.json"
  Write-Host "EDIT config\proxies.json with your proxyxoay keys" -ForegroundColor Yellow
  notepad config\proxies.json
}

# 4) PYTHONPATH for fast_tts.py in parent
$env:PYTHONPATH = "$App;$Root"
$env:TTS_PORT = "8787"

Write-Host ""
Write-Host "Starting API on http://127.0.0.1:8787 ..." -ForegroundColor Cyan
Write-Host "Admin: https://tts-origin.liveyt.pro/admin/"
Write-Host "In another terminal run cloudflared with your tunnel config."
Write-Host ""

python -m uvicorn server.main:app --host 0.0.0.0 --port 8787
