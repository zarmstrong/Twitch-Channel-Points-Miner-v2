#define MyAppName "Twitch Channel Points Miner"
#define MyAppPublisher "Twitch Channel Points Miner"
#define MyAppExeName "TwitchChannelPointsMiner.exe"

#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif

[Setup]
AppId={{8D7522D0-35E5-45A8-8F5E-E46049123B3F}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\TwitchChannelPointsMiner
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=TwitchChannelPointsMiner-{#MyAppVersion}-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#MyAppExeName}
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked
Name: "configure"; Description: "Prefill a new configuration"; GroupDescription: "First run:"; Flags: checkedonce
Name: "enableanalytics"; Description: "Enable the analytics dashboard (recommended)"; GroupDescription: "First run:"; Flags: checkedonce

[Files]
Source: "dist\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; This build of the exe is the "standard" flavor (see build_windows.bat and
; windows_launcher.py's _is_standard_build()) - it keeps configuration,
; cookies, analytics, and logs in the per-user AppData location below, kept
; separate from {app} (the install directory) so installing, upgrading, or
; uninstalling never touches user data.
Source: "config.example.py"; DestDir: "{localappdata}\TwitchChannelPointsMiner\config"; DestName: "config.py"; Flags: onlyifdoesntexist; AfterInstall: CustomizeStarterConfig

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
Filename: "https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3#configuration-file"; Description: "Open the configuration guide"; Flags: shellexec postinstall skipifsilent unchecked

[Code]
const
  // Pascal Script doesn't support local const declarations inside a
  // function (it fails to compile with "'BEGIN' expected"), so this has
  // to live at script level even though only GeneratePassword uses it.
  PasswordChars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';

var
  ConfigurePage: TInputQueryWizardPage;

function EscapePythonString(Value: String): String;
begin
  StringChangeEx(Value, '\', '\\', True);
  StringChangeEx(Value, '"', '\"', True);
  Result := Value;
end;

function GeneratePassword(PasswordLength: Integer): String;
var
  i: Integer;
begin
  Result := '';
  for i := 1 to PasswordLength do
    Result := Result + PasswordChars[Random(Length(PasswordChars)) + 1];
end;

function PythonStreamerList(Value: String): String;
var
  CommaAt: Integer;
  Channel: String;
begin
  Result := '';
  repeat
  begin
    CommaAt := Pos(',', Value);
    if CommaAt > 0 then
    begin
      Channel := Copy(Value, 1, CommaAt - 1);
      Delete(Value, 1, CommaAt);
    end
    else
    begin
      Channel := Value;
      Value := '';
    end;

    Channel := Trim(Channel);
    if Channel <> '' then
    begin
      if Result <> '' then
        Result := Result + ', ';
      Result := Result + '"' + EscapePythonString(Channel) + '"';
    end;
  end
  until Value = '';
end;

procedure CustomizeStarterConfig;
var
  ConfigPath: String;
  ConfigBytes: AnsiString;
  ConfigText: String;
  Username: String;
  Streamers: String;
  StartAt: Integer;
  EndAt: Integer;
begin
  ConfigPath := ExpandConstant('{localappdata}\TwitchChannelPointsMiner\config\config.py');
  if not LoadStringFromFile(ConfigPath, ConfigBytes) then
    Exit;
  ConfigText := String(ConfigBytes);

  Username := Trim(ConfigurePage.Values[0]);
  if Username <> '' then
    StringChangeEx(ConfigText, '"your-twitch-username"',
      '"' + EscapePythonString(Username) + '"', True);

  Streamers := PythonStreamerList(ConfigurePage.Values[1]);
  if Streamers <> '' then
  begin
    StartAt := Pos('STREAMERS = [', ConfigText);
    if StartAt > 0 then
    begin
      EndAt := Pos('    ]', Copy(ConfigText, StartAt, MaxInt));
      if EndAt > 0 then
      begin
        EndAt := StartAt + EndAt - 1;
        ConfigText := Copy(ConfigText, 1, StartAt - 1) +
          'STREAMERS = [' + Streamers + ']' +
          Copy(ConfigText, EndAt + Length('    ]'), MaxInt);
      end;
    end;
  end;

  // Powers the desktop shell's embedded Dashboard tab. Without this, a
  // config.py created by the installer (rather than the exe's own first-run
  // template copy) would start with analytics disabled and nothing for the
  // Dashboard tab to show. This is a Pascal port of
  // ensure_windows_analytics_defaults() in windows_launcher.py, which does
  // the same thing for a ZIP install's first launch - keep the two in sync.
  //
  // AfterInstall only runs when this [Files] entry actually copied the
  // template (Inno Setup skips it when config.py already exists, thanks to
  // onlyifdoesntexist), so ConfigText here should always be the untouched
  // template. Both markers are still checked explicitly anyway, matching
  // config_editor.py's "never overwrite an existing configuration"
  // invariant rather than relying solely on that flag's behavior.
  //
  // The "enableanalytics" task only controls *whether* this already-safe
  // step turns the dashboard on - it does not change that gating. Unchecked,
  // the freshly-created config keeps analytics off (no password generated
  // either) and the exe's first run falls through to its own "analytics
  // disabled" panel and one-time enable prompt, exactly like any other
  // disabled-analytics config.
  if WizardIsTaskSelected('enableanalytics') and
     (Pos('''enable_analytics'': False,', ConfigText) > 0) and
     (Pos('ANALYTICS_CONFIG = None', ConfigText) > 0) then
  begin
    StringChangeEx(ConfigText, '''enable_analytics'': False,',
      '''enable_analytics'': True,', True);
    ConfigText := ConfigText + #13#10 +
      '# --- Added by the installer: enables the embedded dashboard ---' + #13#10 +
      '# Change these values (or set enable_analytics back to False) any time.' + #13#10 +
      'ANALYTICS_CONFIG = {' + #13#10 +
      '    ''host'': ''127.0.0.1'',' + #13#10 +
      '    ''port'': 54455,' + #13#10 +
      '    ''refresh'': 5,' + #13#10 +
      '    ''days_ago'': 7,' + #13#10 +
      '    ''password'': ''' + GeneratePassword(24) + ''',' + #13#10 +
      '    ''log_poll_interval'': 5,' + #13#10 +
      '}' + #13#10;
  end;

  ConfigBytes := AnsiString(ConfigText);
  SaveStringToFile(ConfigPath, ConfigBytes, False);
end;

procedure InitializeWizard;
begin
  // No Randomize/RandSeed call needed (and neither exists in Pascal
  // Script): Inno Setup's built-in Random() is backed by its own
  // cryptographically strong RNG (TStrongRandom), not the Delphi RTL's
  // seed-dependent one, so it's already unpredictable per run.
  ConfigurePage := CreateInputQueryPage(wpSelectTasks,
    'Configure the miner',
    'Optionally prefill the initial configuration',
    'These values are used only when no configuration already exists. ' +
    'You can change every setting later - either here or in the app''s ' +
    'own Config tab, which opens automatically the first time it runs.');
  ConfigurePage.Add('Twitch username:', False);
  ConfigurePage.Add('Channels to watch (comma-separated):', False);
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = ConfigurePage.ID) and
    (not WizardIsTaskSelected('configure'));
end;
