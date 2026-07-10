# One-time setup on Windows Server
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "== TTS API Windows install =="

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
  Write-Error "Install Python 3.11+ from python.org and re-run."
}

if (-not (Test-Path ".venv")) {
  py -3 -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
python -m pip install -U pip
pip install -r requirements.txt
Write-Host "Fetching Camoufox browser..."
python -m camoufox fetch

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Created .env — edit TTS_ADMIN_PASSWORD"
}
if (-not (Test-Path "config\settings.json")) {
  Copy-Item "config\settings.example.json" "config\settings.json"
}
if (-not (Test-Path "config\proxies.json")) {
  Copy-Item "config\proxies.example.json" "config\proxies.json"
  Write-Host "Created config\proxies.json — add your proxyxoay keys"
}

New-Item -ItemType Directory -Force -Path data\db, data\audio | Out-Null
Write-Host "Done. Next: edit .env + config\proxies.json then run scripts\run_server.ps1"
