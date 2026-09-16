@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\bootstrap-control-panel.ps1"
if errorlevel 1 (
  echo.
  echo Unable to prepare or start the control panel. Review the error above.
  pause
)
