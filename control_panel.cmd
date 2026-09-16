@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYTHON%" set "PYTHON=pythonw.exe"
start "Finance Agent Control Panel" /b "%PYTHON%" "%~dp0scripts\control_panel.py"
if errorlevel 1 (
  echo Unable to start the control panel. Run install-and-start.ps1 once, then try again.
  pause
)
