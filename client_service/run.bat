@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM --- Run uploader with auto-detected IPv4 callback URL ---
REM Usage:
REM   run.bat <file1> [file2 ...] [--concurrency N]
REM Env overrides (optional):
REM   API_BASE, API_KEY, WEBHOOK_SECRET, CALLBACK_PORT

cd /d "%~dp0"

REM ---- Ensure venv ----
if not exist ".venv\Scripts\activate.bat" (
  echo Creating virtual environment at .venv ...
  python -m venv ".venv"
  if errorlevel 1 (
    echo [ERROR] Failed to create venv. Ensure Python is installed and on PATH.
    echo(
    pause
    exit /b 1
  )
)

REM ---- Activate venv ----
call ".venv\Scripts\activate.bat"
if errorlevel 1 (
  echo [ERROR] Failed to activate venv.
  echo(
  pause
  exit /b 1
)

REM ---- Ensure dependencies ----
if not exist "requirements.txt" (
  echo [ERROR] requirements.txt not found in client_service.
  echo(
  pause
  exit /b 1
)

python -c "import fastapi, uvicorn, requests" >nul 2>&1
if errorlevel 1 (
  echo Installing Python dependencies from requirements.txt ...
  pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip install failed.
    echo(
    pause
    exit /b 1
  )
)

REM ---- Load .env first (if present) ----
if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
    set "K=%%A"
    set "V=%%B"
    if not "!K!"=="" (
      if "!K:~0,1!" NEQ "#" (
        set "!K!=!V!"
      )
    )
  )
)

REM ---- Defaults ----
if "%API_BASE%"=="" set "API_BASE=http://127.0.0.1:8000"
if "%API_KEY%"=="" set "API_KEY=test-key-123"
if "%CALLBACK_PORT%"=="" set "CALLBACK_PORT=9000"

REM WEBHOOK_SECRET is optional for upload, but required if you want signature verification on receiver.
if "%WEBHOOK_SECRET%"=="" set "WEBHOOK_SECRET=your-shared-secret"

REM ---- Find IPv4 address (best effort) ----
set "CLIENT_IP="
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /i "IPv4"') do (
  set "ip=%%A"
  set "ip=!ip: =!"
  if not "!ip!"=="" (
    if not defined CLIENT_IP (
      echo !ip! | findstr /b /c:"169.254." >nul
      if errorlevel 1 (
        echo !ip! | findstr /b /c:"127." >nul
        if errorlevel 1 (
          set "CLIENT_IP=!ip!"
        )
      )
    )
  )
)

if "%CLIENT_IP%"=="" (
  echo [WARN] Could not auto-detect IPv4. Using localhost callback URL.
  set "CLIENT_IP=127.0.0.1"
)

if "%CALLBACK_URL%"=="" set "CALLBACK_URL=http://%CLIENT_IP%:%CALLBACK_PORT%/idp/webhook"

echo(
echo ==== IDP Client uploader ====
echo API_BASE=%API_BASE%
echo API_KEY=%API_KEY%
echo CALLBACK_URL=%CALLBACK_URL%
echo WEBHOOK_SECRET=^<set^>
echo(

REM Optional: configure default upload concurrency
if "%UPLOAD_CONCURRENCY%"=="" set "UPLOAD_CONCURRENCY=3"

REM If no args were provided, read files_to_upload.txt
if "%~1"=="" (
  if not exist "files_to_upload.txt" (
    echo [ERROR] No files provided and files_to_upload.txt not found.
    echo Create client_service\files_to_upload.txt with one file path per line.
    echo(
    pause
    exit /b 2
  )

  set "FILE_ARGS="
  for /f "usebackq delims=" %%L in ("files_to_upload.txt") do (
    set "LINE=%%L"
    if not "!LINE!"=="" (
      if "!LINE:~0,1!" NEQ "#" (
        set "FILE_ARGS=!FILE_ARGS! "!LINE!""
      )
    )
  )

  if "!FILE_ARGS!"=="" (
    echo [ERROR] files_to_upload.txt has no usable file paths.
    echo(
    pause
    exit /b 2
  )

  echo Uploading files from files_to_upload.txt ...
  echo(
  python "uploader.py" !FILE_ARGS! --concurrency %UPLOAD_CONCURRENCY%
  set "EXITCODE=%errorlevel%"
  echo(
  echo Done. ExitCode=%EXITCODE%
  pause
  exit /b %EXITCODE%
)

REM ---- Run uploader ----
python "uploader.py" %*
set "EXITCODE=%errorlevel%"
echo(
echo Done. ExitCode=%EXITCODE%
pause
exit /b %EXITCODE%

