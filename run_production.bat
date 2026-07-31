@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo(
echo ==== IDP Invoice - Production Runner ====

REM ---- Python presence check ----
where py >nul 2>&1
if errorlevel 1 (
  where python >nul 2>&1
  if errorlevel 1 goto :python_missing
)

REM ---- MongoDB best-effort check ----
call :check_mongo

REM ---- Ensure invoice lifecycle folders exist ----
for %%D in (
  "invoices_data\to_be_processed"
  "invoices_data\_api_staging"
  "invoices_data\HITL_pending"
  "invoices_data\ERROR"
  "invoices_data\Completed"
) do (
  if not exist %%D mkdir %%D >nul 2>&1
)

REM ---- Ensure venv ----
set "VENV_PY=venv\Scripts\python.exe"
if not exist "%VENV_PY%" set "VENV_PY=.venv\Scripts\python.exe"
if not exist "%VENV_PY%" goto :need_install

REM ---- Validate venv Python version (must be 3.11) ----
set "VENVVER="
set "PYFULL="
for /f "tokens=2" %%v in ('"%VENV_PY%" --version 2^>^&1') do set "PYFULL=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PYFULL%") do set "VENVVER=%%a.%%b"
if "%VENVVER%"=="" goto :need_install
if not "%VENVVER%"=="3.11" goto :wrong_venv_python

REM ---- Ensure dependencies ----
"%VENV_PY%" -c "import watchdog, pymongo, dotenv, fastapi, uvicorn, multipart, requests; import llama_index.core; import llama_index.embeddings.huggingface; import llama_index.llms.ollama" >nul 2>&1
if errorlevel 1 goto :need_install

goto :start_watcher

:need_install
echo Virtual environment/dependencies not ready. Running install.bat ...
call "%~dp0install.bat" --no-pause
if errorlevel 1 goto :install_failed

REM Re-check venv version after install
set "VENV_PY=venv\Scripts\python.exe"
if not exist "%VENV_PY%" set "VENV_PY=.venv\Scripts\python.exe"
set "VENVVER="
set "PYFULL="
for /f "tokens=2" %%v in ('"%VENV_PY%" --version 2^>^&1') do set "PYFULL=%%v"
for /f "tokens=1,2 delims=." %%a in ("%PYFULL%") do set "VENVVER=%%a.%%b"
if not "%VENVVER%"=="3.11" goto :wrong_venv_python

:start_watcher

echo(
echo =======================
echo Select service to run:
echo   1. Files upload watcher
echo   2. Chatbot service
echo   3. Both (watcher + chatbot)
echo   4. API server (FastAPI)
echo   5. API server + Frontend + watcher
echo =======================
set "SERVICE_CHOICE="
set "CHOICE_SET=0"
echo(
echo Enter choice (1/2/3/4/5):
echo Auto-selecting option 5 if no input within 10 seconds...
for /l %%S in (10,-1,1) do (
  echo   Default 5 in %%S seconds... Press 1/2/3/4/5 to override.
  choice /c 12345 /n /t 1 >nul
  if not errorlevel 0 (
    set "SERVICE_CHOICE=!ERRORLEVEL!"
    set "CHOICE_SET=1"
  )
)
if "!CHOICE_SET!"=="0" set "SERVICE_CHOICE=5"
:service_choice_done

:service_choice_done
if "%SERVICE_CHOICE%"=="1" goto :run_watcher_only
if "%SERVICE_CHOICE%"=="2" goto :run_chatbot_only
if "%SERVICE_CHOICE%"=="3" goto :run_both
if "%SERVICE_CHOICE%"=="4" goto :run_api_only
if "%SERVICE_CHOICE%"=="5" goto :run_api_and_frontend
echo Invalid choice. Defaulting to option 5.
goto :run_api_and_frontend

:run_watcher_only
echo(
echo Starting watcher in a new window ...
start "watcher" cmd /k ""%cd%\%VENV_PY%" "%cd%\backend\agents\watch_raw.py""

echo(
echo [OK] Watcher window started.
echo Drop files into: %cd%\invoices_data\to_be_processed
echo(
echo Press any key to close this window.
pause >nul
exit /b 0

:run_chatbot_only
echo(
echo Starting chatbot service in this window ...
"%VENV_PY%" "%cd%\backend\agents\rag_chatbot.py"

echo(
echo Chatbot service exited.
echo Press any key to close this window.
pause >nul
exit /b 0

:run_both
echo(
echo Starting watcher in a new window ...
start "watcher" cmd /k ""%cd%\%VENV_PY%" "%cd%\backend\agents\watch_raw.py""

echo(
echo Starting chatbot service in this window ...
"%VENV_PY%" "%cd%\backend\agents\rag_chatbot.py"

echo(
echo Chatbot service exited. Watcher window may still be open.
echo Press any key to close this window.
pause >nul
exit /b 0

:run_api_only
echo(
call :start_tally_bridge
echo Starting FastAPI API server in this window ...
"%VENV_PY%" -m uvicorn backend.api:app --host 0.0.0.0 --port 8000

echo(
echo API server exited.
echo Press any key to close this window.
pause >nul
exit /b 0

:run_api_and_frontend
echo(
echo Starting watcher in a new window ...
start "watcher" cmd /k ""%cd%\%VENV_PY%" "%cd%\backend\agents\watch_raw.py""

echo(
call :start_tally_bridge
call :ensure_frontend

start "frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

REM Open default browser pointing at the frontend
start "" "http://localhost:5173/"

"%VENV_PY%" -m uvicorn backend.api:app --host 0.0.0.0 --port 8000

echo(
echo API server exited. Frontend window may still be open.
echo Press any key to close this window.
pause >nul
exit /b 0

:ensure_frontend
REM Ensure frontend dependencies (run npm install once if needed)
pushd "%~dp0frontend"
if exist "node_modules" (
  echo Frontend dependencies already installed.
) else (
  echo Installing frontend dependencies - running npm install ...
  call npm install
)
popd
goto :eof

:ensure_tally_bridge_env
if not exist "TALLY INTEGRATION\.env" (
  if exist "TALLY INTEGRATION\.env.example" (
    copy /y "TALLY INTEGRATION\.env.example" "TALLY INTEGRATION\.env" >nul
  )
)
goto :eof

:start_tally_bridge
if not exist "TALLY INTEGRATION\api_server.py" goto :eof
call :ensure_tally_bridge_env
findstr /i "TALLY_ENABLED=true" .env >nul 2>&1
if errorlevel 1 goto :eof
echo Starting Tally bridge on port 8001 ^(requires TallyPrime on port 9000^) ...
start "tally-bridge" cmd /k "cd /d %~dp0 && %VENV_PY% -m backend.run_tally_bridge"
goto :eof

:python_missing
echo [ERROR] Python not found in PATH.
echo Install Python 3.11+ and then run install.bat.
pause
exit /b 1

:install_failed
echo [ERROR] install.bat failed.
pause
exit /b 1

:wrong_venv_python
echo [ERROR] Your venv is using Python %VENVVER% but PaddleOCR requires Python 3.11 on Windows.
echo         Fix: delete venv (and/or .venv) and re-run run_production.bat (it should recreate with Python 3.11).
pause
exit /b 1

:check_mongo
set "MONGO_OK=0"
where mongod >nul 2>&1
if not errorlevel 1 set "MONGO_OK=1"
if "%MONGO_OK%"=="0" (
  sc query MongoDB >nul 2>&1
  if not errorlevel 1 set "MONGO_OK=1"
)
if "%MONGO_OK%"=="0" (
  echo [WARN] MongoDB not detected ^(mongod not in PATH and service "MongoDB" not found^).
  echo        If you want DB storage, install MongoDB Community Server and start it.
)
exit /b 0

