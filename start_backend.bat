@echo off
title RecoveryOps Backend
cd /d "%~dp0backend"

echo ==========================================
echo        RECOVERY OPERATIONS SYSTEM
echo ==========================================
echo.

where py >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python launcher 'py' was not found.
  echo Install Python 3.11+ and enable "Add Python to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python virtual environment...
  py -m venv .venv
  if errorlevel 1 (
    echo ERROR: Could not create virtual environment.
    pause
    exit /b 1
  )
)

call ".venv\Scripts\activate.bat"

echo Installing/checking dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo.
  echo ERROR: Dependency installation failed.
  pause
  exit /b 1
)

if not exist ".env" (
  copy /Y ".env.example" ".env" >nul
)

echo.
echo ==========================================
echo Backend starting...
echo Website: http://127.0.0.1:5000
echo Login: admin / admin123
echo ==========================================
echo.

python run.py

echo.
echo Backend stopped.
pause
