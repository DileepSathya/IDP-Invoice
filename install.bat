@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo(
echo ==== IDP Invoice - Install ====

REM --- Check Python ---
set "PY_LAUNCH="
where py >nul 2>&1
if not errorlevel 1 (
  py -3.11 -c "import sys; print(sys.version)" >nul 2>&1
  if not errorlevel 1 (
    set "PY_LAUNCH=py -3.11"
  )
)

if not defined PY_LAUNCH (
  where python >nul 2>&1
  if errorlevel 1 (
    echo [ERROR] Python not found ^(neither `py` launcher nor `python` in PATH^).
    echo Install Python 3.11.x and re-run this script.
    exit /b 1
  )
  set "PY_LAUNCH=python"
)

REM --- Create venv if missing ---
if not exist "venv\Scripts\python.exe" (
  echo Creating virtual environment in venv ...
  %PY_LAUNCH% -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    exit /b 1
  )
)

set "PY=venv\Scripts\python.exe"

REM --- Require Python 3.11 for PaddleOCR/OpenCV/NumPy compatibility on Windows ---
set "PYVER="
set "PYFULL="
for /f "tokens=2" %%v in ('"%PY%" --version 2^>^&1') do set "PYFULL=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PYFULL%") do set "PYVER=%%a.%%b"
if not "%PYVER%"=="3.11" (
  echo [ERROR] This project requires Python 3.11 for PaddleOCR on Windows.
  echo         Detected Python: %PYVER%
  echo         Fix: delete venv and re-run install.bat using Python 3.11.x
  exit /b 1
)

echo Upgrading pip/setuptools/wheel ...
"%PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  echo [ERROR] Failed to upgrade pip tooling.
  exit /b 1
)

REM --- Choose requirements file ---
set "REQ=requirements.txt"
if "%PYVER%"=="3.11" (
  if exist "requirements-py311.txt" (
    set "REQ=requirements-py311.txt"
  )
)

echo Installing dependencies from %REQ% ...
"%PY%" -m pip install -r "%REQ%"
if errorlevel 1 (
  echo [ERROR] pip install failed.
  exit /b 1
)

if exist "TALLY INTEGRATION\requirements.txt" (
  echo Installing Tally bridge dependencies ...
  "%PY%" -m pip install -r "TALLY INTEGRATION\requirements.txt"
  if errorlevel 1 (
    echo [ERROR] Tally bridge pip install failed.
    exit /b 1
  )
)

if exist ".env.example" (
  if not exist ".env" (
    echo Creating .env from .env.example ...
    copy /y ".env.example" ".env" >nul
  ) else (
    "%PY%" sync_env.py >nul 2>&1
  )
)

if not exist "TALLY INTEGRATION\.env.example" goto :tally_env_done
if not exist "TALLY INTEGRATION\.env" (
  echo Creating TALLY INTEGRATION\.env from .env.example ...
  copy /y "TALLY INTEGRATION\.env.example" "TALLY INTEGRATION\.env" >nul
) else (
  "%PY%" "TALLY INTEGRATION\sync_env.py" >nul 2>&1
)
:tally_env_done

echo(
echo [OK] Install complete.
if /i "%~1"=="--no-pause" exit /b 0
echo(
echo Press any key to close this window.
pause >nul
exit /b 0

