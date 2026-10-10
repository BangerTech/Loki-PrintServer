; Loki-PrintServer Windows Installer (Inno Setup)
; NOTE: All paths are relative to the location of this .iss file (client/build/)
#define MyAppName "Loki-Client"
#define MyAppVersion "1.5.2"
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
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
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
Source: "..\windows\usbip-win\USBip-Setup.exe"; DestDir: "{app}\usbip-win"; Flags: ignoreversion
Source: "install-usbip-silent.cmd"; DestDir: "{app}\usbip-win"; Flags: ignoreversion

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
; USBip is also Inno Setup — cannot run it while this Setup holds the mutex.
; Helper waits for LokiClient-Setup.exe to exit, then installs the driver silently.
Filename: "{cmd}"; \
  Parameters: "/c start """" /b ""{app}\usbip-win\install-usbip-silent.cmd"""; \
  WorkingDir: "{app}\usbip-win"; \
  StatusMsg: "Preparing USB/IP driver…"; \
  Flags: runhidden nowait
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    MsgBox(
      'Loki-Client is installed.' + #13#10 + #13#10 +
      'The USB/IP driver installs automatically after you click Finish ' +
      '(no second wizard).' + #13#10 + #13#10 +
      'When the small confirmation appears, restart Windows once.',
      mbInformation, MB_OK);
end;
