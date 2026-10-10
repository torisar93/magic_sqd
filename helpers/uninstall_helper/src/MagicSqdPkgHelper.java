import android.content.Context;
import android.content.IIntentReceiver;
import android.content.IIntentSender;
import android.content.Intent;
import android.content.IntentSender;
import android.content.pm.PackageInstaller;
import android.os.Bundle;
import android.os.IBinder;
import android.os.Looper;

import java.lang.reflect.Constructor;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;

/**
 * Удаление приложения от имени shell через PackageInstaller — для магнитол, где adb не пускает «pm uninstall»
 * (VOLGA/Geely N155, Geely OneOS/Monji, Jetour T2: OPEN «shell:pm uninstall …» сразу закрывается, откат в сток упирался в
 * «удалите штатно», логи №797, №962, №1664, №1746). Запуск тот же, что у хелпера установки dex_shell_helper.dex:
 *
 *   CLASSPATH=/data/local/tmp/msqd_pkg_helper.dex app_process /data/local/tmp MagicSqdPkgHelper &lt;пакет&gt;
 *
 * В именах файла и класса НЕТ слова «uninstall»: эти прошивки отклоняют ЛЮБУЮ shell-команду с ним — прежний
 * хелпер (uninstall_helper.dex, тот же код под старым именем) за 21 попытку в 1.0.51–1.1.1 не сработал ни разу,
 * отклонялись даже chmod и rm его файла (лог №4649). Аргументы — только имя пакета.
 *
 * Печатает «Success» или «Failure [причина]» (как сама команда pm), код выхода 0 / 1, 2 — неверный вызов.
 * Приём — как у pm (PackageManagerShellCommand.LocalIntentReceiver): итог приходит в свой IIntentSender.
 * Сборка — build.sh рядом (stubs/ — заглушки скрытых классов только для компиляции, в .dex не попадают).
 */
public final class MagicSqdPkgHelper {
    private static final int TIMEOUT_SECONDS = 60;

    public static void main(String[] args) {
        if (args.length != 1 || args[0].isEmpty()) {
            System.err.println("Usage: MagicSqdPkgHelper <package>");
            System.exit(2);
        }
        try {
            Context context = systemContext();
            PackageInstaller installer = context.getPackageManager().getPackageInstaller();
            LocalReceiver receiver = new LocalReceiver();
            installer.uninstall(args[0], receiver.intentSender());
            Intent result = receiver.await(TIMEOUT_SECONDS);
            if (result == null) {
                System.out.println("Failure [timed out waiting for uninstall result]");
                System.exit(1);
            }
            int status = result.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE);
            if (status == PackageInstaller.STATUS_SUCCESS) {
                System.out.println("Success");
                System.exit(0);
            }
            String message = result.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE);
            System.out.println("Failure [" + (message != null ? message : "status " + status) + "]");
            System.exit(1);
        } catch (Throwable e) {
            System.out.println("Failure [" + e + "]");
            System.exit(1);
        }
    }

    /** Контекст системы без приложения — как у scrcpy/хелпера установки: свой ActivityThread и его системный контекст. */
    private static Context systemContext() throws Exception {
        if (Looper.getMainLooper() == null) {
            Looper.prepareMainLooper();
        }
        Class<?> activityThread = Class.forName("android.app.ActivityThread");
        Constructor<?> constructor = activityThread.getDeclaredConstructor();
        constructor.setAccessible(true);
        Object thread = constructor.newInstance();
        Field current = activityThread.getDeclaredField("sCurrentActivityThread");
        current.setAccessible(true);
        current.set(null, thread);
        Method getSystemContext = activityThread.getDeclaredMethod("getSystemContext");
        getSystemContext.setAccessible(true);
        return (Context) getSystemContext.invoke(thread);
    }

    private static final class LocalReceiver {
        private final LinkedBlockingQueue<Intent> results = new LinkedBlockingQueue<>();

        private final IIntentSender.Stub sender = new IIntentSender.Stub() {
            @Override
            public void send(int code, Intent intent, String resolvedType, IBinder whitelistToken,
                             IIntentReceiver finishedReceiver, String requiredPermission, Bundle options) {
                results.offer(intent);
            }
        };

        IntentSender intentSender() throws Exception {
            Constructor<IntentSender> constructor = IntentSender.class.getDeclaredConstructor(IIntentSender.class);
            constructor.setAccessible(true);
            return constructor.newInstance(sender);
        }

        Intent await(int seconds) throws InterruptedException {
            return results.poll(seconds, TimeUnit.SECONDS);
        }
    }
}
