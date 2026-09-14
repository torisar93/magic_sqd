package ru.magicsqd.mobile.usb

data class ApkOperationProgress(
    val phase: String,
    val bytesDone: Long? = null,
    val bytesTotal: Long? = null,
) {
    val determinate: Boolean get() = bytesDone != null && bytesTotal != null && bytesTotal > 0
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
}
