package ru.magicsqd.mobile.usb

data class ApkOperationProgress(
    val phase: String,
    val bytesDone: Long? = null,
    val bytesTotal: Long? = null,
    // Фаза grant (выдача разрешений): шаги вместо байтов — «Разрешение N из M» (progress08.js).
    val stepsDone: Int? = null,
    val stepsTotal: Int? = null,
) {
    val determinate: Boolean get() = (bytesDone != null && bytesTotal != null && bytesTotal > 0) ||
        (stepsDone != null && stepsTotal != null && stepsTotal > 0)
}

/** Scoped to this synchronous installation worker, never another ADB operation. */
internal object AdbInstallProgress {
    private class Scope(val emit: (ApkOperationProgress) -> Unit, val cancelled: () -> Boolean) {
        var done = 0L
        var total = 0L
        var lastEmit = 0L
    }
    private val current = ThreadLocal<Scope>()

    fun <T> observe(emit: (ApkOperationProgress) -> Unit, cancelled: () -> Boolean, block: () -> T): T {
        val previous = current.get()
        current.set(Scope(emit, cancelled))
        return try {
            checkCancelled()
            val result = block()
            checkCancelled()
            result
        } finally {
            if (previous == null) current.remove() else current.set(previous)
        }
    }

    fun checkCancelled() {
        if (current.get()?.cancelled?.invoke() == true) error("Очередь остановлена пользователем")
    }

    fun beginTransfer(total: Long) {
        checkCancelled()
        current.get()?.let {
            it.done = 0; it.total = total; it.lastEmit = System.nanoTime()
            it.emit(ApkOperationProgress("transfer", 0, total))
        }
    }

    /** Count payload only after the corresponding ADB WRTE acknowledgement. */
    fun acknowledge(bytes: Int) {
        current.get()?.let {
            it.done += bytes
            val now = System.nanoTime()
            if (it.done == it.total || now - it.lastEmit >= 100_000_000L) {
                it.emit(ApkOperationProgress("transfer", it.done, it.total))
                it.lastEmit = now
            }
        }
    }

    fun installing() {
        checkCancelled()
        current.get()?.emit?.invoke(ApkOperationProgress("install"))
    }

    /** Выдача разрешений только что поставленному приложению (AdbPermissions.grantAllPermissions — и внутри
     *  localinstall/dex_shell, и после установки): фаза grant, total = 0 — ещё не знаем, сколько шагов. Вне
     *  установки (кнопка «Выдать разрешения») области нет — ничего не шлём. Остановку не проверяем: выдачу
     *  не прерывали и раньше, приложение уже стоит. */
    fun granting(done: Int = 0, total: Int = 0) {
        current.get()?.emit?.invoke(
            if (total > 0) ApkOperationProgress("grant", stepsDone = done, stepsTotal = total)
            else ApkOperationProgress("grant"))
    }
}
