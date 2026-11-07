; PubHorror - Inno Setup script
; Works from either the project root or "source code\". Run build.bat first so dist\PubHorror\ exists.
; Output: PubHorror_Setup_<version>.exe in the project root.

#define MyAppName      "PubHorror"
#define MyAppVersion   "1.0.5"
#define MyAppExeName   "PubHorror.exe"

; Paths in Inno are relative to this script. Detect where it was placed.
#ifexist "source code\dist\PubHorror\PubHorror.exe"
  #define Src  "source code\"
  #define Root ""
#else
  #define Src  ""
  #define Root "..\"
#endif

[Setup]
AppId={{B7A3F2C1-5D4E-4F6A-9C21-0A1B2C3D4E5F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
; Per-user install: the app writes data\, library\ and posters\ next to the exe,
; so it must live somewhere the user can write (not Program Files).
PrivilegesRequired=lowest
OutputDir={#Root}.
OutputBaseFilename=PubHorror_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyAppExeName}
#ifexist Src + "app\assets\PubHorrorIcon.ico"
SetupIconFile={#Src}app\assets\PubHorrorIcon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"

[Dirs]
; Created empty; never overwritten or removed, so user data survives upgrades/uninstall.
Name: "{app}\data";    Flags: uninsneveruninstall
Name: "{app}\library"; Flags: uninsneveruninstall
Name: "{app}\posters"; Flags: uninsneveruninstall

[Files]
Source: "{#Src}dist\PubHorror\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; WebView2 bootstrapper, only extracted if the runtime is missing
Source: "{#Src}MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not WebView2Installed
; MakeMKV installer (disc ripping), only extracted if MakeMKV is missing. Put the official
; installer next to this script and name it MakeMKV_setup.exe.
Source: "{#Src}MakeMKV_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not MakeMKVInstalled

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft WebView2 runtime..."; Flags: waituntilterminated; Check: not WebView2Installed
; MakeMKV installs to Program Files, so its installer needs admin. shellexec lets Windows show the
; UAC prompt even though PubHorror itself installs per-user.
Filename: "{tmp}\MakeMKV_setup.exe"; Parameters: "/S"; StatusMsg: "Installing MakeMKV (needed for playing discs)..."; Flags: shellexec waituntilterminated; Check: not MakeMKVInstalled
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[Code]
function WebView2Installed: Boolean;
var
  v: String;
begin
  Result :=
    RegQueryStringValue(HKLM32, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) or
    RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v) or
    RegQueryStringValue(HKCU,   'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', v);
  if Result then
    Result := (v <> '') and (v <> '0.0.0.0');
end;

{ Same locations main.py checks for makemkvcon }
function MakeMKVInstalled: Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{commonpf64}\MakeMKV\makemkvcon64.exe')) or
    FileExists(ExpandConstant('{commonpf32}\MakeMKV\makemkvcon64.exe')) or
    FileExists(ExpandConstant('{commonpf64}\MakeMKV\makemkvcon.exe')) or
    FileExists(ExpandConstant('{commonpf32}\MakeMKV\makemkvcon.exe'));
end;
