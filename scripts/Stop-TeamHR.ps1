# TeamHR Automation - Stop Script
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot "data\server.pid"

if (Test-Path $PidFile) {
    $serverPid = Get-Content $PidFile | Select-Object -First 1
    try {
        Stop-Process -Id $serverPid -Force -ErrorAction Stop
        Write-Host "[OK] Server stopped (PID: $serverPid)" -ForegroundColor Green
    } catch {
        Write-Host "[WARNING] Process $serverPid not found or already stopped" -ForegroundColor Yellow
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "[INFO] No PID file found. Trying to stop uvicorn processes..." -ForegroundColor Yellow
    $uvicornProcs = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*uvicorn*app.main*" }
    if ($uvicornProcs) {
        $uvicornProcs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
        Write-Host "[OK] Stopped $($uvicornProcs.Count) uvicorn process(es)" -ForegroundColor Green
    }
}
Write-Host "[OK] TeamHR stopped" -ForegroundColor Green