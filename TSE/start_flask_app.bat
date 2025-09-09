@echo off
ECHO Starting applications...

REM This script assumes it is located in D:\tse\TSE_app\TSE\
REM All paths are relative to this location.

REM Get the directory where this batch file is located
set "CURRENT_DIR=%~dp0"

REM Activate the virtual environment
ECHO Activating virtual environment...
call "%CURRENT_DIR%tseVenv\Scripts\activate.bat"

REM Start the main Flask app in the background using the venv's pythonw
ECHO Starting main Flask application (TSE)...
start "Flask App - TSE" pythonw "%CURRENT_DIR%app.py"

REM Wait briefly to ensure Flask starts before running the simulator
timeout /t 5 /nobreak >nul


REM Change directory to the simulator folder and run it
ECHO Starting simulator application (iot_report)...
cd /d "%CURRENT_DIR%..\iot_report"
start "Simulator App - iot_report" pythonw app.py

ECHO Startup script finished.