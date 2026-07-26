@echo off
chcp 65001 >nul
setlocal
title AI-GUI 1.30.10 Portable Launcher
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\start.ps1"
set "exitCode=%errorlevel%"
if not "%exitCode%"=="0" (
  echo.
  echo Portable startup failed. Review the message above, then press any key to close.
  pause >nul
)
exit /b %exitCode%
