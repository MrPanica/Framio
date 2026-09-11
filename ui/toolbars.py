# -*- coding: utf-8 -*-
"""
Минималистичные плавающие панели инструментов в стиле Lightshot.
Используют профессиональные векторные SVG-иконки (Lucide / Feather).
Автоматически адаптируются к светлой или тёмной теме Windows.
Поддерживают выбор стиля стрелок, компактные размеры кнопок (28x28 px)
и умные popover-окна настроек.
"""

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QSize, QRect, QRectF
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMenu,
    QButtonGroup, QFrame, QLabel, QSlider, QColorDialog,
    QCheckBox, QComboBox, QSpinBox, QApplication
)
from PyQt6.QtGui import QAction, QColor, QIcon, QPixmap, QPainter, QBrush, QPen, QLinearGradient

from .widgets import ModernButton
from .icons import create_themed_icon, create_style_preview_icon
from utils.image_filters import FilterType, FILTER_NAMES
from utils.win32_helper import is_system_dark_theme
from utils.screen_lock import enumerate_recordable_windows
from utils.window_icon import get_window_qicon
from config import ConfigManager
from utils.i18n import tr


class ToolType:
    MOVE = "move"
    SELECT = "select"
    PEN = "pen"
    SHAPES = "shapes"
    LINE = "line"
    ARROW = "arrow"
    RECT = "rect"
    FILLED_RECT = "filled_rect"
    CIRCLE = "circle"
    TEXT = "text"
    HIGHLIGHTER = "highlighter"
    MOSAIC = "mosaic"
    CAPTURE_MASK = "capture_mask"


def get_theme_styles():
    """Возвращает набор стилей в зависимости от системной темы Windows."""
    is_dark = is_system_dark_theme()
    if is_dark:
        return {
            "is_dark": True,
            "panel_frame": """
                QFrame {
                    background-color: #1a1a1d;
                    border: 1px solid #323238;
                    border-radius: 6px;
                }
                QToolTip {
                    background-color: #18181b;
                    color: #ffffff;
                    border: 1px solid #3f3f46;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 11px;
                }
            """,
            "button_base": """
                QPushButton {
                    background-color: transparent;
                    color: #d4d4d8;
                    border: 1px solid transparent;
                    border-radius: 4px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #2b2b32;
                    border-color: #3e3e48;
                    color: #ffffff;
                }
                QPushButton:pressed {
                    background-color: #141416;
                }
                QPushButton:checked {
                    background-color: #2563eb;
                    border-color: #3b82f6;
                    color: #ffffff;
                }
            """,
            "sep_color": "#323238",
            "popup_frame": """
                QFrame {
                    background-color: #18181b;
                    border: 1px solid #3f3f46;
                    border-radius: 8px;
                }
                QLabel {
                    color: #f4f4f5;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }
                QCheckBox {
                    color: #d4d4d8;
                    font-size: 12px;
                }
                QComboBox {
                    background-color: #27272a;
                    color: #ffffff;
                    border: 1px solid #3f3f46;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 11px;
                }
            """,
            "popup_item_hover": "#27272a",
            "text_color": "#e4e4e7"
        }
    else:
        return {
            "is_dark": False,
            "panel_frame": """
                QFrame {
                    background-color: #ebebee;
                    border: 1px solid #c8c8cf;
                    border-radius: 6px;
                }
                QToolTip {
                    background-color: #18181b;
                    color: #ffffff;
                    border: 1px solid #3f3f46;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 11px;
                }
            """,
            "button_base": """
                QPushButton {
                    background-color: transparent;
                    color: #27272a;
                    border: 1px solid transparent;
                    border-radius: 4px;
                    padding: 0px;
                }
                QPushButton:hover {
                    background-color: #dadadd;
                    border-color: #b0b0b8;
                    color: #000000;
                }
                QPushButton:pressed {
                    background-color: #c8c8cc;
                }
                QPushButton:checked {
                    background-color: #0078d4;
                    border-color: #0066b8;
                    color: #ffffff;
                }
            """,
            "sep_color": "#d0d0d8",
            "popup_frame": """
                QFrame {
                    background-color: #f8f8fa;
                    border: 1px solid #d1d5db;
                    border-radius: 8px;
                }
                QLabel {
                    color: #18181b;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 12px;
                }
                QCheckBox {
                    color: #27272a;
                    font-size: 12px;
                }
                QComboBox {
                    background-color: #ffffff;
                    color: #18181b;
                    border: 1px solid #d1d5db;
                    border-radius: 4px;
                    padding: 4px 8px;
                    font-size: 11px;
                }
            """,
            "popup_item_hover": "#e5e7eb",
            "text_color": "#18181b"
        }


def get_context_menu_style(is_dark: bool) -> str:
    """Возвращает CSS-стиль для контекстных меню с учетом системной темы."""
    if is_dark:
        return """
            QMenu {
                background-color: #1f1f23;
                border: 1px solid #383842;
                border-radius: 8px;
                padding: 4px;
                color: #f4f4f5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 24px 6px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2b2b34;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: #383842;
                margin: 4px 6px;
            }
        """
    else:
        return """
            QMenu {
                background-color: #ffffff;
                border: 1px solid #e4e4e7;
                border-radius: 8px;
                padding: 4px;
                color: #18181b;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 24px 6px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #f4f4f5;
                color: #18181b;
            }
            QMenu::separator {
                height: 1px;
                background: #e4e4e7;
                margin: 4px 6px;
            }
        """


def show_smart_popup(anchor, popup: QWidget):
    """
    Умное позиционирование выпадающего окна:
    Если внизу экрана не хватает места, всплывает СВЕРХУ кнопки или точки клика.
    Также не выходит за границы экрана слева и справа.
    anchor: QWidget или QPoint (глобальные координаты).
    """
    popup.adjustSize()
    if isinstance(anchor, QPoint):
        anchor_global = anchor
        anchor_h = 12
        screen = QApplication.screenAt(anchor_global) or QApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
    else:
        anchor_global = anchor.mapToGlobal(QPoint(0, 0))
        anchor_h = anchor.height()
        screen_geo = anchor.screen().availableGeometry()

    pop_w = popup.sizeHint().width()
    pop_h = popup.sizeHint().height()

    if anchor_global.y() + anchor_h + pop_h + 8 <= screen_geo.bottom():
        y = anchor_global.y() + anchor_h + 4
    else:
        y = anchor_global.y() - pop_h - 4

    x = anchor_global.x()
    if x + pop_w > screen_geo.right():
        x = screen_geo.right() - pop_w - 6
    if x < screen_geo.left():
        x = screen_geo.left() + 6

    popup.move(x, y)
    popup.show()


def show_side_smart_popup(anchor, popup: QWidget, prefer_side: str = "right"):
    """
    Умное позиционирование выпадающей панели СБОКУ от кнопки (в стиле Photoshop / Figma):
    По умолчанию открывается СПРАВА (наружу от области выделения).
    Если справа места до границы экрана недостаточно, открывается СЛЕВА.
    По вертикали центрируется относительно кнопки anchor и удерживается в пределах видимого экрана.
    """
    popup.adjustSize()
    if isinstance(anchor, QPoint):
        anchor_global = anchor
        anchor_w = 26
        anchor_h = 26
        screen = QApplication.screenAt(anchor_global) or QApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
    else:
        anchor_global = anchor.mapToGlobal(QPoint(0, 0))
        anchor_w = anchor.width()
        anchor_h = anchor.height()
        screen = anchor.screen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

    pop_w = popup.sizeHint().width()
    pop_h = popup.sizeHint().height()

    if prefer_side == "left":
        if anchor_global.x() - pop_w - 6 >= screen_geo.left():
            x = anchor_global.x() - pop_w - 6
        else:
            x = anchor_global.x() + anchor_w + 6
    else:
        if anchor_global.x() + anchor_w + pop_w + 6 <= screen_geo.right():
            x = anchor_global.x() + anchor_w + 6
        else:
            x = anchor_global.x() - pop_w - 6

    y = anchor_global.y() + (anchor_h - pop_h) // 2
    if y + pop_h > screen_geo.bottom() - 6:
        y = screen_geo.bottom() - pop_h - 6
    if y < screen_geo.top() + 6:
        y = screen_geo.top() + 6

    popup.move(x, y)
    popup.show()


class _ComboCountdownCompatProxy:
    def __init__(self, spin):
        self._spin = spin
        self._presets = [3, 5, 10]

    def count(self) -> int:
        return len(self._presets)

    def currentText(self) -> str:
        return f"{self._spin.value()}{tr('popup_video_timer_sec', ' сек')}"

    def setCurrentIndex(self, idx: int):
        if 0 <= idx < len(self._presets):
            self._spin.setValue(self._presets[idx])

    def setCurrentText(self, text: str):
        try:
            val = int(text.split()[0])
            self._spin.setValue(val)
        except Exception:
            pass

    def itemText(self, idx: int) -> str:
        if 0 <= idx < len(self._presets):
            return f"{self._presets[idx]}{tr('popup_video_timer_sec', ' сек')}"
        return ""

    def setEnabled(self, enabled: bool):
        self._spin.setEnabled(enabled)

    def isEnabled(self) -> bool:
        return self._spin.isEnabled()


class VideoOptionsPopup(QFrame):
    start_video = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config
        self.setFixedWidth(290)

        theme = get_theme_styles()
        self.setStyleSheet(theme["popup_frame"] + """
            QFrame {
                max-width: 290px;
            }
            QComboBox {
                background-color: #27272a;
                color: #ffffff;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 4px 6px;
                font-size: 11px;
                max-width: 266px;
            }
            QComboBox QAbstractItemView {
                background-color: #18181b;
                color: #f4f4f5;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 4px;
                outline: none;
                max-width: 270px;
            }
            QSpinBox {
                background-color: #27272a;
                color: #ffffff;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 11px;
                min-width: 70px;
            }
            QSpinBox:focus {
                border: 1px solid #38bdf8;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #3f3f46;
                border: none;
                width: 16px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #52525b;
            }
            QScrollBar:vertical {
                background: #18181b;
                width: 8px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #3f3f46;
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #52525b;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QPushButton {
                background-color: #dc2626;
                color: #ffffff;
                border: 1px solid #ef4444;
                border-radius: 5px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        lbl = QLabel(tr("popup_video_title", "Параметры видеозаписи"))
        lbl.setStyleSheet("color: #ef4444; font-weight: bold;")
        layout.addWidget(lbl)

        # Выбор источника захвата (окно или весь экран)
        row_w = QVBoxLayout()
        row_w.setSpacing(3)
        row_w.addWidget(QLabel(tr("popup_video_source", "Источник захвата:")))
        self.combo_window = QComboBox()
        self.combo_window.setIconSize(QSize(18, 18))
        self.combo_window.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_window.setMinimumContentsLength(18)
        self.combo_window.setMaxVisibleItems(14)
        self.combo_window.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        row_w.addWidget(self.combo_window)
        layout.addLayout(row_w)

        row_fps = QHBoxLayout()
        row_fps.addWidget(QLabel(tr("popup_video_fps", "Кадры в секунду (FPS):")))
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["15", "24", "30", "60"])
        self.combo_fps.setCurrentText(str(getattr(self.cfg, "video_fps", 30)))
        self.combo_fps.setToolTip(tr("popup_video_fps_tip", "Частота кадров будущей записи видео"))
        row_fps.addWidget(self.combo_fps)
        layout.addLayout(row_fps)

        self.chk_mic = QCheckBox(tr("popup_video_mic", "Запись звука с микрофона"))
        self.chk_mic.setChecked(getattr(self.cfg, "record_mic", True))
        self.chk_mic.toggled.connect(self._on_audio_setting_changed)
        layout.addWidget(self.chk_mic)

        self.chk_system = QCheckBox(tr("popup_video_system", "Запись звука из игр / системы (динамики)"))
        self.chk_system.setChecked(getattr(self.cfg, "record_system", True))
        self.chk_system.toggled.connect(self._on_audio_setting_changed)
        layout.addWidget(self.chk_system)

        row_c = QHBoxLayout()
        row_c.addWidget(QLabel(tr("popup_video_codec", "Кодек:")))
        self.combo_codec = QComboBox()
        self.combo_codec.addItems([tr("popup_video_codec_mp4v", "mp4v (Стандарт)"), tr("popup_video_codec_avc1", "avc1 (H.264)"), "XVID"])
        row_c.addWidget(self.combo_codec)
        layout.addLayout(row_c)

        # Таймер перед началом записи
        row_timer = QHBoxLayout()
        self.chk_countdown = QCheckBox(tr("popup_video_timer", "Таймер перед записью:"))
        self.chk_countdown.setChecked(False)
        row_timer.addWidget(self.chk_countdown)
        self.spin_countdown = QSpinBox()
        self.spin_countdown.setRange(1, 60)
        self.spin_countdown.setSuffix(tr("popup_video_timer_sec", " сек"))
        cur_cd = getattr(self.cfg, "record_countdown_seconds", 3)
        self.spin_countdown.setValue(cur_cd)
        self.spin_countdown.setToolTip(tr("popup_video_timer_tip", "Длительность обратного отсчёта (1–60 сек)"))
        self.spin_countdown.setEnabled(False)
        self.chk_countdown.toggled.connect(self.spin_countdown.setEnabled)
        row_timer.addWidget(self.spin_countdown)
        layout.addLayout(row_timer)

        btn_start = QPushButton(tr("popup_video_start", "Начать запись видео"))
        btn_start.clicked.connect(self._on_start)
        layout.addWidget(btn_start)

    @property
    def combo_countdown(self):
        return _ComboCountdownCompatProxy(self.spin_countdown)

    def showEvent(self, event):
        super().showEvent(event)
        self.chk_mic.setChecked(getattr(self.cfg, "record_mic", True))
        self.chk_system.setChecked(getattr(self.cfg, "record_system", True))
        self.chk_countdown.setChecked(False)
        self.combo_fps.setCurrentText(str(getattr(self.cfg, "video_fps", 30)))
        cur_cd = getattr(self.cfg, "record_countdown_seconds", 3)
        self.spin_countdown.setValue(cur_cd)
        self.spin_countdown.setEnabled(False)

        # Восстановление кодека
        saved_codec = getattr(self.cfg, "video_codec", "mp4v")
        for i in range(self.combo_codec.count()):
            if self.combo_codec.itemText(i).startswith(saved_codec):
                self.combo_codec.setCurrentIndex(i)
                break

        # Заполнение списка окон с нативными иконками процессов
        self.combo_window.clear()
        screen_icon = get_window_qicon(0)
        self.combo_window.addItem(screen_icon, tr("popup_video_all_screens", "Весь экран / Все окна"), 0)
        self.combo_window.setItemData(0, tr("popup_video_all_screens_tip", "Записывать всю область экрана под рамкой со всеми окнами"), Qt.ItemDataRole.ToolTipRole)
        try:
            windows = enumerate_recordable_windows()
            for hwnd, title in windows:
                short_title = title if len(title) <= 26 else title[:24] + "…"
                ico = get_window_qicon(hwnd)
                self.combo_window.addItem(ico, short_title, hwnd)
                idx = self.combo_window.count() - 1
                self.combo_window.setItemData(idx, title, Qt.ItemDataRole.ToolTipRole)
        except Exception:
            pass

        target_title = getattr(self.cfg, "target_window_title", "")
        if target_title:
            for i in range(self.combo_window.count()):
                if target_title in self.combo_window.itemText(i):
                    self.combo_window.setCurrentIndex(i)
                    break

    def _on_audio_setting_changed(self):
        self.cfg.record_mic = self.chk_mic.isChecked()
        self.cfg.record_system = self.chk_system.isChecked()
        self.config_mgr.save()

    def _on_start(self):
        codec_raw = self.combo_codec.currentText().split()[0]
        mic_val = self.chk_mic.isChecked()
        sys_val = self.chk_system.isChecked()
        target_hwnd = self.combo_window.currentData()
        target_title = self.combo_window.currentText() if target_hwnd else ""

        cd_enabled = self.chk_countdown.isChecked()
        cd_sec = self.spin_countdown.value()

        self.cfg.record_mic = mic_val
        self.cfg.record_system = sys_val
        fps = int(self.combo_fps.currentText())
        self.cfg.video_fps = fps
        self.cfg.video_codec = codec_raw
        self.cfg.target_window_title = target_title
        self.cfg.record_countdown_enabled = cd_enabled
        self.cfg.record_countdown_seconds = cd_sec
        self.config_mgr.save()

        params = {
            "mic": mic_val,
            "system": sys_val,
            "fps": fps,
            "codec": codec_raw,
            "target_hwnd": target_hwnd,
            "countdown": cd_enabled,
            "countdown_seconds": cd_sec
        }
        self.close()
        self.start_video.emit(params)


class GifOptionsPopup(QFrame):
    start_gif = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config

        theme = get_theme_styles()
        self.setStyleSheet(theme["popup_frame"] + """
            QSpinBox {
                background-color: #27272a;
                color: #ffffff;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 3px 6px;
                font-size: 11px;
                min-width: 70px;
            }
            QSpinBox:focus {
                border: 1px solid #a855f7;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                background-color: #3f3f46;
                border: none;
                width: 16px;
            }
            QSpinBox::up-button:hover, QSpinBox::down-button:hover {
                background-color: #52525b;
            }
            QPushButton {
                background-color: #7c3aed;
                color: #ffffff;
                border: 1px solid #8b5cf6;
                border-radius: 5px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #6d28d9;
            }
        """)

        self.setFixedWidth(230)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        lbl = QLabel(tr("popup_gif_title", "Параметры GIF-анимации"))
        lbl.setStyleSheet("color: #a855f7; font-weight: bold;")
        layout.addWidget(lbl)

        layout.addWidget(QLabel(tr("popup_gif_compression", "Сжатие / Цвета:")))
        self.combo_q = QComboBox()
        self.gif_options = [
            (tr("popup_gif_opt_max", "Максимальное (64 цвета)"), 64, "none"),
            (tr("popup_gif_opt_high", "Высокое (64 цвета, bayer)"), 64, "bayer"),
            (tr("popup_gif_opt_balance", "Баланс (128 цветов)"), 128, "bayer"),
            (tr("popup_gif_opt_quality", "Высокое качество (256 цв.)"), 256, "bayer"),
            (tr("popup_gif_opt_extreme", "Экстремальное (32 цвета)"), 32, "none"),
        ]
        for title, colors, dither in self.gif_options:
            self.combo_q.addItem(title, (colors, dither))
        layout.addWidget(self.combo_q)

        row_f = QHBoxLayout()
        row_f.addWidget(QLabel(tr("popup_gif_fps", "Частота (FPS):")))
        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["15", "20", "24", "10"])
        row_f.addWidget(self.combo_fps)
        layout.addLayout(row_f)

        # Таймер перед началом записи
        row_timer = QHBoxLayout()
        self.chk_countdown = QCheckBox(tr("popup_video_timer", "Таймер перед записью:"))
        self.chk_countdown.setChecked(False)
        row_timer.addWidget(self.chk_countdown)
        self.spin_countdown = QSpinBox()
        self.spin_countdown.setRange(1, 60)
        self.spin_countdown.setSuffix(tr("popup_video_timer_sec", " сек"))
        cur_cd = getattr(self.cfg, "record_countdown_seconds", 3)
        self.spin_countdown.setValue(cur_cd)
        self.spin_countdown.setToolTip(tr("popup_video_timer_tip", "Длительность обратного отсчёта (1–60 сек)"))
        self.spin_countdown.setEnabled(False)
        self.chk_countdown.toggled.connect(self.spin_countdown.setEnabled)
        row_timer.addWidget(self.spin_countdown)
        layout.addLayout(row_timer)

        btn_start = QPushButton(tr("popup_gif_start", "Начать запись GIF"))
        btn_start.clicked.connect(self._on_start)
        layout.addWidget(btn_start)

    @property
    def combo_countdown(self):
        return _ComboCountdownCompatProxy(self.spin_countdown)

    def showEvent(self, event):
        super().showEvent(event)
        self.chk_countdown.setChecked(False)
        cur_cd = getattr(self.cfg, "record_countdown_seconds", 3)
        self.spin_countdown.setValue(cur_cd)
        self.spin_countdown.setEnabled(False)

        saved_fps = str(getattr(self.cfg, "gif_fps", 15))
        idx = self.combo_fps.findText(saved_fps)
        if idx >= 0:
            self.combo_fps.setCurrentIndex(idx)

        saved_colors = getattr(self.cfg, "gif_colors", 64)
        saved_dither = getattr(self.cfg, "gif_dither", "none")
        for i in range(self.combo_q.count()):
            data = self.combo_q.itemData(i)
            if data and data[0] == saved_colors and data[1] == saved_dither:
                self.combo_q.setCurrentIndex(i)
                break

    def _on_start(self):
        fps_val = int(self.combo_fps.currentText())
        data = self.combo_q.currentData() or (64, "none")
        colors_val, dither_val = data

        cd_enabled = self.chk_countdown.isChecked()
        cd_sec = self.spin_countdown.value()

        self.cfg.gif_fps = fps_val
        self.cfg.gif_colors = colors_val
        self.cfg.gif_dither = dither_val
        self.cfg.record_countdown_enabled = cd_enabled
        self.cfg.record_countdown_seconds = cd_sec
        self.config_mgr.save()

        params = {
            "fps": fps_val,
            "colors": colors_val,
            "dither": dither_val,
            "quality": self.combo_q.currentText(),
            "countdown": cd_enabled,
            "countdown_seconds": cd_sec
        }
        self.close()
        self.start_gif.emit(params)


class ScreenshotFormatPopup(QFrame):
    format_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config

        theme = get_theme_styles()
        self.setStyleSheet(theme["popup_frame"] + f"""
            QPushButton {{
                background-color: transparent;
                color: {theme['text_color']};
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                text-align: left;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {theme['popup_item_hover']};
                color: #3b82f6;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        formats = [
            ("png", tr("popup_fmt_png", "PNG (Максимальное качество)")),
            ("jpg", tr("popup_fmt_jpg", "JPEG (Компактный размер)")),
            ("webp", tr("popup_fmt_webp", "WebP (Современный формат)"))
        ]

        for ext, label in formats:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, e=ext: self._on_select(e))
            layout.addWidget(btn)

    def _on_select(self, ext: str):
        self.cfg.last_save_format = ext
        self.config_mgr.save()
        self.close()
        self.format_selected.emit(ext)


class CopyFormatPopup(QFrame):
    format_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config

        theme = get_theme_styles()
        self.setStyleSheet(theme["popup_frame"] + f"""
            QPushButton {{
                background-color: transparent;
                color: {theme['text_color']};
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                text-align: left;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {theme['popup_item_hover']};
                color: #3b82f6;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        formats = [
            ("standard", tr("popup_copy_standard", "Изображение (Стандартный буфер)")),
            ("png", tr("popup_copy_png", "PNG байты (PNG Image)")),
            ("jpg", tr("popup_copy_jpg", "JPEG байты (JPG Image)")),
            ("data_uri", tr("popup_copy_data_uri", "Data URI (Base64 текст)"))
        ]

        for fmt, label in formats:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, f=fmt: self._on_select(f))
            layout.addWidget(btn)

    def _on_select(self, fmt: str):
        self.cfg.default_copy_format = fmt
        self.config_mgr.save()
        self.close()
        self.format_selected.emit(fmt)


class SearchEnginePopup(QFrame):
    engine_selected = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        theme = get_theme_styles()
        self.setStyleSheet(theme["popup_frame"] + f"""
            QPushButton {{
                background-color: transparent;
                color: {theme['text_color']};
                border: none;
                border-radius: 4px;
                padding: 6px 14px;
                text-align: left;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: {theme['popup_item_hover']};
                color: #3b82f6;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(2)

        engines = [
            ("google", tr("popup_search_google", "Поиск в Google Lens")),
            ("yandex", tr("popup_search_yandex", "Поиск в Яндекс Картинках"))
        ]

        for eng, label in engines:
            btn = QPushButton(label)
            btn.clicked.connect(lambda checked, e=eng: self._select(e))
            layout.addWidget(btn)

    def _select(self, engine: str):
        self.close()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(10, lambda: self.engine_selected.emit(engine))


def style_toggle_btn(btn: QPushButton, checked: bool, is_dark: bool = True):
    if checked:
        btn.setStyleSheet("background-color: #2563eb; color: #ffffff; border: 1px solid #3b82f6; border-radius: 4px; font-weight: bold; font-size: 11px;")
    else:
        bg = "#27272a" if is_dark else "#e4e4e7"
        fg = "#d4d4d8" if is_dark else "#27272a"
        border = "#3f3f46" if is_dark else "#d1d5db"
        btn.setStyleSheet(f"background-color: {bg}; color: {fg}; border: 1px solid {border}; border-radius: 4px; font-size: 11px;")


class ToolPropertiesFlyout(QFrame):
    settings_updated = pyqtSignal()
    pipette_requested = pyqtSignal()

    PRESET_COLORS = [
        "#FF2E2E", "#00C0FF", "#2ECC71", "#FFD700",
        "#FF8C00", "#9B59B6", "#FFFFFF", "#1E1E1E"
    ]

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.tool_type = ToolType.PEN
        self.tool_data = {}

        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["popup_frame"] + """
            QLabel {
                font-size: 11px;
                font-weight: bold;
            }
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 8, 10, 8)
        self.layout.setSpacing(8)

        self.lbl_title = QLabel(tr("prop_tool_settings", "Настройки инструмента"))
        self.lbl_title.setStyleSheet("color: #3b82f6; font-weight: bold;")
        self.layout.addWidget(self.lbl_title)

        # 1. Выбор подтипа фигуры (Линия, Стрелка, Прямоугольник, Круг)
        self.shapes_type_widget = QWidget()
        shapes_type_layout = QHBoxLayout(self.shapes_type_widget)
        shapes_type_layout.setContentsMargins(0, 0, 0, 0)
        shapes_type_layout.setSpacing(4)
        self.shape_sub_buttons = {}
        shape_subs = [
            ("line", "line", tr("shape_line", "Прямая линия")),
            ("arrow", "arrow", tr("shape_arrow", "Стрелка")),
            ("rect", "rect", tr("shape_rect", "Прямоугольник")),
            ("circle", "circle", tr("shape_circle", "Круг / Овал"))
        ]
        for sub_k, ico_k, tip in shape_subs:
            sbtn = QPushButton()
            sbtn.setToolTip(tip)
            sbtn.setFixedSize(28, 24)
            sbtn.setCursor(Qt.CursorShape.PointingHandCursor)
            sbtn.setIcon(create_themed_icon(ico_k, self.is_dark, size=16))
            sbtn.setIconSize(QSize(16, 16))
            sbtn.clicked.connect(lambda checked, sk=sub_k: self._on_subshape_pick(sk))
            shapes_type_layout.addWidget(sbtn)
            self.shape_sub_buttons[sub_k] = sbtn
        self.layout.addWidget(self.shapes_type_widget)

        # Выпадающий список стиля стрелок
        self.arrow_sub_widget = QWidget()
        arrow_sub_layout = QHBoxLayout(self.arrow_sub_widget)
        arrow_sub_layout.setContentsMargins(0, 0, 0, 0)
        arrow_sub_layout.setSpacing(6)
        arrow_sub_layout.addWidget(QLabel(tr("prop_style", "Стиль:")))
        self.combo_arrow_style = QComboBox()
        self.combo_arrow_style.setIconSize(QSize(26, 18))
        arrow_styles = [
            ("classic", "arrow_classic", tr("prop_arrow_classic", "Классическая стрелка")),
            ("barbed", "arrow_barbed", tr("prop_arrow_barbed", "С вырезом (усиками)")),
            ("double", "arrow_double", tr("prop_arrow_double", "Двусторонняя стрелка")),
            ("stealth", "arrow_stealth", tr("prop_arrow_stealth", "Стелс-стрелка")),
            ("dashed", "arrow_dashed", tr("prop_arrow_dashed", "Пунктирная стрелка"))
        ]
        for s_key, ico_key, s_name in arrow_styles:
            ico = create_style_preview_icon(ico_key, self.is_dark)
            self.combo_arrow_style.addItem(ico, s_name, s_key)
        self.combo_arrow_style.currentIndexChanged.connect(self._on_arrow_combo_changed)
        arrow_sub_layout.addWidget(self.combo_arrow_style, 1)
        self.layout.addWidget(self.arrow_sub_widget)

        # Выпадающий список стиля линий
        self.line_sub_widget = QWidget()
        line_sub_layout = QHBoxLayout(self.line_sub_widget)
        line_sub_layout.setContentsMargins(0, 0, 0, 0)
        line_sub_layout.setSpacing(6)
        line_sub_layout.addWidget(QLabel(tr("prop_style", "Стиль:")))
        self.combo_line_style = QComboBox()
        self.combo_line_style.setIconSize(QSize(26, 18))
        line_styles = [
            ("solid", "line_solid", tr("prop_line_solid", "Сплошная линия")),
            ("dashed", "line_dashed", tr("prop_line_dashed", "Пунктирная линия")),
            ("dotted", "line_dotted", tr("prop_line_dotted", "Точечная линия"))
        ]
        for l_key, ico_key, l_name in line_styles:
            ico = create_style_preview_icon(ico_key, self.is_dark)
            self.combo_line_style.addItem(ico, l_name, l_key)
        self.combo_line_style.currentIndexChanged.connect(self._on_line_combo_changed)
        line_sub_layout.addWidget(self.combo_line_style, 1)
        self.layout.addWidget(self.line_sub_widget)

        # Выпадающий список стиля прямоугольника
        self.rect_sub_widget = QWidget()
        rect_sub_layout = QHBoxLayout(self.rect_sub_widget)
        rect_sub_layout.setContentsMargins(0, 0, 0, 0)
        rect_sub_layout.setSpacing(6)
        rect_sub_layout.addWidget(QLabel(tr("prop_corners", "Углы:")))
        self.combo_rect_style = QComboBox()
        self.combo_rect_style.setIconSize(QSize(26, 18))
        rect_styles = [
            ("sharp", "rect_sharp", tr("prop_corners_sharp", "Прямые углы")),
            ("rounded", "rect_rounded", tr("prop_corners_rounded", "Скруглённые углы"))
        ]
        for r_key, ico_key, r_name in rect_styles:
            ico = create_style_preview_icon(ico_key, self.is_dark)
            self.combo_rect_style.addItem(ico, r_name, r_key)
        self.combo_rect_style.currentIndexChanged.connect(self._on_rect_combo_changed)
        rect_sub_layout.addWidget(self.combo_rect_style, 1)
        self.layout.addWidget(self.rect_sub_widget)

        # 3. Настройки заливки (Контур vs Заливка, Сплошной vs Градиент, Прозрачность)
        self.fill_widget = QWidget()
        fill_layout = QVBoxLayout(self.fill_widget)
        fill_layout.setContentsMargins(0, 0, 0, 0)
        fill_layout.setSpacing(6)

        row_fm = QHBoxLayout()
        self.btn_outline = QPushButton(tr("prop_outline", "Контур"))
        self.btn_outline.setFixedSize(68, 24)
        self.btn_outline.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_outline.clicked.connect(lambda: self._set_filled(False))
        self.btn_filled = QPushButton(tr("prop_filled", "Заливка"))
        self.btn_filled.setFixedSize(68, 24)
        self.btn_filled.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_filled.clicked.connect(lambda: self._set_filled(True))
        row_fm.addWidget(self.btn_outline)
        row_fm.addWidget(self.btn_filled)
        row_fm.addStretch()
        fill_layout.addLayout(row_fm)

        # Опции залитой фигуры
        self.fill_options_widget = QWidget()
        fill_opt_layout = QVBoxLayout(self.fill_options_widget)
        fill_opt_layout.setContentsMargins(0, 0, 0, 0)
        fill_opt_layout.setSpacing(6)

        row_gt = QHBoxLayout()
        self.btn_fill_solid = QPushButton(tr("prop_fill_solid", "Сплошной"))
        self.btn_fill_solid.setFixedSize(74, 22)
        self.btn_fill_solid.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fill_solid.clicked.connect(lambda: self._set_gradient(False))
        self.btn_fill_gradient = QPushButton(tr("prop_fill_gradient", "Градиент"))
        self.btn_fill_gradient.setFixedSize(74, 22)
        self.btn_fill_gradient.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fill_gradient.clicked.connect(lambda: self._set_gradient(True))
        row_gt.addWidget(self.btn_fill_solid)
        row_gt.addWidget(self.btn_fill_gradient)
        row_gt.addStretch()
        fill_opt_layout.addLayout(row_gt)

        # Градиент: выбор 2 цветов
        self.gradient_colors_widget = QWidget()
        grad_col_layout = QHBoxLayout(self.gradient_colors_widget)
        grad_col_layout.setContentsMargins(0, 0, 0, 0)
        grad_col_layout.setSpacing(6)
        grad_col_layout.addWidget(QLabel(tr("prop_colors", "Цвета:")))

        self.btn_grad1 = QPushButton("1")
        self.btn_grad1.setFixedSize(26, 24)
        self.btn_grad1.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_grad1.setToolTip(tr("prop_grad1_tip", "Первый цвет градиента"))
        self.btn_grad1.clicked.connect(lambda: self._pick_gradient_color(1))
        grad_col_layout.addWidget(self.btn_grad1)

        grad_col_layout.addWidget(QLabel("→"))

        self.btn_grad2 = QPushButton("2")
        self.btn_grad2.setFixedSize(26, 24)
        self.btn_grad2.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_grad2.setToolTip(tr("prop_grad2_tip", "Второй цвет градиента"))
        self.btn_grad2.clicked.connect(lambda: self._pick_gradient_color(2))
        grad_col_layout.addWidget(self.btn_grad2)
        grad_col_layout.addStretch()
        fill_opt_layout.addWidget(self.gradient_colors_widget)

        # Прозрачность заливки
        self.lbl_fill_opacity = QLabel(tr("prop_fill_opacity", "Прозрачность заливки: {pct}%", pct=100))
        fill_opt_layout.addWidget(self.lbl_fill_opacity)
        self.slider_fill_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_fill_opacity.setRange(0, 100)
        self.slider_fill_opacity.setValue(100)
        self.slider_fill_opacity.valueChanged.connect(self._on_fill_opacity_changed)
        fill_opt_layout.addWidget(self.slider_fill_opacity)

        fill_layout.addWidget(self.fill_options_widget)
        self.layout.addWidget(self.fill_widget)

        # 4. Настройки маркера-хайлайтера
        self.highlighter_widget = QWidget()
        hl_layout = QVBoxLayout(self.highlighter_widget)
        hl_layout.setContentsMargins(0, 0, 0, 0)
        hl_layout.setSpacing(4)
        self.lbl_hl_opacity = QLabel(tr("prop_hl_opacity", "Непрозрачность маркера: {pct}%", pct=35))
        hl_layout.addWidget(self.lbl_hl_opacity)
        self.slider_hl_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_hl_opacity.setRange(10, 100)
        self.slider_hl_opacity.setValue(35)
        self.slider_hl_opacity.valueChanged.connect(self._on_hl_opacity_changed)
        hl_layout.addWidget(self.slider_hl_opacity)
        self.layout.addWidget(self.highlighter_widget)

        # 5. Настройки текста
        self.text_widget = QWidget()
        text_layout = QVBoxLayout(self.text_widget)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(6)

        row_font = QHBoxLayout()
        self.combo_font = QComboBox()
        self.combo_font.addItems([
            "Segoe UI", "Arial", "Consolas", "Times New Roman",
            "Calibri", "Verdana", "Tahoma", "Impact", "Courier New"
        ])
        self.combo_font.currentTextChanged.connect(self._on_font_family_changed)
        row_font.addWidget(self.combo_font)

        self.btn_bold = QPushButton("B")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setFixedSize(26, 24)
        self.btn_bold.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.btn_bold.setToolTip(tr("prop_bold_tip", "Жирный шрифт"))
        self.btn_bold.toggled.connect(self._on_bold_toggled)
        row_font.addWidget(self.btn_bold)

        self.btn_italic = QPushButton("I")
        self.btn_italic.setCheckable(True)
        self.btn_italic.setFixedSize(26, 24)
        self.btn_italic.setStyleSheet("font-style: italic; font-size: 13px; font-family: 'Times New Roman', serif;")
        self.btn_italic.setToolTip(tr("prop_text_italic", "Курсив"))
        self.btn_italic.toggled.connect(self._on_italic_toggled)
        row_font.addWidget(self.btn_italic)

        self.btn_underline = QPushButton("U")
        self.btn_underline.setCheckable(True)
        self.btn_underline.setFixedSize(26, 24)
        self.btn_underline.setStyleSheet("text-decoration: underline; font-size: 13px;")
        self.btn_underline.setToolTip(tr("prop_underline_tip", "Подчёркнутый шрифт"))
        self.btn_underline.toggled.connect(self._on_underline_toggled)
        row_font.addWidget(self.btn_underline)

        text_layout.addLayout(row_font)

        # Опции фона текста
        self.chk_flyout_text_bg = QCheckBox(tr("prop_text_bg", "Фон под текстом"))
        self.chk_flyout_text_bg.setStyleSheet("font-size: 11px;")
        self.chk_flyout_text_bg.toggled.connect(self._on_flyout_text_bg_toggled)
        text_layout.addWidget(self.chk_flyout_text_bg)

        self.flyout_text_bg_opt = QWidget()
        bg_opt_lay = QVBoxLayout(self.flyout_text_bg_opt)
        bg_opt_lay.setContentsMargins(0, 0, 0, 0)
        bg_opt_lay.setSpacing(4)
        self.lbl_flyout_text_bg_alpha = QLabel(tr("prop_text_bg_alpha", "Непрозрачность фона: {pct}%", pct=70))
        self.lbl_flyout_text_bg_alpha.setStyleSheet("font-size: 11px;")
        bg_opt_lay.addWidget(self.lbl_flyout_text_bg_alpha)
        self.slider_flyout_text_bg_alpha = QSlider(Qt.Orientation.Horizontal)
        self.slider_flyout_text_bg_alpha.setRange(10, 100)
        self.slider_flyout_text_bg_alpha.setValue(70)
        self.slider_flyout_text_bg_alpha.valueChanged.connect(self._on_flyout_text_bg_alpha_changed)
        bg_opt_lay.addWidget(self.slider_flyout_text_bg_alpha)
        text_layout.addWidget(self.flyout_text_bg_opt)
        self.flyout_text_bg_opt.hide()

        self.layout.addWidget(self.text_widget)

        # 6. Палитра цветов
        self.color_widget = QWidget()
        color_layout = QHBoxLayout(self.color_widget)
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(5)

        self.color_buttons = {}
        for col in self.PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"background-color: {col}; border-radius: 11px; border: 1px solid #555;")
            btn.clicked.connect(lambda checked, c=col: self._on_color_pick(c))
            color_layout.addWidget(btn)
            self.color_buttons[col] = btn

        # Эффект мозаики (векторная SVG иконка)
        self.btn_mosaic_color = QPushButton()
        self.btn_mosaic_color.setFixedSize(24, 22)
        self.btn_mosaic_color.setToolTip(tr("prop_mosaic_tip", "Режим мозаики (цензура) для любого инструмента"))
        self.btn_mosaic_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mosaic_color.setIcon(create_themed_icon("mosaic", self.is_dark, size=14))
        self.btn_mosaic_color.setIconSize(QSize(14, 14))
        self.btn_mosaic_color.setStyleSheet("background-color: #27272a; border-radius: 11px; border: 1px solid #555;")
        self.btn_mosaic_color.clicked.connect(lambda: self._on_color_pick("mosaic"))
        color_layout.addWidget(self.btn_mosaic_color)
        self.color_buttons["mosaic"] = self.btn_mosaic_color

        # Эффект размытия (векторная SVG иконка)
        self.btn_blur_color = QPushButton()
        self.btn_blur_color.setFixedSize(24, 22)
        self.btn_blur_color.setToolTip(tr("prop_blur_tip", "Режим размытия (блюр) для любого инструмента"))
        self.btn_blur_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_blur_color.setIcon(create_themed_icon("blur", self.is_dark, size=14))
        self.btn_blur_color.setIconSize(QSize(14, 14))
        self.btn_blur_color.setStyleSheet("background-color: #27272a; border-radius: 11px; border: 1px solid #555;")
        self.btn_blur_color.clicked.connect(lambda: self._on_color_pick("blur"))
        color_layout.addWidget(self.btn_blur_color)
        self.color_buttons["blur"] = self.btn_blur_color

        # Пипетка для захвата цвета с экрана (векторная SVG иконка)
        self.btn_pipette = QPushButton()
        self.btn_pipette.setFixedSize(24, 22)
        self.btn_pipette.setToolTip(tr("prop_eyedropper_tip", "Пипетка (выбрать цвет с экрана)"))
        self.btn_pipette.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pipette.setIcon(create_themed_icon("pipette", self.is_dark, size=14))
        self.btn_pipette.setIconSize(QSize(14, 14))
        self.btn_pipette.setStyleSheet("background-color: #27272a; border-radius: 11px; border: 1px solid #555;")
        self.btn_pipette.clicked.connect(self._on_pipette_clicked)
        color_layout.addWidget(self.btn_pipette)

        # Выбор произвольного цвета (векторная SVG иконка палитры)
        self.btn_more_color = QPushButton()
        self.btn_more_color.setFixedSize(24, 22)
        self.btn_more_color.setToolTip(tr("prop_color_custom", "Выбрать произвольный цвет"))
        self.btn_more_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more_color.setIcon(create_themed_icon("palette", self.is_dark, size=14))
        self.btn_more_color.setIconSize(QSize(14, 14))
        self.btn_more_color.clicked.connect(self._open_custom_dialog)
        color_layout.addWidget(self.btn_more_color)

        self.layout.addWidget(self.color_widget)

        # Выбор режима цензуры: Мозаика vs Блюр (для ToolType.MOSAIC)
        self.censor_type_widget = QWidget()
        censor_type_layout = QHBoxLayout(self.censor_type_widget)
        censor_type_layout.setContentsMargins(0, 0, 0, 0)
        censor_type_layout.setSpacing(6)
        self.btn_mode_mosaic = QPushButton(tr("prop_censor_mosaic", "Мозаика"))
        self.btn_mode_mosaic.setFixedSize(78, 24)
        self.btn_mode_mosaic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_mosaic.clicked.connect(lambda: self._set_censor_mode("mosaic"))
        self.btn_mode_blur = QPushButton(tr("prop_censor_blur", "Блюр"))
        self.btn_mode_blur.setFixedSize(78, 24)
        self.btn_mode_blur.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mode_blur.clicked.connect(lambda: self._set_censor_mode("blur"))
        censor_type_layout.addWidget(self.btn_mode_mosaic)
        censor_type_layout.addWidget(self.btn_mode_blur)
        censor_type_layout.addStretch()
        self.layout.addWidget(self.censor_type_widget)

        # 7. Сгруппированный контейнер для ползунков (толщина, зернистость, блюр)
        self.slider_card = QFrame()
        self.slider_card.setStyleSheet("""
            QFrame {
                background-color: rgba(24, 24, 27, 180);
                border: 1px solid #2d3139;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        slider_card_layout = QVBoxLayout(self.slider_card)
        slider_card_layout.setContentsMargins(6, 6, 6, 6)
        slider_card_layout.setSpacing(6)

        self.lbl_size = QLabel(tr("prop_stroke_width", "Толщина: {val} px", val=4))
        slider_card_layout.addWidget(self.lbl_size)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 32)
        self.slider.setValue(4)
        self.slider.valueChanged.connect(self._on_slider_changed)
        slider_card_layout.addWidget(self.slider)

        self.lbl_grain = QLabel(tr("prop_mosaic_size", "Размер мозаики: {val} px", val=8))
        slider_card_layout.addWidget(self.lbl_grain)
        self.slider_grain = QSlider(Qt.Orientation.Horizontal)
        self.slider_grain.setRange(3, 30)
        self.slider_grain.setValue(8)
        self.slider_grain.valueChanged.connect(self._on_grain_slider_changed)
        slider_card_layout.addWidget(self.slider_grain)

        self.lbl_blur = QLabel(tr("prop_blur_radius", "Степень размытия: {val} px", val=15))
        slider_card_layout.addWidget(self.lbl_blur)
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(3, 45)
        self.slider_blur.setValue(15)
        self.slider_blur.valueChanged.connect(self._on_blur_slider_changed)
        slider_card_layout.addWidget(self.slider_blur)

        self.layout.addWidget(self.slider_card)

    def load_tool(self, tool_type: str, tool_data: dict):
        self.tool_type = tool_type
        self.tool_data = tool_data

        names = {
            ToolType.MOVE: tr("tool_move", "Перемещение рамки"),
            ToolType.PEN: tr("tool_pen", "Карандаш"),
            ToolType.SHAPES: tr("tool_shapes", "Фигуры"),
            ToolType.TEXT: tr("tool_text", "Текст"),
            ToolType.HIGHLIGHTER: tr("tool_highlighter", "Маркер-хайлайтер"),
            ToolType.MOSAIC: tr("tool_mosaic", "Мозаика (Цензура)")
        }
        self.lbl_title.setText(names.get(tool_type, tr("flyout_tool", "Инструмент")))

        # Скрываем все контекстные виджеты
        self.shapes_type_widget.hide()
        self.censor_type_widget.hide()
        self.arrow_sub_widget.hide()
        self.line_sub_widget.hide()
        self.rect_sub_widget.hide()
        self.fill_widget.hide()
        self.highlighter_widget.hide()
        self.text_widget.hide()
        self.color_widget.hide()
        self.slider_card.hide()
        self.slider.hide()
        self.lbl_size.hide()
        self.slider_grain.hide()
        self.lbl_grain.hide()
        self.slider_blur.hide()
        self.lbl_blur.hide()

        if tool_type == ToolType.SHAPES:
            self.shapes_type_widget.show()
            sub = tool_data.get("subshape", "arrow")
            self._highlight_subshape(sub)

            self.arrow_sub_widget.hide()
            self.line_sub_widget.hide()
            self.rect_sub_widget.hide()
            self.fill_widget.hide()
            self.fill_options_widget.hide()

            if sub == "arrow":
                self.arrow_sub_widget.show()
                cur_arrow = tool_data.get("arrow_style", "barbed")
                idx = self.combo_arrow_style.findData(cur_arrow)
                if idx >= 0:
                    self.combo_arrow_style.blockSignals(True)
                    self.combo_arrow_style.setCurrentIndex(idx)
                    self.combo_arrow_style.blockSignals(False)

                # Выбор: Контурный или Залитый наконечник стрелки
                self.fill_widget.show()
                self.btn_outline.setText(tr("prop_outline", "Контур"))
                self.btn_filled.setText(tr("prop_filled", "Заливка"))
                filled = tool_data.get("filled", True)
                style_toggle_btn(self.btn_outline, not filled, self.is_dark)
                style_toggle_btn(self.btn_filled, filled, self.is_dark)

            elif sub == "line":
                self.line_sub_widget.show()
                cur_line = tool_data.get("line_style", "solid")
                idx = self.combo_line_style.findData(cur_line)
                if idx >= 0:
                    self.combo_line_style.blockSignals(True)
                    self.combo_line_style.setCurrentIndex(idx)
                    self.combo_line_style.blockSignals(False)

            elif sub in ("rect", "circle"):
                if sub == "rect":
                    self.rect_sub_widget.show()
                    cur_round = tool_data.get("is_rounded", False)
                    idx = self.combo_rect_style.findData("rounded" if cur_round else "sharp")
                    if idx >= 0:
                        self.combo_rect_style.blockSignals(True)
                        self.combo_rect_style.setCurrentIndex(idx)
                        self.combo_rect_style.blockSignals(False)

                self.fill_widget.show()
                self.btn_outline.setText(tr("prop_outline", "Контур"))
                self.btn_filled.setText(tr("prop_filled", "Заливка"))
                filled = tool_data.get("filled", False)
                style_toggle_btn(self.btn_outline, not filled, self.is_dark)
                style_toggle_btn(self.btn_filled, filled, self.is_dark)

                if filled:
                    self.fill_options_widget.show()
                    is_grad = tool_data.get("is_gradient", False)
                    style_toggle_btn(self.btn_fill_solid, not is_grad, self.is_dark)
                    style_toggle_btn(self.btn_fill_gradient, is_grad, self.is_dark)

                    fill_alpha = tool_data.get("fill_alpha", 255)
                    pct = int(fill_alpha * 100 / 255)
                    self.slider_fill_opacity.blockSignals(True)
                    self.slider_fill_opacity.setValue(pct)
                    self.slider_fill_opacity.blockSignals(False)
                    self.lbl_fill_opacity.setText(tr("prop_fill_opacity", "Прозрачность заливки: {pct}%", pct=pct))

                    if is_grad:
                        self.gradient_colors_widget.show()
                        c1 = tool_data.get("gradient_color1", "#2ECC71")
                        c2 = tool_data.get("gradient_color2", "#00C0FF")
                        self.btn_grad1.setStyleSheet(f"background-color: {c1}; color: #fff; font-weight: bold; border-radius: 4px; border: 1px solid #fff;")
                        self.btn_grad2.setStyleSheet(f"background-color: {c2}; color: #fff; font-weight: bold; border-radius: 4px; border: 1px solid #fff;")
                    else:
                        self.gradient_colors_widget.hide()

            # Цвет контура / сплошной заливки
            cur_col = tool_data.get("color", "#2ECC71")
            if not (sub in ("rect", "circle") and tool_data.get("filled") and tool_data.get("is_gradient")):
                self.color_widget.show()
                self._highlight_color(cur_col)
                if str(cur_col).lower() == "mosaic":
                    g_val = tool_data.get("pixel_size", 8)
                    self.slider_grain.blockSignals(True)
                    self.slider_grain.setValue(g_val)
                    self.slider_grain.blockSignals(False)
                    self.lbl_grain.setText(tr("prop_mosaic_size", "Размер мозаики: {val} px", val=g_val))
                    self.lbl_grain.show()
                    self.slider_grain.show()
                elif str(cur_col).lower() == "blur":
                    b_val = tool_data.get("blur_radius", 15)
                    self.slider_blur.blockSignals(True)
                    self.slider_blur.setValue(b_val)
                    self.slider_blur.blockSignals(False)
                    self.lbl_blur.setText(tr("prop_blur_radius", "Степень размытия: {val} px", val=b_val))
                    self.lbl_blur.show()
                    self.slider_blur.show()

            self.slider_card.show()
            self.slider.show()
            self.lbl_size.show()
            val = tool_data.get("size", 4)
            self.slider.setRange(1, 24)
            self.slider.setValue(val)
            self.lbl_size.setText(tr("prop_stroke_width", "Толщина: {val} px", val=val))

        elif tool_type == ToolType.HIGHLIGHTER:
            self.highlighter_widget.show()
            alpha = tool_data.get("alpha", 90)
            pct = int(alpha * 100 / 255)
            self.slider_hl_opacity.blockSignals(True)
            self.slider_hl_opacity.setValue(pct)
            self.slider_hl_opacity.blockSignals(False)
            self.lbl_hl_opacity.setText(tr("prop_hl_opacity", "Непрозрачность маркера: {pct}%", pct=pct))

            self.color_widget.show()
            self._highlight_color(tool_data.get("color", "#FFD700"))

            self.slider_card.show()
            self.slider.show()
            self.lbl_size.show()
            val = tool_data.get("size", 18)
            self.slider.setRange(4, 48)
            self.slider.setValue(val)
            self.lbl_size.setText(tr("prop_highlighter_size", "Толщина маркера: {val} px", val=val))

        elif tool_type == ToolType.TEXT:
            self.text_widget.show()
            self.combo_font.blockSignals(True)
            self.combo_font.setCurrentText(tool_data.get("font_family", "Segoe UI"))
            self.combo_font.blockSignals(False)

            self.btn_bold.blockSignals(True)
            self.btn_bold.setChecked(tool_data.get("is_bold", True))
            style_toggle_btn(self.btn_bold, self.btn_bold.isChecked(), self.is_dark)
            self.btn_bold.blockSignals(False)

            self.btn_italic.blockSignals(True)
            self.btn_italic.setChecked(tool_data.get("is_italic", False))
            style_toggle_btn(self.btn_italic, self.btn_italic.isChecked(), self.is_dark)
            self.btn_italic.blockSignals(False)

            self.btn_underline.blockSignals(True)
            self.btn_underline.setChecked(tool_data.get("is_underline", False))
            style_toggle_btn(self.btn_underline, self.btn_underline.isChecked(), self.is_dark)
            self.btn_underline.blockSignals(False)

            has_bg = tool_data.get("has_bg", False)
            self.chk_flyout_text_bg.blockSignals(True)
            self.chk_flyout_text_bg.setChecked(has_bg)
            self.chk_flyout_text_bg.blockSignals(False)
            self.flyout_text_bg_opt.setVisible(has_bg)

            bg_pct = int(tool_data.get("bg_alpha", 180) * 100 / 255)
            self.slider_flyout_text_bg_alpha.blockSignals(True)
            self.slider_flyout_text_bg_alpha.setValue(bg_pct)
            self.lbl_flyout_text_bg_alpha.setText(tr("prop_text_bg_alpha", "Непрозрачность фона: {pct}%", pct=bg_pct))
            self.slider_flyout_text_bg_alpha.blockSignals(False)

            self.color_widget.show()
            self._highlight_color(tool_data.get("color", "#FFFFFF"))

            self.slider_card.show()
            self.slider.show()
            self.lbl_size.show()
            val = tool_data.get("size", 18)
            self.slider.setRange(10, 64)
            self.slider.setValue(val)
            self.lbl_size.setText(tr("prop_font_size", "Размер шрифта: {val} pt", val=val))

        elif tool_type == ToolType.PEN:
            self.color_widget.show()
            cur_col = tool_data.get("color", "#FF2E2E")
            self._highlight_color(cur_col)
            self.slider_card.show()
            self.slider.show()
            self.lbl_size.show()
            val = tool_data.get("size", 4)
            self.slider.setRange(1, 24)
            self.slider.setValue(val)
            self.lbl_size.setText(tr("prop_stroke_width", "Толщина: {val} px", val=val))
            if str(cur_col).lower() == "mosaic":
                g_val = tool_data.get("pixel_size", 8)
                self.slider_grain.blockSignals(True)
                self.slider_grain.setValue(g_val)
                self.slider_grain.blockSignals(False)
                self.lbl_grain.setText(tr("prop_mosaic_size", "Размер мозаики: {val} px", val=g_val))
                self.lbl_grain.show()
                self.slider_grain.show()
            elif str(cur_col).lower() == "blur":
                b_val = tool_data.get("blur_radius", 15)
                self.slider_blur.blockSignals(True)
                self.slider_blur.setValue(b_val)
                self.slider_blur.blockSignals(False)
                self.lbl_blur.setText(tr("prop_blur_radius", "Степень размытия: {val} px", val=b_val))
                self.lbl_blur.show()
                self.slider_blur.show()

        elif tool_type == ToolType.MOSAIC:
            self.censor_type_widget.show()
            self.slider_card.show()
            cmode = tool_data.get("censor_mode", "mosaic")
            style_toggle_btn(self.btn_mode_mosaic, cmode == "mosaic", self.is_dark)
            style_toggle_btn(self.btn_mode_blur, cmode == "blur", self.is_dark)
            if cmode == "blur":
                b_val = tool_data.get("blur_radius", 15)
                self.slider_blur.blockSignals(True)
                self.slider_blur.setValue(b_val)
                self.slider_blur.blockSignals(False)
                self.lbl_blur.setText(tr("prop_blur_radius", "Степень размытия: {val} px", val=b_val))
                self.lbl_blur.show()
                self.slider_blur.show()
            else:
                g_val = tool_data.get("size", 8)
                self.slider_grain.blockSignals(True)
                self.slider_grain.setValue(g_val)
                self.slider_grain.blockSignals(False)
                self.lbl_grain.setText(tr("prop_mosaic_size", "Размер мозаики: {val} px", val=g_val))
                self.lbl_grain.show()
                self.slider_grain.show()

        else:
            self.slider_card.show()
            self.lbl_size.show()
            self.lbl_size.setText(tr("prop_drag_area", "Перетаскивайте область мышью"))

        self.adjustSize()

    def _highlight_subshape(self, active_sub: str):
        for sub_k, btn in self.shape_sub_buttons.items():
            if sub_k == active_sub:
                btn.setStyleSheet("background-color: #2563eb; border: 1px solid #3b82f6; border-radius: 4px;")
            else:
                btn.setStyleSheet("background-color: #27272a; border: 1px solid #444; border-radius: 4px;")

    def _on_subshape_pick(self, subshape: str):
        self.tool_data["subshape"] = subshape
        self.load_tool(self.tool_type, self.tool_data)
        self.settings_updated.emit()

    def _set_filled(self, filled: bool):
        self.tool_data["filled"] = filled
        self.load_tool(self.tool_type, self.tool_data)
        self.settings_updated.emit()

    def _set_gradient(self, is_grad: bool):
        self.tool_data["is_gradient"] = is_grad
        self.load_tool(self.tool_type, self.tool_data)
        self.settings_updated.emit()

    def _on_fill_opacity_changed(self, val: int):
        self.lbl_fill_opacity.setText(tr("prop_fill_opacity", "Прозрачность заливки: {pct}%", pct=val))
        self.tool_data["fill_alpha"] = int(val * 255 / 100)
        self.settings_updated.emit()

    def _pick_gradient_color(self, idx: int):
        key = f"gradient_color{idx}"
        cur = self.tool_data.get(key, "#2ECC71" if idx == 1 else "#00C0FF")
        c = QColorDialog.getColor(QColor(cur), self, tr("shape_edit_grad_picker_title", "Выбор цвета {idx}", idx=idx))
        if c.isValid():
            self.tool_data[key] = c.name()
            self.load_tool(self.tool_type, self.tool_data)
            self.settings_updated.emit()

    def _on_hl_opacity_changed(self, val: int):
        self.lbl_hl_opacity.setText(tr("prop_hl_opacity", "Непрозрачность маркера: {pct}%", pct=val))
        self.tool_data["alpha"] = int(val * 255 / 100)
        self.settings_updated.emit()

    def _on_font_family_changed(self, fam: str):
        self.tool_data["font_family"] = fam
        self.settings_updated.emit()

    def _on_bold_toggled(self, checked: bool):
        self.tool_data["is_bold"] = checked
        style_toggle_btn(self.btn_bold, checked, self.is_dark)
        self.settings_updated.emit()

    def _on_underline_toggled(self, checked: bool):
        self.tool_data["is_underline"] = checked
        style_toggle_btn(self.btn_underline, checked, self.is_dark)
        self.settings_updated.emit()

    def _on_italic_toggled(self, checked: bool):
        self.tool_data["is_italic"] = checked
        style_toggle_btn(self.btn_italic, checked, self.is_dark)
        self.settings_updated.emit()

    def _on_pipette_clicked(self):
        self.close()
        self.pipette_requested.emit()

    def _on_arrow_combo_changed(self, idx: int):
        s_key = self.combo_arrow_style.itemData(idx)
        if s_key:
            self.tool_data["arrow_style"] = s_key
            self.settings_updated.emit()

    def _on_line_combo_changed(self, idx: int):
        s_key = self.combo_line_style.itemData(idx)
        if s_key:
            self.tool_data["line_style"] = s_key
            self.settings_updated.emit()

    def _on_rect_combo_changed(self, idx: int):
        s_key = self.combo_rect_style.itemData(idx)
        if s_key:
            self.tool_data["is_rounded"] = (s_key == "rounded")
            self.settings_updated.emit()

    def _on_flyout_text_bg_toggled(self, checked: bool):
        self.tool_data["has_bg"] = checked
        self.flyout_text_bg_opt.setVisible(checked)
        self.settings_updated.emit()

    def _on_flyout_text_bg_alpha_changed(self, val: int):
        self.lbl_flyout_text_bg_alpha.setText(tr("prop_text_bg_alpha", "Непрозрачность фона: {pct}%", pct=val))
        self.tool_data["bg_alpha"] = int(val * 255 / 100)
        self.settings_updated.emit()

    def _set_censor_mode(self, mode: str):
        self.tool_data["censor_mode"] = mode
        self.load_tool(self.tool_type, self.tool_data)
        self.settings_updated.emit()

    def _on_grain_slider_changed(self, val: int):
        self.lbl_grain.setText(tr("prop_mosaic_size", "Размер мозаики: {val} px", val=val))
        self.tool_data["pixel_size"] = val
        if self.tool_type == ToolType.MOSAIC:
            self.tool_data["size"] = val
        self.settings_updated.emit()

    def _on_blur_slider_changed(self, val: int):
        self.lbl_blur.setText(tr("prop_blur_radius", "Степень размытия: {val} px", val=val))
        self.tool_data["blur_radius"] = val
        self.settings_updated.emit()

    def _highlight_color(self, current_color):
        current_color = str(current_color).lower()
        for col, btn in self.color_buttons.items():
            if col.lower() == current_color:
                if col in ("mosaic", "blur"):
                    btn.setStyleSheet("background-color: #3b82f6; border-radius: 11px; border: 2px solid #ffffff;")
                else:
                    btn.setStyleSheet(f"background-color: {col}; border-radius: 11px; border: 2px solid #ffffff;")
            else:
                if col in ("mosaic", "blur"):
                    btn.setStyleSheet("background-color: #27272a; border-radius: 11px; border: 1px solid #555;")
                else:
                    btn.setStyleSheet(f"background-color: {col}; border-radius: 11px; border: 1px solid #444;")

    def _on_color_pick(self, color):
        self.tool_data["color"] = color
        self._highlight_color(color)
        if str(color).lower() == "mosaic":
            g_val = self.tool_data.get("pixel_size", 8)
            self.slider_grain.blockSignals(True)
            self.slider_grain.setValue(g_val)
            self.slider_grain.blockSignals(False)
            self.lbl_grain.setText(tr("prop_mosaic_size", "Размер мозаики: {val} px", val=g_val))
            self.lbl_grain.show()
            self.slider_grain.show()
            self.lbl_blur.hide()
            self.slider_blur.hide()
        elif str(color).lower() == "blur":
            b_val = self.tool_data.get("blur_radius", 15)
            self.slider_blur.blockSignals(True)
            self.slider_blur.setValue(b_val)
            self.slider_blur.blockSignals(False)
            self.lbl_blur.setText(tr("prop_blur_radius", "Степень размытия: {val} px", val=b_val))
            self.lbl_blur.show()
            self.slider_blur.show()
            self.lbl_grain.hide()
            self.slider_grain.hide()
        else:
            self.lbl_grain.hide()
            self.slider_grain.hide()
            self.lbl_blur.hide()
            self.slider_blur.hide()
        self.adjustSize()
        self.settings_updated.emit()

    def _open_custom_dialog(self):
        cur = self.tool_data.get("color", "#FF2E2E")
        c = QColorDialog.getColor(QColor(cur), self, tr("prop_color_custom", "Выбрать произвольный цвет"))
        if c.isValid():
            self._on_color_pick(c.name())

    def _on_slider_changed(self, val):
        self.tool_data["size"] = val
        if self.tool_type == ToolType.MOSAIC:
            self.lbl_size.setText(tr("prop_mosaic_size", "Размер мозаики: {val} px", val=val))
        elif self.tool_type == ToolType.TEXT:
            self.lbl_size.setText(tr("prop_font_size", "Размер шрифта: {val} pt", val=val))
        elif self.tool_type == ToolType.HIGHLIGHTER:
            self.lbl_size.setText(tr("prop_highlighter_size", "Толщина маркера: {val} px", val=val))
        else:
            self.lbl_size.setText(tr("prop_stroke_width", "Толщина: {val} px", val=val))
        self.settings_updated.emit()


class ShapesFlyoutWidget(QFrame):
    """
    Боковая выпадающая палитра фигур в стиле Adobe Photoshop / Figma.
    Компактная колонка иконок без текста. Подсказка инструмента отображается при наведении.
    """
    shape_chosen = pyqtSignal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["popup_frame"] + f"""
            QPushButton {{
                border-radius: 4px;
                border: 1px solid transparent;
                background-color: transparent;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {theme['popup_item_hover']};
                border-color: #3b82f6;
            }}
            QPushButton:pressed {{
                background-color: #1d4ed8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        shapes_list = [
            ("arrow", "arrow", tr("shape_arrow", "Стрелка"), {"subshape": "arrow"}),
            ("line", "line", tr("shape_line", "Прямая линия"), {"subshape": "line"}),
            ("rect", "rect", tr("shape_rect", "Прямоугольник (контур)"), {"subshape": "rect", "filled": False, "is_rounded": False}),
            ("rect_rounded", "rect", tr("shape_rect_rounded", "Скруглённый прямоугольник"), {"subshape": "rect", "filled": False, "is_rounded": True}),
            ("filled_rect", "filled_rect", tr("shape_filled_rect", "Залитый прямоугольник"), {"subshape": "rect", "filled": True, "is_rounded": False}),
            ("circle", "circle", tr("shape_circle", "Круг / Овал (контур)"), {"subshape": "circle", "filled": False}),
            ("circle_filled", "circle", tr("shape_circle_filled", "Залитый круг / Овал"), {"subshape": "circle", "filled": True}),
        ]

        for s_id, ico_name, title, opts in shapes_list:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setIcon(create_themed_icon(ico_name, self.is_dark, size=16))
            btn.setIconSize(QSize(16, 16))
            btn.setToolTip(title)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, sid=s_id, o=opts: self._on_select(sid, o))
            layout.addWidget(btn)

    def _on_select(self, sid: str, opts: dict):
        self.hide()
        self.shape_chosen.emit(sid, opts)


class CaptureMaskFlyoutWidget(QFrame):
    """Выбор формы маски записи: произвольный контур, прямоугольник или овал."""
    mask_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["popup_frame"] + """
            QPushButton { border-radius: 4px; border: 1px solid transparent; background: transparent; padding: 4px; }
            QPushButton:hover { background: #27272a; border-color: #3b82f6; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        for kind, icon, title in (
            ("freeform", "pen", tr("capture_mask_freeform", "Произвольный контур")),
            ("rect", "rect", tr("capture_mask_rect", "Прямоугольная маска")),
            ("circle", "circle", tr("capture_mask_circle", "Овальная маска")),
        ):
            button = QPushButton()
            button.setFixedSize(28, 28)
            button.setIcon(create_themed_icon(icon, self.is_dark, size=16))
            button.setIconSize(QSize(16, 16))
            button.setToolTip(title)
            button.clicked.connect(lambda checked, value=kind: self._choose(value))
            layout.addWidget(button)

    def _choose(self, kind: str):
        self.hide()
        self.mask_chosen.emit(kind)


class CensorEffectsFlyoutWidget(QFrame):
    """
    Боковое выпадающее меню региональных эффектов и цензуры в стиле Adobe Photoshop / Figma.
    Компактная колонка иконок без текста. Инструмент применяется к выделяемой прямоугольной области.
    """
    censor_chosen = pyqtSignal(str)
    filter_chosen = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["popup_frame"] + f"""
            QPushButton {{
                border-radius: 4px;
                border: 1px solid transparent;
                background-color: transparent;
                padding: 4px;
            }}
            QPushButton:hover {{
                background-color: {theme['popup_item_hover']};
                border-color: #3b82f6;
            }}
            QPushButton:pressed {{
                background-color: #1d4ed8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)

        censor_items = [
            ("normal", "layers", tr("censor_normal", "Обычный — убирает эффекты нижних слоёв в этой области")),
            ("mosaic", "mosaic", tr("censor_mosaic", "Мозаика (Пикселизация области)")),
            ("blur", "blur", tr("censor_blur", "Размытие (Блюр области)")),
            ("grayscale", "grayscale", tr("censor_grayscale", "Чёрно-белый (Grayscale области)")),
            ("invert", "invert", tr("censor_invert", "Инверсия цветов (Область)")),
            ("vibrant", "vibrant", tr("censor_vibrant", "Повышенная контрастность / Насыщенность (Область)")),
            ("sepia", "sepia", tr("censor_sepia", "Тёплая сепия (Винтаж области)")),
        ]

        for c_id, ico_name, title in censor_items:
            btn = QPushButton()
            btn.setFixedSize(28, 28)
            btn.setIcon(create_themed_icon(ico_name, self.is_dark, size=16))
            btn.setIconSize(QSize(16, 16))
            btn.setToolTip(title)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked, cid=c_id: self._on_censor_click(cid))
            layout.addWidget(btn)

    def _on_censor_click(self, cid: str):
        self.hide()
        self.censor_chosen.emit(cid)

    def _on_filter_click(self, ft: str):
        self.hide()
        self.filter_chosen.emit(ft)


class RightDrawingToolbar(QFrame):
    tool_changed = pyqtSignal(str)
    tool_settings_updated = pyqtSignal()
    layers_clicked = pyqtSignal()
    history_clicked = pyqtSignal()
    capture_mask_chosen = pyqtSignal(str)
    undo_clicked = pyqtSignal()
    redo_clicked = pyqtSignal()
    filter_selected = pyqtSignal(str)
    pipette_requested = pyqtSignal()
    passthrough_toggled = pyqtSignal(bool)

    def enterEvent(self, event):
        """Поднимает боковую панель над нижней при наведении."""
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "_raise_toolbar"):
            parent._raise_toolbar(self)
        super().enterEvent(event)

    def __init__(self, parent=None, show_labels: bool = False):
        super().__init__(parent)
        self.show_labels = bool(show_labels)
        self.current_tool = ToolType.MOVE

        self.tools_config = {
            ToolType.MOVE: {},
            ToolType.SELECT: {},
            ToolType.PEN: {"color": "#FF2E2E", "size": 4},
            ToolType.SHAPES: {
                "subshape": "arrow",
                "color": "#2ECC71",
                "size": 4,
                "arrow_style": "barbed",
                "filled": True,
                "fill_alpha": 255,
                "is_gradient": False,
                "gradient_color1": "#2ECC71",
                "gradient_color2": "#00C0FF"
            },
            ToolType.HIGHLIGHTER: {"color": "#FFD700", "size": 18, "alpha": 90},
            ToolType.TEXT: {
                "color": "#FFFFFF",
                "size": 18,
                "font_family": "Segoe UI",
                "is_bold": True,
                "is_italic": False,
                "is_underline": False,
                "has_bg": False,
                "bg_color": "#000000",
                "bg_alpha": 180
            },
            ToolType.MOSAIC: {"size": 8, "censor_mode": "mosaic", "blur_radius": 15},
            ToolType.CAPTURE_MASK: {"mask_kind": "freeform"},
        }

        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["panel_frame"] + theme["button_base"])
        if self.show_labels:
            self.setFixedWidth(136)
            self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        else:
            self.setFixedWidth(34)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4 if self.show_labels else 3, 4 if self.show_labels else 3, 4 if self.show_labels else 3, 4 if self.show_labels else 3)
        layout.setSpacing(3 if self.show_labels else 2)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)
        self.tool_buttons = {}

        # 1. Основные инструменты рисования
        labels = {
            ToolType.MOVE: tr("tool_lbl_cursor", "Курсор"),
            ToolType.SELECT: tr("tool_lbl_select", "Выделение"),
            ToolType.PEN: tr("tool_lbl_pen", "Карандаш"),
            ToolType.HIGHLIGHTER: tr("tool_lbl_highlighter", "Маркер"),
            ToolType.SHAPES: tr("tool_lbl_shapes", "Фигуры"),
            ToolType.CAPTURE_MASK: tr("tool_lbl_mask", "Маска"),
            ToolType.TEXT: tr("tool_lbl_text", "Текст"),
        }
        drawing_tools = [
            (ToolType.MOVE, "move", tr("tool_move", "Перемещение / изменение рамки")),
            (ToolType.SELECT, "select", tr("tool_select", "Выделение объектов (выбор фигур рамкой или кликом)")),
            (ToolType.PEN, "pen", tr("tool_pen", "Карандаш")),
            (ToolType.HIGHLIGHTER, "highlighter", tr("tool_highlighter", "Маркер-хайлайтер")),
            (ToolType.SHAPES, "shapes", tr("tool_shapes", "Фигуры (Линия, Стрелка, Прямоугольник, Круг)")),
            (ToolType.CAPTURE_MASK, "mask", tr("tool_capture_mask", "Маска области записи")),
            (ToolType.TEXT, "text", tr("tool_text", "Текст"))
        ]

        for t_type, ico_name, tip in drawing_tools:
            btn_txt = f"  {labels.get(t_type, '')}" if self.show_labels else ""
            btn = ModernButton(btn_txt, tip)
            btn.setCheckable(True)
            if self.show_labels:
                btn.setFixedSize(126, 26)
                btn.setStyleSheet("""
                    QPushButton {
                        text-align: left;
                        padding-left: 8px;
                        font-size: 11px;
                        font-weight: 500;
                    }
                """)
                btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
            else:
                btn.setFixedSize(26, 26)
            btn.setIcon(create_themed_icon(ico_name, self.is_dark, size=16))
            btn.setIconSize(QSize(16, 16))
            if t_type == ToolType.MOVE:
                btn.setChecked(True)
            self.button_group.addButton(btn)
            self.tool_buttons[t_type] = btn
            if t_type == ToolType.SHAPES:
                btn.clicked.connect(lambda checked: self._toggle_shapes_flyout())
            elif t_type == ToolType.CAPTURE_MASK:
                btn.clicked.connect(lambda checked: self._toggle_capture_mask_flyout())
            else:
                btn.clicked.connect(lambda checked, t=t_type: self.select_tool(t, show_options=False))
            layout.addWidget(btn)

        # Разделитель 1
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet(f"background-color: {theme['sep_color']}; max-height: 1px;")
        layout.addWidget(sep1)

        # 2. Квадратный индикатор цвета / свойств в стиле Lightshot
        self.btn_color_swatch = QPushButton()
        self.btn_color_swatch.setObjectName("colorSwatchBtn")
        if self.show_labels:
            self.btn_color_swatch.setFixedSize(126, 26)
            self.btn_color_swatch.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        else:
            self.btn_color_swatch.setFixedSize(22, 22)
        self.btn_color_swatch.setToolTip(tr("action_color_swatch", "Палитра, цвет и свойства инструмента"))
        self.btn_color_swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_color_swatch.clicked.connect(self._toggle_properties_flyout)
        self._update_color_swatch()
        layout.addWidget(self.btn_color_swatch, alignment=Qt.AlignmentFlag.AlignCenter)

        # Защита зоны перенесена в шапку записи, сохраняем скрытый виджет для совместимости
        self.chk_passthrough = ModernButton("", "")
        self.chk_passthrough.hide()

        # Дополнительные действия панели скрыты, чтобы маленькие зоны не
        # перекрывались боковой панелью. Кнопка остаётся компактной (полукнопка)
        # и раскрывает их по запросу.
        self._more_tools_expanded = False
        self._advanced_tool_widgets = []
        more_txt = f"  {tr('tool_lbl_more', 'Ещё')}" if self.show_labels else ""
        self.btn_more_tools = ModernButton(more_txt, tr("tool_more", "Дополнительные инструменты"))
        if self.show_labels:
            self.btn_more_tools.setFixedSize(126, 20)
            self.btn_more_tools.setStyleSheet("text-align: center; font-size: 10px; font-weight: 500;")
            self.btn_more_tools.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        else:
            self.btn_more_tools.setFixedSize(26, 14)
        self.btn_more_tools.setIcon(create_themed_icon("more", self.is_dark, size=14))
        self.btn_more_tools.setIconSize(QSize(14, 14))
        self.btn_more_tools.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more_tools.clicked.connect(self._toggle_more_tools)
        layout.addWidget(self.btn_more_tools, alignment=Qt.AlignmentFlag.AlignCenter)

        # Разделитель 2
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet(f"background-color: {theme['sep_color']}; max-height: 1px;")
        layout.addWidget(sep2)
        self._advanced_tool_widgets.append(sep2)

        # 3. Отмена и повтор
        undo_redo = [
            ("undo", tr("tool_undo", "Отмена (Ctrl+Z)"), tr("tool_lbl_undo", "Отмена"), self.undo_clicked.emit),
            ("redo", tr("tool_redo", "Повтор (Ctrl+Y)"), tr("tool_lbl_redo", "Повтор"), self.redo_clicked.emit),
        ]
        for ico_name, tip, lbl, handler in undo_redo:
            btn_txt = f"  {lbl}" if self.show_labels else ""
            btn = ModernButton(btn_txt, tip)
            if self.show_labels:
                btn.setFixedSize(126, 26)
                btn.setStyleSheet("text-align: left; padding-left: 8px; font-size: 11px; font-weight: 500;")
                btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
            else:
                btn.setFixedSize(26, 26)
            btn.setIcon(create_themed_icon(ico_name, self.is_dark, size=16))
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(handler)
            layout.addWidget(btn)
            self._advanced_tool_widgets.append(btn)

        # Разделитель 3
        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.HLine)
        sep3.setStyleSheet(f"background-color: {theme['sep_color']}; max-height: 1px;")
        layout.addWidget(sep3)
        self._advanced_tool_widgets.append(sep3)

        # 4. Инструмент «Цензура и фильтры» (боковой флайаут)
        mosaic_txt = f"  {tr('tool_lbl_censor', 'Цензура')}" if self.show_labels else ""
        btn_mosaic = ModernButton(mosaic_txt, tr("action_censor_tip", "Цензура и фильтры (Мозаика / Размытие / Эффекты)"))
        btn_mosaic.setCheckable(True)
        if self.show_labels:
            btn_mosaic.setFixedSize(126, 26)
            btn_mosaic.setStyleSheet("text-align: left; padding-left: 8px; font-size: 11px; font-weight: 500;")
            btn_mosaic.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        else:
            btn_mosaic.setFixedSize(26, 26)
        btn_mosaic.setIcon(create_themed_icon("mosaic", self.is_dark, size=16))
        btn_mosaic.setIconSize(QSize(16, 16))
        self.button_group.addButton(btn_mosaic)
        self.tool_buttons[ToolType.MOSAIC] = btn_mosaic
        btn_mosaic.clicked.connect(lambda checked: self._toggle_censor_flyout())
        layout.addWidget(btn_mosaic)
        self._advanced_tool_widgets.append(btn_mosaic)

        # Разделитель 4
        sep4 = QFrame()
        sep4.setFrameShape(QFrame.Shape.HLine)
        sep4.setStyleSheet(f"background-color: {theme['sep_color']}; max-height: 1px;")
        layout.addWidget(sep4)
        self._advanced_tool_widgets.append(sep4)

        # 5. Слои и история действий
        layer_history = [
            ("layers", tr("action_layers", "Управление слоями"), tr("action_lbl_layers", "Слои"), self.layers_clicked.emit),
            ("history", tr("action_history", "История действий"), tr("action_lbl_history", "История"), self.history_clicked.emit)
        ]
        for ico_name, tip, lbl, handler in layer_history:
            btn_txt = f"  {lbl}" if self.show_labels else ""
            btn = ModernButton(btn_txt, tip)
            if self.show_labels:
                btn.setFixedSize(126, 26)
                btn.setStyleSheet("text-align: left; padding-left: 8px; font-size: 11px; font-weight: 500;")
                btn.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
            else:
                btn.setFixedSize(26, 26)
            btn.setIcon(create_themed_icon(ico_name, self.is_dark, size=16))
            btn.setIconSize(QSize(16, 16))
            btn.clicked.connect(handler)
            layout.addWidget(btn)
            self._advanced_tool_widgets.append(btn)

        self.properties_flyout = ToolPropertiesFlyout(self)
        self.properties_flyout.settings_updated.connect(self._on_settings_updated)
        self.properties_flyout.pipette_requested.connect(self.pipette_requested.emit)

        self.shapes_flyout = ShapesFlyoutWidget(self)
        self.shapes_flyout.shape_chosen.connect(self._on_shapes_flyout_chosen)

        self.capture_mask_flyout = CaptureMaskFlyoutWidget(self)
        self.capture_mask_flyout.mask_chosen.connect(self._on_capture_mask_chosen)

        self.censor_flyout = CensorEffectsFlyoutWidget(self)
        self.censor_flyout.censor_chosen.connect(self._on_censor_chosen)
        self.censor_flyout.filter_chosen.connect(self._on_filter_chosen)
        self._set_more_tools_visible(False)

    def _set_more_tools_visible(self, expanded: bool):
        self._more_tools_expanded = bool(expanded)
        for widget in getattr(self, "_advanced_tool_widgets", []):
            widget.setVisible(self._more_tools_expanded)
        if hasattr(self, "btn_more_tools"):
            self.btn_more_tools.setIcon(create_themed_icon(
                "chevron_up" if self._more_tools_expanded else "more",
                self.is_dark,
                size=14,
            ))
        if self.layout() is not None:
            self.layout().activate()
        self.adjustSize()

    def _toggle_more_tools(self):
        new_expanded = not self._more_tools_expanded
        self._user_expanded_tools = new_expanded
        self._set_more_tools_visible(new_expanded)
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "_update_toolbar_positions"):
            parent._update_toolbar_positions()

    @property
    def current_color(self) -> str:
        cfg = self.tools_config.get(self.current_tool, {})
        return cfg.get("color", "#FF2E2E")

    @current_color.setter
    def current_color(self, val: str):
        if self.current_tool in self.tools_config:
            self.tools_config[self.current_tool]["color"] = val

    def set_tool_color(self, color: str):
        if self.current_tool in self.tools_config:
            self.tools_config[self.current_tool]["color"] = color
        self._update_color_swatch()
        self.properties_flyout.load_tool(self.current_tool, self.tools_config.get(self.current_tool, {}))
        self.tool_settings_updated.emit()

    def _on_color_chosen(self, color: str):
        self.set_tool_color(color)

    def _update_color_swatch(self):
        cfg = self.tools_config.get(self.current_tool, {})
        col = cfg.get("color", "#FF2E2E")
        border_col = "#ffffff" if self.is_dark else "#52525b"

        is_grad = False
        if self.current_tool == ToolType.SHAPES:
            sub = cfg.get("subshape", "rect")
            if sub in ("rect", "circle") and cfg.get("filled") and cfg.get("is_gradient"):
                is_grad = True
                c1 = cfg.get("gradient_color1", "#2ECC71")
                c2 = cfg.get("gradient_color2", "#00C0FF")

        if getattr(self, "show_labels", False):
            # В режиме с подписями создаём миниатюру цвета в иконке и пишем "Свойства"
            ico = None
            if is_grad:
                pm = QPixmap(14, 14)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                grad = QLinearGradient(0, 0, 14, 14)
                grad.setColorAt(0, QColor(c1))
                grad.setColorAt(1, QColor(c2))
                p.setBrush(QBrush(grad))
                p.setPen(QPen(QColor(border_col), 1))
                p.drawRoundedRect(QRectF(0.5, 0.5, 13, 13), 2, 2)
                p.end()
                ico = QIcon(pm)
            elif str(col).lower() == "blur" or (self.current_tool == ToolType.MOSAIC and cfg.get("censor_mode") == "blur"):
                ico = create_themed_icon("blur", self.is_dark, size=14)
            elif str(col).lower() == "mosaic" or self.current_tool == ToolType.MOSAIC:
                ico = create_themed_icon("mosaic", self.is_dark, size=14)
            else:
                pm = QPixmap(14, 14)
                pm.fill(Qt.GlobalColor.transparent)
                p = QPainter(pm)
                p.setRenderHint(QPainter.RenderHint.Antialiasing)
                p.setBrush(QBrush(QColor(col)))
                p.setPen(QPen(QColor(border_col), 1))
                p.drawRoundedRect(QRectF(0.5, 0.5, 13, 13), 2, 2)
                p.end()
                ico = QIcon(pm)

            self.btn_color_swatch.setIcon(ico if ico is not None else QIcon())
            self.btn_color_swatch.setIconSize(QSize(14, 14))
            self.btn_color_swatch.setText(f"  {tr('tool_lbl_properties', 'Свойства')}")
            self.btn_color_swatch.setStyleSheet("""
                QPushButton#colorSwatchBtn {
                    background-color: #27272a;
                    border: 1px solid #3f3f46;
                    border-radius: 4px;
                    color: #f4f4f5;
                    font-size: 11px;
                    font-weight: 500;
                    text-align: left;
                    padding-left: 8px;
                }
                QPushButton#colorSwatchBtn:hover {
                    background-color: #3f3f46;
                    border-color: #38bdf8;
                }
            """)
            return

        if is_grad:
            self.btn_color_swatch.setIcon(QIcon())
            self.btn_color_swatch.setStyleSheet(f"""
                QPushButton#colorSwatchBtn {{
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 {c1}, stop:1 {c2});
                    border: 1px solid {border_col};
                    border-radius: 3px;
                }}
                QPushButton#colorSwatchBtn:hover {{
                    border: 2px solid #38bdf8;
                }}
            """)
            return

        if str(col).lower() == "blur" or (self.current_tool == ToolType.MOSAIC and cfg.get("censor_mode") == "blur"):
            self.btn_color_swatch.setIcon(create_themed_icon("blur", self.is_dark, size=14))
            self.btn_color_swatch.setIconSize(QSize(14, 14))
            self.btn_color_swatch.setStyleSheet(f"""
                QPushButton#colorSwatchBtn {{
                    background-color: #27272a;
                    border: 1px solid {border_col};
                    border-radius: 3px;
                }}
                QPushButton#colorSwatchBtn:hover {{
                    border: 2px solid #38bdf8;
                }}
            """)
        elif str(col).lower() == "mosaic" or self.current_tool == ToolType.MOSAIC:
            self.btn_color_swatch.setIcon(create_themed_icon("mosaic", self.is_dark, size=14))
            self.btn_color_swatch.setIconSize(QSize(14, 14))
            self.btn_color_swatch.setStyleSheet(f"""
                QPushButton#colorSwatchBtn {{
                    background-color: #27272a;
                    border: 1px solid {border_col};
                    border-radius: 3px;
                }}
                QPushButton#colorSwatchBtn:hover {{
                    border: 2px solid #38bdf8;
                }}
            """)
        else:
            self.btn_color_swatch.setIcon(QIcon())
            self.btn_color_swatch.setStyleSheet(f"""
                QPushButton#colorSwatchBtn {{
                    background-color: {col};
                    border: 1px solid {border_col};
                    border-radius: 3px;
                }}
                QPushButton#colorSwatchBtn:hover {{
                    border: 2px solid #38bdf8;
                }}
            """)

    def _on_settings_updated(self):
        self._update_color_swatch()
        self.tool_settings_updated.emit()

    def select_tool(self, tool_type: str, show_options: bool = False):
        self.current_tool = tool_type
        if tool_type in self.tool_buttons:
            self.tool_buttons[tool_type].setChecked(True)
        self._update_color_swatch()
        self.tool_changed.emit(tool_type)

        # Для инструментов «Текст» и «Мозаика» при явном запросе открываем панель параметров.
        # Для инструмента «Фигуры» панель параметров автоматически НЕ открывается.
        if (tool_type in (ToolType.TEXT, ToolType.MOSAIC) and show_options) or (tool_type not in (ToolType.MOVE, ToolType.SELECT, ToolType.SHAPES) and show_options):
            self._show_properties_flyout()
        else:
            self.properties_flyout.hide()

    def get_current_settings(self) -> dict:
        return self.tools_config.get(self.current_tool, {})

    def _toggle_properties_flyout(self):
        if self.properties_flyout.isVisible():
            self.properties_flyout.hide()
        else:
            self._show_properties_flyout()

    def _toggle_shapes_flyout(self):
        if self.shapes_flyout.isVisible():
            self.shapes_flyout.hide()
        else:
            if hasattr(self, "censor_flyout") and self.censor_flyout.isVisible():
                self.censor_flyout.hide()
            if hasattr(self, "properties_flyout") and self.properties_flyout.isVisible():
                self.properties_flyout.hide()
            show_side_smart_popup(self.tool_buttons[ToolType.SHAPES], self.shapes_flyout)

    def _toggle_capture_mask_flyout(self):
        if self.capture_mask_flyout.isVisible():
            self.capture_mask_flyout.hide()
        else:
            if self.shapes_flyout.isVisible():
                self.shapes_flyout.hide()
            if self.properties_flyout.isVisible():
                self.properties_flyout.hide()
            show_side_smart_popup(self.tool_buttons[ToolType.CAPTURE_MASK], self.capture_mask_flyout)

    def _on_capture_mask_chosen(self, kind: str):
        cfg = self.tools_config.setdefault(ToolType.CAPTURE_MASK, {})
        cfg["mask_kind"] = kind
        self.select_tool(ToolType.CAPTURE_MASK, show_options=False)
        self.capture_mask_chosen.emit(kind)

    def _toggle_censor_flyout(self):
        if self.censor_flyout.isVisible():
            self.censor_flyout.hide()
        else:
            if hasattr(self, "shapes_flyout") and self.shapes_flyout.isVisible():
                self.shapes_flyout.hide()
            if hasattr(self, "properties_flyout") and self.properties_flyout.isVisible():
                self.properties_flyout.hide()
            show_side_smart_popup(self.tool_buttons[ToolType.MOSAIC], self.censor_flyout)

    def _on_shapes_flyout_chosen(self, sid: str, opts: dict):
        cfg = self.tools_config.setdefault(ToolType.SHAPES, {})
        cfg.update(opts)
        self.select_tool(ToolType.SHAPES, show_options=False)
        self.properties_flyout.hide()
        self.tool_settings_updated.emit()

    def _on_censor_chosen(self, cid: str):
        cfg = self.tools_config.setdefault(ToolType.MOSAIC, {})
        cfg["censor_mode"] = cid
        self.select_tool(ToolType.MOSAIC, show_options=True)
        self.tool_settings_updated.emit()

    def _on_filter_chosen(self, f_type: str):
        self.filter_selected.emit(f_type)

    def _show_properties_flyout(self):
        target_tool = self.current_tool
        p = self.parent()
        if p and hasattr(p, "transform_box") and p.transform_box.is_active():
            act_s = p.transform_box.shape
            if act_s and type(act_s).__name__ == "TextShape":
                target_tool = ToolType.TEXT
        elif p and getattr(p, "last_active_shape", None):
            if type(p.last_active_shape).__name__ == "TextShape":
                target_tool = ToolType.TEXT

        cfg = self.tools_config.setdefault(target_tool, {})
        self.properties_flyout.load_tool(target_tool, cfg)
        anchor = self.tool_buttons.get(target_tool, self.btn_color_swatch)
        show_side_smart_popup(anchor, self.properties_flyout)


class RegionActionHeader(QFrame):
    """Компактная верхняя панель добавления зон и массовых действий."""

    add_mode_toggled = pyqtSignal(bool)
    save_clicked = pyqtSignal(str)
    copy_clicked = pyqtSignal(str)
    record_video_started = pyqtSignal(dict)
    record_gif_started = pyqtSignal(dict)
    mass_filter_selected = pyqtSignal(str)
    mass_filter_parameters_changed = pyqtSignal(dict)
    close_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["panel_frame"] + theme["button_base"] + """
            QLabel {
                color: #e4e4e7;
                font-size: 11px;
                font-weight: 600;
                background: transparent;
                border: none;
            }
        """)
        self.setFixedHeight(38)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(3)

        self.lbl_title = QLabel(tr("region_header_title", "Зоны · массовые действия"))
        layout.addWidget(self.lbl_title)

        self.btn_add = ModernButton("", tr("action_add_region", "Добавить зону выделения (+ / Ctrl)"))
        self.btn_add.setCheckable(True)
        self.btn_add.setFixedSize(28, 28)
        self.btn_add.setIcon(create_themed_icon("add_region", self.is_dark, size=16, custom_color="#93c5fd"))
        self.btn_add.setIconSize(QSize(16, 16))
        self.btn_add.clicked.connect(self.add_mode_toggled.emit)
        layout.addWidget(self.btn_add)

        self.btn_close = ModernButton("", tr("action_region_header_close", "Отменить добавление / закрыть выделения"))
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setIcon(create_themed_icon("close", self.is_dark, size=16, custom_color="#fca5a5"))
        self.btn_close.setIconSize(QSize(16, 16))
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #3f1d1d;
                border: 1px solid #7f1d1d;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #ef4444;
            }
        """)
        self.btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.btn_close)

        self.btn_mass_save = ModernButton(tr("region_mass_save_short", "Сохранить"), tr("action_all_save", "Сохранить скриншоты всех зон"))
        self.btn_mass_save.setIcon(create_themed_icon("save", self.is_dark, size=14))
        self.btn_mass_save.clicked.connect(self._show_save_formats)
        layout.addWidget(self.btn_mass_save)

        self.btn_mass_copy = ModernButton(tr("region_mass_copy_short", "Копировать"), tr("action_all_copy", "Скопировать все зоны"))
        self.btn_mass_copy.setIcon(create_themed_icon("copy", self.is_dark, size=14))
        self.btn_mass_copy.clicked.connect(self._show_copy_formats)
        layout.addWidget(self.btn_mass_copy)

        self.btn_mass_video = ModernButton(tr("region_mass_video_short", "Видео"), tr("action_all_video", "Записывать видео всех зон"))
        self.btn_mass_video.setIcon(create_themed_icon("video", self.is_dark, size=14))
        self.btn_mass_video.clicked.connect(self._show_video_popup)
        layout.addWidget(self.btn_mass_video)

        self.btn_mass_gif = ModernButton("GIF", tr("action_all_gif", "Записывать GIF всех зон"))
        self.btn_mass_gif.setIcon(create_themed_icon("gif", self.is_dark, size=14))
        self.btn_mass_gif.clicked.connect(self._show_gif_popup)
        layout.addWidget(self.btn_mass_gif)

        self.btn_mass_filter = ModernButton("", tr("action_all_filter", "Эффект для всех зон"))
        self.btn_mass_filter.setFixedSize(28, 28)
        self.btn_mass_filter.setIcon(create_themed_icon("filter", self.is_dark, size=15))
        self.btn_mass_filter.setIconSize(QSize(15, 15))
        self.btn_mass_filter.clicked.connect(self._show_filter_popup)
        layout.addWidget(self.btn_mass_filter)

        self.popup_formats = ScreenshotFormatPopup(self)
        self.popup_formats.format_selected.connect(self.save_clicked.emit)
        self.popup_copy = CopyFormatPopup(self)
        self.popup_copy.format_selected.connect(self.copy_clicked.emit)
        self.popup_video = VideoOptionsPopup(self)
        self.popup_video.start_video.connect(self.record_video_started.emit)
        self.popup_gif = GifOptionsPopup(self)
        self.popup_gif.start_gif.connect(self.record_gif_started.emit)
        self.popup_filter = None
        self.mass_filter = FilterType.NONE
        self.mass_filter_selection = FilterType.NONE
        self.mass_filter_params = {"blur_radius": 15, "pixel_size": 12}

    def set_region_state(self, add_mode: bool, total_count: int, available_count: int):
        self.btn_add.setChecked(bool(add_mode))
        if add_mode:
            self.lbl_title.setText(
                tr("action_add_region_active", "Режим добавления зон включён — выделите следующую область")
            )
        else:
            self.lbl_title.setText(
                tr("region_header_title_count", "Зоны · массовые действия ({available}/{total})", available=available_count, total=total_count)
            )
        enabled = available_count > 0
        for button in (self.btn_mass_save, self.btn_mass_copy, self.btn_mass_video, self.btn_mass_gif, self.btn_mass_filter):
            button.setEnabled(enabled)
        self.setVisible(bool(add_mode or total_count > 1))

    def _show_save_formats(self):
        show_smart_popup(self.btn_mass_save, self.popup_formats)

    def _show_copy_formats(self):
        show_smart_popup(self.btn_mass_copy, self.popup_copy)

    def _show_video_popup(self):
        show_smart_popup(self.btn_mass_video, self.popup_video)

    def _show_gif_popup(self):
        show_smart_popup(self.btn_mass_gif, self.popup_gif)

    def _show_filter_popup(self):
        if self.popup_filter is None:
            self.popup_filter = WholeAreaFilterPopup(
                self,
                title=tr("action_all_filter", "Эффект для всех зон"),
            )
            self.popup_filter.filter_selected.connect(self._on_mass_filter_selected)
            self.popup_filter.parameters_changed.connect(self._on_mass_filter_parameters_changed)
        self.popup_filter.set_filter(self.mass_filter_selection)
        self.popup_filter.set_parameters(self.mass_filter_params)
        show_smart_popup(self.btn_mass_filter, self.popup_filter)

    def _on_mass_filter_selected(self, filter_type: str):
        self.mass_filter_selection = filter_type
        self.mass_filter = filter_type
        if filter_type == FilterType.NONE:
            self.btn_mass_filter.setStyleSheet("")
            self.btn_mass_filter.setIcon(create_themed_icon("filter", self.is_dark, size=15))
        else:
            self.btn_mass_filter.setStyleSheet("background-color: #2563eb; border: 1px solid #3b82f6; border-radius: 4px;")
            self.btn_mass_filter.setIcon(create_themed_icon("filter", self.is_dark, size=15, custom_color="#ffffff"))
        self.mass_filter_selected.emit(filter_type)

    def _on_mass_filter_parameters_changed(self, params: dict):
        self.mass_filter_params.update(params or {})
        self.mass_filter_parameters_changed.emit(dict(self.mass_filter_params))

    def reset_mass_filter(self):
        """Сбрасывает применение эффекта и восстанавливает пункт по умолчанию."""
        self.mass_filter = FilterType.NONE
        self.mass_filter_selection = FilterType.NONE
        self.mass_filter_params = {"blur_radius": 15, "pixel_size": 12}
        self.btn_mass_filter.setStyleSheet("")
        self.btn_mass_filter.setIcon(create_themed_icon("filter", self.is_dark, size=15))
        if self.popup_filter is not None:
            self.popup_filter.set_filter(self.mass_filter_selection)
            self.popup_filter.set_parameters(self.mass_filter_params)


class WholeAreaFilterPopup(QFrame):
    """Компактные настройки фильтра всего захватываемого изображения."""

    filter_selected = pyqtSignal(str)
    parameters_changed = pyqtSignal(dict)

    def __init__(self, parent=None, title: str | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(260)
        self.filter_params = {"blur_radius": 15, "pixel_size": 12}
        self.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 7px;
            }
            QLabel {
                color: #e4e4e7;
                border: none;
                font-size: 11px;
            }
            QComboBox {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #52525b;
                border-radius: 4px;
                padding: 4px 6px;
                min-height: 22px;
            }
            QSlider::groove:horizontal {
                height: 4px;
                background: #3f3f46;
                border-radius: 2px;
            }
            QSlider::handle:horizontal {
                width: 12px;
                margin: -4px 0;
                background: #60a5fa;
                border-radius: 6px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 9, 10, 9)
        layout.setSpacing(5)

        title_label = QLabel(title or tr("whole_filter_title", "Эффект всей области"))
        title_label.setStyleSheet("color: #38bdf8; font-weight: bold; border: none;")
        layout.addWidget(title_label)

        self.combo_filter = QComboBox()
        from utils.image_filters import get_localized_filter_names
        for filter_type, label in get_localized_filter_names().items():
            self.combo_filter.addItem(label, filter_type)
        self.combo_filter.currentIndexChanged.connect(self._on_filter_changed)
        layout.addWidget(self.combo_filter)

        self.lbl_blur = QLabel()
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(3, 45)
        self.slider_blur.valueChanged.connect(self._on_blur_changed)
        layout.addWidget(self.lbl_blur)
        layout.addWidget(self.slider_blur)

        self.lbl_pixel = QLabel()
        self.slider_pixel = QSlider(Qt.Orientation.Horizontal)
        self.slider_pixel.setRange(3, 40)
        self.slider_pixel.valueChanged.connect(self._on_pixel_changed)
        layout.addWidget(self.lbl_pixel)
        layout.addWidget(self.slider_pixel)

        self._sync_parameter_widgets()

    def set_filter(self, filter_type: str):
        for index in range(self.combo_filter.count()):
            if self.combo_filter.itemData(index) == filter_type:
                self.combo_filter.blockSignals(True)
                self.combo_filter.setCurrentIndex(index)
                self.combo_filter.blockSignals(False)
                break
        self._sync_parameter_widgets()

    def set_parameters(self, params: dict):
        self.filter_params.update(params or {})
        self.slider_blur.blockSignals(True)
        self.slider_pixel.blockSignals(True)
        self.slider_blur.setValue(int(self.filter_params.get("blur_radius", 15)))
        self.slider_pixel.setValue(int(self.filter_params.get("pixel_size", 12)))
        self.slider_blur.blockSignals(False)
        self.slider_pixel.blockSignals(False)
        self._sync_parameter_widgets()

    def _sync_parameter_widgets(self):
        current = self.combo_filter.currentData()
        self.lbl_blur.setText(tr("filter_blur_radius", "Сила блюра: {val} px", val=self.filter_params["blur_radius"]))
        self.lbl_pixel.setText(tr("filter_pixel_size", "Зернистость мозаики: {val} px", val=self.filter_params["pixel_size"]))
        self.lbl_blur.setVisible(current == FilterType.BLUR)
        self.slider_blur.setVisible(current == FilterType.BLUR)
        self.lbl_pixel.setVisible(current == FilterType.PIXELATE)
        self.slider_pixel.setVisible(current == FilterType.PIXELATE)

    def _on_filter_changed(self, _index: int):
        self._sync_parameter_widgets()
        self.filter_selected.emit(self.combo_filter.currentData())

    def _on_blur_changed(self, value: int):
        self.filter_params["blur_radius"] = int(value)
        self._sync_parameter_widgets()
        self.parameters_changed.emit(dict(self.filter_params))

    def _on_pixel_changed(self, value: int):
        self.filter_params["pixel_size"] = int(value)
        self._sync_parameter_widgets()
        self.parameters_changed.emit(dict(self.filter_params))


class BottomActionToolbar(QFrame):
    save_clicked = pyqtSignal(str)
    copy_clicked = pyqtSignal(str)
    scrolling_screenshot_requested = pyqtSignal()
    search_image_requested = pyqtSignal(str)
    record_video_started = pyqtSignal(dict)
    record_gif_started = pyqtSignal(dict)
    filter_selected = pyqtSignal(str)
    filter_parameters_changed = pyqtSignal(dict)
    lock_toggled = pyqtSignal(bool)
    dynamic_bg_toggled = pyqtSignal(bool)
    passthrough_toggled = pyqtSignal(bool)
    settings_clicked = pyqtSignal()
    add_region_clicked = pyqtSignal()
    all_regions_action = pyqtSignal(str)
    close_clicked = pyqtSignal()

    def enterEvent(self, event):
        """Поднимает нижнюю панель над боковой при наведении."""
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "_raise_toolbar"):
            parent._raise_toolbar(self)
        super().enterEvent(event)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_locked = False
        self.current_filter = FilterType.NONE
        self.filter_params = {"blur_radius": 15, "pixel_size": 12}
        self.filter_popup = None

        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["panel_frame"] + theme["button_base"])

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 3, 4, 3)
        layout.setSpacing(2)

        # 1. Сохранить (SVG иконка дискеты)
        self.btn_save = ModernButton("", tr("action_save", "Сохранить скриншот (клик — выбор PNG, JPG, WebP) [Ctrl+S]"))
        self.btn_save.setFixedSize(28, 28)
        self.btn_save.setIcon(create_themed_icon("save", self.is_dark, size=16))
        self.btn_save.setIconSize(QSize(16, 16))
        self.btn_save.clicked.connect(self._show_save_formats)
        layout.addWidget(self.btn_save)

        self.popup_formats = ScreenshotFormatPopup(self)
        self.popup_formats.format_selected.connect(self.save_clicked.emit)

        # 2. Скопировать (SVG иконка двух листов с выбором формата)
        self.btn_copy = ModernButton("", tr("action_copy", "Скопировать скриншот (клик — выбор формата) [Ctrl+C]"))
        self.btn_copy.setFixedSize(28, 28)
        self.btn_copy.setIcon(create_themed_icon("copy", self.is_dark, size=16))
        self.btn_copy.setIconSize(QSize(16, 16))
        self.btn_copy.clicked.connect(self._show_copy_formats)
        layout.addWidget(self.btn_copy)

        self.popup_copy = CopyFormatPopup(self)
        self.popup_copy.format_selected.connect(self.copy_clicked.emit)

        # 3. Видео MP4 (SVG иконка видеокамеры)
        self.btn_video = ModernButton("", tr("action_video_tip", "Запись видео MP4 (настройки звука и кодека)"))
        self.btn_video.setFixedSize(28, 28)
        self.btn_video.setIcon(create_themed_icon("video", self.is_dark, size=16))
        self.btn_video.setIconSize(QSize(16, 16))
        self.btn_video.clicked.connect(self._show_video_popup)
        layout.addWidget(self.btn_video)

        self.popup_video = VideoOptionsPopup(self)
        self.popup_video.start_video.connect(self.record_video_started.emit)

        # 4. GIF (SVG иконка с надписью GIF в рамке)
        self.btn_gif = ModernButton("", tr("action_gif_tip", "Запись GIF (настройки качества и FPS)"))
        self.btn_gif.setFixedSize(28, 28)
        self.btn_gif.setIcon(create_themed_icon("gif", self.is_dark, size=18))
        self.btn_gif.setIconSize(QSize(18, 18))
        self.btn_gif.clicked.connect(self._show_gif_popup)
        layout.addWidget(self.btn_gif)

        self.popup_gif = GifOptionsPopup(self)
        self.popup_gif.start_gif.connect(self.record_gif_started.emit)

        # Кнопка-полукнопка для скрытия/раскрытия дополнительных действий панели
        self._more_actions_expanded = False
        self._advanced_action_widgets = []
        self.btn_more_actions = ModernButton("", tr("action_more", "Дополнительные действия"))
        self.btn_more_actions.setFixedSize(14, 28)
        self.btn_more_actions.setIcon(create_themed_icon("more_vertical", self.is_dark, size=14))
        self.btn_more_actions.setIconSize(QSize(14, 14))
        self.btn_more_actions.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_more_actions.clicked.connect(self._toggle_more_actions)
        layout.addWidget(self.btn_more_actions)

        # 5. Длинный скриншот (SVG иконка листа со стрелкой вниз)
        self.btn_scroll = ModernButton("", tr("action_scroll", "Длинный скриншот с автопрокруткой [S]"))
        self.btn_scroll.setFixedSize(28, 28)
        self.btn_scroll.setIcon(create_themed_icon("scroll", self.is_dark, size=16))
        self.btn_scroll.setIconSize(QSize(16, 16))
        self.btn_scroll.clicked.connect(self.scrolling_screenshot_requested.emit)
        layout.addWidget(self.btn_scroll)
        self._advanced_action_widgets.append(self.btn_scroll)

        # 6. Поиск по картинке (SVG иконка лупы)
        self.btn_search = ModernButton("", tr("action_search", "Искать в Google Lens или Яндекс Картинках"))
        self.btn_search.setFixedSize(28, 28)
        self.btn_search.setIcon(create_themed_icon("search", self.is_dark, size=16))
        self.btn_search.setIconSize(QSize(16, 16))
        self.btn_search.clicked.connect(self._show_search_menu)
        layout.addWidget(self.btn_search)
        self._advanced_action_widgets.append(self.btn_search)

        self.popup_search = SearchEnginePopup(self)
        self.popup_search.engine_selected.connect(self.search_image_requested.emit)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet(f"background-color: {theme['sep_color']}; max-width: 1px;")
        layout.addWidget(sep1)
        self._advanced_action_widgets.append(sep1)

        # 7. Фильтр всего экрана (клик — выбор фильтра: размытие, ч/б, сепия, инверсия и т.д.)
        self.btn_filter = ModernButton("", tr("action_filters", "Эффекты и цветовые фильтры всего экрана"))
        self.btn_filter.setFixedSize(28, 28)
        self.btn_filter.setIcon(create_themed_icon("filter", self.is_dark, size=16))
        self.btn_filter.setIconSize(QSize(16, 16))
        self.btn_filter.clicked.connect(self._show_filter_menu)
        layout.addWidget(self.btn_filter)
        self._advanced_action_widgets.append(self.btn_filter)

        # 8. Замок блокировки рамки (SVG иконка замка)
        self.btn_lock = ModernButton("", tr("action_lock", "Зафиксировать рамку от случайных сдвигов"))
        self.btn_lock.setFixedSize(28, 28)
        self.btn_lock.setIcon(create_themed_icon("unlock", self.is_dark, size=16))
        self.btn_lock.setIconSize(QSize(16, 16))
        self.btn_lock.clicked.connect(self._toggle_lock)
        layout.addWidget(self.btn_lock)
        self._advanced_action_widgets.append(self.btn_lock)

        # 9. Динамический фон (SVG иконка монитора с воспроизведением)
        self.chk_dynamic_bg = ModernButton("", tr("action_dynamic_bg", "Динамический фон: живой рабочий стол внутри рамки (вкл/выкл)"))
        self.chk_dynamic_bg.setCheckable(True)
        self.chk_dynamic_bg.setFixedSize(28, 28)
        self.chk_dynamic_bg.setIcon(create_themed_icon("dynamic_bg", self.is_dark, size=16))
        self.chk_dynamic_bg.setIconSize(QSize(16, 16))
        self.chk_dynamic_bg.toggled.connect(self.dynamic_bg_toggled.emit)
        layout.addWidget(self.chk_dynamic_bg)
        self._advanced_action_widgets.append(self.chk_dynamic_bg)

        # 9.1 Неосязаемая / защитная зона (клики внутри рамки не взаимодействуют с фоновыми окнами)
        self.chk_passthrough = ModernButton("", tr("action_passthrough", "Защита зоны: клики внутри рамки не взаимодействуют с фоновыми окнами (вкл/выкл)"))
        self.chk_passthrough.setCheckable(True)
        self.chk_passthrough.setFixedSize(28, 28)
        self.chk_passthrough.setIcon(create_themed_icon("passthrough", self.is_dark, size=16))
        self.chk_passthrough.setIconSize(QSize(16, 16))
        self.chk_passthrough.toggled.connect(self.passthrough_toggled.emit)
        layout.addWidget(self.chk_passthrough)
        self._advanced_action_widgets.append(self.chk_passthrough)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet(f"background-color: {theme['sep_color']}; max-width: 1px;")
        layout.addWidget(sep2)
        self._advanced_action_widgets.append(sep2)

        # 10. Настройки (SVG иконка шестеренки)
        self.btn_settings = ModernButton("", tr("settings_title", "Настройки Framio"))
        self.btn_settings.setFixedSize(28, 28)
        self.btn_settings.setIcon(create_themed_icon("settings", self.is_dark, size=16))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self.btn_settings)
        self._advanced_action_widgets.append(self.btn_settings)

        # 11. Добавить зону выделения (SVG иконка плюсика)
        self.btn_add_region = ModernButton("", tr("action_add_region", "Добавить зону выделения (+ / Ctrl)"))
        self.btn_add_region.setFixedSize(28, 28)
        self.btn_add_region.setIcon(create_themed_icon("add_region", self.is_dark, size=16, custom_color="#93c5fd"))
        self.btn_add_region.setIconSize(QSize(16, 16))
        self.btn_add_region.setStyleSheet("""
            QPushButton {
                background-color: #1e3a8a;
                border: 1px solid #2563eb;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #3b82f6;
            }
        """)
        self.btn_add_region.clicked.connect(self.add_region_clicked.emit)
        layout.addWidget(self.btn_add_region)

        # 12. Общие действия для всех зон (показываются только при multi-selection)
        self.btn_all_regions = ModernButton("", tr("action_all_regions", "Действия для всех зон"))
        self.btn_all_regions.setFixedSize(28, 28)
        self.btn_all_regions.setIcon(create_themed_icon("layers", self.is_dark, size=16, custom_color="#c4b5fd"))
        self.btn_all_regions.setIconSize(QSize(16, 16))
        self.btn_all_regions.setStyleSheet("""
            QPushButton {
                background-color: #3b1f68;
                border: 1px solid #7c3aed;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #6d28d9;
            }
        """)
        self.btn_all_regions.clicked.connect(self._show_all_regions_menu)
        self.btn_all_regions.hide()
        layout.addWidget(self.btn_all_regions)

        # 13. Закрыть (SVG иконка крестика)
        self.btn_close = ModernButton("", tr("action_close", "Закрыть выделение (Esc)"))
        self.btn_close.setFixedSize(28, 28)
        self.btn_close.setIcon(create_themed_icon("close", self.is_dark, size=16, custom_color="#fca5a5"))
        self.btn_close.setIconSize(QSize(16, 16))
        self.btn_close.setStyleSheet("""
            QPushButton {
                background-color: #3f1d1d;
                border: 1px solid #7f1d1d;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #ef4444;
            }
        """)
        self.btn_close.clicked.connect(self.close_clicked.emit)
        layout.addWidget(self.btn_close)
        self._set_more_actions_visible(False)

    def _set_more_actions_visible(self, expanded: bool):
        self._more_actions_expanded = bool(expanded)
        for widget in getattr(self, "_advanced_action_widgets", []):
            widget.setVisible(self._more_actions_expanded)
        if hasattr(self, "btn_more_actions"):
            self.btn_more_actions.setIcon(create_themed_icon(
                "chevron_left" if self._more_actions_expanded else "more_vertical",
                self.is_dark,
                size=14,
            ))
        self.adjustSize()

    def _toggle_more_actions(self):
        new_expanded = not self._more_actions_expanded
        self._user_expanded_actions = new_expanded
        self._set_more_actions_visible(new_expanded)
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "_update_toolbar_positions"):
            parent._update_toolbar_positions()

    def update_multi_region_state(self, has_multiple: bool):
        """Обновляет подсказку кнопки закрытия в зависимости от наличия нескольких зон."""
        if hasattr(self, "btn_all_regions"):
            self.btn_all_regions.setVisible(has_multiple)
        if hasattr(self, "btn_close"):
            if has_multiple:
                self.btn_close.setToolTip(tr("action_close_region", "Удалить активную зону (Esc / Ctrl+W)"))
            else:
                self.btn_close.setToolTip(tr("action_close", "Закрыть выделение (Esc)"))

    def update_add_region_state(self, is_active: bool):
        """Показывает, что следующий drag добавит новую зону и не заменит старые."""
        if not hasattr(self, "btn_add_region"):
            return
        self.btn_add_region.setChecked(is_active)
        if is_active:
            self.btn_add_region.setToolTip(tr("action_add_region_active", "Режим добавления зон включён — выделите следующую область"))
            self.btn_add_region.setStyleSheet("""
                QPushButton {
                    background-color: #2563eb;
                    border: 1px solid #60a5fa;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #3b82f6; }
            """)
        else:
            self.btn_add_region.setToolTip(tr("action_add_region", "Добавить зону выделения (+ / Ctrl)"))
            self.btn_add_region.setStyleSheet("""
                QPushButton {
                    background-color: #1e3a8a;
                    border: 1px solid #2563eb;
                    border-radius: 4px;
                }
                QPushButton:hover { background-color: #3b82f6; }
            """)

    def _show_save_formats(self):
        show_smart_popup(self.btn_save, self.popup_formats)

    def _show_copy_formats(self):
        show_smart_popup(self.btn_copy, self.popup_copy)

    def _show_search_menu(self):
        show_smart_popup(self.btn_search, self.popup_search)

    def _show_video_popup(self):
        show_smart_popup(self.btn_video, self.popup_video)

    def _show_gif_popup(self):
        show_smart_popup(self.btn_gif, self.popup_gif)

    def _show_all_regions_menu(self):
        menu = QMenu(self)
        menu.addAction(tr("action_all_save", "Сохранить скриншоты всех зон")).triggered.connect(
            lambda: self.all_regions_action.emit("save")
        )
        menu.addAction(tr("action_all_copy", "Скопировать все зоны")).triggered.connect(
            lambda: self.all_regions_action.emit("copy")
        )
        menu.addSeparator()
        menu.addAction(tr("action_all_video", "Записывать видео всех зон")).triggered.connect(
            lambda: self.all_regions_action.emit("video")
        )
        menu.addAction(tr("action_all_gif", "Записывать GIF всех зон")).triggered.connect(
            lambda: self.all_regions_action.emit("gif")
        )
        show_smart_popup(self.btn_all_regions, menu)

    def _toggle_lock(self):
        self.is_locked = not self.is_locked
        if self.is_locked:
            self.btn_lock.setIcon(create_themed_icon("lock", self.is_dark, size=16, custom_color="#fde68a"))
            self.btn_lock.setToolTip(tr("action_locked_tip", "Рамка зафиксирована (кликните, чтобы разблокировать)"))
            self.btn_lock.setStyleSheet("background-color: #78350f; border: 1px solid #92400e; border-radius: 4px;")
        else:
            self.btn_lock.setIcon(create_themed_icon("unlock", self.is_dark, size=16))
            self.btn_lock.setToolTip(tr("action_lock", "Зафиксировать рамку от случайных сдвигов"))
            self.btn_lock.setStyleSheet("")
        self.lock_toggled.emit(self.is_locked)

    def _show_filter_menu(self):
        if self.filter_popup is None:
            self.filter_popup = WholeAreaFilterPopup(self)
            self.filter_popup.filter_selected.connect(self._select_filter)
            self.filter_popup.parameters_changed.connect(self._on_filter_parameters_changed)
        self.filter_popup.set_filter(self.current_filter)
        self.filter_popup.set_parameters(self.filter_params)
        show_smart_popup(self.btn_filter, self.filter_popup)

    def _select_filter(self, f_type):
        self.current_filter = f_type
        if self.filter_popup is not None:
            self.filter_popup.set_filter(f_type)
        if f_type == FilterType.NONE:
            self.btn_filter.setStyleSheet("")
            self.btn_filter.setIcon(create_themed_icon("filter", self.is_dark, size=16))
        else:
            self.btn_filter.setStyleSheet("background-color: #2563eb; border: 1px solid #3b82f6; border-radius: 4px;")
            self.btn_filter.setIcon(create_themed_icon("filter", self.is_dark, size=16, custom_color="#ffffff"))
        self.filter_selected.emit(f_type)

    def _on_filter_parameters_changed(self, params: dict):
        self.filter_params.update(params or {})
        self.filter_parameters_changed.emit(dict(self.filter_params))

    def _on_blur_radius_changed(self, value: int):
        """Слот для тестов и внешних интеграций, меняющий радиус блюра."""
        self._on_filter_parameters_changed({"blur_radius": int(value)})

    def _on_pixel_size_changed(self, value: int):
        """Слот для тестов и внешних интеграций, меняющий зернистость мозаики."""
        self._on_filter_parameters_changed({"pixel_size": int(value)})

    def sync_filter(self, f_type: str):
        self.current_filter = f_type
        if self.filter_popup is not None:
            self.filter_popup.set_filter(f_type)
        if f_type == FilterType.NONE:
            self.btn_filter.setStyleSheet("")
            self.btn_filter.setIcon(create_themed_icon("filter", self.is_dark, size=16))
        else:
            self.btn_filter.setStyleSheet("background-color: #2563eb; border: 1px solid #3b82f6; border-radius: 4px;")
            self.btn_filter.setIcon(create_themed_icon("filter", self.is_dark, size=16, custom_color="#ffffff"))

    def sync_filter_params(self, params: dict):
        """Синхронизирует ползунки эффекта с параметрами активной зоны."""
        self.filter_params.update(params or {})
        if self.filter_popup is not None:
            self.filter_popup.set_parameters(self.filter_params)

    def reset_filter(self):
        self.filter_params = {"blur_radius": 15, "pixel_size": 12}
        if self.filter_popup is not None:
            self.filter_popup.set_parameters(self.filter_params)
        self.sync_filter(FilterType.NONE)
