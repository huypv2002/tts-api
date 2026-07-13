# Smoke test: create MP3 via local or remote tts-api
# Usage (from repo root C:\TTS\tts-api):
#   powershell -ExecutionPolicy Bypass -File .\smoke_api.ps1
#   powershell -ExecutionPolicy Bypass -File .\smoke_api.ps1 -BaseUrl "https://tts-origin.liveyt.pro"
#   powershell -ExecutionPolicy Bypass -File .\smoke_api.ps1 -ApiKey "tts_xxx"

param(
    [string]$BaseUrl = "http://127.0.0.1:8787",
    [string]$ApiKey = "",
    [string]$Text = "Hello from smoke API test on Windows."
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Find-ApiKey {
    param([string]$Explicit)
    if ($Explicit) { return $Explicit.Trim() }

    $candidates = @(
        (Join-Path $Root "tts-api\data\bootstrap_key.txt"),
        (Join-Path $Root "data\bootstrap_key.txt")
    )
    foreach ($f in $candidates) {
        if (Test-Path $f) {
            $raw = Get-Content $f -Raw
            if ($raw -match "tts_[A-Za-z0-9_\-]+") {
                Write-Host "Using key from $f" -ForegroundColor Cyan
                return $Matches[0]
            }
        }
    }
    return ""
}

$ApiKey = Find-ApiKey -Explicit $ApiKey
if (-not $ApiKey) {
    Write-Host "ERROR: No API key. Pass -ApiKey tts_xxx or create key in Admin." -ForegroundColor Red
    Write-Host "Admin: $BaseUrl/admin/  or  https://tts-origin.liveyt.pro/admin/"
    exit 1
}

Write-Host "BaseUrl = $BaseUrl"
Write-Host "Key     = $($ApiKey.Substring(0, [Math]::Min(16, $ApiKey.Length)))..."

$health = Invoke-RestMethod -Uri "$BaseUrl/v1/health" -Method Get -TimeoutSec 20
Write-Host "Health ok=$($health.ok) workers=$($health.workers) proxy_ready=$($health.proxy.ready)"

$headers = @{
    "X-API-Key"    = $ApiKey
    "Content-Type" = "application/json"
}
$body = @{
    text = $Text
    lang = "en"
    wait = $true
} | ConvertTo-Json

Write-Host "POST /v1/tts (wait=true, up to ~90s)..." -ForegroundColor Yellow
$job = Invoke-RestMethod -Uri "$BaseUrl/v1/tts" -Method Post -Headers $headers -Body $body -TimeoutSec 120
Write-Host ($job | ConvertTo-Json -Compress)

if ($job.status -ne "done") {
    # poll a bit more
    $id = $job.id
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 2
        $job = Invoke-RestMethod -Uri "$BaseUrl/v1/tts/$id" -Headers @{ "X-API-Key" = $ApiKey } -TimeoutSec 30
        Write-Host "poll status=$($job.status)"
        if ($job.status -eq "done" -or $job.status -eq "failed") { break }
    }
}

if ($job.status -eq "failed") {
    Write-Host "FAILED: $($job.error)" -ForegroundColor Red
    exit 1
}
if ($job.status -ne "done") {
    Write-Host "TIMEOUT status=$($job.status)" -ForegroundColor Red
    exit 1
}

$out = Join-Path $Root "smoke_api_out.mp3"
$audioUrl = "$BaseUrl/v1/tts/$($job.id)/audio"
Invoke-WebRequest -Uri $audioUrl -Headers @{ "X-API-Key" = $ApiKey } -OutFile $out -TimeoutSec 60
$len = (Get-Item $out).Length
Write-Host "OK saved $out ($len bytes) job=$($job.id) duration_ms=$($job.duration_ms)" -ForegroundColor Green
