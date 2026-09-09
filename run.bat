@echo off
rem ============================================================
rem  NovelFound one-click launcher (Windows)
rem  First run: creates .venv and installs dependencies.
rem  Later runs: just starts the app.
rem  Note: this file is ASCII + CRLF on purpose, so cmd.exe
rem        parses it correctly on any Windows codepage.
rem ============================================================
setlocal
cd /d "%~dp0"

if not exist "main.py" (
    echo [ERROR] main.py not found. Please put run.bat next to main.py.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 goto :fail_venv
    echo [2/3] Installing dependencies ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail_deps
)

echo [3/3] Checking dependencies ...
".venv\Scripts\python.exe" -c "import PyQt5, requests, bs4" 1>nul 2>nul
if errorlevel 1 (
    echo Dependencies missing, installing from requirements.txt ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :fail_deps
)

echo Starting NovelFound ...
start "" ".venv\Scripts\pythonw.exe" main.py
exit /b 0

:fail_venv
echo [ERROR] Cannot create virtual environment.
echo         Make sure Python 3.8+ is installed and available as "python".
pause
exit /b 1

:fail_deps
echo [ERROR] Failed to install dependencies. Check your network, or run manually:
echo         .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1