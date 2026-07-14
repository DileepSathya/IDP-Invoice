@echo off
REM Run this from the PO_DB root folder.
REM Loads/upserts whatever CSVs are sitting in .\data into PO_DB.

call "po_loader\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Could not activate virtual environment. Check the path above.
    pause
    exit /b 1
)

python po_loader\load_data.py --data-dir data --config po_loader\config.ini

echo.
echo Done. Press any key to close.
pause >nul