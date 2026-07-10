# Cloudflare Tunnel setup helper (Windows)
# Prerequisites: cloudflared installed + `cloudflared tunnel login` already done
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$TunnelName = if ($args[0]) { $args[0] } else { "tts-api" }
$LocalPort = if ($env:TTS_PORT) { $env:TTS_PORT } else { "8787" }

Write-Host "Tunnel name: $TunnelName  local: http://127.0.0.1:$LocalPort"

# Create tunnel if missing
$existing = cloudflared tunnel list 2>$null | Select-String $TunnelName
if (-not $existing) {
  Write-Host "Creating tunnel $TunnelName ..."
  cloudflared tunnel create $TunnelName
}

# Write config
$cfgDir = Join-Path $env:USERPROFILE ".cloudflared"
$cred = Get-ChildItem $cfgDir -Filter "*.json" | Where-Object { $_.Name -ne "cert.pem" } | Select-Object -First 1
if (-not $cred) {
  Write-Error "No tunnel credentials in $cfgDir — run: cloudflared tunnel login"
}

# Resolve tunnel UUID from list
$list = cloudflared tunnel list
# User should set hostname
$Hostname = Read-Host "Public hostname (e.g. tts.yourdomain.com)"

$configPath = Join-Path (Get-Location) "cloudflared-config.yml"
@"
tunnel: $TunnelName
credentials-file: $($cred.FullName)

ingress:
  - hostname: $Hostname
    service: http://127.0.0.1:$LocalPort
  - service: http_status:404
"@ | Set-Content -Path $configPath -Encoding UTF8

Write-Host "Wrote $configPath"
Write-Host "DNS route:"
Write-Host "  cloudflared tunnel route dns $TunnelName $Hostname"
Write-Host "Run tunnel:"
Write-Host "  cloudflared tunnel --config $configPath run $TunnelName"
Write-Host ""
Write-Host "Then set public URL in admin Settings or .env:"
Write-Host "  TTS_PUBLIC_BASE_URL=https://$Hostname"
