# Run TTS API on Windows Server
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".venv")) {
  Write-Host "Creating venv..."
  py -3 -m venv .venv
}
& .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt --quiet

# Camoufox browser binaries (first run)
python -c "from camoufox.sync_api import Camoufox" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "Fetching Camoufox..."
  camoufox fetch
}

$env:PYTHONPATH = (Get-Location).Path
# parent repo for fast_tts
$env:PYTHONPATH = "$env:PYTHONPATH;$((Resolve-Path ..).Path)"

$port = 8787
if (Test-Path .env) {
  Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#][^=]+)=(.*)$') {
      [System.Environment]::SetEnvironmentVariable($matches[1].Trim(), $matches[2].Trim(), "Process")
    }
  }
}
if ($env:TTS_PORT) { $port = [int]$env:TTS_PORT }

Write-Host "Starting TTS API on 0.0.0.0:$port"
python -m uvicorn server.main:app --host 0.0.0.0 --port $port
