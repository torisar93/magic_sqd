/*
 * JNI-мост libaums → libusb: запись на флешку с телефона новым способом (Kotlin —
 * ru.magicsqd.mobile.usb.libusb.LibusbCommunication и LibusbNative; зачем и как собирать — README.md рядом).
 *
 * По мотивам libaums/libusbcommunication/src/main/c/usb.c (Apache-2.0, (c) magnusja,
 * https://github.com/magnusja/libaums). Изменено:
 *  - у каждого подключения свой контекст libusb (libusb_init_context), а не общий на процесс;
 *  - при сбое подключения всё уже открытое закрывается (раньше контекст оставался висеть);
 *  - данные передачи копируются только своим куском (Get/SetByteArrayRegion) с проверкой границ, а не всем
 *    массивом через GetByteArrayElements; у управляющей передачи wValue/wIndex 16 бит (было 8);
 *  - дескриптор сеанса отдаётся отдельно от кода ошибки: на arm64 с Android 11 у указателей кучи метка в
 *    старшем байте (0xB4…), как число они отрицательные — «дескриптор > 0» не годится (поймано на эмуляторе
 *    Android 15: libusb ни разу не подключалась, всё уходило на прежний способ).
 */
#include <jni.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include <android/log.h>
#include <libusb.h>

#define TAG "msqd_usb"

struct msqd_usb {
    libusb_context *ctx;
    libusb_device_handle *devh;
};

static struct msqd_usb *session(jlong handle) {
    return (struct msqd_usb *) (uintptr_t) handle;
}

/* Кусок [offset, offset + length) лежит внутри массива. */
static int in_bounds(JNIEnv *env, jbyteArray data, jint offset, jint length) {
    if (data == NULL || offset < 0 || length < 0) return 0;
    jsize size = (*env)->GetArrayLength(env, data);
    return offset <= size && length <= size - offset;
}

/* Свой контекст без поиска устройств (на телефоне без root список /dev/bus/usb недоступен) и устройство по
 * дескриптору файла от UsbManager.openDevice — файл остаётся за Java (libusb_close его не закрывает).
 * 0 — удалось, дескриптор сеанса в handle[0]; иначе код ошибки libusb (< 0). */
JNIEXPORT jint JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeInit(JNIEnv *env, jclass clazz, jint fd, jlongArray handle) {
    (void) clazz;
    if (handle == NULL || (*env)->GetArrayLength(env, handle) < 1) return LIBUSB_ERROR_INVALID_PARAM;
    const struct libusb_init_option options[] = {{.option = LIBUSB_OPTION_NO_DEVICE_DISCOVERY}};
    struct msqd_usb *usb = calloc(1, sizeof(*usb));
    if (usb == NULL) return LIBUSB_ERROR_NO_MEM;
    int ret = libusb_init_context(&usb->ctx, options, 1);
    if (ret != LIBUSB_SUCCESS) {
        __android_log_print(ANDROID_LOG_ERROR, TAG, "libusb_init_context: %s", libusb_error_name(ret));
        free(usb);
        return ret;
    }
    ret = libusb_wrap_sys_device(usb->ctx, (intptr_t) fd, &usb->devh);
    if (ret != LIBUSB_SUCCESS || usb->devh == NULL) {
        __android_log_print(ANDROID_LOG_ERROR, TAG, "libusb_wrap_sys_device: %s", libusb_error_name(ret));
        libusb_exit(usb->ctx);
        free(usb);
        return ret != LIBUSB_SUCCESS ? ret : LIBUSB_ERROR_OTHER;
    }
    const jlong value = (jlong) (uintptr_t) usb;
    (*env)->SetLongArrayRegion(env, handle, 0, 1, &value);
    return LIBUSB_SUCCESS;
}

JNIEXPORT void JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeClose(JNIEnv *env, jclass clazz, jlong handle) {
    (void) env;
    (void) clazz;
    struct msqd_usb *usb = session(handle);
    if (usb == NULL) return;
    libusb_close(usb->devh);
    libusb_exit(usb->ctx);
    free(usb);
}

/* Передача bulk: число переданных байт или код ошибки libusb (< 0). Таймаут с частично переданными данными —
 * тоже ошибка: libaums повторит команду целиком. */
JNIEXPORT jint JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeBulkTransfer(
        JNIEnv *env, jclass clazz, jlong handle, jint endpoint, jbyteArray data, jint offset, jint length, jint timeout) {
    (void) clazz;
    struct msqd_usb *usb = session(handle);
    if (usb == NULL || !in_bounds(env, data, offset, length)) return LIBUSB_ERROR_INVALID_PARAM;
    unsigned char *buffer = NULL;
    if (length > 0) {
        buffer = malloc((size_t) length);
        if (buffer == NULL) return LIBUSB_ERROR_NO_MEM;
    }
    int is_in = (endpoint & LIBUSB_ENDPOINT_DIR_MASK) == LIBUSB_ENDPOINT_IN;
    if (!is_in && length > 0) (*env)->GetByteArrayRegion(env, data, offset, length, (jbyte *) buffer);
    int transferred = 0;
    int ret = libusb_bulk_transfer(usb->devh, (unsigned char) endpoint, buffer, length, &transferred,
                                   (unsigned int) timeout);
    if (ret == LIBUSB_SUCCESS && is_in && transferred > 0) {
        (*env)->SetByteArrayRegion(env, data, offset, transferred, (const jbyte *) buffer);
    }
    free(buffer);
    return ret == LIBUSB_SUCCESS ? transferred : ret;
}

/* Управляющая передача (GET MAX LUN, сброс BOT): число байт или код ошибки libusb. */
JNIEXPORT jint JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeControlTransfer(
        JNIEnv *env, jclass clazz, jlong handle, jint request_type, jint request, jint value, jint index,
        jbyteArray data, jint length, jint timeout) {
    (void) clazz;
    struct msqd_usb *usb = session(handle);
    if (usb == NULL || length > 0xFFFF || (length > 0 && !in_bounds(env, data, 0, length))) {
        return LIBUSB_ERROR_INVALID_PARAM;
    }
    unsigned char *buffer = NULL;
    if (length > 0) {
        buffer = malloc((size_t) length);
        if (buffer == NULL) return LIBUSB_ERROR_NO_MEM;
    }
    int is_in = (request_type & LIBUSB_ENDPOINT_DIR_MASK) == LIBUSB_ENDPOINT_IN;
    if (!is_in && length > 0) (*env)->GetByteArrayRegion(env, data, 0, length, (jbyte *) buffer);
    int ret = libusb_control_transfer(usb->devh, (uint8_t) request_type, (uint8_t) request, (uint16_t) value,
                                      (uint16_t) index, buffer, (uint16_t) length, (unsigned int) timeout);
    if (ret > 0 && is_in) (*env)->SetByteArrayRegion(env, data, 0, ret, (const jbyte *) buffer);
    free(buffer);
    return ret;
}

JNIEXPORT jint JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeReset(JNIEnv *env, jclass clazz, jlong handle) {
    (void) env;
    (void) clazz;
    struct msqd_usb *usb = session(handle);
    return usb == NULL ? LIBUSB_ERROR_INVALID_PARAM : libusb_reset_device(usb->devh);
}

JNIEXPORT jint JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeClearHalt(
        JNIEnv *env, jclass clazz, jlong handle, jint endpoint) {
    (void) env;
    (void) clazz;
    struct msqd_usb *usb = session(handle);
    return usb == NULL ? LIBUSB_ERROR_INVALID_PARAM : libusb_clear_halt(usb->devh, (unsigned char) endpoint);
}

/* «LIBUSB_ERROR_TIMEOUT» и т.п. — для журнала. */
JNIEXPORT jstring JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeErrorName(JNIEnv *env, jclass clazz, jint code) {
    (void) clazz;
    return (*env)->NewStringUTF(env, libusb_error_name(code));
}

/* «1.0.30» — какая libusb в сборке, для журнала. */
JNIEXPORT jstring JNICALL
Java_ru_magicsqd_mobile_usb_libusb_LibusbNative_nativeVersion(JNIEnv *env, jclass clazz) {
    (void) clazz;
    const struct libusb_version *version = libusb_get_version();
    char text[32];
    snprintf(text, sizeof(text), "%u.%u.%u", version->major, version->minor, version->micro);
    return (*env)->NewStringUTF(env, text);
}
