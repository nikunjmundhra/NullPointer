@echo off
setlocal
title AirLens India - Judge Demo Launcher

REM This file lives in dashboard\. Its parent is the project root.
cd /d "%~dp0.."

echo.
echo =============================================================
echo   AIRLENS INDIA - LIVE MODEL + DASHBOARD DEMO
echo =============================================================
echo.

REM Determine Python executable
set "PYTHON_EXE="
where py >nul 2>nul
if %errorlevel% equ 0 (
  set "PYTHON_EXE=py -3"
) else (
  where python >nul 2>nul
  if %errorlevel% equ 0 (
    set "PYTHON_EXE=python"
  )
)

if "%PYTHON_EXE%"=="" (
  echo Error: Python 3 was not found in PATH.
  echo Please install Python 3 and add it to PATH before running this script.
  pause
  exit /b 1
)

REM 1. Create virtual environment if it doesn't exist
if not exist ".venv\Scripts\python.exe" (
  echo [1/4] Creating virtual environment ^(.venv^)...
  %PYTHON_EXE% -m venv .venv
  if errorlevel 1 (
    echo.
    echo Failed to create virtual environment.
    pause
    exit /b 1
  )
) else (
  echo [1/4] Found existing virtual environment ^(.venv^).
)

REM 2. Install requirements
echo [2/4] Installing/verifying requirements from requirements.txt...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo Failed to install requirements.
  pause
  exit /b 1
)

REM 3. Run the pipeline
echo.
echo [3/4] Running the AQI prediction pipeline...
".venv\Scripts\python.exe" src\pipeline\run_pipeline.py
if errorlevel 1 (
  echo.
  echo The prediction pipeline failed, so the dashboard was not started.
  pause
  exit /b 1
)

REM 4. Start local server & open dashboard
echo.
echo [4/4] Starting the local dashboard server...
start "AirLens Dashboard Server" cmd /k "".venv\Scripts\python.exe" scripts\serve_dashboard.py"

echo Opening the dashboard in your default browser...
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000/dashboard/"

echo.
echo =============================================================
echo Demo is ready! Keep the 'AirLens Dashboard Server' window open.
echo To run a fresh prediction, close that server window and run this file again.
echo =============================================================
echo.
pause
