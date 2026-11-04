package ru.magicsqd.mobile.usb

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.BatteryManager
import android.os.Build

/**
 * Что телефон видит по USB, когда устройства с ADB-интерфейсом нет. Разбор логов 04.11.2026: Haval Jolion 2026 с
 * Android — у 18 из 26 техников десятки «Устройство с ADB-интерфейсом не найдено» подряд, а по логу не понять, видна ли
 * магнитола вообще (с ПК та же модель подключается у 40 из 41). Три случая — три разных совета технику:
 *  - [CAUSE_NO_ADB]: устройство по USB есть, но без ADB-интерфейса — магнитола видна, а ADB на ней закрыт
 *    (или вставлена флешка, а не кабель от магнитолы);
 *  - [CAUSE_PHONE_IS_DEVICE]: по USB ничего нет, но телефон сам подключён как устройство и заряжается — главной в паре
 *    стала магнитола (кабель USB-C↔USB-C: роль host достаётся любой стороне), а не телефон;
 *  - [CAUSE_NOTHING]: ничего нет вовсе — переходник, кабель без передачи данных, порт.
 */
object UsbPicture {
    const val CAUSE_NO_ADB = "no_adb"
    const val CAUSE_PHONE_IS_DEVICE = "phone_is_device"
    const val CAUSE_NOTHING = "nothing"

    /** UsbManager.ACTION_USB_STATE — скрытая константа; липкое системное сообщение о том, подключён ли телефон сам
     * как USB-устройство (к компьютеру/магнитоле) и какие функции включены (mtp, ptp, accessory — Android Auto…). */
    private const val ACTION_USB_STATE = "android.hardware.usb.action.USB_STATE"
    private val FUNCTIONS = listOf("mtp", "ptp", "accessory", "rndis", "midi", "adb")

    data class Picture(val cause: String, val line: String)

    fun describe(context: Context, usbManager: UsbManager): Picture {
        val devices = usbManager.deviceList.values.toList()
        if (devices.isNotEmpty()) {
            val list = devices.take(4).joinToString("; ") { describeDevice(it) }
            return Picture(CAUSE_NO_ADB, "USB: телефон видит устройств: ${devices.size} — $list. ADB-интерфейса среди них нет.")
        }
        val state = sticky(context, ACTION_USB_STATE)
        val plugged = sticky(context, Intent.ACTION_BATTERY_CHANGED)?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0
        val asDevice = state?.getBooleanExtra("connected", false) == true
        val charging = plugged == BatteryManager.BATTERY_PLUGGED_USB || plugged == BatteryManager.BATTERY_PLUGGED_AC
        if (asDevice || charging) {
            val functions = FUNCTIONS.filter { state?.getBooleanExtra(it, false) == true }
            val how = buildList {
                if (asDevice) add("подключён как устройство" + if (functions.isEmpty()) "" else " (${functions.joinToString(", ")})")
                if (charging) add("заряжается")
            }.joinToString(", ")
            return Picture(
                CAUSE_PHONE_IS_DEVICE,
                "USB: телефон не видит ни одного устройства, а сам $how — главной в паре стала магнитола, а не телефон.",
            )
        }
        return Picture(CAUSE_NOTHING, "USB: телефон не видит ни одного устройства и сам ни к чему не подключён.")
    }

    /** «vid:pid Имя (интерфейсы: ff/42/01, 08/06/50)» — классы интерфейсов показывают, что это: 08 — флешка, 06 — фото
     * (PTP/MTP), 09 — разветвитель, ff/42/01 — ADB. */
    private fun describeDevice(device: UsbDevice): String {
        val name = runCatching { listOfNotNull(device.manufacturerName, device.productName).joinToString(" ") }
            .getOrNull()?.trim()?.takeIf { it.isNotEmpty() }
        val ifaces = (0 until device.interfaceCount).take(6).joinToString(", ") { i ->
            val f = device.getInterface(i)
            "%02x/%02x/%02x".format(f.interfaceClass, f.interfaceSubclass, f.interfaceProtocol)
        }
        val id = "%04x:%04x".format(device.vendorId, device.productId)
        return listOfNotNull(id, name?.let { "«$it»" }, "(интерфейсы: ${ifaces.ifEmpty { "нет" }})").joinToString(" ")
    }

    private fun sticky(context: Context, action: String): Intent? = runCatching {
        val filter = IntentFilter(action)
        if (Build.VERSION.SDK_INT >= 33) context.registerReceiver(null, filter, Context.RECEIVER_NOT_EXPORTED)
        else context.registerReceiver(null, filter)
    }.getOrNull()
}
