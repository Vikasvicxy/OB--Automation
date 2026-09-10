@echo off
echo Installing TeamHR Automation for the first time...
echo.
cd /d %~dp0
powershell -ExecutionPolicy Bypass -File "%~dp0setup_windows.ps1" %*
echo.
echo Setup finished. Double-click scripts\Start-TeamHR.bat to start.
pause