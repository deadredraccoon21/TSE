# =================================================================================
# Create-HtmlFiles.ps1
#
# DESCRIPTION:
# This script generates multiple HTML files based on a single template.
# It reads 'H-Plant-1.html', replaces the node prefix (e.g., 'Spg1') with
# a new prefix, updates the department title, and saves the result as a new
# file according to the mapping defined in the $departments array.
#
# USAGE:
# 1. Save this script as 'Create-HtmlFiles.ps1'.
# 2. Place it in the same directory as your 'H-Plant-1.html' file.
# 3. Open PowerShell in that directory.
# 4. Run the script by typing: .\Create-HtmlFiles.ps1
#
# NOTE:
# If you get an error about script execution, run this command first:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
# =================================================================================

# --- Configuration ---

# The source template file. Must be in the same directory as the script.
$sourceFile = @(
    "H-Plant-21_.html"
)

# The original node prefix found in the source file that needs to be replaced.
$originalPrefix =  @("Prep","Prep1","Prep2","Spg", "Spg1", "spg2", "spg3", "TFO", "Wdg", "OE", "Lconer", "Aconer", "BR", "Card", "Comber", "Wvg", "Wvg1", "Wvg2", "Warp", "WvgL", "WvgL1", "WvgL2", "WvgL3", "Pcnl", "Knit", "SpgU2", "Spg1U2", "Spg2U2")

# --- Data Mapping (Transcribed from your image) ---
# Each object represents one new HTML file to be created.
$departments = @(
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Prep'; TemplateName = 'H-Plant-21_Prep' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Prep1'; TemplateName = 'H-Plant-21_Prep1' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Prep2'; TemplateName = 'H-Plant-21_Prep2' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Spg'; TemplateName = 'H-Plant-21_Spg' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Spg1'; TemplateName = 'H-Plant-21_Spg1' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Spg2'; TemplateName = 'H-Plant-21_Spg2' }, # Note: Following image's template name exactly
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Spg3'; TemplateName = 'H-Plant-21_Spg3' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Wdg'; TemplateName = 'H-Plant-21_Wdg' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'OE'; TemplateName = 'H-Plant-21_OE' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'TFO'; TemplateName = 'H-Plant-21_TFO' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Lconer'; TemplateName = 'H-Plant-21_Lconer' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Aconer'; TemplateName = 'H-Plant-21_Aconer' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Wvg'; TemplateName = 'H-Plant-21_Wvg' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Wvg1'; TemplateName = 'H-Plant-21_Wvg1' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Wvg2'; TemplateName = 'H-Plant-21_Wvg2' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Warp'; TemplateName = 'H-Plant-21_Warp' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'BR'; TemplateName = 'H-Plant-21_BR' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Card'; TemplateName = 'H-Plant-21_Card' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Knit'; TemplateName = 'H-Plant-21_Knit' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'SpgU2'; TemplateName = 'H-Plant-21_SpgU2' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Spg1U2'; TemplateName = 'H-Plant-21_Spg1U2' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Spg2U2'; TemplateName = 'H-Plant-21_Spg2U2' },
    [pscustomobject]@{ DepartmentName = '{{ dept_name }}'; ShortName = 'Pcnl'; TemplateName = 'H-Plant-21_Pcnl' }

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