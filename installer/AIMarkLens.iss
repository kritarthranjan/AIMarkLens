; AIMarkLens Setup (Inno Setup 6). Built by build.ps1, which passes:
;   /DEdition=Public|Personal  /DAppVersion=x.y.z  /DSourceDir=<application folder>  /DOutputDir=<folder>
;
; Installs per user by default (no administrator rights), or for all users if chosen on the first page.
; The uninstaller removes the program, shortcuts and Apps & Features entry, and on its last page offers to
; delete the user's settings, API keys, analysis history and downloaded tools as well.

#ifndef Edition
  #define Edition "Public"
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif
#ifndef SourceDir
  #error SourceDir must point at the built application folder (run build.ps1).
#endif
#ifndef OutputDir
  #define OutputDir "."
#endif

#define AppName "AIMarkLens"
#define AppExe "AIMarkLens.exe"
#define Publisher "AIMarkLens Project"
#if Edition == "Personal"
  #define AppGuid "{{6F3C8A2E-4B1D-4E7A-9C55-2D8E61A4B9F1}"
  #define DisplayName "AIMarkLens Personal Edition"
  #define InstallFolder "AIMarkLens Personal"
#else
  #define AppGuid "{{2A9E5D47-8C3B-4F16-A0D2-7B4C19E6F358}"
  #define DisplayName "AIMarkLens"
  #define InstallFolder "AIMarkLens"
#endif

[Setup]
AppId={#AppGuid}
AppName={#DisplayName}
AppVersion={#AppVersion}
AppVerName={#DisplayName} {#AppVersion}
AppPublisher={#Publisher}
AppCopyright=Copyright (C) {#Publisher}
VersionInfoVersion={#AppVersion}
VersionInfoProductName={#DisplayName}
VersionInfoDescription={#DisplayName} Setup
DefaultDirName={autopf}\{#InstallFolder}
DisableProgramGroupPage=yes
DisableWelcomePage=no
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=AIMarkLens-{#AppVersion}-{#Edition}-Setup
SetupIconFile=..\AIMarkLens\resources\app-icon.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#DisplayName}
WizardStyle=modern
WizardImageFile=..\assets\Installer_Welcome_Banner.bmp
WizardSmallImageFile=..\assets\Installer_Header.bmp
Compression=lzma2/max
SolidCompression=yes
AppMutex=AIMarkLens.SingleInstance,AETHERVEXA.SingleInstance
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
english.WelcomeLabel2=This will install [name/ver] on your computer.%n%nAIMarkLens analyses images and text for AI watermarks and provenance signals. It only ever reads your files: originals are never modified.%n%nClose other applications before continuing.

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\Uninstaller_Banner.bmp"; DestDir: "{app}\setup"; DestName: "uninstall-banner.bmp"; Flags: ignoreversion

[InstallDelete]
; Version 1.0.0 was published as AETHERVEXA. This AppId upgrades that install in place, so remove its old shortcuts.
Type: files; Name: "{autoprograms}\AETHERVEXA.lnk"
Type: files; Name: "{autodesktop}\AETHERVEXA.lnk"
Type: files; Name: "{autoprograms}\AETHERVEXA Personal Edition.lnk"
Type: files; Name: "{autodesktop}\AETHERVEXA Personal Edition.lnk"

[Icons]
Name: "{autoprograms}\{#DisplayName}"; Filename: "{app}\{#AppExe}"; Comment: "AI watermark & provenance lab"
Name: "{autodesktop}\{#DisplayName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#DisplayName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\setup"

[Code]
var
  BannerCopy: String;

{ Keep a copy of the banner outside the program folder, which is deleted during uninstall. }
function InitializeUninstall(): Boolean;
begin
  Result := True;
  BannerCopy := ExpandConstant('{app}\setup\uninstall-banner.bmp');
  if FileExists(BannerCopy) then
  begin
    if FileCopy(BannerCopy, GetTempDir() + 'AIMarkLens-uninstall-banner.bmp', False) then
      BannerCopy := GetTempDir() + 'AIMarkLens-uninstall-banner.bmp'
    else
      BannerCopy := '';
  end
  else
    BannerCopy := '';
end;

{ The last page of the uninstaller: shows the banner and asks whether to remove personal data too. }
function AskToRemoveData(): Boolean;
var
  Form: TSetupForm;
  Banner: TBitmapImage;
  Title, Body: TNewStaticText;
  RemoveBox: TNewCheckBox;
  Done: TNewButton;
begin
  Result := False;
  Form := CreateCustomForm(ScaleX(560), ScaleY(314), False, False);
  try
    Form.Caption := '{#DisplayName} Uninstall';

    Banner := TBitmapImage.Create(Form);
    Banner.Parent := Form;
    Banner.SetBounds(0, 0, ScaleX(164), ScaleY(314));
    Banner.Stretch := True;
    if BannerCopy <> '' then
      Banner.Bitmap.LoadFromFile(BannerCopy);

    Title := TNewStaticText.Create(Form);
    Title.Parent := Form;
    Title.SetBounds(ScaleX(184), ScaleY(24), ScaleX(360), ScaleY(30));
    Title.Font.Size := 12;
    Title.Font.Style := [fsBold];
    Title.Caption := '{#DisplayName} has been removed';

    Body := TNewStaticText.Create(Form);
    Body.Parent := Form;
    Body.SetBounds(ScaleX(184), ScaleY(64), ScaleX(356), ScaleY(110));
    Body.AutoSize := False;
    Body.WordWrap := True;
    Body.Caption := 'The program, its shortcuts and its Apps & Features entry were removed. Images you analysed and ' +
      'reports you exported were not touched.' + #13#10#13#10 +
      'Your settings, encrypted API keys, local analysis history and downloaded tools (ExifTool, c2patool) are ' +
      'still on this computer, so a reinstall picks up where you left off. They are shared by both editions.';

    RemoveBox := TNewCheckBox.Create(Form);
    RemoveBox.Parent := Form;
    RemoveBox.SetBounds(ScaleX(184), ScaleY(196), ScaleX(356), ScaleY(36));
    RemoveBox.Caption := 'Also delete settings, API keys, history and tools';
    RemoveBox.Checked := False;

    Done := TNewButton.Create(Form);
    Done.Parent := Form;
    Done.SetBounds(ScaleX(440), ScaleY(266), ScaleX(100), ScaleY(28));
    Done.Caption := 'Finish';
    Done.ModalResult := mrOk;
    Done.Default := True;

    Form.ActiveControl := Done;
    Form.ShowModal();
    Result := RemoveBox.Checked;
  finally
    Form.Free();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  RemoveData: Boolean;
begin
  if CurUninstallStep <> usPostUninstall then
    Exit;
  if UninstallSilent() then
    RemoveData := ExpandConstant('{param:REMOVEDATA|0}') = '1'
  else
    RemoveData := AskToRemoveData();
  if RemoveData then
  begin
    DelTree(ExpandConstant('{userappdata}\AIMarkLens'), True, True, True);
    DelTree(ExpandConstant('{localappdata}\AIMarkLens'), True, True, True);
  end;
  if (BannerCopy <> '') and (Pos(GetTempDir(), BannerCopy) = 1) then
    DeleteFile(BannerCopy);
end;
