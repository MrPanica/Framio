# -*- coding: utf-8 -*-
"""
Плавающая интерактивная рамка живого перевода экрана в реальном времени (Live Screen Translator).
Позволяет свободно перемещать и масштабировать область на экране, непрерывно сканирует
текст под рамкой с помощью локального OCR и динамически отображает перевод:
1. В виде аккуратных субтитров (HUD) внизу рамки (в стиле игровых субтитров).
2. Либо прямо поверх оригинального текста (In-place замена) с точным соответствием размера шрифта.

Особенности эргономики:
- Полностью векторный интерфейс (Lucide / SVG), без эмодзи и смайликов.
- Компактные кнопки настроек с выпадающими списками (Язык, Режим, Настройки оформления).
- Режим полной маскировки (Глазик): скрывает всю рамку и шапку, оставляя лишь крошечную
  иконку глазика. Клики и наведение мыши проходят сквозь рамку прямо в игру (WM_NCHITTEST -> HTTRANSPARENT),
  пока снова не будет нажат глазик.
- Настройки прозрачности фона, стиля подложки, размера шрифта и частоты сканирования.
- Плавное перемещение как за заголовок, так и за любую область рамки (Win32 SendMessageW).
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
from utils.translator import translate_text, get_available_translation_languages
from utils.i18n import tr
from .icons import create_themed_icon, get_svg_pixmap


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


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
                        # Кадр не изменился — пропускаем тяжёлый OCR/перевод
                        self.msleep(interval)
                        continue

                self._last_frame_small = small_gray

            # Запуск OCR
            blocks = extract_text_and_blocks(frame_bgr, lang=src)
            full_text = " ".join(b["text"] for b in blocks if b.get("text")).strip()

            if not full_text:
                full_text, _ = extract_text_from_image(frame_bgr, lang=src)

            if not full_text:
                self.msleep(interval)
                continue

            # Если распознанный текст совпадает с прошлым — не переводим повторно
            if full_text == self._last_ocr_text:
                self.msleep(interval)
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
            self.msleep(interval)


class TranslationFrameWindow(QWidget):
    """
    Интерактивное окно рамки живого перевода.
    Поддерживает:
    - Компактный заголовок с объединенными кнопками настроек (Язык, Режим, Настройки оформления)
    - Режим скрытия и сквозного клика по кнопке «Глазик» (остается только иконка глазика)
    - Плавное перемещение и изменение размера
    - Динамический расчет размера шрифта для In-place наложения
    - Пользовательские параметры фона и прозрачности
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

        # Конфигурация параметров оформления и работы
        self.current_mode = "hud"  # "hud" (субтитры) или "inplace" (поверх слов)
        self.src_lang = "auto"
        self.tgt_lang = "ru"
        self.is_paused = False
        self.is_locked_stealth = False  # Режим блокировки и скрытия (по кнопке глазика)
        self.is_hovered = False

        self.bg_opacity = 0.85  # 85% по умолчанию
        self.bg_theme = "slate"  # "slate", "oled", "cyber"
        self.hud_font_size = 0   # 0 = Авто, иначе 11, 14, 18, 22
        self.scan_interval = 300
        self.smart_diff_enabled = True

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

        header_layout = QHBoxLayout(self.header_frame)
        header_layout.setContentsMargins(6, 4, 6, 4)
        header_layout.setSpacing(6)

        # Объединенный чип заголовка и ручки перемещения (Title & Grip)
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
        header_layout.addWidget(self.title_container)

        # Кнопка выбора языков (Компактная кнопка с выпадающим меню)
        self.btn_lang = QPushButton()
        self._update_lang_button_text()
        self.btn_lang.setToolTip(tr("trans_lang_tooltip", "Нажмите для выбора языков перевода"))
        self.btn_lang.clicked.connect(self._show_lang_menu)
        header_layout.addWidget(self.btn_lang)

        # Кнопка выбора режима (Субтитры / Поверх текста)
        self.btn_mode = QPushButton()
        self._update_mode_button_text()
        self.btn_mode.setToolTip(tr("trans_mode_tooltip", "Нажмите для переключения режима отображения"))
        self.btn_mode.clicked.connect(self._show_mode_menu)
        header_layout.addWidget(self.btn_mode)

        # Кнопка расширенных настроек (Фон, Прозрачность, Шрифт, Скорость)
        self.btn_settings = QPushButton(tr("trans_settings_btn", "Настройки ▾"))
        self.btn_settings.setIcon(create_themed_icon("settings", is_dark=True, size=13))
        self.btn_settings.setToolTip(tr("trans_settings_tooltip", "Настройки прозрачности, темы и оптимизации"))
        self.btn_settings.clicked.connect(self._show_settings_menu)
        header_layout.addWidget(self.btn_settings)

        # Пауза / Пуск
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

        # Кнопка «Глазик» — переход в скрытый режим
        self.btn_eye = QPushButton()
        self.btn_eye.setIcon(create_themed_icon("eye", is_dark=True, size=14, custom_color="#38bdf8"))
        self.btn_eye.setToolTip(tr("trans_eye_tooltip", "Скрыть рамку и панель (оставить только значок глазика). Клики мыши будут проходить сквозь рамку."))
        self.btn_eye.setFixedSize(26, 24)
        self.btn_eye.setStyleSheet("QPushButton { border-color: rgba(56, 189, 248, 140); } QPushButton:hover { background-color: #0284c7; }")
        self.btn_eye.clicked.connect(lambda: self.set_stealth_lock(True))
        header_layout.addWidget(self.btn_eye)

        # Закрыть
        self.btn_close = QPushButton()
        self.btn_close.setIcon(create_themed_icon("close", is_dark=True, size=13))
        self.btn_close.setToolTip(tr("trans_close_tooltip", "Закрыть рамку перевода"))
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setStyleSheet("QPushButton:hover { background-color: #ef4444; border-color: #f87171; }")
        self.btn_close.clicked.connect(self.close)
        header_layout.addWidget(self.btn_close)

        # 2. Плавающая иконка разблокировки глазика (появляется только в режиме маскировки)
        self.btn_eye_unlock = QPushButton(self)
        self.btn_eye_unlock.setIcon(create_themed_icon("eye", is_dark=True, size=14, custom_color="#38bdf8"))
        self.btn_eye_unlock.setToolTip(tr("trans_unlock_tooltip", "Нажмите на глазик, чтобы вернуть настройки"))
        self.btn_eye_unlock.setFixedSize(26, 26)
        self.btn_eye_unlock.setStyleSheet("""
            QPushButton {
                background-color: rgba(15, 23, 42, 235);
                border: 1.5px solid #38bdf8;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0284c7;
                border-color: #7dd3fc;
            }
        """)
        self.btn_eye_unlock.clicked.connect(lambda: self.set_stealth_lock(False))
        self.btn_eye_unlock.hide()

        # 3. Нижняя плашка HUD для субтитров
        self.hud_frame = QFrame(self)
        hud_layout = QVBoxLayout(self.hud_frame)
        hud_layout.setContentsMargins(10, 8, 10, 8)
        self.lbl_hud_text = QLabel(tr("trans_waiting_text", "Ожидание текста в рамке..."))
        self.lbl_hud_text.setWordWrap(True)
        self.lbl_hud_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hud_layout.addWidget(self.lbl_hud_text)

        self._update_hud_style()

        # Назначаем фильтр событий для перемещения рамки
        for w in (self.header_frame, self.title_container, self.lbl_title, self.lbl_icon, self.drag_grip):
            w.installEventFilter(self)

        self._update_layout_positions()

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

    def _update_lang_button_text(self):
        s = self.src_lang.upper() if self.src_lang != "auto" else tr("lang_auto", "Auto")
        t = self.tgt_lang.upper()
        self.btn_lang.setText(tr("trans_lang_btn", "Язык: {src} → {tgt} ▾").format(src=s, tgt=t))

    def _update_mode_button_text(self):
        if self.current_mode == "inplace":
            self.btn_mode.setText(tr("trans_mode_btn", "Режим: {mode} ▾").format(mode=tr("trans_mode_inplace", "Поверх текста")))
            self.btn_mode.setIcon(create_themed_icon("scan_text", is_dark=True, size=13))
        else:
            self.btn_mode.setText(tr("trans_mode_btn", "Режим: {mode} ▾").format(mode=tr("trans_mode_hud", "Субтитры")))
            self.btn_mode.setIcon(create_themed_icon("text", is_dark=True, size=13))

    def _update_hud_style(self):
        r, g, b = self.THEME_COLORS.get(self.bg_theme, (15, 23, 42))
        alpha = int(self.bg_opacity * 255)
        font_px = self.hud_font_size if self.hud_font_size > 0 else 13

        if self.bg_opacity <= 0.05:
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

    def _show_lang_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        # Популярные пары
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
            act.triggered.connect(lambda ch, src=s, tgt=t: self._set_languages(src, tgt))

        menu.addSeparator()

        # Подменю исходного языка
        src_menu = menu.addMenu(tr("trans_menu_src", "Исходный язык"))
        src_menu.setStyleSheet(self._menu_stylesheet())
        all_src = [("auto", "Auto"), ("en", "English"), ("ja", "Japanese"), ("zh-CN", "Chinese"),
                   ("de", "German"), ("fr", "French"), ("es", "Spanish"), ("ko", "Korean"), ("ru", "Русский")]
        for tag, name in all_src:
            act = src_menu.addAction(name)
            act.triggered.connect(lambda ch, s=tag: self._set_languages(s, self.tgt_lang))

        # Подменю языка перевода
        tgt_menu = menu.addMenu(tr("trans_menu_tgt", "Язык перевода"))
        tgt_menu.setStyleSheet(self._menu_stylesheet())
        all_tgt = [("ru", "Русский"), ("en", "English"), ("de", "Deutsch"), ("fr", "Français"),
                   ("es", "Español"), ("zh-CN", "中文"), ("ja", "日本語")]
        for tag, name in all_tgt:
            act = tgt_menu.addAction(name)
            act.triggered.connect(lambda ch, t=tag: self._set_languages(self.src_lang, t))

        menu.exec(self.btn_lang.mapToGlobal(QPoint(0, self.btn_lang.height() + 2)))

    def _set_languages(self, src: str, tgt: str):
        self.src_lang = src
        self.tgt_lang = tgt
        self._update_lang_button_text()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.set_languages(src, tgt)

    def _show_mode_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        act_hud = menu.addAction(tr("trans_mode_hud", "Субтитры (HUD внизу)"))
        act_hud.setIcon(create_themed_icon("text", is_dark=True, size=13))
        act_hud.triggered.connect(lambda: self._set_display_mode("hud"))

        act_inplace = menu.addAction(tr("trans_mode_inplace", "Поверх текста (In-place)"))
        act_inplace.setIcon(create_themed_icon("scan_text", is_dark=True, size=13))
        act_inplace.triggered.connect(lambda: self._set_display_mode("inplace"))

        menu.exec(self.btn_mode.mapToGlobal(QPoint(0, self.btn_mode.height() + 2)))

    def _set_display_mode(self, mode: str):
        self.current_mode = mode
        self._update_mode_button_text()
        self._update_layout_positions()
        self.update()

    def _toggle_display_mode(self):
        """Переключатель режима для совместимости с тестами и хоткеями."""
        new_mode = "inplace" if self.current_mode == "hud" else "hud"
        self._set_display_mode(new_mode)

    def _show_settings_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        # 1. Прозрачность фона
        op_menu = menu.addMenu(tr("trans_menu_opacity", "Прозрачность фона"))
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
            act.setChecked(abs(self.bg_opacity - val) < 0.05)
            act.triggered.connect(lambda ch, v=val: self._set_opacity(v))

        # 2. Стиль / Цвет фона
        theme_menu = menu.addMenu(tr("trans_menu_theme", "Цвет фона"))
        theme_menu.setStyleSheet(self._menu_stylesheet())
        themes = [
            ("slate", tr("trans_theme_slate", "Тёмный сланец (Slate)")),
            ("oled", tr("trans_theme_oled", "Глубокий чёрный (OLED)")),
            ("cyber", tr("trans_theme_cyber", "Кибер-синий (Cyber)")),
        ]
        for key, name in themes:
            act = theme_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(self.bg_theme == key)
            act.triggered.connect(lambda ch, k=key: self._set_theme(k))

        # 3. Размер шрифта субтитров
        font_menu = menu.addMenu(tr("trans_menu_font_size", "Размер шрифта субтитров"))
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
            act.setChecked(self.hud_font_size == sz)
            act.triggered.connect(lambda ch, s=sz: self._set_font_size(s))

        menu.addSeparator()

        # 4. Скорость сканирования (FPS)
        fps_menu = menu.addMenu(tr("trans_menu_fps", "Скорость сканирования"))
        fps_menu.setStyleSheet(self._menu_stylesheet())
        speeds = [
            (150, tr("trans_fps_fast", "Быстро (150 мс)")),
            (300, tr("trans_fps_opt", "Оптимально (300 мс)")),
            (600, tr("trans_fps_eco", "Энергосбережение (600 мс)")),
        ]
        for ms, label in speeds:
            act = fps_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.scan_interval == ms)
            act.triggered.connect(lambda ch, m=ms: self._set_scan_interval(m))

        # 5. Smart Diff (Оптимизация CPU)
        act_diff = menu.addAction(tr("trans_menu_smart_diff", "Умная пауза при статичном кадре"))
        act_diff.setCheckable(True)
        act_diff.setChecked(self.smart_diff_enabled)
        act_diff.triggered.connect(self._toggle_smart_diff)

        menu.exec(self.btn_settings.mapToGlobal(QPoint(0, self.btn_settings.height() + 2)))

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

    def set_stealth_lock(self, locked: bool):
        """
        Включает или выключает режим маскировки по кнопке «Глазик».
        В заблокированном режиме:
        - Шапка и контур рамки полностью скрываются.
        - Отображается только мини-иконка глазика в углу.
        - Наведение и клики мыши по области рамки проходят насквозь в фоновое окно/игру.
        - Единственный элемент, реагирующий на клик — иконка глазика.
        """
        self.is_locked_stealth = locked
        if locked:
            self.header_frame.hide()
            self.btn_eye_unlock.show()
            self.btn_eye_unlock.raise_()
        else:
            self.btn_eye_unlock.hide()
            self.header_frame.show()
            self.header_frame.raise_()
        self.update()

    def unfold_controls(self):
        """Разворачивает панель управления и снимает скрытие (для хоткеев и меню)."""
        self.set_stealth_lock(False)

    def _update_layout_positions(self):
        w, h = self.width(), self.height()
        hdr_h = 34
        self.header_frame.setGeometry(6, 6, max(100, w - 12), hdr_h)
        self.btn_eye_unlock.setGeometry(w - 32, 6, 26, 26)

        hud_h = max(42, min(110, int(h * 0.35)))
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

    # ------------------ Обработка событий мыши и перемещения ------------------

    def nativeEvent(self, eventType, message):
        """
        В режиме маскировки (Глазик) перенаправляет все клики и движения мыши сквозь рамку
        в лежащее под ней окно (HTTRANSPARENT), за исключением координат кнопки разблокировки глазика.
        """
        if sys.platform == "win32" and self.is_locked_stealth:
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    local_pt = self.mapFromGlobal(QPoint(x, y))
                    if self.btn_eye_unlock.isVisible() and self.btn_eye_unlock.geometry().contains(local_pt):
                        return True, 1  # HTCLIENT
                    return True, -1  # HTTRANSPARENT: клики идут в игру/приложение
            except Exception:
                pass
        return super().nativeEvent(eventType, message)

    def _start_system_move(self) -> bool:
        """Инициирует нативное аппаратное перемещение окна Windows (Aero Snap, multi-monitor, 0 lag)."""
        if sys.platform == "win32":
            try:
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
        if self.is_locked_stealth:
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

            # Клик по свободной области рамки начинает перемещение
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
        if self.is_locked_stealth:
            return

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
        if watched in (self.header_frame, self.title_container, self.lbl_title, self.lbl_icon, self.drag_grip):
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

        # 1. Отрисовка границ рамки (только если не активен режим маскировки Глазика)
        if not self.is_locked_stealth:
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

        # 2. Режим In-place: отрисовка перевода прямо поверх оригинального текста с адаптивным кеглем
        if self.current_mode == "inplace" and self.translated_blocks:
            base_font = QFont("Segoe UI", 10, QFont.Weight.DemiBold)
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

                # Вычисляем размер шрифта строго пропорционально высоте оригинального блока текста
                if self.hud_font_size > 0:
                    target_pixel_size = self.hud_font_size
                else:
                    target_pixel_size = max(10, int(round(bh * 0.72)))

                font = QFont(base_font)
                font.setPixelSize(target_pixel_size)
                fm = QFontMetrics(font)

                text_w = fm.horizontalAdvance(txt)
                eff_w = max(bw, float(text_w + 12))
                max_avail_w = max(35.0, float(w - bx - 8))
                if eff_w > max_avail_w:
                    eff_w = max_avail_w
                    if text_w > eff_w and self.hud_font_size == 0:
                        scale_factor = eff_w / max(1.0, float(text_w))
                        adj_size = max(9, int(round(target_pixel_size * max(0.70, scale_factor))))
                        font.setPixelSize(adj_size)
                        fm = QFontMetrics(font)

                painter.setFont(font)

                pad_x = 4.0
                pad_y = 2.0
                bg_rect = QRectF(bx - pad_x, by - pad_y, eff_w + pad_x * 2, bh + pad_y * 2)

                # Подложка под переведённый текст с учетом настроек прозрачности
                if self.bg_opacity > 0.05:
                    painter.setBrush(QColor(r, g, b, alpha))
                    painter.setPen(QPen(QColor(59, 130, 246, min(200, alpha + 30)), 1.0))
                    painter.drawRoundedRect(bg_rect, 4.0, 4.0)

                # Текст перевода: контрастный светлый с четким рендерингом
                painter.setPen(QColor(248, 250, 252))
                painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, txt)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
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
