@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem Customer one-click mode uses the bundled, validated local Runner.
set "GUI_RUNNER_MODE=process"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File ".\start.ps1"
if errorlevel 1 (
  echo.
  echo Startup failed. Review the message above. No online repair was attempted.
  pause
  exit /b 1
)
