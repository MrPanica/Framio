# -*- coding: utf-8 -*-
"""
Плавающая интерактивная рамка живого перевода экрана в реальном времени (Live Screen Translator).
Позволяет свободно перемещать и масштабировать область на экране, непрерывно сканирует
текст под рамкой с помощью локального OCR и динамически отображает перевод:
1. В виде аккуратных субтитров (HUD) внизу рамки (в стиле игровых субтитров).
2. Либо прямо поверх оригинального текста (In-place замена) с точным соответствием размера шрифта.

Особенности эргономики:
- Полностью векторный интерфейс (Lucide / SVG), без эмодзи и смайликов.
- Плавное перемещение как за заголовок, так и за любую область рамки (через нативный Win32 Move).
- Игровой режим / Стелс: автоскрытие рамки и шапки, сквозной клик (WS_EX_TRANSPARENT) для игр.
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
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QComboBox,
    QApplication, QGraphicsDropShadowEffect, QToolTip
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QCursor, QPaintEvent, QMouseEvent,
    QPainterPath, QLinearGradient, QFontMetrics
)

from utils.screen_lock import safe_grab_screen_bgr, user32
from utils.ocr_helper import extract_text_and_blocks, extract_text_from_image
from utils.translator import translate_text, get_available_translation_languages
from utils.i18n import tr
from .icons import create_themed_icon, get_svg_pixmap


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
        self._mutex = QMutex()
        self._rect = QRect(100, 100, 480, 240)
        self._src_lang = "auto"
        self._tgt_lang = "ru"
        self._last_frame_small = None
        self._last_ocr_text = ""

    def update_geometry(self, rect: QRect):
        with QMutexLocker(self._mutex):
            self._rect = QRect(rect)
            self._last_frame_small = None  # Сбрасываем кэш кадра при смене положения

    def set_languages(self, src: str, tgt: str):
        with QMutexLocker(self._mutex):
            self._src_lang = src
            self._tgt_lang = tgt
            self._last_ocr_text = ""  # Принудительный повторный перевод

    def set_paused(self, paused: bool):
        with QMutexLocker(self._mutex):
            self._paused = paused

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

            if paused or r.width() < 30 or r.height() < 30:
                self.msleep(200)
                continue

            rx, ry, rw, rh = r.x(), r.y(), r.width(), r.height()
            frame_bgr = safe_grab_screen_bgr(rx, ry, rw, rh)

            if frame_bgr is None or frame_bgr.size == 0:
                self.msleep(200)
                continue

            # Smart Diff: уменьшаем кадр до 120x80 и проверяем изменение
            try:
                small_gray = cv2.cvtColor(cv2.resize(frame_bgr, (120, 80), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
            except Exception:
                small_gray = None

            if self._last_frame_small is not None and small_gray is not None:
                diff = cv2.absdiff(small_gray, self._last_frame_small)
                mean_diff = np.mean(diff)
                if mean_diff < 1.2:
                    # Кадр практически не изменился — пропускаем тяжёлый OCR/перевод
                    self.msleep(200)
                    continue

            self._last_frame_small = small_gray

            # Запуск OCR
            blocks = extract_text_and_blocks(frame_bgr, lang=src)
            full_text = " ".join(b["text"] for b in blocks if b.get("text")).strip()

            if not full_text:
                full_text, _ = extract_text_from_image(frame_bgr, lang=src)

            if not full_text:
                self.msleep(250)
                continue

            # Если распознанный текст совпадает с прошлым — не переводим повторно
            if full_text == self._last_ocr_text:
                self.msleep(200)
                continue

            self._last_ocr_text = full_text

            # Перевод общего текста
            translated_full = translate_text(full_text, source_lang=src, target_lang=tgt)

            # Перевод каждого блока для режима In-place
            translated_blocks = []
            for b in blocks:
                b_text = b.get("text", "").strip()
                if b_text:
                    b_tr = translate_text(b_text, source_lang=src, target_lang=tgt)
                    translated_blocks.append({
                        "original": b_text,
                        "translated": b_tr,
                        "x": b.get("x", 0),
                        "y": b.get("y", 0),
                        "width": b.get("width", 50),
                        "height": b.get("height", 20)
                    })

            self.translation_ready.emit(full_text, translated_full, translated_blocks)
            self.msleep(250)


class TranslationFrameWindow(QWidget):
    """
    Интерактивное окно рамки перевода.
    Поддерживает:
    - Свободное перетаскивание (через нативный Win32 Move или Qt-обработчики)
    - Изменение размеров по 8 направлениям
    - Режимы HUD-субтитров и In-place наложения поверх слов с пропорциональным кеглем шрифта
    - Игровой режим: автоскрытие шапки, сверхтонкая рамка, сквозной клик (WS_EX_TRANSPARENT)
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

    def __init__(self, initial_rect: QRect | None = None, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(320, 140)

        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)

        if initial_rect is not None and initial_rect.isValid() and not initial_rect.isEmpty():
            w = max(340, initial_rect.width())
            h = max(160, initial_rect.height())
            x = initial_rect.x()
            y = initial_rect.y()
            self.setGeometry(x, y, w, h)
        else:
            w, h = 540, 260
            x = screen_geo.x() + (screen_geo.width() - w) // 2
            y = screen_geo.y() + (screen_geo.height() - h) // 2
            self.setGeometry(x, y, w, h)

        self.current_mode = "hud"  # "hud" (субтитры) или "inplace" (поверх слов)
        self.is_paused = False
        self.is_stealth = False
        self.is_click_through = False
        self.is_hovered = False

        self.translated_text = ""
        self.original_text = ""
        self.translated_blocks = []

        # Состояния мыши для перемещения и ресайза
        self.active_handle = self.HANDLE_NONE
        self.is_resizing = False
        self.is_moving_window = False
        self.drag_start_pos = QPoint()
        self.initial_geometry = QRect()

        # Таймер автоскрытия шапки при неактивности
        self.stealth_timer = QTimer(self)
        self.stealth_timer.setSingleShot(True)
        self.stealth_timer.timeout.connect(self._on_stealth_timeout)

        self._setup_ui()

        # Рабочий поток
        self.worker = TranslationScannerWorker(self)
        self.worker.translation_ready.connect(self._on_translation_ready)
        self.worker.update_geometry(self.geometry())
        self.worker.start()

    def _setup_ui(self):
        # 1. Верхняя панель управления (Header Bar)
        self.header_frame = QFrame(self)
        self.header_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(18, 20, 28, 240);
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
                padding: 2px 6px;
            }
            QPushButton:hover {
                background-color: #2563eb;
                color: #ffffff;
                border-color: #60a5fa;
            }
            QComboBox {
                background-color: rgba(39, 39, 42, 210);
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                font-size: 11px;
                padding: 1px 4px;
            }
            QComboBox:hover {
                border-color: #60a5fa;
            }
        """)

        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)

        # Значок захвата / перетаскивания (Grip)
        self.drag_grip = QLabel(self)
        self.drag_grip.setPixmap(get_svg_pixmap("move", color="#94a3b8", size=14))
        self.drag_grip.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.drag_grip.setToolTip(tr("trans_drag_tooltip", "Зажмите ЛКМ для перемещения рамки"))
        header_layout.addWidget(self.drag_grip)

        # Векторная иконка перевода и заголовок (без эмодзи)
        self.lbl_icon = QLabel(self)
        self.lbl_icon.setPixmap(get_svg_pixmap("translate", color="#60a5fa", size=15))
        header_layout.addWidget(self.lbl_icon)

        self.lbl_title = QLabel(tr("trans_frame_title", "Live Перевод"))
        self.lbl_title.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        header_layout.addWidget(self.lbl_title)

        # Выбор языков
        self.combo_src = QComboBox()
        self.combo_src.addItem(tr("lang_auto", "Auto"), "auto")
        self.combo_src.addItem("EN", "en")
        self.combo_src.addItem("JA", "ja")
        self.combo_src.addItem("ZH", "zh-CN")
        self.combo_src.addItem("DE", "de")
        self.combo_src.addItem("FR", "fr")
        self.combo_src.currentIndexChanged.connect(self._on_language_changed)
        header_layout.addWidget(self.combo_src)

        lbl_arrow = QLabel("→")
        lbl_arrow.setStyleSheet("color: #60a5fa; font-weight: bold; font-size: 13px;")
        header_layout.addWidget(lbl_arrow)

        self.combo_tgt = QComboBox()
        self.combo_tgt.addItem("RU (Русский)", "ru")
        self.combo_tgt.addItem("EN (English)", "en")
        self.combo_tgt.addItem("DE (Deutsch)", "de")
        self.combo_tgt.currentIndexChanged.connect(self._on_language_changed)
        header_layout.addWidget(self.combo_tgt)

        # Переключатель режима отображения: HUD (субтитры) vs In-place (без эмодзи)
        self.btn_toggle_mode = QPushButton(tr("trans_mode_hud", "Субтитры"))
        self.btn_toggle_mode.setIcon(create_themed_icon("text", is_dark=True, size=13))
        self.btn_toggle_mode.setToolTip(tr("trans_mode_tooltip", "Переключить режим: Субтитры внизу / Текст поверх экрана"))
        self.btn_toggle_mode.clicked.connect(self._toggle_display_mode)
        header_layout.addWidget(self.btn_toggle_mode)

        # Пауза / Возобновление
        self.btn_pause = QPushButton()
        self.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=13))
        self.btn_pause.setToolTip(tr("trans_pause_tooltip", "Приостановить / возобновить сканирование"))
        self.btn_pause.setFixedSize(26, 24)
        self.btn_pause.clicked.connect(self._toggle_pause)
        header_layout.addWidget(self.btn_pause)

        # Копировать перевод
        self.btn_copy = QPushButton()
        self.btn_copy.setIcon(create_themed_icon("copy", is_dark=True, size=13))
        self.btn_copy.setToolTip(tr("trans_copy_tooltip", "Скопировать текущий перевод в буфер"))
        self.btn_copy.setFixedSize(26, 24)
        self.btn_copy.clicked.connect(self._copy_translation)
        header_layout.addWidget(self.btn_copy)

        # Сквозной клик (Игровой режим: клики мыши проходят в игру)
        self.btn_passthrough = QPushButton()
        self.btn_passthrough.setIcon(create_themed_icon("passthrough", is_dark=True, size=13))
        self.btn_passthrough.setToolTip(tr("trans_passthrough_tooltip", "Сквозной клик для игр (клики мыши проходят в игру). Возврат: Ctrl+Shift+T"))
        self.btn_passthrough.setFixedSize(26, 24)
        self.btn_passthrough.clicked.connect(self._toggle_click_through)
        header_layout.addWidget(self.btn_passthrough)

        # Свернуть панель / Стелс
        self.btn_stealth = QPushButton()
        self.btn_stealth.setIcon(create_themed_icon("eye_off", is_dark=True, size=13))
        self.btn_stealth.setToolTip(tr("trans_stealth_tooltip", "Свернуть панель (Игровой режим)"))
        self.btn_stealth.setFixedSize(26, 24)
        self.btn_stealth.clicked.connect(self.fold_controls)
        header_layout.addWidget(self.btn_stealth)

        # Закрыть
        self.btn_close = QPushButton()
        self.btn_close.setIcon(create_themed_icon("close", is_dark=True, size=13))
        self.btn_close.setToolTip(tr("trans_close_tooltip", "Закрыть рамку перевода"))
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setStyleSheet("QPushButton:hover { background-color: #ef4444; border-color: #f87171; }")
        self.btn_close.clicked.connect(self.close)
        header_layout.addWidget(self.btn_close)

        # 2. Мини-кнопка разворачивания настроек (появляется в углу, когда шапка скрыта)
        self.btn_unfold = QPushButton(self)
        self.btn_unfold.setIcon(create_themed_icon("settings", is_dark=True, size=12))
        self.btn_unfold.setToolTip(tr("trans_unfold_tooltip", "Развернуть панель управления [Ctrl+Shift+T]"))
        self.btn_unfold.setFixedSize(26, 22)
        self.btn_unfold.setStyleSheet("""
            QPushButton {
                background-color: rgba(18, 20, 28, 170);
                border: 1px solid rgba(255, 255, 255, 40);
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #2563eb;
                border-color: #60a5fa;
            }
        """)
        self.btn_unfold.clicked.connect(self.unfold_controls)
        self.btn_unfold.hide()

        # 3. Нижняя плашка HUD для субтитров (высокое качество, игровой стиль)
        self.hud_frame = QFrame(self)
        self.hud_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(10, 12, 18, 235);
                border: 1px solid rgba(59, 130, 246, 130);
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                font-weight: 500;
                line-height: 1.35;
            }
        """)
        hud_layout = QVBoxLayout(self.hud_frame)
        hud_layout.setContentsMargins(10, 8, 10, 8)
        self.lbl_hud_text = QLabel(tr("trans_waiting_text", "Ожидание текста в рамке..."))
        self.lbl_hud_text.setWordWrap(True)
        self.lbl_hud_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hud_layout.addWidget(self.lbl_hud_text)

        # Установка фильтра событий на шапку для перемещения
        for w in (self.header_frame, self.lbl_title, self.lbl_icon, self.drag_grip):
            w.installEventFilter(self)

        self._update_layout_positions()

    def _update_layout_positions(self):
        w, h = self.width(), self.height()
        hdr_h = 34
        self.header_frame.setGeometry(6, 6, max(100, w - 12), hdr_h)
        self.btn_unfold.setGeometry(w - 32, 6, 26, 22)

        hud_h = max(42, min(110, int(h * 0.35)))
        self.hud_frame.setGeometry(8, max(hdr_h + 10, h - hud_h - 8), max(100, w - 16), hud_h)
        self.hud_frame.setVisible(self.current_mode == "hud")

    def fold_controls(self):
        """Сворачивает шапку в компактный режим (Стелс / В игре)."""
        self.is_stealth = True
        self.header_frame.hide()
        self.btn_unfold.show()
        self.update()

    def unfold_controls(self):
        """Разворачивает полную панель управления."""
        self.is_stealth = False
        self.btn_unfold.hide()
        self.header_frame.show()
        self.update()

    def _on_stealth_timeout(self):
        if not self.is_hovered and not self.is_moving_window and not self.is_resizing:
            self.fold_controls()

    def set_click_through(self, enabled: bool):
        """Включает или выключает сквозной клик (WS_EX_TRANSPARENT) для игр."""
        self.is_click_through = enabled
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = int(self.winId())
                style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
                if enabled:
                    ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00000020)  # WS_EX_TRANSPARENT
                else:
                    ctypes.windll.user32.SetWindowLongW(hwnd, -20, style & ~0x00000020)
            except Exception as e:
                print("Set click-through error:", e)

        if enabled:
            self.fold_controls()
            QToolTip.showText(QCursor.pos(), tr("trans_passthrough_active", "Сквозной клик активен. Нажмите Ctrl+Shift+T для возврата панели."), self)
        else:
            self.unfold_controls()
        self.update()

    def _toggle_click_through(self):
        self.set_click_through(not self.is_click_through)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_layout_positions()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.update_geometry(self.geometry())

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.update_geometry(self.geometry())

    def enterEvent(self, event):
        super().enterEvent(event)
        self.is_hovered = True
        self.stealth_timer.stop()
        if not self.is_click_through:
            self.unfold_controls()
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.is_hovered = False
        if not self.is_click_through:
            self.stealth_timer.start(3000)
        self.update()

    def _on_language_changed(self):
        src = self.combo_src.currentData() or "auto"
        tgt = self.combo_tgt.currentData() or "ru"
        self.worker.set_languages(src, tgt)

    def _toggle_display_mode(self):
        if self.current_mode == "hud":
            self.current_mode = "inplace"
            self.btn_toggle_mode.setText(tr("trans_mode_inplace", "Поверх текста"))
            self.btn_toggle_mode.setIcon(create_themed_icon("scan_text", is_dark=True, size=13))
        else:
            self.current_mode = "hud"
            self.btn_toggle_mode.setText(tr("trans_mode_hud", "Субтитры"))
            self.btn_toggle_mode.setIcon(create_themed_icon("text", is_dark=True, size=13))
        self._update_layout_positions()
        self.update()

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self.worker.set_paused(self.is_paused)
        if self.is_paused:
            self.btn_pause.setIcon(create_themed_icon("play", is_dark=True, size=13))
            self.btn_pause.setStyleSheet("background-color: #059669; border-color: #10b981; color: white;")
        else:
            self.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=13))
            self.btn_pause.setStyleSheet("")

    def _copy_translation(self):
        txt = self.translated_text.strip()
        if txt:
            QApplication.clipboard().setText(txt)
            QToolTip.showText(QCursor.pos(), tr("trans_copied", "Перевод скопирован в буфер!"), self)

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

    def _start_system_move(self) -> bool:
        """Инициирует нативное аппаратное перемещение окна Windows (Aero Snap, multi-monitor, 0 lag)."""
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.ReleaseCapture()
                ctypes.windll.user32.SendMessageW(int(self.winId()), 0x00A1, 0x02, 0)  # WM_NCLBUTTONDOWN, HTCAPTION
                return True
            except Exception:
                pass
        return False

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
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._hit_test(event.pos())
            if handle != self.HANDLE_NONE:
                self.is_resizing = True
                self.active_handle = handle
                self.drag_start_pos = event.globalPosition().toPoint()
                self.initial_geometry = self.geometry()
                event.accept()
                return

            # Клик по свободной области рамки сразу начинает перемещение
            if self._start_system_move():
                event.accept()
                return

            self.is_moving_window = True
            self.drag_start_pos = event.globalPosition().toPoint()
            self.initial_geometry = self.geometry()
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.is_resizing:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            rect = QRect(self.initial_geometry)
            min_w, min_h = 240, 140

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
            if hasattr(self, "worker") and self.worker.isRunning():
                self.worker.update_geometry(self.geometry())
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
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def eventFilter(self, watched, event):
        if watched in (self.header_frame, self.lbl_title, self.lbl_icon, self.drag_grip):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if self._start_system_move():
                    return True
                self.is_moving_window = True
                self.drag_start_pos = event.globalPosition().toPoint()
                self.initial_geometry = self.geometry()
                return True
            elif event.type() == QEvent.Type.MouseMove and self.is_moving_window:
                delta = event.globalPosition().toPoint() - self.drag_start_pos
                self.move(self.initial_geometry.topLeft() + delta)
                return True
            elif event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.is_moving_window = False
                return True
        return super().eventFilter(watched, event)

    # ------------------ Отрисовка рамки и In-place текста ------------------

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        w, h = self.width(), self.height()

        # 1. Отрисовка границ рамки в зависимости от режима (активен / стелс)
        if self.is_hovered or not self.is_stealth:
            pen = QPen(QColor(59, 130, 246, 210), 1.8, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QColor(15, 23, 42, 20))  # Легчайшее затемнение центра
            painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

            # Маркеры по углам
            painter.setBrush(QColor(96, 165, 250))
            painter.setPen(Qt.PenStyle.NoPen)
            m = 6
            painter.drawRect(0, 0, m, m)
            painter.drawRect(w - m, 0, m, m)
            painter.drawRect(0, h - m, m, m)
            painter.drawRect(w - m, h - m, m, m)
        else:
            # Стелс / В игре: ультра-тонкая ненавязчивая пунктирная или полупрозрачная линия
            pen = QPen(QColor(59, 130, 246, 35), 1.0, Qt.PenStyle.SolidLine)
            painter.setPen(pen)
            painter.setBrush(QColor(0, 0, 0, 1))  # Прозрачный центр (улавливает события)
            painter.drawRoundedRect(1, 1, w - 2, h - 2, 4, 4)

        # 2. Режим In-place: отрисовка перевода прямо поверх оригинального текста с адаптивным кеглем
        if self.current_mode == "inplace" and self.translated_blocks:
            base_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)

            for b in self.translated_blocks:
                bx = float(b.get("x", 0))
                by = float(b.get("y", 0))
                bw = float(b.get("width", 50))
                bh = float(b.get("height", 20))
                txt = b.get("translated", "")
                if not txt:
                    continue

                # Вычисляем размер шрифта строго пропорционально высоте оригинального блока текста
                target_pixel_size = max(10, int(round(bh * 0.72)))
                font = QFont(base_font)
                font.setPixelSize(target_pixel_size)
                fm = QFontMetrics(font)

                text_w = fm.horizontalAdvance(txt)
                # Даём блоку ширины запас под перевод (русский часто длиннее английского)
                eff_w = max(bw, float(text_w + 12))
                max_avail_w = max(35.0, float(w - bx - 8))
                if eff_w > max_avail_w:
                    eff_w = max_avail_w
                    if text_w > eff_w:
                        scale_factor = eff_w / max(1.0, float(text_w))
                        adj_size = max(9, int(round(target_pixel_size * max(0.70, scale_factor))))
                        font.setPixelSize(adj_size)
                        fm = QFontMetrics(font)

                painter.setFont(font)

                # Подложка под переведённый текст (в стиле аккуратных игровых плашек)
                pad_x = 4.0
                pad_y = 2.0
                bg_rect = QRectF(bx - pad_x, by - pad_y, eff_w + pad_x * 2, bh + pad_y * 2)

                painter.setBrush(QColor(10, 14, 23, 235))
                painter.setPen(QPen(QColor(59, 130, 246, 120), 1.0))
                painter.drawRoundedRect(bg_rect, 4.0, 4.0)

                # Текст перевода: контрастный светлый с четким рендерингом
                painter.setPen(QColor(248, 250, 252))
                painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, txt)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.stop()
        self.closed.emit()
        self.frame_closed.emit()
        super().closeEvent(event)
