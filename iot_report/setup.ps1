# Function to check if a command exists
function command_exists {
    param ($command)
    $exists = Get-Command $command -ErrorAction SilentlyContinue
    return $exists -ne $null
}

# Install Python on Windows
function install_python_windows {
    Write-Output "Downloading Python..."
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.10.4/python-3.10.4-amd64.exe" -OutFile "python-3.10.4-amd64.exe"
    Write-Output "Installing Python..."
    Start-Process -FilePath "python-3.10.4-amd64.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait
    Remove-Item "python-3.10.4-amd64.exe"
    
    # Update PATH environment variable
    $pythonPath = "C:\Program Files\Python310"
    if (!(Test-Path $pythonPath)) {
        $pythonPath = "C:\Program Files\Python311"
    }
    if (!(Test-Path $pythonPath)) {
        $pythonPath = "C:\Program Files\Python312"
    }
    [System.Environment]::SetEnvironmentVariable("PATH", $env:PATH + ";" + $pythonPath + ";" + "$pythonPath\Scripts\", [System.EnvironmentVariableTarget]::Machine)
    
    # Reload the environment variables in the current session
    $env:Path = [System.Environment]::GetEnvironmentVariable("PATH", [System.EnvironmentVariableTarget]::Machine)
}

# Ensure pip is installed
function ensure_pip_installed {
    if (!(command_exists "pip")) {
        Write-Output "Installing pip..."
        Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile "get-pip.py"
        python get-pip.py
        Remove-Item "get-pip.py"
    } else {
        Write-Output "pip is already installed."
    }
}

# Install pip modules
function install_pip_modules {
    Write-Output "Installing pip modules..."
    python -m pip install flask flask-wtf pyodbc bcrypt
}

# Check and install Python if not present
if (command_exists "python") {
    Write-Output "Python is already installed."
} else {
    install_python_windows
}

# Ensure pip is installed
ensure_pip_installed

# Install the required pip modules
install_pip_modules

# Get the current directory where setup.ps1 is located
$sourceDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Get the current user's Startup folder path
$destinationDir = [System.IO.Path]::Combine([System.Environment]::GetFolderPath('Startup'))

# Function to find pythonw.exe
function Find-Pythonw {
    $paths = @(
        "$env:ProgramFiles\Python*",
        "$env:LOCALAPPDATA\Programs\Python\Python*",
        "$env:ProgramFiles(x86)\Python*"
    )
    foreach ($path in $paths) {
        $pythonwPath = Get-ChildItem -Path $path -Filter "pythonw.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pythonwPath) {
            return $pythonwPath.FullName
        }
    }
    throw "pythonw.exe not found."
}

# Find the pythonw.exe path
$pythonExePath = Find-Pythonw

# Copy app.py, static, and templates to Startup folder
Copy-Item -Path (Join-Path $sourceDir "reportapp.py") -Destination $destinationDir -Force
Copy-Item -Path (Join-Path $sourceDir "static") -Destination $destinationDir -Recurse -Force
Copy-Item -Path (Join-Path $sourceDir "templates") -Destination $destinationDir -Recurse -Force

# Rename app.py to app.pyw in the Startup folder
$sourceFile = Join-Path $destinationDir "reportapp.py"
$targetFile = Join-Path $destinationDir "reportapp.pyw"
Rename-Item -Path $sourceFile -NewName "reportapp.pyw" -Force

# Set static and templates folders and their contents to hidden
Get-ChildItem -Path (Join-Path $destinationDir "static") -Recurse | ForEach-Object { $_.Attributes = 'Hidden' }
Get-ChildItem -Path (Join-Path $destinationDir "templates") -Recurse | ForEach-Object { $_.Attributes = 'Hidden' }
(Get-Item -Path (Join-Path $destinationDir "static")).Attributes = 'Hidden'
(Get-Item -Path (Join-Path $destinationDir "templates")).Attributes = 'Hidden'

# Set the default application for .pyw files to pythonw.exe using cmd.exe
$pythonExePathEscaped = [System.IO.Path]::GetFullPath($pythonExePath) -replace '\\', '\\\\'
$assocCommand = "assoc .pyw=Python.File"
$ftypeCommand = "ftype Python.File=`"$pythonExePathEscaped`" `"%1`""

cmd.exe /c $assocCommand
cmd.exe /c $ftypeCommand

# Create a scheduled task to run app.pyw on startup
$taskName = "RunReportAppOnStartup"
$taskPath = Join-Path $destinationDir "reportapp.pyw"
$action = New-ScheduledTaskAction -Execute $pythonExePath -Argument $taskPath
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

# Check if the task already exists and delete it
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -TaskName $taskName

Write-Host "Setup completed successfully."
Write-Output "Setup complete!"