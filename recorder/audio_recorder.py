# -*- coding: utf-8 -*-
"""
Запись звука из игр (Team Fortress 2 и др.), системных динамиков и микрофона в WAV-файл.
Использует Windows WASAPI Loopback через pyaudiowpatch для прямого цифрового захвата
звука приложений и микширует его со звуком микрофона.
"""

import os
import wave
import threading
from pathlib import Path
import numpy as np

try:
    import pyaudiowpatch as pyaudio
except ImportError:
    try:
        import pyaudio
    except ImportError:
        pyaudio = None

AUDIO_INIT_LOCK = threading.Lock()


def resample_audio(data: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Быстрая передискретизация аудио через линейную интерполяцию numpy."""
    if orig_sr == target_sr or len(data) == 0:
        return data
    orig_len = len(data)
    target_len = int(round(orig_len * target_sr / orig_sr))
    if target_len <= 0:
        return np.zeros((0, data.shape[1] if data.ndim > 1 else 1), dtype=data.dtype)
    orig_x = np.linspace(0, orig_len, orig_len, endpoint=False)
    target_x = np.linspace(0, orig_len, target_len, endpoint=False)
    if data.ndim == 1:
        return np.interp(target_x, orig_x, data).astype(data.dtype)
    else:
        out = np.empty((target_len, data.shape[1]), dtype=data.dtype)
        for ch in range(data.shape[1]):
            out[:, ch] = np.interp(target_x, orig_x, data[:, ch])
        return out


class AudioRecorder:
    def __init__(self, output_wav_path: str, record_mic: bool = True, record_system: bool = True, samplerate: int = 48000):
        self.output_wav_path = output_wav_path
        self.record_mic = record_mic
        self.record_system = record_system
        self.mic_muted = not record_mic
        self.system_muted = not record_system
        self.target_samplerate = samplerate
        self.channels = 2  # stereo

        self.is_recording = False
        self.is_paused = False

        self._pa = None
        self._sys_stream = None
        self._mic_stream = None
        self._sys_frames = []
        self._mic_frames = []

        self._sys_rate = samplerate
        self._sys_channels = 2
        self._mic_rate = samplerate
        self._mic_channels = 2

    def set_mic_muted(self, muted: bool):
        self.mic_muted = muted
        print(f"[AudioRecorder] Микрофон: {'ОТКЛЮЧЕН (MUTE)' if muted else 'ВКЛЮЧЕН'}")

    def set_system_muted(self, muted: bool):
        self.system_muted = muted
        print(f"[AudioRecorder] Системный звук: {'ОТКЛЮЧЕН (MUTE)' if muted else 'ВКЛЮЧЕН'}")

    def start(self):
        if pyaudio is None:
            return

        Path(self.output_wav_path).parent.mkdir(parents=True, exist_ok=True)
        self.is_recording = True
        self.is_paused = False
        self._sys_frames.clear()
        self._mic_frames.clear()

        try:
            with AUDIO_INIT_LOCK:
                self._pa = pyaudio.PyAudio()
        except Exception as e:
            print(f"[AudioRecorder] Ошибка инициализации PyAudio: {e}")
            self.is_recording = False
            return

        # 1. Захват системного звука / игры через WASAPI Loopback
        try:
            loopback_dev = None
            if hasattr(self._pa, "get_default_wasapi_loopback"):
                try:
                    loopback_dev = self._pa.get_default_wasapi_loopback()
                except Exception as e:
                    print(f"[AudioRecorder] Не удалось получить дефолтный WASAPI loopback: {e}")

            if loopback_dev is None and hasattr(pyaudio, "paWASAPI"):
                # Ищем подходящее loopback устройство вручную
                for i in range(self._pa.get_device_count()):
                    dev_info = self._pa.get_device_info_by_index(i)
                    if dev_info.get("isLoopbackDevice", False):
                        loopback_dev = dev_info
                        break

            if loopback_dev:
                self._sys_rate = int(loopback_dev["defaultSampleRate"])
                self._sys_channels = int(loopback_dev["maxInputChannels"])

                def sys_callback(in_data, frame_count, time_info, status):
                    if self.is_recording and not self.is_paused:
                        if self.system_muted:
                            self._sys_frames.append(b"\x00" * len(in_data))
                        else:
                            self._sys_frames.append(in_data)
                    return (None, pyaudio.paContinue)

                self._sys_stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=self._sys_channels,
                    rate=self._sys_rate,
                    input=True,
                    input_device_index=loopback_dev["index"],
                    stream_callback=sys_callback
                )
                self._sys_stream.start_stream()
                print(f"[AudioRecorder] Системный звук (игры) захватывается с: {loopback_dev['name']} ({self._sys_rate} Hz, {self._sys_channels} ch)")
            else:
                print("[AudioRecorder] WASAPI loopback устройство не найдено в системе.")
        except Exception as sys_err:
            print(f"[AudioRecorder] Ошибка запуска захвата звука игры: {sys_err}")

        # 2. Захват микрофона
        try:
            mic_dev = self._pa.get_default_input_device_info()
            if mic_dev and mic_dev.get("maxInputChannels", 0) > 0:
                self._mic_rate = int(mic_dev["defaultSampleRate"])
                self._mic_channels = min(2, max(1, int(mic_dev["maxInputChannels"])))

                def mic_callback(in_data, frame_count, time_info, status):
                    if self.is_recording and not self.is_paused:
                        if self.mic_muted:
                            self._mic_frames.append(b"\x00" * len(in_data))
                        else:
                            self._mic_frames.append(in_data)
                    return (None, pyaudio.paContinue)

                self._mic_stream = self._pa.open(
                    format=pyaudio.paInt16,
                    channels=self._mic_channels,
                    rate=self._mic_rate,
                    input=True,
                    input_device_index=mic_dev["index"],
                    stream_callback=mic_callback
                )
                self._mic_stream.start_stream()
                print(f"[AudioRecorder] Микрофон захватывается с: {mic_dev['name']} ({self._mic_rate} Hz, {self._mic_channels} ch)")
            else:
                print("[AudioRecorder] Микрофон по умолчанию не найден.")
        except Exception as mic_err:
            print(f"[AudioRecorder] Ошибка запуска захвата микрофона: {mic_err}")

        if self._sys_stream is None and self._mic_stream is None:
            print("[AudioRecorder] Не удалось запустить ни один аудиопоток.")
            self.is_recording = False

    def pause(self):
        self.is_paused = True

    def resume(self):
        self.is_paused = False

    def stop(self, target_duration: float = None):
        if not self.is_recording:
            return

        self.is_recording = False

        # Остановка потоков
        for stream in (self._sys_stream, self._mic_stream):
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
        self._sys_stream = None
        self._mic_stream = None

        if self._pa is not None:
            try:
                with AUDIO_INIT_LOCK:
                    self._pa.terminate()
            except Exception:
                pass
            self._pa = None

        # Обработка и сведение аудиодорожек
        try:
            arr_sys = None
            if self._sys_frames:
                raw_sys = b"".join(self._sys_frames)
                if len(raw_sys) > 0:
                    arr_sys = np.frombuffer(raw_sys, dtype=np.int16).reshape((-1, self._sys_channels))
                    if self._sys_channels == 1:
                        arr_sys = np.repeat(arr_sys, 2, axis=1)
                    if self._sys_rate != self.target_samplerate:
                        arr_sys = resample_audio(arr_sys, self._sys_rate, self.target_samplerate)

            arr_mic = None
            if self._mic_frames:
                raw_mic = b"".join(self._mic_frames)
                if len(raw_mic) > 0:
                    arr_mic = np.frombuffer(raw_mic, dtype=np.int16).reshape((-1, self._mic_channels))
                    if self._mic_channels == 1:
                        arr_mic = np.repeat(arr_mic, 2, axis=1)
                    if self._mic_rate != self.target_samplerate:
                        arr_mic = resample_audio(arr_mic, self._mic_rate, self.target_samplerate)

            # Микширование
            final_audio = None
            if arr_sys is not None and arr_mic is not None:
                max_len = max(len(arr_sys), len(arr_mic))
                if len(arr_sys) < max_len:
                    pad = np.zeros((max_len - len(arr_sys), 2), dtype=np.int16)
                    arr_sys = np.vstack([arr_sys, pad])
                if len(arr_mic) < max_len:
                    pad = np.zeros((max_len - len(arr_mic), 2), dtype=np.int16)
                    arr_mic = np.vstack([arr_mic, pad])
                mixed = arr_sys.astype(np.int32) + arr_mic.astype(np.int32)
                final_audio = np.clip(mixed, -32768, 32767).astype(np.int16)
            elif arr_sys is not None:
                final_audio = arr_sys
            elif arr_mic is not None:
                final_audio = arr_mic

            # Дополнение тишиной до точной длительности видео, чтобы видео никогда не обрезалось
            if target_duration is not None and target_duration > 0:
                target_samples = int(round(target_duration * self.target_samplerate))
                if final_audio is None:
                    final_audio = np.zeros((target_samples, 2), dtype=np.int16)
                elif len(final_audio) < target_samples:
                    pad = np.zeros((target_samples - len(final_audio), 2), dtype=np.int16)
                    final_audio = np.vstack([final_audio, pad])

            if final_audio is not None and len(final_audio) > 0:
                with wave.open(self.output_wav_path, "wb") as wf:
                    wf.setnchannels(self.channels)
                    wf.setsampwidth(2)  # int16
                    wf.setframerate(self.target_samplerate)
                    wf.writeframes(final_audio.tobytes())
                print(f"[AudioRecorder] Звуковой файл сохранен: {self.output_wav_path} ({len(final_audio)} сэмплов, {len(final_audio)/self.target_samplerate:.2f} сек.)")
            else:
                print("[AudioRecorder] Аудиоданные не записаны.")
        except Exception as e:
            print(f"[AudioRecorder] Ошибка сохранения итогового WAV: {e}")
