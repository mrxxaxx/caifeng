; 采风安装包（Inno Setup 7）。由 scripts/build.py 调用，版本号从 pyproject.toml 传进来：
;   ISCC /DAppVersion=0.1.0 installer\caifeng.iss
; 装到当前用户目录，不需要管理员权限。

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6F3C2B1E-8A4D-4E7B-9C51-3D2A7E0F4B86}
AppName=采风
AppVersion={#AppVersion}
AppPublisher=xxa
DefaultDirName={autopf}\caifeng
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern dynamic
SetupIconFile=..\src\caifeng\assets\icon.ico
UninstallDisplayIcon={app}\caifeng.exe
UninstallDisplayName=采风
Compression=lzma2
SolidCompression=yes
OutputDir=..\dist
OutputBaseFilename=caifeng-v{#AppVersion}-setup

[Languages]
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

; 先清掉旧版本的文件，免得升级后残留用不到的库
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "..\dist\caifeng\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\采风"; Filename: "{app}\caifeng.exe"
Name: "{autodesktop}\采风"; Filename: "{app}\caifeng.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\caifeng.exe"; Description: "{cm:LaunchProgram,采风}"; Flags: nowait postinstall skipifsilent
; 软件里点「立即更新」是静默安装（/SILENT），装好直接打开；--after-update 让新版等旧版退出
Filename: "{app}\caifeng.exe"; Parameters: "--after-update"; Flags: nowait; Check: WizardSilent

[Code]
// 卸载完问一句要不要连设置、下载列表和登录数据一起删（默认的数据目录）
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Data: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    Data := ExpandConstant('{localappdata}\caifeng');
    if DirExists(Data) and not UninstallSilent and
       (MsgBox('要同时删除设置、下载列表和网站登录数据吗？' #13#13 + Data, mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = idYes) then
      DelTree(Data, True, True, True);
  end;
end;
