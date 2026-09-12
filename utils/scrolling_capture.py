# -*- coding: utf-8 -*-
"""
Модуль длинных скриншотов (Scrolling Screenshot / Long Screenshot) для Framio.
Обеспечивает:
1. Четкую направляющую рамку захвата (ScrollingCaptureGuide), невидимую для скриншотов
   благодаря SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE), с поддержкой сквозных кликов.
2. Живое отслеживание прокрутки колесом мыши в реальном времени (120 мс) с многополосным
   сопоставлением OpenCV matchTemplate для бесшовной склейки с точностью до 1 пикселя.
3. Поддержку ручного пошагового захвата на полную высоту рамки («Сделать кадр»).
4. Опциональную автоматическую плавную прокрутку («Авто-скролл»).
5. Компактный плавающий HUD управления со счетчиком кадров, высоты и подсказками.
"""

from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes
from pathlib import Path
from datetime import datetime
import cv2
import numpy as np

from PyQt6.QtCore import Qt, QObject, pyqtSignal, QTimer, QPoint, QRect, QSize
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFrame, QApplication
)
from PyQt6.QtGui import QImage, QPixmap, QColor, QFont, QPainter, QPen, QBrush, QKeyEvent

from utils.screen_lock import safe_grab_screen_bgr, user32, gdi32, POINT
from utils.i18n import tr
from ui.icons import create_themed_icon

# Константы Win32
MOUSEEVENTF_WHEEL = 0x0800
WM_MOUSEWHEEL = 0x020A
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WDA_EXCLUDEFROMCAPTURE = 0x00000011


class ScrollingCaptureGuide(QWidget):
    """
    Направляющая рамка области длинного скриншота.
    Отображает границы выделения, уголков и подсказку для пользователя.
    Полностью скрыта от снимков экрана через SetWindowDisplayAffinity(0x11)
    и пропускает любые клики и прокрутку колесом мыши к окну под ней через WS_EX_TRANSPARENT.
    """
    def __init__(self, rect: QRect, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setGeometry(rect)

        # Применяем исключение из скриншотов и сквозной ввод мыши
        try:
            hwnd = int(self.winId())
            user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE)
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_TRANSPARENT | WS_EX_LAYERED)
        except Exception as e:
            print(f"[ScrollingCaptureGuide] Ошибка установки стиля: {e}")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()

        # 1. Тонкая акцентная рамка циан/голубая
        pen_border = QPen(QColor("#00bcd4"), 2)
        pen_border.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen_border)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(1, 1, w - 2, h - 2)

        # 2. Угловые акцентные скобы (L-brackets)
        pen_corner = QPen(QColor("#38bdf8"), 3)
        pen_corner.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_corner)
        c_len = 16

        # Верхний левый угол
        painter.drawLine(1, 1, 1 + c_len, 1)
        painter.drawLine(1, 1, 1, 1 + c_len)

        # Верхний правый угол
        painter.drawLine(w - 2, 1, w - 2 - c_len, 1)
        painter.drawLine(w - 2, 1, w - 2, 1 + c_len)

        # Нижний левый угол
        painter.drawLine(1, h - 2, 1 + c_len, h - 2)
        painter.drawLine(1, h - 2, 1, h - 2 - c_len)

        # Нижний правый угол
        painter.drawLine(w - 2, h - 2, w - 2 - c_len, h - 2)
        painter.drawLine(w - 2, h - 2, w - 2, h - 2 - c_len)

        # 3. Информационный бейдж в левом верхнем углу
        badge_text = tr("scroll_guide_badge", "Область длинного скриншота [Крутите колесо мыши]")
        font = QFont("Segoe UI", 9, QFont.Weight.DemiBold)
        painter.setFont(font)
        fm = painter.fontMetrics()
        tw = fm.horizontalAdvance(badge_text) + 16
        th = 22

        badge_rect = QRect(6, 6, tw, th)
        painter.setPen(QPen(QColor("#0284c7"), 1))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(badge_rect, 4, 4)

        painter.setPen(QColor("#38bdf8"))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)


class ScrollingCaptureHUD(QWidget):
    """
    Компактный плавающий HUD управления длинным скриншотом.
    Скрыт от захвата экрана через SetWindowDisplayAffinity.
    Предоставляет кнопки Завершить, Автопрокрутка, Сделать кадр и Отмена.
    """
    done_clicked = pyqtSignal()
    pause_toggled = pyqtSignal()
    step_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        try:
            user32.SetWindowDisplayAffinity(int(self.winId()), WDA_EXCLUDEFROMCAPTURE)
        except Exception:
            pass

        self._init_ui()

    def _init_ui(self):
        self.setStyleSheet("""
            QFrame#hudContainer {
                background-color: #141720;
                border: 1px solid #272d3d;
                border-radius: 8px;
            }
            QLabel {
                color: #f1f5f9;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 12px;
            }
            QPushButton {
                background-color: #1f2533;
                color: #f1f5f9;
                border: 1px solid #2e374c;
                border-radius: 5px;
                padding: 5px 12px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #2b3345;
                border-color: #3b455e;
            }
            QPushButton:pressed {
                background-color: #161a24;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        container = QFrame(self)
        container.setObjectName("hudContainer")
        v_layout = QVBoxLayout(container)
        v_layout.setContentsMargins(12, 8, 12, 8)
        v_layout.setSpacing(6)

        # Верхняя строка: Заголовок, Счетчик и Кнопки
        h_layout = QHBoxLayout()
        h_layout.setSpacing(10)

        # 1. Заголовок
        self.lbl_title = QLabel(tr("scroll_hud_title", "Длинный скриншот"))
        self.lbl_title.setStyleSheet("color: #38bdf8; font-weight: 700; font-size: 12px;")
        h_layout.addWidget(self.lbl_title)

        # 2. Счетчик кадров и высоты
        self.lbl_status = QLabel(tr("scroll_hud_status", frames=1, height=0))
        self.lbl_status.setStyleSheet("""
            background-color: #1a202c;
            color: #f8fafc;
            border: 1px solid #2d3748;
            border-radius: 4px;
            padding: 3px 8px;
            font-family: Consolas, monospace;
            font-size: 11px;
            font-weight: 600;
        """)
        h_layout.addWidget(self.lbl_status)

        # 3. Кнопка Завершить (Enter)
        self.btn_done = QPushButton(tr("scroll_hud_done", "Завершить (Enter)"))
        self.btn_done.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_done.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                border: 1px solid #38bdf8;
                color: #ffffff;
                font-weight: 600;
                padding: 5px 14px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_done.clicked.connect(self.done_clicked.emit)
        h_layout.addWidget(self.btn_done)

        # 4. Кнопка Авто-скролл / Пауза
        self.btn_pause = QPushButton(tr("scroll_hud_autoscroll", "Авто-скролл"))
        self.btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause.setToolTip(tr("scroll_hud_autoscroll_tip", "Включить автоматическую плавную прокрутку страницы"))
        self.btn_pause.clicked.connect(self.pause_toggled.emit)
        h_layout.addWidget(self.btn_pause)

        # 5. Кнопка Сделать кадр (по высоте рамки)
        self.btn_step = QPushButton(tr("scroll_hud_step", "Сделать кадр"))
        self.btn_step.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_step.setToolTip(tr("scroll_step_tip", "Захватить текущий экран и склеить с предыдущими (по высоте рамки)"))
        self.btn_step.clicked.connect(self.step_clicked.emit)
        h_layout.addWidget(self.btn_step)

        # 6. Кнопка Отмена (Esc)
        self.btn_cancel = QPushButton(tr("scroll_hud_cancel", "Отмена (Esc)"))
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)
        h_layout.addWidget(self.btn_cancel)

        v_layout.addLayout(h_layout)

        # Нижняя строка: Подсказка
        self.lbl_hint = QLabel(tr("scroll_hud_hint", "Крутите колесо мыши для плавной прокрутки или нажимайте «Сделать кадр». Нажмите Enter для сохранения."))
        self.lbl_hint.setStyleSheet("color: #8892b0; font-size: 11px;")
        v_layout.addWidget(self.lbl_hint)

        main_layout.addWidget(container)
        self.adjustSize()

    def keyPressEvent(self, event: QKeyEvent):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.done_clicked.emit()
        elif event.key() == Qt.Key.Key_Space:
            self.step_clicked.emit()
        elif event.key() == Qt.Key.Key_Escape:
            self.cancel_clicked.emit()
        else:
            super().keyPressEvent(event)

    def update_progress(self, frames: int, height_px: int):
        self.lbl_status.setText(tr("scroll_hud_status", frames=frames, height=height_px))
        self.adjustSize()

    def set_paused_state(self, is_paused: bool):
        if is_paused:
            self.btn_pause.setText(tr("scroll_hud_resume", "Продолжить"))
            self.btn_pause.setStyleSheet("")
        else:
            self.btn_pause.setText(tr("scroll_hud_pause", "Пауза"))
            self.btn_pause.setStyleSheet("background-color: #b45309; border-color: #f59e0b; color: #ffffff;")


class ScrollingCaptureEngine(QObject):
    """
    Движок вертикального захвата и бесшовной склейки прокручиваемой страницы.
    Поддерживает:
    - Интерактивную прокрутку страницы колесом мыши пользователем с авто-детектированием сдвига (120 мс)
    - Ручной захват кадров («Сделать кадр») по высоте рамки без видимых стыков
    - Опциональную автоматическую прокрутку
    """
    progress = pyqtSignal(int, int)  # (frames_captured, total_height_px)
    finished = pyqtSignal(object)  # final stacked BGR numpy array
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, region: tuple[int, int, int, int], target_hwnd: int | None = None, parent=None):
        super().__init__(parent)
        self.region = region  # (rx, ry, rw, rh)
        self.target_hwnd = target_hwnd
        self.strips: list[np.ndarray] = []
        self.total_height = 0
        self.last_frame: np.ndarray | None = None
        self.frames_captured = 0
        self.is_running = False
        self.is_paused = True  # Авто-скролл отключен по умолчанию (пользователь крутит сам)
        self.is_autoscrolling = False
        self._is_stitching = False
        self._consecutive_fails = 0

        self.max_height = 40000  # Защита от бесконечного скролла
        self.guide: ScrollingCaptureGuide | None = None

        # Таймер отслеживания ручной прокрутки (120 мс)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(120)
        self.poll_timer.timeout.connect(self._poll_scroll)

        # Таймер автопрокрутки (250 мс)
        self.autoscroll_timer = QTimer(self)
        self.autoscroll_timer.setInterval(250)
        self.autoscroll_timer.timeout.connect(self._autoscroll_tick)

    @property
    def accumulated_bgr(self) -> np.ndarray | None:
        if not self.strips:
            return None
        if len(self.strips) == 1:
            return self.strips[0]
        return np.vstack(self.strips)

    @accumulated_bgr.setter
    def accumulated_bgr(self, val: np.ndarray | None):
        if val is None:
            self.strips = []
            self.total_height = 0
        else:
            self.strips = [val]
            self.total_height = val.shape[0]

    def start(self):
        """Запуск захвата первого кадра, отображение направляющей рамки и запуск мониторинга скролла."""
        rx, ry, rw, rh = self.region
        try:
            img0 = safe_grab_screen_bgr(rx, ry, rw, rh, self.target_hwnd)
        except Exception as e:
            self.error.emit(str(e))
            return

        if img0 is None or img0.size == 0 or np.count_nonzero(img0) == 0:
            self.error.emit(tr("scroll_err_grab", "Не удалось захватить начальную область экрана."))
            return

        self.strips = [img0.copy()]
        self.total_height = img0.shape[0]
        self.last_frame = img0.copy()
        self.frames_captured = 1
        self.is_running = True
        self.is_paused = True
        self.is_autoscrolling = False
        self._is_stitching = False
        self._consecutive_fails = 0

        # Показываем направляющую рамку
        self.guide = ScrollingCaptureGuide(QRect(rx, ry, rw, rh))
        self.guide.show()

        self.progress.emit(self.frames_captured, self.total_height)
        self.poll_timer.start()

    def capture_step(self):
        """Ручной захват кадра (по высоте рамки или текущему положению скролла)."""
        if not self.is_running or self._is_stitching:
            return
        rx, ry, rw, rh = self.region
        try:
            curr_bgr = safe_grab_screen_bgr(rx, ry, rw, rh, self.target_hwnd)
        except Exception:
            return

        if curr_bgr is None or curr_bgr.size == 0:
            return

        # Принудительно пришиваем кадр, если совпадение не найдено (disjoint screens)
        self.stitch_frame(curr_bgr, force_append_on_fail=True)

    def toggle_pause(self) -> bool:
        """Переключает режим автоматической прокрутки колесом мыши."""
        if self.is_autoscrolling:
            self.stop_autoscroll()
            return True
        else:
            self.start_autoscroll()
            return False

    def start_autoscroll(self):
        if not self.is_running:
            return
        self.is_autoscrolling = True
        self.is_paused = False
        self.autoscroll_timer.start()

    def stop_autoscroll(self):
        self.is_autoscrolling = False
        self.is_paused = True
        self.autoscroll_timer.stop()

    def pause(self):
        self.stop_autoscroll()

    def resume(self):
        self.start_autoscroll()

    def finish(self):
        """Завершает захват и возвращает длинное склеенное изображение."""
        try:
            self.cleanup()
            if self.strips:
                final_img = np.vstack(self.strips) if len(self.strips) > 1 else self.strips[0]
                if final_img is not None and final_img.shape[0] > 0:
                    self.finished.emit(final_img)
                    return
            self.cancelled.emit()
        except Exception as e:
            print(f"[ScrollingCaptureEngine] Ошибка при завершении: {e}")
            self.cancelled.emit()

    def cancel(self):
        """Отмена длинного скриншота без сохранения."""
        try:
            self.cleanup()
            self.strips.clear()
            self.total_height = 0
            self.cancelled.emit()
        except Exception:
            self.cancelled.emit()

    def cleanup(self):
        self.is_running = False
        self.is_autoscrolling = False
        self.is_paused = True
        self._is_stitching = False
        self.poll_timer.stop()
        self.autoscroll_timer.stop()
        if self.guide:
            try:
                self.guide.close()
                self.guide.deleteLater()
            except Exception:
                pass
            self.guide = None

    def _poll_scroll(self):
        """Периодическая проверка изменения экрана при прокрутке колесом мыши."""
        if not self.is_running or self._is_stitching:
            return
        rx, ry, rw, rh = self.region
        try:
            curr_bgr = safe_grab_screen_bgr(rx, ry, rw, rh, self.target_hwnd)
        except Exception:
            return

        if curr_bgr is None or curr_bgr.size == 0:
            return

        # Быстрая проверка на отсутствие движения (прореженная сетка 1/8)
        if self.last_frame is not None:
            diff = np.mean(np.abs(
                curr_bgr[::8, ::8, :].astype(np.int16) -
                self.last_frame[::8, ::8, :].astype(np.int16)
            ))
            if diff < 1.2:
                self._consecutive_fails = 0
                return

        try:
            self._is_stitching = True
            matched = self.stitch_frame(curr_bgr, force_append_on_fail=False)
            if matched:
                self._consecutive_fails = 0
            else:
                self._consecutive_fails += 1
                # Если 2 тика подряд экран изменился, но перекрытие не найдено
                # (быстрый скролл пользователем или прыжок через страницу),
                # синхронизируем last_frame, чтобы не крутить тяжелые поиски на одном старом кадре
                if self._consecutive_fails >= 2:
                    self.last_frame = curr_bgr.copy()
                    self._consecutive_fails = 0
        finally:
            self._is_stitching = False

    def _autoscroll_tick(self):
        """Шаг автоматической прокрутки страницы без принудительного перемещения курсора мыши."""
        if not self.is_running or not self.is_autoscrolling:
            return

        rx, ry, rw, rh = self.region
        cx = rx + rw // 2
        cy = ry + rh // 2

        # Отправляем WM_MOUSEWHEEL окну под точкой скролла напрямую без перемещения реального курсора
        try:
            hwnd_under = user32.WindowFromPoint(POINT(int(cx), int(cy)))
            if hwnd_under:
                wParam = ctypes.c_uint((-140 << 16) & 0xFFFFFFFF).value
                lParam = (int(cy) << 16) | (int(cx) & 0xFFFF)
                user32.PostMessageW(hwnd_under, WM_MOUSEWHEEL, wParam, lParam)
        except Exception:
            pass

    def stitch_frame(self, curr_bgr: np.ndarray, force_append_on_fail: bool = False) -> bool:
        """
        Сопоставляет текущий захваченный кадр с накопленным изображением через
        высокоточный многополосный шаблонный поиск OpenCV matchTemplate.
        Бесшовно склеивает изображение вниз с точностью до 1 пикселя.
        """
        if curr_bgr is None or curr_bgr.size == 0 or not self.strips:
            return False

        H, W = curr_bgr.shape[:2]
        if H < 20 or W < 20:
            return False

        # Если превышена максимальная высота, больше не наращиваем
        if self.total_height >= self.max_height:
            return False

        ref_frame = self.last_frame if self.last_frame is not None else self.strips[-1]
        best_dy, best_conf = self._detect_vertical_shift(ref_frame, curr_bgr)

        # Надежное совпадение сдвига (от 4 до H пикселей)
        if best_conf >= 0.70 and 4 <= best_dy <= H:
            new_strip = curr_bgr[H - best_dy :, :, :]
            self.strips.append(new_strip)
            self.total_height += new_strip.shape[0]
            self.last_frame = curr_bgr.copy()
            self.frames_captured += 1
            self.progress.emit(self.frames_captured, self.total_height)
            return True

        # Если включен принудительный режим (пользователь нажал «Сделать кадр» без перекрытия):
        if force_append_on_fail:
            self.strips.append(curr_bgr)
            self.total_height += curr_bgr.shape[0]
            self.last_frame = curr_bgr.copy()
            self.frames_captured += 1
            self.progress.emit(self.frames_captured, self.total_height)
            return True

        return False

    def _detect_vertical_shift(self, prev_bgr: np.ndarray, curr_bgr: np.ndarray) -> tuple[int, float]:
        """
        Высокоскоростной многополосный алгоритм вычисления вертикального сдвига dy.
        Оптимизирован для полного исключения лагов и зависаний:
        - Равномерное горизонтальное прореживание до ширины ~160 px снижает вычислительную сложность в 10+ раз
          при сохранении идеальной точности vertical dy 1 px.
        - Поддержка страниц с плавающими и фиксированными шапками (sticky headers/navbars).
        - Ранний выход при уверенном совпадении (> 85%).
        """
        H, W = prev_bgr.shape[:2]

        # Исключаем вертикальный скроллбар справа (28 px) и границу слева (8 px)
        w_start = 8
        w_end = max(w_start + 40, W - 28)

        # Равномерное горизонтальное прореживание: строки не меняются (точность dy 100%),
        # а вычисления matchTemplate ускоряются на порядок!
        tw = 160
        step = max(1, (w_end - w_start) // tw)

        p_slice = np.ascontiguousarray(prev_bgr[:, w_start:w_end:step, :])
        c_slice = np.ascontiguousarray(curr_bgr[:, w_start:w_end:step, :])
        g_prev = cv2.cvtColor(p_slice, cv2.COLOR_BGR2GRAY)
        g_curr = cv2.cvtColor(c_slice, cv2.COLOR_BGR2GRAY)

        strip_h = min(36, max(16, H // 8))
        best_dy = 0
        best_conf = -1.0

        # Стратегия 1: Поиск полос из prev (снизу вверх) в текущем кадре curr
        for anchor_y in [H, int(H * 0.75), int(H * 0.5), int(H * 0.3)]:
            if anchor_y < strip_h:
                continue
            strip = g_prev[anchor_y - strip_h : anchor_y, :]
            if float(np.std(strip)) < 2.0:
                continue
            res = cv2.matchTemplate(g_curr, strip, cv2.TM_CCOEFF_NORMED)
            min_v, max_v, min_l, max_l = cv2.minMaxLoc(res)
            if max_v > best_conf:
                cand_dy = anchor_y - strip_h - max_l[1]
                if 4 <= cand_dy <= H:
                    best_conf = max_v
                    best_dy = cand_dy
                    # Ранний выход при уверенном совпадении > 85%
                    if best_conf >= 0.85:
                        return best_dy, best_conf

        # Стратегия 2: Поиск верхней полосы curr в prev.
        # Проверяем позицию y=0, а также полосу ниже типичной «плавающей шапки» сайтов
        top_candidates = [0]
        offset_y = min(H // 4, max(strip_h, 60))
        if offset_y + strip_h <= H and offset_y not in top_candidates:
            top_candidates.append(offset_y)

        for curr_y in top_candidates:
            top_strip = g_curr[curr_y : curr_y + strip_h, :]
            if float(np.std(top_strip)) < 2.0:
                continue
            res = cv2.matchTemplate(g_prev, top_strip, cv2.TM_CCOEFF_NORMED)
            min_v, max_v, min_l, max_l = cv2.minMaxLoc(res)
            if max_v > best_conf:
                cand_dy = max_l[1] - curr_y
                if 4 <= cand_dy <= H:
                    best_conf = max_v
                    best_dy = cand_dy
                    if best_conf >= 0.85:
                        return best_dy, best_conf

        return best_dy, best_conf
