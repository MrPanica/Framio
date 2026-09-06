# -*- coding: utf-8 -*-
"""
Win32 утилиты для корректного захвата экрана в играх (включая безрамочные / borderless)
и полноэкранных приложениях.
Обеспечивает снятие ограничений мыши (ClipCursor), сброс захвата (ReleaseCapture),
восстановление видимости курсора (ShowCursor) и надежную передачу фокуса (SetForegroundWindow).
"""

import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

# Настройка типов Win32 функций для 64-битной Windows
user32.ClipCursor.argtypes = [ctypes.c_void_p]
user32.ClipCursor.restype = wintypes.BOOL

user32.ReleaseCapture.argtypes = []
user32.ReleaseCapture.restype = wintypes.BOOL

user32.ShowCursor.argtypes = [wintypes.BOOL]
user32.ShowCursor.restype = ctypes.c_int

user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND

user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL

user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL

user32.SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.UINT
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL

user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
user32.keybd_event.restype = None


def release_mouse_traps():
    """
    Освобождает курсор от любых ловушек оконных (borderless) и полноэкранных 3D-игр:
    1. ClipCursor(None) — снимает фиксацию мыши в центре экрана или границах окна игры.
    2. ReleaseCapture() — сбрасывает монопольный захват мыши игрой.
    3. ShowCursor(True) — восстанавливает системный курсор, если игра сделала его невидимым.
    """
    try:
        user32.ClipCursor(None)
        user32.ReleaseCapture()
        while user32.ShowCursor(True) < 0:
            pass
    except Exception as e:
        print(f"[Win32Helper] Ошибка release_mouse_traps: {e}")


def force_foreground_window(hwnd: int):
    """
    Принудительно делает окно оверлея активным на переднем плане, обходя LockSetForegroundWindow.
    Это деактивирует окно игры (отправляет WM_ACTIVATE / WM_KILLFOCUS), благодаря чему игра
    автоматически прекращает крутить камеру и удерживать мышь в центре.
    """
    try:
        release_mouse_traps()

        HWND_TOPMOST = -1
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040

        # Ставим окно поверх всех остальных (включая безрамочные игры)
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)

        fore_hwnd = user32.GetForegroundWindow()
        cur_thread = kernel32.GetCurrentThreadId()
        fore_thread = user32.GetWindowThreadProcessId(fore_hwnd, None) if fore_hwnd else 0

        # Метод 1: Подключение к потоку активного окна через AttachThreadInput
        if fore_thread and fore_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fore_thread, True)
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.AttachThreadInput(cur_thread, fore_thread, False)
        else:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)

        # Метод 2: Обход системного запрета через имитацию клавиши Alt (VK_MENU)
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

    except Exception as e:
        print(f"[Win32Helper] Ошибка force_foreground_window: {e}")


def is_system_dark_theme() -> bool:
    """Проверяет, включена ли тёмная тема в Windows через реестр AppsUseLightTheme."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return val == 0
    except Exception:
        return True

