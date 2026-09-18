# TeamHR Automation - Start Script
# Detects environment, validates config, starts server, opens browser
# Fast path: does NOT reinstall dependencies on every start (only when the
# venv or the dependency marker is missing).

param(
    [int]$Port = 8000,
    [string]$ListenHost = "127.0.0.1"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$PidFile = Join-Path $ProjectRoot "data\server.pid"
$LogFile = Join-Path $ProjectRoot "data\logs\server.log"
$ErrLogFile = Join-Path $ProjectRoot "data\logs\server_error.log"
$Marker = Join-Path $ProjectRoot "data\.deps-installed"

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
    Write-Host "[ERROR] Run scripts\setup_windows.ps1 first." -ForegroundColor Red
    exit 1
}

# Check venv; create it if missing
if (-not (Test-Path $VenvPython)) {
    Write-Host "[INFO] Creating virtual environment..." -ForegroundColor Yellow
    & python -m venv (Join-Path $ProjectRoot "venv")
    if (-not (Test-Path $VenvPython)) {
        Write-Host "[ERROR] Could not create the venv. Run scripts\setup_windows.ps1." -ForegroundColor Red
        exit 1
    }
}

# Install dependencies ONLY when the marker is missing (i.e. first run).
# This makes every subsequent start fast.
if (-not (Test-Path $Marker)) {
    Write-Host "[INFO] First run: installing dependencies..." -ForegroundColor Yellow
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Dependency install failed. Run scripts\setup_windows.ps1." -ForegroundColor Red
        exit 1
    }
    Set-Content -Path $Marker -Value (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Write-Host "[OK] Dependencies installed" -ForegroundColor Green
}

# Check if port is in use
$portCheck = Get-NetTCPConnection -LocalPort $Port -ErrorAction SilentlyContinue
if ($portCheck) {
    Write-Host "[WARNING] Port $Port is already in use. Trying next port..." -ForegroundColor Yellow
    $Port++
}

# Start server
Write-Host "[INFO] Starting TeamHR on http://${ListenHost}:${Port}" -ForegroundColor Green
$serverProc = Start-Process -FilePath $VenvPython -ArgumentList "-m", "uvicorn", "app.main:app", "--host", $ListenHost, "--port", $Port -WorkingDirectory $ProjectRoot -PassThru -NoNewWindow -RedirectStandardOutput $LogFile -RedirectStandardError $ErrLogFile

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