# -*- coding: utf-8 -*-
"""
Звуковые эффекты приложения Framio:
- Реалистичный механический звук затвора фотоаппарата для снимков экрана.
- Стильный мягкий двухтональный звук для всплывающих уведомлений (тостов).
"""

import sys
import io
import wave
import math
import random
import threading
import struct

_SHUTTER_WAV = None
_NOTIFICATION_WAV = None
_LOCK = threading.Lock()


def _generate_tactile_soft_silent_wav() -> bytes:
    """Генерирует тактильный мягкий приглушенный щелчок (11_Tactile_Soft_Silent).
    
    Длительность 410 мс:
    - 0..45 мс: мягкий тактильный провал мембраны (1800 -> 900 Гц)
    - 30..200 мс: мягкий упор силиконового демпфера (310 Гц и 140 Гц)
    - 160..390 мс: бархатное акустическое затухание (480 Гц)
    """
    sample_rate = 44100
    dur = 0.41
    n = int(sample_rate * dur)
    res = [0.0] * n
    for i in range(n):
        t = i / sample_rate
        if t < 0.045:
            res[i] += math.sin(2 * math.pi * (1800 - t * 20000) * t) * 0.5 * math.exp(-t * 95)
        # Мягкий резиновый упор
        if 0.03 <= t < 0.20:
            t2 = t - 0.03
            res[i] += math.sin(2 * math.pi * 310 * t2) * 0.7 * math.exp(-t2 * 32)
            res[i] += math.sin(2 * math.pi * 140 * t2) * 0.55 * math.exp(-t2 * 26)
        # Бархатный отклик
        if 0.16 <= t < 0.39:
            t3 = t - 0.16
            res[i] += math.sin(2 * math.pi * 480 * t3) * 0.15 * math.exp(-t3 * 22)

    peak = max(max(abs(s) for s in res), 0.001)
    scale = 0.92 / max(peak, 0.92)
    int_samples = [int(max(-1.0, min(1.0, s * scale)) * 32767) for s in res]

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        raw_data = struct.pack(f'<{len(int_samples)}h', *int_samples)
        wf.writeframes(raw_data)

    return buf.getvalue()


def _generate_shutter_wav() -> bytes:
    """Генерирует тактильный мягкий щелчок для снимков экрана."""
    return _generate_tactile_soft_silent_wav()


def _generate_notification_wav() -> bytes:
    """Генерирует тактильный мягкий щелчок для всплывающих уведомлений."""
    return _generate_tactile_soft_silent_wav()


def get_shutter_sound_bytes() -> bytes:
    """Возвращает кэшированные байты WAV-файла звука затвора камеры."""
    global _SHUTTER_WAV
    if _SHUTTER_WAV is None:
        with _LOCK:
            if _SHUTTER_WAV is None:
                _SHUTTER_WAV = _generate_shutter_wav()
    return _SHUTTER_WAV


def get_notification_sound_bytes() -> bytes:
    """Возвращает кэшированные байты WAV-файла звука уведомления."""
    global _NOTIFICATION_WAV
    if _NOTIFICATION_WAV is None:
        with _LOCK:
            if _NOTIFICATION_WAV is None:
                _NOTIFICATION_WAV = _generate_notification_wav()
    return _NOTIFICATION_WAV


def _play_wav_bytes(data: bytes):
    """Надёжно воспроизводит аудиоданные WAV без блокировки интерфейса и без сбоев."""
    if sys.platform != "win32" or not data:
        return
    try:
        # Вызов Win32 API winmm PlaySoundW напрямую с флагами:
        # SND_MEMORY (0x0004) | SND_ASYNC (0x0001) | SND_NODEFAULT (0x0002)
        import ctypes
        res = ctypes.windll.winmm.PlaySoundW(data, 0, 0x0004 | 0x0001 | 0x0002)
        if not res:
            raise RuntimeError("PlaySoundW returned 0")
    except Exception:
        try:
            import winsound
            import threading
            threading.Thread(
                target=winsound.PlaySound,
                args=(data, winsound.SND_MEMORY),
                daemon=True
            ).start()
        except Exception:
            pass


def play_capture_sound():
    """Воспроизводит реалистичный механический звук затвора фотоаппарата."""
    data = get_shutter_sound_bytes()
    _play_wav_bytes(data)


def play_notification_sound():
    """Воспроизводит мягкий современный звук всплывающего уведомления."""
    data = get_notification_sound_bytes()
    _play_wav_bytes(data)
