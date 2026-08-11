@echo off
REM Watch po-db\data for CSV files and load them into PO_DB automatically.
REM Leave this window open while running, or use the main IDP launcher (PO_DB_WATCHER_ENABLED=true).

setlocal
cd /d "%~dp0"

if not exist "po-watcher.exe" (
    echo [ERROR] po-watcher.exe not found. Run packaging\build.ps1 first.
    pause
    exit /b 1
)

if not exist "config.ini" (
    if exist "config.example.ini" (
        copy /Y "config.example.ini" "config.ini"
    ) else (
        echo [ERROR] config.ini not found.
        pause
        exit /b 1
    )
)

po-watcher.exe --data-dir data --loader po-loader.exe --config config.ini

echo.
echo Watcher stopped. Press any key to close.
pause >nul
