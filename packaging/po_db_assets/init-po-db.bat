@echo off
REM Initialize bundled PostgreSQL and create the PO_DB schema.
REM Run once from the po-db folder (or double-click from dist\IDP-Invoice\po-db\).

setlocal
cd /d "%~dp0"

set "PGROOT=%CD%\pgsql"
set "PGDATA=%CD%\pgdata"
set "PGLOG=%CD%\logs\postgres.log"
set "PATH=%PGROOT%\bin;%PATH%"

if not exist "%PGROOT%\bin\initdb.exe" (
    echo [ERROR] Bundled PostgreSQL not found at %PGROOT%\bin
    echo Run packaging\build.ps1 to bundle PostgreSQL into po-db\pgsql\
    pause
    exit /b 1
)

if not exist "%CD%\logs" mkdir "%CD%\logs"
if not exist "%CD%\pgdata" mkdir "%CD%\pgdata"

if not exist "%PGDATA%\PG_VERSION" (
    echo Initializing PostgreSQL data directory ...
    initdb -D "%PGDATA%" -U postgres -A trust -E UTF8
    if errorlevel 1 (
        echo [ERROR] initdb failed.
        pause
        exit /b 1
    )
    echo port = 5432 >> "%PGDATA%\postgresql.conf"
    echo listen_addresses = '127.0.0.1' >> "%PGDATA%\postgresql.conf"
)

echo Starting PostgreSQL ...
pg_ctl start -D "%PGDATA%" -l "%PGLOG%" -w
if errorlevel 1 (
    echo [ERROR] pg_ctl start failed. Check %PGLOG%
    pause
    exit /b 1
)

echo Creating PO_DB database if needed ...
psql -U postgres -tc "SELECT 1 FROM pg_database WHERE datname = 'PO_DB'" | findstr /C:"1" >nul
if errorlevel 1 (
    psql -U postgres -c "CREATE DATABASE \"PO_DB\";"
    if errorlevel 1 (
        echo [ERROR] CREATE DATABASE failed.
        pause
        exit /b 1
    )
)

echo Applying schema from sql\create_po_database.sql ...
psql -U postgres -d PO_DB -f "%CD%\sql\create_po_database.sql"
if errorlevel 1 (
    echo [ERROR] Schema apply failed.
    pause
    exit /b 1
)

echo.
echo [OK] PO_DB is ready on localhost:5432
echo      Drop CSVs into data\ and run run-watcher.bat (or start the main launcher).
echo.
pause
