# TeamHR Automation - Stop Script
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "data\server.pid"

if (Test-Path $PidFile) {
    $pid = Get-Content $PidFile | Select-Object -First 1
    try {
        Stop-Process -Id $pid -Force -ErrorAction Stop
        Write-Host "[OK] Server stopped (PID: $pid)" -ForegroundColor Green
    } catch {
        Write-Host "[WARNING] Process $pid not found or already stopped" -ForegroundColor Yellow
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[INFO] No PID file found. Trying to stop uvicorn processes..." -ForegroundColor Yellow
    Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*uvicorn*teamhr*" } | Stop-Process -Force
}
Write-Host "[OK] TeamHR stopped" -ForegroundColor Green
