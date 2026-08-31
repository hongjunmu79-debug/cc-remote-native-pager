; Inno Setup script for the cc-remote Windows installer.
;
; Compiled by build-installer.ps1 (ISCC.exe) with these defines:
;   /DStageDir=<abs path to the staged release root>   (contains setup.ps1,
;                                                       packaging/, payload/)
;   /DDistVersion=<canonical distribution_version>      from release-metadata.json
;   /DProductVersion=<canonical product_version>        from release-metadata.json
;   /DOutputDir=<abs path for the produced .exe>
;   /DSetupArgs=-NoServices                             optional; a CI smoke
;                                                       build installs without
;                                                       registering scheduled tasks
;
; The produced executable is a genuine installer: it extracts the release
; bundle into the chosen install root and runs setup.ps1, which performs the
; transactional install (verified payload, runtime venv, immutable releases\
; directory, config wizard, supervised scheduled tasks, firewall rule). The
; .exe itself performs no install logic of its own — it bootstraps the shipped
; setup.ps1 flow, so the artifact can never drift from install.ps1.

#ifndef StageDir
  #error "StageDir is not defined; run build-installer.ps1 instead of compiling this file directly"
#endif
#ifndef DistVersion
  #error "DistVersion is not defined; run build-installer.ps1"
#endif
#ifndef ProductVersion
  #define ProductVersion "0.0.0"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif
#ifndef OutputName
  #define OutputName "cc-remote-v{#DistVersion}-windows-x64-setup"
#endif
#ifndef SetupArgs
  #define SetupArgs "-Unattended -AllowInsecureHttp"
#endif

[Setup]
AppId={{8F6D9A7E-CCR0-4E21-9B5E-CCREMOTE2026}}
AppName=cc-remote
AppVersion={#ProductVersion}
AppVerName=cc-remote {#ProductVersion}
AppPublisher=cc-remote native-pager
AppComments=Self-hosted remote control for Claude Code / Codex
DefaultDirName={localappdata}\cc-remote
DefaultGroupName=cc-remote
Compression=lzma2
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename={#OutputName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
CreateUninstallRegKey=no
Uninstallable=no
DisableWelcomePage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableStartupPrompt=yes
ShowLanguageDialog=no
WizardStyle=modern

[Files]
; The release bundle keeps the archive layout: setup.ps1 at the root, the
; packaging/windows scripts, and the verified payload tree. The Inno-managed
; copy lives at {app}\release and is what setup.ps1 runs from.
Source: "{#StageDir}\setup.ps1"; DestDir: "{app}\release"; Flags: ignoreversion
Source: "{#StageDir}\packaging\*"; DestDir: "{app}\release\packaging"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#StageDir}\payload\*"; DestDir: "{app}\release\payload"; Flags: ignoreversion recursesubdirs createallsubdirs

[Run]
; setup.ps1 performs the real install into {app} (the user-chosen install
; root). The wizard waits for it to finish so failures surface to the user.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""setup.ps1"" {#SetupArgs}"; WorkingDir: "{app}\release"; Flags: waituntilterminated; StatusMsg: "Installing cc-remote (this may take a minute)..."
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\release\packaging\windows\open-console.ps1"" -InstallRoot ""{app}"""; Description: "Open cc-remote console"; Flags: postinstall nowait skipifsilent

[Icons]
Name: "{group}\cc-remote 控制台"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\release\packaging\windows\open-console.ps1"" -InstallRoot ""{app}"""; WorkingDir: "{app}"
Name: "{autodesktop}\cc-remote 控制台"; Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\release\packaging\windows\open-console.ps1"" -InstallRoot ""{app}"""; WorkingDir: "{app}"
