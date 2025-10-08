# =================================================================================
# Create-HtmlFiles.ps1
#
# DESCRIPTION:
# This script generates multiple HTML files based on a single template.
# It reads 'h-plant-1.html', replaces the node prefix (e.g., 'Spg1') with
# a new prefix, updates the department title, and saves the result as a new
# file according to the mapping defined in the $departments array.
#
# USAGE:
# 1. Save this script as 'Create-HtmlFiles.ps1'.
# 2. Place it in the same directory as your 'h-plant-1.html' file.
# 3. Open PowerShell in that directory.
# 4. Run the script by typing: .\Create-HtmlFiles.ps1
#
# NOTE:
# If you get an error about script execution, run this command first:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
# =================================================================================

# --- Configuration ---

# The source template file. Must be in the same directory as the script.
$sourceFile = "h-plant-1.html"

# The original node prefix found in the source file that needs to be replaced.
$originalPrefix = "Spg1"

# --- Data Mapping (Transcribed from your image) ---
# Each object represents one new HTML file to be created.
$departments = @(
    [pscustomobject]@{ DepartmentName = 'Preparatory'; ShortName = 'Prep'; TemplateName = 'H-Plant-1_Prep' },
    [pscustomobject]@{ DepartmentName = 'Preparatory1'; ShortName = 'Prep1'; TemplateName = 'H-Plant-1_Prep1' },
    [pscustomobject]@{ DepartmentName = 'Preparatory2'; ShortName = 'Prep2'; TemplateName = 'H-Plant-1_Prep2' },
    [pscustomobject]@{ DepartmentName = 'Spinning'; ShortName = 'Spg'; TemplateName = 'H-Plant-1_Spg' },
    [pscustomobject]@{ DepartmentName = 'Spinning1'; ShortName = 'Spg1'; TemplateName = 'H-Plant-1_Spg1' },
    [pscustomobject]@{ DepartmentName = 'Spinning2'; ShortName = 'Spg2'; TemplateName = 'H-Plant-1_Sp2' }, # Note: Following image's template name exactly
    [pscustomobject]@{ DepartmentName = 'Spinning3'; ShortName = 'Spg3'; TemplateName = 'H-Plant-1_Spg3' },
    [pscustomobject]@{ DepartmentName = 'Winding'; ShortName = 'Wdg'; TemplateName = 'H-Plant-1_Wdg' },
    [pscustomobject]@{ DepartmentName = 'Open End'; ShortName = 'OE'; TemplateName = 'H-Plant-1_OE' },
    [pscustomobject]@{ DepartmentName = 'TFO'; ShortName = 'TFO'; TemplateName = 'H-Plant-1_TFO' },
    [pscustomobject]@{ DepartmentName = 'LinkConer'; ShortName = 'Lconer'; TemplateName = 'H-Plant-1_Lconer' },
    [pscustomobject]@{ DepartmentName = 'AutoConer'; ShortName = 'Aconer'; TemplateName = 'H-Plant-1_Aconer' }
)

# --- Script Logic ---

# Check if the source file exists before starting
if (-not (Test-Path $sourceFile)) {
    Write-Error "Error: Source file '$sourceFile' not found. Please make sure it's in the same directory as the script."
    # Pause the script to allow user to read the error in case the window closes.
    Read-Host "Press Enter to exit"
    return
}

# Read the entire content of the template file into a single string for faster processing
Write-Host "Reading template file: $sourceFile"
$templateContent = Get-Content -Path $sourceFile -Raw

# Loop through each department entry defined above
foreach ($dept in $departments) {
    $newPrefix = $dept.ShortName
    $newFileName = "$($dept.TemplateName).html"
    $newDepartmentName = $dept.DepartmentName

    Write-Host "-> Generating '$newFileName' | Replacing '$originalPrefix' with '$newPrefix'..."

    # Perform the replacement for the node prefix
    $newContent = $templateContent -replace $originalPrefix, $newPrefix

    # BONUS: Also replace the department name title for a fully complete template
    # This finds `<b><u>{{ dept_name }}</u></b>` and replaces the placeholder.
    $newContent = $newContent -replace '{{ dept_name }}', $newDepartmentName

    # Save the modified content to the new file, using UTF-8 encoding for web compatibility
    try {
        Set-Content -Path $newFileName -Value $newContent -Encoding UTF8 -ErrorAction Stop
    }
    catch {
        Write-Error "Failed to write file '$newFileName'. Error: $_"
    }
}

Write-Host ""
Write-Host "Script finished. All files have been generated successfully." -ForegroundColor Green