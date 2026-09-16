@echo off
setlocal
cd /d "%~dp0"
title DSH Finance Agent Updater
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\update-project.ps1"
set "UPDATE_EXIT_CODE=%ERRORLEVEL%"
echo.
if "%UPDATE_EXIT_CODE%"=="0" (
  echo Update completed successfully. All services remain stopped.
) else (
  echo Update failed with exit code %UPDATE_EXIT_CODE%. Review the messages above.
)
pause
exit /b %UPDATE_EXIT_CODE%
