@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\stop.ps1"
if errorlevel 1 (
  echo.
  echo Stop failed. No unverified process was terminated.
  pause
  exit /b 1
)
pause
