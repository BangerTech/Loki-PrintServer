; Loki-PrintServer Windows Installer (Inno Setup)
; NOTE: All paths are relative to the location of this .iss file (client/build/)
#define MyAppName "Loki-Client"
#define MyAppVersion "1.4.5"
#define MyAppPublisher "BangerTECH"
#define MyAppURL "https://github.com/BangerTech/Loki-PrintServer"
#define MyAppExeName "LokiClient.exe"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EF1234567890}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
OutputDir=..\dist
OutputBaseFilename=LokiClient-Setup
SetupIconFile=..\assets\icon.ico
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Admin is required to install the bundled usbip-win VHCI driver.
; {autodesktop}/{autopf} then resolve to all-users locations.
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "german"; MessagesFile: "compiler:Languages\German.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startupitem"; Description: "Start Loki-PrintServer at login"; GroupDescription: "Auto-start:"

[Files]
Source: "..\dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\windows\usbip-win\*"; DestDir: "{app}\usbip-win"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
  ValueType: string; ValueName: "{#MyAppName}"; \
  ValueData: """{app}\{#MyAppExeName}"""; Flags: uninsdeletevalue; \
  Tasks: startupitem

[Run]
; WorkingDir must be the driver folder so usbip.exe finds the .inf/.sys/.cat files.
Filename: "{app}\usbip-win\usbip.exe"; Parameters: "install"; \
  WorkingDir: "{app}\usbip-win"; \
  StatusMsg: "Installing USB/IP driver…"; \
  Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
