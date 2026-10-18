# libusb для записи на флешку с телефона

С 18.10.2026 приложение пишет на флешку через [libusb](https://libusb.info), а не через
`UsbDeviceConnection.bulkTransfer` внутри libaums, — с откатом на прежний способ при любом сбое.

**Зачем.** С Android 9 `bulkTransfer` отдаёт ядру кусок libaums (до 256 КБ) одной передачей. Ядро ищет под него
непрерывную память и на фрагментированной отказывает — запись обрывается «MAX_RECOVERY_ATTEMPTS». В логах это до 40%
записей на Android 13/15/16. libusb отдаёт тот же кусок очередью URB, и ядро режет её по 16 КБ. Ещё libusb отличает
остановку конечной точки (STALL) от прочих сбоев, поэтому libaums сбрасывает флешку, а не повторяет команду вслепую.

**Где что:**
- `msqd_usb.c` — JNI-мост: Kotlin `ru.magicsqd.mobile.usb.libusb.LibusbNative` → libusb. Сделан по мотивам
  libaums/libusbcommunication (Apache-2.0, © magnusja); что изменено — в шапке файла.
- `CMakeLists.txt`, `build.sh` — сборка `libusb-1.0.so` (libusb без изменений) и `libmsqd_usb.so` (мост).
- `android/app/src/main/jniLibs/<abi>/` — готовые библиотеки. Они лежат в git, поэтому сборке приложения NDK не нужен.
- `.../usb/libusb/LibusbCommunication.kt` — транспорт для libaums.
- `.../usb/UsbFlashTransport.kt` — когда брать libusb и когда откатываться на прежний способ.
- `.../usb/UsbFlashSession.kt` (`fallBack`) — откат посреди работы.

**Откат на прежний способ:**
- libusb не загрузилась или флешка через неё не подключилась → флешка сразу подключается прежним способом.
- Сбой связи посреди записи → флешка переподключается прежним способом, и файл пишется заново. После этого до
  перезапуска программы работает только прежний способ.
- На этом телефоне libusb выключается до следующей версии программы, если:
  - было два отката подряд, а прежний способ справлялся;
  - или прошлый запуск упал в машинном коде, когда флешка была подключена через libusb.
- С сервера libusb выключается для всех без выпуска: в `content/config/client.json` прописать
  `"settings": {"android_flash_transport": "android"}`. Действует со следующего подключения флешки.
- libusb берётся только на Android 11+. Там Android сообщает, отчего закрылся прошлый запуск. На Android 8–12
  прежний способ и так почти не сбоит.

В журнале способ виден в строках «Флешка смонтирована: … — libusb 1.0.30» и «Запись на флешку закончена: … (libusb
1.0.30).», а откаты — в строках «libusb: …».

## Пересборка

Пересобирать нужно только при смене версии libusb или правке `msqd_usb.c`:

```bash
sdkmanager "ndk;29.0.14206865" "cmake;3.22.1"
bash android/native/libusb/build.sh
```

Скрипт скачивает `libusb-<версия>.tar.bz2` с GitHub-релиза libusb и сверяет SHA-256. Затем он собирает
arm64-v8a, armeabi-v7a и x86_64 и проверяет, что сегменты выровнены по 16 КБ: Android 15+ на телефонах с такой
страницей памяти иначе библиотеку не загрузит. Сборка воспроизводимая: тот же NDK даёт побайтно те же `.so`.
`tests/test_android_libusb.py` сверяет готовые библиотеки с исходниками.

## Проверка на эмуляторе

Тесты `android/app/src/androidTest/.../usb/LibusbFlashTest.kt` пишут на настоящую USB-флешку, только эмулированную
QEMU. Через libusb и прежним способом они проверяют большой файл и мелкие файлы со сверкой. Ещё они проверяют выбор
способа по версии Android, выключатель с сервера, обрыв связи посреди записи, второй откат подряд и падение в машинном
коде.

1. Образ флешки (macOS):

   ```bash
   dd if=/dev/zero of=usb.img bs=1m count=512
   DEV=$(hdiutil attach -nomount -imagekey diskimage-class=CRawDiskImage usb.img | awk 'NR==1{print $1}')
   diskutil partitionDisk $DEV MBR FAT32 MSQDTEST 100% && hdiutil detach $DEV
   ```

2. Эмулятор с образом Android 11+ arm64 (`system-images;android-35;default;arm64-v8a`). Нужен именно контроллер
   EHCI: в ядре эмулятора Android 9 нет драйвера xHCI.

   ```bash
   emulator -avd <avd> -no-window -no-snapshot -writable-system -port 5582 -qemu -device usb-ehci,id=ehci \
     -blockdev driver=file,node-name=usbfile,filename=$PWD/usb.img -blockdev driver=raw,node-name=usbstick,file=usbfile \
     -device usb-storage,bus=ehci.0,drive=usbstick,removable=on
   ```

3. В образах эмулятора нет функции USB host. Её нужно включить: выполнить `adb root` и `adb remount`, затем
   `adb reboot`, снова `adb root` и `adb remount`. После этого положить файл
   `/system/etc/permissions/android.hardware.usb.host.xml` с текстом
   `<permissions><feature name="android.hardware.usb.host" /></permissions>` и ещё раз сделать `adb reboot`.
   Флешку покажет `adb shell dumpsys usb` («QEMU USB HARDDRIVE»).

4. Собрать и поставить APK: `./gradlew :app:assembleDebug :app:assembleDebugAndroidTest`, затем `adb install -r -t`
   для обоих APK.

5. Тесты запускаются по одному: состояние выбора способа живёт в процессе.

   ```bash
   bash android/native/libusb/run_emulator_test.sh emulator-5582 <метод>
   ```

   Методы: `libusbRoundTrip`, `androidRoundTrip`, `sessionPicksTransportByAndroidVersion`,
   `serverSwitchTurnsLibusbOff`, `brokenLibusbFallsBackToStandardMidWrite`,
   `secondFallbackInRowTurnsLibusbOffOnThisPhone`.

   Падение проверяется в два шага:
   - запустить `crashGuardMountAndWait` с `-e crashGuard 1` в фоне;
   - дождаться в logcat строки `ready-for-crash pid=N` и выполнить `adb shell kill -11 N` (нужен `adb root`);
   - запустить `EXTRA="-e crashGuard 1" … crashGuardAfterCrash`.

Первая команда после включения эмулятора может упасть с `IllegalArgumentException` в `Buffer.limit` внутри
`requestSense`. Причина: флешка QEMU сначала отвечает UNIT ATTENTION, а libaums 0.10.0 неверно читает ответ на REQUEST
SENSE. В ядре эмулятора нет драйвера usb-storage, который снял бы это состояние раньше. Повторный запуск проходит.

Результат 18.10.2026 на Android 15: все тесты проходят. Скорость на эмуляторе у обоих способов одинаковая, около
3 МБ/с: упирается в эмуляцию EHCI, на телефоне она другая.

## Лицензия

libusb распространяется по GNU LGPL 2.1 или новее. Текст лицензии лежит в
`android/app/src/main/res/raw/libusb_lgpl_2_1.txt` и попадает в APK. В приложении libusb — отдельная динамическая
библиотека `libusb-1.0.so` из неизменённых исходников указанного выше релиза. Её можно заменить, не пересобирая
остальное.
