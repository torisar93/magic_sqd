Положите сюда adb.exe и его спутники (AdbWinApi.dll, AdbWinUsbApi.dll) —
их можно взять из архива "platform-tools" с официального сайта Android:
https://developer.android.com/tools/releases/platform-tools

Файлы нужны:
  tools\adb.exe
  tools\AdbWinApi.dll
  tools\AdbWinUsbApi.dll

Если этой папки/adb.exe нет, приложение попробует использовать adb из
системного PATH (если он у вас уже установлен и прописан в PATH).
