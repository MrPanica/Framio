# -*- coding: utf-8 -*-
"""
Запись видео MP4 с поддержкой прямого вывода через FFmpeg (H.264 libx264, CRF 17)
для достижения кристальной четкости 1:1 без размытия, задержек и артефактов mp4v.
В случае отсутствия FFmpeg используется встроенный OpenCV VideoWriter.
"""

import os
import time
import subprocess
import cv2
import numpy as np
from pathlib import Path

try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = None


class VideoRecorder:
    def __init__(self, output_path: str, fps: int = 30, codec: str = "mp4v", width: int = 1280, height: int = 720):
        self.output_path = output_path
        self.fps = fps
        self.codec = codec
        self.target_width = width if width % 2 == 0 else width + 1
        self.target_height = height if height % 2 == 0 else height + 1
        self.target_width = max(16, self.target_width)
        self.target_height = max(16, self.target_height)

        self.writer = None
        self._proc = None
        self.is_recording = False
        self.is_paused = False
        self.frame_count = 0
        self.start_time = 0
        self.paused_duration = 0
        self._pause_start = 0

    def start(self):
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        self.is_recording = True
        self.is_paused = False
        self.frame_count = 0
        self.start_time = time.time()
        self.paused_duration = 0

        # 1. Приоритет: аппаратный/быстрый pipe через FFmpeg libx264
        if FFMPEG_EXE:
            try:
                cmd = [
                    FFMPEG_EXE, "-y",
                    "-f", "rawvideo",
                    "-vcodec", "rawvideo",
                    "-s", f"{self.target_width}x{self.target_height}",
                    "-pix_fmt", "bgr24",
                    "-r", str(self.fps),
                    "-i", "-",
                    "-c:v", "libx264",
                    "-crf", "17",
                    "-preset", "ultrafast",
                    "-tune", "zerolatency",
                    "-g", str(max(15, self.fps * 2)),
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    self.output_path
                ]
                creation_flags = 0x08000000 if os.name == "nt" else 0
                self._proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    bufsize=10485760,
                    creationflags=creation_flags
                )
                print(f"[VideoRecorder] Запущен FFmpeg libx264 pipe ({self.target_width}x{self.target_height} @ {self.fps} FPS, CRF 17)")
                return
            except Exception as e:
                print(f"[VideoRecorder] Ошибка запуска FFmpeg pipe: {e}, переключение на OpenCV...")
                self._proc = None

        # 2. Резерв: OpenCV VideoWriter
        fourcc_name = "mp4v" if self.codec in ("mp4v", "libx264") else self.codec
        fourcc = cv2.VideoWriter_fourcc(*fourcc_name)
        self.writer = cv2.VideoWriter(self.output_path, fourcc, float(self.fps), (self.target_width, self.target_height))

        if not self.writer.isOpened():
            print(f"[VideoRecorder] Кодек {fourcc_name} не открылся, пробуем mp4v...")
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self.writer = cv2.VideoWriter(self.output_path, fourcc, float(self.fps), (self.target_width, self.target_height))

    def pause(self):
        if self.is_recording and not self.is_paused:
            self.is_paused = True
            self._pause_start = time.time()

    def resume(self):
        if self.is_recording and self.is_paused:
            self.is_paused = False
            self.paused_duration += time.time() - self._pause_start

    def write_frame(self, frame_bgr: np.ndarray):
        if not self.is_recording or self.is_paused:
            return

        h, w = frame_bgr.shape[:2]

        if w == self.target_width and h == self.target_height:
            output_frame = frame_bgr
        else:
            # Динамическое масштабирование: при изменении размера рамки записи
            # область плавно масштабируется без появления черных полос и пустых квадратов
            interp = cv2.INTER_AREA if (w > self.target_width or h > self.target_height) else cv2.INTER_LINEAR
            output_frame = cv2.resize(frame_bgr, (self.target_width, self.target_height), interpolation=interp)

        # Запись кадра
        if self._proc and self._proc.stdin:
            try:
                self._proc.stdin.write(output_frame.tobytes())
                self.frame_count += 1
            except Exception as pipe_err:
                print(f"[VideoRecorder] Ошибка записи в FFmpeg pipe: {pipe_err}")
        elif self.writer:
            self.writer.write(output_frame)
            self.frame_count += 1

    def stop(self):
        self.is_recording = False
        self.is_paused = False

        if self._proc:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.wait(timeout=5)
            except Exception as e:
                print(f"[VideoRecorder] Ошибка завершения FFmpeg: {e}")
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

        if self.writer is not None:
            self.writer.release()
            self.writer = None

        print(f"[VideoRecorder] Запись завершена. Кадров: {self.frame_count}, Сохранено: {self.output_path}")

    def get_elapsed_seconds(self) -> float:
        if not self.is_recording:
            return 0.0
        current = self._pause_start if self.is_paused else time.time()
        return max(0.0, current - self.start_time - self.paused_duration)
