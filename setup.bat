@echo off
SETLOCAL

REM Navigate to the directory this script is in
cd /d "%~dp0"

REM Step 1: Create the virtual environment if it doesn't exist
if not exist venv (
    echo Creating virtual environment in "venv"...
    python -m venv venv
) else (
    echo Virtual environment already exists.
)

REM Step 2: Activate the venv
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Step 3: Install dependencies
echo Installing dependencies from requirements.txt...
pip install --upgrade pip
pip install -r requirements.txt

REM Step 4: Install Playwright browsers
echo Installing Playwright browsers...
playwright install

echo Setup complete.
pause
ENDLOCAL
