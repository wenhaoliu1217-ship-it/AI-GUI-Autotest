@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Verify-Package.ps1"
if errorlevel 1 (
  echo Integrity check failed.
  pause
  exit /b 1
)
pause
