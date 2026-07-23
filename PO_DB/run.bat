@echo off
REM Run this from the PO_DB root folder.
REM Starts the watcher: checks .\data every 5 minutes and automatically
REM loads/upserts any CSVs it finds into PO_DB.
REM
REM On success the CSVs are deleted. On failure they are moved into
REM .\data\data_error\<timestamp>\ along with an error.txt explaining why.
REM
REM Leave this window open - it runs until you close it or press Ctrl+C.

call "po_loader\.venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Could not activate virtual environment. Check the path above.
    pause
    exit /b 1
)

python po_loader\watch_data.py --data-dir data --config po_loader\config.ini

echo.
echo Watcher stopped. Press any key to close.
pause >nul