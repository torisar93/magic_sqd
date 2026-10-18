# libusb и JNI-мост к ней для записи на флешку с телефона — собирает build.sh (см. README.md), не Gradle:
# готовые .so лежат в android/app/src/main/jniLibs/, сборке приложения NDK не нужен.
cmake_minimum_required(VERSION 3.18.1)
project(msqd_usb C)

if(NOT LIBUSB_PATH)
  message(FATAL_ERROR "LIBUSB_PATH — папка распакованного libusb-<версия>.tar.bz2")
endif()

set(LIBUSB_SRC "${LIBUSB_PATH}/libusb")

# libusb отдельной библиотекой (LGPL-2.1: её можно заменить, не пересобирая приложение) — исходники как есть,
# config.h — их же для Android. Список файлов — Linux usbfs без udev, как в libaums/libusbcommunication.
add_library(usb-1.0 SHARED
  ${LIBUSB_SRC}/core.c
  ${LIBUSB_SRC}/descriptor.c
  ${LIBUSB_SRC}/hotplug.c
  ${LIBUSB_SRC}/io.c
  ${LIBUSB_SRC}/strerror.c
  ${LIBUSB_SRC}/sync.c
  ${LIBUSB_SRC}/os/events_posix.c
  ${LIBUSB_SRC}/os/linux_netlink.c
  ${LIBUSB_SRC}/os/linux_usbfs.c
  ${LIBUSB_SRC}/os/threads_posix.c)
target_include_directories(usb-1.0 PRIVATE ${LIBUSB_PATH}/android ${LIBUSB_SRC} ${LIBUSB_SRC}/os)
target_link_libraries(usb-1.0 log)

add_library(msqd_usb SHARED msqd_usb.c)
target_include_directories(msqd_usb PRIVATE ${LIBUSB_SRC})
target_compile_options(msqd_usb PRIVATE -Wall -Wextra -Werror)
target_link_libraries(msqd_usb usb-1.0 log)

# Android 15+ на телефонах со страницей памяти 16 КБ грузит только библиотеки с сегментами, выровненными по 16 КБ.
foreach(lib usb-1.0 msqd_usb)
  target_link_options(${lib} PRIVATE "-Wl,-z,max-page-size=16384" "-Wl,--build-id=sha1")
endforeach()
