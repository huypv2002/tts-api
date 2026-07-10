# Install cloudflared as Windows service (run as Admin)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$config = Join-Path (Get-Location) "cloudflared-config.yml"
if (-not (Test-Path $config)) {
  Write-Error "Missing cloudflared-config.yml — run setup_tunnel.ps1 first"
}
cloudflared service install --config $config
Write-Host "Service installed. Start with: net start cloudflared  (or services.msc)"
