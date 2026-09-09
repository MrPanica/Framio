# -*- coding: utf-8 -*-
"""
Окно рамки записи экрана (видео MP4 и GIF).
Имеет полую область внутри (setMask) для беспрепятственного взаимодействия с рабочим столом,
яркую цветную рамку снаружи, маркеры изменения размера и плавающую панель управления с таймером,
профессиональными векторными SVG-иконками, кнопкой настроек (выбор приложения/окна) и кнопкой Стоп.
"""

import ctypes
from ctypes import wintypes
from pathlib import Path
from datetime import datetime
from PyQt6.QtCore import Qt, QRect, QRectF, QPoint, pyqtSignal, QSize, QEvent, QObject, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QFrame,
    QApplication, QComboBox
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QRegion, QIcon, QPolygon,
    QPainterPathStroker
)

from recorder.capture_worker import CaptureWorker
from utils.sound import play_capture_sound
from utils.screen_lock import enumerate_recordable_windows, user32
from utils.capture_mask import mask_path_for_frame
from utils.window_icon import get_window_qicon
from config import ConfigManager
from ui.icons import create_themed_icon
from ui.recording_canvas import RecordingDrawingCanvas, RecordingDrawingToolbar
from utils.i18n import tr


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT)
    ]

WM_MOUSEACTIVATE = 0x0021
MA_ACTIVATE = 1

BORDER_THICKNESS = 3
HEADER_HEIGHT = 38
COLLAPSED_HEADER_HEIGHT = 30
HANDLE_SIZE = 8


class HeaderDragFilter(QObject):
    """
    Фильтр событий мыши для плавающей шапки окна записи.
    Позволяет перемещать всю рамку записи по экрану, зажимая любую область шапки,
    с динамической сменой курсора (SizeAllCursor -> ClosedHandCursor).
    Оптимизирован для 60+ FPS без вызова тяжелых перестроений масок DWM во время драга.
    """
    def __init__(self, rec_win):
        super().__init__(rec_win)
        self.rec_win = rec_win
        self.dragging = False
        self.drag_start = QPoint()
        self.start_inner_x = 0
        self.start_inner_y = 0

    def eventFilter(self, watched, event):
        etype = event.type()
        if etype == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                if isinstance(watched, (QPushButton, QComboBox)):
                    return False
                self.rec_win._raise_for_pointer()
                self.dragging = True
                self.drag_start = event.globalPosition().toPoint()
                self.start_inner_x = self.rec_win.inner_x
                self.start_inner_y = self.rec_win.inner_y
                QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
                return True
        elif etype == QEvent.Type.MouseMove:
            if self.dragging:
                gpos = event.globalPosition().toPoint()
                dx = gpos.x() - self.drag_start.x()
                dy = gpos.y() - self.drag_start.y()
                self.rec_win.inner_x = self.start_inner_x + dx
                min_top = self.rec_win._get_min_top()
                self.rec_win.inner_y = max(min_top, self.start_inner_y + dy)
                self.rec_win._sync_position()
                return True
            elif not isinstance(watched, (QPushButton, QComboBox)):
                self.rec_win._raise_for_pointer()
                if QApplication.overrideCursor() is None:
                    watched.setCursor(Qt.CursorShape.SizeAllCursor)
        elif etype == QEvent.Type.MouseButtonRelease:
            if self.dragging and event.button() == Qt.MouseButton.LeftButton:
                self.dragging = False
                if QApplication.overrideCursor() is not None:
                    QApplication.restoreOverrideCursor()
                self.rec_win._sync_geometry()
                return True
        return False


class RecordingSettingsPopup(QFrame):
    window_selected = pyqtSignal(object)  # hwnd or None
    pick_window_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setFixedWidth(290)
        self.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                border: 1px solid #3f3f46;
                border-radius: 8px;
                max-width: 290px;
            }
            QLabel {
                color: #f4f4f5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
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
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        lbl = QLabel(tr("rec_source_label"))
        lbl.setStyleSheet("color: #38bdf8; font-weight: bold;")
        layout.addWidget(lbl)

        self.combo_win = QComboBox()
        self.combo_win.setIconSize(QSize(18, 18))
        self.combo_win.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.combo_win.setMinimumContentsLength(18)
        self.combo_win.setMaxVisibleItems(14)
        self.combo_win.view().setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.combo_win)

        self.btn_pick = QPushButton(tr("rec_pick_window_btn", "Выбрать окно кликом мыши"))
        self.btn_pick.setIcon(create_themed_icon("crosshair", is_dark=True, size=13, custom_color="#38bdf8"))
        self.btn_pick.clicked.connect(self._on_pick_clicked)
        layout.addWidget(self.btn_pick)

        btn_refresh = QPushButton(tr("rec_refresh_windows"))
        btn_refresh.setIcon(create_themed_icon("history", is_dark=True, size=13))
        btn_refresh.clicked.connect(self.populate_windows)
        layout.addWidget(btn_refresh)

        self.combo_win.currentIndexChanged.connect(self._on_combo_changed)

    def _on_pick_clicked(self):
        self.close()
        self.pick_window_clicked.emit()

    def populate_windows(self, current_hwnd=None):
        self.combo_win.blockSignals(True)
        self.combo_win.clear()
        screen_ico = get_window_qicon(0)
        self.combo_win.addItem(screen_ico, tr("rec_source_all_windows"), 0)
        self.combo_win.setItemData(0, tr("rec_source_all_desc"), Qt.ItemDataRole.ToolTipRole)
        selected_idx = 0
        try:
            windows = enumerate_recordable_windows()
            for idx, (hwnd, title) in enumerate(windows, start=1):
                short = title if len(title) <= 26 else title[:24] + "…"
                ico = get_window_qicon(hwnd)
                self.combo_win.addItem(ico, short, hwnd)
                self.combo_win.setItemData(idx, title, Qt.ItemDataRole.ToolTipRole)
                if current_hwnd and hwnd == current_hwnd:
                    selected_idx = idx
        except Exception:
            pass

        self.combo_win.setCurrentIndex(selected_idx)
        self.combo_win.blockSignals(False)

    def _on_combo_changed(self, idx):
        hwnd = self.combo_win.currentData()
        self.window_selected.emit(hwnd)


class RecordingFrameWindow(QWidget):
    recording_closed = pyqtSignal(str)
    geometry_changed = pyqtSignal()
    save_progress = pyqtSignal(str, int, str)  # (filename, percent_0_to_100, stage_desc)

    def __init__(self, mode="video", rect=None, regions=None, region_index=None, region_count=1, record_mic=None, record_system=None, codec=None, target_hwnd=None, capture_mask=None, mask_getter=None, is_fullscreen=False, countdown=None, countdown_seconds=None, filter_type=None, filter_params=None, parent=None):
        super().__init__(parent)
        self.regions = [QRect(int(r.x()), int(r.y()), int(r.width()), int(r.height())) for r in regions] if regions else []
        self.region_index = region_index
        self.region_count = max(1, int(region_count or 1))
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setMouseTracking(True)

        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config

        self.mode = mode
        self.is_fullscreen = is_fullscreen
        self.header_collapsed = False
        self.header_on_bottom = False
        self.record_mic = record_mic if record_mic is not None else getattr(self.cfg, "record_mic", True)
        self.record_system = record_system if record_system is not None else getattr(self.cfg, "record_system", True)
        self.codec = codec if codec is not None else self.cfg.video_codec
        self.target_hwnd = target_hwnd
        self.capture_mask = capture_mask
        self.mask_getter = mask_getter
        self.filter_type = filter_type or "none"
        self.filter_params = dict(filter_params or {})
        self.accent_color = QColor("#ef4444") if mode == "video" else QColor("#a855f7")

        self.countdown_enabled = countdown if countdown is not None else getattr(self.cfg, "record_countdown_enabled", False)
        self.countdown_seconds = countdown_seconds if countdown_seconds is not None else getattr(self.cfg, "record_countdown_seconds", 3)
        self.is_counting_down = False
        self.current_countdown = self.countdown_seconds
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(1000)
        self.countdown_timer.timeout.connect(self._on_countdown_tick)

        screen_geo = QApplication.primaryScreen().geometry()

        if self.is_fullscreen:
            self.inner_x = screen_geo.left()
            self.inner_y = screen_geo.top()
            self.inner_w = screen_geo.width()
            self.inner_h = screen_geo.height()
        elif rect is None or rect.width() < 32 or rect.height() < 32:
            init_w, init_h = 800, 600
            init_x = screen_geo.left() + (screen_geo.width() - init_w) // 2
            init_y = screen_geo.top() + (screen_geo.height() - init_h) // 2
            min_top = screen_geo.top() + BORDER_THICKNESS
            self.inner_x = max(screen_geo.left() + BORDER_THICKNESS, min(init_x, screen_geo.right() - init_w - BORDER_THICKNESS))
            self.inner_y = max(min_top, min(init_y, screen_geo.bottom() - init_h - BORDER_THICKNESS))
            self.inner_w = max(160, min(init_w, screen_geo.width() - 2 * BORDER_THICKNESS))
            self.inner_h = max(100, min(init_h, screen_geo.height() - 2 * BORDER_THICKNESS - HEADER_HEIGHT))
        else:
            init_x = int(rect.x())
            init_y = int(rect.y())
            init_w = int(rect.width())
            init_h = int(rect.height())
            min_top = screen_geo.top() + BORDER_THICKNESS
            self.inner_x = max(screen_geo.left() + BORDER_THICKNESS, min(init_x, screen_geo.right() - init_w - BORDER_THICKNESS))
            self.inner_y = max(min_top, min(init_y, screen_geo.bottom() - init_h - BORDER_THICKNESS))
            self.inner_w = max(160, min(init_w, screen_geo.width() - 2 * BORDER_THICKNESS))
            self.inner_h = max(100, min(init_h, screen_geo.height() - 2 * BORDER_THICKNESS - HEADER_HEIGHT))

        # Маска хранится в локальных координатах исходной зоны. Это нужно
        # для того, чтобы при изменении размера окна рамка и кадр использовали
        # одну и ту же систему координат.
        self.capture_mask_region = (self.inner_w, self.inner_h) if self.capture_mask else None

        self.is_locked = False
        self.is_paused = False
        self.is_resizing = False
        self.is_moving = False
        self.is_saving = False
        self.is_finished = False
        self.draw_bar_visible = False
        self.is_pinned = False
        self.active_handle = 0
        self.drag_start_pos = QPoint()

        self.canvas = None
        self.drawing_toolbar = None
        self.btn_mic = None
        self.btn_system = None
        self.btn_draw = None

        self.capture_worker = None
        self.output_path = ""

        self._init_ui()

        # Создаем холст живого рисования и панель инструментов
        self.canvas = RecordingDrawingCanvas(self)
        self.drawing_toolbar = RecordingDrawingToolbar(self)
        self.drawing_toolbar.hide()
        self.drawing_toolbar.tool_selected.connect(self._on_tool_selected)
        self.drawing_toolbar.color_changed.connect(self.canvas.set_color)
        self.drawing_toolbar.stroke_changed.connect(self.canvas.set_stroke_width)
        self.drawing_toolbar.grain_changed.connect(self.canvas.set_grain)
        self.drawing_toolbar.blur_changed.connect(self.canvas.set_blur)
        self.drawing_toolbar.undo_requested.connect(self.canvas.undo)
        self.drawing_toolbar.clear_requested.connect(self.canvas.clear_all)
        self.drawing_toolbar.pin_toggled.connect(self.canvas.set_pinned)

        # Устанавливаем фильтр перетаскивания за шапку
        self.drag_filter = HeaderDragFilter(self)
        self.header_frame.installEventFilter(self.drag_filter)
        self.lbl_mode.installEventFilter(self.drag_filter)
        self.lbl_mode_icon.installEventFilter(self.drag_filter)
        self.lbl_target_icon.installEventFilter(self.drag_filter)
        self.lbl_timer.installEventFilter(self.drag_filter)
        self.lbl_size.installEventFilter(self.drag_filter)

        try:
            user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
        except Exception:
            pass
        self._sync_geometry()
        if self.countdown_enabled and self.countdown_seconds > 0:
            self._start_countdown()
        else:
            self._start_capture()

    def showEvent(self, event):
        """Повторно исключает окно записи из системного захвата после показа."""
        super().showEvent(event)
        try:
            user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
        except Exception:
            pass
        self.geometry_changed.emit()

    def nativeEvent(self, eventType, message):
        """
        Гарантирует мгновенную доставку клика мыши (в том числе поверх полноэкранных игр),
        предотвращая отбрасывание клика при неактивном окне через WM_MOUSEACTIVATE -> MA_ACTIVATE.
        """
        if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = MSG.from_address(int(message))
                if msg.message == WM_MOUSEACTIVATE:
                    return True, MA_ACTIVATE
            except Exception:
                pass
        return False, 0

    def _init_ui(self):
        self.header_frame = QFrame(self)
        self.header_frame.setGeometry(0, 0, self.width(), HEADER_HEIGHT)
        self.header_frame.setCursor(Qt.CursorShape.SizeAllCursor)
        self.header_frame.setStyleSheet(f"""
            QFrame {{
                background-color: #18181b;
                border-bottom: 2px solid {self.accent_color.name()};
            }}
            QLabel {{
                color: #f4f4f5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
                font-weight: bold;
            }}
            QPushButton {{
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 12px;
                font-weight: 500;
            }}
            QPushButton:hover {{
                background-color: #3f3f46;
            }}
        """)

        layout = QHBoxLayout(self.header_frame)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(6)

        # 1. Векторный индикатор режима и перетаскивание
        self.lbl_mode_icon = QLabel()
        mode_color = "#ef4444" if self.mode == "video" else "#a855f7"
        self.lbl_mode_icon.setPixmap(create_themed_icon("record", is_dark=True, size=12, custom_color=mode_color).pixmap(12, 12))
        self.lbl_mode_icon.setToolTip(tr("rec_status_active", "Индикатор активной записи"))
        self.lbl_mode_icon.setCursor(Qt.CursorShape.SizeAllCursor)
        layout.addWidget(self.lbl_mode_icon)

        mode_text = "REC MP4" if self.mode == "video" else "REC GIF"
        if self.region_index is not None and self.region_count > 1:
            mode_text += f" · ЗОНА {self.region_index}/{self.region_count}"
        self.lbl_mode = QLabel(f"⋮⋮ {mode_text}")
        self.lbl_mode.setStyleSheet(f"color: {self.accent_color.name()}; font-weight: bold;")
        self.lbl_mode.setCursor(Qt.CursorShape.SizeAllCursor)
        self.lbl_mode.setToolTip(tr("rec_tip_drag", "Потяните за шапку, чтобы переместить рамку записи по экрану"))
        layout.addWidget(self.lbl_mode)

        # 1.1 Нативная иконка целевого приложения / окна
        self.lbl_target_icon = QLabel()
        self.lbl_target_icon.setFixedSize(18, 18)
        self.lbl_target_icon.setCursor(Qt.CursorShape.SizeAllCursor)
        self._update_target_icon_display()
        layout.addWidget(self.lbl_target_icon)

        # 2. Интерактивные переключатели звука (векторные SVG)
        if self.mode == "video":
            self.btn_mic = QPushButton()
            self.btn_mic.setFixedSize(28, 26)
            self.btn_mic.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_mic.clicked.connect(self._toggle_mic)
            self._update_mic_button()
            layout.addWidget(self.btn_mic)

            self.btn_system = QPushButton()
            self.btn_system.setFixedSize(28, 26)
            self.btn_system.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_system.clicked.connect(self._toggle_system)
            self._update_system_button()
            layout.addWidget(self.btn_system)

        # 3. Таймер
        self.lbl_timer = QLabel("00:00")
        self.lbl_timer.setCursor(Qt.CursorShape.SizeAllCursor)
        self.lbl_timer.setToolTip(tr("rec_tip_duration", "Длительность текущей записи и число записанных кадров"))
        layout.addWidget(self.lbl_timer)

        # 4. Разрешение
        self.lbl_size = QLabel(f"{self.inner_w}×{self.inner_h}")
        self.lbl_size.setStyleSheet("color: #a1a1aa; font-family: Consolas, monospace;")
        self.lbl_size.setCursor(Qt.CursorShape.SizeAllCursor)
        self.lbl_size.setToolTip(tr("rec_tip_resolution", "Текущий размер выделенной области записи в пикселях"))
        layout.addWidget(self.lbl_size)

        layout.addStretch()

        # 4.5 Кнопка живого рисования и заметок
        self.btn_draw = QPushButton()
        self.btn_draw.setIcon(create_themed_icon("pen", is_dark=True, size=14, custom_color="#38bdf8"))
        self.btn_draw.setIconSize(QSize(14, 14))
        self.btn_draw.setToolTip("Панель инструментов живого рисования и заметок [Ctrl+Z отмена]")
        self.btn_draw.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_draw.setFixedSize(28, 26)
        self.btn_draw.clicked.connect(self._toggle_drawing_bar)
        layout.addWidget(self.btn_draw)

        # 5. Пауза (компактная векторная SVG иконка)
        self.btn_pause = QPushButton()
        self.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=14))
        self.btn_pause.setIconSize(QSize(14, 14))
        self.btn_pause.setToolTip("Приостановить запись [Пробел]")
        self.btn_pause.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pause.setFixedSize(28, 26)
        self.btn_pause.clicked.connect(self._toggle_pause)
        layout.addWidget(self.btn_pause)

        # 6. Стоп (компактная векторная SVG иконка)
        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(create_themed_icon("stop", is_dark=True, size=14, custom_color="#ffffff"))
        self.btn_stop.setIconSize(QSize(14, 14))
        self.btn_stop.setToolTip("Завершить запись и сохранить в файл [Enter / Space]")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setFixedSize(28, 26)
        self.btn_stop.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                border: 1px solid #ef4444;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #b91c1c;
                border: 1px solid #f87171;
            }
            QPushButton:pressed {
                background-color: #991b1b;
            }
        """)
        self.btn_stop.clicked.connect(self.stop_and_save)
        layout.addWidget(self.btn_stop)

        # 7. Замок (векторная SVG иконка)
        self.btn_lock = QPushButton()
        self.btn_lock.setIcon(create_themed_icon("unlock", is_dark=True, size=14))
        self.btn_lock.setIconSize(QSize(14, 14))
        self.btn_lock.setToolTip(tr("rec_tip_lock", "Зафиксировать рамку от случайного изменения размера"))
        self.btn_lock.setFixedSize(28, 26)
        self.btn_lock.clicked.connect(self._toggle_lock)
        layout.addWidget(self.btn_lock)

        # 8. Настройки записи (выбор приложения/окна захвата)
        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(create_themed_icon("settings", is_dark=True, size=14))
        self.btn_settings.setIconSize(QSize(14, 14))
        self.btn_settings.setToolTip(tr("rec_tip_target_win", "Параметры записи (выбор приложения / отдельного окна)"))
        self.btn_settings.setFixedSize(28, 26)
        self.btn_settings.clicked.connect(self._show_settings_popup)
        layout.addWidget(self.btn_settings)

        self.popup_settings = RecordingSettingsPopup(self)
        self.popup_settings.window_selected.connect(self._on_window_changed)
        self.popup_settings.pick_window_clicked.connect(self._start_window_pick_mode)

        # 9. Отмена (векторная SVG иконка)
        btn_cancel = QPushButton()
        btn_cancel.setIcon(create_themed_icon("close", is_dark=True, size=13, custom_color="#fca5a5"))
        btn_cancel.setIconSize(QSize(13, 13))
        btn_cancel.setToolTip(tr("rec_tip_cancel", "Отменить запись без сохранения и удалить файл [Esc]"))
        btn_cancel.setFixedSize(26, 26)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #3f1d1d;
                border: 1px solid #7f1d1d;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #ef4444;
                border-color: #f87171;
            }
        """)
        btn_cancel.clicked.connect(self.cancel_recording)
        layout.addWidget(btn_cancel)

        # Компактное состояние оставляет только эту кнопку, чтобы шапка не
        # закрывала соседние панели и область записи у края экрана.
        self.btn_header_toggle = QPushButton()
        self.btn_header_toggle.setIcon(create_themed_icon("chevron_up", is_dark=True, size=14))
        self.btn_header_toggle.setIconSize(QSize(14, 14))
        self.btn_header_toggle.setFixedSize(26, 26)
        self.btn_header_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_header_toggle.clicked.connect(self._toggle_header_collapsed)
        layout.addWidget(self.btn_header_toggle)

        self._header_content_widgets = [
            self.lbl_mode_icon, self.lbl_mode, self.lbl_target_icon,
            self.lbl_timer, self.lbl_size, self.btn_draw, self.btn_pause,
            self.btn_stop, self.btn_lock, self.btn_settings, btn_cancel,
        ]
        if self.btn_mic is not None:
            self._header_content_widgets.append(self.btn_mic)
        if self.btn_system is not None:
            self._header_content_widgets.append(self.btn_system)
        self._update_header_toggle_ui()

    def _show_settings_popup(self):
        self.popup_settings.populate_windows(self.target_hwnd)
        p = self.btn_settings.mapToGlobal(QPoint(0, self.btn_settings.height() + 4))
        self.popup_settings.move(p)
        self.popup_settings.show()

    def _update_target_icon_display(self):
        if hasattr(self, "lbl_target_icon"):
            if self.target_hwnd and int(self.target_hwnd) > 0:
                ico = get_window_qicon(int(self.target_hwnd), size=16)
                self.lbl_target_icon.setPixmap(ico.pixmap(16, 16))
                try:
                    length = user32.GetWindowTextLengthW(int(self.target_hwnd))
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(int(self.target_hwnd), buf, length + 1)
                    title = buf.value.strip()
                except Exception:
                    title = getattr(self.cfg, "target_window_title", "")
                self.lbl_target_icon.setToolTip(f"Записывается окно: {title}")
            else:
                ico = get_window_qicon(0, size=16)
                self.lbl_target_icon.setPixmap(ico.pixmap(16, 16))
                self.lbl_target_icon.setToolTip(tr("rec_tip_all_screens", "Записывается вся область экрана под рамкой"))

    def _on_window_changed(self, hwnd):
        self.target_hwnd = hwnd
        if self.capture_worker:
            self.capture_worker.set_target_hwnd(hwnd)
        self._update_target_icon_display()
        if hwnd:
            print(f"[RecordingWindow] Выбрано целевое окно HWND {hwnd}")
        else:
            print("[RecordingWindow] Выбран захват всего экрана под рамкой")

    def _get_min_top(self):
        screen = QApplication.primaryScreen()
        return (screen.geometry().top() if screen is not None else 0) + BORDER_THICKNESS

    def _header_height(self):
        if getattr(self, "header_collapsed", False):
            return COLLAPSED_HEADER_HEIGHT
        return HEADER_HEIGHT + (30 if getattr(self, "draw_bar_visible", False) else 0)

    def _update_header_placement(self):
        """Выбирает сторону шапки, чтобы она не закрывала верхнюю часть записи."""
        screen = QApplication.primaryScreen()
        if screen is None:
            self.header_on_bottom = False
            return
        geo = screen.geometry()
        head_h = self._header_height()
        has_space_above = self.inner_y - geo.top() >= head_h + BORDER_THICKNESS
        self.header_on_bottom = not has_space_above

    def _capture_origin_y(self):
        return BORDER_THICKNESS if self.header_on_bottom else self._header_height() + BORDER_THICKNESS

    def _toggle_header_collapsed(self):
        self.header_collapsed = not self.header_collapsed
        if self.header_collapsed:
            self.draw_bar_visible = False
            if self.drawing_toolbar:
                self.drawing_toolbar.hide()
        self._update_header_toggle_ui()
        self._sync_geometry()

    def _update_header_toggle_ui(self):
        if not hasattr(self, "btn_header_toggle"):
            return
        icon_name = "chevron_down" if self.header_collapsed else "chevron_up"
        tip_key = "rec_header_expand_tip" if self.header_collapsed else "rec_header_collapse_tip"
        self.btn_header_toggle.setIcon(create_themed_icon(icon_name, is_dark=True, size=14))
        self.btn_header_toggle.setToolTip(tr(tip_key, "Развернуть шапку" if self.header_collapsed else "Свернуть шапку"))
        for widget in getattr(self, "_header_content_widgets", []):
            widget.setVisible(not self.header_collapsed)
        self.btn_header_toggle.setVisible(True)

    def _toggle_mic(self):
        self.record_mic = not self.record_mic
        if self.capture_worker:
            self.capture_worker.set_mic_muted(not self.record_mic)
        self._update_mic_button()

    def _raise_for_pointer(self):
        """Делает рамку под курсором верхней среди перекрывающихся зон."""
        self.raise_()

    def _update_mic_button(self):
        if not self.btn_mic:
            return
        if self.record_mic:
            self.btn_mic.setIcon(create_themed_icon("mic", is_dark=True, size=14, custom_color="#38bdf8"))
            self.btn_mic.setToolTip(tr("rec_tip_mic_on", "Микрофон включен (кликните для отключения)"))
            self.btn_mic.setStyleSheet("")
        else:
            self.btn_mic.setIcon(create_themed_icon("mic_off", is_dark=True, size=14, custom_color="#ef4444"))
            self.btn_mic.setToolTip(tr("rec_tip_mic_off", "Микрофон отключен (кликните для включения)"))
            self.btn_mic.setStyleSheet("background-color: #3f1d1d; border: 1px solid #7f1d1d;")

    def _toggle_system(self):
        self.record_system = not self.record_system
        if self.capture_worker:
            self.capture_worker.set_system_muted(not self.record_system)
        self._update_system_button()

    def _update_system_button(self):
        if not self.btn_system:
            return
        if self.record_system:
            self.btn_system.setIcon(create_themed_icon("speaker", is_dark=True, size=14, custom_color="#38bdf8"))
            self.btn_system.setToolTip(tr("rec_tip_sys_on", "Системный звук включен (кликните для отключения)"))
            self.btn_system.setStyleSheet("")
        else:
            self.btn_system.setIcon(create_themed_icon("speaker_off", is_dark=True, size=14, custom_color="#ef4444"))
            self.btn_system.setToolTip(tr("rec_tip_sys_off", "Системный звук отключен (кликните для включения)"))
            self.btn_system.setStyleSheet("background-color: #3f1d1d; border: 1px solid #7f1d1d;")

    def _on_tool_selected(self, tool_name: str):
        if self.canvas:
            self.canvas.set_tool(tool_name)
            if tool_name != "cursor":
                self.canvas.raise_()
                self.canvas.activateWindow()

    def _toggle_drawing_bar(self):
        self.draw_bar_visible = not self.draw_bar_visible
        if self.draw_bar_visible:
            self.btn_draw.setStyleSheet("background-color: #0c4a6e; border: 1px solid #0284c7;")
            # При открытии панели рисования автоматически активируем инструмент карандаша,
            # чтобы пользователь мог немедленно рисовать и клики не улетали на фоновые окна
            if self.drawing_toolbar:
                self.drawing_toolbar.select_tool("pen")
        else:
            self.btn_draw.setStyleSheet("")
            # При закрытии панели возвращаем режим курсора со сквозным кликом в фон
            if self.drawing_toolbar:
                self.drawing_toolbar.select_tool("cursor")
            elif self.canvas:
                self.canvas.set_tool("cursor")
        self._sync_geometry()

    def _sync_position(self):
        """
        Легковесная синхронизация координат при перетаскивании рамки без перестроения масок.
        Обеспечивает плавное перемещение со скоростью 60-144 FPS без лагов DWM.
        """
        self._update_header_placement()
        head_h = self._header_height()
        if getattr(self, "is_fullscreen", False):
            self.move(self.inner_x, self.inner_y)
        else:
            win_x = self.inner_x - BORDER_THICKNESS
            win_y = self.inner_y - BORDER_THICKNESS
            if not self.header_on_bottom:
                win_y -= head_h
            self.move(win_x, win_y)

        if self.canvas:
            self.canvas.move(self.inner_x, self.inner_y)
            if getattr(self.canvas, "is_pinned", False):
                self.canvas.update()

    def _sync_geometry(self):
        self._update_header_placement()
        head_h = self._header_height()

        if getattr(self, "is_fullscreen", False):
            win_x = self.inner_x
            win_y = self.inner_y
            win_w = self.inner_w
            win_h = self.inner_h

            self.setGeometry(win_x, win_y, win_w, win_h)
            header_w = min(560, win_w - 40)
            header_x = (win_w - header_w) // 2
            header_y = max(0, self.inner_h - head_h - 6) if self.header_on_bottom else 6
            self.header_frame.setGeometry(header_x, header_y, header_w, COLLAPSED_HEADER_HEIGHT if self.header_collapsed else HEADER_HEIGHT)

            if self.drawing_toolbar:
                if self.draw_bar_visible:
                    toolbar_y = header_y + HEADER_HEIGHT if not self.header_on_bottom else max(0, header_y - 30)
                    self.drawing_toolbar.setGeometry(header_x, toolbar_y, header_w, 30)
                    self.drawing_toolbar.show()
                else:
                    self.drawing_toolbar.hide()

            header_region = QRegion(header_x, header_y, header_w, head_h)
            indicator_region = self._mask_indicator_region(0, 0, self.inner_w, self.inner_h)
            self.setMask(header_region.united(indicator_region))
        else:
            win_x = self.inner_x - BORDER_THICKNESS
            win_y = self.inner_y - BORDER_THICKNESS
            if not self.header_on_bottom:
                win_y -= head_h
            win_w = self.inner_w + 2 * BORDER_THICKNESS
            win_h = self.inner_h + 2 * BORDER_THICKNESS + head_h

            self.setGeometry(win_x, win_y, win_w, win_h)
            header_y = self.inner_h + BORDER_THICKNESS if self.header_on_bottom else 0
            self.header_frame.setGeometry(0, header_y, win_w, COLLAPSED_HEADER_HEIGHT if self.header_collapsed else HEADER_HEIGHT)

            if self.drawing_toolbar:
                if self.draw_bar_visible:
                    toolbar_y = header_y + HEADER_HEIGHT if not self.header_on_bottom else max(BORDER_THICKNESS, header_y - 30)
                    self.drawing_toolbar.setGeometry(0, toolbar_y, win_w, 30)
                    self.drawing_toolbar.show()
                else:
                    self.drawing_toolbar.hide()

            # Вырезаем внутреннюю область, оставляя только шапку и рамку
            outer_region = QRegion(0, 0, win_w, win_h)
            if getattr(self, "regions", None) and len(self.regions) > 1:
                mask_region = outer_region
                for reg in self.regions:
                    rx = int(reg.x() - self.inner_x + BORDER_THICKNESS)
                    ry = int(reg.y() - self.inner_y + self._capture_origin_y())
                    rw = int(reg.width())
                    rh = int(reg.height())
                    mask_region = mask_region.subtracted(QRegion(rx, ry, rw, rh))
            else:
                inner_region = QRegion(BORDER_THICKNESS, self._capture_origin_y(), self.inner_w, self.inner_h)
                mask_region = outer_region.subtracted(inner_region)

            # Внутренняя область обычно вырезается из окна записи, чтобы
            # клики проходили в приложение под ним. Для маски добавляем
            # обратно только узкий штрих её контура: саму запись это не
            # перекрывает, но пользователь видит точную область захвата.
            indicator_region = self._mask_indicator_region(
                BORDER_THICKNESS, self._capture_origin_y(),
                self.inner_w, self.inner_h,
            )
            if not indicator_region.isEmpty():
                mask_region = mask_region.united(indicator_region)
            self.setMask(mask_region)

        if self.canvas:
            self.canvas.sync_to_rec_geometry(self.inner_x, self.inner_y, self.inner_w, self.inner_h)
            if not self.canvas.isVisible():
                self.canvas.show()
            if getattr(self.canvas, "current_tool", "cursor") != "cursor":
                self.canvas.raise_()

        self.lbl_size.setText(f"{self.inner_w}×{self.inner_h}")
        self.update()
        self.geometry_changed.emit()

    def _mask_indicator_region(self, bx: int, by: int, bw: int, bh: int) -> QRegion:
        """Возвращает узкую видимую область для контура одной или нескольких масок."""
        if self.capture_mask is None:
            return QRegion()

        base_w, base_h = self.capture_mask_region or (bw, bh)
        path = mask_path_for_frame(
            self.capture_mask,
            bw,
            bh,
            (0, 0, base_w, base_h),
        )
        if path.isEmpty():
            return QRegion()

        path = path.translated(float(bx), float(by))
        stroker = QPainterPathStroker()
        stroker.setWidth(6.0)
        stroke_path = stroker.createStroke(path)
        result = QRegion()
        for polygon_f in stroke_path.toFillPolygons():
            points = [QPoint(round(point.x()), round(point.y())) for point in polygon_f]
            if len(points) >= 3:
                result = result.united(QRegion(QPolygon(points)))
        return result

    def _start_capture(self):
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        if self.mode == "video":
            ext = "mp4"
            target_dir = self.cfg.save_dir_videos
        else:
            ext = "gif"
            target_dir = self.cfg.save_dir_gifs

        Path(target_dir).mkdir(parents=True, exist_ok=True)
        zone_suffix = f"_Zone-{self.region_index}" if self.region_index is not None and self.region_count > 1 else ""
        self.output_path = str(Path(target_dir) / f"Recording_{timestamp}{zone_suffix}.{ext}")
        fps = self.cfg.video_fps if self.mode == "video" else self.cfg.gif_fps

        # Если выбрано конкретное окно и оно сейчас свёрнуто, автоматически разворачиваем его
        if self.target_hwnd and user32.IsWindow(int(self.target_hwnd)):
            if user32.IsIconic(int(self.target_hwnd)):
                user32.ShowWindow(int(self.target_hwnd), 9)  # SW_RESTORE

        def get_region():
            return (self.inner_x, self.inner_y, self.inner_w, self.inner_h)

        self.capture_worker = CaptureWorker(
            mode=self.mode,
            output_path=self.output_path,
            region_getter=get_region,
            layer_manager=self.canvas.layer_manager if self.canvas else None,
            canvas=self.canvas,
            fps=fps,
            codec=self.codec,
            record_mic=self.record_mic,
            record_system=self.record_system,
            compress_gif=getattr(self.cfg, "compress_gif", True),
            compress_video=getattr(self.cfg, "compress_video", True),
            gif_colors=getattr(self.cfg, "gif_colors", 64),
            gif_dither=getattr(self.cfg, "gif_dither", "none"),
            target_hwnd=self.target_hwnd,
            capture_mask=self.capture_mask,
            mask_getter=self.mask_getter,
            filter_type=self.filter_type,
            filter_params=self.filter_params,
            parent=self
        )
        self.capture_worker.tick.connect(self._on_tick)
        self.capture_worker.save_progress.connect(self.save_progress.emit)
        self.capture_worker.recording_finished.connect(self._on_finished)
        self.capture_worker.start()

    def _on_tick(self, elapsed_sec: float, frame_count: int):
        m = int(elapsed_sec // 60)
        s = int(elapsed_sec % 60)
        self.lbl_timer.setText(f"{m:02d}:{s:02d} ({frame_count} кадр.)")

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        mode_text = "REC MP4" if self.mode == "video" else "REC GIF"
        if self.is_paused:
            self.capture_worker.pause()
            self.btn_pause.setIcon(create_themed_icon("play", is_dark=True, size=14, custom_color="#22c55e"))
            self.btn_pause.setToolTip("Возобновить запись [Пробел]")
            self.lbl_mode.setText("⋮⋮ ПАУЗА")
        else:
            self.capture_worker.resume()
            self.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=14))
            self.btn_pause.setToolTip("Приостановить запись [Пробел]")
            self.lbl_mode.setText(f"⋮⋮ {mode_text}")

    def set_paused(self, paused: bool):
        """Устанавливает состояние паузы без переключения наугад."""
        paused = bool(paused)
        if self.is_paused != paused:
            self._toggle_pause()

    def _toggle_lock(self):
        self.is_locked = not self.is_locked
        if self.is_locked:
            self.btn_lock.setIcon(create_themed_icon("lock", is_dark=True, size=14))
            self.btn_lock.setToolTip(tr("rec_tip_unlock", "Разблокировать рамку для изменения размера"))
            self.btn_lock.setStyleSheet("background-color: #b45309;")
        else:
            self.btn_lock.setIcon(create_themed_icon("unlock", is_dark=True, size=14))
            self.btn_lock.setToolTip(tr("rec_tip_lock", "Зафиксировать рамку от случайного изменения размера"))
            self.btn_lock.setStyleSheet("")
        self.update()

    def _start_countdown(self):
        self.is_counting_down = True
        self.current_countdown = self.countdown_seconds
        start_txt = tr("rec_mode_countdown_prefix", sec=self.current_countdown)
        self.lbl_mode.setText(f"⋮⋮ {start_txt}")
        self.lbl_mode.setStyleSheet("color: #eab308; font-weight: bold;")
        self.lbl_timer.setText(tr("rec_countdown_status", sec=self.current_countdown))
        if self.canvas:
            self.canvas.set_countdown(self.current_countdown)
        self.countdown_timer.start()

    def _on_countdown_tick(self):
        self.current_countdown -= 1
        if self.current_countdown > 0:
            start_txt = tr("rec_mode_countdown_prefix", sec=self.current_countdown)
            self.lbl_mode.setText(f"⋮⋮ {start_txt}")
            self.lbl_timer.setText(tr("rec_countdown_status", sec=self.current_countdown))
            if self.canvas:
                self.canvas.set_countdown(self.current_countdown)
        else:
            self.countdown_timer.stop()
            self.is_counting_down = False
            if self.canvas:
                self.canvas.clear_countdown()
            mode_text = "REC MP4" if self.mode == "video" else "REC GIF"
            self.lbl_mode.setText(f"⋮⋮ {mode_text}")
            self.lbl_mode.setStyleSheet(f"color: {self.accent_color.name()}; font-weight: bold;")
            play_capture_sound()
            self._start_capture()

    def snap_to_window(self, hwnd: int):
        if not hwnd or not user32.IsWindow(int(hwnd)):
            return
        # Свёрнутые окна из списка имеют служебный rect около 160x28. Их всё
        # равно нужно принять как цель записи; реальный rect будет получен
        # после восстановления окна в _start_capture().
        self.target_hwnd = hwnd
        rect = wintypes.RECT()
        if user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            wx = rect.left
            wy = rect.top
            ww = rect.right - rect.left
            wh = rect.bottom - rect.top
            if ww > 50 and wh > 50:
                self.inner_x = wx
                self.inner_y = max(self._get_min_top(), wy)
                self.inner_w = ww
                self.inner_h = wh
                if self.capture_worker:
                    self.capture_worker.set_target_hwnd(hwnd)
                self._update_target_icon_display()
                self._sync_geometry()
        self._update_target_icon_display()
        if self.capture_worker:
            self.capture_worker.set_target_hwnd(hwnd)

    def _start_window_pick_mode(self):
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QCursor
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #18181b;
                border: 1px solid #38bdf8;
                border-radius: 6px;
                padding: 4px;
                color: #f4f4f5;
            }
            QMenu::item {
                padding: 6px 24px 6px 12px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
        """)
        try:
            windows = enumerate_recordable_windows()
            for hwnd, title in windows:
                ico = get_window_qicon(hwnd)
                act = menu.addAction(ico, title)
                act.triggered.connect(lambda checked, h=hwnd: self.snap_to_window(h))
        except Exception:
            pass
        menu.exec(QCursor.pos())

    def stop_recording(self):
        """Псевдоним для совместимости с внешними вызовами."""
        self.stop_and_save()

    def stop_and_save(self):
        if self.is_counting_down:
            self.countdown_timer.stop()
            self.is_counting_down = False
            if self.canvas:
                self.canvas.clear_countdown()
            play_capture_sound()
            self._start_capture()
            return

        if self.capture_worker is None or self.is_saving:
            return
        self.is_saving = True

        # 1. Мгновенно скрываем рамку записи и холст рисования с экрана!
        self.hide()
        if self.canvas:
            self.canvas.hide()
            self.canvas.close()

        # 2. Для массовой записи итоговое уведомление будет одно на всю
        # группу. Поэтому не засыпаем пользователя одинаковыми сообщениями
        # при остановке 4–5 зон одновременно.
        app_inst = getattr(QApplication.instance(), "app_instance", None)
        if app_inst and self.region_count <= 1:
            is_gif = self.mode == "gif"
            title = tr("notif_rec_saving_gif", "Сохранение GIF...") if is_gif else tr("notif_rec_saving_video", "Сохранение видео...")
            body = tr("rec_exporting_wait", "Идёт оптимизация и кодирование в высоком качестве (в фоне)...")
            app_inst.show_notification(
                f"⏳ {title}",
                body,
                timeout=3500
            )

        # 3. Останавливаем рабочий поток и запускаем финализацию файла
        self.capture_worker.stop()

    def cancel_recording(self):
        if getattr(self, "is_counting_down", False):
            self.countdown_timer.stop()
            self.is_counting_down = False
            if self.canvas:
                self.canvas.clear_countdown()

        self.hide()
        if self.canvas:
            self.canvas.hide()
            self.canvas.close()
        self.is_saving = True
        self.is_finished = True
        if self.capture_worker:
            if hasattr(self.capture_worker, "cancel"):
                self.capture_worker.cancel()
            else:
                self.capture_worker.stop()
        try:
            p = Path(self.output_path)
            if p.exists():
                p.unlink()
        except Exception:
            pass
        self.recording_closed.emit("")
        self.close()

    def wait_for_shutdown(self, timeout_ms: int = 10000) -> bool:
        """Дожидается остановки потока захвата и всех его дочерних рекордеров."""
        worker = self.capture_worker
        if worker is None:
            return True

        if worker.isRunning():
            if hasattr(worker, "cancel"):
                worker.cancel()
            if not worker.wait(max(0, int(timeout_ms))):
                print("[RecordingWindow] Поток записи не завершился за отведённое время.")
                return False

        return not worker.isRunning()

    def _on_finished(self, out_path: str):
        self.is_finished = True
        if self.canvas:
            self.canvas.close()
        if self.cfg.play_sound:
            play_capture_sound()
        print(f"[RecordingWindow] Запись завершена: {out_path}")
        self.recording_closed.emit(out_path)
        self.close()

    def closeEvent(self, event):
        if self.canvas:
            self.canvas.close()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_Z:
            if self.canvas:
                self.canvas.undo()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if getattr(self, "is_counting_down", False):
                self.countdown_timer.stop()
                self.is_counting_down = False
                if self.canvas:
                    self.canvas.clear_countdown()
                mode_text = "REC MP4" if self.mode == "video" else "REC GIF"
                self.lbl_mode.setText(f"⋮⋮ {mode_text}")
                self.lbl_mode.setStyleSheet(f"color: {self.accent_color.name()}; font-weight: bold;")
                play_capture_sound()
                self._start_capture()
                return
            self.stop_and_save()
        elif event.key() == Qt.Key.Key_Escape:
            if getattr(self, "is_counting_down", False):
                self.cancel_recording()
                return
            self.stop_and_save()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        if getattr(self, "is_fullscreen", False):
            # У полноэкранной записи видна только шапка. Если задана маска,
            # оставляем её тонкий контур видимым и в этом режиме.
            if self.capture_mask is not None:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                mask_path = mask_path_for_frame(
                    self.capture_mask,
                    self.inner_w,
                    self.inner_h,
                    (0, 0, *(self.capture_mask_region or (self.inner_w, self.inner_h))),
                )
                if not mask_path.isEmpty():
                    painter.setPen(QPen(
                        QColor("#38bdf8"),
                        1.5,
                        Qt.PenStyle.DashLine,
                        Qt.PenCapStyle.RoundCap,
                        Qt.PenJoinStyle.RoundJoin,
                    ))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(mask_path)
                painter.end()
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        head_h = self._header_height()
        bx = BORDER_THICKNESS
        by = self._capture_origin_y()
        bw = self.inner_w
        bh = self.inner_h

        pen = QPen(self.accent_color, BORDER_THICKNESS)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        # Красная рамка всегда показывает границы зоны записи.
        painter.drawRect(
            bx - BORDER_THICKNESS // 2,
            by - BORDER_THICKNESS // 2,
            bw + BORDER_THICKNESS,
            bh + BORDER_THICKNESS,
        )

        # Отдельный тонкий пунктир показывает фактический контур маски,
        # который попадёт в кадр. Он намеренно отличается от красной рамки,
        # чтобы область захвата была понятна во время записи.
        mask_bounds = None
        if self.capture_mask is not None:
            base_w, base_h = self.capture_mask_region or (bw, bh)
            mask_path = mask_path_for_frame(
                self.capture_mask,
                bw,
                bh,
                (0, 0, base_w, base_h),
            )
            if not mask_path.isEmpty():
                painter.setPen(QPen(
                    QColor("#38bdf8"),
                    1.5,
                    Qt.PenStyle.DashLine,
                    Qt.PenCapStyle.RoundCap,
                    Qt.PenJoinStyle.RoundJoin,
                ))
                painter.drawPath(mask_path.translated(float(bx), float(by)))
                mask_bounds = mask_path.boundingRect().translated(float(bx), float(by))

        if getattr(self, "regions", None) and len(self.regions) > 1:
            pen_sub = QPen(QColor(self.accent_color.red(), self.accent_color.green(), self.accent_color.blue(), 180), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen_sub)
            for reg in self.regions:
                rx = int(reg.x() - self.inner_x + BORDER_THICKNESS)
                ry = int(reg.y() - self.inner_y + self._capture_origin_y())
                rw = int(reg.width())
                rh = int(reg.height())
                painter.drawRect(rx, ry, rw, rh)

        # Маркеры изменения размера
        if not self.is_locked:
            painter.setPen(QPen(QColor(255, 255, 255), 1.5))
            painter.setBrush(QBrush(self.accent_color))
            hs = HANDLE_SIZE

            handle_rect = mask_bounds or QRectF(float(bx), float(by), float(bw), float(bh))

            points = [
                QPoint(int(handle_rect.left()), int(handle_rect.top())),
                QPoint(int(handle_rect.center().x()), int(handle_rect.top())),
                QPoint(int(handle_rect.right()), int(handle_rect.top())),
                QPoint(int(handle_rect.right()), int(handle_rect.center().y())),
                QPoint(int(handle_rect.right()), int(handle_rect.bottom())),
                QPoint(int(handle_rect.center().x()), int(handle_rect.bottom())),
                QPoint(int(handle_rect.left()), int(handle_rect.bottom())),
                QPoint(int(handle_rect.left()), int(handle_rect.center().y()))
            ]
            for p in points:
                painter.drawRect(p.x() - hs//2, p.y() - hs//2, hs, hs)

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return

        pos = event.position().toPoint()
        if self.header_frame.geometry().contains(pos):
            self._raise_for_pointer()
            self.is_moving = True
            self.drag_start_pos = event.globalPosition().toPoint()
            self.start_inner_x = self.inner_x
            self.start_inner_y = self.inner_y
            QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
            return

        if not self.is_locked:
            h = self._hit_test_handle(pos)
            if h > 0:
                self._raise_for_pointer()
                self.is_resizing = True
                self.active_handle = h
                self.drag_start_pos = event.globalPosition().toPoint()
                self.start_inner_x = self.inner_x
                self.start_inner_y = self.inner_y
                self.start_inner_w = self.inner_w
                self.start_inner_h = self.inner_h

    def mouseMoveEvent(self, event):
        gpos = event.globalPosition().toPoint()

        if self.is_moving:
            dx = gpos.x() - self.drag_start_pos.x()
            dy = gpos.y() - self.drag_start_pos.y()
            self.inner_x = self.start_inner_x + dx
            min_top = self._get_min_top()
            self.inner_y = max(min_top, self.start_inner_y + dy)
            self._sync_position()
            return

        if self.is_resizing and not self.is_locked:
            dx = gpos.x() - self.drag_start_pos.x()
            dy = gpos.y() - self.drag_start_pos.y()
            min_top = self._get_min_top()

            if self.active_handle in (1, 6, 7):
                new_w = max(160, self.start_inner_w - dx)
                self.inner_x = self.start_inner_x + (self.start_inner_w - new_w)
                self.inner_w = new_w
            if self.active_handle in (3, 4, 5):
                self.inner_w = max(160, self.start_inner_w + dx)
            if self.active_handle in (1, 2, 3):
                new_h = max(100, self.start_inner_h - dy)
                self.inner_y = max(min_top, self.start_inner_y + (self.start_inner_h - new_h))
                self.inner_h = new_h
            if self.active_handle in (5, 6, 7):
                self.inner_h = max(100, self.start_inner_h + dy)

            self._sync_geometry()
            return

        pos = event.position().toPoint()
        if not self.is_locked:
            h = self._hit_test_handle(pos)
            if h > 0:
                self._raise_for_pointer()
                if h in (1, 5): self.setCursor(Qt.CursorShape.SizeFDiagCursor)
                elif h in (3, 7): self.setCursor(Qt.CursorShape.SizeBDiagCursor)
                elif h in (2, 6): self.setCursor(Qt.CursorShape.SizeVerCursor)
                else: self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self.header_frame.geometry().contains(pos):
                self._raise_for_pointer()
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else: self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self.is_moving:
            self.is_moving = False
            if QApplication.overrideCursor() is not None:
                QApplication.restoreOverrideCursor()
            self._sync_geometry()
        self.is_resizing = False
        self.active_handle = 0

    def _hit_test_handle(self, pt: QPoint):
        bx = BORDER_THICKNESS
        by = self._capture_origin_y()
        bw, bh = self.inner_w, self.inner_h
        hs = HANDLE_SIZE + 4

        if QRect(bx - hs, by - hs, hs*2, hs*2).contains(pt): return 1
        if QRect(bx + bw//2 - hs, by - hs, hs*2, hs*2).contains(pt): return 2
        if QRect(bx + bw - hs, by - hs, hs*2, hs*2).contains(pt): return 3
        if QRect(bx + bw - hs, by + bh//2 - hs, hs*2, hs*2).contains(pt): return 4
        if QRect(bx + bw - hs, by + bh - hs, hs*2, hs*2).contains(pt): return 5
        if QRect(bx + bw//2 - hs, by + bh - hs, hs*2, hs*2).contains(pt): return 6
        if QRect(bx - hs, by + bh - hs, hs*2, hs*2).contains(pt): return 7
        if QRect(bx - hs, by + bh//2 - hs, hs*2, hs*2).contains(pt): return 8
        return 0
