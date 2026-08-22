package ru.magicsqd.mobile.usb

import android.content.Context
import android.util.Base64
import java.io.File
import java.math.BigInteger
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.security.KeyFactory
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.interfaces.RSAPrivateKey
import java.security.interfaces.RSAPublicKey
import java.security.spec.PKCS8EncodedKeySpec
import java.security.spec.X509EncodedKeySpec

/**
 * RSA-ключ и подпись для ADB AUTH (см. AdbProtocol.A_AUTH в UsbAdbTransport.kt).
 * ADB использует НЕ стандартную Java-подпись, а два нестандартных места:
 *
 * 1. "Подпись" 20-байтного токена — это не SHA1withRSA (который сам считает
 *    хэш от сообщения), а сырая RSA-операция над вручную собранным PKCS#1v1.5
 *    блоком, где токен уже трактуется как готовый SHA-1 дайджест (сверено с
 *    исходником rsa.sign(data, key, 'SHA-1-PREHASHED') из python `rsa`/
 *    `adb_shell` — эталонной реализации, раз проверить негде кроме как на
 *    живом устройстве). Считаем modPow вручную (BigInteger), а не через
 *    Signature.getInstance("NONEwithRSA") — этот алгоритм на части версий
 *    Android недоступен в штатном провайдере без явного BouncyCastle.
 *
 * 2. Публичный ключ для AUTH_RSAPUBLICKEY — это НЕ X.509, а свой бинарный
 *    struct с параметрами Montgomery-умножения (n0inv, rr), которые нужны
 *    RSA-реализации на стороне adbd. Формат и алгоритм расчёта n0inv/rr взят
 *    из adb_shell.auth.keygen (эталонная актуальная реализация).
 */
object AdbAuth {
    private const val KEY_SIZE_BITS = 2048
    private const val MODULUS_BYTES = KEY_SIZE_BITS / 8 // 256
    private const val MODULUS_WORDS = MODULUS_BYTES / 4 // 64

    private val SHA1_DIGEST_INFO_PREFIX = byteArrayOf(
        0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e,
        0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14
    )

    /** Персистентный ключ в приватном хранилище приложения — чтобы устройство не
     * спрашивало разрешение на КАЖДЫЙ запуск, а запоминало ключ один раз. */
    fun loadOrCreateKeyPair(context: Context): KeyPair {
        val privFile = File(context.filesDir, "adb_auth_priv.der")
        val pubFile = File(context.filesDir, "adb_auth_pub.der")
        if (privFile.exists() && pubFile.exists()) {
            val kf = KeyFactory.getInstance("RSA")
            val priv = kf.generatePrivate(PKCS8EncodedKeySpec(privFile.readBytes()))
            val pub = kf.generatePublic(X509EncodedKeySpec(pubFile.readBytes()))
            return KeyPair(pub, priv)
        }
        val kp = KeyPairGenerator.getInstance("RSA").apply { initialize(KEY_SIZE_BITS) }.genKeyPair()
        privFile.writeBytes(kp.private.encoded)
        pubFile.writeBytes(kp.public.encoded)
        return kp
    }

    fun signToken(privateKey: RSAPrivateKey, token: ByteArray): ByteArray {
        val digestInfo = SHA1_DIGEST_INFO_PREFIX + token
        val paddingLen = MODULUS_BYTES - digestInfo.size - 3
        check(paddingLen > 0) { "Ключ слишком мал для PKCS#1v1.5 SHA-1 подписи" }

        val padded = ByteArray(MODULUS_BYTES)
        padded[0] = 0x00
        padded[1] = 0x01
        for (i in 2 until 2 + paddingLen) padded[i] = 0xFF.toByte()
        padded[2 + paddingLen] = 0x00
        System.arraycopy(digestInfo, 0, padded, 3 + paddingLen, digestInfo.size)

        val m = BigInteger(1, padded)
        val s = m.modPow(privateKey.privateExponent, privateKey.modulus)
        return normalizeToLength(s.toByteArray(), MODULUS_BYTES)
    }

    private fun normalizeToLength(bytes: ByteArray, length: Int): ByteArray = when {
        bytes.size == length -> bytes
        bytes.size == length + 1 && bytes[0] == 0.toByte() -> bytes.copyOfRange(1, bytes.size)
        bytes.size < length -> ByteArray(length - bytes.size) + bytes
        else -> error("Неожиданная длина: ${bytes.size}, ожидали $length")
    }

    /** big-endian BigInteger -> little-endian массив фиксированной длины (без знакового 0x00). */
    private fun toLittleEndianFixed(value: BigInteger, size: Int): ByteArray {
        var be = value.toByteArray()
        if (be.size > size && be[0] == 0.toByte()) be = be.copyOfRange(1, be.size)
        check(be.size <= size) { "Значение не помещается в $size байт (нужно ${be.size})" }
        val out = ByteArray(size)
        for (i in be.indices) out[i] = be[be.size - 1 - i]
        return out
    }

    /** adbd читает payload как null-terminated C-строку — нужен явный завершающий 0x00 байт. */
    fun encodePublicKeyForAdb(publicKey: RSAPublicKey, comment: String = "unknown@magicsqd-mobile"): ByteArray {
        val n = publicKey.modulus
        val e = publicKey.publicExponent

        val r32 = BigInteger.ONE.shiftLeft(32)
        val n0invPos = n.mod(r32).modInverse(r32)
        val n0inv = r32.subtract(n0invPos).mod(r32)

        // (2^(MODULUS_BYTES*8))^2 mod n == 2^(MODULUS_BYTES*8*2) mod n
        val rr = BigInteger.ONE.shiftLeft(MODULUS_BYTES * 8 * 2).mod(n)

        val buf = ByteBuffer.allocate(4 + 4 + MODULUS_BYTES + MODULUS_BYTES + 4).order(ByteOrder.LITTLE_ENDIAN)
        buf.putInt(MODULUS_WORDS)
        buf.putInt(n0inv.toInt())
        buf.put(toLittleEndianFixed(n, MODULUS_BYTES))
        buf.put(toLittleEndianFixed(rr, MODULUS_BYTES))
        buf.putInt(e.toInt())

        val base64 = Base64.encodeToString(buf.array(), Base64.NO_WRAP)
        val withComment = "$base64 $comment"
        return withComment.toByteArray(Charsets.UTF_8) + byteArrayOf(0)
    }
}
