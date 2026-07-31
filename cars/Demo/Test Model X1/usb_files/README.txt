Файлы отсюда копируются в КОРЕНЬ флешки при установке через режим
"Через USB-флешку...", если у модели нет собственного usb_install.py.

Например положите сюда:
  - app.apk             -> окажется в корне флешки как app.apk
  - firmware/patch.bin  -> окажется в корне флешки как firmware/patch.bin

Отмеченные галочками приложения из общей папки apk/ тоже копируются в
корень флешки автоматически — их сюда дублировать не нужно.

Если для этой модели нужна нестандартная логика (переименование файлов,
особая структура папок, которую ждёт магнитола) — создайте рядом со
скриптом install.py файл usb_install.py с функцией run(ctx). Пример:

    def run(ctx):
        ctx.log("Готовлю флешку для Test Model X1")
        ctx.copy_file(ctx.usb_file("app.apk"), "update/app.apk")
        ctx.copy_selected_apks("update/apps")
        ctx.write_text("update/install.cfg", "AUTO_INSTALL=1\n")

Доступные методы ctx — в app/usb_context.py.
