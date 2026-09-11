# -*- coding: utf-8 -*-
"""
Поток захвата экрана с поддержкой чистого звука из игр и микрофона (WASAPI Loopback),
кристальной чёткости 1:1 физического разрешения экрана и настоящей 256-цветовой палитры GIF.
"""

import os
import time
import uuid
import tempfile
import subprocess
import cv2
import numpy as np
from pathlib import Path
from PyQt6.QtCore import QThread, pyqtSignal, QPointF
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QImage, QPainter, QPixmap

from .video_recorder import VideoRecorder
from .audio_recorder import AudioRecorder
from .gif_recorder import GifRecorder, DynamicGifRecorder
from utils.image_filters import apply_filter, FilterType
from utils.capture_mask import apply_mask_to_bgr
from utils.screen_lock import (
    safe_grab_screen_bgr, safe_grab_screen_pixmap, qimage_to_cv2_bgr,
    capture_window_or_screen_bgr
)

try:
    import imageio_ffmpeg
    FFMPEG_EXE = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:
    FFMPEG_EXE = None


class CaptureWorker(QThread):
    tick = pyqtSignal(float, int)  # (elapsed_sec, frame_count)
    recording_finished = pyqtSignal(str)  # output_path
    save_progress = pyqtSignal(str, int, str)  # (filename, percent_0_to_100, stage_desc)
    error_occurred = pyqtSignal(str)

    def __init__(self, mode: str, output_path: str, region_getter, layer_manager=None, canvas=None, fps: int = 30, codec: str = "mp4v",
                 record_mic: bool = True, record_system: bool = True,
                 compress_gif: bool = True, compress_video: bool = True,
                 gif_colors: int = 64, gif_dither: str = "none",
                 target_hwnd: int | None = None, capture_mask=None, mask_getter=None,
                 filter_type: str = FilterType.NONE, filter_params: dict | None = None,
                 parent=None):
        super().__init__(parent)
        self.mode = mode  # "video" or "gif"
        self.output_path = output_path
        self.region_getter = region_getter
        self.layer_manager = layer_manager
        self.canvas = canvas
        self.fps = max(5, min(60, fps))
        self.codec = codec
        self.record_mic = record_mic
        self.record_system = record_system
        self.compress_gif = compress_gif
        self.compress_video = compress_video
        self.gif_colors = max(16, min(256, gif_colors))
        self.gif_dither = gif_dither
        self.target_hwnd = target_hwnd
        self.capture_mask = capture_mask
        self.mask_getter = mask_getter
        self.filter_type = filter_type or FilterType.NONE
        self.filter_params = dict(filter_params or {})

        self.running = False
        self.is_cancelled = False
        self.paused = False
        self.total_paused_duration = 0.0
        self._pause_start = 0.0
        self.video_recorder = None
        self.audio_recorder = None
        self.temp_video_path = ""
        self.temp_audio_path = ""
        self._ffmpeg_process = None

    def set_target_hwnd(self, hwnd: int | None):
        self.target_hwnd = hwnd

    def set_filter(self, filter_type: str, filter_params: dict | None = None):
        self.filter_type = filter_type or FilterType.NONE
        if filter_params is not None:
            self.filter_params = dict(filter_params)

    def set_mic_muted(self, muted: bool):
        self.record_mic = not muted
        if self.audio_recorder:
            self.audio_recorder.set_mic_muted(muted)

    def set_system_muted(self, muted: bool):
        self.record_system = not muted
        if self.audio_recorder:
            self.audio_recorder.set_system_muted(muted)

    def pause(self):
        if not self.paused:
            self.paused = True
            self._pause_start = time.perf_counter()
            if self.video_recorder:
                self.video_recorder.pause()
            if self.audio_recorder:
                self.audio_recorder.pause()

    def resume(self):
        if self.paused:
            self.paused = False
            self.total_paused_duration += time.perf_counter() - self._pause_start
            if self.video_recorder:
                self.video_recorder.resume()
            if self.audio_recorder:
                self.audio_recorder.resume()

    def cancel(self):
        self.is_cancelled = True
        self.running = False
        self._terminate_ffmpeg_process()

    def stop(self):
        self.running = False

    def _terminate_ffmpeg_process(self):
        """Останавливает дочерний FFmpeg при отмене записи или выходе приложения."""
        proc = self._ffmpeg_process
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            proc.wait(timeout=2.0)
        except Exception:
            try:
                proc.kill()
                proc.wait(timeout=2.0)
            except Exception:
                pass

    def _cleanup_cancelled_files(self):
        """Удаляет временные и недописанные файлы после отмены записи."""
        paths = [
            self.temp_video_path,
            self.temp_audio_path,
            self.output_path,
            f"{self.output_path}.comp.mp4" if self.output_path else "",
            f"{self.output_path}.comp.gif" if self.output_path else "",
        ]
        for path in paths:
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    def _apply_censor_shapes(self, frame_bgr, all_shapes, offset: QPointF):
        """Применяет эффекты цензуры (мозаика, блюр, ч/б, инверсия, сепия, насыщенность) к текущему видеокадру."""
        if frame_bgr is None or not all_shapes:
            return frame_bgr

        fh, fw = frame_bgr.shape[:2]
        for shape in all_shapes:
            if shape is None or not getattr(shape, "visible", True):
                continue

            shape_name = shape.__class__.__name__
            is_mosaic = getattr(shape, "is_mosaic", False) or shape_name == "MosaicShape"
            is_blur = getattr(shape, "is_blur", False) or shape_name == "BlurShape"
            is_regional = shape_name == "RegionalEffectShape" or hasattr(shape, "effect_type")
            if not (is_mosaic or is_blur or is_regional):
                continue

            pixel_size = max(1, int(getattr(shape, "pixel_size", 8) or getattr(shape, "intensity", 8) or 8))
            blur_radius = max(1, int(getattr(shape, "blur_radius", 15) or getattr(shape, "intensity", 15) or 15))
            effect_type = getattr(shape, "effect_type", "")
            if not effect_type:
                if is_mosaic:
                    effect_type = "pixelate"
                elif is_blur:
                    effect_type = "blur"
                else:
                    effect_type = "pixelate"
            elif effect_type == "mosaic":
                effect_type = "pixelate"

            if shape_name in ("MosaicShape", "BlurShape", "RectangleShape", "CircleShape", "RegionalEffectShape"):
                rect = shape.rect.translated(-offset.x(), -offset.y()).normalized()
                sx = max(0, min(fw - 1, int(rect.x())))
                sy = max(0, min(fh - 1, int(rect.y())))
                sw = max(1, min(fw - sx, int(rect.width())))
                sh = max(1, min(fh - sy, int(rect.height())))
                if sw < 4 or sh < 4:
                    continue

                roi = frame_bgr[sy:sy + sh, sx:sx + sw]
                if effect_type != "normal":
                    processed = apply_filter(roi.copy(), effect_type, blur_radius=blur_radius, pixel_size=pixel_size)
                else:
                    processed = roi

                if shape_name == "CircleShape":
                    mask = np.zeros((sh, sw), dtype=np.uint8)
                    cv2.ellipse(mask, (sw // 2, sh // 2), (sw // 2, sh // 2), 0, 0, 360, 255, -1)
                    roi[mask > 0] = processed[mask > 0]
                    frame_bgr[sy:sy + sh, sx:sx + sw] = roi
                else:
                    frame_bgr[sy:sy + sh, sx:sx + sw] = processed

            elif shape_name == "PenShape" and getattr(shape, "points", None) and len(shape.points) >= 2:
                mask = np.zeros((fh, fw), dtype=np.uint8)
                points = np.array(
                    [[int(point.x() - offset.x()), int(point.y() - offset.y())] for point in shape.points],
                    dtype=np.int32,
                )
                pen_width = max(16, int(shape.stroke_width * 2))
                cv2.polylines(mask, [points], isClosed=False, color=255, thickness=pen_width, lineType=cv2.LINE_AA)
                if np.any(mask):
                    filtered = apply_filter(frame_bgr.copy(), effect_type, blur_radius=blur_radius, pixel_size=pixel_size)
                    frame_bgr[mask > 0] = filtered[mask > 0]

        return frame_bgr

    def run(self):
        self.running = True
        self.total_paused_duration = 0.0
        self._pause_start = 0.0

        try:
            init_x, init_y, init_w, init_h = self.region_getter()

            # Захватываем первый физический кадр для определения точных физических размеров видео 1:1
            first_frame = capture_window_or_screen_bgr(init_x, init_y, init_w, init_h, target_hwnd=self.target_hwnd)
            if first_frame is not None and first_frame.size > 0:
                actual_h, actual_w = first_frame.shape[:2]
            else:
                actual_w = max(32, int(init_w))
                actual_h = max(32, int(init_h))

            actual_w = actual_w if actual_w % 2 == 0 else actual_w + 1
            actual_h = actual_h if actual_h % 2 == 0 else actual_h + 1

            parent_dir = Path(self.output_path).parent
            parent_dir.mkdir(parents=True, exist_ok=True)

            temp_dir = Path(tempfile.gettempdir()) / "framio"
            temp_dir.mkdir(parents=True, exist_ok=True)

            # Пути для промежуточных файлов
            uid = f"{time.time_ns()}_{os.getpid()}_{uuid.uuid4().hex[:6]}"
            if self.mode == "video":
                self.temp_video_path = str(temp_dir / f"temp_vid_{uid}.mp4")
                self.temp_audio_path = str(temp_dir / f"temp_aud_{uid}.wav")
                self.video_recorder = VideoRecorder(self.temp_video_path, fps=self.fps, codec=self.codec, width=actual_w, height=actual_h)
                self.audio_recorder = AudioRecorder(self.temp_audio_path, record_mic=self.record_mic, record_system=self.record_system)
                self.audio_recorder.start()
                self.video_recorder.start()

            elif self.mode == "gif":
                # Для GIF используем промежуточный видеопоток без потерь для последующего PaletteGen
                if FFMPEG_EXE:
                    self.temp_video_path = str(temp_dir / f"temp_gif_{uid}.mp4")
                    # Обычный VideoRecorder фиксирует размер первого кадра.
                    # DynamicGifRecorder переключает сегмент при расширении
                    # рамки и затем собирает их в общий поток максимального
                    # размера, поэтому боковой resize реально попадает в GIF.
                    self.video_recorder = DynamicGifRecorder(
                        self.temp_video_path,
                        fps=self.fps,
                        width=actual_w,
                        height=actual_h,
                    )
                    self.video_recorder.start()
                else:
                    self.video_recorder = GifRecorder(self.output_path, fps=self.fps, width=actual_w, height=actual_h)
                    self.video_recorder.start()

            frame_interval = 1.0 / self.fps
            last_tick_emit = 0.0
            rec_start_time = None
            frame_count_written = 0

            while self.running:
                if self.paused:
                    time.sleep(0.05)
                    continue

                try:
                    rx, ry, rw, rh = self.region_getter()
                    rx, ry = int(rx), int(ry)
                    rw = max(16, int(rw))
                    rh = max(16, int(rh))

                    # Захватываем живой рабочий стол 1:1 или выбранное окно
                    frame_bgr = capture_window_or_screen_bgr(rx, ry, rw, rh, target_hwnd=self.target_hwnd)
                    if frame_bgr is not None and frame_bgr.size > 0:
                        # MP4-поток фиксированного размера масштабирует кадр
                        # обратно к стартовой геометрии. GIF использует
                        # DynamicGifRecorder и сохраняет фактический размер
                        # каждого кадра до финального объединения сегментов.
                        if not isinstance(self.video_recorder, DynamicGifRecorder) and (
                            frame_bgr.shape[0] != actual_h or frame_bgr.shape[1] != actual_w
                        ):
                            interp = cv2.INTER_AREA if (frame_bgr.shape[1] > actual_w or frame_bgr.shape[0] > actual_h) else cv2.INTER_LINEAR
                            frame_bgr = cv2.resize(frame_bgr, (actual_w, actual_h), interpolation=interp)

                        # Маска живёт в координатах самой зоны, поэтому при
                        # изменении размеров окна она пересчитывается на каждый кадр.
                        active_mask = self.mask_getter() if callable(self.mask_getter) else self.capture_mask
                        if active_mask is not None:
                            frame_bgr = apply_mask_to_bgr(
                                frame_bgr,
                                active_mask,
                                (0, 0, max(1, rw), max(1, rh)),
                            )

                        # Изолированная отрисовка векторных слоёв и цензуры (мозаика и блюр)
                        try:
                            lock = getattr(self.canvas, "shape_lock", None)
                            if lock is not None:
                                with lock:
                                    raw_shapes = list(self.layer_manager.shapes) if (self.layer_manager and getattr(self.layer_manager, "shapes", None)) else []
                                    raw_temp = getattr(self.canvas, "temp_shape", None)
                                    all_shapes = [s.clone() for s in raw_shapes if s is not None] + ([raw_temp.clone()] if raw_temp is not None else [])
                            else:
                                raw_shapes = list(self.layer_manager.shapes) if (self.layer_manager and getattr(self.layer_manager, "shapes", None)) else []
                                raw_temp = getattr(self.canvas, "temp_shape", None)
                                all_shapes = [s.clone() for s in raw_shapes if s is not None] + ([raw_temp.clone()] if raw_temp is not None else [])

                            if all_shapes:
                                is_pinned = getattr(self.layer_manager, "is_pinned", False) if self.layer_manager else False
                                offset = QPointF(float(rx), float(ry)) if is_pinned else QPointF(0.0, 0.0)
                                fh, fw = frame_bgr.shape[:2]

                                # 1. Наложение эффектов цензуры (мозаика и блюр) прямо на BGR-кадр
                                frame_bgr = self._apply_censor_shapes(frame_bgr, all_shapes, offset)

                                # 2. Отрисовка векторных фигур (стрелки, карандаш, текст, рамки)
                                normal_vector_shapes = [
                                    s for s in all_shapes
                                    if s is not None and getattr(s, "visible", True)
                                    and not getattr(s, "is_mosaic", False)
                                    and not getattr(s, "is_blur", False)
                                    and s.__class__.__name__ not in ("MosaicShape", "BlurShape", "RegionalEffectShape")
                                    and not hasattr(s, "effect_type")
                                ]
                                if normal_vector_shapes:
                                    h, w = frame_bgr.shape[:2]
                                    bgra = np.ascontiguousarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2BGRA))
                                    qimg = QImage(bgra.data, w, h, w * 4, QImage.Format.Format_ARGB32)
                                    painter = QPainter(qimg)
                                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                                    for s in normal_vector_shapes:
                                        s.draw(painter, offset=offset)
                                    painter.end()
                                    ptr = qimg.constBits()
                                    ptr.setsize(h * w * 4)
                                    arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 4))
                                    frame_bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
                        except Exception as draw_err:
                            print(f"[CaptureWorker] Предупреждение при наложении фигур: {draw_err}")

                        if self.filter_type != FilterType.NONE:
                            frame_bgr = apply_filter(
                                frame_bgr,
                                self.filter_type,
                                **self.filter_params,
                            )

                        # Синхронизация реального времени (Wall-clock CFR):
                        # Считаем точное количество кадров, которое должно быть в видео к этому моменту
                        now = time.perf_counter()
                        if rec_start_time is None:
                            rec_start_time = now
                            expected_frames = 1
                            deficit = 0
                        else:
                            elapsed = now - rec_start_time - self.total_paused_duration
                            expected_frames = max(1, int(round(elapsed * self.fps)))
                            deficit = expected_frames - frame_count_written

                        # Если захват кадра занял больше времени, повторяем кадр нужное число раз,
                        # чтобы скорость воспроизведения видео строго равнялась 1.0x (без ускорения)
                        # и звук оставался идеально синхронизирован
                        repeat_count = max(1, min(deficit, self.fps * 2)) if deficit > 0 else 1
                        for _ in range(repeat_count):
                            if isinstance(self.video_recorder, VideoRecorder):
                                self.video_recorder.write_frame(frame_bgr)
                            else:
                                self.video_recorder.add_frame(frame_bgr)
                            frame_count_written += 1

                        if now - last_tick_emit >= 0.2:
                            last_tick_emit = now
                            elapsed_sec = self.video_recorder.get_elapsed_seconds()
                            self.tick.emit(elapsed_sec, frame_count_written)
                except Exception as frame_err:
                    print(f"[CaptureWorker] Ошибка кадра: {frame_err}")

                # Точный расчет сна до времени следующего кадра по реальным часам
                if rec_start_time is not None:
                    next_frame_time = rec_start_time + self.total_paused_duration + frame_count_written * frame_interval
                    sleep_time = next_frame_time - time.perf_counter()
                    if sleep_time > 0.001:
                        time.sleep(sleep_time)
                else:
                    time.sleep(0.005)

        except Exception as e:
            print(f"[CaptureWorker] Ошибка в цикле записи: {e}")
            self.error_occurred.emit(str(e))
        finally:
            self._finalize_recording()

    def _emit_progress(self, percent: int, stage_desc: str):
        try:
            fname = Path(self.output_path).name if self.output_path else "Запись"
            self.save_progress.emit(fname, min(100, max(0, int(percent))), stage_desc)
        except Exception:
            pass

    def _run_ffmpeg_with_progress(self, cmd: list[str], total_frames: int, base_pct: int, max_pct: int, stage_name: str) -> int:
        creation_flags = 0x08000000 if os.name == "nt" else 0
        full_cmd = list(cmd) + ["-progress", "pipe:1", "-stats_period", "0.3"]
        self._emit_progress(base_pct, stage_name)
        proc = None
        try:
            proc = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=creation_flags
            )
            self._ffmpeg_process = proc
            pct_span = max_pct - base_pct
            for line in proc.stdout:
                if self.is_cancelled:
                    self._terminate_ffmpeg_process()
                    break
                line = line.strip()
                if line.startswith("frame="):
                    try:
                        cur_f = int(line.split("=")[1])
                        if total_frames > 0:
                            ratio = min(1.0, max(0.0, cur_f / float(total_frames)))
                            cur_pct = int(base_pct + ratio * pct_span)
                            self._emit_progress(cur_pct, f"{stage_name}: {cur_pct}%")
                    except Exception:
                        pass
                elif line == "progress=end":
                    self._emit_progress(max_pct, f"{stage_name}: {max_pct}%")
            proc.wait()
            return -2 if self.is_cancelled else proc.returncode
        except Exception as e:
            print(f"[CaptureWorker] Ошибка FFmpeg с прогрессом: {e}")
            return -1
        finally:
            if self._ffmpeg_process is proc:
                self._ffmpeg_process = None

    def _finalize_recording(self):
        if getattr(self, "is_cancelled", False):
            if self.video_recorder:
                try:
                    self.video_recorder.stop()
                except Exception:
                    pass
            if self.audio_recorder:
                try:
                    self.audio_recorder.stop()
                except Exception:
                    pass
            self._terminate_ffmpeg_process()
            self._cleanup_cancelled_files()
            return

        # 1. Останавливаем видео и аудио
        if self.video_recorder:
            self.video_recorder.stop()

        total_frames = getattr(self.video_recorder, "frame_count", 0)
        if total_frames <= 0:
            total_frames = len(getattr(self.video_recorder, "frames", []))
        if total_frames <= 0:
            total_frames = 30  # fallback

        video_duration = (total_frames / float(self.fps)) if self.fps > 0 else 0.0

        if self.audio_recorder:
            self.audio_recorder.stop(target_duration=video_duration)

        if self.is_cancelled:
            self._terminate_ffmpeg_process()
            self._cleanup_cancelled_files()
            return

        self._emit_progress(5, "Финализация записи...")

        # 2. Если режим ВИДЕО со звуком — объединяем видео + аудио через FFmpeg
        if self.mode == "video" and (self.record_mic or self.record_system) and FFMPEG_EXE:
            if os.path.exists(self.temp_video_path) and os.path.exists(self.temp_audio_path) and os.path.getsize(self.temp_audio_path) > 100:
                print(f"[CaptureWorker] Объединение видео и звука в: {self.output_path}...")
                cmd = [
                    FFMPEG_EXE, "-y",
                    "-i", self.temp_video_path,
                    "-i", self.temp_audio_path,
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "192k",
                    self.output_path
                ]
                mux_max = 30 if self.compress_video else 95
                ret = self._run_ffmpeg_with_progress(cmd, total_frames, 5, mux_max, "Сведение звука")
                if self.is_cancelled:
                    self._cleanup_cancelled_files()
                    return
                if ret == 0 and os.path.exists(self.output_path):
                    print("[CaptureWorker] Видео со звуком успешно собрано!")
                else:
                    print(f"[CaptureWorker] FFmpeg вернул код {ret}, перенос видеопотока...")
                    os.replace(self.temp_video_path, self.output_path)

                # Чистим временные файлы
                for p in [self.temp_video_path, self.temp_audio_path]:
                    if os.path.exists(p):
                        try: os.remove(p)
                        except Exception: pass
            elif os.path.exists(self.temp_video_path):
                os.replace(self.temp_video_path, self.output_path)
                if os.path.exists(self.temp_audio_path):
                    try: os.remove(self.temp_audio_path)
                    except Exception: pass

        # 3. Если режим GIF — генерируем оптимизированный GIF через FFmpeg PaletteGen
        elif self.mode == "gif" and FFMPEG_EXE and self.temp_video_path and os.path.exists(self.temp_video_path):
            dither_str = "bayer:bayer_scale=3" if self.gif_dither == "bayer" else "none"
            print(f"[CaptureWorker] Создание сжатого GIF ({self.gif_colors} цветов, дизеринг {dither_str}): {self.output_path}...")
            cmd = [
                FFMPEG_EXE, "-y",
                "-i", self.temp_video_path,
                "-vf", f"fps={self.fps},split[s0][s1];[s0]palettegen=max_colors={self.gif_colors}:reserve_transparent=0[p];[s1][p]paletteuse=dither={dither_str}",
                self.output_path
            ]
            gif_max = 60 if self.compress_gif else 95
            ret = self._run_ffmpeg_with_progress(cmd, total_frames, 10, gif_max, f"Генерация GIF ({self.gif_colors} цветов)")
            if self.is_cancelled:
                self._cleanup_cancelled_files()
                return
            if ret == 0:
                print(f"[CaptureWorker] Оптимизированный GIF ({self.gif_colors} цветов) успешно сохранён!")
            else:
                print(f"[CaptureWorker] Ошибка PaletteGen (код {ret})")

            if os.path.exists(self.temp_video_path):
                try: os.remove(self.temp_video_path)
                except Exception: pass

            # Дополнительное сжатие GIF алгоритмами оптимизации кадров
            if self.compress_gif and os.path.exists(self.output_path):
                try:
                    from PIL import Image, ImageSequence
                    print(f"[CaptureWorker] Запуск сжатия GIF (Pillow Optimize)...")
                    self._emit_progress(65, "Оптимизация кадров GIF...")
                    with Image.open(self.output_path) as im:
                        frames = [f.copy() for f in ImageSequence.Iterator(im)]
                        if frames:
                            temp_comp = self.output_path + ".comp.gif"
                            self._emit_progress(80, "Сжатие LZW...")
                            frames[0].save(
                                temp_comp,
                                save_all=True,
                                append_images=frames[1:],
                                optimize=True,
                                loop=0,
                                duration=int(1000 / self.fps)
                            )
                            if os.path.exists(temp_comp):
                                if os.path.getsize(temp_comp) < os.path.getsize(self.output_path):
                                    os.replace(temp_comp, self.output_path)
                                    print(f"[CaptureWorker] GIF успешно оптимизирован и сжат!")
                                else:
                                    os.remove(temp_comp)
                except Exception as comp_err:
                    print(f"[CaptureWorker] Ошибка сжатия GIF: {comp_err}")

        # 4. Пост-сжатие видео MP4 алгоритмом H.264 при запросе
        if self.mode == "video" and self.compress_video and FFMPEG_EXE and os.path.exists(self.output_path):
            try:
                comp_video = self.output_path + ".comp.mp4"
                cmd_v = [
                    FFMPEG_EXE, "-y",
                    "-i", self.output_path,
                    "-c:v", "libx264",
                    "-crf", "22",
                    "-preset", "faster",
                    "-c:a", "copy",
                    "-movflags", "+faststart",
                    comp_video
                ]
                ret_v = self._run_ffmpeg_with_progress(cmd_v, total_frames, 30, 95, "Сжатие H.264")
                if self.is_cancelled:
                    self._cleanup_cancelled_files()
                    return
                if ret_v == 0 and os.path.exists(comp_video):
                    if os.path.getsize(comp_video) < os.path.getsize(self.output_path):
                        os.replace(comp_video, self.output_path)
                        print(f"[CaptureWorker] Видео MP4 оптимизировано!")
                    else:
                        os.remove(comp_video)
            except Exception as e_v:
                print(f"[CaptureWorker] Ошибка оптимизации видео: {e_v}")

        if self.is_cancelled:
            self._cleanup_cancelled_files()
            return

        self._emit_progress(100, "Готово")
        self.recording_finished.emit(self.output_path)
