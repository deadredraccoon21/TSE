<#
.SYNOPSIS
    Deletes a Grafana dashboard by its Title.
.DESCRIPTION
    1. Searches the Grafana API for a dashboard matching the exact title.
    2. Extracts the UID.
    3. Deletes the dashboard using the UID.
.EXAMPLE
    .\Delete-Dashboard.ps1 -DashboardTitle "Spinning Area 01"
#>

param (
    [Parameter(Mandatory=$true)]
    [string]$DashboardTitle
)

# --- Configuration ---
$grafanaUrl = "http://localhost:3000"
$adminUser  = "admin"
$adminPass  = "maptse"

# --- Auth Setup ---
$base64AuthInfo = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes(("${adminUser}:${adminPass}")))
$headers = @{
    "Authorization" = "Basic $base64AuthInfo"
    "Content-Type"  = "application/json"
}

# --- Step 1: Search for the Dashboard UID ---
Write-Host "Searching for dashboard with title: '$DashboardTitle'..." -ForegroundColor Cyan

# Encode title for URL (handle spaces, etc.)
$encodedTitle = [Uri]::EscapeDataString($DashboardTitle)

try {
    # Search API: type=dash-db ensures we only look for dashboards, not folders
    $searchUri = "$grafanaUrl/api/search?query=$encodedTitle&type=dash-db"
    $searchResults = Invoke-RestMethod -Uri $searchUri -Method Get -Headers $headers
}
catch {
    Write-Error "Failed to contact Grafana Search API."
    Write-Host $_.Exception.Message
    exit
}

# Filter results for an EXACT match on title
# (The API query is a "contains" search, so "Spin" might return "Spinning 1" and "Spinning 2")
$targetDashboard = $searchResults | Where-Object { $_.title -eq $DashboardTitle } | Select-Object -First 1

if (-not $targetDashboard) {
    Write-Error "Dashboard with exact title '$DashboardTitle' not found."
    Write-Host "Partial matches found: $($searchResults.title -join ', ')" -ForegroundColor Gray
    exit
}

$uidToDelete = $targetDashboard.uid
Write-Host "Found Dashboard. UID: $uidToDelete" -ForegroundColor Yellow

# --- Step 2: Delete the Dashboard ---
Write-Host "Attempting to delete..." -NoNewline

try {
    $deleteUri = "$grafanaUrl/api/dashboards/uid/$uidToDelete"
    $response = Invoke-RestMethod -Uri $deleteUri `
                                  -Method Delete `
                                  -Headers $headers
    
    Write-Host " Done." -ForegroundColor Green
    Write-Host "------------------------------------------------"
    Write-Host "Success: $($response.message)" -ForegroundColor Green
    Write-Host "Title:   $($response.title)"
    Write-Host "------------------------------------------------"
}
catch {
    Write-Host " Failed." -ForegroundColor Red
    Write-Error "Could not delete dashboard."
    if ($_.Exception.Response) {
        $stream = $_.Exception.Response.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        Write-Error $reader.ReadToEnd()
    }
}