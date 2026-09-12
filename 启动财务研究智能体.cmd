@echo off
setlocal
set "SCRIPT=%~dp0install-and-start.ps1"
if not exist "%SCRIPT%" set "SCRIPT=C:\Users\15261\Downloads\finance_agent_win\finance_agent_win\install-and-start.ps1"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
if errorlevel 1 (
  echo.
  echo Startup failed. Review the message above, then press any key to close this window.
  pause >nul
)
