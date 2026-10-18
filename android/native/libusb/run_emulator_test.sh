#!/usr/bin/env bash
# Один тест LibusbFlashTest (android/app/src/androidTest) на эмуляторе с флешкой QEMU — README.md, «Проверка на
# эмуляторе». Системное окно «Разрешить доступ к USB-устройству» нажимается само.
# usage: run_emulator_test.sh <serial> <метод> [таймаут_с]; доп. аргументы am instrument — в EXTRA (EXTRA="-e crashGuard 1").
set -u
serial=$1; method=$2; timeout=${3:-300}
adb="${ANDROID_HOME:-$HOME/Library/Android/sdk}/platform-tools/adb"
out=$(mktemp)
"$adb" -s "$serial" logcat -c
# shellcheck disable=SC2086
"$adb" -s "$serial" shell am instrument -w -r ${EXTRA:-} -e class "ru.magicsqd.mobile.usb.LibusbFlashTest#$method" \
  ru.magicsqd.mobile.test/androidx.test.runner.AndroidJUnitRunner > "$out" 2>&1 &
pid=$!
start=$(date +%s)
while kill -0 $pid 2>/dev/null; do
  if (( $(date +%s) - start > timeout )); then kill $pid; echo "Время вышло"; break; fi
  xml=$("$adb" -s "$serial" exec-out uiautomator dump /dev/tty 2>/dev/null)
  if [[ "$xml" == *'android:id/button1'* && "$xml" == *[Uu][Ss][Bb]* ]]; then
    xy=$(python3 -c '
import re, sys
m = re.search(r"resource-id=\"android:id/button1\"[^>]*bounds=\"\[(\d+),(\d+)\]\[(\d+),(\d+)\]\"", sys.stdin.read())
if m:
    a, b, c, d = map(int, m.groups())
    print((a + c) // 2, (b + d) // 2)
' <<<"$xml")
    [ -n "$xy" ] && "$adb" -s "$serial" shell input tap $xy
  fi
  sleep 2
done
wait $pid 2>/dev/null
grep -E "^OK|FAILURES|Tests run|shortMsg" "$out" | tail -3
grep -q "^OK" "$out" || sed -n '/INSTRUMENTATION_STATUS: stack=/,/^INSTRUMENTATION_STATUS: stream/p' "$out" | head -20
"$adb" -s "$serial" logcat -d -s LibusbFlashTest:I | grep -v "^---"
rm -f "$out"
