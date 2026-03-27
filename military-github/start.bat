@echo off
setlocal enabledelayedexpansion

echo ========================================
echo   Military Manager - Startup Script
echo ========================================
echo.

:: Check if UV is installed
where uv >nul 2>nul
if %errorlevel% neq 0 (
    echo [!] UV not found. Installing UV...
    powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
    echo [+] UV installed. Please restart this script.
    pause
    exit /b
)

:: Check if virtual environment exists
if not exist ".venv" (
    echo [*] Creating virtual environment...
    uv venv
)

:: Install/update dependencies
echo [*] Installing dependencies...
uv pip install -e ".[dev]"

:: Check for .env file
if not exist ".env" (
    if exist ".env.example" (
        echo [!] .env file not found. Copying from .env.example...
        copy .env.example .env
        echo [!] Please edit .env with your settings, then run this script again.
        notepad .env
        pause
        exit /b
    )
)

:: Create data directory
if not exist "data" (
    echo [*] Creating data directory...
    mkdir data
)

:: Activate virtual environment and run
echo [*] Starting application...
call .venv\Scripts\activate.bat

:: Run Streamlit app
streamlit run src/military_manager/main.py --server.port 8501

pause
