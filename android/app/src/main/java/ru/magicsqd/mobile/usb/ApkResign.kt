package ru.magicsqd.mobile.usb

import com.android.apksig.ApkSigner
import java.io.File
import java.security.KeyFactory
import java.security.PrivateKey
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import java.security.spec.PKCS8EncodedKeySpec

/**
 * Переподпись APK своим сертификатом (v1+v2+v3, официальная библиотека
 * Google apksig — porт desktop-версии, см. app/apk_signer.py за подробным
 * объяснением ЗАЧЕМ это нужно: некоторые платформы (Changan WutongOS)
 * проверяют serial number сертификата APK и отказываются ставить обычный
 * APK без совпадения — см. research/Changan/notes.md). В отличие от
 * desktop, тут не нужен отдельный JRE — apksig чистая Java-библиотека,
 * работающая прямо в ART.
 *
 * cars/<Марка>/<Модель>/files/resign_cert/{private.pk8,certificate.crt} —
 * та же договорённость о расположении файлов сертификата, что и на
 * desktop (private.pk8 — PKCS#8 DER, certificate.crt — X.509 PEM/DER).
 */
fun resignCertDirForModel(modelDir: File): File? {
    val dir = File(modelDir, "files/resign_cert")
    return if (File(dir, "private.pk8").isFile && File(dir, "certificate.crt").isFile) dir else null
}

fun resignApkFile(inputApk: File, certDir: File, outputApk: File) {
    val privateKey: PrivateKey = KeyFactory.getInstance("RSA")
        .generatePrivate(PKCS8EncodedKeySpec(File(certDir, "private.pk8").readBytes()))
    val certificate: X509Certificate = File(certDir, "certificate.crt").inputStream().use { stream ->
        CertificateFactory.getInstance("X.509").generateCertificate(stream) as X509Certificate
    }
    val signerConfig = ApkSigner.SignerConfig.Builder("cert", privateKey, listOf(certificate)).build()
    ApkSigner.Builder(listOf(signerConfig))
        .setInputApk(inputApk)
        .setOutputApk(outputApk)
        .build()
        .sign()
}
