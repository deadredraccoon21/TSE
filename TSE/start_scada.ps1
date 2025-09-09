# PowerShell equivalent of your startup script

Write-Host "Starting applications..."

# Get the directory of this script
$CurrentDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# Path to Python executables in venv
$VenvPython = Join-Path $CurrentDir "tseVenv\Scripts\python.exe"
$VenvPythonw = Join-Path $CurrentDir "tseVenv\Scripts\pythonw.exe"

# Start the main Flask app in background
Write-Host "Starting main Flask application (TSE)..."
Start-Process -FilePath $VenvPythonw -ArgumentList "`"$CurrentDir\app.py`""

# Wait briefly to ensure Flask starts
Start-Sleep -Seconds 5

# Start the simulator app
$SimulatorDir = Join-Path $CurrentDir "..\iot_report"
Write-Host "Starting simulator application (iot_report)..."
Start-Process -FilePath (Join-Path $SimulatorDir "tseVenv\Scripts\pythonw.exe") -ArgumentList "`"$SimulatorDir\app.py`""

Write-Host "Startup script finished."
