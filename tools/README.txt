Положите сюда adb.exe и его спутники (AdbWinApi.dll, AdbWinUsbApi.dll) —
их можно взять из архива "platform-tools" с официального сайта Android:
https://developer.android.com/tools/releases/platform-tools

Файлы нужны:
  tools\adb.exe
  tools\AdbWinApi.dll
  tools\AdbWinUsbApi.dll

Если этой папки/adb.exe нет, приложение попробует использовать adb из
системного PATH (если он у вас уже установлен и прописан в PATH).

Для переподписи APK своим сертификатом (app/apk_signer.py, нужна на
некоторых моделях — см. cars/<Марка>/<Модель>/files/resign_cert/) нужны
ещё два файла, тоже не хранятся в git:

  tools\apksigner.jar   — из Android SDK build-tools, например
    %LOCALAPPDATA%\Android\Sdk\build-tools\34.0.0\lib\apksigner.jar

  tools\jre_minimal\    — минимальный JRE, собрать через jlink (входит в
    любой полноценный JDK 17+):
      jlink --add-modules java.base,java.logging --strip-debug \
            --no-man-pages --no-header-files --compress=zip-9 \
            --output tools\jre_minimal

Без этих двух файлов программа продолжит работать нормально — переподпись
просто не сработает на моделях, которым она нужна (ясная ошибка вместо
тихого сбоя, см. app/apk_signer.py: find_java_path/find_apksigner_jar).
