# One-command demo launcher: starts the gated studio + cloudflared tunnel and prints the
# public link. Ctrl+C shuts both down. Invoked by `make ambit-demo`.
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."

# Stop anything left over from a previous run.
Get-NetTCPConnection -LocalPort 8603 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

# AWS credentials (from goblin-guide\.env; adjust the path if yours differs).
$envFile = "..\goblin-guide\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
        $k, $v = ($_ -split '=', 2)
        [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim())
    }
}

$env:STUDIO_USERNAME = "JBPoquelin"
if (-not $env:STUDIO_PASSWORD) { $env:STUDIO_PASSWORD = Read-Host "Studio password" }
$env:PYTHONPATH = "src"

Write-Host "Starting the studio..."
$app = Start-Process -FilePath "python" -PassThru -WindowStyle Hidden -ArgumentList @(
    "-m", "streamlit", "run", "src/ambit_codebook/review_app.py",
    "--server.port", "8603", "--server.headless", "true",
    "--server.enableCORS", "false", "--server.enableXsrfProtection", "false")

Write-Host "Starting the tunnel..."
$out = Join-Path $env:TEMP "ambit_tunnel.out"
$err = Join-Path $env:TEMP "ambit_tunnel.err"
Remove-Item $out, $err -ErrorAction SilentlyContinue
$tun = Start-Process -FilePath ".\cloudflared.exe" -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $out -RedirectStandardError $err `
    -ArgumentList @("tunnel", "--url", "http://localhost:8603")

# Poll cloudflared's logs for the public URL.
$url = $null
for ($i = 0; $i -lt 40 -and -not $url; $i++) {
    Start-Sleep -Seconds 1
    $m = Select-String -Path $err, $out -Pattern "https://[-a-z0-9]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($m) { $url = $m.Matches[0].Value }
}

Write-Host ""
if ($url) {
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "  Codebook Studio is LIVE:" -ForegroundColor Green
    Write-Host "    $url" -ForegroundColor Cyan
    Write-Host "  login:  JBPoquelin  /  (your password)" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
} else {
    Write-Host "Could not read the tunnel URL yet. Check: $err" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Leave this window open. Press Ctrl+C to stop the studio + tunnel."

try {
    Wait-Process -Id $tun.Id
} finally {
    Stop-Process -Id $app.Id -Force -ErrorAction SilentlyContinue
    Stop-Process -Id $tun.Id -Force -ErrorAction SilentlyContinue
    Write-Host "`nStopped studio + tunnel."
}
