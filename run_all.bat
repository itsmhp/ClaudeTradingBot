@echo off
echo ============================================================
echo  ClaudeTradingBot - Phase 2 Bootstrap
echo ============================================================
echo.
cd /d "%~dp0"

echo [1/2] Running Phase 1 bootstrap (creates core modules)...
python bootstrap.py
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: bootstrap.py failed.
    pause
    exit /b 1
)

echo.
echo [2/2] Running Phase 2 bootstrap (creates dashboard + WS)...
python bootstrap_phase2.py
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: bootstrap_phase2.py failed.
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
echo ============================================================
echo  Done! Start the bot with:
echo    python main.py
echo  Dashboard at: http://localhost:8000
echo ============================================================
pause
