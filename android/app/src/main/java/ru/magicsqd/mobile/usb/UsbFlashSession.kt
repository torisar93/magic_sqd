package ru.magicsqd.mobile.usb

import android.content.Context
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import me.jahnen.libaums.core.UsbMassStorageDevice
import me.jahnen.libaums.core.fs.FileSystem
import ru.magicsqd.mobile.usb.libusb.LibusbException
import java.io.IOException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * Держит ОДНУ смонтированную флешку живой между шагами "usb"-этапа мастера
 * установки — тот же принцип, что и AdbSession для ADB-соединения. НЕ
 * форматирует флешку сама по себе при подключении (см. connectBlocking) —
 * desktop-версия тоже не форматирует автоматически на каждом "usb"-этапе
 * (ctx.copy_dir просто пишет поверх того, что уже смонтировано); явное
 * форматирование — отдельное действие (см. format), технику решать самому.
 *
 * Способ связи с флешкой — libusb или прежний (UsbFlashTransport.kt); сбой через libusb посреди работы —
 * переподключение прежним способом и повтор (fallBack).
 */
object UsbFlashSession {
    @Volatile private var device: UsbMassStorageDevice? = null
    @Volatile private var fs: FileSystem? = null
    @Volatile private var appContext: Context? = null

    /** Способ связи текущего подключения; null — флешка не подключена. */
    @Volatile var transport: FlashTransport? = null
        private set

    /** Был откат с libusb, а удачной операции после него ещё не было (noteSuccess). */
    @Volatile private var fallbackVerdictPending = false

    val isMounted: Boolean get() = fs != null

    /** Флешка перестала отвечать посреди операции (libaums: MAX_RECOVERY_ATTEMPTS) — это подключение дальше падает
     * сразу, даже если флешку не трогали: «записано 0 из 25 файлов за 0 с» трижды подряд (Belgee X70, лог №1779,
     * 29.09). Следующая операция сначала подключается к флешке заново (WebBridge.remountIfReplugged). */
    @Volatile var broken = false
        private set

    fun noteFailure(message: String?) {
        if (device != null && message?.contains("MAX_RECOVERY_ATTEMPTS") == true) broken = true
    }

    /** Смонтированная флешка всё ещё вставлена в телефон. Флешку для QR ADB и флагов Jolion носят в магнитолу
     * и обратно; заново вставленная — уже другое USB-устройство (новое имя /dev/bus/usb/…), а старое
     * подключение libaums на ней падает MAX_RECOVERY_ATTEMPTS. */
    fun isStillAttached(context: Context): Boolean {
        val mounted = device ?: return false
        val manager = context.getSystemService(Context.USB_SERVICE) as? UsbManager ?: return true
        return manager.deviceList.values.any { it.deviceName == mounted.usbDevice.deviceName }
    }

    fun disconnect() {
        // Старое подключение закрываем: к той же флешке (её не вынимали) libaums иначе не подключится заново —
        // интерфейс остаётся занят прежним соединением. Вынутую — закрыть не выйдет, это не страшно.
        try { device?.close() } catch (_: Exception) { }
        if (transport == FlashTransport.LIBUSB) appContext?.let { FlashTransports.markClosed(it) }
        device = null
        fs = null
        transport = null
        broken = false
    }

    fun connectBlocking(context: Context, log: (String) -> Unit): Result<FileSystem> {
        disconnect()
        appContext = context.applicationContext
        val target = listMassStorageDevices(context).firstOrNull()
            ?: return Result.failure(IllegalStateException(
                "Флешка не найдена (нет USB mass storage устройств) — проверь OTG-подключение."))
        if (!requestUsbPermissionBlocking(context, target.usbDevice)) {
            return Result.failure(IllegalStateException("Пользователь отклонил разрешение на доступ к флешке"))
        }
        if (FlashTransports.choose(context, log) == FlashTransport.ANDROID) {
            return mount(target, FlashTransport.ANDROID, log)
        }
        val viaLibusb = mount(target, FlashTransport.LIBUSB, log)
        val error = viaLibusb.exceptionOrNull() ?: return viaLibusb
        if (error.message == FLASH_CORRUPTED) return viaLibusb  // испорченная FAT — способ связи ни при чём
        // Не подключилась через libusb — сразу прежним способом; до перезапуска программы только им.
        FlashTransports.noteFallback()
        log("libusb: флешка не подключилась (${error.message}) — подключаюсь стандартным способом.")
        val again = listMassStorageDevices(context).firstOrNull() ?: return viaLibusb
        return mount(again, FlashTransport.ANDROID, log).onSuccess {
            // «Разделы ещё не определились» — флешка не успела, а не libusb: не в счёт откатов подряд.
            if (error.message != PARTITIONS_PENDING) FlashTransports.noteStandardHelped(context, log)
        }
    }

    private fun mount(target: UsbMassStorageDevice, via: FlashTransport, log: (String) -> Unit): Result<FileSystem> {
        return try {
            FlashTransports.init(target, via)
            // Сразу после подключения раздел иногда ещё не успел
            // определиться (см. install_logs на сервере, 2026-09: "Index 0
            // out of bounds for length 0" — сырое исключение от голого
            // partitions[0] на пустом списке) — JS-сторона сама ретраит
            // usbConnect несколько раз подряд, так что это не фатально
            // (в логах видно, что через несколько попыток монтирование
            // всё-таки удаётся), но лучше человеческое сообщение, чем
            // голый IndexOutOfBoundsException на каждую неудачную попытку.
            val partitions = target.partitions
            if (partitions.isEmpty()) {
                closeQuietly(target)
                return Result.failure(IllegalStateException(PARTITIONS_PENDING))
            }
            val partitionFs = partitions[0].fileSystem
            device = target
            fs = partitionFs
            transport = via
            if (via == FlashTransport.LIBUSB) appContext?.let { FlashTransports.markOpen(it) }
            log("Флешка смонтирована: ${partitionFs.volumeLabel ?: "(без метки)"}, ${partitionFs.type} — " +
                FlashTransports.title(via))
            Result.success(partitionFs)
        } catch (e: Exception) {
            // Неудачное подключение закрываем: иначе оно держит интерфейс флешки до следующей попытки.
            closeQuietly(target)
            Result.failure(IllegalStateException(connectFailure(e), e))
        } catch (e: LinkageError) {
            // Нет или не загрузилась libusb (UnsatisfiedLinkError) — только у способа LIBUSB; откат — в connectBlocking.
            closeQuietly(target)
            Result.failure(IllegalStateException("libusb не загрузилась (${e.javaClass.simpleName}: ${e.message})", e))
        } catch (e: OutOfMemoryError) {
            // Испорченная таблица FAT: libaums идёт по цепочке кластеров корневой папки, а на зацикленной цепочке
            // список рос до 280 МБ — программа падала (логи №3807, №3833: Realme, Jolion 2026). OutOfMemoryError — не
            // Exception, выше его не ловили. Огромный список уже мусор — память вернётся. Отформатировать такую флешку
            // программа сама не может: libaums падает ещё при подключении.
            closeQuietly(target)
            Result.failure(IllegalStateException(FLASH_CORRUPTED))
        }
    }

    private fun closeQuietly(target: UsbMassStorageDevice) {
        try { target.close() } catch (_: Throwable) { }
    }

    /** Сбой связи с флешкой через libusb посреди операции — переподключиться к ней прежним способом и дать операции
     * повторить: и повтор, и вся дальнейшая работа до перезапуска программы — им. Возвращает новую файловую систему;
     * null — подключение и так прежнее, сбой не связи (ошибка FAT libaums «newLimit > capacity» от способа связи не
     * зависит) или флешка не подключилась и прежним способом — тогда как раньше после «перестала отвечать»: операция
     * падает, следующая подключится к флешке заново (broken). */
    fun fallBack(error: Throwable, what: String, log: (String) -> Unit): FileSystem? {
        if (transport != FlashTransport.LIBUSB || !isLinkFailure(error)) return null
        val context = appContext ?: return null
        FlashTransports.noteFallback()
        log("libusb: сбой при $what (${describeUsbError(error)}) — переподключаю флешку стандартным способом.")
        val staleDevice = device
        val staleFs = fs
        val result = connectBlocking(context, log)
        result.getOrNull()?.let {
            fallbackVerdictPending = true
            return it
        }
        log("Флешка не подключилась и стандартным способом: ${result.exceptionOrNull()?.message}")
        device = staleDevice
        fs = staleFs
        transport = FlashTransport.ANDROID
        broken = true
        return null
    }

    /** Операция с флешкой удалась: если перед ней был откат с libusb — прежний способ справился (FlashTransports
     * считает откаты подряд). */
    fun noteSuccess(log: (String) -> Unit) {
        if (!fallbackVerdictPending) return
        fallbackVerdictPending = false
        appContext?.let { FlashTransports.noteStandardHelped(it, log) }
    }

    /** Этап записи на флешку закончен без ошибок: через libusb — счёт откатов подряд заново. */
    fun noteStageWritten() {
        if (transport == FlashTransport.LIBUSB) appContext?.let { FlashTransports.noteLibusbWorked(it) }
    }

    /** Короткая операция с флешкой (убрать файлы-триггеры, прочитать отчёт магнитолы): сбой связи через libusb —
     * переподключиться прежним способом и повторить её один раз. */
    fun <T> retryOverStandard(what: String, log: (String) -> Unit, operation: (FileSystem) -> T): T {
        val result = try {
            operation(requireFs())
        } catch (e: Exception) {
            operation(fallBack(e, what, log) ?: throw e)
        }
        noteSuccess(log)
        return result
    }

    /** Способ связи текущего подключения — для итога записи в журнале. */
    fun transportTitle(): String = transport?.let { FlashTransports.title(it) } ?: "флешка не подключена"

    /** Сбой связи с флешкой, а не файловой системы: исключение libusb или исчерпанные повторы libaums. */
    private fun isLinkFailure(error: Throwable): Boolean = generateSequence(error) { it.cause }.take(8).any {
        it is LibusbException || it.message?.contains("MAX_RECOVERY_ATTEMPTS") == true
    }

    /** Подпись «Флешка подключена, но разделы ещё не определились» — JS-сторона повторяет подключение. */
    const val PARTITIONS_PENDING = "Флешка подключена, но разделы ещё не определились — пробую снова..."

    /** Текст для испорченной файловой системы — по нему окно «что сделать» (user_errors.js, flash_corrupted) советует
     * форматировать на компьютере, а не кнопкой программы. */
    const val FLASH_CORRUPTED = "Флешка повреждена: её файловая система испорчена (таблица FAT), программа не может ни " +
        "прочитать её, ни отформатировать. Отформатируйте флешку на компьютере в FAT32 или возьмите другую."

    /** Сырое исключение libaums при подключении — понятной фразой: по ней окно «что сделать» (user_errors.js)
     * отличает «флешка перестала отвечать» (переходник, контакт) от «не удалось прочитать» (не FAT32, повреждена).
     * Раньше техник видел голое «Index 8 out of bounds for length 8», «newLimit > capacity» или «?». */
    private fun connectFailure(e: Exception): String {
        val raw = e.message?.takeIf { it.isNotBlank() } ?: e.javaClass.simpleName
        return if (raw.contains("MAX_RECOVERY_ATTEMPTS") || raw.contains("claim interface", ignoreCase = true)) {
            "Флешка перестала отвечать при подключении ($raw)"
        } else {
            "Не удалось прочитать флешку ($raw)"
        }
    }

    /** Полное форматирование в FAT32 (см. UsbFlashFormat.kt) — ЗАТИРАЕТ всё
     * содержимое. Флешка должна быть уже подключена через connectBlocking. */
    fun format(capacityBytes: Long, volumeLabel: String, log: (String) -> Unit): Result<FileSystem> {
        val dev = device ?: return Result.failure(IllegalStateException("Флешка не подключена"))
        return when (val result = formatFat32(dev.partitions[0], capacityBytes, volumeLabel, log)) {
            is FormatResult.Success -> {
                fs = result.fs
                noteSuccess(log)
                Result.success(result.fs)
            }
            is FormatResult.Failed -> {
                // Сбой связи через libusb — переподключиться прежним способом и отформатировать им.
                if (fallBack(IOException(result.reason), "форматировании", log) != null) {
                    format(capacityBytes, volumeLabel, log)
                } else {
                    Result.failure(IllegalStateException(result.reason))
                }
            }
        }
    }

    fun requireFs(): FileSystem = fs ?: error("Флешка не подключена — сначала нужно её смонтировать")

    fun capacityBytes(): Long = requireFs().capacity

    private fun requestUsbPermissionBlocking(context: Context, device: UsbDevice): Boolean {
        val latch = CountDownLatch(1)
        var granted = false
        requestUsbPermission(context, device) { result ->
            granted = result
            latch.countDown()
        }
        latch.await(60, TimeUnit.SECONDS)
        return granted
    }
}
