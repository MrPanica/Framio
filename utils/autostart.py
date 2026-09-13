# -*- coding: utf-8 -*-
"""
Управление автозагрузкой Framio при старте Windows через ветку реестра HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run.
Не требует прав администратора (работает в пользовательском окружении).
"""

import sys
import winreg
from pathlib import Path

REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "Framio"


def is_windows_autostart_enabled() -> bool:
    """Проверяет, включен ли автозапуск приложения в реестре Windows."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ) as key:
            for name in (APP_NAME, "LightCap"):
                try:
                    val, _ = winreg.QueryValueEx(key, name)
                    if val:
                        return True
                except FileNotFoundError:
                    pass
            return False
    except FileNotFoundError:
        return False
    except Exception:
        return False


def set_windows_autostart(enabled: bool) -> bool:
    """
    Включает или выключает автозапуск приложения в реестре Windows.
    Запускает программу в свёрнутом виде в системный трей (--minimized).
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                if getattr(sys, "frozen", False):
                    exe_path = str(Path(sys.executable).resolve())
                    cmd = f'"{exe_path}" --minimized'
                else:
                    repo_root = Path(__file__).parent.parent
                    compiled_exe = repo_root / "dist-onefile" / "Framio.exe"
                    folder_exe = repo_root / "dist" / "Framio" / "Framio.exe"
                    if compiled_exe.exists():
                        cmd = f'"{compiled_exe.resolve()}" --minimized'
                    elif folder_exe.exists():
                        cmd = f'"{folder_exe.resolve()}" --minimized'
                    else:
                        python_exe = Path(sys.executable)
                        pythonw_exe = python_exe.with_name("pythonw.exe")
                        exe_to_use = pythonw_exe if pythonw_exe.exists() else python_exe
                        main_py = str((repo_root / "main.py").resolve())
                        cmd = f'"{exe_to_use.resolve()}" "{main_py}" --minimized'
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                try:
                    winreg.DeleteValue(key, "LightCap")
                except Exception:
                    pass
            else:
                for name in (APP_NAME, "LightCap"):
                    try:
                        winreg.DeleteValue(key, name)
                    except FileNotFoundError:
                        pass
        return True
    except Exception as e:
        print(f"[Autostart] Ошибка настройки автозапуска: {e}")
        return False
