@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist ".venv\.setup_complete" if exist ".venv\Scripts\python.exe" goto launch

echo First launch: installing what the app needs. This happens only once
echo and can take several minutes (Python packages and Chromium).
echo.
call "%~dp0setup_windows.bat" nopause
if errorlevel 1 (
  echo.
  echo Setup did not finish, so the app cannot start yet.
  pause
  exit /b 1
)
echo.

:launch
echo Starting Crisp Automation...
".venv\Scripts\python.exe" gui_app.py
if errorlevel 1 (
  echo.
  echo The app closed with an error. See the messages above.
  pause
)
