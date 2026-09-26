# -*- coding: utf-8 -*-
"""
Звуковые эффекты приложения Framio: реалистичный звук щелчка затвора фотоаппарата.
"""

import sys
import io
import wave
import math
import random
import threading
import struct

_SHUTTER_WAV = None
_LOCK = threading.Lock()


def _generate_shutter_wav() -> bytes:
    """Генерирует высококачественный двухфазный механический щелчок шторки/зеркала затвора камеры."""
    sample_rate = 44100
    duration = 0.16  # 160 мс
    num_samples = int(sample_rate * duration)

    # Детерминированный генератор для воспроизводимого идеального звучания
    rng = random.Random(42)
    samples = []

    for i in range(num_samples):
        t = i / sample_rate
        val = 0.0

        # Щелчок 1: открытие первой шторки затвора (0 .. 25 мс)
        if 0.0 <= t < 0.025:
            t1 = t
            env1 = math.exp(-t1 * 260)
            click1 = math.sin(2 * math.pi * (3200 - t1 * 80000) * t1) * 0.75
            noise1 = (rng.random() * 2 - 1) * 0.5 * env1
            body1 = math.sin(2 * math.pi * 750 * t1) * 0.4 * env1
            val += (click1 + noise1 + body1) * env1

        # Натяжение пружины между шторками (20 .. 45 мс)
        if 0.020 <= t < 0.045:
            t_mid = t - 0.020
            val += (rng.random() * 2 - 1) * 0.06 * math.sin(math.pi * t_mid / 0.025)

        # Щелчок 2: закрытие второй шторки и возврат зеркала (45 .. 150 мс)
        if 0.045 <= t < 0.15:
            t2 = t - 0.045
            env2 = math.exp(-t2 * 68)
            snap2 = math.sin(2 * math.pi * (3800 - t2 * 85000) * t2) * 0.95 * math.exp(-t2 * 210)
            noise2 = (rng.random() * 2 - 1) * 0.65 * math.exp(-t2 * 125)
            thud2 = math.sin(2 * math.pi * 320 * t2) * 0.5 * math.exp(-t2 * 55)
            thud3 = math.sin(2 * math.pi * 160 * t2) * 0.35 * math.exp(-t2 * 40)
            val += (snap2 + noise2 + thud2 + thud3) * env2

        val = max(-1.0, min(1.0, val * 0.85))
        samples.append(int(val * 32767))

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        raw_data = struct.pack(f'<{len(samples)}h', *samples)
        wf.writeframes(raw_data)

    return buf.getvalue()


def get_shutter_sound_bytes() -> bytes:
    """Возвращает кэшированные байты WAV-файла звука затвора камеры."""
    global _SHUTTER_WAV
    if _SHUTTER_WAV is None:
        with _LOCK:
            if _SHUTTER_WAV is None:
                _SHUTTER_WAV = _generate_shutter_wav()
    return _SHUTTER_WAV


def play_capture_sound():
    """Воспроизводит реалистичный механический звук затвора фотоаппарата без блокировки интерфейса."""
    if sys.platform != "win32":
        return
    try:
        import winsound
        data = get_shutter_sound_bytes()
        # SND_MEMORY (4) | SND_ASYNC (1) | SND_NODEFAULT (2)
        winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
