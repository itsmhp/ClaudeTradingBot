@echo off
echo ============================================================
echo  ClaudeTradingBot - Phase 1 Bootstrap
echo ============================================================
echo.
cd /d "%~dp0"
python bootstrap.py
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ERROR: bootstrap.py failed. Make sure Python is installed.
    echo Try: py bootstrap.py  OR  python3 bootstrap.py
    pause
    exit /b 1
)
echo.
echo ============================================================
echo  Installing Python dependencies...
echo ============================================================
pip install -r requirements.txt
echo.
echo ============================================================
echo  Running tests...
echo ============================================================
pytest tests/ -v --tb=short
echo.
pause
