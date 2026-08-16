@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if defined PYTHONPATH (
  set "PYTHONPATH=%CD%\src;%PYTHONPATH%"
) else (
  set "PYTHONPATH=%CD%\src"
)

echo Regulation Agent UI: http://127.0.0.1:8765
echo Close this terminal window to stop the service.
echo.

python -m regulation_agent
set "exit_code=%ERRORLEVEL%"

echo.
echo Regulation Agent service stopped. Exit code: %exit_code%
pause
exit /b %exit_code%
