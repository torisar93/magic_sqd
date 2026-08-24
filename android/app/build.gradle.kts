import org.jetbrains.kotlin.gradle.dsl.JvmTarget
import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

// Пароли живут вне git в keystore/keystore.properties (см. .gitignore).
// Если файла нет (например, чужая машина без релизного ключа), release-сборка
// просто останется неподписанной — debug/CI на этом не ломаются.
val keystoreProps = Properties().apply {
    val f = rootProject.file("keystore/keystore.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}

android {
    namespace = "ru.magicsqd.mobile"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.magicsqd.mobile"
        minSdk = 26
        targetSdk = 35
        versionCode = 15
        versionName = "0.2.1"

        ndk {
            // arm64-v8a/armeabi-v7a — реальные телефоны; x86_64 — эмулятор для UI-отладки
            // (USB host спайки на эмуляторе всё равно не тестируются, там нет реального OTG).
            abiFilters += listOf("arm64-v8a", "armeabi-v7a", "x86_64")
        }
    }

    signingConfigs {
        if (keystoreProps.containsKey("storeFile")) {
            create("release") {
                storeFile = rootProject.file(keystoreProps["storeFile"] as String)
                storePassword = keystoreProps["storePassword"] as String
                keyAlias = keystoreProps["keyAlias"] as String
                keyPassword = keystoreProps["keyPassword"] as String
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (keystoreProps.containsKey("storeFile")) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        // lintVitalAnalyzeRelease падает с внутренней ошибкой тулинга ("25.0.2"),
        // не связанной с реальными проблемами в коде — блокирует release-сборку
        // без этого отключения.
        checkReleaseBuilds = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

// Отдельный top-level блок, а не android.defaultConfig.python{} (это была
// устаревшая Groovy-эры форма, которую я перепутал с актуальным DSL — см.
// chaquo.com/chaquopy/doc/current/android.html).
chaquopy {
    defaultConfig {
        version = "3.11"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    // WebViewAssetLoader — отдаёт instruction.html картинки (files/instruction_N/
    // images/) из приватного хранилища приложения через https://appassets.
    // androidplatform.net/, недоступные из origin file:///android_asset/ (см.
    // MainActivity.kt/app.js "instruction"-этап).
    implementation("androidx.webkit:webkit:1.12.1")

    // USB mass storage (флешки) без root — читает/пишет/форматирует FAT32 через
    // userspace SCSI-over-bulk-transfer поверх UsbManager. Последний релиз 0.10.0
    // (2023), но библиотека зрелая и это единственный практичный путь без root.
    implementation("me.jahnen.libaums:core:0.10.0")
}
