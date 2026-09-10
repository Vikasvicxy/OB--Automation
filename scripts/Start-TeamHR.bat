@echo off
echo Starting TeamHR Automation...
cd /d %~dp0
powershell -ExecutionPolicy Bypass -File "%~dp0Start-TeamHR.ps1" %*
