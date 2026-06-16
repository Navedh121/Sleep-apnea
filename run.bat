@echo off
setlocal EnableDelayedExpansion
title SpO2 Monitor - Starting...

echo.
echo  SpO2 Sleep-Apnea Screening Monitor
echo  =====================================
echo.

:: ── Step 1: Virtual environment ────────────────────────────────────────────
if not exist .venv (
    echo [1/4] Creating Python virtual environment ^(first run only^)...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo  ERROR: Could not create virtual environment.
        echo  Make sure Python 3.10 or newer is installed and on your PATH.
        echo  Download from: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    echo [1/4] Installing dependencies ^(this only runs once^)...
    call .venv\Scripts\activate.bat
    pip install -r requirements.txt --quiet
    if errorlevel 1 (
        echo.
        echo  ERROR: pip install failed. Check requirements.txt and your internet connection.
        pause
        exit /b 1
    )
) else (
    echo [1/4] Python environment ready.
    call .venv\Scripts\activate.bat
)

:: ── Step 2: Start the server in a separate window ──────────────────────────
echo [2/4] Starting server...
start "SpO2 Server  (close this window to stop the server)" ^
    cmd /k "call .venv\Scripts\activate.bat && echo. && echo  Server starting... && echo. && uvicorn backend.main:app --port 8000"

:: ── Step 3: Wait until the server accepts requests ─────────────────────────
echo [3/4] Waiting for server to be ready...
:wait_loop
python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/nights', timeout=2)" 2>nul
if errorlevel 1 (
    timeout /t 1 /nobreak >nul
    goto wait_loop
)
echo      Server is ready.

:: ── Step 4: Seed demo data if the database is empty ───────────────────────
echo [4/4] Checking database...
python seed_demo.py

:: ── Open the app in the default browser ───────────────────────────────────
echo.
echo  Opening http://localhost:8000 ...
start http://localhost:8000

echo.
echo  Done! The app should open in your browser now.
echo  To stop the server, close the "SpO2 Server" window.
echo.
pause
