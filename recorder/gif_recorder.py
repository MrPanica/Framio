# -*- coding: utf-8 -*-
"""
Высокоскоростная запись и сохранение GIF-анимаций (без лагов и зависаний).
"""

import os
import time
import shutil
import cv2
import numpy as np
from PIL import Image
from pathlib import Path

from .video_recorder import VideoRecorder


def _even_size(width: int, height: int) -> tuple[int, int]:
    """Возвращает минимальный чётный размер, подходящий для видеокодека."""
    return max(16, int(width) + (int(width) % 2)), max(16, int(height) + (int(height) % 2))


class DynamicGifRecorder:
    """Записывает GIF через сегменты с поддержкой изменения размера рамки.

    FFmpeg принимает raw-видео только одного размера за один поток. Поэтому
    при увеличении области открывается новый небольшой сегмент, а при
    завершении сегменты приводятся к максимальному размеру и объединяются.
    Это сохраняет содержимое расширенной области и не держит все кадры GIF в
    оперативной памяти.
    """

    SEGMENT_STEP = 32

    def __init__(self, temp_video_path: str, fps: int = 15, width: int = 800, height: int = 600):
        self.temp_video_path = temp_video_path
        self.fps = max(5, min(int(fps), 30))
        self.initial_width, self.initial_height = _even_size(width, height)
        self.segment_dir = Path(f"{temp_video_path}.segments")
        self.segments: list[tuple[str, int, int]] = []
        self._segment_recorder = None
        self._segment_width = 0
        self._segment_height = 0
        self.max_width = self.initial_width
        self.max_height = self.initial_height
        self.frame_count = 0
        self.is_recording = False
        self.is_paused = False
        self.start_time = 0.0
        self.paused_duration = 0.0
        self._pause_start = 0.0

    @staticmethod
    def _bucket(value: int) -> int:
        value = max(16, int(value))
        step = DynamicGifRecorder.SEGMENT_STEP
        return max(step, ((value + step - 1) // step) * step)

    def start(self):
        shutil.rmtree(self.segment_dir, ignore_errors=True)
        self.segment_dir.mkdir(parents=True, exist_ok=True)
        self.segments.clear()
        self._segment_recorder = None
        self._segment_width, self._segment_height = 0, 0
        self.max_width, self.max_height = self.initial_width, self.initial_height
        self.frame_count = 0
        self.is_recording = True
        self.is_paused = False
        self.start_time = time.time()
        self.paused_duration = 0.0

    def pause(self):
        if self.is_recording and not self.is_paused:
            self.is_paused = True
            self._pause_start = time.time()
            if self._segment_recorder:
                self._segment_recorder.pause()

    def resume(self):
        if self.is_recording and self.is_paused:
            self.is_paused = False
            self.paused_duration += time.time() - self._pause_start
            if self._segment_recorder:
                self._segment_recorder.resume()

    def _close_segment(self):
        if self._segment_recorder is None:
            return
        self._segment_recorder.stop()
        self._segment_recorder = None

    def _open_segment(self, width: int, height: int):
        self._close_segment()
        self._segment_width, self._segment_height = _even_size(width, height)
        self.max_width = max(self.max_width, self._segment_width)
        self.max_height = max(self.max_height, self._segment_height)
        segment_path = self.segment_dir / f"segment-{len(self.segments):04d}.mp4"
        recorder = VideoRecorder(
            str(segment_path),
            fps=self.fps,
            codec="libx264",
            width=self._segment_width,
            height=self._segment_height,
        )
        recorder.start()
        self._segment_recorder = recorder
        self.segments.append((str(segment_path), self._segment_width, self._segment_height))

    def add_frame(self, frame_bgr: np.ndarray):
        if not self.is_recording or self.is_paused or frame_bgr is None or frame_bgr.size == 0:
            return

        height, width = frame_bgr.shape[:2]
        if self._segment_recorder is None:
            # Первый поток должен совпадать с начальной рамкой. Квантование
            # применяется только к последующим расширениям, чтобы не
            # увеличивать маленькую область без действия пользователя.
            self._open_segment(self.initial_width, self.initial_height)
        else:
            requested_width = self._bucket(width) if width > self._segment_width else self._segment_width
            requested_height = self._bucket(height) if height > self._segment_height else self._segment_height
            if requested_width > self._segment_width or requested_height > self._segment_height:
                self._open_segment(requested_width, requested_height)

        self._segment_recorder.write_frame(frame_bgr)
        self.frame_count += 1

    def _normalize_segments(self):
        if not self.segments:
            return

        # Даже при одном сегменте переносим его в ожидаемый путь. При
        # нескольких сегментах VideoRecorder масштабирует их к общей ширине,
        # сохраняя расширенную область без чёрных полос.
        if len(self.segments) == 1 and (
            self.segments[0][1] == self.max_width and self.segments[0][2] == self.max_height
        ):
            os.replace(self.segments[0][0], self.temp_video_path)
            return

        normalized = VideoRecorder(
            self.temp_video_path,
            fps=self.fps,
            codec="libx264",
            width=self.max_width,
            height=self.max_height,
        )
        normalized.start()
        try:
            for segment_path, _, _ in self.segments:
                capture = cv2.VideoCapture(segment_path)
                try:
                    while True:
                        ok, frame = capture.read()
                        if not ok:
                            break
                        normalized.write_frame(frame)
                finally:
                    capture.release()
            normalized.stop()
        except Exception:
            try:
                normalized.stop()
            except Exception:
                pass
            raise

    def stop(self):
        self.is_recording = False
        self.is_paused = False
        self._close_segment()
        try:
            if self.frame_count > 0:
                self._normalize_segments()
        finally:
            shutil.rmtree(self.segment_dir, ignore_errors=True)

    def get_elapsed_seconds(self) -> float:
        if not self.is_recording:
            return 0.0
        current = self._pause_start if self.is_paused else time.time()
        return max(0.0, current - self.start_time - self.paused_duration)

class GifRecorder:
    def __init__(self, output_path: str, fps: int = 15, width: int = 800, height: int = 600, max_colors: int = 256):
        self.output_path = output_path
        self.fps = max(5, min(fps, 30))
        self.target_width = width
        self.target_height = height
        self.max_width = width
        self.max_height = height
        
        self.frames = []  # list of PIL Image in 'P' mode
        self.is_recording = False
        self.is_paused = False
        self.start_time = 0
        self.paused_duration = 0
        self._pause_start = 0

    def start(self):
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        self.frames.clear()
        self.max_width = self.target_width
        self.max_height = self.target_height
        self.is_recording = True
        self.is_paused = False
        self.start_time = time.time()
        self.paused_duration = 0

    def pause(self):
        if self.is_recording and not self.is_paused:
            self.is_paused = True
            self._pause_start = time.time()

    def resume(self):
        if self.is_recording and self.is_paused:
            self.is_paused = False
            self.paused_duration += time.time() - self._pause_start

    def add_frame(self, frame_bgr: np.ndarray):
        if not self.is_recording or self.is_paused:
            return

        h, w = frame_bgr.shape[:2]
        self.max_width = max(self.max_width, int(w))
        self.max_height = max(self.max_height, int(h))
        # Размер GIF определяется максимальной реально записанной рамкой.
        # Не уменьшаем кадр сразу: иначе расширение рамки теряется ещё до
        # сохранения файла.
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)

        # Быстрое 8-битное квантование в палитру без блокирующей перегенерации
        img = Image.fromarray(rgb)
        p_frame = img.convert("P", dither=Image.Dither.NONE)
        self.frames.append(p_frame)

    def stop(self):
        self.is_recording = False
        self.is_paused = False
        if not self.frames:
            print("[GifRecorder] Нет кадров для сохранения GIF.")
            return

        duration_ms = max(20, int(1000 / self.fps))
        print(f"[GifRecorder] Сохранение {len(self.frames)} кадров в GIF: {self.output_path}...")
        try:
            normalized_frames = []
            for frame in self.frames:
                if frame.size != (self.max_width, self.max_height):
                    frame = frame.convert("RGB").resize(
                        (self.max_width, self.max_height),
                        Image.Resampling.BILINEAR,
                    ).convert("P", dither=Image.Dither.NONE)
                normalized_frames.append(frame)

            # Быстрое сохранение без блокирующего optimize=True, занимающего минуты
            normalized_frames[0].save(
                self.output_path,
                save_all=True,
                append_images=normalized_frames[1:],
                duration=duration_ms,
                loop=0
            )
            print(f"[GifRecorder] GIF успешно сохранён: {self.output_path}")
        except Exception as e:
            print(f"[GifRecorder] Ошибка сохранения GIF: {e}")

    def get_elapsed_seconds(self) -> float:
        if not self.is_recording:
            return 0.0
        current = self._pause_start if self.is_paused else time.time()
        return max(0.0, current - self.start_time - self.paused_duration)
