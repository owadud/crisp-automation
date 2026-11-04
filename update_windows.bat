@echo off
setlocal
cd /d "%~dp0"
git pull --ff-only
if errorlevel 1 (pause & exit /b 1)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (pause & exit /b 1)
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 (pause & exit /b 1)
echo Update completed.
pause
