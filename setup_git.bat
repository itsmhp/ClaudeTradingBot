@echo off
echo ============================================================
echo  ClaudeTradingBot - Git Setup and Push
echo  Target: https://github.com/itsmhp/ClaudeTradingBot
echo ============================================================
echo.
cd /d "%~dp0"

git --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: git not found. Install Git from https://git-scm.com
    pause
    exit /b 1
)

echo [1/5] Initializing git repository...
if not exist ".git" (
    git init
    git branch -M main
) else (
    echo      Already initialized.
)

echo.
echo [2/5] Setting remote origin...
git remote get-url origin >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    git remote set-url origin https://github.com/itsmhp/ClaudeTradingBot.git
) else (
    git remote add origin https://github.com/itsmhp/ClaudeTradingBot.git
)
echo      Remote: https://github.com/itsmhp/ClaudeTradingBot.git

echo.
echo [3/5] Staging all files...
git add .
git status --short

echo.
echo [4/5] Creating commit...
git commit -m "Initial commit: ClaudeTradingBot Phase 1 + Phase 2" -m "- Core trading engine (MT5 bridge, Claude client, risk manager, signal engine)" -m "- FastAPI backend with WebSocket support (/ws/live)" -m "- Real-time dashboard (dark theme, Chart.js, 5 sections)" -m "- Telegram notifications + webhook notifier" -m "- SQLite trade logging with async SQLAlchemy" -m "- Screenshot upload feature" -m "- Full test suite (19 tests)" -m "- bootstrap.py + bootstrap_phase2.py for one-command setup" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"

echo.
echo [5/5] Pushing to GitHub...
git push -u origin main
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo If push failed due to auth, run:
    echo   git push -u origin main
    echo and sign in via the browser popup.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  SUCCESS!
echo  View at: https://github.com/itsmhp/ClaudeTradingBot
echo ============================================================
pause
