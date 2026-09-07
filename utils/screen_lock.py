# -*- coding: utf-8 -*-
"""
Потокобезопасная синхронизация захвата экрана.
Предотвращает конкурентные вызовы Direct3D / GDI / QScreen::grabWindow
из нескольких фоновых потоков (видео, GIF) и основного потока GUI (скриншот).
Поддерживает нативный Win32 CAPTUREBLT для корректного захвата оконных игр без рамки (borderless),
аппаратных оверлеев и Direct3D/Vulkan flip-моделей с 100% точностью пикселей 1:1 с учетом DPI.
"""

import threading
import ctypes
from ctypes import wintypes
import numpy as np
import cv2
from PyQt6.QtGui import QPixmap, QImage
from PyQt6.QtWidgets import QApplication

SCREEN_CAPTURE_LOCK = threading.RLock()

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi

class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


# WindowFromPoint принимает POINT по значению, а HWND должен сохраняться на 64-битной Windows.
user32.WindowFromPoint.restype = wintypes.HWND
user32.WindowFromPoint.argtypes = [POINT]
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]

# 64-битные типы аргументов и возвращаемых значений для предотвращения обрезания указателей
user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]

user32.PrintWindow.restype = wintypes.BOOL
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]

user32.ReleaseDC.restype = wintypes.BOOL
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]

gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]

gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]

gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]

gdi32.BitBlt.restype = wintypes.BOOL
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]

gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]

gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]

gdi32.GetDIBits.restype = ctypes.c_int
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                           ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD)
    ]

class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3)
    ]


def get_screen_dpr() -> float:
    """Возвращает текущий коэффициент масштабирования Windows (DPI Ratio)."""
    try:
        screen = QApplication.primaryScreen()
        if screen is not None:
            return float(screen.devicePixelRatio())
    except Exception:
        pass
    return 1.0


def win32_captureblt_pixmap(rx: int, ry: int, rw: int, rh: int) -> QPixmap | None:
    """
    Захватывает область экрана через Win32 GDI BitBlt с флагом CAPTUREBLT (0x40000000)
    с учетом физического масштабирования Windows DPR.
    """
    try:
        try:
            dwmapi.DwmFlush()
        except Exception:
            pass
        dpr = get_screen_dpr()
        px = int(round(rx * dpr))
        py = int(round(ry * dpr))
        pw = max(1, int(round(rw * dpr)))
        ph = max(1, int(round(rh * dpr)))

        hScreenDC = user32.GetDC(0)
        if not hScreenDC:
            return None

        hMemoryDC = gdi32.CreateCompatibleDC(hScreenDC)
        if not hMemoryDC:
            user32.ReleaseDC(0, hScreenDC)
            return None

        hBitmap = gdi32.CreateCompatibleBitmap(hScreenDC, pw, ph)
        if not hBitmap:
            gdi32.DeleteDC(hMemoryDC)
            user32.ReleaseDC(0, hScreenDC)
            return None

        hOldBitmap = gdi32.SelectObject(hMemoryDC, hBitmap)

        SRCCOPY = 0x00CC0020
        CAPTUREBLT = 0x40000000
        ok = gdi32.BitBlt(hMemoryDC, 0, 0, pw, ph, hScreenDC, px, py, SRCCOPY | CAPTUREBLT)
        if not ok:
            gdi32.SelectObject(hMemoryDC, hOldBitmap)
            gdi32.DeleteObject(hBitmap)
            gdi32.DeleteDC(hMemoryDC)
            user32.ReleaseDC(0, hScreenDC)
            return None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = pw
        bmi.bmiHeader.biHeight = -ph  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB

        buf = ctypes.create_string_buffer(pw * ph * 4)
        gdi32.GetDIBits(hMemoryDC, hBitmap, 0, ph, buf, ctypes.byref(bmi), 0)

        gdi32.SelectObject(hMemoryDC, hOldBitmap)
        gdi32.DeleteObject(hBitmap)
        gdi32.DeleteDC(hMemoryDC)
        user32.ReleaseDC(0, hScreenDC)

        raw = bytes(buf)
        qimg = QImage(raw, pw, ph, pw * 4, QImage.Format.Format_RGB32).copy()
        if not qimg.isNull():
            qimg.setDevicePixelRatio(dpr)
            return QPixmap.fromImage(qimg)
    except Exception as e:
        print(f"[ScreenLock] win32_captureblt_pixmap failed: {e}")
    return None


def win32_captureblt_bgr(rx: int, ry: int, rw: int, rh: int) -> np.ndarray | None:
    """
    Прямой захват физических пикселей экрана 1:1 в массив BGR numpy через GDI BitBlt с CAPTUREBLT.
    Не интерполирует и не размывает изображение.
    """
    try:
        try:
            dwmapi.DwmFlush()
        except Exception:
            pass
        dpr = get_screen_dpr()
        px = int(round(rx * dpr))
        py = int(round(ry * dpr))
        pw = max(16, int(round(rw * dpr)))
        ph = max(16, int(round(rh * dpr)))

        hScreenDC = user32.GetDC(0)
        if not hScreenDC:
            return None

        hMemoryDC = gdi32.CreateCompatibleDC(hScreenDC)
        if not hMemoryDC:
            user32.ReleaseDC(0, hScreenDC)
            return None

        hBitmap = gdi32.CreateCompatibleBitmap(hScreenDC, pw, ph)
        if not hBitmap:
            gdi32.DeleteDC(hMemoryDC)
            user32.ReleaseDC(0, hScreenDC)
            return None

        hOldBitmap = gdi32.SelectObject(hMemoryDC, hBitmap)

        SRCCOPY = 0x00CC0020
        CAPTUREBLT = 0x40000000
        ok = gdi32.BitBlt(hMemoryDC, 0, 0, pw, ph, hScreenDC, px, py, SRCCOPY | CAPTUREBLT)
        if not ok:
            gdi32.SelectObject(hMemoryDC, hOldBitmap)
            gdi32.DeleteObject(hBitmap)
            gdi32.DeleteDC(hMemoryDC)
            user32.ReleaseDC(0, hScreenDC)
            return None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = pw
        bmi.bmiHeader.biHeight = -ph  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB

        buf = ctypes.create_string_buffer(pw * ph * 4)
        gdi32.GetDIBits(hMemoryDC, hBitmap, 0, ph, buf, ctypes.byref(bmi), 0)

        gdi32.SelectObject(hMemoryDC, hOldBitmap)
        gdi32.DeleteObject(hBitmap)
        gdi32.DeleteDC(hMemoryDC)
        user32.ReleaseDC(0, hScreenDC)

        # GDI 32-bit DIB возвращает BGRA (4 байта на пиксель)
        arr = np.frombuffer(buf, dtype=np.uint8).reshape((ph, pw, 4))
        return np.ascontiguousarray(arr[:, :, :3])
    except Exception as e:
        print(f"[ScreenLock] win32_captureblt_bgr failed: {e}")
        return None


def safe_grab_screen_bgr(rx: int, ry: int, rw: int, rh: int, target_hwnd: int | None = None) -> np.ndarray:
    """Безопасно захватывает область экрана напрямую в формат OpenCV BGR 1:1 под глобальным мьютексом."""
    if target_hwnd is not None and int(target_hwnd) > 0:
        return capture_window_or_screen_bgr(rx, ry, rw, rh, target_hwnd)

    with SCREEN_CAPTURE_LOCK:
        try:
            bgr = win32_captureblt_bgr(rx, ry, rw, rh)
            if bgr is not None and bgr.size > 0:
                return bgr
        except Exception:
            pass

        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                pix = screen.grabWindow(0, int(rx), int(ry), int(rw), int(rh))
                if pix is not None and not pix.isNull():
                    return qimage_to_cv2_bgr(pix.toImage())
        except Exception as e:
            print(f"[ScreenLock] Резервный захват экрана не удался: {e}")

        dpr = get_screen_dpr()
        pw = max(16, int(round(rw * dpr)))
        ph = max(16, int(round(rh * dpr)))
        return np.zeros((ph, pw, 3), dtype=np.uint8)


def safe_grab_screen_pixmap(rx: int, ry: int, rw: int, rh: int) -> QPixmap:
    """Безопасно захватывает прямоугольную область экрана под глобальным мьютексом (для GUI потока)."""
    with SCREEN_CAPTURE_LOCK:
        try:
            pix = win32_captureblt_pixmap(rx, ry, rw, rh)
            if pix is not None and not pix.isNull():
                return pix
        except Exception:
            pass

        try:
            screen = QApplication.primaryScreen()
            if screen is not None:
                pix = screen.grabWindow(0, int(rx), int(ry), int(rw), int(rh))
                if pix is not None and not pix.isNull():
                    return pix
        except Exception as e:
            print(f"[ScreenLock] Ошибка safe_grab_screen_pixmap: {e}")

        return QPixmap()


def qimage_to_cv2_bgr(qimg: QImage) -> np.ndarray:
    """Безопасная конвертация QImage (ARGB32) в numpy BGR массив без сбоев памяти и переполнения."""
    if qimg is None or qimg.isNull() or qimg.width() <= 0 or qimg.height() <= 0:
        return np.zeros((16, 16, 3), dtype=np.uint8)

    img = qimg if qimg.format() == QImage.Format.Format_ARGB32 else qimg.convertToFormat(QImage.Format.Format_ARGB32)
    w = img.width()
    h = img.height()
    bpl = img.bytesPerLine()

    ptr = img.constBits()
    ptr.setsize(h * bpl)
    raw_bytes = np.array(ptr, copy=True).reshape((h, bpl))

    if bpl == w * 4:
        bgra = raw_bytes.reshape((h, w, 4))
    else:
        bgra = raw_bytes[:, :w * 4].reshape((h, w, 4))

    return cv2.cvtColor(bgra, cv2.COLOR_BGRA2BGR)


def enumerate_recordable_windows() -> list[tuple[int, str]]:
    """
    Возвращает список доступных для записи окон верхнего уровня:
    [(hwnd, window_title), ...]
    Фильтрует скрытые, системные и служебные окна, но включает свёрнутые пользовательские приложения.
    """
    try:
        h_input = user32.OpenInputDesktop(0, False, 0x01FF)
        if h_input:
            user32.SetThreadDesktop(h_input)
    except Exception:
        pass

    windows = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_size_t, ctypes.c_size_t)

    def enum_proc(hwnd, lp):
        try:
            is_visible = user32.IsWindowVisible(hwnd)
            is_iconic = user32.IsIconic(hwnd)
            if not is_visible and not is_iconic:
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
            if not title:
                return True

            # Исключаем служебные/системные окна
            ignored_titles = ("Program Manager", "Default IME", "MSCTFIME UI")
            if title in ignored_titles or title.startswith("Framio") or title.startswith("LightCap"):
                return True

            cloaked = wintypes.DWORD(0)
            dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value != 0:
                return True

            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if not is_iconic and (w < 80 or h < 80):
                return True

            display_title = f"{title} [свёрнуто]" if is_iconic else title
            windows.append((int(hwnd), display_title))
        except Exception:
            pass
        return True

    try:
        if h_input:
            user32.EnumDesktopWindows(h_input, WNDENUMPROC(enum_proc), 0)
        else:
            user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
    except Exception:
        try:
            user32.EnumWindows(WNDENUMPROC(enum_proc), 0)
        except Exception:
            pass

    return windows


# Кэш последних валидных кадров для каждого окна HWND, гарантирующий отсутствие мерцания черных кадров
_WINDOW_FRAME_CACHE: dict[int, np.ndarray] = {}


def capture_window_bgr(hwnd: int, rx: int, ry: int, rw: int, rh: int) -> np.ndarray | None:
    """
    Высокоскоростной захват кадра конкретного окна (HWND) без мерцания экрана.
    Использует прямой BitBlt из контекста окна (GetDC) для Direct3D/OpenGL/Win32 окон,
    мягкий fallback на PrintWindow и кэширование кадров при сворачивании/перекрытии.
    """
    try:
        if not user32.IsWindow(hwnd):
            _WINDOW_FRAME_CACHE.pop(hwnd, None)
            return None

        # Если окно свёрнуто пользователем, отдаём последний валидный кадр
        if user32.IsIconic(hwnd):
            return _WINDOW_FRAME_CACHE.get(hwnd)

        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        win_w = rect.right - rect.left
        win_h = rect.bottom - rect.top
        if win_w <= 0 or win_h <= 0:
            return _WINDOW_FRAME_CACHE.get(hwnd)

        dpr = get_screen_dpr()
        phys_rx = int(round(rx * dpr))
        phys_ry = int(round(ry * dpr))
        phys_rw = max(16, int(round(rw * dpr)))
        phys_rh = max(16, int(round(rh * dpr)))

        hScreenDC = user32.GetDC(0)
        if not hScreenDC:
            return _WINDOW_FRAME_CACHE.get(hwnd)

        hMemDC = gdi32.CreateCompatibleDC(hScreenDC)
        if not hMemDC:
            user32.ReleaseDC(0, hScreenDC)
            return _WINDOW_FRAME_CACHE.get(hwnd)

        hBmp = gdi32.CreateCompatibleBitmap(hScreenDC, win_w, win_h)
        if not hBmp:
            gdi32.DeleteDC(hMemDC)
            user32.ReleaseDC(0, hScreenDC)
            return _WINDOW_FRAME_CACHE.get(hwnd)

        hOld = gdi32.SelectObject(hMemDC, hBmp)
        win_img = None

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = win_w
        bmi.bmiHeader.biHeight = -win_h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        buf = ctypes.create_string_buffer(win_w * win_h * 4)

        # Способ 1 (Строгая изоляция окна от перекрытий): PrintWindow с флагом PW_RENDERFULLCONTENT (2)
        # В Windows DWM флаг 2 рендерит буфер самого окна напрямую из DWM,
        # полностью игнорируя любые другие окна, перекрывающие или развёрнутые поверх него!
        ok_pw = user32.PrintWindow(hwnd, hMemDC, 2)
        if ok_pw:
            gdi32.GetDIBits(hMemDC, hBmp, 0, win_h, buf, ctypes.byref(bmi), 0)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape((win_h, win_w, 4))
            candidate = np.ascontiguousarray(arr[:, :, :3])
            if np.count_nonzero(candidate) > 0:
                win_img = candidate

        # Способ 2 (Резервный для стандартных GDI окон): PrintWindow с флагом 0 (WM_PRINT)
        if win_img is None:
            ok_pw0 = user32.PrintWindow(hwnd, hMemDC, 0)
            if ok_pw0:
                gdi32.GetDIBits(hMemDC, hBmp, 0, win_h, buf, ctypes.byref(bmi), 0)
                arr = np.frombuffer(buf, dtype=np.uint8).reshape((win_h, win_w, 4))
                candidate = np.ascontiguousarray(arr[:, :, :3])
                if np.count_nonzero(candidate) > 0:
                    win_img = candidate

        # Способ 3 (Крайний случай): BitBlt из DC целевого окна
        if win_img is None:
            hWinDC = user32.GetDC(hwnd)
            if hWinDC:
                ok_blt = gdi32.BitBlt(hMemDC, 0, 0, win_w, win_h, hWinDC, 0, 0, 0x00CC0020)
                user32.ReleaseDC(hwnd, hWinDC)
                if ok_blt:
                    gdi32.GetDIBits(hMemDC, hBmp, 0, win_h, buf, ctypes.byref(bmi), 0)
                    arr = np.frombuffer(buf, dtype=np.uint8).reshape((win_h, win_w, 4))
                    candidate = np.ascontiguousarray(arr[:, :, :3])
                    if np.count_nonzero(candidate) > 0:
                        win_img = candidate

        gdi32.SelectObject(hMemDC, hOld)
        gdi32.DeleteObject(hBmp)
        gdi32.DeleteDC(hMemDC)
        user32.ReleaseDC(0, hScreenDC)

        if win_img is None or np.count_nonzero(win_img) == 0:
            return _WINDOW_FRAME_CACHE.get(hwnd)

        # Кадрируем относительно координат рамки записи
        off_x = phys_rx - rect.left
        off_y = phys_ry - rect.top
        canvas = np.zeros((phys_rh, phys_rw, 3), dtype=np.uint8)

        src_x1 = max(0, off_x)
        src_y1 = max(0, off_y)
        src_x2 = min(win_w, off_x + phys_rw)
        src_y2 = min(win_h, off_y + phys_rh)

        dst_x1 = max(0, -off_x)
        dst_y1 = max(0, -off_y)
        dst_w = max(0, src_x2 - src_x1)
        dst_h = max(0, src_y2 - src_y1)

        if dst_w > 0 and dst_h > 0:
            canvas[dst_y1:dst_y1 + dst_h, dst_x1:dst_x1 + dst_w] = win_img[src_y1:src_y1 + dst_h, src_x1:src_x1 + dst_w]

        if np.count_nonzero(canvas) > 0:
            _WINDOW_FRAME_CACHE[hwnd] = canvas
            return canvas
        else:
            return _WINDOW_FRAME_CACHE.get(hwnd, canvas)

    except Exception as e:
        print(f"[ScreenLock] capture_window_bgr failed: {e}")
        return _WINDOW_FRAME_CACHE.get(hwnd)


def capture_window_or_screen_bgr(rx: int, ry: int, rw: int, rh: int, target_hwnd: int | None = None) -> np.ndarray:
    """
    Универсальный захват:
    - Если target_hwnd указан: строго изолированный захват целевого окна без чужих перекрывающих окон.
    - В противном случае (по умолчанию): прямой захват экрана под рамкой через safe_grab_screen_bgr.
    """
    if target_hwnd is not None and int(target_hwnd) > 0:
        hwnd = int(target_hwnd)
        if user32.IsWindow(hwnd):
            with SCREEN_CAPTURE_LOCK:
                win_bgr = capture_window_bgr(hwnd, rx, ry, rw, rh)
                if win_bgr is not None and win_bgr.size > 0 and np.count_nonzero(win_bgr) > 0:
                    return win_bgr
                cached = _WINDOW_FRAME_CACHE.get(hwnd)
                if cached is not None and cached.size > 0 and np.count_nonzero(cached) > 0:
                    return cached

    return safe_grab_screen_bgr(rx, ry, rw, rh)
