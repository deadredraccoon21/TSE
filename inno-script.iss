; Script generated for Inno Setup
[Setup]
AppName=TSE-Scada
AppVersion=1.0
DefaultDirName={commonpf}\TSE-Scada
DefaultGroupName=TSE Scada
; Require Admin rights
PrivilegesRequired=admin
OutputDir=.
OutputBaseFilename=TSE-Scada_Setup
Compression=lzma
SolidCompression=yes
; Ensure the uninstaller is created
Uninstallable=yes

[Files]
; 1. Application Files
Source: "dist\TSEScada\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; 2. NSSM (Service Manager)
Source: "install_files\nssm.exe"; DestDir: "{app}"; Flags: ignoreversion

; 3. Grafana Installer
Source: "install_files\grafana.msi"; DestDir: "{tmp}"; Flags: deleteafterinstall

; 4. Custom Grafana Config (Server & Auth settings)
Source: "grafana_custom.ini"; DestDir: "{tmp}"; Flags: deleteafterinstall

; 5. Grafana Data Source (MSSQL Auto-setup)
Source: "grafana_datasource.yaml"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Dirs]
; --- HIDE FOLDER & FIX PERMISSIONS ---
; 'Permissions: users-modify' allows you to edit files (like templates) later without Admin prompts
Name: "{app}"; Attribs: hidden system; Permissions: users-modify
; Log folder (Required for NSSM logging)
Name: "{app}\logs"; Permissions: users-modify

[Code]
var
  InputPage: TInputQueryWizardPage;

// --- CHECK IF GRAFANA IS INSTALLED ---
function IsGrafanaInstalled: Boolean;
begin
  Result := FileExists(ExpandConstant('{commonpf}\GrafanaLabs\grafana\bin\grafana-server.exe'));
end;

procedure InitializeWizard;
begin
  InputPage := CreateInputQueryPage(wpWelcome,
    'Configuration Settings', 'Please enter the site-specific details',
    'These settings will configure the database and OPC UA connection.');
    
  InputPage.Add('MSSQL Server Name (e.g., LOCALHOST\SQLEXPRESS):', False);
  InputPage.Add('OPC UA Endpoint URL:', False);
  
  InputPage.Values[0] := 'LOCALHOST\SQLEXPRESS';
  InputPage.Values[1] := 'opc.tcp://127.0.0.1:4840';
end;

function GetSqlServer(Param: String): String;
begin
  Result := InputPage.Values[0];
end;

function GetOpcUrl(Param: String): String;
begin
  Result := InputPage.Values[1];
end;

[Run]
; 1. Install Grafana (ONLY IF NOT INSTALLED)
Filename: "msiexec.exe"; Parameters: "/i ""{tmp}\grafana.msi"" /qn"; StatusMsg: "Installing Grafana..."; Flags: runhidden; Check: not IsGrafanaInstalled

; 2. Configure Grafana (Copy custom.ini)
Filename: "cmd.exe"; Parameters: "/c copy /Y ""{tmp}\grafana_custom.ini"" ""C:\Program Files\GrafanaLabs\grafana\conf\custom.ini"""; StatusMsg: "Configuring Grafana Settings..."; Flags: runhidden

; 3. Provision Data Source (Copy datasource.yaml to provisioning folder)
Filename: "cmd.exe"; Parameters: "/c copy /Y ""{tmp}\grafana_datasource.yaml"" ""C:\Program Files\GrafanaLabs\grafana\conf\provisioning\datasources\mssql.yaml"""; StatusMsg: "Provisioning Database Connection..."; Flags: runhidden

; 4. Firewall Rules (Open Ports 3000 & 7005)
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -Command ""New-NetFirewallRule -DisplayName 'Grafana 3000' -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow"""; StatusMsg: "Opening Firewall Port 3000..."; Flags: runhidden
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -Command ""New-NetFirewallRule -DisplayName 'TSE Scada 7005' -Direction Inbound -Protocol TCP -LocalPort 7005 -Action Allow"""; StatusMsg: "Opening Firewall Port 7005..."; Flags: runhidden

; 5. Restart Grafana Service (To load new configs)
Filename: "net.exe"; Parameters: "stop Grafana"; Flags: runhidden
Filename: "net.exe"; Parameters: "start Grafana"; Flags: runhidden

; 6. Run App Configuration
Filename: "{app}\TSEScada.exe"; Parameters: "--install-config ""{code:GetSqlServer}"" ""{code:GetOpcUrl}"""; StatusMsg: "Initializing Database and Settings..."; Flags: waituntilterminated runhidden

; 7. SETUP BACKGROUND SERVICE (NSSM)
; Stop existing service if re-installing
Filename: "{app}\nssm.exe"; Parameters: "stop Tse-Scada"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove Tse-Scada confirm"; Flags: runhidden

; Install Service
Filename: "{app}\nssm.exe"; Parameters: "install Tse-Scada ""{app}\TSEScada.exe"""; Flags: runhidden; StatusMsg: "Registering Service..."
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada Description ""IoT Flask Application Backend"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppDirectory ""{app}"""; Flags: runhidden

; --- LOGGING CONFIGURATION (10 MB Limit) ---
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppStdout ""{app}\logs\service.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppStderr ""{app}\logs\service.log"""; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppRotateFiles 1"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppRotateBytes 10485760"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppRotateOnline 1"; Flags: runhidden
; -------------------------------------------

; Auto-Start and Restart Logic
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada Start SERVICE_AUTO_START"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppExit Default Restart"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "set Tse-Scada AppThrottle 1500"; Flags: runhidden

; Start Service
Filename: "{app}\nssm.exe"; Parameters: "start Tse-Scada"; Flags: runhidden; StatusMsg: "Starting Service..."

[UninstallRun]
; 1. Stop and Remove Service
Filename: "{app}\nssm.exe"; Parameters: "stop Tse-Scada"; Flags: runhidden
Filename: "{app}\nssm.exe"; Parameters: "remove Tse-Scada confirm"; Flags: runhidden
; 2. Remove Firewall Rules
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -Command ""Remove-NetFirewallRule -DisplayName 'Grafana 3000' -ErrorAction SilentlyContinue"""; Flags: runhidden
Filename: "powershell.exe"; Parameters: "-ExecutionPolicy Bypass -Command ""Remove-NetFirewallRule -DisplayName 'TSE Scada 7005' -ErrorAction SilentlyContinue"""; Flags: runhidden