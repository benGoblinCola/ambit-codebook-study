# Starts the gated Codebook Studio locally (PowerShell).
# Then, in a SECOND terminal, start the tunnel to get a public HTTPS link:
#     .\cloudflared.exe tunnel --url http://localhost:8603
#
# You'll be prompted for the username and password (so they're never stored on disk).
# Set $env:STUDIO_USERNAME / $env:STUDIO_PASSWORD beforehand to skip the prompts.
$ErrorActionPreference = "Stop"
Set-Location "$PSScriptRoot\.."

# Load AWS credentials from goblin-guide\.env (adjust the path if yours differs).
$envFile = "..\goblin-guide\.env"
if (Test-Path $envFile) {
    Get-Content $envFile | Where-Object { $_ -match '^\s*[A-Za-z_][A-Za-z0-9_]*=' } | ForEach-Object {
        $k, $v = ($_ -split '=', 2)
        [Environment]::SetEnvironmentVariable($k.Trim(), $v.Trim())
    }
}

if (-not $env:STUDIO_USERNAME) { $env:STUDIO_USERNAME = Read-Host "Studio username" }
if (-not $env:STUDIO_PASSWORD) { $env:STUDIO_PASSWORD = Read-Host "Studio password" }
$env:PYTHONPATH = "src"

streamlit run src/ambit_codebook/review_app.py `
    --server.port 8603 --server.enableCORS false --server.enableXsrfProtection false
