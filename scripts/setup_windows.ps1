# TeamHR Automation - Windows Setup Script
# Idempotent: safe to run multiple times
# Installs dependencies ONCE. Start-TeamHR.ps1 will not reinstall them.

param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot "venv\Scripts\python.exe"
$Marker = Join-Path $ProjectRoot "data\.deps-installed"

Write-Host "=== TeamHR Automation Setup ===" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
Write-Host "[1/6] Checking Python..." -ForegroundColor Yellow
try {
    $ver = & python --version 2>&1
    Write-Host "  OK: $ver" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Install Python 3.10+ from python.org" -ForegroundColor Red
    Write-Host "  Ensure 'Add python.exe to PATH' is checked during install." -ForegroundColor Red
    exit 1
}

# 2. Create venv
Write-Host "[2/6] Virtual environment..." -ForegroundColor Yellow
if (-not (Test-Path $VenvPython)) {
    & python -m venv (Join-Path $ProjectRoot "venv")
    Write-Host "  Created venv" -ForegroundColor Green
} else {
    Write-Host "  venv exists" -ForegroundColor Green
}

# 3. Install deps (once; marker file prevents reinstall on every start)
Write-Host "[3/6] Installing dependencies..." -ForegroundColor Yellow
if (-not (Test-Path $Marker)) {
    & $VenvPython -m pip install --upgrade pip --quiet
    & $VenvPython -m pip install -r (Join-Path $ProjectRoot "requirements.txt") --quiet
    New-Item -ItemType Directory -Path (Join-Path $ProjectRoot "data") -Force | Out-Null
    Set-Content -Path $Marker -Value (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    Write-Host "  Dependencies installed" -ForegroundColor Green
} else {
    Write-Host "  Already installed (marker present) - skipping" -ForegroundColor Gray
}

# 4. Install Playwright browser
Write-Host "[4/6] Playwright browser..." -ForegroundColor Yellow
if (-not $SkipBrowser) {
    & $VenvPython -m playwright install chromium 2>$null
    Write-Host "  Chromium installed" -ForegroundColor Green
} else {
    Write-Host "  Skipped" -ForegroundColor Gray
}

# 5. Create directories
Write-Host "[5/6] Creating directories..." -ForegroundColor Yellow
$dirs = @("data", "data\database", "data\generated", "data\backups", "data\uploads", "data\logs", "data\masters")
foreach ($d in $dirs) {
    $path = Join-Path $ProjectRoot $d
    if (-not (Test-Path $path)) { New-Item -ItemType Directory -Path $path -Force | Out-Null }
}
Write-Host "  Directories ready" -ForegroundColor Green

# 6. Initialize DB and health check
Write-Host "[6/6] Database initialization..." -ForegroundColor Yellow
& $VenvPython -c "from app.database import init_db; init_db(); print('  DB initialized')"
if ($LASTEXITCODE -ne 0) {
    Write-Host "  WARNING: DB init failed. Check you ran this from the project root." -ForegroundColor Red
} else {
    Write-Host "  Database ready" -ForegroundColor Green
}

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host "Run: scripts\Start-TeamHR.ps1" -ForegroundColor White
Write-Host "Or:  scripts\Start-TeamHR.bat" -ForegroundColor White