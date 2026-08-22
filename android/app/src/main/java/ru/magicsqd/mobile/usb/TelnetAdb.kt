package ru.magicsqd.mobile.usb

import java.net.InetSocketAddress
import java.net.Socket

/**
 * Порт cars/_shared/telnet_adb.py:enable_adb_via_telnet — на некоторых
 * магнитолах (например Geely CityRay) ADB-отладка изначально скрыта и
 * включается голой telnet-командой (обычно
 * "setprop persist.service.adb.button.visible ON"), а не через меню.
 * НЕ ADB-протокол — простой текстовый TCP, поэтому не через AdbTransport.
 *
 * Автопоиск IPv6-соседа (desktop: scan_ipv6_neighbors через PowerShell
 * Get-NetNeighbor) НЕ портирован — на Android нет доступа к NDP-таблице без
 * root. Вместо этого — только тот же запасной путь, что и на desktop, когда
 * скан ничего не нашёл: технику самому ввести адрес (см. app.js "#ask"-подобный
 * ввод перед telnet-этапом).
 */
sealed class TelnetResult {
    object Success : TelnetResult()
    data class Failed(val reason: String) : TelnetResult()
}

fun enableAdbViaTelnet(
    host: String,
    command: String = "setprop persist.service.adb.button.visible ON",
    port: Int = 23,
    timeoutMs: Int = 10000,
    log: (String) -> Unit,
): TelnetResult {
    val trimmedHost = host.trim()
    if (trimmedHost.isEmpty()) return TelnetResult.Failed("Не указан адрес магнитолы")

    log("Подключаюсь по telnet к [$trimmedHost]:$port")
    return try {
        Socket().use { socket ->
            socket.connect(InetSocketAddress(trimmedHost, port), timeoutMs)
            socket.soTimeout = timeoutMs
            Thread.sleep(500)
            drain(socket)
            socket.getOutputStream().apply {
                write((command + "\r\n").toByteArray(Charsets.US_ASCII))
                flush()
            }
            Thread.sleep(500)
            drain(socket)
        }
        log("Команда отправлена. Кнопка включения ADB должна появиться в настройках Android на магнитоле.")
        TelnetResult.Success
    } catch (e: Exception) {
        TelnetResult.Failed("Не удалось подключиться по telnet к [$trimmedHost]:$port: ${e.javaClass.simpleName}: ${e.message}")
    }
}

/** Вычитывает то, что телнет-демон успел прислать (баннер/эхо) — не разбираем, просто не оставляем висеть в буфере. */
private fun drain(socket: Socket) {
    try {
        val buf = ByteArray(4096)
        socket.soTimeout = 500
        socket.getInputStream().read(buf)
    } catch (_: Exception) {
    }
}
