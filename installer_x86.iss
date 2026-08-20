; Инсталлятор Magic SQD — x86 (32-бит) сборка, для старых ноутбуков на
; Windows 7 (минимум) с 32-битной ОС, реальный случай среди установщиков —
; часто держат такие машины специально ради старого софта для чип-тюнинга,
; который на новых Windows не идёт (см. обсуждение в истории коммитов).
; Отдельный файл, а не флаг у installer.iss — собирается из ОТДЕЛЬНОЙ
; PyInstaller-сборки (dist_x86\magic_sqd, см. .venv-x86 — 32-битный Python,
; иначе PyInstaller не даст 32-битный exe) и распространяется отдельным
; файлом на скачивание, НЕ через автообновление (app/web/api/update_api.py
; проверяет/качает только MagicSQD_Setup.exe x64 — научить его различать
; архитектуру машины не входило в объём этой задачи, x86-технику придётся
; перекачивать вручную при выходе новых версий, пока это не сделано).
;
; Свой AppId — не тот же, что у x64 installer.iss: не тот же компьютер
; технически не может иметь оба (32-битная машина не запустит x64-сборку
; вовсе, а 64-битная соберёт из update_api.py x64), поэтому конфликтовать
; они не должны, но общий AppId между установщиками разной битности —
; лишний риск (Inno Setup сам решает "это тот же продукт" по AppId,
; смешивать 32/64-битные записи под одним ключом реестра не стоит).
;
; Ставим БЕЗ прав администратора в {localappdata}\Programs — та же причина,
; что и в installer.iss (программа сама пишет рядом с exe: startup.log,
; crash.log, докачанный контент cars/apk через content_sync.py).

#define MyAppName "Magic SQD"
#define MyAppVersion "0.4.8-alpha"
#define MyAppPublisher "Magic SQD"
#define MyAppExeName "magic_sqd.exe"

[Setup]
AppId={{F3F4E4F0-3F7B-4B2B-9A79-6C1E2E7D6D4B}
AppName={#MyAppName} (x86)
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=MagicSQD_Setup_x86
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; НЕТ ArchitecturesInstallIn64BitMode — по умолчанию инсталлятор (и сам
; процесс установки) 32-битный, единственный режим, который реально
; ставится и работает на настоящей 32-битной Windows (в отличие от x64
; installer.iss, эта сборка не претендует на "нативный x64 режим", ей
; незачем — dist_x86\magic_sqd и так собран 32-битным PyInstaller'ом).
MinVersion=6.1sp1
; Подстраховка для автообновления — см. installer.iss за тем же
; комментарием (сюда перенесено для единообразия, хотя x86-сборка пока не
; участвует в автообновлении, см. заголовок файла).
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
; См. installer.iss — та же причина: content_sync.py докачивает apk/
; и files/usb_files моделей сам, инсталлятор их не бандлит.
Source: "dist_x86\magic_sqd\*"; DestDir: "{app}"; Excludes: "apk,files,usb_files,__pycache__"; Flags: ignoreversion recursesubdirs createallsubdirs

Source: "server.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "submit.json"; DestDir: "{app}"; Flags: ignoreversion

; WebView2 Runtime Bootstrapper — см. installer.iss за полным обоснованием.
; Тот же самый файл годится для любой архитектуры — сам определяет, какой
; рантайм (x86/x64/arm64) скачать под конкретную машину.
Source: "assets\MicrosoftEdgeWebview2Setup.exe"; DestDir: "{app}\tools"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
; См. installer.iss за полным обоснованием каждой строки.
Type: filesandordirs; Name: "{app}\cars"
Type: filesandordirs; Name: "{app}\apk"
Type: filesandordirs; Name: "{app}\debug_logs"
Type: filesandordirs; Name: "{app}\_qt_fallback"
Type: files; Name: "{app}\*.log"
Type: files; Name: "{app}\client_id.txt"
Type: files; Name: "{app}\seen_versions.json"
Type: files; Name: "{app}\DEBUG_LOG_ALL"

[Code]
// См. installer.iss за полным обоснованием общей идеи (официальная
// документация Microsoft про то, где искать версию WebView2 в реестре).
// Отличие от installer.iss: там инсталлятор всегда 64-битный (см.
// ArchitecturesInstallIn64BitMode), поэтому чтение WOW6432Node было
// осмысленным способом достать 32-битную запись WebView2 в обход
// автоматического редиректа реестра. Здесь ЖЕ инсталлятор САМ 32-битный —
// на настоящей 32-битной Windows ветки WOW6432Node вообще не существует
// (это чисто 64-битная OC-конструкция), поэтому нужна ЕЩЁ и проверка
// обычного (не-WOW6432Node) пути HKLM — иначе на реальной 32-битной
// машине с уже стоящим системным (не per-user) WebView2 функция ошибочно
// решила бы, что рантайма нет, и запустила бы установку заново (не
// критично — бутстраппер сам увидит, что уже стоит, и промолчит — но
// лишний шаг и лишняя точка отказа без интернета в момент установки).
function IsWebView2Installed(): Boolean;
var
  Version: String;
begin
  Result := False;
  if RegQueryStringValue(HKLM, 'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
    if (Version <> '') and (Version <> '0.0.0.0') then
      Result := True;
  if not Result then
    if RegQueryStringValue(HKLM, 'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
      if (Version <> '') and (Version <> '0.0.0.0') then
        Result := True;
  if not Result then
    if RegQueryStringValue(HKCU, 'Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}', 'pv', Version) then
      if (Version <> '') and (Version <> '0.0.0.0') then
        Result := True;
end;

[Run]
Filename: "{app}\tools\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; StatusMsg: "Устанавливаю компонент WebView2 Runtime..."; Check: not IsWebView2Installed; Flags: waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall
