package ru.magicsqd.mobile

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * Обновление программы прямо из неё (владелец, 18.10.2026: «а можем в новую версию встроить чтобы она спрашивала
 * разрешение на установку из неизвестных источников и сама ставила апдейты?»). Раньше «Скачать APK» в окне обновления
 * открывал ссылку в браузере: техник сам скачивал файл, открывал его и давал браузеру разрешение на установку.
 *
 * Разрешение «Установка неизвестных приложений» — один раз и для нашей программы (окно настроек Android, permissionIntent).
 * Обновление: APK в кэш со сверкой sha256 из version.json (download) → это наш пакет, новее установленного и подписан тем
 * же ключом (verify) → сессия PackageInstaller (install). На Android 12+ — без окна подтверждения: программа обновляет
 * сама себя (setRequireUserAction + UPDATE_PACKAGES_WITHOUT_USER_ACTION). На старых, или если Android всё же спросит, —
 * его окно «Обновить приложение?» (UpdateStatusReceiver). Пока ставится новая версия, Android закрывает программу.
 */
object AppSelfUpdate {
    /** Итог сессии установки (UpdateStatusReceiver) — WebBridge передаёт его в окно обновления. */
    @Volatile var onStatus: ((stage: String, message: String) -> Unit)? = null

    /** Программа на экране (MainActivity: onStart/onStop). */
    @Volatile var appVisible = false

    /** Окно подтверждения Android, отложенное до возвращения в программу: техник мог свернуть её, пока качалось
     * обновление, а свёрнутой программе Android 10+ открывать окна не даёт (запрет запуска из фона). */
    @Volatile private var pendingConfirm: Intent? = null

    /** Окно подтверждения Android (UpdateStatusReceiver): сразу, если программа на экране, иначе — когда в неё вернутся. */
    fun confirm(context: Context, intent: Intent) {
        if (appVisible) context.startActivity(intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) else pendingConfirm = intent
    }

    /** MainActivity.onStart: открыть отложенное окно подтверждения. */
    fun showPendingConfirm(activity: Activity) {
        val intent = pendingConfirm ?: return
        pendingConfirm = null
        try {
            activity.startActivity(intent)
        } catch (e: Exception) {
            onStatus?.invoke("error", confirmFailed(e))
        }
    }

    fun confirmFailed(e: Exception) = "не открылось окно подтверждения Android (${e.message ?: e.javaClass.simpleName})"

    /** Новая версия встала — открыть программу снова (её закрыли на время установки). Android 10+ из фона не даст —
     * тогда техник откроет её сам, как и просит окно обновления. */
    fun reopen(context: Context) {
        if (appVisible) return
        val launch = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: return
        try {
            context.startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (_: Exception) { }
    }

    fun canInstall(context: Context): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.O || context.packageManager.canRequestPackageInstalls()

    /** Android 12+ ставит обновление программы самой себе без окна подтверждения. */
    fun silentPossible(): Boolean = Build.VERSION.SDK_INT >= Build.VERSION_CODES.S

    fun permissionIntent(context: Context): Intent =
        Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:${context.packageName}"))

    /** APK обновления в кэш программы; sha256 (если сервер его дал) обязан совпасть. Ошибка — текстом для техника. */
    fun download(context: Context, url: String, sha256: String?, onProgress: (done: Long, total: Long) -> Unit): File {
        val dir = File(context.cacheDir, "update").apply { mkdirs() }
        dir.listFiles()?.forEach { it.delete() }  // недокачанное с прошлого раза
        val target = File(dir, "update.apk")
        val connection = (URL(url).openConnection() as HttpURLConnection).apply {
            connectTimeout = 15_000
            readTimeout = 30_000
            instanceFollowRedirects = true  // GitHub отдаёт файл переадресацией
        }
        try {
            val code = connection.responseCode
            if (code != HttpURLConnection.HTTP_OK) throw IOException("сервер ответил $code")
            val total = connection.contentLengthLong
            val digest = MessageDigest.getInstance("SHA-256")
            var done = 0L
            connection.inputStream.use { input ->
                target.outputStream().use { output ->
                    val buffer = ByteArray(256 * 1024)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        output.write(buffer, 0, read)
                        digest.update(buffer, 0, read)
                        done += read
                        onProgress(done, total)
                    }
                }
            }
            if (total > 0 && done != total) throw IOException("скачано $done из $total байт — соединение оборвалось")
            val actual = digest.digest().joinToString("") { "%02x".format(it) }
            if (!sha256.isNullOrBlank() && !actual.equals(sha256.trim(), ignoreCase = true)) {
                throw IOException("файл повредился при скачивании (контрольная сумма не совпала)")
            }
            return target
        } catch (e: Exception) {
            target.delete()
            throw e
        } finally {
            connection.disconnect()
        }
    }

    /** Почему этот файл не ставить, или null: в нём наша программа, новее установленной и подписана тем же ключом
     * (Android и сам не обновит программу файлом с чужой подписью — проверяем заранее, чтобы сказать понятно). */
    fun verify(context: Context, apk: File): String? {
        val pm = context.packageManager
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            PackageManager.GET_SIGNING_CERTIFICATES
        } else {
            @Suppress("DEPRECATION") PackageManager.GET_SIGNATURES
        }
        // Android 9 читает подпись файла только с GET_SIGNATURES: с одним GET_SIGNING_CERTIFICATES signingInfo == null
        // и любой файл выглядел бы «подписанным другим ключом» (проверено на эмуляторе 9, 18.10.2026).
        @Suppress("DEPRECATION")
        val archiveFlags = if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) flags or PackageManager.GET_SIGNATURES else flags
        val archive = pm.getPackageArchiveInfo(apk.path, archiveFlags) ?: return "файл обновления не читается как приложение"
        if (archive.packageName != context.packageName) return "в файле другое приложение (${archive.packageName})"
        val installed = pm.getPackageInfo(context.packageName, flags)
        if (versionCode(archive) <= versionCode(installed)) return "в файле не новая версия (${archive.versionName})"
        val signers = signers(archive)
        if (signers.isEmpty() || signers != signers(installed)) return "файл подписан другим ключом"
        return null
    }

    private fun versionCode(info: PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) info.longVersionCode else @Suppress("DEPRECATION") info.versionCode.toLong()

    private fun signers(info: PackageInfo): Set<String> {
        val raw = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.signingInfo?.apkContentsSigners  // текущая подпись, без прошлых ключей ротации
        } else {
            @Suppress("DEPRECATION") info.signatures
        }
        val sha = MessageDigest.getInstance("SHA-256")
        return raw.orEmpty().map { signature -> sha.digest(signature.toByteArray()).joinToString("") { "%02x".format(it) } }
            .toSet()
    }

    /** Поставить скачанный APK сессией PackageInstaller; итог придёт в UpdateStatusReceiver. */
    fun install(context: Context, apk: File) {
        val installer = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) setInstallReason(PackageManager.INSTALL_REASON_USER)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        val sessionId = installer.createSession(params)
        try {
            installer.openSession(sessionId).use { session ->
                apk.inputStream().use { input ->
                    session.openWrite("base.apk", 0, apk.length()).use { output ->
                        input.copyTo(output, 256 * 1024)
                        session.fsync(output)
                    }
                }
                val intent = Intent(context, UpdateStatusReceiver::class.java)
                // FLAG_MUTABLE: итог (EXTRA_STATUS, окно подтверждения) Android дописывает в этот интент сам.
                val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                    (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)
                session.commit(PendingIntent.getBroadcast(context, sessionId, intent, flags).intentSender)
            }
        } catch (e: Exception) {
            try { installer.abandonSession(sessionId) } catch (_: Exception) { }
            throw e
        }
    }

    fun statusText(status: Int, message: String?): String {
        val reason = when (status) {
            PackageInstaller.STATUS_FAILURE_ABORTED -> "установку отменили"
            PackageInstaller.STATUS_FAILURE_BLOCKED -> "Android запретил установку"
            PackageInstaller.STATUS_FAILURE_CONFLICT -> "мешает уже установленная версия"
            PackageInstaller.STATUS_FAILURE_INCOMPATIBLE -> "версия не подходит этому телефону"
            PackageInstaller.STATUS_FAILURE_INVALID -> "файл обновления повреждён"
            PackageInstaller.STATUS_FAILURE_STORAGE -> "на телефоне не хватает места"
            else -> "Android не установил обновление"
        }
        // «Отмена» в окне подтверждения — понятно и без текста Android («INSTALL_FAILED_ABORTED: User rejected permissions»).
        return if (message.isNullOrBlank() || status == PackageInstaller.STATUS_FAILURE_ABORTED) reason else "$reason ($message)"
    }
}

/** Итог сессии установки обновления (AppSelfUpdate.install): окно подтверждения Android — показать, ошибку — в окно
 * обновления. Успех приходит уже в новую версию (старую Android закрывает на время установки) — она открывается снова. */
class UpdateStatusReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
            PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                val confirm = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION") intent.getParcelableExtra(Intent.EXTRA_INTENT)
                }
                if (confirm != null) {
                    try {
                        AppSelfUpdate.confirm(context, confirm)
                        AppSelfUpdate.onStatus?.invoke("confirm", "")
                    } catch (e: Exception) {
                        AppSelfUpdate.onStatus?.invoke("error", AppSelfUpdate.confirmFailed(e))
                    }
                } else {
                    AppSelfUpdate.onStatus?.invoke("error", "Android не показал окно подтверждения")
                }
            }
            PackageInstaller.STATUS_SUCCESS -> {
                AppSelfUpdate.onStatus?.invoke("done", "")
                AppSelfUpdate.reopen(context)
            }
            else -> AppSelfUpdate.onStatus?.invoke("error",
                AppSelfUpdate.statusText(status, intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE)))
        }
    }
}
