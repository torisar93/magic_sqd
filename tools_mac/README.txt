Положите сюда adb и fastboot для macOS (без расширения — Mach-O, не
Windows-exe) — универсальные бинарники (Intel + Apple Silicon) можно взять
из архива "platform-tools" для Mac с официального сайта Android:
https://developer.android.com/tools/releases/platform-tools

Файлы нужны:
  tools_mac/adb
  tools_mac/fastboot

Если этой папки/adb нет, приложение попробует использовать adb из
системного PATH (если он у вас уже установлен и прописан в PATH).

Для переподписи APK своим сертификатом (app/apk_signer.py, нужна на
некоторых моделях — см. cars/<Марка>/<Модель>/files/resign_cert/) нужны
ещё два файла, тоже не хранятся в git:

  tools_mac/apksigner.jar   — из Android SDK build-tools, например
    ~/Library/Android/sdk/build-tools/35.0.0/lib/apksigner.jar
    (чистая Java-библиотека — тот же самый файл, что и в tools/apksigner.jar
    для Windows, платформо-независимый)

  tools_mac/jre_minimal/    — минимальный JRE, собрать через jlink (входит в
    любой полноценный JDK 17+):
      jlink --add-modules java.base,java.logging --strip-debug \
            --no-man-pages --no-header-files --compress=2 \
            --output tools_mac/jre_minimal
    (--compress=zip-9 из tools/README.txt — синтаксис JDK 21+; на JDK 17
    используется числовая схема, --compress=2 — максимальный уровень)

Без этих двух файлов программа продолжит работать нормально — переподпись
просто не сработает на моделях, которым она нужна (ясная ошибка вместо
тихого сбоя, см. app/apk_signer.py: find_java_path/find_apksigner_jar).

Для превью иконки личных APK техника без готовой иконки на сервере
(app/apk_icons.py) нужен ещё tools_mac/aapt — из тех же build-tools:
  ~/Library/Android/sdk/build-tools/35.0.0/aapt
Без него просто показывается нейтральная иконка вместо превью — не критично.
