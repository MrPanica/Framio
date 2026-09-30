# -*- coding: utf-8 -*-
"""
Высокоскоростной модуль работы с системным буфером обмена Windows (Win32 Clipboard).
Помещает снимки экрана напрямую в разделяемую память ОС Windows:
- CF_DIB (8) — универсальный растровый формат Windows (Telegram, Paint, Word, классические Win32 программы)
- PNG — стандартный зарегистрированный формат Windows для Chromium, Electron, Antigravity, Discord, Slack, Firefox
- image/png — стандартный MIME-формат для Qt, браузеров и веб-компонентов
- application/x-framio-image-list — мультизональный формат для Framio

Полностью исключает задержки OLE delayed rendering, тайм-ауты COM RPC и создание промежуточных дисковых файлов.
"""

import sys
import json
import base64
import time
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage
from PyQt6.QtCore import QBuffer, QIODevice, QMimeData


def _set_images_win32(images: list[QImage], fmt: str = "png") -> bool:
    """Записывает изображения в системный буфер обмена Windows через нативный Win32 API."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    GlobalAlloc = kernel32.GlobalAlloc
    GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    GlobalAlloc.restype = wintypes.HGLOBAL

    GlobalLock = kernel32.GlobalLock
    GlobalLock.argtypes = [wintypes.HGLOBAL]
    GlobalLock.restype = ctypes.c_void_p

    GlobalUnlock = kernel32.GlobalUnlock
    GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    GlobalUnlock.restype = wintypes.BOOL

    OpenClipboard = user32.OpenClipboard
    OpenClipboard.argtypes = [wintypes.HWND]
    OpenClipboard.restype = wintypes.BOOL

    EmptyClipboard = user32.EmptyClipboard
    EmptyClipboard.argtypes = []
    EmptyClipboard.restype = wintypes.BOOL

    SetClipboardData = user32.SetClipboardData
    SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    SetClipboardData.restype = wintypes.HANDLE

    CloseClipboard = user32.CloseClipboard
    CloseClipboard.argtypes = []
    CloseClipboard.restype = wintypes.BOOL

    GMEM_MOVEABLE = 0x0002
    CF_DIB = 8
    CF_PNG = user32.RegisterClipboardFormatW("PNG")
    CF_MIME_PNG = user32.RegisterClipboardFormatW("image/png")
    CF_FRAMIO_LIST = user32.RegisterClipboardFormatW("application/x-framio-image-list")

    primary_img = images[0]

    # 1. Получаем PNG байты
    buf_png = QBuffer()
    buf_png.open(QIODevice.OpenModeFlag.WriteOnly)
    primary_img.save(buf_png, "PNG")
    png_bytes = bytes(buf_png.data())
    buf_png.close()

    # 2. Получаем DIB байты (BMP без 14-байтового BITMAPFILEHEADER)
    buf_bmp = QBuffer()
    buf_bmp.open(QIODevice.OpenModeFlag.WriteOnly)
    primary_img.save(buf_bmp, "BMP")
    bmp_bytes = bytes(buf_bmp.data())
    buf_bmp.close()
    dib_bytes = bmp_bytes[14:] if len(bmp_bytes) > 14 else b""

    # 3. При наличии нескольких зон формируем JSON со списком Base64
    custom_bytes = None
    if len(images) > 1:
        encoded = []
        for img in images:
            b = QBuffer()
            b.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(b, "PNG")
            encoded.append(base64.b64encode(bytes(b.data())).decode("ascii"))
            b.close()
        payload = json.dumps({"format": "png", "images": encoded}, separators=(",", ":"))
        custom_bytes = payload.encode("ascii")

    # Попытка открыть буфер обмена (до 8 попыток с микропаузой 5 мс на случай кратковременной блокировки другим процессом)
    opened = False
    for _ in range(8):
        if OpenClipboard(None):
            opened = True
            break
        time.sleep(0.005)

    if not opened:
        return False

    try:
        EmptyClipboard()

        # Помещаем CF_DIB (универсально для Telegram, Paint, Word, классических Win32 приложений)
        if dib_bytes:
            h_dib = GlobalAlloc(GMEM_MOVEABLE, len(dib_bytes))
            if h_dib:
                p_dib = GlobalLock(h_dib)
                if p_dib:
                    ctypes.memmove(p_dib, dib_bytes, len(dib_bytes))
                    GlobalUnlock(h_dib)
                    SetClipboardData(CF_DIB, h_dib)

        # Помещаем PNG (для Chromium, Electron, Antigravity, Discord, Slack, Firefox, web-приложений)
        if png_bytes:
            h_png = GlobalAlloc(GMEM_MOVEABLE, len(png_bytes))
            if h_png:
                p_png = GlobalLock(h_png)
                if p_png:
                    ctypes.memmove(p_png, png_bytes, len(png_bytes))
                    GlobalUnlock(h_png)
                    SetClipboardData(CF_PNG, h_png)

            # Дополнительно регистрируем MIME-формат image/png
            h_mpng = GlobalAlloc(GMEM_MOVEABLE, len(png_bytes))
            if h_mpng:
                p_mpng = GlobalLock(h_mpng)
                if p_mpng:
                    ctypes.memmove(p_mpng, png_bytes, len(png_bytes))
                    GlobalUnlock(h_mpng)
                    SetClipboardData(CF_MIME_PNG, h_mpng)

        # Помещаем список зон Framio
        if custom_bytes and CF_FRAMIO_LIST:
            h_custom = GlobalAlloc(GMEM_MOVEABLE, len(custom_bytes))
            if h_custom:
                p_custom = GlobalLock(h_custom)
                if p_custom:
                    ctypes.memmove(p_custom, custom_bytes, len(custom_bytes))
                    GlobalUnlock(h_custom)
                    SetClipboardData(CF_FRAMIO_LIST, h_custom)

        return True
    finally:
        CloseClipboard()


def _set_images_qt(images: list[QImage], fmt: str = "png") -> bool:
    """Резервный кроссплатформенный метод через Qt QMimeData."""
    mime = QMimeData()
    primary = images[0]
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    primary.save(buf, "PNG")
    mime.setData("image/png", buf.data())
    mime.setImageData(primary)
    buf.close()

    if len(images) > 1:
        encoded = []
        for img in images:
            b = QBuffer()
            b.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(b, "PNG")
            encoded.append(base64.b64encode(bytes(b.data())).decode("ascii"))
            b.close()
        payload = json.dumps({"format": "png", "images": encoded}, separators=(",", ":"))
        mime.setData("application/x-framio-image-list", payload.encode("ascii"))

    QApplication.clipboard().setMimeData(mime)
    return True


def copy_images_to_clipboard(images: list[QImage], fmt: str = "png") -> bool:
    """
    Молниеносно помещает изображения в системный буфер обмена.
    На Windows использует прямой Win32 API с одновременной регистрацией CF_DIB, PNG и image/png
    для 100% совместимости со всеми редакторами, мессенджерами и веб-приложениями.
    """
    import os

    if not images or all(img.isNull() for img in images):
        return False

    valid_images = [img for img in images if not img.isNull()]
    if not valid_images:
        return False

    # В headless / offscreen режиме (CI тесты) используем изолированный Qt in-memory буфер
    app = QApplication.instance()
    is_offscreen = (app is not None and app.platformName() == "offscreen") or (os.environ.get("QT_QPA_PLATFORM") == "offscreen")
    if is_offscreen:
        return _set_images_qt(valid_images, fmt)

    if sys.platform == "win32":
        try:
            ok = _set_images_win32(valid_images, fmt)
            if ok:
                return True
        except Exception as e:
            print(f"[ClipboardHelper] Win32 copy error, falling back to Qt: {e}")

    return _set_images_qt(valid_images, fmt)

