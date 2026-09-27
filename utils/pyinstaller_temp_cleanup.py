# -*- coding: utf-8 -*-
"""Безопасная уборка оставшихся каталогов one-file PyInstaller в TEMP."""

import shutil
import sys
import tempfile
import time
from pathlib import Path


def cleanup_stale_pyinstaller_temp_dirs(max_age_hours: float = 0.2) -> int:
    """Удаляет только старые каталоги ``_MEI*`` Framio/PyInstaller.

    Нормальный запуск one-file удаляет свой каталог сам. Если процесс был
    завершён аварийно, каталог может остаться. Удаляются только каталоги с
    точным префиксом ``_MEI`` и возрастом больше 12 минут (или переданного max_age_hours);
    текущий ``sys._MEIPASS`` и занятые Windows-файлы не трогаются.
    """
    temp_root = Path(tempfile.gettempdir())
    current_meipass = (
        Path(getattr(sys, "_MEIPASS", "")).resolve()
        if getattr(sys, "_MEIPASS", "")
        else None
    )
    cutoff = time.time() - max(60.0, float(max_age_hours) * 3600.0)
    removed = 0

    try:
        candidates = list(temp_root.iterdir())
    except OSError:
        return 0

    for path in candidates:
        if not path.is_dir() or not path.name.startswith("_MEI"):
            continue
        try:
            resolved = path.resolve()
            if current_meipass is not None and resolved == current_meipass:
                continue
            if path.stat().st_mtime >= cutoff:
                continue
            shutil.rmtree(path)
            removed += 1
        except OSError:
            # Занятые DLL или каталог другого ещё работающего процесса
            # останутся до следующего запуска.
            continue

    return removed
