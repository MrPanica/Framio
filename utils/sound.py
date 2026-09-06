# -*- coding: utf-8 -*-
"""
Звуковые эффекты приложения.
"""

import sys

def play_capture_sound():
    """Воспроизводит ненавязчивый системный звук затвора/уведомления."""
    try:
        if sys.platform == "win32":
            import winsound
            # MB_ICONASTERISK (0x40) или системный звук щелчка
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass
