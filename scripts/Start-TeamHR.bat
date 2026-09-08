@echo off
echo Starting TeamHR Automation...
cd /d %~dp0
powershell -ExecutionPolicy Bypass -File scripts\Start-TeamHR.ps1 %*
