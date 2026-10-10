#!/bin/bash
# Сборка cars/_shared/msqd_pkg_helper.dex из src/MagicSqdPkgHelper.java (см. докстринг класса). Прежний
# cars/_shared/uninstall_helper.dex (тот же код, класс со словом «uninstall») остаётся на сервере для версий до
# 1.1.1 — его зовёт встроенный код программы; новый зовёт общий код каталога (adb_permissions.py).
# Нужны: JDK 17 (javac) и Android SDK (platforms/android-35/android.jar, build-tools/*/d8).
#   helpers/uninstall_helper/build.sh
set -euo pipefail
cd "$(dirname "$0")"
SDK=${ANDROID_HOME:-$HOME/Library/Android/sdk}
ANDROID_JAR="$SDK/platforms/android-35/android.jar"
D8=$(ls -d "$SDK"/build-tools/*/d8 | sort -V | tail -1)
JAVAC=${JAVAC:-javac}
OUT=../../cars/_shared/msqd_pkg_helper.dex
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

mkdir -p "$WORK/stubs" "$WORK/classes"
# Заглушки скрытых классов — отдельно: ими только компилируем, в .dex они не идут (на устройстве — настоящие).
"$JAVAC" -source 8 -target 8 -nowarn -classpath "$ANDROID_JAR" -d "$WORK/stubs" stubs/android/content/*.java
"$JAVAC" -source 8 -target 8 -nowarn -classpath "$ANDROID_JAR:$WORK/stubs" -d "$WORK/classes" src/MagicSqdPkgHelper.java
"$D8" --release --min-api 26 --lib "$ANDROID_JAR" --classpath "$WORK/stubs" --output "$WORK" $(find "$WORK/classes" -name '*.class')
cp "$WORK/classes.dex" "$OUT"
echo "готово: $OUT ($(wc -c < "$OUT" | tr -d ' ') байт)"
