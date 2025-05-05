@echo off
REM Change to the directory this .bat file is in
cd /d "%~dp0"

REM Activate the virtual environment (relative to this path)
call "%~dp0venv\Scripts\activate.bat"

REM Run the Python script
python main.py

