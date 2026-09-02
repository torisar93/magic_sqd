; Инсталлятор Magic SQD — Windows 7 (x86), легаси-сборка.
;
; ОТДЕЛЬНАЯ от installer_x86.iss (обычная 32-битная сборка — Python 3.12,
; WebView2): на настоящей Windows 7 WebView2 в принципе недоступен — не
; наша логика чинить, официальный инсталлятор WebView2 Runtime от Microsoft
; сам больше не запускается на этой ОС (реальный случай, проверено на живой
; машине — "точка входа не найдена: GetPackagesByPackageFamily/
; PssQuerySnapshot", это Windows 8.1+ API, которых в kernel32.dll настоящей
; "семёрки" просто нет). По той же причине там же не работал и обычный
; Python 3.12 (сам требует Windows 8.1+, см. PEP 11) — поэтому здесь
; ОТДЕЛЬНАЯ сборка (dist_win7\magic_sqd, см. magic_sqd_win7.spec,
; main_web_win7.py, .venv-win7 — 32-битный Python 3.8, последняя версия
; CPython с официальной поддержкой Windows 7) с вшитым PySide2/Qt5
; (последняя версия Qt, ещё поддерживающая Windows 7) вместо WebView2 —
; никакого WebView2-бутстрапа в этом инсталляторе нет вовсе, он бы всё
; равно не запустился.
;
; Ставим БЕЗ прав администратора в {localappdata}\Programs — та же причина,
; что и в installer.iss (программа сама пишет рядом с exe: startup.log,
; crash.log, докачанный контент cars/apk через content_sync.py).

#define MyAppName "Magic SQD"
#define MyAppVersion "0.7.4"
#define MyAppPublisher "Magic SQD"
#define MyAppExeName "magic_sqd.exe"

[Setup]
AppId={{7B8C6E1A-4D2F-4A6B-8E9C-1F2A3B4C5D6E}
AppName={#MyAppName} (Windows 7)
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=installer_output
OutputBaseFilename=MagicSQD_Setup_Win7
SetupIconFile=assets\icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; НЕТ ArchitecturesInstallIn64BitMode — 32-битный инсталлятор, единственный
; режим, который реально ставится и работает на настоящей 32-битной
; Windows 7 (см. installer_x86.iss за тем же выбором и полным обоснованием).
MinVersion=6.1sp1
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "russian"; MessagesFile: "compiler:Languages\Russian.isl"

[Tasks]
Name: "desktopicon"; Description: "Создать значок на рабочем столе"; GroupDescription: "Дополнительные значки:"

[Files]
; См. installer.iss — та же причина: content_sync.py докачивает apk/
; и files/usb_files моделей сам, инсталлятор их не бандлит. cars\_shared\*\*
; — подпапки cars/_shared/ (тяжёлые payload-наборы техника, например
; freetuga/) туда же; *.py-хелперы прямо в _shared/ по-прежнему ставятся.
; НЕТ строки про MicrosoftEdgeWebview2Setup.exe — на настоящей Windows 7 он
; не запустится (см. пояснение в шапке файла), включать незачем.
Source: "dist_win7\magic_sqd\*"; DestDir: "{app}"; Excludes: "apk,files,usb_files,__pycache__,_shared\*\*"; Flags: ignoreversion recursesubdirs createallsubdirs

Source: "server.json"; DestDir: "{app}"; Flags: ignoreversion
Source: "submit.json"; DestDir: "{app}"; Flags: ignoreversion
; Адрес админ-API (см. installer.iss за полным обоснованием) — для
; паритета с основной сборкой, разблокировка функций администратора та же.
Source: "admin.json"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Удалить {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[UninstallDelete]
; См. installer.iss за полным обоснованием — удаляем всю папку {app}
; целиком, включая admin_saved_login.json (логин/пароль администратора в
; открытом виде) и любые другие файлы, которые программа могла написать
; сама во время работы.
Type: filesandordirs; Name: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Запустить {#MyAppName}"; Flags: nowait postinstall
