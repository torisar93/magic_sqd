#!/usr/bin/env bash
# Пересборка libusb и JNI-моста (msqd_usb.c) для записи на флешку с телефона — см. README.md рядом.
# Готовые .so кладёт в android/app/src/main/jniLibs/<abi>/ (они в git: сборке приложения NDK не нужен).
# Нужны Android NDK r29 и CMake из Android SDK (sdkmanager "ndk;29.0.14206865" "cmake;3.22.1"). Сборка
# воспроизводимая: те же NDK и libusb дают побайтно те же .so (пути временной папки в них не попадают).
set -euo pipefail

LIBUSB_VERSION=1.0.30
LIBUSB_SHA256=fea36f34f9156400209595e300840767ab1a385ede1dc7ee893015aea9c6dbaf
ABIS=(arm64-v8a armeabi-v7a x86_64)  # как abiFilters в android/app/build.gradle.kts

SDK=${ANDROID_HOME:-$HOME/Library/Android/sdk}
NDK=${ANDROID_NDK_HOME:-$SDK/ndk/29.0.14206865}
CMAKE_BIN=${CMAKE_BIN:-$SDK/cmake/3.22.1/bin}
case "$(uname -s)" in
  Darwin) HOST=darwin-x86_64 ;;
  Linux) HOST=linux-x86_64 ;;
  *) echo "Неизвестная система: $(uname -s)" >&2; exit 1 ;;
esac
LLVM="$NDK/toolchains/llvm/prebuilt/$HOST/bin"

here=$(cd "$(dirname "$0")" && pwd -P)
out="$here/../../app/src/main/jniLibs"
work=$(cd "$(mktemp -d)" && pwd -P)  # pwd -P: ninja пишет в отладку настоящий путь (/private/var/…)
trap 'rm -rf "$work"' EXIT

archive="$work/libusb-$LIBUSB_VERSION.tar.bz2"
curl -fsSL -o "$archive" "https://github.com/libusb/libusb/releases/download/v$LIBUSB_VERSION/libusb-$LIBUSB_VERSION.tar.bz2"
actual=$( (command -v sha256sum >/dev/null && sha256sum "$archive" || shasum -a 256 "$archive") | cut -d' ' -f1)
if [ "$actual" != "$LIBUSB_SHA256" ]; then
  echo "Не та контрольная сумма libusb-$LIBUSB_VERSION.tar.bz2: $actual" >&2
  exit 1
fi
tar -xjf "$archive" -C "$work"

for abi in "${ABIS[@]}"; do
  build="$work/build-$abi"
  "$CMAKE_BIN/cmake" -S "$here" -B "$build" -G Ninja \
    -DCMAKE_MAKE_PROGRAM="$CMAKE_BIN/ninja" \
    -DCMAKE_TOOLCHAIN_FILE="$NDK/build/cmake/android.toolchain.cmake" \
    -DANDROID_ABI="$abi" -DANDROID_PLATFORM=android-26 -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_FLAGS="-ffile-prefix-map=$work=/build -ffile-prefix-map=$here=/src -ffile-prefix-map=$NDK=/ndk" \
    -DLIBUSB_PATH="$work/libusb-$LIBUSB_VERSION" >/dev/null
  "$CMAKE_BIN/cmake" --build "$build" >/dev/null
  mkdir -p "$out/$abi"
  for lib in libusb-1.0.so libmsqd_usb.so; do
    "$LLVM/llvm-strip" --strip-unneeded -o "$out/$abi/$lib" "$build/$lib"
    # Каждый сегмент LOAD — с выравниванием не меньше 16 КБ (Android 15+ со страницей 16 КБ).
    while read -r align; do
      if (( align < 16384 )); then
        echo "$abi/$lib: сегмент LOAD выровнен по $align — меньше 16 КБ" >&2
        exit 1
      fi
    done < <("$LLVM/llvm-readelf" -lW "$out/$abi/$lib" | awk '$1 == "LOAD" { print $NF }')
  done
  echo "$abi: $(cd "$out/$abi" && ls -l libusb-1.0.so libmsqd_usb.so | awk '{printf "%s %s байт; ", $NF, $5}')"
done
