# Function to check if a command exists
function command_exists {
    param ($command)
    try {
        Get-Command $command -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

# Install Python on Windows
function install_python_windows {
    Write-Output "Downloading Python..."
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.10.4/python-3.10.4-amd64.exe" -OutFile "python-3.10.4-amd64.exe"
    Write-Output "Installing Python..."
    Start-Process -FilePath "python-3.10.4-amd64.exe" -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait
    Remove-Item "python-3.10.4-amd64.exe"

    # Update PATH
    $possiblePaths = @(
        "C:\Program Files\Python310",
        "C:\Program Files\Python311",
        "C:\Program Files\Python312"
    )
    foreach ($pythonPath in $possiblePaths) {
        if (Test-Path $pythonPath) {
            [System.Environment]::SetEnvironmentVariable("PATH", $env:PATH + ";" + $pythonPath + ";" + "$pythonPath\Scripts\", [System.EnvironmentVariableTarget]::Machine)
            break
        }
    }

    # Reload PATH for current session
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

# Install requirements.txt
function install_requirements_file {
    if (Test-Path "requirements.txt") {
        Write-Output "Installing modules from requirements.txt..."
        python -m pip install -r requirements.txt
    } else {
        Write-Output "requirements.txt not found, skipping."
    }
}

# Run Python scripts
function run_python_scripts {
    Write-Output "Running db.py..."
    python db.py
}

# Find pythonw.exe
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

# Create .bat file to run the Flask app
function create_startup_bat {
    param (
        [string]$batPath,
        [string]$scriptDir
    )
    $batContent = @"
REM Start Flask app (ensure app.py has app.run(...))
start "" pythonw app.py
"@
    Set-Content -Path $batPath -Value $batContent -Encoding ASCII
}

# Create shortcut in Startup folder
function create_shortcut {
    param (
        [string]$targetPath,
        [string]$shortcutPath
    )
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetPath
    $shortcut.WorkingDirectory = Split-Path $targetPath
    $shortcut.Save()
}

# ========== MAIN EXECUTION ==========

# Check and install Python if needed
if (command_exists "python") {
    Write-Output "Python is already installed."
} else {
    install_python_windows
}

ensure_pip_installed
install_pip_modules
install_requirements_file
run_python_scripts

# Install Grafana Enterprise
Write-Output "Downloading Grafana Enterprise installer..."
$grafanaEnterpriseUrl = "https://dl.grafana.com/enterprise/release/grafana-enterprise-10.0.0.windows-amd64.msi"
$installerPath = "C:\temp\grafana_enterprise_installer.msi"

if (-not (Test-Path -Path (Split-Path -Path $installerPath -Parent))) {
    New-Item -Path (Split-Path -Path $installerPath -Parent) -ItemType Directory
}

Invoke-WebRequest -Uri $grafanaEnterpriseUrl -OutFile $installerPath
Write-Output "Installing Grafana..."
Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$installerPath`" /quiet /norestart" -NoNewWindow -Wait
Start-Service -Name "grafana"
Write-Output "Grafana service started."

# Paths
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$batFilePath = Join-Path $scriptDir "start_flask_app.bat"
$startupFolder = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startupFolder "StartFlaskApp.lnk"

# Create .bat file and shortcut
create_startup_bat -batPath $batFilePath -scriptDir $scriptDir
create_shortcut -targetPath $batFilePath -shortcutPath $shortcutPath

Write-Host "Setup completed successfully. Flask app will auto-start at login."
