@echo off
echo =======================================
echo Starting TSEScada Build Process...
echo =======================================

:: 1. Activate the virtual environment
call venv\Scripts\activate.bat

:: 2. Force Playwright to download the browser locally
set PLAYWRIGHT_BROWSERS_PATH=0
playwright install chromium

:: 3. Run the PyInstaller build
python -m PyInstaller TSEScada.spec --clean

echo =======================================
echo Build Complete! Check the "dist" folder.
echo =======================================
pause