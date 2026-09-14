# Android UI redesign migration audit — feature-parity comparison

Дата: 2026-09-14. Сравнение бизнес-логики живого Android-приложения
(`android/app/src/main/assets/js/app.js`, `android/app/src/main/java/ru/magicsqd/mobile/WebBridge.kt`)
с пакетом переноса `D:\Users\user\Documents\MagicSQD-UI-Transfer-2026-09-14`
(Android LAB13: `interface/android/app/src/main/assets/js/app.js`, Kotlin-снимки
в `integration/lab/android/app/src/main/java/ru/magicsqd/mobile/`).

Это research-документ для планирования отдельного будущего захода по переносу
Android UI — код Android в этом заходе не менялся (кроме иконок лаунчера, см.
`[[project_ci_release_pipeline_bugs]]`/основной план переноса). Не production
и не пользовательский документ.

## Главный вывод

LAB13's `app.js` — не переписанный с нуля файл, а настоящее слияние живой
бизнес-логики в новую разметку/CSS: те же имена Bridge-методов, те же имена
событий `window.events.on(...)`, те же русскоязычные комментарии и переменные
(`sessionLog`/`sessionHasActivity`/`chatForcedProvider`). LAB13's `WebBridge.kt`
снимок — байтовый супернабор живого (972 → 1053 строк после нормализации
CRLF/LF), с ровно двумя намеренными тестовыми регрессиями (см. п.8-9) плюс
новый код иконок/прогресса. `InstallEngine.kt` аналогично строгий супернабор.
`MainActivity.kt`, `AdbTlsAuth.kt`, `MdnsResolve.kt`, `NetworkScan.kt`,
`UsbFlashQrAdb.kt`, `AdbPermissions.kt` вообще отсутствуют в LAB-снимке Kotlin
(см. `docs/03-android.md:125`) — то есть не трогались для редизайна вообще.

## 1. Аккаунты техников (регистрация/вход/восстановление/выход, синк своих машин)

**Классификация: EQUIVALENT**

- Живой: `buildAccountSection()` в `android/app/src/main/assets/js/app.js:2175-2281`,
  вызывает `Bridge.call("auth_login"/"auth_register"/"auth_logout"/"auth_forgot_password"/"auth_status", …)`,
  слушает `auth_login_result`/`auth_register_result`/`auth_logout_result`/`auth_forgot_password_result`/`auth_sync_finished`
  (строки 2499-2511). Kotlin: `WebBridge.kt:163-167` диспетчер, `authSyncMyCars()` строки 273-281.
- LAB13: `buildAccountSection()` в `interface/android/.../js/app.js:2622-2757`, те же
  имена Bridge-методов и событий (строки 3001-3013), показывается через
  `showMenuModal("Аккаунт", …)` (строка 2759) вместо простого `showModal([...])` —
  чисто UI-отличие. Kotlin diff показывает **ноль изменений** в `auth_*`/`authSyncMyCars()`.
- Половина "слияние с админ-сессией" из коммита `e94108c` — desktop-only
  (ставит cookie `is_admin` для `auth_dialog.js`). У Android нет нативного
  admin/car-editor режима вообще (`docs/03-android.md:109`), так что для этой
  половины сравнивать нечего — не пробел.

**Риск: низкий.** Bridge-контракт и Kotlin-реализация не тронуты; нужно
только перевесить модальное представление (`showModal` → `showMenuModal`).

## 2. ИИ-чат с принудительным переключением провайдера (`/deepseek`, `/qwen`, `/auto`)

**Классификация: EQUIVALENT**

- Живой: `CHAT_PROVIDER_PREFIX_RE`/`CHAT_PROVIDER_COMMAND_RE`/`CHAT_PROVIDER_LABELS`
  в `app.js:307-356`; `chatForcedProvider` и `chatSendMessage()`/`chatSendTurn()`
  (строки 402-429) передают `provider: chatForcedProvider || ""` в `chat_send`.
  Kotlin: `WebBridge.kt:151-155`.
- LAB13: идентичные регэкспы, идентичные `CHAT_PROVIDER_LABELS`, идентичные
  функции в `interface/.../js/app.js:308-429` — номера строк почти совпадают.
  `chat_send` Kotlin-диспетчер/реализация не тронуты.

**Риск: низкий/незначительный.** Перенесено практически дословно.

## 3. Wi-Fi/TLS-ADB переподключение с автообнаружением порта

**Классификация: EQUIVALENT**

- Живой: `scanHosts(port)` (`WebBridge.kt:628-672`) — ping-sweep + mDNS;
  `scanAdbService()` (строки 686-701) — TLS-ADB автообнаружение порта через
  `MdnsResolve.resolveAdbTlsConnectEndpoints()`. JS: `promptHostPicker(title, port,
  onSubmit, { editablePort: true, discoverAdbService: true })` в `app.js:1023-1032`,
  `Bridge.call("scan_adb_service", {})`, колбэки на строках 1166-1180.
- LAB13: тот же `promptHostPicker(..., { editablePort: true, discoverAdbService: true })`
  в `interface/.../js/app.js:995-1004`, идентичный вызов и колбэки на строках 1199-1317.
- `AdbTlsAuth.kt`, `MdnsResolve.kt`, `NetworkScan.kt` отсутствуют в LAB-снимке
  Kotlin вообще — не тронуты, идентичны живым.

**Риск: низкий.** Чистая UI-обёртка вокруг нетронутого Kotlin-транспорта.

## 4. Пароль ADB по QR-коду

**Классификация: EQUIVALENT**

- Живой: `renderQrAdbStage(page)` в `app.js:1859-1905`, четыре карточки-шага,
  вызывающие `qr_adb_write_flag`/`qr_adb_get_password`; результаты через
  `onQrAdbWriteResult`/`onQrAdbPasswordResult` (строки 1907-1915), события на
  2524-2525. Kotlin-диспетчер `WebBridge.kt:179-180`; нативная поддержка в
  `UsbFlashQrAdb.kt`.
- LAB13: `renderQrAdbStage(page, stage)` в `interface/.../js/app.js:2224-2276`,
  те же имена Bridge-методов и событий результата, через общий хелпер
  `sendUsbOperation(method, args, stage)` (используется для всех USB-этапов;
  дополнительно синтезирует локальный error-результат при сбое транспорта,
  строки 2160-2161 — добавленная устойчивость, не изменение поведения).
  `UsbFlashQrAdb.kt` отсутствует в LAB-снимке (не тронут).

**Риск: низкий.** Нужно только переоформить четыре шага под новую карточную
вёрстку LAB13; Bridge-контракт и нативная поддержка не меняются.

## 5. Выбор личных APK через `OpenMultipleDocuments` (SAF)

**Классификация: EQUIVALENT**

- Живой: `MainActivity.kt:61-64` регистрирует `pickApksLauncher` через
  `ActivityResultContracts.OpenMultipleDocuments()`, связывает
  `bridge.pickApksLauncher`; результат идёт через `bridge.onApksPicked(uris)`
  (`WebBridge.kt:936`). JS вызывает `Bridge.call("pick_personal_apks", {})`
  (`app.js:1731`), обрабатывает `onPersonalApksPicked` (строки 1196-1204) на
  событии `personal_apks_picked` (2515).
- LAB13: тот же `Bridge.call("pick_personal_apks", {})` (`interface/.../js/app.js:1762`),
  тот же обработчик (строки 1229-1240) и подписка на событие (3017). `MainActivity.kt`
  подтверждённо не менялся в пакете (`docs/03-android.md:32-38, 125`). LAB
  дополнительно включает `personalApks` в модель "черновика" выбора на USB-этапе
  (строки 1840/1859/1894) — доработка взаимодействия поверх той же возможности,
  не пробел.

**Риск: низкий.** Нативных изменений не требуется, JS-точки вызова уже есть в
новых рендерах этапов.

## 6. Автоподбор способа установки APK для капризных магнитол

**Классификация: EQUIVALENT**

- Живой: `InstallEngine.INSTALL_METHODS` (`InstallEngine.kt:197-216`) — порядок
  `pm_install → pm_install_stream → pm_install_spoofed → localinstall (chery_localinstall.apk)
  → dex_shell_install (dex_shell_helper.dex / app_process)`. `installApks()`
  (строка 228+) сначала пробует `preferredMethod`, потом перебирает список,
  запоминая сработавший метод. Вызывается через `Bridge.call("adb_install_apks",
  { index, apkPaths, appsInstallMethod: stage.apps_install_method || "" })`
  (`app.js:1798-1799`) → `WebBridge.kt` строка 158 → `adbInstallApks()` →
  `installEngine().installApks(...)`.
- LAB13: идентичный вызов `adb_install_apks` в `interface/.../js/app.js:2013-2014`.
  Kotlin diff показывает, что `installApks(...)` становится тонкой обёрткой,
  делегирующей в новый `installApksWithProgress(...)` — **порядок
  `INSTALL_METHODS` и логика фолбэка/"запоминания сработавшего метода"
  побайтово идентичны**; diff только добавляет параметры `cancelled`/`onProgress`/
  `onDetail` и обёртку `AdbInstallProgress.observe(...)` вокруг каждого вызова
  `install(bytes, log)`. `WebBridge.kt`'s `adbInstallApks()` также добавляет
  события `ApkDownloadProgress`/`apk_progress` и флаг `labCancelInstall` (новая
  кнопка отмены, подтверждена в LAB `app.js:2002-2007`, вызывающая
  `Bridge.call('adb_cancel_install', {})`, диспетчеризуется новой строкой в
  `WebBridge.kt`).

**Риск: низкий.** Самый надёжно подтверждённый пункт — сам алгоритм фолбэка не
тронут, только обёрнут в прогресс/отмену (чистое улучшение относительно
живого, где нет кнопки отмены и есть только грубый прогресс).

## 7. Этап действий/прав (выдача разрешений, mock-location)

**Классификация: EQUIVALENT**

- Живой: `renderActionsStage(page, stage)` в `app.js:1590-1629`. `grant_permissions`/
  `mock_location` получают список пакетов через `Bridge.call("actions_list_packages",
  { thirdPartyOnly: true })`, показывают `promptPackagePicker(...)` (строки
  1211-1240), затем `actions_grant_permissions`/`actions_mock_location`. Прочие
  виды печатают "не поддерживается в мобильной версии". Kotlin: `WebBridge.kt:187-189`
  → `AdbPermissions.kt`.
- LAB13: `renderActionsStage(page, stage)` в `interface/.../js/app.js:1591-1640+`,
  те же три имени Bridge-методов, тот же `promptPackagePicker`, то же сообщение
  о неподдержке, плюс добавленное состояние `disabled`/заметка/ошибка
  (`flowCard`, `showLabNotice`, `.flow-action-note`) и `try/catch` вокруг вызовов
  Bridge. Kotlin-сторона в LAB-снимке не тронута.

**Риск: низкий.** Новое оформление карточек — строгий UI-супернабор живой логики.

## 8. Автоотправка install-log после каждой попытки установки

**Классификация: PARTIAL/OLDER — намеренно заглушено для тестовой сборки LAB, тривиально восстановить**

- Живой: `flushSessionLog(success)` в `app.js:136-145` вызывает
  `Bridge.call("install_log_send", { brand, model, modification, success, log })`,
  вызывается из `advanceAfter`/`__handleBackPress`/`openWizard`/
  `window.__flushInstallLogOnStop` (строка 1387). Kotlin-диспетчер `WebBridge.kt:156`
  → `installLogSend(...)` реально шлёт через `install_log_bridge.send_install_log(...)`
  на `INSTALL_LOG_URL` (коммит `d442601`).
- LAB13 JS: `flushSessionLog`, `sessionLog`, `sessionHasActivity`, `sessionSent`,
  `window.__flushInstallLogOnStop` — все присутствуют и идентичны
  (`interface/.../js/app.js:113-145, 1334-1335`), та же форма вызова Bridge.
- LAB13 Kotlin (diff, старая строка 526): тело `installLogSend(...)` заменено на
  no-op с комментарием (`// UI LAB: retain the local log and chat, skip production
  statistics.`) — намеренная LAB-заглушка, явно подтверждённая
  `docs/03-android.md:145-148` ("сохранить production-отправку через
  `install_log_bridge`").

**Риск: очень низкий.** JS не тронут; нужно вернуть тело одного ~15-строчного
Kotlin-метода.

## 9. Реальная проверка обновлений (`startUpdateCheck()` / `app_update_check`)

**Классификация: PARTIAL/OLDER — намеренно заглушено для тестовой сборки LAB; JS-сторона вызывает корректно**

- Живой: `checkForUpdate()` (`app.js:2083-2087`) вызывает
  `Bridge.call("app_update_check", {})`; `onUpdateCheckResult(event)` (строки
  2089-2104) рисует модалку обновления из `update.version`/`update.changelog`/
  `update.download_url`. Kotlin: `"app_update_check" -> { startUpdateCheck(); "{}" }`
  (`WebBridge.kt:128`) — реальная реализация.
- LAB13 JS: `checkForUpdate()`/`onUpdateCheckResult()` в `interface/.../js/app.js:2469-2475`,
  идентичный вызов и идентичное потребление полей, подписаны на то же событие
  `update_check_result` (строка 3029). **JS-сторона корректна, изменений не требует.**
- LAB13 Kotlin (diff, старая строка 128): диспетчер заменён на захардкоженную
  заглушку `available: false`, подтверждено `docs/03-android.md:145-147`
  ("восстановить существующий `startUpdateCheck()`").

**Риск: очень низкий.** Как и п.8 — вернуть одну строку Kotlin-диспетчера.

## 10. Прочая бизнес-логика, замеченная по ходу (вне исходного списка)

- **Разрешение иконки APK (`lab_apk_icon` / событие `apk_icon`)** — новое в
  LAB13, живого эквивалента нет: Kotlin diff добавляет `loadLabApkIcon()`/
  `serverApkIcon()` (URL из `apk_icons`-поля манифеста, фолбэк на локальное
  извлечение через `PackageManager`, LRU-кэш), в паре с JS-потребителем
  (`docs/03-android.md:127-131`). При будущем слиянии сохранить, не считать
  рискованным легаси — но зависит от поля `apk_icons` в `manifest.json`,
  которое должно существовать на сервере (см. основной план переноса, §6).
- **Реальный побайтовый прогресс установки/скачивания** (`apk_progress`:
  `phase`/`determinate`/`bytes_done`/`bytes_total`) — тоже новое
  (`ApkOperationProgress.kt`, `AdbInstall.kt`/`AdbInstallProgress`, плюс
  Python-колбэки в `content_sync.py`/`apk_library.py`/`mobile_bridge.py`,
  `docs/03-android.md:117-123, 133-143`). У живого только грубый счётчик
  готово/всего. Затрагивает Chaquopy Python-файлы вне Kotlin/JS — отслеживать
  отдельно на реальном заходе переноса.
- **Кнопка отмены очереди установки** (`adb_cancel_install`/`labCancelInstall`)
  — новое в LAB13 (см. п.6); у живого нет способа отменить установку на лету.
- **Плейсхолдер `CHAT_KEY`** — в обоих Kotlin-снимках пакета `CHAT_KEY` заменён
  на литерал `"__KEEP_EXISTING_CHAT_KEY__"` (`docs/03-android.md:18`). Не
  реальное значение — никогда не вставлять в продакшен при будущем слиянии.
- **Паритет ключей настроек** (`auto_sync`, `reduced_motion`, `compact_log`,
  `keep_screen_on`, `chat_enabled`) — подтверждено идентично между живым
  (`app.js` ~2295+, 2530-2534) и LAB13 (`interface/.../js/app.js:2780-2860,
  3034-3039`). Не зона риска, упомянуто т.к. `docs/03-android.md:108` называет
  это инвариантом.
- **Форма аргументов `adb_install_apks`** (`index`, `apkPaths`,
  `appsInstallMethod`) идентична между живым (строки 1798-1799) и LAB13
  (строки 2013-2014) — подтверждает п.6 повторно.

## Итог и относительная оценка объёма будущего переноса

Из 9 пунктов из исходного списка **7 уже полностью EQUIVALENT** в LAB13
(аккаунты техников, принудительное переключение ИИ-провайдера, Wi-Fi/TLS-ADB
переподключение с автообнаружением, пароль ADB по QR, выбор личных APK,
автоподбор способа установки, этап действий/прав). **2 являются
PARTIAL/OLDER только из-за двух намеренных, хорошо задокументированных
однофункциональных Kotlin-заглушек** (автоотправка install-log, проверка
обновлений) — оба пакета документации сами прямо помечают их как "не
переносить в продакшен". **Ничего не ABSENT.**

Это существенно менее рискованный перенос, чем предполагалось изначально —
LAB13's `app.js` является настоящим слиянием живой бизнес-логики в новую
разметку, а не переписыванием с нуля, потерявшим функциональность. Kotlin-
сторона — строгий супернабор живого (кроме двух отмеченных заглушек) и
добавляет три по-настоящему новые возможности (разрешение иконки APK,
реальный побайтовый прогресс, отмена очереди установки), которые стоит
сохранить, а не считать регрессией.

**Относительный объём работы, от большего к меньшему:**

1. **Наибольший объём — пункты 4 (QR-ADB) и 7 (этап действий/прав):** оба
   перешли от простого списка элементов у живого к более богатым карточным
   компонентам LAB13 (`qrAdbStepCard`/`flowCard`, `showLabNotice`, состояние
   занятости/ошибки по карточке). Bridge-контракт не изменён, но DOM-структуру/
   привязку событий нужно полностью перепроверить против нового CSS
   (`usb06.css`, `ui07.css`/`ui08.css`) и инвариантов `labInstallBusy`/
   `is-installing-apps` (`docs/03-android.md:105`).
2. **Средний — пункт 6 (автоподбор способа установки):** Kotlin-логику легко
   сохранить (подтверждена побайтовая совместимость), но наложение нового UI
   прогресса/кнопки отмены на то, во что живой `app.js` эволюционирует к
   моменту реального переноса, потребует аккуратности, поскольку отрисовка
   прогресса затрагивает общий стек `progress08.js`/`ui-lab.js`
   (`docs/03-android.md:76`).
3. **Тривиально — пункты 8 и 9:** вернуть тела двух Kotlin-функций к живой
   реализации; JS-работы ноль.
4. **Тривиально — пункты 1, 2, 3, 5:** Bridge-контракты и Kotlin-реализация не
   тронуты; работа ограничена проверкой, что новая разметка модалок/панелей
   сохраняет нужные поля форм и обработчики.

Главный риск не в каком-то одном из этих 9 пунктов, а в оговорке уже
имеющейся в `docs/03-android.md:96-109`: живой `app.js` продолжит
накапливать бизнес-логику между сейчас (2026-09-14) и моментом реального
переноса, так что слияние нужно будет пересчитывать против `app.js` таким,
какой он есть на тот момент — этот аудит снимок состояния, а не постоянная
гарантия.
