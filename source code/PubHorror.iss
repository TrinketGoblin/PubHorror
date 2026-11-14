; PubHorror - Inno Setup script
; Works from either the project root or "source code\". Run build.bat first so dist\PubHorror\ exists.
; Output: PubHorror_Setup_<version>.exe in the project root.

#define MyAppName      "PubHorror"
#define MyAppVersion   "1.0.8"
#define MyAppExeName   "PubHorror.exe"

; Paths in Inno are relative to this script. Detect where it was placed.
#ifexist "source code\dist\PubHorror\PubHorror.exe"
  #define Src  "source code\"
  #define Root ""
#else
  #define Src  ""
  #define Root "..\"
#endif
; The built app folder. Override for a test build:  ISCC /DDistDir=C:\somewhere\dist\PubHorror /O<outdir> PubHorror.iss
#ifndef DistDir
  #define DistDir Src + "dist\PubHorror"
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
; If PubHorror (or its sharing component, sidecar\node.exe) is running during an upgrade, ask to close it.
CloseApplications=yes
#ifexist Src + "app\assets\PubHorrorIcon.ico"
SetupIconFile={#Src}app\assets\PubHorrorIcon.ico
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional icons:"
; Only matters for the optional "Share my library" / Watch Party feature. Without it, Windows asks the first
; time sharing is turned on. Needs one admin approval; limited to PRIVATE networks (home Wi-Fi), never public.
Name: "firewall"; Description: "Allow PubHorror's library sharing on private (home) networks in Windows Firewall (asks for admin approval)"; GroupDescription: "Sharing / Watch Party (optional):"; Flags: unchecked

[Dirs]
; Created empty; never overwritten or removed, so user data survives upgrades/uninstall.
Name: "{app}\data";    Flags: uninsneveruninstall
Name: "{app}\library"; Flags: uninsneveruninstall
Name: "{app}\posters"; Flags: uninsneveruninstall

[Files]
; Includes sidecar\ (node.exe + the sharing server) and licenses\node-LICENSE.txt, added by build_tools\prepare_sidecar.ps1
Source: "{#DistDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
; WebView2 bootstrapper, only extracted if the runtime is missing
Source: "{#Src}MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Check: not WebView2Installed
; MakeMKV is NOT bundled. It decrypts discs, so the user downloads it from makemkv.com
; themselves. The installer only offers to open the download page (see [Run]).
; ffmpeg + ffprobe, installed next to PubHorror.exe where the app looks for them first.
; Needed for disc playback, audio conversion, subtitle extraction and clip export.
; Put ffmpeg.exe and ffprobe.exe in an "ffmpeg" folder next to this script, with the build's LICENSE.
Source: "{#Src}ffmpeg\ffmpeg.exe";  DestDir: "{app}"; Flags: ignoreversion
Source: "{#Src}ffmpeg\ffprobe.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#Src}ffmpeg\LICENSE.txt"; DestDir: "{app}\licenses"; DestName: "ffmpeg-LICENSE.txt"; Flags: ignoreversion
Source: "{#Src}THIRD_PARTY_NOTICES.txt"; DestDir: "{app}\licenses"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Installing Microsoft WebView2 runtime..."; Flags: waituntilterminated; Check: not WebView2Installed
; Offer (unchecked by default) to open the official MakeMKV download page if it isn't installed.
Filename: "https://www.makemkv.com/download/"; Description: "Open the MakeMKV download page (only needed to play discs)"; Flags: shellexec nowait postinstall skipifsilent unchecked; Check: not MakeMKVInstalled
; Firewall rule for the sharing component: inbound, PRIVATE networks only, only for {app}\sidecar\node.exe,
; only the sharing ports (TCP 47420-47430) and LAN discovery (UDP 5353). One elevated cmd = one UAC prompt.
Filename: "{sys}\cmd.exe"; Parameters: "/c netsh advfirewall firewall delete rule name=""PubHorror sharing"" >nul & netsh advfirewall firewall add rule name=""PubHorror sharing"" dir=in action=allow program=""{app}\sidecar\node.exe"" profile=private protocol=TCP localport=47420-47430 enable=yes & netsh advfirewall firewall add rule name=""PubHorror sharing"" dir=in action=allow program=""{app}\sidecar\node.exe"" profile=private protocol=UDP localport=5353 enable=yes"; Verb: "runas"; Flags: shellexec runhidden waituntilterminated; StatusMsg: "Adding the Windows Firewall rule for sharing..."; Tasks: firewall
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallRun]
; Remove the firewall rule again, but only if we added it (so uninstalling doesn't ask for admin otherwise).
Filename: "{sys}\cmd.exe"; Parameters: "/c netsh advfirewall firewall delete rule name=""PubHorror sharing"""; Verb: "runas"; Flags: shellexec runhidden waituntilterminated; RunOnceId: "RemoveSharingFirewallRule"; Check: FirewallRuleWasAdded

[UninstallDelete]
Type: files; Name: "{app}\sidecar\firewall-rule-added"

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

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('firewall') then
    SaveStringToFile(ExpandConstant('{app}\sidecar\firewall-rule-added'), '1', False);
end;

function FirewallRuleWasAdded: Boolean;
begin
  Result := FileExists(ExpandConstant('{app}\sidecar\firewall-rule-added'));
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
