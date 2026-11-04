@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "NOPAUSE=%~1"

echo =========================================
echo  Crisp Automation - Windows setup
echo =========================================
echo.

set "PYEXE="
call :find_python
if defined PYEXE goto have_python

where winget >nul 2>nul
if errorlevel 1 goto no_python
echo Python was not found. Installing Python 3.12 with winget...
echo This can take a few minutes.
winget install --id Python.Python.3.12 --exact --scope user --silent --accept-package-agreements --accept-source-agreements
rem The new Python is not on PATH in this window yet, so look for it directly.
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYEXE="%LocalAppData%\Programs\Python\Python312\python.exe""
if not defined PYEXE call :find_python
if not defined PYEXE goto no_python

:have_python
echo Using Python: %PYEXE%
%PYEXE% -c "import sys,struct; sys.exit(0 if sys.version_info >= (3, 9) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 goto bad_python

if exist ".venv\Scripts\python.exe" goto have_venv
echo.
echo Creating the virtual environment...
%PYEXE% -m venv .venv
if errorlevel 1 goto failed

:have_venv
echo.
echo Installing Python packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed

echo.
echo Installing the Chromium browser used by the automation...
".venv\Scripts\python.exe" -m playwright install chromium
if errorlevel 1 goto failed

echo done> ".venv\.setup_complete"
echo.
echo Setup completed.
if /i not "%NOPAUSE%"=="nopause" (
  echo Double-click run_windows.bat to launch the app.
  pause
)
exit /b 0

:no_python
echo.
echo Python 3.9 or newer (64-bit) is required and could not be installed automatically.
echo Install Python 3.12 from https://www.python.org/downloads/windows/
echo During installation, tick "Add python.exe to PATH". Then run this again.
goto failed_end

:bad_python
echo.
echo The Python found is too old or 32-bit. The app needs 64-bit Python 3.9 or newer.
echo Install 64-bit Python 3.12 from https://www.python.org/downloads/windows/
goto failed_end

:failed
echo.
echo Setup failed. Check the error above and try again.

:failed_end
if /i not "%NOPAUSE%"=="nopause" pause
exit /b 1

rem ---------------------------------------------------------------------
rem Sets PYEXE to a working 64-bit Python 3 launcher, or leaves it empty.
rem "python" alone may be the Microsoft Store placeholder, so it is tested.
:find_python
py -3-64 -c "import sys" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=py -3-64"
  exit /b 0
)
py -3 -c "import sys" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=py -3"
  exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=python"
  exit /b 0
)
exit /b 0
