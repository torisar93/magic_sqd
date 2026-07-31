Эта папка не является маркой авто (её имя начинается с "_", поэтому
приложение её игнорирует в списке марок).

Сюда можно класть общие .py модули с кодом, который используется в
install.py нескольких моделей — приложение автоматически добавляет эту
папку в sys.path при запуске.

Пример:
  cars/_shared/common.py:
      def grant_all_permissions(ctx, package):
          ctx.shell(f"pm grant {package} android.permission.WRITE_EXTERNAL_STORAGE")

  cars/Chery/Tiggo 7 Pro/install.py:
      import common

      def run(ctx):
          ctx.install_apk(ctx.file("app.apk"))
          common.grant_all_permissions(ctx, "com.example.app")
