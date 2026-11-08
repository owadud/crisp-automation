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
rem One-time: Desktop + Start menu shortcut, so next time no terminal is needed.
if not exist "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Crisp Automation.lnk" (
  echo Creating the "Crisp Automation" shortcut on the Desktop and in the Start menu...
  ".venv\Scripts\python.exe" -m crisp_gui.desktop_shortcut
)
if /i "%~1"=="console" goto console
rem Open the app without a console window; this window closes right away.
start "" ".venv\Scripts\pythonw.exe" gui_app.py
exit /b 0

:console
rem run_windows.bat console = keep this window and show all messages (for troubleshooting).
echo Starting Crisp Automation...
".venv\Scripts\python.exe" gui_app.py
if errorlevel 1 (
  echo.
  echo The app closed with an error. See the messages above.
  pause
)
