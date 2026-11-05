; Installateur de LiveDeck (Inno Setup 6). Lancé par tools/build_exe.py :
;   ISCC /DAppVersion=1.5.0 /DSourceDir=...\dist\LiveDeck /DOutputDir=...\dist /DIconFile=...\livedeck.ico tools\installer.iss

#ifndef AppVersion
  #error AppVersion manquant
#endif

[Setup]
AppId={{6F0E5C1A-3B7D-4C52-9A48-1D5E7A0C4B21}
AppName=LiveDeck
AppVersion={#AppVersion}
AppPublisher=LiveDeck
VersionInfoVersion={#AppVersion}
; installation pour l'utilisateur courant : pas de droits administrateur (dans %LOCALAPPDATA%\Programs)
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
DefaultDirName={autopf}\LiveDeck
DefaultGroupName=LiveDeck
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\LiveDeck.exe
UninstallDisplayName=LiveDeck
SetupIconFile={#IconFile}
OutputDir={#OutputDir}
OutputBaseFilename=LiveDeck-{#AppVersion}-win64-setup
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
; propose de fermer LiveDeck s'il tourne, et le relance après une mise à jour
CloseApplications=yes
RestartApplications=yes

[Languages]
Name: "french"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\LiveDeck"; Filename: "{app}\LiveDeck.exe"
Name: "{autodesktop}\LiveDeck"; Filename: "{app}\LiveDeck.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\LiveDeck.exe"; Description: "Lancer LiveDeck"; Flags: nowait postinstall skipifsilent

[Code]
// La configuration (%APPDATA%\LiveDeck) n'est jamais supprimée. Seule l'entrée de démarrage automatique,
// qui pointe vers l'exe désinstallé, est retirée.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usPostUninstall then
    RegDeleteValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Run', 'LiveDeck');
end;
