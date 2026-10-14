package ru.magicsqd.mobile

import android.annotation.SuppressLint
import android.app.Activity
import android.app.KeyguardManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.PowerManager
import android.provider.MediaStore
import android.util.Log
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import androidx.appcompat.app.AlertDialog
import androidx.core.content.FileProvider
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat
import org.json.JSONObject

/**
 * Главный экран — новый мобильный интерфейс с нуля (assets/index.html),
 * не портирован из desktop-версии app/web/frontend. Каталог машин (cars/)
 * синхронизируется с сервера через content_sync.py/scanner.py, перенесённые
 * в Chaquopy (см. WebBridge.kt/android/app/src/main/python/) — сами файлы
 * чистый stdlib на десктопе, портировались почти без изменений. Реальный
 * install-движок (выполнение stages.py через уже готовые
 * UsbAdbTransport/UsbFlashFormat) — следующий шаг.
 *
 * Старый диагностический экран (проверка ADB/USB-транспорта) —
 * [DiagnosticsActivity], больше не launcher, но код рабочий и доступен
 * через adb для будущей отладки транспортного слоя в отрыве от UI.
 */
class MainActivity : AppCompatActivity() {
    // Класс-уровня, не локальная в onCreate — нужна ещё и в onStop() ниже
    // (флаш лога установки при сворачивании/закрытии).
    private lateinit var webView: WebView

    // Сторож интерфейса с сервера (UiBundleAssets): снимается по ui_ready, иначе — откат на встроенный.
    private val uiWatchdog = android.os.Handler(android.os.Looper.getMainLooper())

    // <input type="file"> в окне программы (фото экрана для ИИ-помощника, 14.10.2026, и любые будущие поля выбора файла
    // из бандла интерфейса — уже без выпуска): камера или галерея. Без WebChromeClient.onShowFileChooser WebView молча
    // игнорирует нажатие на поле выбора файла. JS узнаёт, что это есть, по app_version.file_chooser (WebBridge).
    private var fileChooserCallback: ValueCallback<Array<Uri>>? = null
    private var cameraPhoto: java.io.File? = null
    private val fileChooserLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val callback = fileChooserCallback ?: return@registerForActivityResult
        fileChooserCallback = null
        val photo = cameraPhoto
        cameraPhoto = null
        val picked = if (result.resultCode != Activity.RESULT_OK) null
            else WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
                ?: photo?.takeIf { it.length() > 0 }?.let { arrayOf(FileProvider.getUriForFile(this, "$packageName.files", it)) }
        callback.onReceiveValue(picked)
    }

    private fun showFileChooser(callback: ValueCallback<Array<Uri>>, params: WebChromeClient.FileChooserParams): Boolean {
        fileChooserCallback?.onReceiveValue(null)
        fileChooserCallback = callback
        val gallery = params.createIntent()
        if (!params.acceptTypes.any { it.startsWith("image/") }) return launchFileChooser(gallery)
        if (params.isCaptureEnabled) return launchFileChooser(cameraIntent())
        // Камеру запускаем сами, а не через Intent.createChooser: так разрешение на запись снимка (EXTRA_OUTPUT)
        // доходит до приложения камеры на всех прошивках.
        AlertDialog.Builder(this)
            .setTitle("Фото экрана")
            .setItems(arrayOf("Сфотографировать", "Выбрать из галереи")) { _, which ->
                launchFileChooser(if (which == 0) cameraIntent() else gallery)
            }
            .setOnCancelListener { fileChooserCallback?.onReceiveValue(null); fileChooserCallback = null }
            .show()
        return true
    }

    private fun cameraIntent(): Intent {
        val dir = java.io.File(cacheDir, "photos").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }  // прошлые снимки уже прочитаны окном программы — не копим
        val file = java.io.File(dir, "screen_${System.currentTimeMillis()}.jpg")
        cameraPhoto = file
        return Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            .putExtra(MediaStore.EXTRA_OUTPUT, FileProvider.getUriForFile(this, "$packageName.files", file))
            .addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }

    private fun launchFileChooser(intent: Intent): Boolean {
        try {
            fileChooserLauncher.launch(intent)
        } catch (e: ActivityNotFoundException) {
            Log.w(TAG, "нет приложения для выбора файла: ${e.message}")
            fileChooserCallback?.onReceiveValue(null)
            fileChooserCallback = null
            cameraPhoto = null
        }
        return true
    }

    companion object {
        private const val TAG = "MainActivity"
        private const val INDEX_URL = "https://appassets.androidplatform.net/assets/index.html"
    }

    /** Перехватывает необработанные исключения ЛЮБОГО потока (JVM-глобальная
     * настройка, а не только для этого потока/Activity) — раньше такого не
     * было вовсе: реальный вылет приложения не оставлял никакого следа даже
     * локально, не то что на сервере (см. WebBridge.kt: clientLogError — та
     * же идея для необработанных JS-ошибок). Дописывает маркер + полный
     * стек-трейс в прочный журнал ТЕКУЩЕЙ сессии (см. InstallLogQueue.kt) —
     * если сессии сейчас нет, запись тихо уберётся следующим
     * startSession/recoverStaleCurrent, ничего не сломается (см. их
     * докстринги) — затем передаёт управление ПРЕЖНЕМУ обработчику: не
     * подавляет штатное поведение ОС ("приложение остановлено" и т.п.),
     * только успевает записать лог раньше, чем процесс исчезнет. Первой
     * строкой в onCreate — максимально рано, до создания WebView и всего
     * остального. */
    private fun installCrashHandler() {
        val previous = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            try {
                InstallLogQueue.appendCurrent(
                    filesDir,
                    "${InstallLogQueue.CRASH_MARKER}\n${Log.getStackTraceString(throwable)}",
                    true,
                )
            } catch (_: Exception) {
                // второй сбой здесь не должен помешать штатной обработке ниже
            }
            previous?.uncaughtException(thread, throwable)
        }
    }

    @SuppressLint("SetJavaScriptEnabled") // локальный ассет, не произвольные сайты — безопасно
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        installCrashHandler()
        // Явно, а не полагаясь только на неявный edge-to-edge от targetSdk 35 —
        // на некоторых OEM-прошивках (MIUI/HyperOS и т.п.) неявное поведение
        // применяется через раз, из-за чего контент оказывался НЕ на весь
        // экран (обрезан по краям) вместо ожидаемого "под системными барами".
        WindowCompat.setDecorFitsSystemWindows(window, false)
        setContentView(R.layout.activity_webview)

        val root = findViewById<android.widget.FrameLayout>(R.id.root)
        webView = findViewById(R.id.webView)
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true // localStorage — под будущий resizer/сохранение состояния
        val bridge = WebBridge(this, webView)
        webView.addJavascriptInterface(bridge, "AndroidBridge")
        webView.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean =
                showFileChooser(callback, params)
        }

        // "Добавить свой APK..." (см. WebBridge.kt: pickPersonalApks/
        // onApksPicked) — регистрировать ActivityResultLauncher нужно ДО
        // onStart, поэтому мост не может завести его сам, только вызвать
        // через эту лямбду. OpenMultipleDocuments вместо GetMultipleContents
        // — даёт долгоживущее разрешение на конкретный файл, а не только на
        // время текущего запроса (не нужно здесь, но безопаснее по умолчанию).
        val pickApksLauncher = registerForActivityResult(ActivityResultContracts.OpenMultipleDocuments()) { uris ->
            bridge.onApksPicked(uris)
        }
        bridge.pickApksLauncher = { mimeTypes -> pickApksLauncher.launch(mimeTypes) }

        // targetSdk 35 включает edge-to-edge по умолчанию — без этого контент
        // рисуется ПОД статус-баром (часы/иконки поверх "Magic SQD"/кнопки
        // "Назад"). Отступ вешаем на родительский FrameLayout, а не на сам
        // WebView — у WebView свой Chromium-компоузитор, который сбрасывал
        // padding при переключении экранов picker/wizard (сильное изменение
        // высоты DOM триггерило релэйаут, съедавший applied padding).
        // В edge-to-edge (см. setDecorFitsSystemWindows(false) выше) system
        // сам НЕ ужимает окно под клавиатуру — windowSoftInputMode="adjustResize"
        // в этом режиме не действует вообще, только insets-события. Раньше
        // здесь учитывались только systemBars() — клавиатура просто перекрывала
        // строку ввода чата/консоли внизу лога (см. .log-cmd-row, position:
        // fixed внутри .log-overlay), никакой отступ под неё не подставлялся.
        // Берём максимум из системных баров и текущей высоты IME — так нижний
        // отступ растёт, когда клавиатура открыта, и возвращается к обычному
        // (высота навбара) размеру, когда она скрыта.
        ViewCompat.setOnApplyWindowInsetsListener(root) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            val ime = insets.getInsets(WindowInsetsCompat.Type.ime())
            view.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime.bottom))
            insets
        }

        // WebViewAssetLoader вместо голого file:///android_asset/ — нужен, чтобы
        // "instruction"-этап мастера (app.js: container.innerHTML = stage.
        // instruction_html) мог показывать картинки. Они лежат в приватном
        // хранилище приложения (files/instruction_N/images/ синкнутой модели,
        // см. wizard_spec.py) — с origin file:///android_asset/ эти
        // относительные пути были недостижимы вообще (другой корень). Второй
        // path handler ("/data/") отдаёт всё под context.filesDir — оттуда же,
        // где content_sync.py держит cars/.
        // "/assets/" — встроенный интерфейс из APK, а поверх него файлы интерфейса с сервера, если для этой версии
        // приложения скачан проверенный бандл (UiBundleAssets, ui_bundle.py).
        val uiAssets = UiBundleAssets(this)
        val uiRoot = UiBundleAssets.root(this)
        val uiBundle = UiBundleAssets.active(uiRoot, appVersion())
        uiAssets.use(uiBundle?.dir)
        bridge.uiRev = uiBundle?.rev ?: 0
        if (uiBundle != null) {
            Log.i(TAG, "интерфейс с сервера: ${uiBundle.dir.name}")
            bridge.onUiReady = { runOnUiThread { uiWatchdog.removeCallbacksAndMessages(null) } }
            uiWatchdog.postDelayed({ fallbackToBuiltinUi(bridge, uiAssets, uiRoot, uiBundle.rev) },
                UiBundleAssets.UI_READY_TIMEOUT_MS)
        }
        val assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", uiAssets)
            .addPathHandler("/data/", WebViewAssetLoader.InternalStoragePathHandler(this, filesDir))
            .build()
        webView.webViewClient = object : WebViewClientCompat() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                assetLoader.shouldInterceptRequest(request.url)

            // Ссылки на Boosty (приветственное/поздравительное окно, см.
            // app.js) — это наш собственный appassets.androidplatform.net
            // origin, поэтому WebView иначе попытался бы загрузить boosty.to
            // ПРЯМО В WebView. Открываем во внешнем браузере системным
            // Intent'ом вместо этого — обычное ожидание техника от ссылки.
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                if (url.host == "appassets.androidplatform.net") return false
                return try {
                    startActivity(Intent(Intent.ACTION_VIEW, url).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                    true
                } catch (_: ActivityNotFoundException) {
                    false
                }
            }
        }

        webView.loadUrl(INDEX_URL)

        // Всё состояние (открытая модалка, текущий этап мастера, хлебные
        // крошки пикера) живёт в JS — системный жест/кнопка "назад" по
        // умолчанию просто закрывает Activity, что для техника выглядит как
        // "приложение вылетело" на первом же свайпе. Вместо этого спрашиваем
        // JS (window.__handleBackPress, см. app.js), что делать — реально
        // выходим из приложения только когда JS сам говорит "exit" (пикер на
        // верхнем уровне, ни одной модалки/оверлея не открыто).
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                webView.evaluateJavascript(
                    "(window.__handleBackPress ? window.__handleBackPress() : 'exit')"
                ) { rawResult ->
                    val action = rawResult?.trim('"')
                    if (action == "exit") {
                        isEnabled = false
                        onBackPressedDispatcher.onBackPressed()
                        isEnabled = true
                    }
                }
            }
        })
    }

    private fun appVersion(): String = try {
        packageManager.getPackageInfo(packageName, 0).versionName ?: ""
    } catch (_: Exception) {
        ""
    }

    /** Интерфейс с сервера не дошёл до ui_ready (app.js, конец запуска) за минуту — сломан или не подходит этой
     * версии. Выпуск помечается плохим (к нему не вернёмся до следующего), окно открывается со встроенным. */
    private fun fallbackToBuiltinUi(bridge: WebBridge, uiAssets: UiBundleAssets, uiRoot: java.io.File, rev: Int) {
        Log.w(TAG, "интерфейс с сервера (выпуск $rev) не запустился — открываю встроенный")
        Thread {
            try {
                com.chaquo.python.Python.getInstance().getModule("mobile_bridge")
                    .callAttr("ui_bundle_mark_bad", uiRoot.absolutePath, rev)
            } catch (e: Exception) {
                Log.w(TAG, "не удалось пометить интерфейс с сервера плохим", e)
            }
        }.start()
        uiAssets.use(null)
        bridge.uiRev = 0
        bridge.onUiReady = null
        webView.clearCache(true)
        webView.loadUrl(INDEX_URL)
    }

    override fun onDestroy() {
        uiWatchdog.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    override fun onStart() {
        super.onStart()
        webView.evaluateJavascript("(window.__onAppShown && window.__onAppShown())", null)
    }

    override fun onStop() {
        super.onStop()
        // Программу свернули (погас экран, открыли другое приложение). Раньше здесь лог сессии отправлялся и
        // запечатывался — всё, что происходило потом (докачка, запись, итог), на сервер уже не попадало (Belgee S50,
        // 28.09: логи обрывались на «Скачиваю …»). Теперь только строка в журнал: сессия живёт дальше в прочном
        // журнале на диске и уходит целиком, когда закончится, — или при следующем запуске, если систему программу
        // выгрузила (см. app.js: __onAppHidden, InstallLogQueue.recoverStaleCurrent).
        webView.evaluateJavascript("(window.__onAppHidden && window.__onAppHidden(${JSONObject.quote(hiddenReason())}))", null)
    }

    /** Что увело программу с экрана — в строку «Программа свёрнута: …». Раньше строка была общей («погас экран или
     * открыто другое приложение»), и было не понять, что свернуло программу (Belgee S50, лог №1718, 29.09: «само
     * вылетело, ничего не нажимал»). Какое именно приложение открылось, Android не говорит. */
    private fun hiddenReason(): String {
        if (isFinishing) return "окно программы закрыто"
        val power = getSystemService(POWER_SERVICE) as? PowerManager
        if (power != null && !power.isInteractive) return "погас экран"
        val keyguard = getSystemService(KEYGUARD_SERVICE) as? KeyguardManager
        if (keyguard?.isKeyguardLocked == true) return "телефон заблокирован"
        return "открыто другое приложение или нажата «Домой»"
    }
}
