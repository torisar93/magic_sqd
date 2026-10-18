"""Запись на флешку с телефона через libusb с откатом на прежний способ (владелец, 18.10.2026: «давай сразу добавим в
приложение но безопасно чтобы если что программа сама откатывалась на старый способ»).

Готовые библиотеки (android/app/src/main/jniLibs, собирает android/native/libusb/build.sh) сверяются с исходниками:
все ABI сборки, выравнивание по 16 КБ (Android 15+), JNI-имена ровно те, что объявлены в Kotlin. Остальное — что откат
на прежний способ действительно есть во всех местах, где флешка подключается и пишется."""
from __future__ import annotations

import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "android"
JNI_LIBS = ANDROID / "app/src/main/jniLibs"
NATIVE = ANDROID / "native/libusb"
USB = ANDROID / "app/src/main/java/ru/magicsqd/mobile/usb"
LIBS = ("libusb-1.0.so", "libmsqd_usb.so")
MACHINES = {"arm64-v8a": (2, 183), "armeabi-v7a": (1, 40), "x86_64": (2, 62)}  # (ELFCLASS, e_machine)
JNI_PREFIX = "Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_"


def _abi_filters() -> list:
    gradle = (ANDROID / "app/build.gradle.kts").read_text(encoding="utf-8")
    return re.findall(r'"([\w-]+)"', re.search(r"abiFilters \+= listOf\(([^)]*)\)", gradle).group(1))


def _load_aligns(data: bytes) -> list:
    """p_align всех сегментов PT_LOAD (ELF32/ELF64, little-endian)."""
    assert data[:4] == b"\x7fELF" and data[5] == 1
    if data[4] == 2:
        phoff, = struct.unpack_from("<Q", data, 32)
        phentsize, phnum = struct.unpack_from("<HH", data, 54)
        headers = [struct.unpack_from("<IIQQQQQQ", data, phoff + i * phentsize) for i in range(phnum)]
        return [h[7] for h in headers if h[0] == 1]
    phoff, = struct.unpack_from("<I", data, 28)
    phentsize, phnum = struct.unpack_from("<HH", data, 42)
    headers = [struct.unpack_from("<IIIIIIII", data, phoff + i * phentsize) for i in range(phnum)]
    return [h[7] for h in headers if h[0] == 1]


def _kotlin_natives() -> set:
    code = (USB / "libusb/LibusbCommunication.kt").read_text(encoding="utf-8")
    return set(re.findall(r"@JvmStatic external fun (\w+)\(", code))


def test_prebuilt_libraries_for_every_abi_are_16k_aligned():
    assert sorted(_abi_filters()) == sorted(MACHINES)
    for abi, (elf_class, machine) in MACHINES.items():
        for lib in LIBS:
            data = (JNI_LIBS / abi / lib).read_bytes()
            assert data[4] == elf_class and struct.unpack_from("<H", data, 18)[0] == machine, f"{abi}/{lib}"
            aligns = _load_aligns(data)
            assert aligns and min(aligns) >= 16384, f"{abi}/{lib}: {aligns}"


def test_jni_bridge_exports_exactly_the_kotlin_natives():
    natives = _kotlin_natives()
    assert {"nativeInit", "nativeClose", "nativeBulkTransfer", "nativeControlTransfer"} <= natives
    source = (NATIVE / "msqd_usb.c").read_text(encoding="utf-8")
    assert set(re.findall(JNI_PREFIX + r"(\w+)\(", source)) == natives
    for abi in MACHINES:
        data = (JNI_LIBS / abi / "libmsqd_usb.so").read_bytes()
        assert b"libusb-1.0.so\x00" in data, abi  # DT_NEEDED: libusb — отдельной библиотекой (LGPL)
        for name in natives:
            assert (JNI_PREFIX + name).encode() + b"\x00" in data, f"{abi}: {name}"
    loader = (USB / "libusb/LibusbCommunication.kt").read_text(encoding="utf-8")
    assert 'System.loadLibrary("msqd_usb")' in loader


def test_build_script_pins_libusb_and_matches_gradle_abis():
    script = (NATIVE / "build.sh").read_text(encoding="utf-8")
    assert re.search(r"^LIBUSB_VERSION=1\.0\.\d+$", script, re.M)
    assert re.search(r"^LIBUSB_SHA256=[0-9a-f]{64}$", script, re.M)
    abis = re.search(r"^ABIS=\(([^)]*)\)", script, re.M).group(1).split()
    assert sorted(abis) == sorted(_abi_filters())
    assert "max-page-size=16384" in (NATIVE / "CMakeLists.txt").read_text(encoding="utf-8")
    # LGPL: текст лицензии — в APK (res/raw), не в папке интерфейса (она уходит в бандлы интерфейса)
    license_text = (ANDROID / "app/src/main/res/raw/libusb_lgpl_2_1.txt").read_text(encoding="utf-8")
    assert license_text.lstrip().startswith("GNU LESSER GENERAL PUBLIC LICENSE")


def test_libusb_only_where_it_is_safe_and_switchable_from_the_server():
    transport = (USB / "UsbFlashTransport.kt").read_text(encoding="utf-8")
    choose = transport[transport.index("fun choose("):transport.index("fun init(")]
    assert "Build.VERSION.SDK_INT < Build.VERSION_CODES.R) return FlashTransport.ANDROID" in choose
    assert 'serverSetting(context) == "android"' in choose and "failedThisRun" in choose
    assert 'SERVER_SETTING = "android_flash_transport"' in transport
    # падение в машинном коде при флешке через libusb — дальше на этом телефоне только прежний способ
    assert "ApplicationExitInfo.REASON_CRASH_NATIVE" in transport and "BuildConfig.VERSION_CODE" in transport
    init = transport[transport.index("fun init("):transport.index("fun noteFallback(")]
    assert "finally" in init and "UnderlyingUsbCommunication.DEVICE_CONNECTION_SYNC" in init


def test_every_flash_operation_falls_back_to_the_standard_way():
    session = (USB / "UsbFlashSession.kt").read_text(encoding="utf-8")
    connect = session[session.index("fun connectBlocking("):session.index("private fun mount(")]
    assert connect.index("mount(target, FlashTransport.LIBUSB, log)") < connect.index("mount(again, FlashTransport.ANDROID, log)")
    assert "catch (e: LinkageError)" in session  # нет libusb в сборке — тоже откат, а не падение
    write = (USB / "UsbFlashWrite.kt").read_text(encoding="utf-8")
    one_file = write[write.index("fun writeFileToUsb("):write.index("data class UsbPlanFile(")]
    assert "UsbFlashSession.fallBack(e, " in one_file and "current = fresh" in one_file
    for stage in ("fun writeUsbPlan(", "fun writeUsbStage("):
        body = write[write.index(stage):]
        assert "UsbFlashSession.retryOverStandard(" in body[:body.index("StageRunResult.Success")]
    qr = (USB / "UsbFlashQrAdb.kt").read_text(encoding="utf-8")
    assert qr.count("UsbFlashSession.retryOverStandard(") == 2
    assert "fallBack(IOException(result.reason), \"форматировании\", log)" in session
    # способ связи — в журнале у подключения и у итога записи
    assert "FlashTransports.title(via)" in session
    bridge = (ANDROID / "app/src/main/java/ru/magicsqd/mobile/WebBridge.kt").read_text(encoding="utf-8")
    assert "val via = UsbFlashSession.transportTitle()" in bridge and bridge.count("за $took ($via).") == 2


def test_libusb_control_transfer_fails_like_the_standard_way():
    # Флешки, отвечающие STALL на GET MAX LUN, libaums подключает по результату -1 — исключение их бы не пускало.
    code = (USB / "libusb/LibusbCommunication.kt").read_text(encoding="utf-8")
    control = code[code.index("override fun controlTransfer("):code.index("override fun resetDevice(")]
    assert "if (result < 0) -1 else result" in control and "throw" not in control
