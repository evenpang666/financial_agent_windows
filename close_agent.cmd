@echo off
setlocal
set "SCRIPT=%~dp0scripts\stop-services.ps1"
if not exist "%SCRIPT%" (
  echo Stop script not found: "%SCRIPT%"
  echo Press any key to close this window.
  pause >nul
  exit /b 1
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"
set "EXITCODE=%ERRORLEVEL%"
echo.
if not "%EXITCODE%"=="0" (
  echo Some services could not be stopped. Review the message above.
) else (
  echo Shutdown completed.
)
echo Press any key to close this window.
pause >nul
exit /b %EXITCODE%
