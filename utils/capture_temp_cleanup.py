# -*- coding: utf-8 -*-
"""Безопасная уборка промежуточных файлов Framio в TEMP."""

import tempfile
import time
from pathlib import Path


def cleanup_stale_capture_temp_files(max_age_hours: int = 24) -> int:
    """Удаляет только старые промежуточные файлы Framio из TEMP.

    PyInstaller-файлы `_MEI*` здесь намеренно не трогаются: их обслуживает
    bootloader, и они не принадлежат рабочему каталогу записи. Эта уборка
    касается только известных имён временных MP4/WAV, которые остаются после
    сбоя или жёсткого завершения процесса.
    """
    temp_dir = Path(tempfile.gettempdir()) / "framio"
    if not temp_dir.is_dir():
        return 0

    cutoff = time.time() - max(1, int(max_age_hours)) * 60 * 60
    prefixes = ("temp_vid_", "temp_aud_", "temp_gif_")
    removed = 0
    try:
        candidates = list(temp_dir.iterdir())
    except OSError:
        return 0

    for path in candidates:
        if not path.is_file() or not path.name.startswith(prefixes):
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            # Файл может принадлежать ещё работающему процессу или быть
            # временно заблокирован Windows — оставляем его до следующего запуска.
            continue

    try:
        if not any(temp_dir.iterdir()):
            temp_dir.rmdir()
    except OSError:
        pass
    return removed
