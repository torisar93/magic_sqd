package ru.magicsqd.mobile

import android.content.Context
import android.webkit.WebResourceResponse
import androidx.webkit.WebViewAssetLoader
import org.json.JSONObject
import java.io.File

/**
 * Интерфейс с сервера на Android (ui_bundle.py; владелец, 2026-10-06: «чтобы обновлений приложения стало поменьше»).
 *
 * Скачанный и проверенный бандл лежит в files/ui/android-<версия>-r<выпуск>/ — файлы интерфейса (index.html, js/,
 * css/ …), изменённые после выпуска приложения. Обработчик пути "/assets/" сначала ищет файл там, иначе отдаёт
 * встроенный из APK — правка вёрстки или текста доходит до техника без нового выпуска приложения. Скачивает бандл
 * WebBridge (mobile_bridge.ui_bundle_refresh) — применяется он со следующего запуска; не дошёл интерфейс до ui_ready
 * за [UI_READY_TIMEOUT_MS] — MainActivity помечает выпуск плохим и открывает встроенный.
 */
class UiBundleAssets(private val context: Context) : WebViewAssetLoader.PathHandler {
    private val builtin = WebViewAssetLoader.AssetsPathHandler(context)

    @Volatile
    private var overlay: Pair<File, WebViewAssetLoader.InternalStoragePathHandler>? = null

    /** Папка бандла поверх встроенного интерфейса; null — только встроенный. */
    fun use(dir: File?) {
        overlay = try {
            dir?.let { it.canonicalFile to WebViewAssetLoader.InternalStoragePathHandler(context, it) }
        } catch (_: Exception) {
            null  // папка вне данных приложения или недоступна — встроенный интерфейс
        }
    }

    override fun handle(path: String): WebResourceResponse? {
        overlay?.let { (dir, handler) ->
            val file = File(dir, path)
            if (file.isFile && file.canonicalPath.startsWith(dir.path + File.separator)) return handler.handle(path)
        }
        return builtin.handle(path)
    }

    data class Active(val dir: File, val rev: Int)

    companion object {
        const val UI_READY_TIMEOUT_MS = 60_000L
        private const val PLATFORM = "android"

        fun root(context: Context) = File(context.filesDir, "ui")

        /** Действующий бандл для этой версии приложения — те же правила, что ui_bundle.active_dir: state.json этой
         * платформы и версии, выпуск — целое > 0 и не помечен плохим, имя папки по выпуску, папка на месте. */
        fun active(root: File, appVersion: String): Active? = try {
            val state = JSONObject(File(root, "state.json").readText())
            val rawRev = state.opt("rev")
            val rev = if (rawRev is Int) rawRev else null
            val bad = state.optJSONArray("bad")
            val isBad = bad != null && (0 until bad.length()).any { bad.opt(it) == rev }
            val name = "$PLATFORM-$appVersion-r$rev"
            val dir = File(root, name)
            if (state.opt("platform") == PLATFORM && state.opt("app") == appVersion && rev != null && rev > 0 &&
                !isBad && state.opt("dir") == name && dir.isDirectory
            ) Active(dir, rev) else null
        } catch (_: Exception) {
            null  // нет state.json или он битый — встроенный интерфейс
        }
    }
}
