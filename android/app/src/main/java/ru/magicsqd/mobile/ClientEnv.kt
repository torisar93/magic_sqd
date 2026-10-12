package ru.magicsqd.mobile

import android.content.Context
import android.view.inputmethod.InputMethodManager
import org.json.JSONArray
import org.json.JSONObject
import java.util.Locale
import java.util.TimeZone

/**
 * Признаки системы для правила доступа по региону (владелец, 12.10.2026: бесплатно — наши языки и часовые пояса,
 * остальным — по подписке; «чтобы VPN не помогал»; server/access_policy.py). Уходят в запрос токена сборки
 * (python/app_token.py, catalog_setup.configure) и вшиваются сервером в подписанный токен. ПК — app/client_env.py.
 *
 * {"langs": [языки телефона], "kbd": [языки клавиатур], "region": локаль, "tz": минуты к UTC, "os": "android"}.
 * Языки клавиатуры важны: у многих интерфейс телефона английский, но русская клавиатура стоит почти у всех.
 */
object ClientEnv {
    /** Наши языки — интерфейс приложения на русском (server/access_policy.DEFAULT_FREE_LANGS — те же коды). */
    private val RUSSIAN_UI = setOf("ru", "uk", "be", "kk", "uz", "ky", "tg", "tk", "hy", "az", "ka", "ro", "mo")

    fun collect(context: Context): JSONObject {
        val langs = JSONArray()
        val seen = mutableSetOf<String>()
        try {
            val locales = context.resources.configuration.locales
            for (i in 0 until locales.size()) {
                val tag = locales.get(i).toLanguageTag()
                if (seen.add(tag)) langs.put(tag)
            }
        } catch (_: Exception) { /* без языков — сервер судит по адресу */ }
        val kbd = JSONArray()
        try {
            val imm = context.getSystemService(Context.INPUT_METHOD_SERVICE) as? InputMethodManager
            val kbdSeen = mutableSetOf<String>()
            imm?.enabledInputMethodList?.forEach { info ->
                imm.getEnabledInputMethodSubtypeList(info, true).forEach { subtype ->
                    val tag = subtype.languageTag.ifBlank { @Suppress("DEPRECATION") subtype.locale }
                    if (tag.isNotBlank() && kbdSeen.add(tag)) kbd.put(tag)
                }
            }
        } catch (_: Exception) { /* клавиатуры не узнать — достаточно языков телефона */ }
        val tz = TimeZone.getDefault().getOffset(System.currentTimeMillis()) / 60_000
        return JSONObject().put("langs", langs).put("kbd", kbd).put("region", Locale.getDefault().toLanguageTag())
            .put("tz", tz).put("os", "android")
    }

    /** Язык интерфейса приложения: есть наш язык (телефон, клавиатура, локаль) — русский, иначе английский.
     *  Ничего не определилось — русский, как было всегда (английский только по явным признакам). */
    fun uiLanguage(env: JSONObject): String {
        val tags = mutableListOf<String>()
        listOf("langs", "kbd").forEach { key ->
            val items = env.optJSONArray(key) ?: return@forEach
            for (i in 0 until items.length()) tags.add(items.optString(i))
        }
        tags.add(env.optString("region"))
        val codes = tags.map { it.split('-', '_').first().lowercase() }.filter { it.isNotBlank() }.toSet()
        if (codes.isEmpty()) return "ru"
        return if (codes.any { it in RUSSIAN_UI }) "ru" else "en"
    }
}
