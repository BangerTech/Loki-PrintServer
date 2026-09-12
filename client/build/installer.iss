; Loki-PrintServer Windows Installer (Inno Setup)
; NOTE: All paths are relative to the location of this .iss file (client/build/)
#define MyAppName "Loki-Client"
#define MyAppVersion "1.4.11"
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
; 64-bit so {sys} is the real System32 (bcdedit.exe is 64-bit only).
; A 32-bit Setup would look in SysWOW64 and fail with CreateProcess code 2.
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
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

[Code]
function BcdEditPath(): String;
begin
  // Brace comments are illegal here: Inno treats {sys} as a constant.
  // 32-bit Setup on 64-bit Windows: sys is SysWOW64; bcdedit lives in System32.
  if IsWin64 and not Is64BitInstallMode then
    Result := ExpandConstant('{sysnative}\bcdedit.exe')
  else
    Result := ExpandConstant('{sys}\bcdedit.exe');
end;

function NeedRestart(): Boolean;
begin
  Result := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    // Test-signing is required for usbip-win 0.3.5. Never abort Setup if this fails.
    Exec(BcdEditPath(), '/set testsigning on', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    MsgBox(
      'Loki-Client installed.' + #13#10 + #13#10 +
      'The USB/IP driver needs Windows Test-Signing mode.' + #13#10 +
      'Please RESTART Windows now so the plotter can be attached.' + #13#10 + #13#10 +
      'Note: if the plotter still is not detected after reboot, Secure Boot must be ' +
      'turned OFF in your UEFI/BIOS for test-signed drivers to load.',
      mbInformation, MB_OK);
  end;
end;
