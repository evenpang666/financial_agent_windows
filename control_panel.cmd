@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%PYTHON%" (
  where pythonw.exe >nul 2>nul
  if not errorlevel 1 set "PYTHON=pythonw.exe"
)
if not exist "%PYTHON%" if /i not "%PYTHON%"=="pythonw.exe" set "PYTHON=python.exe"
start "Finance Agent Control Panel" /b "%PYTHON%" "%~dp0scripts\control_panel.py"
if errorlevel 1 (
  echo Unable to start the control panel. Install Python 3.11 or later and try again.
  pause
)
