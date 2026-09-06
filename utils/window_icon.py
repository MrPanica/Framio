# -*- coding: utf-8 -*-
"""
Утилита извлечения нативных иконок запущенных приложений из HWND (как в панели задач и диспетчере задач Windows).
Поддерживает кэширование и HiDPI масштабирование для Qt.
"""

import sys
import ctypes
from ctypes import wintypes
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPixmap, QIcon
from PyQt6.QtCore import Qt
from ui.icons import create_themed_icon

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32

user32.GetClassLongPtrW.restype = ctypes.c_void_p
user32.GetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]

_WINDOW_ICON_CACHE: dict[int, QIcon] = {}


def get_window_qicon(hwnd: int, size: int = 18) -> QIcon:
    """
    Возвращает нативную иконку окна (HWND) приложения из панели задач / ресурсов процесса.
    Для hwnd == 0 или None возвращает стилизованную векторную иконку монитора/экрана.
    """
    if not hwnd or int(hwnd) <= 0:
        return create_themed_icon("dynamic_bg", is_dark=True, size=size)

    hwnd = int(hwnd)
    if hwnd in _WINDOW_ICON_CACHE:
        return _WINDOW_ICON_CACHE[hwnd]

    if not user32.IsWindow(hwnd):
        fallback = create_themed_icon("window", is_dark=True, size=size)
        return fallback

    hicon = None
    res = ctypes.c_void_p(0)

    # 1. WM_GETICON (ICON_SMALL2 = 2, ICON_SMALL = 0, ICON_BIG = 1)
    for ico_type in (2, 0, 1):
        if user32.SendMessageTimeoutW(hwnd, 0x007F, ico_type, 0, 2, 80, ctypes.byref(res)) and res.value:
            hicon = res.value
            break

    # 2. GetClassLongPtrW (GCLP_HICONSM = -34, GCLP_HICON = -14)
    if not hicon:
        hicon = user32.GetClassLongPtrW(hwnd, -34)
    if not hicon:
        hicon = user32.GetClassLongPtrW(hwnd, -14)

    # 3. ExtractIconExW из исполняемого файла процесса
    needs_destroy = False
    if not hicon:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        hproc = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if hproc:
            buf = ctypes.create_unicode_buffer(512)
            buf_len = wintypes.DWORD(512)
            if kernel32.QueryFullProcessImageNameW(hproc, 0, buf, ctypes.byref(buf_len)):
                exe_path = buf.value
                h_small = ctypes.c_void_p(0)
                if shell32.ExtractIconExW(exe_path, 0, None, ctypes.byref(h_small), 1) > 0 and h_small.value:
                    hicon = h_small.value
                    needs_destroy = True
            kernel32.CloseHandle(hproc)

    if not hicon:
        fallback = create_themed_icon("window", is_dark=True, size=size)
        _WINDOW_ICON_CACHE[hwnd] = fallback
        return fallback

    try:
        qimg = QImage.fromHICON(hicon)
        if needs_destroy:
            user32.DestroyIcon(hicon)

        if not qimg.isNull() and qimg.width() > 0:
            pix = QPixmap.fromImage(qimg).scaled(
                size * 2, size * 2,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            final_icon = QIcon(pix)
            _WINDOW_ICON_CACHE[hwnd] = final_icon
            return final_icon
    except Exception as e:
        pass

    fallback = create_themed_icon("window", is_dark=True, size=size)
    _WINDOW_ICON_CACHE[hwnd] = fallback
    return fallback
