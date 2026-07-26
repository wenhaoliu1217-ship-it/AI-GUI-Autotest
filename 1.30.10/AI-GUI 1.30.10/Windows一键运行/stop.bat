@echo off
chcp 65001 >nul
setlocal
title Stop AI-GUI 1.30.10 Portable Service
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\stop.ps1"
set "exitCode=%errorlevel%"
if not "%exitCode%"=="0" (
  echo.
  echo Stop failed. No unverified process was terminated. Press any key to close.
  pause >nul
)
exit /b %exitCode%
