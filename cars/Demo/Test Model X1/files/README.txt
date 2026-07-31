Сюда кладутся файлы, специфичные именно для этой модели:
- APK, которые не входят в общий набор (patched launcher, кастомные лаунчеры и т.п.)
- утилиты (например .exe для разлочки, патчер прошивки)
- любые другие файлы, к которым обращается install.py через ctx.file("имя_файла")

Пример: ctx.file("special_launcher.apk") -> Path к файлу
"C:\...\cars\Demo\Test Model X1\files\special_launcher.apk"
