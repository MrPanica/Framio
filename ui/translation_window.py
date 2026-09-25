# -*- coding: utf-8 -*-
"""
Плавающая интерактивная рамка живого перевода экрана в реальном времени (Live Screen Translator).
Позволяет свободно перемещать и масштабировать область на экране, непрерывно сканирует
текст под рамкой с помощью локального OCR и динамически отображает перевод:
1. В виде аккуратных субтитров (HUD) внизу рамки.
2. Либо прямо поверх оригинального текста (In-place замена).
Аппаратно исключена из захвата (WDA_EXCLUDEFROMCAPTURE), не грузит CPU на статичных кадрах (Smart Diff).
"""

from __future__ import annotations
import sys
import time
import numpy as np

from PyQt6.QtCore import (
    Qt, QRect, QRectF, QPoint, QPointF, QSize, QThread, pyqtSignal, pyqtSlot, QMutex, QMutexLocker, QTimer
)
from PyQt6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QComboBox,
    QApplication, QGraphicsDropShadowEffect, QToolTip
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QCursor, QPaintEvent, QMouseEvent,
    QPainterPath, QLinearGradient
)

from utils.screen_lock import safe_grab_screen_bgr, user32
from utils.ocr_helper import extract_text_and_blocks, extract_text_from_image
from utils.translator import translate_text, get_available_translation_languages
from utils.i18n import tr
from .icons import create_themed_icon


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
    Поддерживает перетаскивание, изменение размеров по 8 направлениям,
    режимы HUD-субтитров и In-place наложения поверх слов.
    """
    closed = pyqtSignal()

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

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass

        screen = QApplication.primaryScreen()
        screen_geo = screen.geometry() if screen else QRect(0, 0, 1920, 1080)

        if initial_rect is not None and initial_rect.isValid() and not initial_rect.isEmpty():
            self.setGeometry(initial_rect)
        else:
            w, h = 540, 260
            x = screen_geo.x() + (screen_geo.width() - w) // 2
            y = screen_geo.y() + (screen_geo.height() - h) // 2
            self.setGeometry(x, y, w, h)

        self.current_mode = "hud"  # "hud" (субтитры) или "inplace" (поверх слов)
        self.is_paused = False
        self.translated_text = ""
        self.original_text = ""
        self.translated_blocks = []

        # Состояния мыши для перемещения и ресайза
        self.active_handle = self.HANDLE_NONE
        self.is_resizing = False
        self.is_moving_window = False
        self.drag_start_pos = QPoint()
        self.initial_geometry = QRect()

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
                background-color: rgba(18, 18, 22, 230);
                border: 1px solid #3b82f6;
                border-radius: 6px;
            }
            QLabel {
                color: #f4f4f5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton {
                background-color: rgba(39, 39, 42, 200);
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                padding: 2px 6px;
            }
            QPushButton:hover {
                background-color: #3b82f6;
                color: #ffffff;
                border-color: #60a5fa;
            }
            QComboBox {
                background-color: rgba(39, 39, 42, 200);
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                font-size: 11px;
                padding: 1px 4px;
            }
        """)

        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)

        # Значок и заголовок
        self.lbl_title = QLabel("🌐 " + tr("trans_frame_title", "Live Перевод"))
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

        lbl_arrow = QLabel("➔")
        lbl_arrow.setStyleSheet("color: #60a5fa; font-weight: bold;")
        header_layout.addWidget(lbl_arrow)

        self.combo_tgt = QComboBox()
        self.combo_tgt.addItem("RU (Русский)", "ru")
        self.combo_tgt.addItem("EN (English)", "en")
        self.combo_tgt.addItem("DE (Deutsch)", "de")
        self.combo_tgt.currentIndexChanged.connect(self._on_language_changed)
        header_layout.addWidget(self.combo_tgt)

        # Переключатель режима отображения: HUD (субтитры) vs In-place
        self.btn_toggle_mode = QPushButton("📄 " + tr("trans_mode_hud", "Субтитры"))
        self.btn_toggle_mode.setToolTip(tr("trans_mode_tooltip", "Переключить режим: Субтитры внизу / Текст поверх экрана"))
        self.btn_toggle_mode.clicked.connect(self._toggle_display_mode)
        header_layout.addWidget(self.btn_toggle_mode)

        # Пауза / Пуск
        self.btn_pause = QPushButton("⏸")
        self.btn_pause.setToolTip(tr("trans_pause_tooltip", "Приостановить / возобновить сканирование"))
        self.btn_pause.setFixedWidth(26)
        self.btn_pause.clicked.connect(self._toggle_pause)
        header_layout.addWidget(self.btn_pause)

        # Копировать перевод
        self.btn_copy = QPushButton("📋")
        self.btn_copy.setToolTip(tr("trans_copy_tooltip", "Скопировать текущий перевод в буфер обмена"))
        self.btn_copy.setFixedWidth(26)
        self.btn_copy.clicked.connect(self._copy_translation)
        header_layout.addWidget(self.btn_copy)

        # Закрыть
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedWidth(24)
        self.btn_close.setStyleSheet("QPushButton:hover { background-color: #ef4444; border-color: #f87171; }")
        self.btn_close.clicked.connect(self.close)
        header_layout.addWidget(self.btn_close)

        # 2. Нижняя плашка HUD для субтитров
        self.hud_frame = QFrame(self)
        self.hud_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(15, 17, 23, 235);
                border: 1px solid rgba(59, 130, 246, 180);
                border-radius: 8px;
            }
            QLabel {
                color: #ffffff;
                font-family: 'Segoe UI', sans-serif;
                font-size: 13px;
                font-weight: 500;
                line-height: 1.3;
            }
        """)
        hud_layout = QVBoxLayout(self.hud_frame)
        hud_layout.setContentsMargins(10, 8, 10, 8)
        self.lbl_hud_text = QLabel(tr("trans_waiting_text", "Ожидание текста в рамке..."))
        self.lbl_hud_text.setWordWrap(True)
        self.lbl_hud_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hud_layout.addWidget(self.lbl_hud_text)

        self._update_layout_positions()

    def _update_layout_positions(self):
        w, h = self.width(), self.height()
        # Шапка сверху
        hdr_h = 34
        self.header_frame.setGeometry(6, 6, max(100, w - 12), hdr_h)

        # Плашка субтитров снизу
        hud_h = max(42, min(100, int(h * 0.35)))
        self.hud_frame.setGeometry(8, max(hdr_h + 10, h - hud_h - 8), max(100, w - 16), hud_h)
        self.hud_frame.setVisible(self.current_mode == "hud")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_layout_positions()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.update_geometry(self.geometry())

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.update_geometry(self.geometry())

    def _on_language_changed(self):
        src = self.combo_src.currentData() or "auto"
        tgt = self.combo_tgt.currentData() or "ru"
        self.worker.set_languages(src, tgt)

    def _toggle_display_mode(self):
        if self.current_mode == "hud":
            self.current_mode = "inplace"
            self.btn_toggle_mode.setText("🔤 " + tr("trans_mode_inplace", "Поверх текста"))
        else:
            self.current_mode = "hud"
            self.btn_toggle_mode.setText("📄 " + tr("trans_mode_hud", "Субтитры"))
        self._update_layout_positions()
        self.update()

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self.worker.set_paused(self.is_paused)
        if self.is_paused:
            self.btn_pause.setText("▶")
            self.btn_pause.setStyleSheet("background-color: #10b981; color: white;")
        else:
            self.btn_pause.setText("⏸")
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

            # Если клик в свободной области шапки — перемещаем окно
            if self.header_frame.geometry().contains(event.pos()):
                child = self.header_frame.childAt(event.pos() - self.header_frame.pos())
                if child in (None, self.header_frame, self.lbl_title):
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

    # ------------------ Отрисовка рамки и In-place текста ------------------

    def paintEvent(self, event: QPaintEvent):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # 1. Тонкий полупрозрачный фон и синяя рамка с градиентом
        pen = QPen(QColor(59, 130, 246, 220), 2.0, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(QColor(15, 23, 42, 18))  # Легчайшее затемнение центра
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # 2. Маркеры по углам
        painter.setBrush(QColor(96, 165, 250))
        painter.setPen(Qt.PenStyle.NoPen)
        m = 7
        painter.drawRect(0, 0, m, m)
        painter.drawRect(w - m, 0, m, m)
        painter.drawRect(0, h - m, m, m)
        painter.drawRect(w - m, h - m, m, m)

        # 3. Режим In-place: отрисовка перевода прямо поверх текста
        if self.current_mode == "inplace" and self.translated_blocks:
            painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
            for b in self.translated_blocks:
                bx = float(b.get("x", 0))
                by = float(b.get("y", 0))
                bw = float(b.get("width", 50))
                bh = float(b.get("height", 20))
                txt = b.get("translated", "")
                if not txt:
                    continue

                # Подложка под переведённый текст
                pad = 3.0
                bg_rect = QRectF(bx - pad, by - pad, bw + pad * 2, bh + pad * 2)
                painter.setBrush(QColor(15, 23, 42, 235))
                painter.setPen(QPen(QColor(59, 130, 246, 180), 1.0))
                painter.drawRoundedRect(bg_rect, 4.0, 4.0)

                # Текст перевода
                painter.setPen(QColor(248, 250, 252))
                painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, txt)

    def closeEvent(self, event):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.stop()
        self.closed.emit()
        super().closeEvent(event)
