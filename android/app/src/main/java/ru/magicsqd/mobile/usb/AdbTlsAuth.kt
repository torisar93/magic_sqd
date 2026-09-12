package ru.magicsqd.mobile.usb

import android.content.Context
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.io.File
import java.math.BigInteger
import java.security.KeyPair
import java.security.Signature
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import javax.net.ssl.KeyManager
import javax.net.ssl.X509TrustManager

/**
 * Самоподписанный X.509-сертификат для TLS-ADB (STLS, см. AdbProtocol.A_STLS
 * в UsbAdbTransport.kt), построенный вручную (без BouncyCastle — та же
 * причина, что и в AdbAuth.kt: не тащить лишнюю зависимость ради одной
 * операции).
 *
 * Ключевой факт, ПРОВЕРЕННЫЙ на практике (Python-прототип против двух живых
 * телефонов, Android 11 и Android 16, оба через штатную "Беспроводную
 * отладку"): TLS-ADB (_adb-tls-connect._tcp) проверяет клиентский
 * сертификат по ТОЙ ЖЕ базе доверенных ключей, что и classic USB AUTH — то
 * есть сопряжение по SPAKE2/коду не нужно, если этот же RSA-ключ уже был
 * одобрен через обычный диалог "Разрешить отладку" (см. AdbAuth.loadOrCreateKeyPair
 * / performAuth в UsbAdbTransport.kt). Незнакомый ключ TLS-хендшейк
 * ПРОХОДИТ (крипто-уровень отработал), но устройство сразу после этого
 * рвёт соединение алертом certificate_unknown — БЕЗ какого-либо диалога на
 * экране. Поэтому мы переиспользуем ОДИН И ТОТ ЖЕ RSA-ключ (AdbAuth) и для
 * classic AUTH, и для TLS — первое успешное USB-подключение с тапом
 * "Разрешить" делает и последующие Wi-Fi/TLS-подключения бесшовными.
 */
object AdbTlsAuth {
    private object Der {
        fun length(len: Int): ByteArray {
            if (len < 0x80) return byteArrayOf(len.toByte())
            var l = len
            val out = ArrayList<Byte>()
            while (l > 0) {
                out.add(0, (l and 0xFF).toByte())
                l = l ushr 8
            }
            return byteArrayOf((0x80 or out.size).toByte()) + out.toByteArray()
        }

        fun tlv(tag: Int, content: ByteArray): ByteArray =
            byteArrayOf(tag.toByte()) + length(content.size) + content

        fun concat(vararg parts: ByteArray): ByteArray {
            val out = ByteArrayOutputStream()
            parts.forEach { out.write(it) }
            return out.toByteArray()
        }

        fun sequence(vararg parts: ByteArray): ByteArray = tlv(0x30, concat(*parts))
        fun set(vararg parts: ByteArray): ByteArray = tlv(0x31, concat(*parts))

        fun integer(value: BigInteger): ByteArray = tlv(0x02, value.toByteArray())

        fun oid(dotted: String): ByteArray {
            val parts = dotted.split(".").map { it.toInt() }
            val out = ByteArrayOutputStream()
            out.write(parts[0] * 40 + parts[1])
            for (i in 2 until parts.size) {
                var v = parts[i]
                if (v == 0) {
                    out.write(0)
                    continue
                }
                val chunks = ArrayList<Int>()
                while (v > 0) {
                    chunks.add(0, v and 0x7F)
                    v = v ushr 7
                }
                for (j in 0 until chunks.size - 1) out.write(chunks[j] or 0x80)
                out.write(chunks.last())
            }
            return tlv(0x06, out.toByteArray())
        }

        fun nullValue(): ByteArray = byteArrayOf(0x05, 0x00)
        fun utf8String(s: String): ByteArray = tlv(0x0c, s.toByteArray(Charsets.UTF_8))
        fun bitString(bytes: ByteArray): ByteArray = tlv(0x03, byteArrayOf(0x00) + bytes)
        fun explicit(tagNum: Int, content: ByteArray): ByteArray = tlv(0xA0 or tagNum, content)

        fun utcTime(date: Date): ByteArray {
            val fmt = SimpleDateFormat("yyMMddHHmmss'Z'", Locale.US)
            fmt.timeZone = TimeZone.getTimeZone("UTC")
            return tlv(0x17, fmt.format(date).toByteArray(Charsets.US_ASCII))
        }
    }

    private const val OID_SHA256_WITH_RSA = "1.2.840.113549.1.1.11"
    private const val OID_COMMON_NAME = "2.5.4.3"

    private fun buildName(cn: String): ByteArray {
        val attr = Der.sequence(Der.oid(OID_COMMON_NAME), Der.utf8String(cn))
        return Der.sequence(Der.set(attr))
    }

    /**
     * Строит и подписывает самоподписанный сертификат для переданного ключа.
     * subjectPublicKeyInfo берём готовым из `keyPair.public.encoded` — это
     * штатный Java API уже отдаёт ровно нужный DER (SubjectPublicKeyInfo),
     * руками кодировать RSA-модуль/экспоненту не нужно.
     */
    fun buildSelfSignedCertificate(keyPair: KeyPair, commonName: String = "magicsqd-mobile"): X509Certificate {
        val notBefore = Date(System.currentTimeMillis() - 24L * 3600 * 1000)
        val notAfter = Date(System.currentTimeMillis() + 20L * 365 * 24 * 3600 * 1000)
        val serial = BigInteger(64, java.security.SecureRandom())
        val sigAlgDer = Der.sequence(Der.oid(OID_SHA256_WITH_RSA), Der.nullValue())
        val name = buildName(commonName)

        val tbsCertificate = Der.sequence(
            Der.explicit(0, Der.integer(BigInteger.valueOf(2))), // version v3
            Der.integer(serial),
            sigAlgDer,
            name, // issuer == subject (самоподписанный)
            Der.sequence(Der.utcTime(notBefore), Der.utcTime(notAfter)),
            name,
            keyPair.public.encoded,
        )

        val signature = Signature.getInstance("SHA256withRSA").apply {
            initSign(keyPair.private)
            update(tbsCertificate)
        }.sign()

        val certificateDer = Der.sequence(tbsCertificate, sigAlgDer, Der.bitString(signature))
        val cf = CertificateFactory.getInstance("X.509")
        return cf.generateCertificate(ByteArrayInputStream(certificateDer)) as X509Certificate
    }

    /**
     * Персистентный сертификат — та же логика, что и AdbAuth.loadOrCreateKeyPair
     * для ключа, и по той же причине критично важная: buildSelfSignedCertificate
     * каждый раз кладёт НОВЫЙ случайный serial и notBefore/notAfter, то есть
     * даёт байт-в-байт РАЗНЫЙ сертификат при каждом вызове, даже с тем же
     * RSA-ключом внутри. На реальной магнитоле (Geely, Android 11) это ломало
     * повторное подключение: первый Wi-Fi/TLS коннект прошёл (доверие
     * подтвердили на экране), но следующий — с новым сертификатом — TLS-
     * хендшейк отработал (крипто-уровень успешен), а сразу после этого
     * устройство молча оборвало соединение при повторном CNXN внутри TLS
     * (ровно симптом "неизвестный сертификат", см. комментарий выше и в
     * performTlsUpgrade). Похоже, у этой прошивки доверие к TLS-ADB привязано
     * к отпечатку ИМЕННО сертификата, а не только к обёрнутому в нём
     * RSA-ключу (в отличие от двух телефонов, на которых тестировалось
     * изначально — там смена сертификата, судя по всему, не имела значения).
     * Настоящий adb-клиент на компьютере тоже генерирует такой сертификат
     * РОВНО ОДИН РАЗ и переиспользует его вечно — здесь та же схема.
     */
    fun loadOrCreateCertificate(context: Context, keyPair: KeyPair, commonName: String = "magicsqd-mobile"): X509Certificate {
        val certFile = File(context.filesDir, "adb_tls_cert.der")
        if (certFile.exists()) {
            try {
                val cf = CertificateFactory.getInstance("X.509")
                val cert = cf.generateCertificate(ByteArrayInputStream(certFile.readBytes())) as X509Certificate
                // Сертификат должен быть ИМЕННО от текущего keyPair — иначе на
                // TLS-рукопожатии подпись (CertificateVerify) будет посчитана
                // приватным ключом, который не соответствует публичному ключу
                // внутри сертификата, и сервер закономерно отклонит хендшейк
                // (реальный симптом на живом телефоне: TLS-хендшейк "успешен"
                // на нашей стороне, adbd рвёт его с CERTIFICATE_VERIFY_FAILED).
                // Рассинхрон возможен, если файл ключа (adb_auth_priv.der/
                // adb_auth_pub.der, см. AdbAuth.loadOrCreateKeyPair) когда-либо
                // менялся без соответствующей перегенерации этого файла —
                // сравниваем закодированные публичные ключи побайтово, без
                // этой проверки старый файл сертификата тихо переживает смену
                // ключа и ломает TLS каждый раз заново.
                if (cert.publicKey.encoded.contentEquals(keyPair.public.encoded)) {
                    return cert
                }
            } catch (_: Exception) {
                // Повреждённый файл — сгенерируем и перезапишем заново ниже.
            }
        }
        val cert = buildSelfSignedCertificate(keyPair, commonName)
        certFile.writeBytes(cert.encoded)
        return cert
    }

    /**
     * KeyManager, предъявляющий этот ключ+сертификат серверу при TLS-хендшейке.
     *
     * НЕ через стандартный KeyManagerFactory("PKIX")/KeyStore — тот САМ решает,
     * подходит ли наш ключ под запрос сервера (по keyType/issuers из
     * CertificateRequest), и на практике эта автоматика на части устройств
     * (проверено на реальном телефоне через logcat adbd) отказывается выбрать
     * единственный лежащий в keystore alias — TLS-рукопожатие после этого
     * "успешно" завершается, но КЛИЕНТ НЕ ОТПРАВЛЯЕТ СЕРТИФИКАТ ВООБЩЕ:
     * adbd закономерно рвёт соединение с "Handshake failed in SSL_accept/
     * SSL_connect [PEER_DID_NOT_RETURN_A_CERTIFICATE]" — то есть дело не в
     * доверии/повторных подключениях (см. UsbAdbTransport.performTlsUpgrade),
     * а в том, что наш клиент ни разу не пытался предъявить ключ на этом
     * шаге. У нас всегда РОВНО ОДИН ключ и ОДИН сертификат — выбирать
     * реально не из чего, поэтому вместо того, чтобы полагаться на
     * автоматический подбор alias'а, отдаём его руками, безусловно, при
     * любом запросе сервера (и по классическому X509KeyManager API, и по
     * chooseEngineClientAlias — TLS 1.3 на Android идёт через SSLEngine
     * даже за фасадом SSLSocket, и именно Engine-вариант используется при
     * реальном выборе сертификата в этом случае).
     */
    fun buildKeyManagers(keyPair: KeyPair, certificate: X509Certificate): Array<KeyManager> {
        return arrayOf(SingleIdentityKeyManager(keyPair.private, arrayOf(certificate)))
    }

    private class SingleIdentityKeyManager(
        private val privateKey: java.security.PrivateKey,
        private val chain: Array<X509Certificate>,
    ) : javax.net.ssl.X509ExtendedKeyManager() {
        companion object {
            private const val ALIAS = "adb-tls"
        }

        override fun getClientAliases(keyType: String?, issuers: Array<out java.security.Principal>?) =
            arrayOf(ALIAS)

        override fun chooseClientAlias(
            keyType: Array<out String>?,
            issuers: Array<out java.security.Principal>?,
            socket: java.net.Socket?,
        ) = ALIAS

        override fun chooseEngineClientAlias(
            keyType: Array<out String>?,
            issuers: Array<out java.security.Principal>?,
            engine: javax.net.ssl.SSLEngine?,
        ) = ALIAS

        override fun getServerAliases(keyType: String?, issuers: Array<out java.security.Principal>?): Array<String>? = null
        override fun chooseServerAlias(keyType: String?, issuers: Array<out java.security.Principal>?, socket: java.net.Socket?): String? = null
        override fun getCertificateChain(alias: String?): Array<X509Certificate> = chain
        override fun getPrivateKey(alias: String?): java.security.PrivateKey = privateKey
    }

    /**
     * Доверяем ЛЮБОМУ сертификату сервера — как и в classic AUTH, доверие
     * тут человекоопосредованное (устройство само решает, доверять ли НАШЕМУ
     * ключу, по факту предыдущего USB-approval), а не через настоящую CA-цепочку.
     */
    fun permissiveTrustManager(): X509TrustManager = object : X509TrustManager {
        override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
        override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {}
        override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
    }
}
