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


def _generate_shutter_wav() -> bytes:
    """Генерирует высококачественный механический щелчок шторки/зеркала затвора камеры."""
    sample_rate = 44100
    duration = 0.17  # 170 мс
    num_samples = int(sample_rate * duration)

    rng = random.Random(1337)
    samples = []

    for i in range(num_samples):
        t = i / sample_rate
        val = 0.0

        # Фаза 1: подъём зеркала и открытие первой шторки (0 .. 28 мс)
        if 0.0 <= t < 0.028:
            t1 = t
            env1 = math.exp(-t1 * 220)
            click1 = math.sin(2 * math.pi * (3400 - t1 * 60000) * t1) * 0.85
            snap1 = math.sin(2 * math.pi * 1250 * t1) * 0.5
            noise1 = (rng.random() * 2 - 1) * 0.45 * env1
            val += (click1 + snap1 + noise1) * env1

        # Фаза 2: микро-пауза и натяжение механики (22 .. 48 мс)
        if 0.022 <= t < 0.048:
            t_mid = t - 0.022
            env_mid = math.sin(math.pi * t_mid / 0.026)
            whir = (rng.random() * 2 - 1) * 0.12 * env_mid
            val += whir

        # Фаза 3: срабатывание второй шторки и удар зеркала (48 .. 165 мс)
        if 0.048 <= t < 0.165:
            t2 = t - 0.048
            env2 = math.exp(-t2 * 62)
            snap2 = math.sin(2 * math.pi * (2600 - t2 * 14000) * t2) * 0.95 * math.exp(-t2 * 180)
            clack = math.sin(2 * math.pi * 920 * t2) * 0.6 * math.exp(-t2 * 90)
            body = math.sin(2 * math.pi * 310 * t2) * 0.55 * math.exp(-t2 * 50)
            sub = math.sin(2 * math.pi * 145 * t2) * 0.4 * math.exp(-t2 * 35)
            noise2 = (rng.random() * 2 - 1) * 0.55 * math.exp(-t2 * 110)
            val += (snap2 + clack + body + sub + noise2) * env2

        val = max(-1.0, min(1.0, val * 0.92))
        samples.append(int(val * 32767))

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        raw_data = struct.pack(f'<{len(samples)}h', *samples)
        wf.writeframes(raw_data)

    return buf.getvalue()


def _generate_notification_wav() -> bytes:
    """Генерирует стильный, мягкий и четкий двухтональный звук всплывающего уведомления."""
    sample_rate = 44100
    duration = 0.22  # 220 мс
    num_samples = int(sample_rate * duration)
    samples = []

    for i in range(num_samples):
        t = i / sample_rate
        val = 0.0

        # Нота 1: A5 (880 Гц)
        if t < 0.14:
            t1 = t
            env1 = math.sin(min(1.0, t1 / 0.006) * math.pi * 0.5) * math.exp(-t1 * 26)
            tone1 = math.sin(2 * math.pi * 880.0 * t1) + 0.25 * math.sin(2 * math.pi * 1760.0 * t1)
            val += tone1 * env1 * 0.5

        # Нота 2: E6 (1318.5 Гц)
        if t >= 0.055:
            t2 = t - 0.055
            env2 = math.sin(min(1.0, t2 / 0.006) * math.pi * 0.5) * math.exp(-t2 * 20)
            tone2 = math.sin(2 * math.pi * 1318.5 * t2) + 0.2 * math.sin(2 * math.pi * 2637.0 * t2)
            val += tone2 * env2 * 0.65

        val = max(-1.0, min(1.0, val * 0.9))
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


def get_notification_sound_bytes() -> bytes:
    """Возвращает кэшированные байты WAV-файла звука уведомления."""
    global _NOTIFICATION_WAV
    if _NOTIFICATION_WAV is None:
        with _LOCK:
            if _NOTIFICATION_WAV is None:
                _NOTIFICATION_WAV = _generate_notification_wav()
    return _NOTIFICATION_WAV


def play_capture_sound():
    """Воспроизводит реалистичный механический звук затвора фотоаппарата."""
    if sys.platform != "win32":
        return
    try:
        import winsound
        data = get_shutter_sound_bytes()
        winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass


def play_notification_sound():
    """Воспроизводит мягкий современный звук всплывающего уведомления."""
    if sys.platform != "win32":
        return
    try:
        import winsound
        data = get_notification_sound_bytes()
        winsound.PlaySound(data, winsound.SND_MEMORY | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
    except Exception:
        pass
