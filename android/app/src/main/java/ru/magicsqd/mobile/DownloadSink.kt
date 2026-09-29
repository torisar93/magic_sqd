package ru.magicsqd.mobile

/**
 * Ход скачивания перед записью на флешку для Python (mobile_bridge.py: ensure_apks_downloaded,
 * sync_shared_folder_for): [line] — строка журнала сразу, а не списком после ВСЕГО скачивания; [progress] — байты:
 * в кольцо окна записи и примерно раз в 30 с строкой в журнал. Раньше, пока качались выбранные приложения и
 * комплект файлов (после переустановки программы — под полгигабайта), окно записи просто крутилось, а в журнале
 * было тихо: техник решал, что всё зависло, и сворачивал программу (Belgee S50, 28.09).
 * Методы зовутся из потоков закачки Python — отсюда @Synchronized.
 */
class DownloadSink(
    private val log: (String) -> Unit,
    private val onBytes: (done: Long, total: Long) -> Unit,
) {
    private val startedAt = System.currentTimeMillis()
    private var lastUiAt = 0L
    private var lastLogAt = startedAt
    private var sawBytes = false

    fun line(text: String) = log(text)

    @Synchronized
    fun progress(done: Long, total: Long) {
        val now = System.currentTimeMillis()
        if (total > 0) sawBytes = true
        if (now - lastUiAt >= 200 || done >= total) {
            lastUiAt = now
            onBytes(done, total)
        }
        if (total > 0 && now - lastLogAt >= 30_000) {
            lastLogAt = now
            log("…идёт скачивание: ${megabytes(done)} из ${megabytes(total)}")
        }
    }

    /** Итог подготовки — только если что-то действительно качалось (иначе запись начинается сразу). */
    fun finish() {
        if (sawBytes) log("Файлы для записи скачаны за ${durationText(System.currentTimeMillis() - startedAt)}.")
    }

    companion object {
        fun megabytes(bytes: Long): String =
            if (bytes < 10L * 1024 * 1024) "%.1f МБ".format(java.util.Locale("ru"), bytes / 1048576.0)
            else "${bytes / 1048576} МБ"

        /** «1 файл», «3 файла», «11 файлов» — как ПК (usb_api._files). */
        fun files(n: Int): String {
            val tail = n % 100
            val word = if (tail in 11..14) "файлов" else when (n % 10) { 1 -> "файл"; 2, 3, 4 -> "файла"; else -> "файлов" }
            return "$n $word"
        }

        /** После «из»: «из 1 файла», «из 3 файлов», «из 21 файла». */
        fun ofFiles(n: Int): String = "$n " + if (n % 10 == 1 && n % 100 != 11) "файла" else "файлов"

        fun durationText(ms: Long): String {
            val seconds = (ms / 1000).coerceAtLeast(0)
            return when {
                seconds < 60 -> "$seconds с"
                seconds < 3600 -> "${seconds / 60} мин ${seconds % 60} с"
                else -> "${seconds / 3600} ч ${seconds % 3600 / 60} мин"
            }
        }
    }
}
