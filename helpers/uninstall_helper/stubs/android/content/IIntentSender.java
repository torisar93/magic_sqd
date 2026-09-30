package android.content;

import android.os.Binder;
import android.os.Bundle;
import android.os.IBinder;
import android.os.IInterface;
import android.os.RemoteException;

/** Заглушка скрытого интерфейса — ТОЛЬКО для компиляции MagicSqdUninstaller (в .dex не попадает, на устройстве
 * берётся настоящий из фреймворка). Сигнатура send — как в AOSP с Android 8 (void, с whitelistToken). */
public interface IIntentSender extends IInterface {
    void send(int code, Intent intent, String resolvedType, IBinder whitelistToken, IIntentReceiver finishedReceiver,
              String requiredPermission, Bundle options) throws RemoteException;

    abstract class Stub extends Binder implements IIntentSender {
        public Stub() {
        }

        @Override
        public IBinder asBinder() {
            return this;
        }
    }
}
