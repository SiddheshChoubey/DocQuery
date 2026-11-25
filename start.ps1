# Starts DocQuery with one command: Valkey + Qdrant (Docker), the API, the
# Celery worker and the frontend. Logs from all three share this terminal,
# labelled api / worker / web. Press Ctrl+C to stop them.
#
#   .\start.ps1

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Stop-WithMessage($message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# 1. Check the one-time setup from the README.
if (-not (Test-Path "server\.venv\Scripts\python.exe")) {
    Stop-WithMessage "Python environment not found. Run the setup in README section 2 (py -m venv .venv inside server\)."
}
if (-not (Test-Path "server\.venv\Scripts\honcho.exe")) {
    Stop-WithMessage "honcho is not installed. Run: server\.venv\Scripts\python -m pip install -r server\requirements.txt"
}
if (-not (Test-Path "server\.env")) {
    Stop-WithMessage "server\.env is missing. Copy server\.env.example to server\.env and fill in your keys."
}
if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
    Stop-WithMessage "pnpm was not found. Install it with: npm install --global pnpm@12.14.0"
}

# 2. Start Valkey and Qdrant.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Stop-WithMessage "Docker is not running. Start Docker Desktop, wait until it is ready, then run .\start.ps1 again."
}
Write-Host "Starting Valkey and Qdrant..." -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) { Stop-WithMessage "docker compose up failed. See the messages above." }

# 3. Start the API, worker and frontend together (defined in Procfile).
Write-Host "Starting API (port 8000), worker and frontend (http://localhost:3000). Press Ctrl+C to stop." -ForegroundColor Cyan
& "server\.venv\Scripts\honcho.exe" start

Write-Host ""
Write-Host "Stopped. Valkey and Qdrant are still running in Docker; stop them with: docker compose stop" -ForegroundColor Cyan
