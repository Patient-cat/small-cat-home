; SafeSight v2.0 Windows Installer — Inno Setup 6.x
; Compile with: iscc installer.iss  (or use Inno Setup Compiler GUI)

[Setup]
AppName=SafeSight
AppVersion=2.0.0
AppPublisher=广东理工学院
AppPublisherURL=https://github.com/Patient-cat/small-cat-home
DefaultDirName={autopf}\SafeSight
DefaultGroupName=SafeSight
DisableProgramGroupPage=yes
OutputDir=dist\installer
OutputBaseFilename=SafeSight_Setup_v2.0.0
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

; Icon
SetupIconFile=static\logo.png
UninstallDisplayIcon={app}\safesight.exe

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
; Main application (PyInstaller output)
Source: "dist\safesight\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

; Model files (fallback if not bundled by PyInstaller)
Source: "yolov8m-pose.pt"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist
Source: "fall_detect.pt"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

; Config template (only if not exists)
Source: "cameras.example.json"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

; .env template
Source: ".env.example"; DestDir: "{app}"; Flags: ignoreversion onlyifdoesntexist

; Create data directories
[Dirs]
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\static\falls"; Permissions: users-modify
Name: "{app}\static\uploads"; Permissions: users-modify

[Icons]
Name: "{group}\SafeSight"; Filename: "{app}\safesight.exe"; WorkingDir: "{app}"
Name: "{group}\卸载 SafeSight"; Filename: "{uninstallexe}"
Name: "{commondesktop}\SafeSight"; Filename: "{app}\safesight.exe"; WorkingDir: "{app}"

[Run]
Filename: "{app}\safesight.exe"; Description: "启动 SafeSight 跌倒监测系统"; Flags: postinstall nowait skipifsilent shellexec

[UninstallRun]
; Kill the process if still running
Filename: "taskkill"; Parameters: "/f /im safesight.exe"; Flags: runhidden skipifdoesntexist

[Code]
function InitializeSetup: Boolean;
begin
  Result := True;
end;
