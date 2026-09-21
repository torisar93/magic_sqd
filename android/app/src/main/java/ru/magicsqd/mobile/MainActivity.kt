package ru.magicsqd.mobile

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.util.Log
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewClientCompat

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
        val assetLoader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
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

        webView.loadUrl("https://appassets.androidplatform.net/assets/index.html")

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

    override fun onStop() {
        super.onStop()
        // Лучшее из возможного на случай, если техник свернул/закрыл
        // приложение, не долистав мастер до конца и не нажав "Назад" явно
        // (window.__handleBackPress выше ловит только явный уход) — WebView
        // ещё жив на onStop (в отличие от полного убийства процесса системой,
        // которое поймать вообще нечем), так что JS успевает отправить лог
        // сессии как брошенной, если было что слать (см. app.js:
        // flushSessionLog/window.__flushInstallLogOnStop).
        webView.evaluateJavascript(
            "(window.__flushInstallLogOnStop && window.__flushInstallLogOnStop())", null
        )
    }
}
