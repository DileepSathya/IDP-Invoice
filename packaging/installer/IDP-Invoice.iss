#define AppName "IDP Invoice"
#define AppVersion GetEnv("IDP_INVOICE_VERSION")
#if AppVersion == ""
  #define AppVersion "1.0.0"
#endif
#define Publisher "IDP Invoice"
#define DistRoot "..\..\dist\IDP-Invoice"

[Setup]
AppId={{B676645E-A91F-4DE0-8C86-E93C11DE9F02}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#Publisher}
DefaultDirName={localappdata}\Programs\IDP Invoice
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\..\dist\installer
OutputBaseFilename=IDP-Invoice-Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\Start IDP Invoice.exe
CloseApplications=yes
RestartApplications=no

[Files]
Source: "{#DistRoot}\*"; DestDir: "{app}"; Excludes: "\.env,\license.lic,\invoice_count.enc,\license_state.json,\data\*,\invoices_data\*,\logs\*,\tally-bridge\.env"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#DistRoot}\.env.example"; DestDir: "{app}"; DestName: ".env"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "{#DistRoot}\tally-bridge\.env.example"; DestDir: "{app}\tally-bridge"; DestName: ".env"; Flags: onlyifdoesntexist uninsneveruninstall skipifsourcedoesntexist
Source: "{#DistRoot}\license.lic"; DestDir: "{app}"; Flags: onlyifdoesntexist uninsneveruninstall skipifsourcedoesntexist

[InstallDelete]
Type: filesandordirs; Name: "{app}\idp-services"
Type: filesandordirs; Name: "{app}\idp-api"
Type: filesandordirs; Name: "{app}\idp-watcher"
Type: filesandordirs; Name: "{app}\frontend"
Type: filesandordirs; Name: "{app}\mongodb\bin"
Type: filesandordirs; Name: "{app}\tally-bridge\_internal"
Type: filesandordirs; Name: "{app}\tally-bridge\xml_scripts"
Type: files; Name: "{app}\tally-bridge\tally-bridge.exe"
Type: files; Name: "{app}\tally-bridge\sync_env.py"

[Dirs]
Name: "{app}\data\db"; Flags: uninsneveruninstall
Name: "{app}\invoices_data"; Flags: uninsneveruninstall
Name: "{app}\invoices_data\to_be_processed"; Flags: uninsneveruninstall
Name: "{app}\invoices_data\_api_staging"; Flags: uninsneveruninstall
Name: "{app}\invoices_data\HITL_pending"; Flags: uninsneveruninstall
Name: "{app}\invoices_data\gemini_api_error"; Flags: uninsneveruninstall
Name: "{app}\invoices_data\ERROR"; Flags: uninsneveruninstall
Name: "{app}\invoices_data\Completed"; Flags: uninsneveruninstall
Name: "{app}\invoices_data\merged_sources"; Flags: uninsneveruninstall
Name: "{app}\logs"; Flags: uninsneveruninstall

[Icons]
Name: "{autoprograms}\IDP Invoice"; Filename: "{app}\Start IDP Invoice.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\IDP Invoice"; Filename: "{app}\Start IDP Invoice.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: checkedonce

[Run]
Filename: "{app}\mongodb\bin\vc_redist.x64.exe"; Parameters: "/install /quiet /norestart"; StatusMsg: "Installing Microsoft Visual C++ runtime..."; Flags: waituntilterminated skipifdoesntexist; Check: VCRedistNeedsInstall
Filename: "{app}\Start IDP Invoice.exe"; Description: "Launch IDP Invoice"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
function VCRedistNeedsInstall: Boolean;
var
  Installed: Cardinal;
begin
  Installed := 0;
  Result := not RegQueryDWordValue(
    HKLM64,
    'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64',
    'Installed',
    Installed
  ) or (Installed <> 1);
end;
