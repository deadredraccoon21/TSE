# Requires Administrator privileges to install Python, Grafana, modify Machine PATH,
# and restart Grafana service. Run this script as Administrator.

# Function to check if a command exists
function command_exists {
    param ($command)
    try {
        # Get-Command throws an error if command is not found, which is caught below
        $null = Get-Command $command -ErrorAction Stop
        return $true
    }
    catch {
        return $false
    }
}

# Install Python on Windows (Enhanced version with better PATH handling and error reporting)
function install_python_windows {
    Write-Output "Python command not found. Attempting to download and install Python 3.10.4..."
    $pythonInstaller = "python-3.10.4-amd64.exe"
    # Using a specific version as requested. Consider updating the URL for newer versions.
    $pythonDownloadUri = "https://www.python.org/ftp/python/3.10.4/$pythonInstaller"
    $tempDir = Join-Path $env:TEMP "PythonInstaller" # Use a dedicated temp dir
    $downloadedInstallerPath = Join-Path $tempDir $pythonInstaller

    # Create temp directory
    try {
        if (-not (Test-Path $tempDir)) {
            New-Item -Path $tempDir -ItemType Directory -ErrorAction Stop | Out-Null
            Write-Verbose "Created temporary directory: $tempDir"
        }
    }
    catch {
        Write-Error "Failed to create temporary directory: $($_.Exception.Message)"
        throw "Setup failed during temporary directory creation for Python installer."
    }


    try {
        # Download the installer
        Write-Output "Downloading Python installer from $pythonDownloadUri..."
        Invoke-WebRequest -Uri $pythonDownloadUri -OutFile $downloadedInstallerPath -ErrorAction Stop
        Write-Output "Download complete."

        # Install Python silently for all users, adding to PATH
        Write-Output "Running Python installer (This may take a few minutes)..."
        # Use /passive for a progress bar, /quiet for no UI at all
        Start-Process -FilePath $downloadedInstallerPath -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1" -Wait -ErrorAction Stop
        Write-Output "Python installation complete."

        # Verify installation path and update Machine PATH explicitly as a fallback/assurance
        # The installer with PrependPath=1 *should* handle this, but this adds robustness.
        # We'll add common Python 3 directories to the Machine PATH if they don't exist.
        $pythonInstallPathsToVerify = @(
            "C:\Program Files\Python310",
            "C:\Program Files\Python310\Scripts"
            # Add other potential Python versions if needed for future flexibility
            # "C:\Program Files\Python311",
            # "C:\Program Files\Python311\Scripts",
            # "C:\Program Files\Python312",
            # "C:\Program Files\Python312\Scripts"
            # Include user-specific install paths if relevant, though InstallAllUsers=1 aims for Program Files
            # Join-Path $env:LOCALAPPDATA "Programs\Python\Python*" | ForEach-Object { Get-ChildItem $_ -Directory | Select-Object -ExpandProperty FullName }
        ) | Where-Object { Test-Path $_ -PathType Container } | Sort-Object -Descending # Sort descending to prefer higher versions if checking multiple

        $currentMachinePath = [System.Environment]::GetEnvironmentVariable("PATH", [System.EnvironmentVariableTarget]::Machine)
        $pathChanged = $false

        foreach ($pPath in $pythonInstallPathsToVerify) {
            # Check if path is already present in the Machine PATH (case-insensitive, handle semicolons and leading/trailing spaces around paths)
            if ($currentMachinePath -notmatch "(?i)(^|;)\s*$([regex]::Escape($pPath))\s*(;|$)") {
                Write-Output "Adding '$pPath' to Machine PATH."
                $currentMachinePath += ";" + $pPath
                $pathChanged = $true
            }
            else {
                Write-Verbose "'$pPath' already found in Machine PATH."
            }
        }

        if ($pathChanged) {
            [System.Environment]::SetEnvironmentVariable("PATH", $currentMachinePath, [System.EnvironmentVariableTarget]::Machine)
            Write-Output "Machine PATH environment variable updated. Restart may be required for some applications to see the change."
        }
        else {
            Write-Output "Required Python paths already seem to be in the Machine PATH."
        }


    }
    catch {
        Write-Error "An error occurred during Python installation: $($_.Exception.Message)"
        throw "Python installation failed." # Re-throw the error
    }
    finally {
        # Clean up installer temp directory and files
        Write-Output "Cleaning up temporary installation files..."
        Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Reload PATH for current session to recognize 'python' command immediately
    Write-Output "Reloading current session PATH..."
    $env:Path = [System.Environment]::GetEnvironmentVariable("PATH", [System.EnvironmentVariableTarget]::Machine)

    # Final verification in the current session
    if (command_exists "python") {
        Write-Output "Python installed successfully and 'python' command is available in this session."
    }
    else {
        Write-Warning "Python was installed, but the 'python' command is not yet available in this session's PATH even after reloading. You might need to restart your terminal or machine for it to be recognized correctly."
    }
}

# Ensure pip is installed (Enhanced version with better error handling and upgrade)
function ensure_pip_installed {
    Write-Output "Checking if pip is installed..."
    if (!(command_exists "pip")) {
        Write-Output "pip command not found. Attempting to install pip using get-pip.py..."
        $getPipScript = "get-pip.py"
        $getPipUri = "https://bootstrap.pypa.io/get-pip.py"
        try {
            Write-Output "Downloading get-pip.py from $getPipUri..."
            Invoke-WebRequest -Uri $getPipUri -OutFile $getPipScript
            if ($LASTEXITCODE -ne 0) { throw "Failed to download get-pip.py" }

            Write-Output "Running get-pip.py to install pip..."
            & python $getPipScript --force-reinstall --upgrade --no-cache-dir --disable-pip-version-check
            if ($LASTEXITCODE -ne 0) { throw "get-pip.py script failed to execute." }

            Write-Output "pip installation complete."
        }
        catch {
            Write-Error "An error occurred during pip installation: $($_.Exception.Message)"
            throw "Pip installation failed." # Re-throw error
        }
        finally {
            # Clean up get-pip.py
            if (Test-Path $getPipScript) {
                Remove-Item $getPipScript -Force -ErrorAction SilentlyContinue
            }
        }
    }
    else {
        Write-Output "pip is already installed."
        Write-Output "Upgrading pip..."
        try {
            & python -m pip install --upgrade pip --no-cache-dir --disable-pip-version-check
            if ($LASTEXITCODE -ne 0) { Write-Warning "pip upgrade command exited with a non-zero status code." }
            else { Write-Output "pip upgraded." }
        }
        catch {
            Write-Warning "Failed to upgrade pip: $($_.Exception.Message)"
        }
    }
    # Verify pip command is available after potential install/upgrade
    if (!(command_exists "pip")) {
        throw "Pip command is not available even after attempted installation/upgrade. Cannot proceed with module installation."
    }
}

# --- NEW FUNCTION to create a Python virtual environment ---
function Create-PythonVenv {
    param (
        [string]$VenvPath
    )
    if (Test-Path $VenvPath) {
        Write-Output "Python virtual environment already exists at '$VenvPath'. Skipping creation."
    }
    else {
        Write-Output "Creating Python virtual environment at '$VenvPath'..."
        try {
            & python -m venv $VenvPath
            if ($LASTEXITCODE -ne 0) { throw "Failed to create virtual environment." }
            Write-Output "Virtual environment created successfully."
        }
        catch {
            Write-Error "An error occurred while creating the virtual environment: $($_.Exception.Message)"
            throw "Virtual environment creation failed."
        }
    }
}

# Install pip modules (Modified to install into a Venv)
function install_pip_modules {
    param (
        [string]$VenvPythonExe # Path to the python.exe inside the venv
    )
    Write-Output "Installing required pip modules (flask, flask-wtf, pyodbc, bcrypt) into virtual environment..."
    try {
        & $VenvPythonExe -m pip install flask flask-wtf pyodbc bcrypt --no-cache-dir --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { throw "pip install for required modules failed." }
        Write-Output "Required pip modules installed successfully."
    }
    catch {
        Write-Error "An error occurred during installation of required pip modules: $($_.Exception.Message)"
        throw "Installation of required pip modules failed."
    }
}

# Install requirements.txt (Modified to install into a Venv)
function install_requirements_file {
    param (
        [string]$VenvPythonExe,
        [string]$requirementsFilePath
    )
    if (Test-Path $requirementsFilePath) {
        Write-Output "Installing modules from '$requirementsFilePath' into virtual environment..."
        try {
            & $VenvPythonExe -m pip install -r $requirementsFilePath --no-cache-dir --disable-pip-version-check
            if ($LASTEXITCODE -ne 0) { throw "pip install from requirements.txt failed." }
            Write-Output "Modules from requirements.txt installed successfully."
        }
        catch {
            Write-Error "An error occurred during installation from requirements.txt: $($_.Exception.Message)"
            throw "Installation from requirements.txt failed."
        }
    }
    else {
        Write-Output "requirements.txt not found at '$requirementsFilePath', skipping."
    }
}

# Run Python scripts (Modified to use Venv's Python)
function run_python_scripts {
    param (
        [string]$VenvPythonExe
    )
    $dbScriptPath = Join-Path $PSScriptRoot "db.py" # Assume db.py is in script directory
    # Ensure db.py exists before trying to run it
    if (Test-Path $dbScriptPath) {
        Write-Output "Running db.py using virtual environment's Python..."
        try {
            & $VenvPythonExe $dbScriptPath
            if ($LASTEXITCODE -ne 0) { throw "db.py script failed to execute successfully." }
            Write-Output "db.py executed successfully."
        }
        catch {
            Write-Error "An error occurred while running db.py: $($_.Exception.Message)"
            # Decide if this should be a fatal error or just a warning
            Write-Warning "Execution of db.py failed."
            # throw "db.py execution failed." # Uncomment if this is a critical failure
        }
    }
    else {
        Write-Output "db.py not found at '$dbScriptPath', skipping."
    }
}

# Install Grafana Enterprise (New function)
function install_grafana_enterprise {
    Write-Output "Starting Grafana Enterprise installation..."
    # Using Grafana Enterprise 10.0.0 as specified. RECOMMEND UPDATING URL FOR LATEST VERSION.
    $grafanaEnterpriseUrl = "https://dl.grafana.com/enterprise/release/grafana-enterprise-10.0.0.windows-amd64.msi"
    $installerFileName = "grafana_enterprise_installer.msi"
    $tempDir = Join-Path $env:TEMP "GrafanaInstaller" # Use a dedicated temp dir
    $installerPath = Join-Path $tempDir $installerFileName

    # Create temp directory
    try {
        if (-not (Test-Path $tempDir)) {
            New-Item -Path $tempDir -ItemType Directory -ErrorAction Stop | Out-Null
            Write-Verbose "Created temporary directory for Grafana installer: $tempDir"
        }
    }
    catch {
        Write-Error "Failed to create temporary directory for Grafana: $($_.Exception.Message)"
        throw "Setup failed during temporary directory creation for Grafana installer."
    }


    try {
        # Download the Grafana Enterprise installer
        Write-Output "Downloading Grafana Enterprise installer from $grafanaEnterpriseUrl..."
        Invoke-WebRequest -Uri $grafanaEnterpriseUrl -OutFile $installerPath -ErrorAction Stop
        Write-Output "Download complete."

        # Install Grafana Enterprise silently
        Write-Output "Running Grafana Enterprise installer (This may take a few minutes)..."
        # /i is install, /qn is fully silent (no UI), /norestart prevents reboot
        # /L*v creates a verbose log file - helpful for debugging failed installs
        Start-Process -FilePath "msiexec.exe" -ArgumentList "/i `"$installerPath`" /qn /norestart /L*v `"$tempDir\grafana_install.log`"" -NoNewWindow -Wait -ErrorAction Stop
        Write-Output "Grafana Enterprise installation complete."

        # Start the Grafana service (Installer might start it, but this ensures it)
        Write-Output "Starting Grafana service..."
        # Check if service exists before starting
        if (Get-Service -Name "grafana" -ErrorAction SilentlyContinue) {
            Start-Service -Name "grafana" -ErrorAction Stop
            Write-Output "Grafana service started successfully."
        }
        else {
            Write-Warning "Grafana service 'grafana' not found after installation. It may not have installed correctly."
            Write-Warning "Check install log at: `"$tempDir\grafana_install.log`""
        }


    }
    catch {
        Write-Error "An error occurred during Grafana Enterprise installation: $($_.Exception.Message)"
        Write-Error "Check install log at: `"$tempDir\grafana_install.log`""
        throw "Grafana Enterprise installation failed." # Re-throw the error
    }
    finally {
        # Clean up installer temp directory and files
        Write-Output "Cleaning up temporary Grafana installation files..."
        Remove-Item $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}

# --- Function to dynamically find Grafana's installation path ---
function Find-GrafanaInstallPath {
    Write-Verbose "Attempting to dynamically find Grafana installation path..."
    try {
        # Get the 'grafana' service details
        $service = Get-CimInstance -ClassName Win32_Service -Filter "Name = 'grafana'" -ErrorAction Stop
        if (-not $service) {
            throw "Grafana service 'grafana' not found."
        }

        # Use a robust regular expression to extract the executable path
        $pathRegex = '^"?([^"]+\.exe)"?'
        $executablePath = ''
        if ($service.PathName -match $pathRegex) {
            $executablePath = $matches[1]
        }
        else {
            throw "Could not parse executable path from service PathName: $($service.PathName)"
        }
        
        # The main installation directory is two levels above the executable
        # C:\Path\To\Grafana\bin\grafana-server.exe  -->  C:\Path\To\Grafana
        $installDir = Split-Path (Split-Path $executablePath)

        if ((-not $installDir) -or (-not (Test-Path $installDir))) {
            throw "Could not resolve a valid installation directory from service path: $executablePath"
        }

        Write-Verbose "Found Grafana installation directory at: $installDir"
        return $installDir
    }
    catch {
        Write-Error "Could not dynamically find Grafana installation path: $($_.Exception.Message)"
        throw "Failed to locate Grafana installation."
    }
}

# Creates a custom.ini from defaults.ini and enables embedding in it.
function Configure-GrafanaCustomIni {
    Write-Output "Creating and configuring custom.ini for Grafana..."
    
    try {
        $grafanaInstallDir = Find-GrafanaInstallPath
    }
    catch {
        throw "Cannot configure Grafana because its installation path could not be found. $($_.Exception.Message)"
    }
    
    $defaultsIniPath = Join-Path $grafanaInstallDir "grafana\conf\defaults.ini"
    $customIniPath = Join-Path $grafanaInstallDir "grafana\conf\custom.ini"

    if (-not (Test-Path $defaultsIniPath -PathType Leaf)) {
        Write-Error "Grafana configuration template 'defaults.ini' not found at '$defaultsIniPath'. Cannot create custom configuration."
        throw "'defaults.ini' not found."
    }

    try {
        Write-Output "Creating '$customIniPath' from template..."
        Copy-Item -Path $defaultsIniPath -Destination $customIniPath -Force -ErrorAction Stop

        Write-Output "Enabling iframe embedding in '$customIniPath'..."
        $configContent = Get-Content -Path $customIniPath -Encoding UTF8
        $newConfigContent = @()
        $inSecuritySection = $false
        $embeddingLineFound = $false

        foreach ($line in $configContent) {
            $trimmedLine = $line.Trim()

            # Check for section headers, ignoring comments
            if ($trimmedLine -match "^\[.*?\]$") {
                # Found a section header. Check if it's the security section.
                if ($trimmedLine -ieq "[security]") {
                    $inSecuritySection = $true
                }
                else {
                    $inSecuritySection = $false
                }
                $newConfigContent += $line # Add the section header line
                continue # Move to next line
            }

            # Check if we are in the security section and if the line is the embedding setting
            if ($inSecuritySection) {
                # If line is commented out allow_embedding, uncomment it and set to true
                if ($trimmedLine -match "^[;#]\s*allow_embedding\s*=") {
                    $newConfigContent += "allow_embedding = true"
                    $embeddingLineFound = $true
                    Write-Verbose "Uncommented and updated 'allow_embedding' line to 'true'."
                    continue # Skip adding the original line
                }
                # Look for an existing, uncommented allow_embedding line
                if ($trimmedLine -inotmatch "^[;#]") { 
                    if ($trimmedLine.StartsWith("allow_embedding", [System.StringComparison]::InvariantCultureIgnoreCase)) {
                        # Found the setting line. Replace it with 'true'.
                        $newConfigContent += "allow_embedding = true"
                        $embeddingLineFound = $true
                        Write-Verbose "Updated existing 'allow_embedding' line to 'true'."
                        continue # Skip adding the original line
                    }
                }
            }

            # If the line was not a section header or the embedding setting line, add the original line
            $newConfigContent += $line
        }

        # If the embedding line was not found within the security section (commented or uncommented), add it
        if (-not $embeddingLineFound) {
            Write-Verbose "'allow_embedding' setting not found in [security] section. Adding it."

            # Find the index of the [security] section header
            $securitySectionIndex = -1
            for ($i = 0; $i -lt $newConfigContent.Count; $i++) {
                if ($newConfigContent[$i].Trim() -ieq "[security]") {
                    $securitySectionIndex = $i
                    break
                }
            }

            if ($securitySectionIndex -ne -1) {
                # Insert the line after the [security] header
                $newConfigContent.Insert($securitySectionIndex + 1, "allow_embedding = true")
                Write-Verbose "Inserted 'allow_embedding = true' into [security] section."
            }
            else {
                # This case is highly unlikely since we copied from defaults.ini, but it's good practice.
                Write-Verbose "[security] section not found. Appending section and setting to end of file."
                if (($newConfigContent.Count -gt 0) -and ($newConfigContent[-1].Trim() -ne "")) {
                    $newConfigContent += ""
                }
                $newConfigContent += "[security]"
                $newConfigContent += "allow_embedding = true"
            }
        }

        # Write the modified content back to the custom.ini file
        $newConfigContent | Set-Content -Path $customIniPath -Encoding UTF8 -ErrorAction Stop
        Write-Output "Successfully updated '$customIniPath' to enable embedding."

    }
    catch {
        Write-Error "An error occurred while creating or modifying 'custom.ini': $($_.Exception.Message)"
        throw "Failed to configure 'custom.ini'."
    }
}

# Helper function to check if Grafana service is running and wait for it if needed
function Wait-GrafanaServiceRunning {
    param (
        [int]$TimeoutSeconds = 120, # How long to wait
        [int]$CheckIntervalSeconds = 5 # How often to check
    )
    Write-Output "Checking Grafana service status..."
    $serviceName = "grafana"
    $startTime = [System.DateTime]::Now

    while ([System.DateTime]::Now -lt $startTime.AddSeconds($TimeoutSeconds)) {
        $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
        if ($service) {
            if ($service.Status -eq "Running") {
                Write-Output "Grafana service is running."
                return $true
            }
            else {
                Write-Output "Grafana service status: $($service.Status). Waiting for it to start..."
            }
        }
        else {
            Write-Warning "Grafana service '$serviceName' not found."
            # Decide if this should be a fatal error or wait more
            throw "Grafana service '$serviceName' not found. Cannot proceed with API calls."
        }
        Start-Sleep -Seconds $CheckIntervalSeconds
    }

    # If loop finishes without the service running
    Write-Error "Grafana service did not reach 'Running' status within the timeout period."
    return $false # Service not running
}

# --- NEW: Function to check if a Grafana data source already exists
function Test-GrafanaDatasourceExists {
    param (
        [string]$GrafanaUrl,
        [string]$GrafanaUsername,
        [string]$GrafanaPassword,
        [string]$DatasourceName
    )
    Write-Verbose "Checking if Grafana data source '$DatasourceName' exists..."

    # Prepare auth
    $Credentials = "$($GrafanaUsername):$($GrafanaPassword)"
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Credentials)
    $EncodedCredentials = [System.Convert]::ToBase64String($Bytes)
    $Headers = @{ "Authorization" = "Basic $EncodedCredentials" }

    # API URL to get a data source by name
    $ApiUrl = "$GrafanaUrl/api/datasources/name/$([uri]::EscapeDataString($DatasourceName))"

    try {
        $Response = Invoke-RestMethod -Uri $ApiUrl -Method Get -Headers $Headers -ErrorAction Stop
        # If the command succeeds, a 200 OK was returned, meaning the data source exists.
        Write-Verbose "Data source '$DatasourceName' found."
        return $true
    }
    catch {
        # Check if the error is a 404 Not Found, which means it doesn't exist.
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode -eq [System.Net.HttpStatusCode]::NotFound) {
            Write-Verbose "Data source '$DatasourceName' not found (404), which is expected."
            return $false
        }
        else {
            # For any other error (e.g., can't connect, 500 error), log it and assume it doesn't exist to be safe.
            Write-Warning "An unexpected error occurred while checking for data source '$DatasourceName': $($_.Exception.Message)"
            return $false
        }
    }
}


# Function to create a Grafana data source
function Create-GrafanaDatasource {
    param (
        [string]$GrafanaUrl,
        [string]$GrafanaUsername,
        [string]$GrafanaPassword,
        [string]$DatasourceName,
        [string]$DatasourceType,
        [hashtable]$DatasourceConfig # Hashtable containing URL, Database, User, jsonData, secureJsonData, etc.
    )

    Write-Output "Creating Grafana data source '$DatasourceName' (Type: $DatasourceType)..."

    # --- Prepare Basic Auth Header ---
    $Credentials = "$($GrafanaUsername):$($GrafanaPassword)"
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Credentials)
    $EncodedCredentials = [System.Convert]::ToBase64String($Bytes)

    $Headers = @{
        "Authorization" = "Basic $EncodedCredentials"
        "Content-Type"  = "application/json"
        "Accept"        = "application/json"
    }

    # --- Construct the API Payload ---
    $DatasourcePayload = @{
        name      = $DatasourceName
        type      = $DatasourceType
        access    = $DatasourceConfig.access 
        isDefault = $DatasourceConfig.isDefault -or $false
    }
    if ($DatasourceConfig.ContainsKey('url')) { $DatasourcePayload.url = $DatasourceConfig.url }
    if ($DatasourceConfig.ContainsKey('database')) { $DatasourcePayload.database = $DatasourceConfig.database }
    if ($DatasourceConfig.ContainsKey('user')) { $DatasourcePayload.user = $DatasourceConfig.user }
    if ($DatasourceConfig.ContainsKey('jsonData')) { $DatasourcePayload.jsonData = $DatasourceConfig.jsonData }
    if ($DatasourceConfig.ContainsKey('secureJsonData')) { $DatasourcePayload.secureJsonData = $DatasourceConfig.secureJsonData }

    $PayloadJson = $DatasourcePayload | ConvertTo-Json -Depth 10

    # --- Construct API URL ---
    $ApiUrl = "$GrafanaUrl/api/datasources"

    # --- Send POST Request to create data source ---
    try {
        Write-Host "Attempting to create data source '$DatasourceName' at '$ApiUrl'..."
        $Response = Invoke-RestMethod -Uri $ApiUrl -Method Post -Headers $Headers -Body $PayloadJson -ErrorAction Stop

        Write-Output "Data source created successfully:"
        Write-Output "  ID: $($Response.id)"
        Write-Output "  UID: $($Response.datasource.uid)"
        Write-Output "  Name: $($Response.datasource.name)"
    }
    catch {
        Write-Error "Error creating data source '$DatasourceName': $($_.Exception.Message)"
        if ($_.Exception.Response) {
            $ErrorResponse = $_.Exception.Response.GetResponseStream()
            $Reader = New-Object System.IO.StreamReader($ErrorResponse)
            $ResponseBody = $Reader.ReadToEnd()
            Write-Error "Response Body: $ResponseBody"
        }
        throw "Failed to create data source '$DatasourceName'."
    }
}

# --- NEW: Function to check if a Grafana dashboard already exists by its UID
function Test-GrafanaDashboardExists {
    param (
        [string]$GrafanaUrl,
        [string]$GrafanaUsername,
        [string]$GrafanaPassword,
        [string]$DashboardJsonFilePath
    )

    Write-Verbose "Checking if dashboard from '$DashboardJsonFilePath' already exists via its UID..."

    # Step 1: Read the JSON file to extract the UID
    try {
        $DashboardJsonContent = Get-Content -Raw -Path $DashboardJsonFilePath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $DashboardUid = $DashboardJsonContent.uid
        if ([string]::IsNullOrWhiteSpace($DashboardUid)) {
            Write-Warning "Dashboard JSON file '$DashboardJsonFilePath' does not contain a UID. Cannot check for existence. Assuming it does not exist."
            return $false
        }
    }
    catch {
        Write-Warning "Could not read or parse dashboard JSON file '$DashboardJsonFilePath' to get UID. Assuming it does not exist. Error: $($_.Exception.Message)"
        return $false
    }

    # Step 2: Query the Grafana API
    $Credentials = "$($GrafanaUsername):$($GrafanaPassword)"
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Credentials)
    $EncodedCredentials = [System.Convert]::ToBase64String($Bytes)
    $Headers = @{ "Authorization" = "Basic $EncodedCredentials" }
    $ApiUrl = "$GrafanaUrl/api/dashboards/uid/$DashboardUid"

    try {
        $Response = Invoke-RestMethod -Uri $ApiUrl -Method Get -Headers $Headers -ErrorAction Stop
        Write-Verbose "Dashboard with UID '$DashboardUid' found."
        return $true
    }
    catch {
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode -eq [System.Net.HttpStatusCode]::NotFound) {
            Write-Verbose "Dashboard with UID '$DashboardUid' not found (404)."
            return $false
        }
        else {
            Write-Warning "An unexpected error occurred while checking for dashboard with UID '$DashboardUid': $($_.Exception.Message)"
            return $false
        }
    }
}

# Function to import a Grafana dashboard
function Import-GrafanaDashboard {
    param (
        [string]$GrafanaUrl,
        [string]$GrafanaUsername,
        [string]$GrafanaPassword,
        [string]$DashboardJsonFilePath,
        [string]$FolderUid = "",        # Optional: UID of the folder to import into. "" or $null for General.
        [switch]$OverwriteExisting      # Use -OverwriteExisting to set to $true
    )

    Write-Output "Importing dashboard from '$DashboardJsonFilePath'..."

    if (-not (Test-Path $DashboardJsonFilePath -PathType Leaf)) {
        Write-Error "Dashboard JSON file not found at '$DashboardJsonFilePath'. Skipping dashboard import."
        throw "Dashboard JSON file not found."
    }


    # --- Prepare Basic Auth Header ---
    $Credentials = "$($GrafanaUsername):$($GrafanaPassword)"
    $Bytes = [System.Text.Encoding]::UTF8.GetBytes($Credentials)
    $EncodedCredentials = [System.Convert]::ToBase64String($Bytes)

    $Headers = @{
        "Authorization" = "Basic $EncodedCredentials"
        "Content-Type"  = "application/json"
        "Accept"        = "application/json"
    }

    # --- Read Dashboard JSON ---
    try {
        $DashboardJsonContent = Get-Content -Raw -Path $DashboardJsonFilePath -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        Write-Verbose "Successfully read and parsed dashboard JSON."
    }
    catch {
        Write-Error "Error reading or parsing dashboard JSON file '$DashboardJsonFilePath': $($_.Exception.Message)"
        throw "Failed to read dashboard JSON file."
    }

    # --- Modify Dashboard JSON for Import ---
    $DashboardJsonContent.id = $null

    # --- Construct the API Payload ---
    $ImportPayload = @{
        dashboard = $DashboardJsonContent
        folderUid = $FolderUid
        overwrite = $OverwriteExisting.ToBool()
    }

    $PayloadJson = $ImportPayload | ConvertTo-Json -Depth 10
    Write-Verbose "Generated API payload JSON."

    # --- Construct API URL ---
    $ApiUrl = "$GrafanaUrl/api/dashboards/db"

    # --- Send POST Request to import dashboard ---
    Write-Host "Attempting to import dashboard to Grafana at '$GrafanaUrl'..."

    try {
        Start-Sleep -Seconds 5
        $Response = Invoke-RestMethod -Uri $ApiUrl -Method Post -Headers $Headers -Body $PayloadJson -ErrorAction Stop

        Write-Output "Dashboard imported successfully!"
        Write-Output "  Title: $($Response.dashboard.title)"
        Write-Output "  UID: $($Response.dashboard.uid)"
        Write-Output "  URL: $($GrafanaUrl)$($Response.url)"
    }
    catch {
        Write-Error "Error importing dashboard: $($_.Exception.Message)"
        if ($_.Exception.Response) {
            $ErrorResponse = $_.Exception.Response.GetResponseStream()
            $Reader = New-Object System.IO.StreamReader($ErrorResponse)
            $ResponseBody = $Reader.ReadToEnd()
            Write-Error "Response Body: $ResponseBody"
        }
        throw "Failed to import dashboard from '$DashboardJsonFilePath'."
    }
}


# Create shortcut in Startup folder
function create_shortcut {
    param (
        [string]$targetPath, # Path to the .bat file created above
        [string]$shortcutName = "TseApp" # Default name for the shortcut
    )

    # Get the current user's Startup folder path (runs under the user executing the script)
    $startupFolder = [System.Environment]::GetFolderPath("Startup")
    $shortcutPath = Join-Path $startupFolder "$shortcutName.lnk"

    try {
        Write-Output "Creating shortcut in Startup folder: $shortcutPath"

        # Ensure the target file (.bat) exists
        if (-not (Test-Path $targetPath -PathType Leaf)) {
            throw "Target batch file for shortcut '$targetPath' not found."
        }
        
        $workingDirectory = Split-Path -Path $targetPath

        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)

        $shortcut.TargetPath = $targetPath
        $shortcut.WorkingDirectory = $workingDirectory
        $shortcut.Description = "Start Your Flask Application and Report"
        
        $shortcut.Save()

        if (Test-Path $shortcutPath) {
            Write-Output "Shortcut created successfully in Startup folder."
            Write-Output "Shortcut target: $($shortcut.TargetPath)"
            Write-Output "Shortcut working directory: $($shortcut.WorkingDirectory)"
        }
        else {
            throw "Failed to create shortcut: $shortcutPath"
        }
    }
    catch {
        Write-Error "An error occurred while creating the shortcut: $($_.Exception.Message)"
        throw "Failed to create shortcut in Startup folder."
    }
}


# ========== MAIN EXECUTION ==========

# Get the directory the script is running from
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $scriptDirectory) {
    # Fallback if $PSScriptRoot is null
    $scriptDirectory = Get-Location -Path Content | Select-Object -ExpandProperty Path
    Write-Warning "Running script from interactive console or without a path. Using current location as script directory: $scriptDirectory"
}


# Change directory to the script directory first
Set-Location $scriptDirectory

# --- Python and Application Path Configuration Variables ---
$VenvName = "tseVenv"
$VenvPath = Join-Path $scriptDirectory $VenvName
$VenvPythonExe = Join-Path $VenvPath "Scripts\python.exe"
$requirementsFilePath = Join-Path $scriptDirectory "requirements.txt"
$SimulatorAppDirectory = "..\iot_report"

# --- Startup File Configuration ---
$startupBatFileName = "start_flask_app.bat"
$startupBatPath = Join-Path $scriptDirectory $startupBatFileName
$shortcutName = "StartTSE_Apps"

# --- Grafana Configuration Variables ---
$GrafanaUrl = "http://localhost:3000"
$GrafanaAdminUser = "admin"
$GrafanaAdminPassword = "admin" # !!! REPLACE WITH YOUR ACTUAL GRAFANA ADMIN PASSWORD

# --- MSSQL Data Source Configuration Variables ---
$MSSQLDatasourceName = "mssql-datasource"
$MSSQLDatasourceType = "mssql"
$MSSQLDatasourceConfig = @{
    url            = "localhost:2828"
    database       = "tse_data"
    user           = "tse"
    secureJsonData = @{
        password = "tse@123"
    }
    jsonData       = @{
        authenticationType = "SQL Server Authentication"
        encrypt            = "disable"
        sslmode            = "disable"
    }
    access         = "proxy"
    isDefault      = $true
}

# --- Dashboard Import Configuration Variables ---
$DashboardJsonFiles = @(
    "spinning_2.json",
    "spinning.json",
    "autoconer.json"
)
$DashboardFolderUid = ""

Write-Host "--- Starting Full Application Setup ---" -ForegroundColor Green
Write-Output "Main application directory: $scriptDirectory"

try {
    # Step 1: Check and install Python if needed
    Write-Host "`n--- Checking/Installing Python ---" -ForegroundColor Cyan
    if (command_exists "python") {
        Write-Output "Python is already installed."
    }
    else {
        install_python_windows
        if (!(command_exists "python")) {
            throw "Python command is not available after installation attempt. Cannot proceed."
        }
    }

    # Step 2: Ensure pip is installed
    Write-Host "`n--- Ensuring Pip is Installed ---" -ForegroundColor Cyan
    ensure_pip_installed
    if (!(command_exists "pip")) {
        throw "Pip command is not available after attempted installation. Cannot proceed."
    }

    # Step 3: Create Python Virtual Environment
    Write-Host "`n--- Creating Python Virtual Environment ---" -ForegroundColor Cyan
    Create-PythonVenv -VenvPath $VenvPath

    # Step 4: Install Python modules into the Virtual Environment
    Write-Host "`n--- Installing Python Modules into Venv ---" -ForegroundColor Cyan
    install_pip_modules -VenvPythonExe $VenvPythonExe
    install_requirements_file -VenvPythonExe $VenvPythonExe -requirementsFilePath $requirementsFilePath

    # Step 5: Run Python scripts (like database setup) using the Virtual Environment
    Write-Host "`n--- Running Python Scripts ---" -ForegroundColor Cyan
    run_python_scripts -VenvPythonExe $VenvPythonExe

    # Step 6: Install Grafana Enterprise
    Write-Host "`n--- Installing Grafana Enterprise ---" -ForegroundColor Cyan
    if (Get-Service -Name "grafana" -ErrorAction SilentlyContinue) {
        Write-Output "Grafana service found. Assuming Grafana is already installed. Skipping installation."
    }
    else {
        install_grafana_enterprise
    }

    # Step 7: Create custom.ini, enable embedding, and restart service
    Write-Host "`n--- Configuring Grafana Custom Settings ---" -ForegroundColor Cyan
    if (Get-Service -Name "grafana" -ErrorAction SilentlyContinue) {
        Configure-GrafanaCustomIni
         
        Write-Output "Restarting Grafana service to apply configuration changes..."
        try {
            Restart-Service -Name "grafana" -ErrorAction Stop
            Write-Output "Grafana service restarted successfully."
        }
        catch {
            Write-Error "Failed to restart Grafana service: $($_.Exception.Message)"
            Write-Warning "Please restart the 'grafana' service manually for configuration changes to take effect."
        }

    }
    else {
        Write-Warning "Grafana service 'grafana' not found. Skipping Grafana configuration and restart."
    }

    # Step 8: Wait for Grafana service to be running before attempting API calls
    Write-Host "`n--- Waiting for Grafana Service ---" -ForegroundColor Cyan
    if (Wait-GrafanaServiceRunning) {

        # Step 9: Create MSSQL Data Source in Grafana if it doesn't exist
        Write-Host "`n--- Creating Grafana Data Source ---" -ForegroundColor Cyan
        if (Test-GrafanaDatasourceExists -GrafanaUrl $GrafanaUrl -GrafanaUsername $GrafanaAdminUser -GrafanaPassword $GrafanaAdminPassword -DatasourceName $MSSQLDatasourceName) {
            Write-Output "Grafana data source '$MSSQLDatasourceName' already exists. Skipping creation."
        }
        else {
            Create-GrafanaDatasource -GrafanaUrl $GrafanaUrl -GrafanaUsername $GrafanaAdminUser -GrafanaPassword $GrafanaAdminPassword -DatasourceName $MSSQLDatasourceName -DatasourceType $MSSQLDatasourceType -DatasourceConfig $MSSQLDatasourceConfig
        }

        # Step 10: Import Dashboards into Grafana if they don't exist
        Write-Host "`n--- Importing Grafana Dashboards ---" -ForegroundColor Cyan
        foreach ($dashboardFile in $DashboardJsonFiles) {
            $fullDashboardPath = Join-Path $scriptDirectory $dashboardFile
            if (-not(Test-Path $fullDashboardPath)) {
                Write-Warning "Dashboard file '$dashboardFile' not found at '$fullDashboardPath'. Skipping."
                continue
            }

            if (Test-GrafanaDashboardExists -GrafanaUrl $GrafanaUrl -GrafanaUsername $GrafanaAdminUser -GrafanaPassword $GrafanaAdminPassword -DashboardJsonFilePath $fullDashboardPath) {
                Write-Output "Dashboard '$dashboardFile' (based on its UID) already exists. Skipping import."
            }
            else {
                try {
                    # Import without overwriting, as we've confirmed it doesn't exist
                    Import-GrafanaDashboard -GrafanaUrl $GrafanaUrl -GrafanaUsername $GrafanaAdminUser -GrafanaPassword $GrafanaAdminPassword -DashboardJsonFilePath $fullDashboardPath -FolderUid $DashboardFolderUid -OverwriteExisting:$false
                }
                catch {
                    Write-Warning "Failed to import dashboard '$dashboardFile'. Error: $($_.Exception.Message). Continuing to next dashboard..."
                }
            }
        }

    }
    else {
        Write-Error "Grafana service is not running. Skipping Grafana data source creation and dashboard import."
    }


    # Step 11: Create startup shortcut for existing batch file
    Write-Host "`n--- Setting up Application Startup ---" -ForegroundColor Cyan
    Write-Output "Using existing startup batch file: '$startupBatFileName'"
    if (-not (Test-Path $startupBatPath)) {
        throw "Startup batch file '$startupBatFileName' not found in the script directory. Please ensure it exists before running this script."
    }
    create_shortcut -targetPath $startupBatPath -shortcutName $shortcutName

    # Step 12: Launch the applications for the first time
    Write-Host "`n--- Launching Applications Now ---" -ForegroundColor Cyan
    try {
        Write-Output "Executing '$startupBatPath' to start the Flask apps..."
        Start-Process -FilePath $startupBatPath
        Write-Output "Applications have been started in separate windows."
    }
    catch {
        Write-Warning "Failed to automatically start the applications using '$startupBatPath'. Please start them manually. Error: $($_.Exception.Message)"
    }

    Write-Host "`n--- Setup Complete ---" -ForegroundColor Green
    Write-Output "All steps finished successfully."
    Write-Output "A shortcut named '$shortcutName' has been created in the Startup folder to launch all applications."

}
catch {
    Write-Error "Setup failed: $($_.Exception.Message)"
    Write-Host "--- Setup Failed ---" -ForegroundColor Red
}