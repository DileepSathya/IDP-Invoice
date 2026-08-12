@echo off
REM Bootstrap bundled/external PostgreSQL, create PO_DB + tables, then start CSV watcher.
cd /d "%~dp0"
if not exist "po-db.exe" (
    echo [ERROR] po-db.exe not found. Run packaging\build.ps1 first.
    pause
    exit /b 1
)
po-db.exe %*
