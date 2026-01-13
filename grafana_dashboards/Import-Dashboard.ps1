<#
.SYNOPSIS
    Imports a local JSON dashboard file into Grafana with a specific title.
.EXAMPLE
    .\Import-Dashboard.ps1 -DashboardName "spg1" -NewTitle "Production Spinning 1"
#>

param (
    [Parameter(Mandatory=$true)]
    [string]$DashboardName, # Input: Filename without .json (e.g., "spg1")

    [Parameter(Mandatory=$true)]
    [string]$NewTitle       # Input: The new title to set in the dashboard
)

# --- Configuration ---
$grafanaUrl = "http://localhost:3000"
$adminUser  = "admin"
$adminPass  = "maptse"

# --- Construct File Path ---
# Assumes the file is in the same folder as the script. 
# Change path if needed.
$dashboardFilePath = "$PSScriptRoot\$DashboardName.json"

Write-Host "Processing file: $dashboardFilePath" -ForegroundColor Cyan

# --- Process ---

# 1. Read the local JSON file
if (-not (Test-Path $dashboardFilePath)) {
    Write-Error "File '$dashboardFilePath' not found."
    exit
}

try {
    $dashboardContent = Get-Content -Path $dashboardFilePath -Raw | ConvertFrom-Json
}
catch {
    Write-Error "Could not parse the dashboard JSON file."
    exit
}

# 2. Modify Dashboard Data
# Update the Title based on script input
Write-Host "Changing Title from '$($dashboardContent.title)' to '$NewTitle'" -ForegroundColor Yellow
$dashboardContent.title = $NewTitle

# Nullify ID so Grafana treats this as an import/update correctly
$dashboardContent.id = $null

# NOTE: If you want to create a COPY (not update the existing one), 
# uncomment the line below to remove the UID.
# $dashboardContent.uid = $null 

# 3. Create the API Payload structure
$payloadObject = @{
    dashboard = $dashboardContent
    overwrite = $true
    folderId  = 0      
    message   = "Imported via PowerShell - Title Updated"
}

# 4. Convert Payload back to JSON (Depth 100 is critical)
$jsonPayload = $payloadObject | ConvertTo-Json -Depth 100

# 5. Define Headers (Basic Auth)
$base64AuthInfo = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(("${adminUser}:${adminPass}")))
$headers = @{
    "Authorization" = "Basic $base64AuthInfo"
    "Content-Type"  = "application/json"
}

# 6. Send Request
try {
    $response = Invoke-RestMethod -Uri "$grafanaUrl/api/dashboards/db" `
                                  -Method Post `
                                  -Headers $headers `
                                  -Body $jsonPayload
    
    Write-Host "------------------------------------------------"
    Write-Host "Dashboard Imported Successfully!" -ForegroundColor Green
    Write-Host "Title: $NewTitle"
    Write-Host "URL:   $($grafanaUrl)$($response.url)"
    Write-Host "------------------------------------------------"
}
catch {
    Write-Error "Failed to import dashboard."
    # Attempt to read specific error message from Grafana
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        Write-Error $reader.ReadToEnd()
    }
}