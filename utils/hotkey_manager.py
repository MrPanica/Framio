# -*- coding: utf-8 -*-
"""
Менеджер глобальных горячих клавиш Windows (Win32 RegisterHotKey + fallback на keyboard).
"""

import sys
import ctypes
from ctypes import wintypes
import threading
from PyQt6.QtCore import QObject, pyqtSignal

# Win32 константы
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

VK_SNAPSHOT = 0x2C  # Print Screen
VK_MAPPING = {
    "print screen": VK_SNAPSHOT,
    "printscreen": VK_SNAPSHOT,
    "prntscrn": VK_SNAPSHOT,
    "prtscn": VK_SNAPSHOT,
    "prtsc": VK_SNAPSHOT,
    "prt sc": VK_SNAPSHOT,
    "snapshot": VK_SNAPSHOT,
    "print": VK_SNAPSHOT,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73,
    "f5": 0x74, "f6": 0x75, "f7": 0x76, "f8": 0x77,
    "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "space": 0x20, "esc": 0x1B, "escape": 0x1B,
    "tab": 0x09, "backspace": 0x08, "enter": 0x0D,
    "insert": 0x2D, "delete": 0x2E, "home": 0x24, "end": 0x23,
    "page up": 0x21, "pageup": 0x21, "page down": 0x22, "pagedown": 0x22,
    "pause": 0x13, "scroll lock": 0x91, "scrolllock": 0x91,
}


def parse_hotkey_string(hotkey_str: str):
    """
    Парсит строку комбинации (например, 'Print Screen' или 'Ctrl+Shift+Print Screen') в (modifiers, vk_code).
    Поддерживает как комбинации с модификаторами, так и одиночные клавиши (Print Screen, F1-F12 и т.д.).
    """
    if not hotkey_str or not hotkey_str.strip():
        return 0, 0

    cleaned = hotkey_str.strip()
    parts = [p.strip().lower() for p in cleaned.split("+") if p.strip()]
    modifiers = 0
    vk = 0

    for part in parts:
        if part in ("ctrl", "control"):
            modifiers |= MOD_CONTROL
        elif part in ("shift",):
            modifiers |= MOD_SHIFT
        elif part in ("alt",):
            modifiers |= MOD_ALT
        elif part in ("win", "windows"):
            modifiers |= MOD_WIN
        elif part in VK_MAPPING:
            vk = VK_MAPPING[part]
        elif len(part) == 1:
            vk = ord(part.upper())

    return modifiers, vk


def snapshot_hotkey_matches_modifiers(hotkey_str: str, active_modifiers: int) -> bool:
    """Проверяет, совпадает ли Print Screen-хоткей с зажатыми модификаторами."""
    modifiers, vk = parse_hotkey_string(hotkey_str)
    return vk == VK_SNAPSHOT and modifiers == active_modifiers


class GlobalHotkeyManager(QObject):
    capture_triggered = pyqtSignal()
    quick_fullscreen_triggered = pyqtSignal()
    screenshot_triggered = pyqtSignal()
    record_fullscreen_triggered = pyqtSignal()
    stop_recording_triggered = pyqtSignal()
    hotkey_triggered = pyqtSignal()  # Псевдоним для совместимости с кодом захвата области

    ID_CAPTURE = 101
    ID_RECORD_FULLSCREEN = 102
    ID_STOP_RECORDING = 103
    ID_QUICK_FULLSCREEN = 104
    ID_SCREENSHOT = 105

    def __init__(self, hotkey_capture="Ctrl+Shift+Print Screen",
                 hotkey_quick_fullscreen="Ctrl+Print Screen",
                 hotkey_record_fullscreen="Ctrl+Shift+F9",
                 hotkey_stop_recording="Ctrl+Shift+F10", parent=None,
                 hotkey_screenshot="Print Screen"):
        super().__init__(parent)
        self.hotkey_capture = hotkey_capture
        self.hotkey_quick_fullscreen = hotkey_quick_fullscreen
        self.hotkey_screenshot = hotkey_screenshot
        self.hotkey_record_fullscreen = hotkey_record_fullscreen
        self.hotkey_stop_recording = hotkey_stop_recording
        self.hotkey_str = hotkey_capture

        self._thread = None
        self._stop_event = threading.Event()
        self._thread_id = None
        self._registered_ids = set()
        self._use_keyboard_lib = False

    def start(self):
        if sys.platform != "win32":
            return
        self.stop()
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            if self._thread_id:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=1.0)
            self._thread = None

        if self._use_keyboard_lib:
            try:
                import keyboard
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
            self._use_keyboard_lib = False

    def suspend(self) -> bool:
        """Временно отключает глобальные хуки и возвращает их прежнее состояние."""
        was_active = bool(
            (self._thread and self._thread.is_alive()) or self._use_keyboard_lib
        )
        self.stop()
        return was_active

    def resume(self, was_active: bool) -> None:
        """Восстанавливает глобальные хуки после временной паузы."""
        if was_active:
            self.start()

    def update_hotkey(self, new_hotkey_str):
        self.hotkey_capture = new_hotkey_str
        self.hotkey_str = new_hotkey_str
        self.start()

    def update_hotkeys(self, capture=None, quick_fullscreen=None, record_fullscreen=None, stop_recording=None, screenshot=None):
        if capture:
            self.hotkey_capture = capture
            self.hotkey_str = capture
        if quick_fullscreen:
            self.hotkey_quick_fullscreen = quick_fullscreen
        if screenshot:
            self.hotkey_screenshot = screenshot
        if record_fullscreen:
            self.hotkey_record_fullscreen = record_fullscreen
        if stop_recording:
            self.hotkey_stop_recording = stop_recording
        self.start()

    def _run_loop(self):
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32
        self._registered_ids = set()

        hotkey_configs = [
            (self.ID_CAPTURE, self.hotkey_capture, self.capture_triggered),
            (self.ID_QUICK_FULLSCREEN, self.hotkey_quick_fullscreen, self.quick_fullscreen_triggered),
            (self.ID_SCREENSHOT, self.hotkey_screenshot, self.screenshot_triggered),
            (self.ID_RECORD_FULLSCREEN, self.hotkey_record_fullscreen, self.record_fullscreen_triggered),
            (self.ID_STOP_RECORDING, self.hotkey_stop_recording, self.stop_recording_triggered),
        ]

        signal_map = {}
        snapshot_configs = []

        for hk_id, hk_str, sig in hotkey_configs:
            if not hk_str or not hk_str.strip():
                continue
            mods, vk = parse_hotkey_string(hk_str)
            if not vk:
                print(f"[HotkeyManager] Не удалось разобрать хоткей: {hk_str}")
                continue

            # ВАЖНО: В Windows одиночная клавиша Print Screen (mods == 0 и vk == VK_SNAPSHOT)
            # перехватывается ядром/DWM для стандартных ножниц и снимка экрана. RegisterHotKey возвращает 1,
            # но сообщение WM_HOTKEY никогда не генерируется! Для неё обязательно используется прямой хук.
            if vk == VK_SNAPSHOT and mods == 0:
                print(f"[HotkeyManager] Одиночная клавиша Print Screen для id={hk_id}. Подключаем хук с подавлением.")
                snapshot_configs.append((hk_id, hk_str, sig))
                self._fallback_keyboard(hk_str, sig, suppress=True)
                continue

            success = user32.RegisterHotKey(None, hk_id, mods | MOD_NOREPEAT, vk)
            if not success:
                # Попробуем без MOD_NOREPEAT
                success = user32.RegisterHotKey(None, hk_id, mods, vk)

            if success:
                self._registered_ids.add(hk_id)
                signal_map[hk_id] = sig
            else:
                print(f"[HotkeyManager] RegisterHotKey не удался для {hk_str} (id={hk_id}), подключаем keyboard hook...")
                self._fallback_keyboard(hk_str, sig)

        # Резервный низкоуровневый хук Win32 WH_KEYBOARD_LL для одиночной Print Screen
        h_ll_hook = None
        hook_proc_ref = None
        if snapshot_configs:
            try:
                class KBDLLHOOKSTRUCT(ctypes.Structure):
                    _fields_ = [
                        ("vkCode", wintypes.DWORD),
                        ("scanCode", wintypes.DWORD),
                        ("flags", wintypes.DWORD),
                        ("time", wintypes.DWORD),
                        ("dwExtraInfo", ctypes.c_void_p)
                    ]

                HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, ctypes.c_void_p)
                user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
                user32.SetWindowsHookExW.restype = wintypes.HHOOK
                user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, ctypes.c_void_p]
                user32.CallNextHookEx.restype = ctypes.c_longlong
                user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
                user32.UnhookWindowsHookEx.restype = wintypes.BOOL

                def _ll_hook_cb(nCode, wParam, lParam):
                    if nCode >= 0 and lParam:
                        kb = KBDLLHOOKSTRUCT.from_address(lParam)
                        if kb.vkCode == VK_SNAPSHOT:
                            # 0x0100 = WM_KEYDOWN, 0x0104 = WM_SYSKEYDOWN
                            if wParam in (0x0100, 0x0104):
                                active_modifiers = 0
                                for modifier_vk, modifier_flag in (
                                    (0x11, MOD_CONTROL),  # VK_CONTROL
                                    (0x12, MOD_ALT),      # VK_MENU
                                    (0x10, MOD_SHIFT),    # VK_SHIFT
                                    (0x5B, MOD_WIN),      # VK_LWIN
                                    (0x5C, MOD_WIN),      # VK_RWIN
                                ):
                                    if user32.GetAsyncKeyState(modifier_vk) & 0x8000:
                                        active_modifiers |= modifier_flag

                                matched = False
                                for s_id, s_str, s_sig in snapshot_configs:
                                    if snapshot_hotkey_matches_modifiers(s_str, active_modifiers):
                                        s_sig.emit()
                                        if s_id == self.ID_CAPTURE:
                                            self.hotkey_triggered.emit()
                                        matched = True
                                if matched:
                                    return 1  # Подавляем системные ножницы Windows
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                hook_proc_ref = HOOKPROC(_ll_hook_cb)
                h_ll_hook = user32.SetWindowsHookExW(13, hook_proc_ref, None, 0)
                if h_ll_hook:
                    print("[HotkeyManager] Нативный WH_KEYBOARD_LL хук для Print Screen успешно активен!")
            except Exception as e_hook:
                print(f"[HotkeyManager] Ошибка установки нативного хука: {e_hook}")

        msg = wintypes.MSG()

        try:
            while not self._stop_event.is_set():
                res = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if res <= 0:
                    break
                if msg.message == WM_HOTKEY and msg.wParam in signal_map:
                    target_sig = signal_map[msg.wParam]
                    target_sig.emit()
                    if msg.wParam == self.ID_CAPTURE:
                        self.hotkey_triggered.emit()
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            if h_ll_hook:
                try:
                    user32.UnhookWindowsHookEx(h_ll_hook)
                except Exception:
                    pass
            for hk_id in list(self._registered_ids):
                user32.UnregisterHotKey(None, hk_id)
            self._registered_ids.clear()

    def _fallback_keyboard(self, hk_str: str, sig, suppress: bool = False):
        try:
            import keyboard
            self._use_keyboard_lib = True
            kb_hotkey = hk_str.strip().lower()
            if "print screen" in kb_hotkey or "printscreen" in kb_hotkey or "prtscn" in kb_hotkey or "prtsc" in kb_hotkey:
                kb_hotkey = kb_hotkey.replace("printscreen", "print screen").replace("prtscn", "print screen").replace("prtsc", "print screen")
            if kb_hotkey == "print screen":
                suppress = True
            keyboard.add_hotkey(kb_hotkey, lambda: sig.emit(), suppress=suppress)
            print(f"[HotkeyManager] Keyboard library hook активен для: {kb_hotkey} (suppress={suppress})")
        except Exception as e:
            print(f"[HotkeyManager] Ошибка fallback keyboard hook: {e}")
