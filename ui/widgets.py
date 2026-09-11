# -*- coding: utf-8 -*-
"""
Вспомогательные UI виджеты: бейджи размеров, HUD записи, палитры цветов и выбор толщины.
"""

import ctypes

from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QSize
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QSlider, QColorDialog, QFrame, QToolTip
)
from PyQt6.QtGui import QColor, QFont, QCursor
from utils.i18n import tr
from ui.icons import create_themed_icon

class ModernButton(QPushButton):
    def __init__(self, text="", tooltip="", parent=None):
        super().__init__(text, parent)
        if tooltip:
            self.setToolTip(tooltip)
        self.setAttribute(Qt.WidgetAttribute.WA_AlwaysShowToolTips, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            QPushButton {
                background-color: #2b2e38;
                color: #e6e8ee;
                border: 1px solid #3c404f;
                border-radius: 5px;
                padding: 5px 8px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3b4050;
                border-color: #5c637a;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #1e2027;
            }
            QPushButton:checked {
                background-color: #4a6ee0;
                border-color: #6688f5;
                color: #ffffff;
            }
        """)

    def enterEvent(self, event):
        super().enterEvent(event)
        tip = self.toolTip()
        if tip:
            QToolTip.showText(QCursor.pos(), tip, self)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        QToolTip.hideText()


class DimensionBadge(QFrame):
    fullscreen_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: rgba(24, 24, 27, 230);
                border: 1px solid #3f3f46;
                border-radius: 4px;
            }
            QLabel {
                background: transparent;
                border: none;
                color: #e4e4e7;
                font-family: Consolas, 'Segoe UI', monospace;
                font-size: 11px;
                font-weight: bold;
                padding: 1px 4px 1px 6px;
            }
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 3px;
                padding: 2px;
                margin-right: 3px;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
            QPushButton:pressed {
                background-color: #27272a;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        self.lbl_text = QLabel(self)
        layout.addWidget(self.lbl_text)

        self.btn_fullscreen = QPushButton(self)
        self.btn_fullscreen.setFixedSize(18, 18)
        self.btn_fullscreen.setIconSize(QSize(12, 12))
        self.btn_fullscreen.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fullscreen.setToolTip(tr("action_fullscreen_tip", "Выбрать весь экран (Ctrl+A)"))
        self.btn_fullscreen.setIcon(create_themed_icon("maximize", is_dark=True, size=12, custom_color="#d4d4d8"))
        self.btn_fullscreen.clicked.connect(self.fullscreen_clicked.emit)
        layout.addWidget(self.btn_fullscreen)

        self.hide()

    def update_dimension(self, w: int, h: int, info: str = ""):
        suffix = f"  {info}" if info else ""
        self.lbl_text.setText(f"{int(w)} × {int(h)} px{suffix}")
        self.adjustSize()


class ColorPalettePopup(QFrame):
    color_selected = pyqtSignal(str)
    stroke_changed = pyqtSignal(int)
    grain_changed = pyqtSignal(int)
    blur_changed = pyqtSignal(int)

    PRESET_COLORS = [
        "#FF2E2E", "#FF8C00", "#FFD700", "#2ECC71",
        "#00C0FF", "#3498DB", "#9B59B6", "#FFFFFF", "#111111"
    ]

    def __init__(self, current_color="#FF2E2E", current_width=4, parent=None, current_grain=8, current_blur=15, grain=None, blur=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.current_color = current_color
        self.current_width = current_width
        self.current_grain = grain if grain is not None else current_grain
        self.current_blur = blur if blur is not None else current_blur

        self.setStyleSheet("""
            QFrame {
                background-color: #1e2028;
                border: 1px solid #3e4452;
                border-radius: 8px;
            }
            QLabel {
                color: #abb2bf;
                font-size: 11px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Сетка цветов
        color_layout = QHBoxLayout()
        color_layout.setSpacing(6)

        for col in self.PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {col};
                    border: 1px solid {'#ffffff' if col == current_color else '#444444'};
                    border-radius: 11px;
                }}
                QPushButton:hover {{
                    border: 2px solid #ffffff;
                }}
            """)
            btn.clicked.connect(lambda checked, c=col: self._on_select(c))
            color_layout.addWidget(btn)

        # Кнопка расширенной палитры
        more_btn = QPushButton()
        more_btn.setIcon(create_themed_icon("palette", is_dark=True, size=14))
        more_btn.setFixedSize(24, 22)
        more_btn.setToolTip(tr("palette_custom_tip", "Выбрать произвольный цвет"))
        more_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        more_btn.clicked.connect(self._open_custom_dialog)
        color_layout.addWidget(more_btn)

        # Кнопка мозаики (цензуры)
        self.btn_mosaic = QPushButton()
        self.btn_mosaic.setIcon(create_themed_icon("mosaic", is_dark=True, size=14))
        self.btn_mosaic.setFixedSize(24, 22)
        self.btn_mosaic.setToolTip(tr("draw_mosaic", "Мозаика (Цензура)"))
        self.btn_mosaic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mosaic.setStyleSheet(f"""
            QPushButton {{
                background-color: {'#3b82f6' if current_color == 'mosaic' else '#27272a'};
                border: 1px solid {'#60a5fa' if current_color == 'mosaic' else '#3f3f46'};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3f3f46;
                border: 1px solid #38bdf8;
            }}
        """)
        self.btn_mosaic.clicked.connect(lambda: self._on_select("mosaic"))
        color_layout.addWidget(self.btn_mosaic)

        # Кнопка размытия (блюра)
        self.btn_blur = QPushButton()
        self.btn_blur.setIcon(create_themed_icon("blur", is_dark=True, size=14))
        self.btn_blur.setFixedSize(24, 22)
        self.btn_blur.setToolTip(tr("tool_blur", "Размытие (Блюр)"))
        self.btn_blur.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_blur.setStyleSheet(f"""
            QPushButton {{
                background-color: {'#3b82f6' if current_color == 'blur' else '#27272a'};
                border: 1px solid {'#60a5fa' if current_color == 'blur' else '#3f3f46'};
                border-radius: 4px;
            }}
            QPushButton:hover {{
                background-color: #3f3f46;
                border: 1px solid #60a5fa;
            }}
        """)
        self.btn_blur.clicked.connect(lambda: self._on_select("blur"))
        color_layout.addWidget(self.btn_blur)

        layout.addLayout(color_layout)

        # Сгруппированный блок ползунков
        slider_frame = QFrame()
        slider_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(24, 24, 27, 180);
                border: 1px solid #2d3139;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        slider_layout = QVBoxLayout(slider_frame)
        slider_layout.setContentsMargins(6, 6, 6, 6)
        slider_layout.setSpacing(6)

        # 1. Слайдер толщины линии
        self.stroke_label = QLabel(tr("palette_thickness", "Толщина линии: {val} px", val=self.current_width))
        slider_layout.addWidget(self.stroke_label)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 32)
        self.slider.setValue(self.current_width)
        self.slider.valueChanged.connect(self._on_stroke_changed)
        slider_layout.addWidget(self.slider)

        # 2. Слайдер зернистости (мозаика)
        self.lbl_grain = QLabel(f"Зернистость: {self.current_grain} px")
        self.slider_grain = QSlider(Qt.Orientation.Horizontal)
        self.slider_grain.setRange(3, 30)
        self.slider_grain.setValue(self.current_grain)
        self.slider_grain.valueChanged.connect(self._on_grain_changed)
        slider_layout.addWidget(self.lbl_grain)
        slider_layout.addWidget(self.slider_grain)

        # 3. Слайдер степени размытия (блюр)
        self.lbl_blur = QLabel(f"Степень размытия: {self.current_blur} px")
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(3, 45)
        self.slider_blur.setValue(self.current_blur)
        self.slider_blur.valueChanged.connect(self._on_blur_changed)
        slider_layout.addWidget(self.lbl_blur)
        slider_layout.addWidget(self.slider_blur)

        self.slider_censor = self.slider_grain

        if self.current_color == "mosaic":
            self.lbl_grain.show()
            self.slider_grain.show()
            self.lbl_blur.hide()
            self.slider_blur.hide()
        elif self.current_color == "blur":
            self.lbl_grain.hide()
            self.slider_grain.hide()
            self.lbl_blur.show()
            self.slider_blur.show()
        else:
            self.lbl_grain.hide()
            self.slider_grain.hide()
            self.lbl_blur.hide()
            self.slider_blur.hide()

        layout.addWidget(slider_frame)

    def _on_stroke_changed(self, v: int):
        self.stroke_label.setText(tr("palette_thickness", "Толщина линии: {val} px", val=v))
        self.stroke_changed.emit(v)

    def _on_grain_changed(self, v: int):
        self.current_grain = v
        self.lbl_grain.setText(f"Зернистость: {v} px")
        self.grain_changed.emit(v)

    def _on_blur_changed(self, v: int):
        self.current_blur = v
        self.lbl_blur.setText(f"Степень размытия: {v} px")
        self.blur_changed.emit(v)

    def _on_censor_slider_changed(self, v: int):
        if self.current_color == "mosaic":
            self._on_grain_changed(v)
        else:
            self._on_blur_changed(v)

    def _on_select(self, col: str):
        self.current_color = col
        self.color_selected.emit(col)
        self.close()

    def _open_custom_dialog(self):
        col = QColorDialog.getColor(QColor(self.current_color if self.current_color not in ("mosaic", "blur") else "#FF2E2E"), self, tr("palette_title", "Выбор цвета"))
        if col.isValid():
            self._on_select(col.name())


class RecordingHud(QFrame):
    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()

    def __init__(self, mode="video", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.is_paused = False

        self.setStyleSheet("""
            QFrame {
                background-color: rgba(22, 25, 34, 230);
                border: 1px solid #e06c75;
                border-radius: 6px;
                padding: 2px;
            }
            QLabel {
                color: #ffffff;
                font-family: Consolas, monospace;
                font-size: 13px;
                font-weight: bold;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        # Индикатор записи
        icon_text = "REC" if mode == "video" else "GIF"
        self.rec_label = QLabel(icon_text)
        self.rec_label.setStyleSheet("color: #ff4d4f;")
        layout.addWidget(self.rec_label)

        # Таймер и счетчик кадров
        self.time_label = QLabel("00:00")
        layout.addWidget(self.time_label)

        # Пауза / продолжить
        self.btn_pause = ModernButton("", tr("action_pause_rec", "Пауза записи"))
        self.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=14))
        self.btn_pause.setFixedSize(30, 26)
        self.btn_pause.clicked.connect(self._toggle_pause)
        layout.addWidget(self.btn_pause)

        # Стоп
        self.btn_stop = ModernButton(tr("action_stop_rec", "Стоп"), tr("action_stop_rec_tip", "Завершить и сохранить запись"))
        self.btn_stop.setIcon(create_themed_icon("stop", is_dark=True, size=13))
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #d9383a;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
                padding: 4px 10px;
            }
            QPushButton:hover {
                background-color: #f0484a;
            }
        """)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.btn_stop)

    def update_status(self, elapsed_sec: float, frame_count: int):
        m = int(elapsed_sec // 60)
        s = int(elapsed_sec % 60)
        self.time_label.setText(f"{m:02d}:{s:02d} ({frame_count} кадров)")

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self.btn_pause.setIcon(create_themed_icon("play" if self.is_paused else "pause", is_dark=True, size=14))
        self.rec_label.setText("PAUSE" if self.is_paused else ("REC" if self.mode == "video" else "GIF"))
        self.pause_clicked.emit()


class MassRecordingHud(RecordingHud):
    """Небольшая отдельная панель управления всеми параллельными записями."""

    pause_video_clicked = pyqtSignal()
    pause_gif_clicked = pyqtSignal()
    stop_video_clicked = pyqtSignal()
    stop_gif_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(mode="video", parent=None)
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.rec_label.setText(tr("mass_recording_title", "Массовая запись"))
        self.rec_label.setStyleSheet("color: #f59e0b;")
        self.rec_label.setFixedWidth(140)
        self.time_label.setFixedWidth(150)
        self.btn_pause.hide()
        # Общее действие оставляем доступным рядом с раздельными кнопками:
        # оно по-прежнему останавливает все форматы сразу. Видимые кнопки
        # ниже разделены по типу записи, чтобы GIF не останавливал видео.
        self.btn_stop.setText(tr("mass_recording_stop", "Остановить всё"))
        self.btn_stop.setToolTip(tr("mass_recording_stop_tip", "Остановить все видео и GIF"))
        self.btn_stop.setMinimumHeight(26)
        self.btn_stop.setMinimumWidth(0)
        self.btn_stop.setFixedWidth(96)

        self.btn_pause_video = self._make_action_button(
            "mass_video_pause", "mass_video_pause_tip", "pause"
        )
        self.btn_stop_video = self._make_action_button(
            "mass_video_stop", "mass_video_stop_tip", "stop", destructive=True
        )
        self.btn_pause_gif = self._make_action_button(
            "mass_gif_pause", "mass_gif_pause_tip", "pause"
        )
        self.btn_stop_gif = self._make_action_button(
            "mass_gif_stop", "mass_gif_stop_tip", "stop", destructive=True
        )

        layout = self.layout()
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(5)
        layout.addWidget(self.btn_pause_video)
        layout.addWidget(self.btn_stop_video)
        layout.addWidget(self.btn_pause_gif)
        layout.addWidget(self.btn_stop_gif)

        self.btn_pause_video.clicked.connect(self.pause_video_clicked.emit)
        self.btn_pause_gif.clicked.connect(self.pause_gif_clicked.emit)
        self.btn_stop_video.clicked.connect(self.stop_video_clicked.emit)
        self.btn_stop_gif.clicked.connect(self.stop_gif_clicked.emit)
        self.adjustSize()

    def _make_action_button(self, text_key, tip_key, icon_name, destructive=False):
        button = ModernButton(tr(text_key, text_key), tr(tip_key, tip_key))
        button.setIcon(create_themed_icon(icon_name, is_dark=True, size=13))
        button.setMinimumHeight(26)
        button.setMinimumWidth(0)
        button.setFixedWidth(88)
        if destructive:
            button.setStyleSheet("""
                QPushButton {
                    background-color: #d9383a;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 4px 7px;
                    font-weight: bold;
                }
                QPushButton:hover { background-color: #f0484a; }
                QPushButton:disabled { background-color: #5b2730; color: #b7a4a6; }
            """)
        return button

    def update_count(self, count: int):
        self.time_label.setText(
            tr("mass_recording_active", "Активных зон: {count}", count=max(0, int(count)))
        )
        enabled = max(0, int(count)) > 0
        for button in (
            self.btn_pause_video, self.btn_stop_video,
            self.btn_pause_gif, self.btn_stop_gif,
        ):
            button.setEnabled(enabled)
        self.adjustSize()

    def update_counts(self, video_count: int, gif_count: int):
        video_count = max(0, int(video_count))
        gif_count = max(0, int(gif_count))
        self.time_label.setText(
            tr(
                "mass_recording_active_types",
                "Видео: {video} · GIF: {gif}",
                video=video_count,
                gif=gif_count,
            )
        )
        self.btn_pause_video.setEnabled(video_count > 0)
        self.btn_stop_video.setEnabled(video_count > 0)
        self.btn_pause_gif.setEnabled(gif_count > 0)
        self.btn_stop_gif.setEnabled(gif_count > 0)
        self.adjustSize()

    def set_paused(self, mode: str, paused: bool):
        button = self.btn_pause_video if mode == "video" else self.btn_pause_gif
        key = "mass_video_resume" if mode == "video" else "mass_gif_resume"
        tip_key = "mass_video_resume_tip" if mode == "video" else "mass_gif_resume_tip"
        if not paused:
            key = "mass_video_pause" if mode == "video" else "mass_gif_pause"
            tip_key = "mass_video_pause_tip" if mode == "video" else "mass_gif_pause_tip"
        button.setText(tr(key, key))
        button.setToolTip(tr(tip_key, tip_key))
        button.setIcon(create_themed_icon("play" if paused else "pause", is_dark=True, size=13))
        self.adjustSize()

    def _ensure_topmost(self):
        """Возвращает HUD поверх шапок окон записи без перехвата фокуса."""
        try:
            hwnd = int(self.winId())
            # HWND_TOPMOST = -1; SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE.
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
        except Exception:
            # На Linux/offscreen тестах Win32 API отсутствует; обычного
            # WindowStaysOnTopHint достаточно для Qt-среды.
            pass

    def showEvent(self, event):
        super().showEvent(event)
        self._ensure_topmost()
        try:
            import ctypes
            ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
        except Exception:
            pass
