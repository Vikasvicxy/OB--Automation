# TeamHR Automation - Windows Setup Script
# Idempotent: safe to run multiple times

param(
    [switch]$SkipBrowser
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

Write-Host "=== TeamHR Automation Setup ===" -ForegroundColor Cyan
Write-Host ""

# 1. Check Python
Write-Host "[1/6] Checking Python..." -ForegroundColor Yellow
try {
    $ver = & python --version 2>&1
    Write-Host "  OK: $ver" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Install Python 3.10+ from python.org" -ForegroundColor Red
    exit 1
}

# 2. Create venv
Write-Host "[2/6] Virtual environment..." -ForegroundColor Yellow
$venvPath = Join-Path $ProjectRoot "venv"
if (-not (Test-Path (Join-Path $venvPath "Scripts\python.exe"))) {
    & python -m venv $venvPath
    Write-Host "  Created venv" -ForegroundColor Green
} else {
    Write-Host "  venv exists" -ForegroundColor Green
}

# 3. Activate and install deps
Write-Host "[3/6] Installing dependencies..." -ForegroundColor Yellow
& (Join-Path $venvPath "Scripts\Activate.ps1")
& pip install -r (Join-Path $ProjectRoot "requirements.txt") --quiet
Write-Host "  Dependencies installed" -ForegroundColor Green

# 4. Install Playwright browser
Write-Host "[4/6] Playwright browser..." -ForegroundColor Yellow
if (-not $SkipBrowser) {
    & playwright install chromium 2>$null
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
& python -c "from app.database import init_db; init_db(); print('  DB initialized')"
Write-Host "  Database ready" -ForegroundColor Green

Write-Host ""
Write-Host "=== Setup Complete ===" -ForegroundColor Cyan
Write-Host "Run: scripts\Start-TeamHR.ps1" -ForegroundColor White
Write-Host "Or:  scripts\Start-TeamHR.bat" -ForegroundColor White
