package ru.magicsqd.mobile

import android.hardware.usb.UsbManager
import android.os.Bundle
import android.util.Log
import android.widget.Button
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import ru.magicsqd.mobile.usb.findAdbInterface
import ru.magicsqd.mobile.usb.listMassStorageDevices
import ru.magicsqd.mobile.usb.performCnxnHandshake
import ru.magicsqd.mobile.usb.readFlashInfo
import ru.magicsqd.mobile.usb.requestUsbPermission

/**
 * Экран спайка — использовался для проверки самых рискованных технических
 * допущений (ADB-транспорт по проводу, чтение/форматирование/запись флешки,
 * установка APK — все подтверждены на реальном железе, см. android/README.md).
 * Больше не launcher-экран (см. MainActivity — теперь WebView с новым
 * интерфейсом), но код и кнопки оставлены рабочими для будущей отладки
 * транспортного слоя в отрыве от UI: запустить можно через
 * `adb shell am start -n ru.magicsqd.mobile/.DiagnosticsActivity`.
 */
class DiagnosticsActivity : AppCompatActivity() {

    private lateinit var logView: TextView
    private lateinit var scrollView: ScrollView
    private lateinit var usbManager: UsbManager

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        usbManager = getSystemService(USB_SERVICE) as UsbManager
        logView = findViewById(R.id.tvLog)
        scrollView = logView.parent as ScrollView

        findViewById<Button>(R.id.btnListDevices).setOnClickListener { onListDevices() }
        findViewById<Button>(R.id.btnTestFlash).setOnClickListener { onTestFlash() }
        findViewById<Button>(R.id.btnTestAdb).setOnClickListener { onTestAdb() }

        log("Готово. Подключи USB-устройство через OTG-переходник и нажми одну из кнопок.")

        // Проверка embedding'а Chaquopy — без этого нет смысла переносить
        // cars/_shared/*.py. Реальная бизнес-логика пойдёт сюда позже.
        try {
            if (!Python.isStarted()) Python.start(AndroidPlatform(this))
            val greeting = Python.getInstance().getModule("spike").callAttr("hello").toString()
            log("Chaquopy: $greeting")
        } catch (e: Exception) {
            log("Chaquopy ОШИБКА: ${e.javaClass.simpleName}: ${e.message}")
        }
    }

    private fun log(line: String) {
        Log.d("SpikeLog", line) // видно через adb logcat -s SpikeLog — не нужно диктовать текст с экрана
        runOnUiThread {
            logView.append("\n$line")
            scrollView.post { scrollView.fullScroll(ScrollView.FOCUS_DOWN) }
        }
    }

    private fun onListDevices() {
        val devices = usbManager.deviceList.values
        if (devices.isEmpty()) {
            log("USB-устройств не найдено (deviceList пуст).")
            return
        }
        for (d in devices) {
            log("USB: ${d.deviceName} vid=0x${d.vendorId.toString(16)} pid=0x${d.productId.toString(16)} class=0x${d.deviceClass.toString(16)} interfaces=${d.interfaceCount}")
        }
        val massStorage = listMassStorageDevices(this)
        log("Из них распознано libaums как mass storage: ${massStorage.size}")
    }

    private fun onTestFlash() {
        val devices = listMassStorageDevices(this)
        if (devices.isEmpty()) {
            log("Флешка не найдена (libaums.getMassStorageDevices пуст). Проверь, что это OTG-подключение, а не просто провод питания.")
            return
        }
        val device = devices[0]
        log("Найдена флешка: ${device.usbDevice.deviceName}. Запрашиваю разрешение...")
        requestUsbPermission(this, device.usbDevice) { granted ->
            if (!granted) {
                log("Пользователь отклонил разрешение на доступ к флешке.")
                return@requestUsbPermission
            }
            log("Разрешение получено, читаю раздел...")
            Thread {
                try {
                    val info = readFlashInfo(device)
                    log("OK: label='${info.volumeLabel}' fs=${info.fsType} capacity=${info.capacityBytes / 1024 / 1024}MB free=${info.freeBytes / 1024 / 1024}MB")
                    // Сырой дамп boot sector'а — для сверки с тем, что пишет проверенный
                    // сторонний форматтер, ДО попытки собственной записи (см. UsbFlashFormat.kt).
                    ru.magicsqd.mobile.usb.dumpRawSectors(device.partitions[0], ::log)

                    log("!!! РАЗРУШИТЕЛЬНО: сейчас затру эту флешку своим FAT32-форматтером !!!")
                    val formatResult = ru.magicsqd.mobile.usb.formatFat32(
                        device.partitions[0], info.capacityBytes, "MAGICSQD", ::log
                    )
                    when (formatResult) {
                        is ru.magicsqd.mobile.usb.FormatResult.Success -> {
                            log("FORMAT OK: secPerClus=${formatResult.layout.sectorsPerCluster} fatSize=${formatResult.layout.fatSizeSectors}")
                            // Диагностика: пишем файл СРАЗУ, той же сессией, без переподключения —
                            // проверяем, отваливается ли флешка именно из-за нашего кода, или это
                            // не зависит от того, что делали до этого (см. отчёт про "гаснет диод").
                            log("Пишу тестовый файл в той же сессии, без переподключения...")
                            try {
                                val ok = ru.magicsqd.mobile.usb.writeAndVerifyTestFile(
                                    formatResult.fs, "magicsqd_test.txt", "hello from magicsqd".toByteArray()
                                )
                                log(if (ok) "WRITE OK: файл записан и прочитан обратно, содержимое совпало" else "WRITE НЕУДАЧА: содержимое не совпало после чтения")
                            } catch (e: Exception) {
                                log("WRITE ИСКЛЮЧЕНИЕ: ${e.javaClass.simpleName}: ${e.message}")
                            }
                        }
                        is ru.magicsqd.mobile.usb.FormatResult.Failed ->
                            log("FORMAT НЕУДАЧА: ${formatResult.reason}")
                    }
                } catch (e: Exception) {
                    log("ОШИБКА чтения флешки: ${e.javaClass.simpleName}: ${e.message}")
                }
            }.start()
        }
    }

    private fun onTestAdb() {
        val devices = usbManager.deviceList.values
        val target = devices.firstOrNull { findAdbInterface(it) != null }
        if (target == null) {
            log("Устройство с ADB-интерфейсом (class=0xff/subclass=0x42/protocol=0x01) не найдено среди ${devices.size} подключённых.")
            log("Возможные причины: на магнитоле выключена отладка по USB, либо это не тот порт/режим.")
            return
        }
        log("Найден ADB-интерфейс на ${target.deviceName}. Запрашиваю разрешение...")
        requestUsbPermission(this, target) { granted ->
            if (!granted) {
                log("Пользователь отклонил разрешение на доступ к устройству.")
                return@requestUsbPermission
            }
            val iface = findAdbInterface(target)!!
            val connection = usbManager.openDevice(target)
            if (connection == null) {
                log("usbManager.openDevice вернул null — не удалось открыть соединение.")
                return@requestUsbPermission
            }
            if (!connection.claimInterface(iface.usbInterface, true)) {
                log("claimInterface не удался — устройство занято другим процессом?")
                connection.close()
                return@requestUsbPermission
            }
            val transport = ru.magicsqd.mobile.usb.UsbAdbTransport(connection, iface)
            log("Соединение открыто, отправляю CNXN...")
            // AUTH-ветка (см. performCnxnHandshake) может ждать до 30 сек, пока
            // человек нажмёт "Разрешить" на экране другого устройства — это
            // блокирующий I/O, ему нельзя жить на главном потоке (ANR).
            Thread {
                val result = performCnxnHandshake(transport, this, ::log)
                when (result) {
                    is ru.magicsqd.mobile.usb.AdbHandshakeResult.Connected -> {
                        log("УСПЕХ: устройство ответило CNXN, баннер='${result.bannerFromDevice}'")
                        val shellResult = ru.magicsqd.mobile.usb.runAdbShellCommand(
                            transport, "echo hello_from_magicsqd_mobile", ::log
                        )
                        when (shellResult) {
                            is ru.magicsqd.mobile.usb.AdbShellResult.Output ->
                                log("SHELL OK: '${shellResult.text}'")
                            is ru.magicsqd.mobile.usb.AdbShellResult.Rejected ->
                                log("SHELL ОТКЛОНЁН: ${shellResult.reason}")
                            is ru.magicsqd.mobile.usb.AdbShellResult.Failed ->
                                log("SHELL ОШИБКА: ${shellResult.reason}")
                        }

                        // Пушим и ставим на целевое устройство наш же собственный APK —
                        // чистая проверка транспорта: если сработает, "Magic SQD (spike)"
                        // физически появится в списке приложений на ТОМ устройстве.
                        try {
                            val selfApk = java.io.File(applicationInfo.sourceDir).readBytes()
                            log("Пушу собственный APK (${selfApk.size} байт) на целевое устройство...")
                            val installResult = ru.magicsqd.mobile.usb.installApkOverAdb(transport, selfApk, log = ::log)
                            when (installResult) {
                                is ru.magicsqd.mobile.usb.AdbInstallResult.Success ->
                                    log("INSTALL OK: ${installResult.pmOutput}")
                                is ru.magicsqd.mobile.usb.AdbInstallResult.Failed ->
                                    log("INSTALL НЕУДАЧА: ${installResult.reason}")
                            }
                        } catch (e: Exception) {
                            log("INSTALL ОШИБКА: ${e.javaClass.simpleName}: ${e.message}")
                        }
                    }
                    is ru.magicsqd.mobile.usb.AdbHandshakeResult.Failed ->
                        log("НЕУДАЧА: ${result.reason}")
                }
                transport.close()
            }.start()
        }
    }
}
