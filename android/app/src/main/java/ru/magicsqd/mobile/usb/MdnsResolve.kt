package ru.magicsqd.mobile.usb

import android.content.Context
import android.net.ConnectivityManager
import android.net.wifi.WifiManager
import java.io.ByteArrayOutputStream
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet6Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.MulticastSocket
import java.net.NetworkInterface
import java.net.SocketTimeoutException

/**
 * Одноразовый mDNS-запрос имени "android.local" (RFC 6762) — на магнитолах
 * с открытым Android (например Geely CityRay) это устойчивое системное
 * mDNS-имя, которое техник реально использует руками через Termux
 * ("telnet android.local", либо "ping6 android.local" + "telnet
 * ipv6%wlan0", если голый telnet не задался) — надёжнее произвольного скана
 * подсети/соседей, поэтому это ОСНОВНОЙ способ подсказать адрес магнитолы
 * автоматически (см. promptHostPicker в app.js — этот результат
 * подсвечивается как рекомендованный, скан порта — просто список остальных
 * кандидатов).
 *
 * QU-бит в вопросе (unicast-response, см. RFC 6762 §5.4) — просим ответчика
 * прислать ответ НАМ напрямую, без вступления в multicast-группу ради
 * приёма (проще и быстрее одноразового резолва).
 */
object MdnsResolve {
    private const val MDNS_PORT = 5353
    private const val QUERY_NAME = "android.local"
    private const val TYPE_A = 1
    private const val TYPE_PTR = 12
    private const val TYPE_AAAA = 28

    data class Result(val ipv4: String?, val ipv6: String?)

    fun resolveAndroidLocal(context: Context, timeoutMs: Int = 1200): Result {
        val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val lock = wifi?.createMulticastLock("magicsqd-mdns")?.apply { setReferenceCounted(true) }
        lock?.acquire()
        return try {
            val iface = activeNetworkInterface(context)
            val ipv4 = tryQuery(context, TYPE_A, InetAddress.getByName("224.0.0.251"), null, timeoutMs)
            val ipv6Raw = tryQuery(context, TYPE_AAAA, InetAddress.getByName("ff02::fb"), iface, timeoutMs)
            // fe80::... линк-локальный — без zone id ("%wlan0") сокет на
            // конкретный интерфейс не подключить (см. TelnetAdb.kt), сразу
            // подставляем сюда, чтобы промптHostPicker мог отдать готовый
            // к использованию адрес.
            val ipv6 = if (ipv6Raw != null && ipv6Raw.startsWith("fe80") && iface != null && "%" !in ipv6Raw) {
                "$ipv6Raw%${iface.name}"
            } else ipv6Raw
            Result(ipv4, ipv6)
        } finally {
            try { lock?.release() } catch (_: Exception) {}
        }
    }

    /** РЕАЛЬНЫЙ список IPv6-соседей в сети (не одно конкретное имя) — на
     * Android нет доступа к NDP-таблице без root (см. TelnetAdb.kt), поэтому
     * вместо неё: вступаем в mDNS-группу (ff02::fb) и слушаем окно
     * windowMs, собирая source-адрес КАЖДОГО увиденного mDNS-пакета —
     * запросов и ответов, от кого угодно, не только от "android.local".
     * Дополнительно сами шлём стандартный (НЕ unicast-response) DNS-SD
     * мета-запрос "какие сервисы есть в сети" ("_services._dns-sd._udp.local"
     * PTR) — он теоретически может расшевелить респондеров, которые сами
     * ничего не анонсируют, но исправно отвечают на настоящие mDNS-запросы.
     * Даже без парсинга ответа сам факт "кто-то ответил с этого IP" —
     * legitimate сигнал "это активное устройство в сети", ровно то, что
     * просил техник ("список устройств", а не скан по шаблону). */
    fun scanIpv6Neighbors(context: Context, windowMs: Int = 2500): List<String> {
        val wifi = context.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        val lock = wifi?.createMulticastLock("magicsqd-mdns-scan")?.apply { setReferenceCounted(true) }
        lock?.acquire()
        return try {
            val iface = activeNetworkInterface(context) ?: return emptyList()
            MulticastSocket(MDNS_PORT).use { socket ->
                socket.reuseAddress = true
                // См. NetworkScan.bindToWifi — networkInterface ниже выбирает
                // исходящий интерфейс для multicast-пакетов, но при активном
                // VPN этого недостаточно: явная привязка к Network нужна на
                // уровне политики маршрутизации Android, а не только сокета.
                NetworkScan.wifiNetwork(context)?.bindSocket(socket)
                socket.networkInterface = iface
                val groupAddr = InetSocketAddress(
                    Inet6Address.getByAddress(null, InetAddress.getByName("ff02::fb").address, iface), MDNS_PORT
                )
                try {
                    socket.joinGroup(groupAddr, iface)
                } catch (e: Exception) {
                    return emptyList()
                }
                try {
                    val query = buildQuery("_services._dns-sd._udp.local", TYPE_PTR, quBit = false)
                    socket.send(DatagramPacket(query, query.size, groupAddr))
                } catch (_: Exception) {
                }
                val found = LinkedHashSet<String>()
                socket.soTimeout = 400
                val deadline = System.currentTimeMillis() + windowMs
                val buf = ByteArray(2048)
                while (System.currentTimeMillis() < deadline) {
                    try {
                        val packet = DatagramPacket(buf, buf.size)
                        socket.receive(packet)
                        val addr = packet.address
                        if (addr is Inet6Address) addr.hostAddress?.let { found.add(it) }
                    } catch (_: SocketTimeoutException) {
                        // просто продолжаем ждать до дедлайна
                    } catch (_: Exception) {
                        break
                    }
                }
                try { socket.leaveGroup(groupAddr, iface) } catch (_: Exception) {}
                found.toList()
            }
        } catch (_: Exception) {
            emptyList()
        } finally {
            try { lock?.release() } catch (_: Exception) {}
        }
    }

    /** cm.activeNetwork не годится тут же, что и в NetworkScan.kt —
     * см. NetworkScan.wifiNetwork (точка доступа магнитолы обычно без
     * интернета, поэтому "активной" считается сотовая, а не Wi-Fi). */
    private fun activeNetworkInterface(context: Context): NetworkInterface? {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return null
        val network = NetworkScan.wifiNetwork(context) ?: return null
        val ifaceName = cm.getLinkProperties(network)?.interfaceName ?: return null
        return try { NetworkInterface.getByName(ifaceName) } catch (_: Exception) { null }
    }

    private fun tryQuery(context: Context, qtype: Int, mcastAddr: InetAddress, iface: NetworkInterface?, timeoutMs: Int): String? {
        return try {
            DatagramSocket().use { socket ->
                // См. NetworkScan.bindToWifi — без явной привязки к Wi-Fi-сети
                // активный VPN на телефоне (даже "по приложениям", даже если
                // Magic SQD не включена в него) может увести этот multicast-
                // запрос в свой tun вместо локальной подсети магнитолы.
                NetworkScan.wifiNetwork(context)?.bindSocket(socket)
                socket.soTimeout = timeoutMs
                val dest = if (mcastAddr is Inet6Address && iface != null) {
                    Inet6Address.getByAddress(null, mcastAddr.address, iface)
                } else mcastAddr
                val query = buildQuery(QUERY_NAME, qtype)
                socket.send(DatagramPacket(query, query.size, InetSocketAddress(dest, MDNS_PORT)))
                val deadline = System.currentTimeMillis() + timeoutMs
                while (System.currentTimeMillis() < deadline) {
                    val buf = ByteArray(2048)
                    val packet = DatagramPacket(buf, buf.size)
                    socket.receive(packet) // бросает SocketTimeoutException по истечении soTimeout
                    val ip = parseResponse(buf, packet.length, qtype)
                    if (ip != null) return ip
                }
                null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun buildQuery(name: String, qtype: Int, quBit: Boolean = true): ByteArray {
        val out = ByteArrayOutputStream()
        fun u16(v: Int) { out.write((v shr 8) and 0xFF); out.write(v and 0xFF) }
        u16(0) // ID
        u16(0) // FLAGS — стандартный запрос
        u16(1) // QDCOUNT
        u16(0); u16(0); u16(0) // AN/NS/AR COUNT
        for (label in name.split(".")) {
            out.write(label.length)
            out.write(label.toByteArray(Charsets.US_ASCII))
        }
        out.write(0)
        u16(qtype)
        // QCLASS IN (0x0001) | опционально QU-бит (0x8000, unicast-response,
        // см. RFC 6762 §5.4) — для одноразового резолва имени просим ответ
        // напрямую нам; для группового скана (scanIpv6Neighbors) — обычный
        // multicast-запрос, чтобы ответы увидели/сравнили другие в сети тоже
        // и мы сами слушали именно multicast-трафик, а не ждали unicast.
        u16(if (quBit) 0x8001 else 0x0001)
        return out.toByteArray()
    }

    /** Разбирает DNS-ответ, ищет первую RR нужного типа — для одноразового
     * unicast-ответа на наш же запрос полная валидация имени не нужна. */
    private fun parseResponse(buf: ByteArray, length: Int, qtype: Int): String? {
        if (length < 12) return null
        val qdcount = u16At(buf, 4)
        val ancount = u16At(buf, 6)
        if (ancount <= 0) return null
        var pos = 12
        repeat(qdcount) { pos = skipName(buf, pos) + 4 } // +QTYPE(2)+QCLASS(2)
        repeat(ancount) {
            pos = skipName(buf, pos)
            if (pos + 10 > length) return null
            val rtype = u16At(buf, pos)
            val rdlength = u16At(buf, pos + 8)
            val rdataStart = pos + 10
            if (rtype == qtype) {
                val expectedLen = if (qtype == TYPE_A) 4 else 16
                if (rdlength == expectedLen && rdataStart + rdlength <= length) {
                    val addrBytes = buf.copyOfRange(rdataStart, rdataStart + rdlength)
                    return try { InetAddress.getByAddress(addrBytes).hostAddress } catch (_: Exception) { null }
                }
            }
            pos = rdataStart + rdlength
        }
        return null
    }

    private fun u16At(buf: ByteArray, offset: Int): Int =
        ((buf[offset].toInt() and 0xFF) shl 8) or (buf[offset + 1].toInt() and 0xFF)

    /** Имя может быть меткам или DNS-компрессией (указатель 0xC0xx) — для
     * одноразового ответа хватает поддержать оба варианта. */
    private fun skipName(buf: ByteArray, start: Int): Int {
        var pos = start
        while (pos < buf.size) {
            val len = buf[pos].toInt() and 0xFF
            if (len == 0) return pos + 1
            if (len and 0xC0 == 0xC0) return pos + 2 // указатель — сам по себе 2 байта
            pos += 1 + len
        }
        return pos
    }
}
