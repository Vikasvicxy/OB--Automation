# TeamHR Automation - Start Script
# Detects environment, validates config, starts server, opens browser

param(
    [int]$Port = 8000,
    [string]$ListenHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "data\server.pid"
$LogFile = Join-Path $ProjectRoot "data\logs\server.log"

# Ensure directories exist
$dirs = @("data", "data\database", "data\generated", "data\backups", "data\uploads", "data\logs")
foreach ($d in $dirs) {
    $path = Join-Path $ProjectRoot $d
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
}

# Check Python
try {
    $pythonVersion = & python --version 2>&1
    Write-Host "[OK] Python: $pythonVersion"
} catch {
    Write-Host "[ERROR] Python not found in PATH" -ForegroundColor Red
    exit 1
}

# Check venv
$venvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[INFO] Creating virtual environment..." -ForegroundColor Yellow
    & python -m venv (Join-Path $ProjectRoot "venv")
}

# Activate venv
& (Join-Path $ProjectRoot "venv\Scripts\Activate.ps1")

# Install/update dependencies
Write-Host "[INFO] Checking dependencies..." -ForegroundColor Yellow
& pip install -r (Join-Path $ProjectRoot "requirements.txt") --quiet 2>$null

# Check if port is in use
$portCheck = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if ($portCheck) {
    Write-Host "[WARNING] Port $Port is already in use. Trying next port..." -ForegroundColor Yellow
    $Port++
}

# Start server
Write-Host "[INFO] Starting TeamHR on http://${ListenHost}:${Port}" -ForegroundColor Green
$serverProc = Start-Process -FilePath $venvPython -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $ListenHost, "--port", $Port -WorkingDirectory $ProjectRoot -PassThru -NoNewWindow -RedirectStandardOutput $LogFile -RedirectStandardError (Join-Path $ProjectRoot "data\logs\server_error.log")

# Save PID
$serverProc.Id | Out-File -FilePath $PidFile -Encoding ASCII
Write-Host "[OK] Server started (PID: $($serverProc.Id))" -ForegroundColor Green

# Wait briefly then open browser
Start-Sleep -Seconds 2
Start-Process "http://${ListenHost}:${Port}"
Write-Host "[OK] Browser opened" -ForegroundColor Green
Write-Host ""
Write-Host "TeamHR is running at http://${ListenHost}:${Port}" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop, or run scripts\Stop-TeamHR.ps1" -ForegroundColor Gray
