@echo off
@REM cd /d "C:\Users\TSE\Desktop\Shravan\tse_iot\tse2-main\"

REM Activate the virtual environment
call tseVenv\Scripts\activate.bat

REM Start Flask app (ensure app.py has app.run(...))
start "" pythonw app.py

REM Wait briefly to ensure Flask starts before running the simulator
timeout /t 2 /nobreak >nul

call deactivate

REM Change directory to the simulator folder and run it
cd /d "C:\Users\TSE\Desktop\iot_report"
cd
start "" pythonw app.py
