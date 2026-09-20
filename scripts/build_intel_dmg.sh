#!/bin/bash
# Сборка Intel (x86_64) DMG для macOS — единственный релизный ассет, который не собирает CI
# (в build-release.yml только macos-15 = arm64; Intel-раннера у GitHub нет). Запускать на Mac после того,
# как CI создал релиз vX.Y.Z; версия берётся из app/version.py.
#
#   scripts/build_intel_dmg.sh            # собрать installer_output/MagicSQD_<версия>_x86_64.dmg
#   scripts/build_intel_dmg.sh --upload   # ...и приложить к GitHub-релизу v<версия> (gh release upload)
#
# Нужно один раз (см. память проекта "macOS DMG packaging"):
#   ~/dev-tools/python3.11_x64  — Intel-сборка CPython (под Rosetta) для venv .venv_x64 (создаётся сам, если нет)
#   ~/dev-tools/lipo_shim       — обёртка lipo для PyInstaller под Rosetta (добавляется в PATH, если есть)
# tools_mac (adb/aapt/fastboot/apksigner + универсальный jre_minimal) не в git — берётся с сервера
# (https://magicsqd.ru/ci-tools/tools_mac.zip, тот же архив, что качает CI) в кэш ~/dev-tools/tools_mac_cache.
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c "import re; print(re.search(r'APP_VERSION\s*=\s*\"([^\"]+)\"', open('app/version.py').read()).group(1))")
OUT="installer_output/MagicSQD_${VERSION}_x86_64.dmg"
APP="dist/Magic SQD.app"
CACHE="$HOME/dev-tools/tools_mac_cache"
echo "== Magic SQD $VERSION → $OUT"

[ -d "$HOME/dev-tools/lipo_shim" ] && export PATH="$HOME/dev-tools/lipo_shim:$PATH"

if [ ! -x .venv_x64/bin/python ]; then
  X64PY="$HOME/dev-tools/python3.11_x64/bin/python3.11"
  [ -x "$X64PY" ] || { echo "Нет .venv_x64 и нет $X64PY — см. шапку скрипта"; exit 1; }
  echo "== создаю .venv_x64 (Rosetta)"
  arch -x86_64 "$X64PY" -m venv .venv_x64
  arch -x86_64 .venv_x64/bin/pip install -q -r requirements.txt dmgbuild
fi
.venv_x64/bin/python -c "import platform; assert platform.machine()=='x86_64', platform.machine()" \
  || { echo ".venv_x64 не x86_64"; exit 1; }

if [ ! -x "$CACHE/tools_mac/adb" ]; then
  echo "== скачиваю tools_mac.zip с magicsqd.ru в $CACHE"
  mkdir -p "$CACHE/tools_mac"
  curl -fsSL "https://magicsqd.ru/ci-tools/tools_mac.zip" -o "$CACHE/tools_mac.zip"
  unzip -q -o "$CACHE/tools_mac.zip" -d "$CACHE/tools_mac"
fi
# jre_minimal должен быть универсальным (arm64+x86_64), иначе переподпись APK на Intel-маке не работает
file "$CACHE/tools_mac/jre_minimal/bin/java" | grep -q "x86_64" || { echo "jre_minimal в $CACHE не содержит x86_64-срез"; exit 1; }

echo "== PyInstaller (x86_64)"
rm -rf build dist
.venv_x64/bin/pyinstaller --noconfirm magic_sqd_mac.spec > installer_output/pyinstaller_x64.log 2>&1 || { tail -20 installer_output/pyinstaller_x64.log; exit 1; }
plutil -p "$APP/Contents/Info.plist" | grep -q "\"$VERSION\"" || { echo "версия в Info.plist не $VERSION"; exit 1; }
file "$APP/Contents/MacOS/magic_sqd" | grep -q x86_64 || { echo "бинарник не x86_64"; exit 1; }

echo "== tools_mac → Contents/Resources (не в Contents/MacOS — иначе ломается печать codesign)"
rm -rf "$APP/Contents/Resources/tools_mac"
cp -R "$CACHE/tools_mac" "$APP/Contents/Resources/tools_mac"
chmod +x "$APP/Contents/Resources/tools_mac/"{aapt,adb,fastboot} "$APP/Contents/Resources/tools_mac/jre_minimal/bin/"*

echo "== codesign (ad-hoc, как в CI)"
find "$APP" -type f -not -name "*.json" | while read -r f; do
  file -b "$f" | grep -q "Mach-O" && codesign --force --sign - "$f" 2>&1 | grep -v "replacing existing" || true
done
codesign --force --sign - "$APP" 2>&1 | grep -v "replacing existing" || true
codesign --verify --deep --strict "$APP"

echo "== DMG"
mkdir -p installer_output
.venv_x64/bin/dmgbuild -s scripts/dmg_settings.py -D app="$APP" "Magic SQD" "$OUT" > /dev/null
hdiutil verify "$OUT" > /dev/null
echo "готово: $OUT ($(du -h "$OUT" | cut -f1))"

if [ "${1:-}" = "--upload" ]; then
  gh release upload "v$VERSION" "$OUT" --clobber
  gh release view "v$VERSION" --json assets --jq '.assets[]|select(.name|test("x86_64"))|.name+" "+(.size|tostring)+" "+.digest'
fi
