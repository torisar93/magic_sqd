package ru.magicsqd.mobile.usb

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import java.net.Inet4Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.Socket
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Реальный список устройств локальной подсети (IPv4) — на Android нет
 * доступа к ARP-таблице без root, но ICMP ping (java InetAddress.
 * isReachable) работает без root (ядро Linux разрешает "ping sockets" для
 * обычных UID через net.ipv4.ping_group_range, на этом и держатся все
 * сетевые сканеры вроде PingTools). Список "кто ответил на ping" — это и
 * есть устройства в сети технику, а НЕ "кто держит порт 5555/23 открытым":
 * последнее (см. scanSubnetForPort) — лишь подсказка, какой из найденных
 * хостов вероятнее всего магнитола, а не единственный критерий попадания в
 * список (это и было багом первой версии скана).
 */
object NetworkScan {

    /** cm.activeNetwork — это сеть с выходом в интернет по умолчанию, а
     * точка доступа магнитолы обычно БЕЗ интернета: телефон к ней
     * подключается, но система не переключает "активную" сеть на неё, если
     * рядом жив мобильный интернет — тогда activeNetwork() возвращает
     * сотовую сеть, а не Wi-Fi магнитолы, и весь скан идёт не по той
     * подсети (реальная причина "ничего не находит", хотя устройства в сети
     * есть). Поэтому ищем именно Wi-Fi-транспорт среди ВСЕХ сетей, а не
     * полагаемся на "активную". */
    fun wifiNetwork(context: Context): android.net.Network? {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return null
        return cm.allNetworks.firstOrNull { network ->
            cm.getNetworkCapabilities(network)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) == true
        }
    }

    /** Собственный IPv4 + длина префикса Wi-Fi подключения (к точке доступа
     * магнитолы или её собственной сети) — см. wifiNetwork. */
    private fun getLocalIPv4Subnet(context: Context): Pair<String, Int>? {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager ?: return null
        val network = wifiNetwork(context) ?: return null
        val linkProperties = cm.getLinkProperties(network) ?: return null
        for (la in linkProperties.linkAddresses) {
            val addr = la.address
            if (addr is Inet4Address && !addr.isLoopbackAddress) {
                return (addr.hostAddress ?: continue) to la.prefixLength
            }
        }
        return null
    }

    /** Все адреса подсети (без своего) — не крупнее /24, как и на desktop
     * (см. cars/_shared/wifi_adb.py:scan_for_adb_hosts): сети точек доступа/
     * хотспотов таких магнитол почти всегда /24 и меньше, а сканировать
     * больше — слишком долго. */
    private fun subnetHosts(context: Context): Pair<String, List<String>>? {
        val (ownIp, prefixLen) = getLocalIPv4Subnet(context) ?: return null
        val prefix = maxOf(prefixLen, 24)
        val ownParts = ownIp.split(".").map { it.toIntOrNull() ?: return null }
        if (ownParts.size != 4) return null
        val ownInt = (ownParts[0] shl 24) or (ownParts[1] shl 16) or (ownParts[2] shl 8) or ownParts[3]
        val maskBits = 32 - prefix
        if (maskBits <= 0 || maskBits > 8) return null
        val networkInt = (ownInt shr maskBits) shl maskBits
        val hostCount = (1 shl maskBits) - 2
        if (hostCount <= 0) return null
        val hosts = (1..hostCount).map { i ->
            val hostInt = networkInt + i
            "${(hostInt shr 24) and 0xFF}.${(hostInt shr 16) and 0xFF}.${(hostInt shr 8) and 0xFF}.${hostInt and 0xFF}"
        }.filter { it != ownIp }
        return ownIp to hosts
    }

    private fun <T> parallelForEach(items: List<T>, action: (T) -> Unit) {
        val executor = Executors.newFixedThreadPool(minOf(64, items.size.coerceAtLeast(1)))
        val latch = CountDownLatch(items.size)
        for (item in items) {
            executor.submit {
                try { action(item) } finally { latch.countDown() }
            }
        }
        latch.await(30, TimeUnit.SECONDS)
        executor.shutdownNow()
    }

    private fun sortedByIp(ips: Collection<String>) =
        ips.distinct().sortedBy { ip -> ip.split(".").fold(0L) { acc, part -> acc * 256 + (part.toIntOrNull() ?: 0) } }

    /** ICMP ping-развёртка подсети — реальный список живых хостов, не
     * зависящий от того, какой порт у них открыт. */
    fun pingSweep(context: Context, timeoutMs: Int = 400): List<String> {
        val (_, hosts) = subnetHosts(context) ?: return emptyList()
        val found = Collections.synchronizedList(mutableListOf<String>())
        parallelForEach(hosts) { ip ->
            try {
                if (InetAddress.getByName(ip).isReachable(timeoutMs)) found.add(ip)
            } catch (_: Exception) {
            }
        }
        return sortedByIp(found)
    }

    /** Кто из подсети держит открытым конкретный port (обычно 5555 — Wi-Fi
     * ADB) — некоторые хосты фильтруют ICMP, но отвечают на TCP-connect, так
     * что это дополняет pingSweep, а не заменяет: используется как
     * ОБЪЕДИНЕНИЕ (см. WebBridge.scanHosts), не как единственный фильтр. */
    fun scanSubnetForPort(context: Context, port: Int, timeoutMs: Int = 300): List<String> {
        val (_, hosts) = subnetHosts(context) ?: return emptyList()
        val found = Collections.synchronizedList(mutableListOf<String>())
        parallelForEach(hosts) { ip ->
            try {
                Socket().use { s -> s.connect(InetSocketAddress(ip, port), timeoutMs); found.add(ip) }
            } catch (_: Exception) {
            }
        }
        return sortedByIp(found)
    }
}
