plugins {
    // ПОПРОБОВАЛИ AGP 9.2.0 + встроенный Kotlin — и python{}, и kotlinOptions{}
    // не резолвились, судя по всему Chaquopy 17.0 ещё не тянет новую variant
    // API AGP 9.x, несмотря на заявленный в доке диапазон 7.3-9.2. Откатываемся
    // на заведомо обкатанную связку AGP 8.7.0 + Gradle 8.9 (официальные
    // min/default версии друг для друга, релиз AGP — октябрь 2024, давно
    // проверена в бою) + отдельный плагин Kotlin (обязателен до AGP 9.0).
    id("com.android.application") version "8.7.0" apply false
    id("org.jetbrains.kotlin.android") version "2.0.21" apply false
    id("com.chaquo.python") version "17.0.0" apply false
}
