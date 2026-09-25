# -*- coding: utf-8 -*-
"""
Плавающая интерактивная рамка живого перевода экрана в реальном времени (Live Screen Translator).
Позволяет свободно перемещать и масштабировать область на экране, непрерывно сканирует
текст под рамкой с помощью локального OCR и динамически отображает перевод:
1. В виде аккуратных субтитров (HUD) внизу рамки (в стиле игровых субтитров).
2. Либо прямо поверх оригинального текста (In-place замена) с точным соответствием:
   - размера шрифта (кегль)
   - цвета слов (извлечение доминантного цвета штриха с экрана)
   - гарнитуры и жирности начертания

Особенности эргономики:
- Панель управления и кнопки вынесены ПОЛНОСТЬЮ СНАРУЖИ (сверху рамки отдельным плавающим тулбаром).
- Рамка в режиме перевода является НЕОСЯЗАЕМОЙ (сквозной клик WS_EX_TRANSPARENT) — клики внутри рамки
  проходят прямо в игру или приложение за ней.
- В настройках и на тулбаре есть удобный переключатель между неосязаемым режимом и режимом настройки размера.
- Все кнопки тулбара имеют нормальный курсор руки/указателя (без курсора стрелочек изменения размера).
- Режим полной маскировки (Глазик): скрывает тулбар и рамку, оставляя лишь миниатюрную иконку глазика (16x16 px).
- Иконку глазика можно свободно перемещать по всему экрану как ЛЕВОЙ, так и ПРАВОЙ кнопкой мыши. Одиночный
  клик ЛКМ возвращает панель настроек.
- Высокоскоростной пакетный перевод (translate_batch) всех блоков за один сетевой запрос (<200 мс).
- Аппаратно исключена из захвата (WDA_EXCLUDEFROMCAPTURE), не грузит CPU на статичных кадрах (Smart Diff).
"""

from __future__ import annotations
import sys
import time
import numpy as np

from PyQt6.QtCore import (
    Qt, QRect, QRectF, QPoint, QPointF, QSize, QThread, pyqtSignal, pyqtSlot,
    QMutex, QMutexLocker, QTimer, QEvent
)
from PyQt6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QMenu,
    QApplication, QGraphicsDropShadowEffect, QToolTip
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QCursor, QPaintEvent, QMouseEvent,
    QPainterPath, QLinearGradient, QFontMetrics, QAction
)

from utils.screen_lock import safe_grab_screen_bgr, user32
from utils.ocr_helper import extract_text_and_blocks, extract_text_from_image
from utils.translator import translate_text, translate_batch, get_available_translation_languages
from utils.i18n import tr
from .icons import create_themed_icon, get_svg_pixmap


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


def create_stealth_menu(parent=None) -> QMenu:
    """Создает QMenu с аппаратным исключением из захвата экрана (WDA_EXCLUDEFROMCAPTURE)."""
    menu = QMenu(parent)
    if sys.platform == "win32":
        try:
            ctypes.windll.user32.SetWindowDisplayAffinity(int(menu.winId()), 0x00000011)
        except Exception:
            pass
    return menu


def extract_visual_props(crop_bgr: np.ndarray) -> dict:
    """
    Анализирует вырезку текста из экрана и определяет:
    - color_rgb: кортеж (r, g, b) оригинального цвета текста
    - is_bold: логический флаг жирности начертания
    - font_family: семейство шрифта ('Segoe UI', 'Trebuchet MS', 'Impact', 'Arial Black')
    """
    import cv2
    if crop_bgr is None or crop_bgr.size == 0 or crop_bgr.shape[0] < 4 or crop_bgr.shape[1] < 4:
        return {"color_rgb": (248, 250, 252), "is_bold": False, "font_family": "Segoe UI"}

    try:
        h, w = crop_bgr.shape[:2]
        gray = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        _, mask = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        # Анализ фона по краям вырезки
        border_pixels = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
        bg_val = float(np.median(border_pixels))

        mean_mask_255 = float(np.mean(gray[mask == 255])) if np.any(mask == 255) else 128.0
        is_bright_text = mean_mask_255 > bg_val
        text_mask = (mask == 255) if is_bright_text else (mask == 0)

        text_pts = float(np.count_nonzero(text_mask))
        total_pts = float(mask.size)
        density = text_pts / max(1.0, total_pts)

        # Определение оригинального цвета текста
        if text_pts >= 4:
            bgr_median = np.median(crop_bgr[text_mask], axis=0).astype(int)
            color_rgb = (int(bgr_median[2]), int(bgr_median[1]), int(bgr_median[0]))
        else:
            color_rgb = (248, 250, 252)

        # Определение жирности и гарнитуры
        is_bold = (density > 0.25) or (h >= 26 and density > 0.20)
        if h >= 32 and is_bold:
            font_family = "Trebuchet MS"
        elif density > 0.32:
            font_family = "Arial Black"
        else:
            font_family = "Segoe UI"

        return {
            "color_rgb": color_rgb,
            "is_bold": is_bold,
            "font_family": font_family
        }
    except Exception:
        return {"color_rgb": (248, 250, 252), "is_bold": False, "font_family": "Segoe UI"}


def group_multiline_blocks(raw_blocks: list[dict]) -> list[dict]:
    """
    Интеллектуально объединяет строки OCR, принадлежащие одному абзацу/предложению,
    в единые смысловые блоки с общим контекстом перевода.
    Предотвращает потерю смысла при переносе строк (например, 'favorite / talk show host').
    """
    if not raw_blocks:
        return []

    # Сортируем блоки сверху вниз, затем слева направо
    sorted_blocks = sorted(raw_blocks, key=lambda b: (float(b.get("y", 0)), float(b.get("x", 0))))
    merged = []

    for b in sorted_blocks:
        txt = b.get("text", "").strip()
        if not txt:
            continue
        bx = float(b.get("x", 0))
        by = float(b.get("y", 0))
        bw = float(b.get("width", 50))
        bh = float(b.get("height", 20))

        if not merged:
            merged.append({
                "text": txt,
                "x": bx,
                "y": by,
                "width": bw,
                "height": bh,
                "lines_count": 1
            })
            continue

        prev = merged[-1]
        prev_bottom = prev["y"] + prev["height"]
        prev_h = prev["height"] / max(1, prev.get("lines_count", 1))
        gap_y = by - prev_bottom

        # Проверяем горизонтальное перекрытие или выравнивание
        h_overlap = (bx < (prev["x"] + prev["width"])) and ((bx + bw) > prev["x"])
        left_aligned = abs(bx - prev["x"]) < 40.0
        prev_center = prev["x"] + prev["width"] / 2.0
        curr_center = bx + bw / 2.0
        center_aligned = abs(curr_center - prev_center) < 50.0

        # Условие объединения:
        # 1. Строка находится прямо под предыдущей (межстрочный интервал абзаца или субтитров)
        # 2. Выровнена по горизонтали, левому краю или центру
        is_subsequent_line = (-10.0 <= gap_y <= max(22.0, prev_h * 1.45)) and (h_overlap or left_aligned or center_aligned)

        if is_subsequent_line:
            new_x = min(prev["x"], bx)
            new_y = min(prev["y"], by)
            new_r = max(prev["x"] + prev["width"], bx + bw)
            new_b = max(prev_bottom, by + bh)
            prev["text"] = prev["text"] + " " + txt
            prev["x"] = new_x
            prev["y"] = new_y
            prev["width"] = new_r - new_x
            prev["height"] = new_b - new_y
            prev["lines_count"] = prev.get("lines_count", 1) + 1
        else:
            merged.append({
                "text": txt,
                "x": bx,
                "y": by,
                "width": bw,
                "height": bh,
                "lines_count": 1
            })

    return merged


class TranslationScannerWorker(QThread):
    """
    Фоновый рабочий поток сканирования и перевода.
    Работает с оптимизацией Smart Diff: не тратит ресурсы процессора,
    когда изображение в рамке остаётся неизменным.
    """
    translation_ready = pyqtSignal(str, str, list)  # original, translated, blocks
    scan_status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._paused = False
        self._smart_diff = True
        self._interval_ms = 300
        self._mutex = QMutex()
        self._rect = QRect(100, 100, 480, 240)
        self._src_lang = "auto"
        self._tgt_lang = "ru"
        self._mode = "inplace"
        self._last_frame_small = None
        self._last_ocr_text = ""

    def set_mode(self, mode: str):
        with QMutexLocker(self._mutex):
            self._mode = mode
            self._last_ocr_text = ""

    def update_geometry(self, rect: QRect):
        with QMutexLocker(self._mutex):
            self._rect = QRect(rect)
            self._last_frame_small = None

    def set_languages(self, src: str, tgt: str):
        with QMutexLocker(self._mutex):
            self._src_lang = src
            self._tgt_lang = tgt
            self._last_ocr_text = ""

    def set_paused(self, paused: bool):
        with QMutexLocker(self._mutex):
            self._paused = paused

    def set_interval(self, interval_ms: int):
        with QMutexLocker(self._mutex):
            self._interval_ms = max(100, interval_ms)

    def set_smart_diff(self, enabled: bool):
        with QMutexLocker(self._mutex):
            self._smart_diff = enabled
            if not enabled:
                self._last_frame_small = None

    def stop(self):
        self._running = False
        self.wait(1500)

    def run(self):
        import cv2
        while self._running:
            with QMutexLocker(self._mutex):
                paused = self._paused
                r = QRect(self._rect)
                src = self._src_lang
                tgt = self._tgt_lang
                interval = self._interval_ms
                use_diff = self._smart_diff
                mode = self._mode

            if paused or r.width() < 30 or r.height() < 30:
                self.msleep(200)
                continue

            rx, ry, rw, rh = r.x(), r.y(), r.width(), r.height()
            frame_bgr = safe_grab_screen_bgr(rx, ry, rw, rh)

            if frame_bgr is None or frame_bgr.size == 0:
                self.msleep(interval)
                continue

            # Smart Diff: уменьшаем кадр до 120x80 и проверяем изменение
            if use_diff:
                try:
                    small_gray = cv2.cvtColor(cv2.resize(frame_bgr, (120, 80), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                except Exception:
                    small_gray = None

                if self._last_frame_small is not None and small_gray is not None:
                    diff = cv2.absdiff(small_gray, self._last_frame_small)
                    mean_diff = np.mean(diff)
                    if mean_diff < 1.2:
                        self.msleep(interval)
                        continue

                self._last_frame_small = small_gray

            # Запуск OCR
            raw_blocks = extract_text_and_blocks(frame_bgr, lang=src)
            blocks = group_multiline_blocks(raw_blocks)
            full_text = " ".join(b["text"] for b in blocks if b.get("text")).strip()

            if not full_text:
                full_text, _ = extract_text_from_image(frame_bgr, lang=src)

            if not full_text:
                self.msleep(interval)
                continue

            if full_text == self._last_ocr_text:
                self.msleep(interval)
                continue

            self._last_ocr_text = full_text

            if mode == "hud":
                # В режиме субтитров HUD переводим только общий текст одним быстрым вызовом (~50-150 мс)
                translated_full = translate_text(full_text, source_lang=src, target_lang=tgt)
                self.translation_ready.emit(full_text, translated_full, [])
            else:
                # В режиме In-place собираем все непустые блоки и переводим ПАКЕТОМ за 1 сетевой запрос
                valid_blocks = []
                block_texts = []
                for b in blocks:
                    b_text = b.get("text", "").strip()
                    if b_text:
                        valid_blocks.append(b)
                        block_texts.append(b_text)

                if block_texts:
                    translated_list = translate_batch(block_texts, source_lang=src, target_lang=tgt)
                else:
                    translated_list = []

                translated_blocks = []
                f_h, f_w = frame_bgr.shape[:2]
                for b, b_tr in zip(valid_blocks, translated_list):
                    bx = int(max(0, min(f_w - 2, b.get("x", 0))))
                    by = int(max(0, min(f_h - 2, b.get("y", 0))))
                    bw = int(max(10, min(f_w - bx, b.get("width", 50))))
                    bh = int(max(10, min(f_h - by, b.get("height", 20))))

                    crop = frame_bgr[by:by+bh, bx:bx+bw]
                    visual = extract_visual_props(crop)

                    lines_cnt = max(1, b.get("lines_count", 1))
                    single_line_h = float(bh) / float(lines_cnt)

                    translated_blocks.append({
                        "original": b.get("text", "").strip(),
                        "translated": b_tr,
                        "x": bx,
                        "y": by,
                        "width": bw,
                        "height": bh,
                        "lines_count": lines_cnt,
                        "line_height": single_line_h,
                        "color_rgb": visual["color_rgb"],
                        "is_bold": visual["is_bold"],
                        "font_family": visual["font_family"]
                    })

                translated_full = " ".join(translated_list) if translated_list else translate_text(full_text, source_lang=src, target_lang=tgt)
                self.translation_ready.emit(full_text, translated_full, translated_blocks)

            self.msleep(interval)


class EyeUnlockPill(QPushButton):
    """
    Автономная миниатюрная плавающая кнопка разблокировки глазика.
    Сделана сверхкомпактной (16x16 px, ровно в 2 раза меньше обычной 28x28 px).
    Свободно перемещается по экрану как ЛЕВОЙ, так и ПРАВОЙ кнопкой мыши.
    Одиночный клик левой кнопкой (без перемещения) возвращает панель управления.
    Одиночный клик правой кнопкой (без перемещения) отключает/включает режим перевода (зачеркнутый глазик).
    """
    def __init__(self, target_window: TranslationFrameWindow):
        super().__init__()
        self.target_window = target_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(16, 16)
        self.setIconSize(QSize(10, 10))
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.update_state()

        self._drag_start = QPoint()
        self._start_pos = QPoint()
        self._has_dragged = False

    def update_state(self):
        """Обновляет иконку и стиль в зависимости от активности функции перевода."""
        is_paused = getattr(self.target_window, "is_paused", False)
        if is_paused:
            self.setIcon(create_themed_icon("eye_off", is_dark=True, size=10, custom_color="#f87171"))
            self.setToolTip(tr("trans_unlock_disabled_tooltip", "Перевод отключен. Кликните ПКМ для включения, ЛКМ для возврата настроек, зажмите для перемещения."))
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(15, 23, 42, 245);
                    border: 1px solid rgba(248, 113, 113, 230);
                    border-radius: 3px;
                    padding: 0px;
                    margin: 0px;
                }
                QPushButton:hover {
                    background-color: #991b1b;
                    border-color: #fca5a5;
                }
            """)
        else:
            self.setIcon(create_themed_icon("eye", is_dark=True, size=10, custom_color="#38bdf8"))
            self.setToolTip(tr("trans_unlock_tooltip", "Перетащите ЛКМ/ПКМ в любое место. Кликните ЛКМ для возврата настроек, ПКМ для отключения перевода."))
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(15, 23, 42, 235);
                    border: 1px solid rgba(56, 189, 248, 220);
                    border-radius: 3px;
                    padding: 0px;
                    margin: 0px;
                }
                QPushButton:hover {
                    background-color: #0284c7;
                    border-color: #7dd3fc;
                }
            """)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self._drag_start = event.globalPosition().toPoint()
            self._start_pos = self.pos()
            self._has_dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton):
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() >= 3:
                self._has_dragged = True
                self.move(self._start_pos + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._has_dragged:
                self._on_clicked()
            event.accept()
            return
        elif event.button() == Qt.MouseButton.RightButton:
            if not self._has_dragged:
                self._on_right_clicked()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_clicked(self):
        self.hide()
        if self.target_window:
            self.target_window.set_stealth_lock(False)

    def _on_right_clicked(self):
        """Отключает или возобновляет функцию перевода с отображением перечеркнутого глазика."""
        if self.target_window:
            self.target_window._toggle_pause()

    def update_position(self):
        if self.target_window and self.target_window.isVisible():
            geo = self.target_window.geometry()
            self.move(geo.right() - 20, geo.top() + 4)

    def showEvent(self, event):
        super().showEvent(event)
        self.update_state()
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
                style = ctypes.windll.user32.GetWindowLongW(wid, -20)
                ctypes.windll.user32.SetWindowLongW(wid, -20, style | 0x08000000)
            except Exception:
                pass


class TranslationHudWindow(QWidget):
    """
    Автономное плавающее окно HUD-субтитров живого перевода.
    Размещается поверх экрана, свободно перемещается мышью по всему экрану (на любой монитор),
    не обрезается границами рамки захвата, не перехватывает фокус (WS_EX_NOACTIVATE).
    """
    def __init__(self, frame_window: TranslationFrameWindow):
        super().__init__()
        self.frame_window = frame_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(220, 48)
        self.resize(460, 84)

        self._user_moved = False
        self._is_dragging = False
        self._drag_start = QPoint()
        self._win_start_pos = QPoint()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.hud_frame = QFrame(self)
        hud_layout = QVBoxLayout(self.hud_frame)
        hud_layout.setContentsMargins(12, 8, 12, 8)

        self.lbl_hud_text = QLabel(tr("trans_waiting_text", "Ожидание текста в рамке..."), self.hud_frame)
        self.lbl_hud_text.setWordWrap(True)
        self.lbl_hud_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hud_layout.addWidget(self.lbl_hud_text)

        main_layout.addWidget(self.hud_frame)

        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.setToolTip(tr("trans_hud_move_tooltip", "Субтитры перевода. Зажмите ЛКМ для свободного перемещения по всему экрану."))

        self.sync_style()
        self.sync_to_frame()

    def sync_to_frame(self):
        """Синхронизирует начальное положение под рамкой перевода, если пользователь еще не перемещал HUD вручную."""
        if self._user_moved or not self.frame_window:
            return
        geo = self.frame_window.geometry()
        w = max(340, min(640, geo.width()))
        h = max(50, self.height())
        x = geo.x() + (geo.width() - w) // 2
        y = geo.bottom() + 8
        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)
        if y + h > screen_geo.bottom() - 20:
            y = max(10, geo.y() - h - 40)
        self.setGeometry(x, y, w, h)

    def sync_style(self):
        if not self.frame_window:
            return
        r, g, b = self.frame_window.THEME_COLORS.get(self.frame_window.bg_theme, (15, 23, 42))
        alpha = int(self.frame_window.bg_opacity * 255)
        font_px = self.frame_window.hud_font_size if self.frame_window.hud_font_size > 0 else 13

        if self.frame_window.bg_opacity <= 0.05:
            self.hud_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: transparent;
                    border: none;
                }}
                QLabel {{
                    color: #ffffff;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: {font_px}px;
                    font-weight: 600;
                    line-height: 1.35;
                }}
            """)
        else:
            self.hud_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: rgba({r}, {g}, {b}, {alpha});
                    border: 1px solid rgba(59, 130, 246, {min(220, alpha + 40)});
                    border-radius: 8px;
                }}
                QLabel {{
                    color: #ffffff;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: {font_px}px;
                    font-weight: 500;
                    line-height: 1.35;
                }}
            """)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start = event.globalPosition().toPoint()
            self._win_start_pos = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() >= 2:
                self._user_moved = True
                self.move(self._win_start_pos + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
                style = ctypes.windll.user32.GetWindowLongW(wid, -20)
                ctypes.windll.user32.SetWindowLongW(wid, -20, style | 0x08000000)
            except Exception:
                pass


class TranslationControlBar(QWidget):
    """
    Автономная внешняя панель управления рамки перевода.
    Размещается полностью СНАРУЖИ (сверху) над рамкой перевода.
    Содержит все кнопки и настройки, перемещается вместе с рамкой,
    имеет нормальные курсоры-указатели (без стрелок изменения размера).
    """
    def __init__(self, frame_window: TranslationFrameWindow):
        super().__init__()
        self.frame_window = frame_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(32)

        self._is_dragging = False
        self._drag_start = QPoint()
        self._bar_start_pos = QPoint()
        self._frame_start_pos = QPoint()

        self._setup_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
                style = ctypes.windll.user32.GetWindowLongW(wid, -20)
                ctypes.windll.user32.SetWindowLongW(wid, -20, style | 0x08000000)
            except Exception:
                pass

    def sync_to_frame(self):
        if not self.frame_window:
            return
        geo = self.frame_window.geometry()
        bar_w = max(340, geo.width())
        bar_h = 32
        bar_x = geo.x()
        bar_y = geo.y() - bar_h - 4
        if bar_y < 0:
            bar_y = geo.bottom() + 4
        self.setGeometry(bar_x, bar_y, bar_w, bar_h)

    def _menu_stylesheet(self) -> str:
        return """
            QMenu {
                background-color: #18181b;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                color: #f4f4f5;
                padding: 5px 14px;
                border-radius: 4px;
                font-size: 11px;
                font-family: 'Segoe UI', sans-serif;
            }
            QMenu::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #27272a;
                margin: 4px 6px;
            }
        """

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.inner_frame = QFrame(self)
        self.inner_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(18, 20, 28, 245);
                border: 1px solid rgba(59, 130, 246, 180);
                border-radius: 6px;
            }
            QLabel {
                color: #f4f4f5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton {
                background-color: rgba(39, 39, 42, 210);
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 500;
                padding: 3px 7px;
            }
            QPushButton:hover {
                background-color: #2563eb;
                color: #ffffff;
                border-color: #60a5fa;
            }
        """)

        inner_layout = QHBoxLayout(self.inner_frame)
        inner_layout.setContentsMargins(6, 3, 6, 3)
        inner_layout.setSpacing(6)

        # 1. Заголовок и маркер перемещения всей системы
        self.title_container = QWidget()
        title_box = QHBoxLayout(self.title_container)
        title_box.setContentsMargins(4, 2, 6, 2)
        title_box.setSpacing(5)
        self.title_container.setStyleSheet("background-color: rgba(30, 41, 59, 160); border-radius: 4px;")
        self.title_container.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))

        self.drag_grip = QLabel(self)
        self.drag_grip.setPixmap(get_svg_pixmap("move", color="#94a3b8", size=13))
        title_box.addWidget(self.drag_grip)

        self.lbl_icon = QLabel(self)
        self.lbl_icon.setPixmap(get_svg_pixmap("translate", color="#60a5fa", size=14))
        title_box.addWidget(self.lbl_icon)

        self.lbl_title = QLabel(tr("trans_frame_title", "Live Перевод"))
        title_box.addWidget(self.lbl_title)
        inner_layout.addWidget(self.title_container)

        # 2. Кнопка выбора языков
        self.btn_lang = QPushButton()
        self.btn_lang.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._update_lang_button_text()
        self.btn_lang.setToolTip(tr("trans_lang_tooltip", "Нажмите для выбора языков перевода"))
        self.btn_lang.clicked.connect(self._show_lang_menu)
        inner_layout.addWidget(self.btn_lang)

        # 3. Кнопка выбора режима (Субтитры / Поверх текста)
        self.btn_mode = QPushButton()
        self.btn_mode.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._update_mode_button_text()
        self.btn_mode.setToolTip(tr("trans_mode_tooltip", "Нажмите для переключения режима отображения"))
        self.btn_mode.clicked.connect(self._show_mode_menu)
        inner_layout.addWidget(self.btn_mode)

        # 4. Кнопка переключения неосязаемости (Passthrough / Edit mode)
        self.btn_passthrough = QPushButton()
        self.btn_passthrough.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_passthrough.setFixedSize(26, 24)
        self.btn_passthrough.clicked.connect(self._toggle_passthrough)
        inner_layout.addWidget(self.btn_passthrough)
        self.update_passthrough_ui()

        # 5. Кнопка расширенных настроек
        self.btn_settings = QPushButton(tr("trans_settings_btn", "Настройки ▾"))
        self.btn_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_settings.setIcon(create_themed_icon("settings", is_dark=True, size=13))
        self.btn_settings.setToolTip(tr("trans_settings_tooltip", "Настройки прозрачности, темы, шрифта и цвета"))
        self.btn_settings.clicked.connect(self._show_settings_menu)
        inner_layout.addWidget(self.btn_settings)

        # 6. Пауза / Пуск
        self.btn_pause = QPushButton()
        self.btn_pause.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=13))
        self.btn_pause.setToolTip(tr("trans_pause_tooltip", "Приостановить / возобновить сканирование"))
        self.btn_pause.setFixedSize(26, 24)
        self.btn_pause.clicked.connect(self.frame_window._toggle_pause)
        inner_layout.addWidget(self.btn_pause)

        # 7. Копировать перевод
        self.btn_copy = QPushButton()
        self.btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_copy.setIcon(create_themed_icon("copy", is_dark=True, size=13))
        self.btn_copy.setToolTip(tr("trans_copy_tooltip", "Скопировать текущий перевод в буфер"))
        self.btn_copy.setFixedSize(26, 24)
        self.btn_copy.clicked.connect(self.frame_window._copy_translation)
        inner_layout.addWidget(self.btn_copy)

        # 8. Кнопка «Глазик» — переход в скрытый режим
        self.btn_eye = QPushButton()
        self.btn_eye.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_eye.setIcon(create_themed_icon("eye", is_dark=True, size=14, custom_color="#38bdf8"))
        self.btn_eye.setToolTip(tr("trans_eye_tooltip", "Скрыть рамку и панель (оставить только значок глазика). Клики мыши будут проходить сквозь рамку."))
        self.btn_eye.setFixedSize(26, 24)
        self.btn_eye.setStyleSheet("QPushButton { border-color: rgba(56, 189, 248, 140); } QPushButton:hover { background-color: #0284c7; }")
        self.btn_eye.clicked.connect(lambda: self.frame_window.set_stealth_lock(True))
        inner_layout.addWidget(self.btn_eye)

        # 9. Закрыть
        self.btn_close = QPushButton()
        self.btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_close.setIcon(create_themed_icon("close", is_dark=True, size=13))
        self.btn_close.setToolTip(tr("trans_close_tooltip", "Закрыть рамку перевода"))
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setStyleSheet("QPushButton:hover { background-color: #ef4444; border-color: #f87171; }")
        self.btn_close.clicked.connect(self.frame_window.close)
        inner_layout.addWidget(self.btn_close)

        layout.addWidget(self.inner_frame)

    def _toggle_passthrough(self):
        new_state = not getattr(self.frame_window, "passthrough_enabled", True)
        self.frame_window.set_passthrough(new_state)
        msg = tr("trans_pass_on_pop", "Сквозной клик ВКЛЮЧЕН (клики проходят сквозь рамку)") if new_state else tr("trans_pass_off_pop", "Режим настройки (рамку можно растягивать и двигать)")
        QToolTip.showText(QCursor.pos(), msg, self.btn_passthrough)

    def update_passthrough_ui(self):
        if not hasattr(self, "btn_passthrough"):
            return
        is_pass = getattr(self.frame_window, "passthrough_enabled", True)
        if is_pass:
            self.btn_passthrough.setIcon(create_themed_icon("passthrough", is_dark=True, size=14, custom_color="#38bdf8"))
            self.btn_passthrough.setToolTip(tr("trans_pass_on_tip", "Сквозной клик ВКЛЮЧЕН (рамка неосязаема, клики проходят в игру). Нажмите для режима настройки размера."))
            self.btn_passthrough.setStyleSheet("QPushButton { border-color: rgba(56, 189, 248, 160); background-color: rgba(14, 165, 233, 40); }")
        else:
            self.btn_passthrough.setIcon(create_themed_icon("maximize_2", is_dark=True, size=13, custom_color="#eab308"))
            self.btn_passthrough.setToolTip(tr("trans_pass_off_tip", "Режим настройки ВКЛЮЧЕН (рамку можно двигать и растягивать). Нажмите для включения сквозного клика."))
            self.btn_passthrough.setStyleSheet("QPushButton { border-color: #eab308; background-color: rgba(234, 179, 8, 40); }")

    def _update_lang_button_text(self):
        s = self.frame_window.src_lang.upper() if self.frame_window.src_lang != "auto" else tr("lang_auto", "Auto")
        t = self.frame_window.tgt_lang.upper()
        self.btn_lang.setText(tr("trans_lang_btn", "Язык: {src} → {tgt} ▾").format(src=s, tgt=t))

    def _update_mode_button_text(self):
        if self.frame_window.current_mode == "inplace":
            self.btn_mode.setText(tr("trans_mode_btn", "Режим: {mode} ▾").format(mode=tr("trans_mode_inplace", "Поверх текста")))
            self.btn_mode.setIcon(create_themed_icon("scan_text", is_dark=True, size=13))
        else:
            self.btn_mode.setText(tr("trans_mode_btn", "Режим: {mode} ▾").format(mode=tr("trans_mode_hud", "Субтитры")))
            self.btn_mode.setIcon(create_themed_icon("text", is_dark=True, size=13))

    def _show_lang_menu(self):
        menu = create_stealth_menu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        pairs = [
            ("Auto → RU", "auto", "ru", "Auto → Русский"),
            ("EN → RU", "en", "ru", "English → Русский"),
            ("JA → RU", "ja", "ru", "Japanese → Русский"),
            ("ZH → RU", "zh-CN", "ru", "Chinese → Русский"),
            ("DE → RU", "de", "ru", "German → Русский"),
            ("FR → RU", "fr", "ru", "French → Русский"),
            ("RU → EN", "ru", "en", "Русский → English"),
        ]
        for _, s, t, label in pairs:
            act = menu.addAction(label)
            act.triggered.connect(lambda ch, src=s, tgt=t: self.frame_window._set_languages(src, tgt))

        menu.addSeparator()

        src_menu = create_stealth_menu(menu)
        src_menu.setTitle(tr("trans_menu_src", "Исходный язык"))
        src_menu.setStyleSheet(self._menu_stylesheet())
        all_src = [("auto", "Auto"), ("en", "English"), ("ja", "Japanese"), ("zh-CN", "Chinese"),
                   ("de", "German"), ("fr", "French"), ("es", "Spanish"), ("ko", "Korean"), ("ru", "Русский")]
        for tag, name in all_src:
            act = src_menu.addAction(name)
            act.triggered.connect(lambda ch, s=tag: self.frame_window._set_languages(s, self.frame_window.tgt_lang))
        menu.addMenu(src_menu)

        tgt_menu = create_stealth_menu(menu)
        tgt_menu.setTitle(tr("trans_menu_tgt", "Язык перевода"))
        tgt_menu.setStyleSheet(self._menu_stylesheet())
        all_tgt = [("ru", "Русский"), ("en", "English"), ("de", "Deutsch"), ("fr", "Français"),
                   ("es", "Español"), ("zh-CN", "中文"), ("ja", "日本語")]
        for tag, name in all_tgt:
            act = tgt_menu.addAction(name)
            act.triggered.connect(lambda ch, t=tag: self.frame_window._set_languages(self.frame_window.src_lang, t))
        menu.addMenu(tgt_menu)

        was_paused = self.frame_window.is_paused
        if hasattr(self.frame_window, "worker"):
            self.frame_window.worker.set_paused(True)
        try:
            menu.exec(self.btn_lang.mapToGlobal(QPoint(0, self.btn_lang.height() + 2)))
        finally:
            if hasattr(self.frame_window, "worker"):
                self.frame_window.worker.set_paused(was_paused)

    def _show_mode_menu(self):
        menu = create_stealth_menu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        act_hud = menu.addAction(tr("trans_mode_hud", "Субтитры (HUD внизу)"))
        act_hud.setIcon(create_themed_icon("text", is_dark=True, size=13))
        act_hud.triggered.connect(lambda: self.frame_window._set_display_mode("hud"))

        act_inplace = menu.addAction(tr("trans_mode_inplace", "Поверх текста (In-place)"))
        act_inplace.setIcon(create_themed_icon("scan_text", is_dark=True, size=13))
        act_inplace.triggered.connect(lambda: self.frame_window._set_display_mode("inplace"))

        was_paused = self.frame_window.is_paused
        if hasattr(self.frame_window, "worker"):
            self.frame_window.worker.set_paused(True)
        try:
            menu.exec(self.btn_mode.mapToGlobal(QPoint(0, self.btn_mode.height() + 2)))
        finally:
            if hasattr(self.frame_window, "worker"):
                self.frame_window.worker.set_paused(was_paused)

    def _show_settings_menu(self):
        menu = create_stealth_menu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        # 1. Неосязаемая рамка (сквозные клики)
        act_pass = menu.addAction(tr("trans_opt_passthrough", "Неосязаемая рамка (клики сквозь рамку)"))
        act_pass.setIcon(create_themed_icon("passthrough", is_dark=True, size=13))
        act_pass.setCheckable(True)
        act_pass.setChecked(self.frame_window.passthrough_enabled)
        act_pass.setToolTip(tr("trans_opt_pass_tip", "Позволяет нажимать сквозь рамку прямо в игру или программу"))
        act_pass.triggered.connect(lambda ch: self.frame_window.set_passthrough(ch))

        menu.addSeparator()

        # 2. Повторение цвета оригинального текста
        act_color = menu.addAction(tr("trans_opt_match_color", "Повторять цвет текста оригинала"))
        act_color.setCheckable(True)
        act_color.setChecked(self.frame_window.match_text_color)
        act_color.setToolTip(tr("trans_opt_match_color_tip", "Окрашивать переведенные слова в цвета оригинала с экрана"))
        act_color.triggered.connect(self.frame_window._toggle_match_color)

        # 3. Повторение шрифта и жирности оригинала
        act_font = menu.addAction(tr("trans_opt_match_font", "Повторять шрифт и начертание оригинала"))
        act_font.setCheckable(True)
        act_font.setChecked(self.frame_window.match_font_family)
        act_font.setToolTip(tr("trans_opt_match_font_tip", "Подбирать жирность и гарнитуру шрифта, как в исходном тексте"))
        act_font.triggered.connect(self.frame_window._toggle_match_font)

        menu.addSeparator()

        # 4. Прозрачность фона
        op_menu = create_stealth_menu(menu)
        op_menu.setTitle(tr("trans_menu_opacity", "Прозрачность фона"))
        op_menu.setStyleSheet(self._menu_stylesheet())
        op_levels = [
            (1.0, tr("trans_op_100", "100% (Непрозрачный)")),
            (0.85, tr("trans_op_85", "85% (Оптимальный)")),
            (0.60, tr("trans_op_60", "60% (Полупрозрачный)")),
            (0.30, tr("trans_op_30", "30% (Слабый)")),
            (0.0, tr("trans_op_0", "0% (Без фона)")),
        ]
        for val, label in op_levels:
            act = op_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(abs(self.frame_window.bg_opacity - val) < 0.05)
            act.triggered.connect(lambda ch, v=val: self.frame_window._set_opacity(v))
        menu.addMenu(op_menu)

        # 5. Стиль / Цвет фона
        theme_menu = create_stealth_menu(menu)
        theme_menu.setTitle(tr("trans_menu_theme", "Цвет фона"))
        theme_menu.setStyleSheet(self._menu_stylesheet())
        themes = [
            ("slate", tr("trans_theme_slate", "Тёмный сланец (Slate)")),
            ("oled", tr("trans_theme_oled", "Глубокий чёрный (OLED)")),
            ("cyber", tr("trans_theme_cyber", "Кибер-синий (Cyber)")),
        ]
        for key, name in themes:
            act = theme_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(self.frame_window.bg_theme == key)
            act.triggered.connect(lambda ch, k=key: self.frame_window._set_theme(k))
        menu.addMenu(theme_menu)

        # 6. Размер шрифта субтитров
        font_menu = create_stealth_menu(menu)
        font_menu.setTitle(tr("trans_menu_font_size", "Размер шрифта субтитров"))
        font_menu.setStyleSheet(self._menu_stylesheet())
        fonts = [
            (0, tr("trans_font_auto", "Авто (по тексту)")),
            (11, tr("trans_font_small", "Мелкий (11 px)")),
            (14, tr("trans_font_medium", "Средний (14 px)")),
            (18, tr("trans_font_large", "Крупный (18 px)")),
            (22, tr("trans_font_xlarge", "Очень крупный (22 px)")),
        ]
        for sz, label in fonts:
            act = font_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.frame_window.hud_font_size == sz)
            act.triggered.connect(lambda ch, s=sz: self.frame_window._set_font_size(s))
        menu.addMenu(font_menu)

        menu.addSeparator()

        # 7. Скорость сканирования (FPS)
        fps_menu = create_stealth_menu(menu)
        fps_menu.setTitle(tr("trans_menu_fps", "Скорость сканирования"))
        fps_menu.setStyleSheet(self._menu_stylesheet())
        speeds = [
            (150, tr("trans_fps_fast", "Быстро (150 мс)")),
            (300, tr("trans_fps_opt", "Оптимально (300 мс)")),
            (600, tr("trans_fps_eco", "Энергосбережение (600 мс)")),
        ]
        for ms, label in speeds:
            act = fps_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.frame_window.scan_interval == ms)
            act.triggered.connect(lambda ch, m=ms: self.frame_window._set_scan_interval(m))
        menu.addMenu(fps_menu)

        # 8. Smart Diff (Оптимизация CPU)
        act_diff = menu.addAction(tr("trans_menu_smart_diff", "Умная пауза при статичном кадре"))
        act_diff.setCheckable(True)
        act_diff.setChecked(self.frame_window.smart_diff_enabled)
        act_diff.triggered.connect(self.frame_window._toggle_smart_diff)

        was_paused = self.frame_window.is_paused
        if hasattr(self.frame_window, "worker"):
            self.frame_window.worker.set_paused(True)
        try:
            menu.exec(self.btn_settings.mapToGlobal(QPoint(0, self.btn_settings.height() + 2)))
        finally:
            if hasattr(self.frame_window, "worker"):
                self.frame_window.worker.set_paused(was_paused)

    # ------------------ Перемещение всей связки (панель + рамка) ------------------

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start = event.globalPosition().toPoint()
            self._bar_start_pos = self.pos()
            self._frame_start_pos = self.frame_window.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_start
            self.move(self._bar_start_pos + delta)
            self.frame_window.move(self._frame_start_pos + delta)
            if hasattr(self.frame_window, "worker") and self.frame_window.worker.isRunning():
                self.frame_window.worker.update_geometry(self.frame_window.geometry())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TranslationFrameWindow(QWidget):
    """
    Интерактивное окно рамки живого перевода (Capture Region).
    Поддерживает:
    - Область захвата 1:1 без лишних элементов внутри (панель управления находится снаружи).
    - Неосязаемый режим (Passthrough / WS_EX_TRANSPARENT) по умолчанию при переводе.
    - Переключение в режим изменения размера и перемещения по кнопке или настройкам.
    - Аппаратное исключение из захвата экрана (WDA_EXCLUDEFROMCAPTURE).
    - Динамический расчет размера шрифта, цвета слов и гарнитуры оригинала.
    - Адаптивный перенос и расчет многострочного текста без вылезания за границы.
    - Автономный перетаскиваемый значок глазика (ЛКМ / ПКМ).
    """
    closed = pyqtSignal()
    frame_closed = pyqtSignal()

    HANDLE_SIZE = 8
    HANDLE_NONE = 0
    HANDLE_TL = 1
    HANDLE_T = 2
    HANDLE_TR = 3
    HANDLE_R = 4
    HANDLE_BR = 5
    HANDLE_B = 6
    HANDLE_BL = 7
    HANDLE_L = 8

    THEME_COLORS = {
        "slate": (15, 23, 42),
        "oled": (0, 0, 0),
        "cyber": (10, 25, 47),
    }

    HEADER_OFFSET = 0
    HEADER_BAR_HEIGHT = 32

    def get_capture_rect(self) -> QRect:
        """Возвращает прямоугольник захвата (в новой архитектуре вся область рамки является захватом)."""
        return self.geometry()

    def __init__(self, initial_rect: QRect | None = None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 100)

        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)

        if initial_rect is not None and initial_rect.isValid() and not initial_rect.isEmpty():
            w = max(300, initial_rect.width())
            h = max(120, initial_rect.height())
            x = initial_rect.x()
            y = initial_rect.y()
            self.setGeometry(x, y, w, h)
        else:
            w, h = 520, 240
            x = screen_geo.x() + (screen_geo.width() - w) // 2
            y = screen_geo.y() + (screen_geo.height() - h) // 2
            self.setGeometry(x, y, w, h)

        # Конфигурация параметров оформления и работы
        self.current_mode = "inplace"  # По умолчанию режим "поверх текста" (inplace)
        self.src_lang = "auto"
        self.tgt_lang = "ru"
        self.is_paused = False
        self.is_locked_stealth = False  # Режим скрытия по кнопке глазика
        self.passthrough_enabled = True # Неосязаемая рамка (сквозные клики) ВКЛЮЧЕНА по умолчанию

        self.bg_opacity = 0.85
        self.bg_theme = "slate"
        self.hud_font_size = 0   # 0 = Авто
        self.scan_interval = 300
        self.smart_diff_enabled = True

        self.match_text_color = True    # Соответствие цвета слов
        self.match_font_family = True   # Соответствие шрифта и начертания

        self.translated_text = ""
        self.original_text = ""
        self.translated_blocks = []

        # Состояния мыши для ресайза в режиме настройки
        self.active_handle = self.HANDLE_NONE
        self.is_resizing = False
        self.is_moving_window = False
        self.drag_start_pos = QPoint()
        self.initial_geometry = QRect()

        # Создаем автономное окно HUD для субтитров (перемещается по всему экрану)
        self._setup_hud_ui()

        # Внешняя автономная панель управления (снаружи над рамкой)
        self.control_bar = TranslationControlBar(self)
        self.header_frame = self.control_bar  # Для обратной совместимости с тестами
        self.control_bar.sync_to_frame()
        self.control_bar.show()

        # Автономная кнопка разблокировки глазика
        self.unlock_pill = EyeUnlockPill(self)

        # Рабочий поток OCR и перевода
        self.worker = TranslationScannerWorker(self)
        self.worker.set_mode(self.current_mode)
        self.worker.translation_ready.connect(self._on_translation_ready)
        self.worker.update_geometry(self.geometry())
        self.worker.start()

        # Применяем неосязаемость рамки
        self.set_passthrough(self.passthrough_enabled)

    def _setup_hud_ui(self):
        self.hud_window = TranslationHudWindow(self)
        self.hud_frame = self.hud_window.hud_frame
        self.lbl_hud_text = self.hud_window.lbl_hud_text
        self._update_hud_style()
        self._update_hud_geometry()

    def _update_hud_geometry(self):
        if hasattr(self, "hud_window") and self.hud_window:
            self.hud_window.sync_to_frame()
            is_visible = (self.current_mode == "hud") and not self.is_paused and not self.is_locked_stealth
            self.hud_window.setVisible(is_visible)
            self.hud_frame.setVisible(is_visible)

    def _update_hud_style(self):
        if hasattr(self, "hud_window") and self.hud_window:
            self.hud_window.sync_style()

    def set_passthrough(self, enabled: bool):
        """
        Включает или выключает неосязаемость рамки (WS_EX_TRANSPARENT).
        При enabled=True клики мыши проходят сквозь рамку прямо в приложение или игру под ней.
        При enabled=False рамку можно изменять за границы и перемещать.
        """
        self.passthrough_enabled = enabled
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
                style |= 0x08000000  # WS_EX_NOACTIVATE: не забирать фокус у полноэкранных игр/видео
                if enabled:
                    ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00000020)  # WS_EX_TRANSPARENT
                else:
                    ctypes.windll.user32.SetWindowLongW(hwnd, -20, style & ~0x00000020)
                # SWP_NOSIZE (1) | SWP_NOMOVE (2) | SWP_NOZORDER (4) | SWP_NOACTIVATE (0x10) | SWP_FRAMECHANGED (0x20) = 0x37
                ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0037)
            except Exception:
                pass

        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.update_passthrough_ui()
        self.update()

    def set_stealth_lock(self, locked: bool):
        """
        Включает или выключает режим маскировки по кнопке «Глазик».
        В скрытом режиме тулбар скрыт, отображается только мини-глазик 16x16,
        а клики проходят сквозь рамку.
        """
        self.is_locked_stealth = locked
        if locked:
            if hasattr(self, "control_bar"):
                self.control_bar.hide()
            if hasattr(self, "hud_window"):
                self.hud_window.hide()
            self.unlock_pill.update_position()
            self.unlock_pill.update_state()
            self.unlock_pill.show()
            self.unlock_pill.raise_()
            self.set_passthrough(True)
        else:
            self.unlock_pill.hide()
            if hasattr(self, "control_bar"):
                self.control_bar.sync_to_frame()
                self.control_bar.show()
                self.control_bar.raise_()
            if hasattr(self, "hud_window"):
                self.hud_window.setVisible(self.current_mode == "hud" and not self.is_paused)
            self.set_passthrough(self.passthrough_enabled)
        self.update()

    def unfold_controls(self):
        """Разворачивает панель управления и снимает скрытие."""
        self.set_stealth_lock(False)

    def _set_languages(self, src: str, tgt: str):
        self.src_lang = src
        self.tgt_lang = tgt
        if hasattr(self, "control_bar"):
            self.control_bar._update_lang_button_text()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.set_languages(src, tgt)

    def _set_display_mode(self, mode: str):
        self.current_mode = mode
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.set_mode(mode)
        if hasattr(self, "control_bar"):
            self.control_bar._update_mode_button_text()
        self._update_hud_geometry()
        self.update()

    def _toggle_display_mode(self):
        new_mode = "inplace" if self.current_mode == "hud" else "hud"
        self._set_display_mode(new_mode)

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self.worker.set_paused(self.is_paused)
        if hasattr(self, "hud_window") and self.hud_window:
            self.hud_window.setVisible(self.current_mode == "hud" and not self.is_paused)
        self.hud_frame.setVisible(not self.is_paused if self.current_mode == "hud" else False)

        if hasattr(self, "control_bar") and hasattr(self.control_bar, "btn_pause"):
            if self.is_paused:
                self.control_bar.btn_pause.setIcon(create_themed_icon("play", is_dark=True, size=13))
                self.control_bar.btn_pause.setStyleSheet("background-color: #059669; border-color: #10b981; color: white;")
            else:
                self.control_bar.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=13))
                self.control_bar.btn_pause.setStyleSheet("")

        if hasattr(self, "unlock_pill") and self.unlock_pill:
            self.unlock_pill.update_state()

        self.update()

    def _copy_translation(self):
        txt = self.translated_text.strip()
        if txt:
            QApplication.clipboard().setText(txt)
            QToolTip.showText(QCursor.pos(), tr("trans_copied", "Перевод скопирован в буфер!"), self)

    def _toggle_match_color(self, checked: bool):
        self.match_text_color = checked
        self.update()

    def _toggle_match_font(self, checked: bool):
        self.match_font_family = checked
        self.update()

    def _set_opacity(self, val: float):
        self.bg_opacity = val
        self._update_hud_style()
        self.update()

    def _set_theme(self, theme: str):
        self.bg_theme = theme
        self._update_hud_style()
        self.update()

    def _set_font_size(self, sz: int):
        self.hud_font_size = sz
        self._update_hud_style()
        self.update()

    def _set_scan_interval(self, ms: int):
        self.scan_interval = ms
        if hasattr(self, "worker"):
            self.worker.set_interval(ms)

    def _toggle_smart_diff(self, checked: bool):
        self.smart_diff_enabled = checked
        if hasattr(self, "worker"):
            self.worker.set_smart_diff(checked)

    @pyqtSlot(str, str, list)
    def _on_translation_ready(self, original: str, translated: str, blocks: list):
        self.original_text = original
        self.translated_text = translated
        self.translated_blocks = blocks

        if translated:
            self.lbl_hud_text.setText(translated)
        else:
            self.lbl_hud_text.setText(tr("trans_no_text", "Текст не обнаружен"))
        self.update()

    # ------------------ Обработка перемещения и изменения размера ------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_hud_geometry()
        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.sync_to_frame()
        if hasattr(self, "unlock_pill") and self.unlock_pill.isVisible():
            self.unlock_pill.update_position()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.update_geometry(self.geometry())

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.sync_to_frame()
        if hasattr(self, "unlock_pill") and self.unlock_pill.isVisible():
            self.unlock_pill.update_position()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.update_geometry(self.geometry())

    def _hit_test(self, pos: QPoint) -> int:
        margin = self.HANDLE_SIZE
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()

        on_left = x <= margin
        on_right = x >= w - margin
        on_top = y <= margin
        on_bottom = y >= h - margin

        if on_top and on_left:
            return self.HANDLE_TL
        if on_top and on_right:
            return self.HANDLE_TR
        if on_bottom and on_left:
            return self.HANDLE_BL
        if on_bottom and on_right:
            return self.HANDLE_BR
        if on_top:
            return self.HANDLE_T
        if on_bottom:
            return self.HANDLE_B
        if on_left:
            return self.HANDLE_L
        if on_right:
            return self.HANDLE_R
        return self.HANDLE_NONE

    def _update_cursor(self, handle: int):
        if self.passthrough_enabled:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        cursors = {
            self.HANDLE_TL: Qt.CursorShape.SizeFDiagCursor,
            self.HANDLE_BR: Qt.CursorShape.SizeFDiagCursor,
            self.HANDLE_TR: Qt.CursorShape.SizeBDiagCursor,
            self.HANDLE_BL: Qt.CursorShape.SizeBDiagCursor,
            self.HANDLE_T: Qt.CursorShape.SizeVerCursor,
            self.HANDLE_B: Qt.CursorShape.SizeVerCursor,
            self.HANDLE_L: Qt.CursorShape.SizeHorCursor,
            self.HANDLE_R: Qt.CursorShape.SizeHorCursor,
            self.HANDLE_NONE: Qt.CursorShape.ArrowCursor
        }
        self.setCursor(cursors.get(handle, Qt.CursorShape.ArrowCursor))

    def mousePressEvent(self, event: QMouseEvent):
        if self.passthrough_enabled or self.is_locked_stealth:
            return

        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._hit_test(event.pos())
            if handle != self.HANDLE_NONE:
                self.is_resizing = True
                self.active_handle = handle
                self.drag_start_pos = event.globalPosition().toPoint()
                self.initial_geometry = self.geometry()
                event.accept()
                return

            self.is_moving_window = True
            self.drag_start_pos = event.globalPosition().toPoint()
            self.initial_geometry = self.geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.passthrough_enabled or self.is_locked_stealth:
            return

        if self.is_resizing:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            rect = QRect(self.initial_geometry)
            min_w, min_h = 240, 100

            if self.active_handle in (self.HANDLE_TL, self.HANDLE_L, self.HANDLE_BL):
                new_w = max(min_w, rect.width() - delta.x())
                rect.setLeft(rect.right() - new_w)
            elif self.active_handle in (self.HANDLE_TR, self.HANDLE_R, self.HANDLE_BR):
                rect.setWidth(max(min_w, rect.width() + delta.x()))

            if self.active_handle in (self.HANDLE_TL, self.HANDLE_T, self.HANDLE_TR):
                new_h = max(min_h, rect.height() - delta.y())
                rect.setTop(rect.bottom() - new_h)
            elif self.active_handle in (self.HANDLE_BL, self.HANDLE_B, self.HANDLE_BR):
                rect.setHeight(max(min_h, rect.height() + delta.y()))

            self.setGeometry(rect)
            event.accept()
            return

        if self.is_moving_window:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            self.move(self.initial_geometry.topLeft() + delta)
            event.accept()
            return

        handle = self._hit_test(event.pos())
        self._update_cursor(handle)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self.is_resizing = False
            self.is_moving_window = False
            self.active_handle = self.HANDLE_NONE
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if hasattr(self, "worker") and self.worker.isRunning():
                self.worker.update_geometry(self.geometry())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------ Отрисовка рамки и In-place текста ------------------

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()

        # 1. Отрисовка границы рамки
        if not self.is_locked_stealth:
            if not self.passthrough_enabled:
                # Режим настройки: яркая граница с маркерами ресайза
                pen = QPen(QColor(234, 179, 8, 230), 2.0, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.setBrush(QColor(15, 23, 42, 25))
                painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

                painter.setBrush(QColor(250, 204, 21))
                painter.setPen(Qt.PenStyle.NoPen)
                m = 6
                painter.drawRect(0, 0, m, m)
                painter.drawRect(w - m, 0, m, m)
                painter.drawRect(0, h - m, m, m)
                painter.drawRect(w - m, h - m, m, m)
            else:
                # Неосязаемый режим перевода: аккуратная стильная рамка
                pen = QPen(QColor(59, 130, 246, 200), 1.8, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.setBrush(QColor(15, 23, 42, 15))
                painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

                painter.setBrush(QColor(96, 165, 250))
                painter.setPen(Qt.PenStyle.NoPen)
                m = 6
                painter.drawRect(0, 0, m, m)
                painter.drawRect(w - m, 0, m, m)
                painter.drawRect(0, h - m, m, m)
                painter.drawRect(w - m, h - m, m, m)

        # 2. Режим In-place: отрисовка перевода прямо поверх текста
        if self.current_mode == "inplace" and self.translated_blocks and not self.is_paused:
            r, g, b = self.THEME_COLORS.get(self.bg_theme, (15, 23, 42))
            alpha = int(self.bg_opacity * 255)

            for item in self.translated_blocks:
                bx = float(item.get("x", 0))
                by = float(item.get("y", 0))
                bw = float(item.get("width", 50))
                bh = float(item.get("height", 20))
                txt = item.get("translated", "")
                if not txt:
                    continue

                lines_cnt = max(1, int(item.get("lines_count", 1)))
                line_h = float(item.get("line_height", bh / lines_cnt))

                if self.hud_font_size > 0:
                    base_pixel_size = self.hud_font_size
                else:
                    base_pixel_size = max(10, min(24, int(round(line_h * 0.70))))

                if self.match_font_family:
                    family = item.get("font_family", "Segoe UI")
                    is_bold = item.get("is_bold", True)
                    weight = QFont.Weight.Bold if is_bold else QFont.Weight.DemiBold
                else:
                    family = "Segoe UI"
                    weight = QFont.Weight.DemiBold

                max_avail_w = max(40.0, float(w - bx - 8))

                font = QFont(family, 10, weight)
                font.setPixelSize(base_pixel_size)
                fm = QFontMetrics(font)

                text_w = fm.horizontalAdvance(txt)
                if lines_cnt > 1:
                    preferred_w = min(max_avail_w, max(float(bw * 1.15), 100.0))
                else:
                    preferred_w = min(max_avail_w, max(float(bw), float(text_w + 12)))
                eff_w = min(max_avail_w, preferred_w)

                cur_pixel_size = base_pixel_size
                if lines_cnt == 1 and text_w > eff_w and self.hud_font_size == 0:
                    scale = eff_w / max(1.0, float(text_w))
                    cur_pixel_size = max(9, int(round(base_pixel_size * max(0.72, scale))))
                    font.setPixelSize(cur_pixel_size)
                    fm = QFontMetrics(font)

                calc_rect = fm.boundingRect(
                    QRect(0, 0, int(eff_w), 9999),
                    int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap),
                    txt
                )
                eff_h = max(bh, float(calc_rect.height() + 4))

                if by + eff_h > h - 4:
                    by = max(2.0, float(h - eff_h - 4))

                painter.setFont(font)

                pad_x = 4.0
                pad_y = 2.0
                bg_rect = QRectF(bx - pad_x, by - pad_y, eff_w + pad_x * 2, eff_h + pad_y * 2)

                if self.bg_opacity > 0.05:
                    painter.setBrush(QColor(r, g, b, alpha))
                    painter.setPen(QPen(QColor(59, 130, 246, min(200, alpha + 30)), 1.0))
                    painter.drawRoundedRect(bg_rect, 4.0, 4.0)

                if self.match_text_color and "color_rgb" in item:
                    cr, cg, cb = item["color_rgb"]
                    text_color = QColor(cr, cg, cb)
                else:
                    text_color = QColor(248, 250, 252)

                painter.setPen(text_color)
                painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, txt)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
            except Exception:
                pass
        self.set_passthrough(self.passthrough_enabled)
        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.sync_to_frame()
            self.control_bar.show()
            self.control_bar.raise_()
        self.raise_()

    def closeEvent(self, event):
        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.close()
        if hasattr(self, "hud_window") and self.hud_window:
            self.hud_window.close()
        if hasattr(self, "unlock_pill") and self.unlock_pill:
            self.unlock_pill.close()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.stop()
        self.closed.emit()
        self.frame_closed.emit()
        super().closeEvent(event)
