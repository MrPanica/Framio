# -*- coding: utf-8 -*-
"""
Высокоскоростная запись и сохранение GIF-анимаций (без лагов и зависаний).
"""

import time
import cv2
import numpy as np
from PIL import Image
from pathlib import Path

class GifRecorder:
    def __init__(self, output_path: str, fps: int = 15, width: int = 800, height: int = 600, max_colors: int = 256):
        self.output_path = output_path
        self.fps = max(5, min(fps, 30))
        self.target_width = width
        self.target_height = height
        
        self.frames = []  # list of PIL Image in 'P' mode
        self.is_recording = False
        self.is_paused = False
        self.start_time = 0
        self.paused_duration = 0
        self._pause_start = 0

    def start(self):
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        self.frames.clear()
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
        if w != self.target_width or h != self.target_height:
            interp = cv2.INTER_AREA if (w > self.target_width or h > self.target_height) else cv2.INTER_LINEAR
            resized = cv2.resize(frame_bgr, (self.target_width, self.target_height), interpolation=interp)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        else:
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
            # Быстрое сохранение без блокирующего optimize=True, занимающего минуты
            self.frames[0].save(
                self.output_path,
                save_all=True,
                append_images=self.frames[1:],
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
