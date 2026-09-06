import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Отдельный маленький APK-инструмент для одноразового расследования: можно
// ли включить ADB (сетевой или через persist-свойство) прямо с экрана
// магнитолы, без ноутбука/adb-сессии — технику достаточно перезагрузить
// магнитолу и открыть это приложение на её же экране, чтобы увидеть
// текущее состояние и попробовать способы заново. Не часть основного
// ru.magicsqd.mobile — намеренно свой, минимальный applicationId, чтобы не
// путать с рабочим приложением техника и не тащить лишние зависимости
// (Chaquopy/USB mass storage тут не нужны).
android {
    namespace = "ru.magicsqd.adbdiag"
    compileSdk = 35

    defaultConfig {
        applicationId = "ru.magicsqd.adbdiag"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    lint {
        checkReleaseBuilds = false
    }
}

kotlin {
    compilerOptions {
        jvmTarget.set(JvmTarget.JVM_17)
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
}
