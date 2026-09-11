# -*- coding: utf-8 -*-
"""
Холст живого рисования поверх рамки видеозаписи (RecordingDrawingCanvas)
и компактная панель инструментов аннотирования (RecordingDrawingToolbar).
Поддерживает режим сквозного клика (WA_TransparentForMouseEvents), перетаскивание фигур ПКМ,
контекстное редактирование свойств фигур (ShapeEditPopup), окно слоёв (LayersDialog),
палитру выбора цветов (ColorPalettePopup) и плавное закрепление рисунков (Pin).
"""

import sys
import ctypes
from ctypes import wintypes
import threading
from PyQt6.QtCore import Qt, QRect, QPoint, QPointF, QRectF, pyqtSignal, QSize, QTimer
from PyQt6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QPushButton, QLabel, QLineEdit,
    QMenu, QApplication
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QCursor, QAction, QIcon, QPixmap
)

from models.shapes import (
    BaseShape, PenShape, LineShape, ArrowShape, RectangleShape,
    CircleShape, TextShape, MosaicShape, BlurShape, RegionalEffectShape,
    get_filtered_pixmap
)
from models.layers import LayerManager
from models.history import HistoryManager, HistoryCommand
from ui.icons import create_themed_icon
from ui.shape_editor import ShapeEditPopup
from ui.toolbars import show_smart_popup, get_context_menu_style, get_theme_styles
from ui.layers_dialog import LayersDialog
from ui.widgets import ColorPalettePopup
from ui.transform_box import ShapeTransformBox, HandleType
from utils.screen_lock import safe_grab_screen_pixmap
from utils.image_filters import FilterType
from utils.i18n import tr

user32 = ctypes.windll.user32


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


class RecordingDrawingCanvas(QWidget):
    """
    Прозрачный холст рисования поверх области записи.
    Когда активен инструмент «Курсор/Взаимодействие», холст на 100% пропускает клики сквозь себя в рабочий стол.
    Когда выбран инструмент рисования, холст перехватывает мышь и плавно рисует аннотации (60+ FPS).
    Поддерживает перемещение фигур зажатием ПКМ, вызов меню управления при удержании/клике ПКМ,
    управление слоями и бесшовное закрепление (Pin/Unpin).
    """
    def __init__(self, rec_win, parent=None):
        super().__init__(parent)
        self.rec_win = rec_win
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setMouseTracking(True)

        try:
            user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
        except Exception:
            pass

        self.shape_lock = threading.Lock()
        self.layer_manager = LayerManager(self)
        self.history_manager = HistoryManager(self)
        self.layer_manager.layers_changed.connect(self.update)
        self.layer_manager.layers_changed.connect(self._sync_live_effect_timer)

        self.current_tool = "cursor"  # cursor, pen, arrow, rect, highlighter, text
        self.current_color = "#ef4444"
        self.stroke_width = 4
        self.current_grain = 8
        self.current_blur = 15
        # Режим регионального эффекта (цензуры): mosaic, blur, grayscale, invert, vibrant, sepia
        self.current_effect_mode = "mosaic"
        # Живой фон нужен только пока на холсте есть мозаика/блюр. В простое
        # таймер не работает и не запускает дорогостоящий захват экрана.
        self._live_effect_background = None
        self._live_effect_timer = QTimer(self)
        self._live_effect_timer.setInterval(100)
        self._live_effect_timer.timeout.connect(self._refresh_live_effect_background)
        # По умолчанию рисунки привязаны к рамке (перемещаются вместе с ней плавно)
        self.is_pinned = False
        self.temp_shape = None
        self.drag_start = QPointF()

        # Состояние перемещения фигур ПКМ и контекстного меню
        self.dragged_shape = None
        self.right_clicked_shape = None
        self.active_editing_shape = None
        self.shape_drag_start = QPointF()
        self.shape_drag_initial_pos = QPointF()
        self.right_press_global_pos = None
        self.shape_edit_popup = None
        # Рамка трансформации фигур (Photoshop / Figma style)
        self.transform_box = ShapeTransformBox()
        self.is_transforming = False

        self.right_hold_timer = QTimer(self)
        self.right_hold_timer.setSingleShot(True)
        self.right_hold_timer.timeout.connect(self._on_right_hold_timeout)

        # Редактор текста по клику
        self.text_editor = QLineEdit(self)
        self.text_editor.hide()
        self.text_editor.setStyleSheet("""
            QLineEdit {
                background: rgba(24, 24, 27, 230);
                color: #ffffff;
                border: 1px solid #38bdf8;
                border-radius: 4px;
                padding: 2px 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 16px;
                font-weight: bold;
            }
        """)
        self.text_editor.returnPressed.connect(self._commit_text)
        self.text_editor.editingFinished.connect(self._commit_text)
        self.text_pos = QPointF()
        self.is_protected_zone = False

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass
        self._sync_live_effect_timer()

    def hideEvent(self, event):
        self._live_effect_timer.stop()
        super().hideEvent(event)

    def nativeEvent(self, eventType, message):
        """
        Гарантирует надёжный перехват клика мыши поверх любых окон (видео, плееры, браузер, рабочий стол),
        предотвращая отбрасывание клика в фоновое приложение через WM_MOUSEACTIVATE -> MA_ACTIVATE.
        """
        if eventType in (b"windows_generic_MSG", "windows_generic_MSG"):
            try:
                msg = MSG.from_address(int(message))
                if msg.message == WM_MOUSEACTIVATE:
                    return True, MA_ACTIVATE
            except Exception:
                pass
        return False, 0

    def set_protected_zone(self, enabled: bool):
        """Устанавливает режим защищённой/осязаемой зоны: клики не проходят в фон."""
        self.is_protected_zone = bool(enabled)
        if self.current_tool == "cursor":
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not self.is_protected_zone)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        if self.is_protected_zone:
            self.raise_()
            self.activateWindow()
        self.update()

    def set_tool(self, tool_name: str):
        self.current_tool = tool_name
        if tool_name == "cursor":
            if not getattr(self, "is_protected_zone", False):
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            else:
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            self.setCursor(Qt.CursorShape.CrossCursor)
        self._sync_live_effect_timer()
        self.update()

    def set_color(self, color_hex: str):
        self.current_color = color_hex
        self.update()

    def set_stroke_width(self, width: int):
        self.stroke_width = max(1, min(32, int(width)))
        self.update()

    def set_grain(self, grain: int):
        self.current_grain = max(3, min(30, int(grain)))
        self.update()

    def set_blur(self, blur_radius: int):
        self.current_blur = max(3, min(45, int(blur_radius)))
        self.update()

    def set_effect_mode(self, mode: str):
        """Устанавливает режим регионального эффекта: mosaic, blur, grayscale, invert, vibrant, sepia."""
        self.current_effect_mode = mode
        self._sync_live_effect_timer()

    def _has_live_effects(self) -> bool:
        """Проверяет, нужен ли холсту периодический фон для живого эффекта."""
        shapes = list(self.layer_manager.shapes)
        if self.temp_shape is not None:
            shapes.append(self.temp_shape)
        return any(
            getattr(shape, "visible", True)
            and (
                getattr(shape, "is_mosaic", False)
                or getattr(shape, "is_blur", False)
                or shape.__class__.__name__ in ("MosaicShape", "BlurShape", "RegionalEffectShape")
            )
            for shape in shapes
        )

    def _sync_live_effect_timer(self):
        """Включает обновление живого фона только для активной мозаики/блюра/эффекта."""
        rec_filter = getattr(self.rec_win, "filter_type", "none") if hasattr(self, "rec_win") else "none"
        needs_background = self.isVisible() and (
            self.current_tool in ("mosaic", "blur")
            or (rec_filter and rec_filter != "none")
            or self._has_live_effects()
        )
        if needs_background:
            if self._live_effect_background is None:
                self._refresh_live_effect_background()
            if not self._live_effect_timer.isActive():
                self._live_effect_timer.start()
        else:
            self._live_effect_timer.stop()
            self._live_effect_background = None

    def _refresh_live_effect_background(self):
        """Захватывает фон под холстом для живого предпросмотра цензуры."""
        if not self.isVisible() or not getattr(self.rec_win, "isVisible", lambda: True)():
            return
        try:
            pixmap = safe_grab_screen_pixmap(
                int(self.rec_win.inner_x),
                int(self.rec_win.inner_y),
                max(1, int(self.width())),
                max(1, int(self.height())),
            )
            if pixmap is not None and not pixmap.isNull():
                self._live_effect_background = pixmap
                self.update()
        except Exception:
            # Живой предпросмотр не должен мешать записи, если захват экрана
            # временно недоступен (например, при смене рабочего стола).
            pass

    def set_pinned(self, pinned: bool):
        """
        Переключает режим закрепления (Pin) на экране.
        При включении/выключении Pin автоматически пересчитывает координаты всех существующих фигур,
        чтобы они ни на один пиксель не смещались и не прыгали!
        """
        if self.is_pinned == pinned:
            return

        ix = float(self.rec_win.inner_x)
        iy = float(self.rec_win.inner_y)

        if pinned:
            # Переход из frame-relative в screen-relative (+inner_x, +inner_y)
            for shape in self.layer_manager.shapes:
                shape.translate(ix, iy)
        else:
            # Переход из screen-relative во frame-relative (-inner_x, -inner_y)
            for shape in self.layer_manager.shapes:
                shape.translate(-ix, -iy)

        self.is_pinned = pinned
        self.layer_manager.is_pinned = pinned
        self.update()

    def undo(self):
        with self.shape_lock:
            self.history_manager.undo()
        self.update()

    def clear_all(self):
        with self.shape_lock:
            if not self.layer_manager.shapes:
                return
            old_shapes = list(self.layer_manager.shapes)
            def do_clear():
                with self.shape_lock:
                    self.layer_manager.clear()
                self.update()
            def undo_clear():
                with self.shape_lock:
                    self.layer_manager.shapes = list(old_shapes)
                self.update()
            cmd = HistoryCommand(tr("draw_clear"), do_func=do_clear, undo_func=undo_clear)
            self.history_manager.push_already_done(cmd)
            do_clear()

    def sync_to_rec_geometry(self, x: int, y: int, w: int, h: int):
        self.setGeometry(x, y, w, h)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Если выбран инструмент рисования (не курсор) или активна защита зоны — заливаем холст почти прозрачным цветом (alpha=2).
        # На экране это на 100% невидимо, но Windows DWM перехватывает клики и доставляет их
        # на холст рисования, не пропуская в плеер YouTube или фоновые окна!
        if self.current_tool != "cursor" or getattr(self, "is_protected_zone", False):
            painter.fillRect(self.rect(), QColor(0, 0, 0, 2))

        # Если в шапке выбран общий фильтр записи (ч/б, блюр, инверсия, сепия и т.д.) —
        # отображаем его живой предпросмотр на холсте
        rec_filter = getattr(self.rec_win, "filter_type", "none") if hasattr(self, "rec_win") else "none"
        if rec_filter and rec_filter not in ("none", FilterType.NONE) and self._live_effect_background is not None:
            params = getattr(self.rec_win, "filter_params", {}) if hasattr(self, "rec_win") else {}
            intensity = int(params.get("blur_radius", 15) if rec_filter == "blur" else params.get("pixel_size", 12))
            filtered_bg = get_filtered_pixmap(self._live_effect_background, rec_filter, intensity)
            if filtered_bg is not None and not filtered_bg.isNull():
                painter.drawPixmap(0, 0, filtered_bg)

        # Рисуем все слои
        # Если закреплено (pinned) — координаты фигур хранятся в абсолютных экранных координатах,
        # поэтому смещаем отрисовку на (-inner_x, -inner_y).
        # Если не закреплено — координаты локальны для рамки, offset = (0, 0).
        offset = QPointF(float(self.rec_win.inner_x), float(self.rec_win.inner_y)) if self.is_pinned else QPointF(0.0, 0.0)
        self.layer_manager.draw_all(
            painter,
            offset=offset,
            source_pixmap=self._live_effect_background,
        )

        if self.temp_shape is not None:
            self.temp_shape.draw(
                painter,
                offset=offset,
                source_pixmap=self._live_effect_background,
            )
            if hasattr(self.temp_shape, "rect") and (
                isinstance(self.temp_shape, (MosaicShape, BlurShape, RegionalEffectShape))
                or getattr(self.temp_shape, "is_mosaic", False)
                or getattr(self.temp_shape, "is_blur", False)
            ):
                r = self.temp_shape.rect.translated(-offset.x(), -offset.y()).normalized()
                if not r.isEmpty() and r.width() > 1 and r.height() > 1:
                    painter.save()
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                    pen_bg = QPen(QColor(0, 0, 0, 200), 1.5, Qt.PenStyle.SolidLine)
                    painter.setPen(pen_bg)
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawRect(r)
                    pen_dash = QPen(QColor(56, 189, 248), 1.5, Qt.PenStyle.DashLine)
                    pen_dash.setDashPattern([4, 4])
                    painter.setPen(pen_dash)
                    painter.drawRect(r)
                    painter.restore()

        self._draw_dragged_shape_indicator(painter, offset=offset)

    def set_countdown(self, seconds: int):
        self.countdown_val = seconds
        self.update()

    def clear_countdown(self):
        self.countdown_val = 0
        self.update()

    def _draw_countdown_overlay(self, painter: QPainter):
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        cx = w / 2.0
        cy = h / 2.0

        r = 52.0
        circle_rect = QRectF(cx - r, cy - r - 16.0, r * 2.0, r * 2.0)

        painter.setPen(QPen(QColor(234, 179, 8, 230), 3.5))
        painter.setBrush(QColor(24, 24, 27, 225))
        painter.drawEllipse(circle_rect)

        font_num = QFont("Segoe UI", 44)
        font_num.setBold(True)
        painter.setFont(font_num)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(circle_rect, Qt.AlignmentFlag.AlignCenter, str(self.countdown_val))

        hint_text = tr("rec_countdown_hint", "Приготовьтесь... (Esc для отмены)")
        font_hint = QFont("Segoe UI", 10)
        font_hint.setBold(True)
        painter.setFont(font_hint)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(hint_text) + 20
        badge_rect = QRectF(cx - text_w / 2.0, cy + r - 6.0, text_w, 26.0)

        painter.setPen(QPen(QColor(56, 189, 248, 160), 1))
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawRoundedRect(badge_rect, 5, 5)

        painter.setPen(QColor(255, 255, 255))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, hint_text)

        painter.restore()

    def _apply_shape_geometry(self, target: BaseShape, source: BaseShape):
        """Копирует геометрию из сохраненного состояния в целевую фигуру для Undo/Redo."""
        if not target or not source:
            return
        import copy
        with self.shape_lock:
            for attr in ("rect", "p1", "p2", "points", "path", "pos", "font_size", "rotation"):
                if hasattr(source, attr):
                    val = getattr(source, attr)
                    setattr(target, attr, copy.copy(val) if attr in ("points", "path", "rect") else val)
            if hasattr(target, "cached_mosaic"):
                target.cached_mosaic = None
            if hasattr(target, "cached_blur"):
                target.cached_blur = None

    def _draw_dragged_shape_indicator(self, painter: QPainter, offset: QPointF = QPointF(0, 0)):
        """Отрисовывает контур выделения и рамку трансформации в стиле Photoshop / Figma вокруг активной фигуры."""
        active_shape = None
        if hasattr(self, "transform_box") and self.transform_box.is_active():
            active_shape = self.transform_box.shape
        elif getattr(self, "dragged_shape", None):
            active_shape = self.dragged_shape
        elif getattr(self, "active_editing_shape", None):
            active_shape = self.active_editing_shape

        if active_shape is None or not active_shape.visible or active_shape not in self.layer_manager.shapes:
            return

        # 1. Отрисовываем интерактивную рамку с маркерами
        if hasattr(self, "transform_box") and self.transform_box.is_active():
            self.transform_box.draw(painter, offset=offset)

        bbox = active_shape.get_bounding_rect()
        if not bbox.isValid() or bbox.isEmpty():
            return

        shifted_bbox = bbox.translated(-offset.x(), -offset.y())
        pad = 6.0
        frame_rect = shifted_bbox.adjusted(-pad, -pad, pad, pad)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if not hasattr(self, "transform_box") or not self.transform_box.is_active():
            pen_border = QPen(QColor(56, 189, 248, 220), 1.5, Qt.PenStyle.DashLine)
            pen_border.setDashPattern([5, 3])
            painter.setPen(pen_border)
            painter.setBrush(QColor(56, 189, 248, 25))
            painter.drawRoundedRect(frame_rect, 4, 4)

        try:
            shape_idx = self.layer_manager.shapes.index(active_shape) + 1
        except Exception:
            shape_idx = 1

        rot = getattr(active_shape, "rotation", 0.0)
        rot_str = f" ({round(rot)}°)" if rot != 0.0 else ""
        badge_text = f"{active_shape.name} #{shape_idx}{rot_str}"
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(badge_text)
        text_h = fm.height()

        badge_w = text_w + 14
        badge_h = max(20, text_h + 4)
        top_y = frame_rect.top() - 28.0 - badge_h
        if top_y < 4:
            top_y = frame_rect.bottom() + 6.0

        badge_rect = QRectF(frame_rect.center().x() - badge_w / 2.0, top_y, badge_w, badge_h)
        painter.setPen(QPen(QColor(56, 189, 248, 180), 1))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(badge_rect, 4, 4)

        painter.setPen(QColor(248, 250, 252))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)
        painter.restore()

    def find_shape_at(self, pos: QPointF):
        """Возвращает верхнюю видимую фигуру под точкой pos или None."""
        for shape in reversed(self.layer_manager.shapes):
            if shape.visible and shape.hit_test_rotated(pos):
                return shape
        return None

    def mousePressEvent(self, event):
        pos = event.position()
        offset = QPointF(float(self.rec_win.inner_x), float(self.rec_win.inner_y)) if self.is_pinned else QPointF(0.0, 0.0)
        test_pos = pos + offset

        # 1. Обработка ПКМ (правая кнопка мыши): трансформация фигур или контекстное меню
        if event.button() == Qt.MouseButton.RightButton:
            # Сначала проверяем маркеры активной рамки трансформации
            if self.transform_box.is_active():
                h = self.transform_box.hit_test_handle(pos, offset=offset)
                if h != HandleType.NONE:
                    self.is_transforming = True
                    self.transform_box.start_drag(h, pos)
                    self.shape_drag_initial_pos = pos
                    self.right_press_global_pos = event.globalPosition().toPoint()
                    self.dragged_shape = self.transform_box.shape
                    self.right_clicked_shape = self.transform_box.shape
                    self.active_editing_shape = self.transform_box.shape
                    self.right_hold_timer.start(350)
                    self.setCursor(self.transform_box.get_cursor_for_handle(h))
                    self.update()
                    return

            shape = self.find_shape_at(test_pos)
            if shape is not None:
                self.transform_box.set_shape(shape)
                self.is_transforming = True
                self.transform_box.start_drag(HandleType.INSIDE, pos)
                self.right_clicked_shape = shape
                self.dragged_shape = shape
                self.active_editing_shape = shape
                self.shape_drag_start = test_pos
                self.shape_drag_initial_pos = pos
                self.right_press_global_pos = event.globalPosition().toPoint()
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self.right_hold_timer.start(350)
                self.update()
                return

            if self.transform_box.is_active():
                self.transform_box.set_shape(None)
                self.update()
            if getattr(self, "is_protected_zone", False):
                event.accept()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            if getattr(self, "is_protected_zone", False):
                event.accept()
            return

        if self.text_editor.isVisible() and not self.text_editor.geometry().contains(pos.toPoint()):
            self._commit_text()

        # Проверяем клик ЛКМ по маркерам активной рамки трансформации
        if self.transform_box.is_active():
            h = self.transform_box.hit_test_handle(pos, offset=offset)
            if h != HandleType.NONE:
                self.is_transforming = True
                self.transform_box.start_drag(h, pos)
                self.shape_drag_initial_pos = pos
                self.setCursor(self.transform_box.get_cursor_for_handle(h))
                self.update()
                return

        # Если выбран инструмент "cursor" и кликнули по фигуре — выбираем ее
        if self.current_tool == "cursor":
            shape = self.find_shape_at(test_pos)
            if shape is not None:
                self.transform_box.set_shape(shape)
                self.is_transforming = True
                self.transform_box.start_drag(HandleType.INSIDE, pos)
                self.shape_drag_initial_pos = pos
                self.setCursor(Qt.CursorShape.SizeAllCursor)
                self.update()
                return
            if self.transform_box.is_active():
                self.transform_box.set_shape(None)
                self.update()
            if getattr(self, "is_protected_zone", False):
                event.accept()
            return

        if self.transform_box.is_active():
            self.transform_box.set_shape(None)
            self.update()

        stored_pos = test_pos
        self.drag_start = stored_pos

        if self.current_tool == "text":
            if self.text_editor.isVisible() and self.text_editor.text().strip():
                self._commit_text()
            self.text_pos = stored_pos
            self.text_editor.move(int(pos.x()), int(pos.y()))
            self.text_editor.setText("")
            self.text_editor.show()
            self.text_editor.setFocus()
            return

        with self.shape_lock:
            grain = getattr(self, "current_grain", 8)
            blur_r = getattr(self, "current_blur", 15)
            if self.current_tool == "pen":
                self.temp_shape = PenShape(color=self.current_color, stroke_width=self.stroke_width)
                self.temp_shape.pixel_size = grain
                self.temp_shape.blur_radius = blur_r
                self.temp_shape.add_point(stored_pos)
            elif self.current_tool == "highlighter":
                self.temp_shape = PenShape(color=self.current_color, stroke_width=18, alpha=90)
                self.temp_shape.name = tr("draw_highlighter")
                self.temp_shape.add_point(stored_pos)
            elif self.current_tool == "arrow":
                self.temp_shape = ArrowShape(
                    p1=stored_pos,
                    p2=stored_pos,
                    color=self.current_color,
                    stroke_width=self.stroke_width,
                    arrow_style="barbed"
                )
                self.temp_shape.pixel_size = grain
                self.temp_shape.blur_radius = blur_r
            elif self.current_tool == "line":
                self.temp_shape = LineShape(
                    p1=stored_pos,
                    p2=stored_pos,
                    color=self.current_color,
                    stroke_width=self.stroke_width
                )
            elif self.current_tool == "circle":
                self.temp_shape = CircleShape(
                    rect=QRectF(stored_pos, stored_pos),
                    color=self.current_color,
                    stroke_width=self.stroke_width,
                    filled=False
                )
            elif self.current_tool == "rect":
                self.temp_shape = RectangleShape(
                    rect=QRectF(stored_pos, stored_pos),
                    color=self.current_color,
                    stroke_width=self.stroke_width,
                    filled=False
                )
                self.temp_shape.pixel_size = grain
                self.temp_shape.blur_radius = blur_r
            elif self.current_tool == "mosaic":
                # Выбираем нужную фигуру в зависимости от текущего режима эффекта
                mode = getattr(self, "current_effect_mode", "mosaic")
                if mode == "blur":
                    self.temp_shape = BlurShape(
                        rect=QRectF(stored_pos, stored_pos),
                        blur_radius=blur_r
                    )
                elif mode in ("mosaic", "pixelate"):
                    self.temp_shape = MosaicShape(
                        rect=QRectF(stored_pos, stored_pos),
                        pixel_size=grain
                    )
                else:
                    # grayscale, invert, vibrant, sepia
                    self.temp_shape = RegionalEffectShape(
                        QRectF(stored_pos, stored_pos),
                        effect_type=mode,
                        intensity=grain
                    )
                # Для всех эффектов нужен живой фон — принудительно включаем таймер
                if self._live_effect_background is None:
                    self._refresh_live_effect_background()
                if self.temp_shape is not None and self._live_effect_background is not None:
                    try:
                        self.temp_shape.update_effect(self._live_effect_background)
                    except Exception:
                        pass
        self.update()

    def mouseMoveEvent(self, event):
        pos = event.position()
        offset = QPointF(float(self.rec_win.inner_x), float(self.rec_win.inner_y)) if self.is_pinned else QPointF(0.0, 0.0)
        test_pos = pos + offset

        # 0. Интерактивная трансформация фигуры (масштабирование, вращение, перемещение)
        if getattr(self, "is_transforming", False) and self.transform_box.is_active():
            shift_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.transform_box.drag_to(pos, shift_pressed=shift_pressed, offset=offset)
            total_dx = pos.x() - getattr(self, "shape_drag_initial_pos", pos).x()
            total_dy = pos.y() - getattr(self, "shape_drag_initial_pos", pos).y()
            if abs(total_dx) > 3 or abs(total_dy) > 3:
                self.right_hold_timer.stop()
            self.update()
            return

        # Курсор при наведении на маркеры
        if self.transform_box.is_active() and not self.is_transforming:
            h = self.transform_box.hit_test_handle(pos, offset=offset)
            if h != HandleType.NONE:
                self.setCursor(self.transform_box.get_cursor_for_handle(h))
                return
            elif self.current_tool == "cursor":
                self.setCursor(Qt.CursorShape.ArrowCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)

        # 1. Перемещение фигуры зажатием ПКМ (запасной режим)
        if self.dragged_shape is not None and not self.is_transforming:
            total_dx = test_pos.x() - self.shape_drag_initial_pos.x()
            total_dy = test_pos.y() - self.shape_drag_initial_pos.y()
            if abs(total_dx) > 3 or abs(total_dy) > 3:
                self.right_hold_timer.stop()
            dx = test_pos.x() - self.shape_drag_start.x()
            dy = test_pos.y() - self.shape_drag_start.y()
            with self.shape_lock:
                self.dragged_shape.translate(dx, dy)
            self.shape_drag_start = test_pos
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.update()
            return

        # 2. Рисование фигуры ЛКМ
        if self.temp_shape is None:
            if self.current_tool == "cursor" and getattr(self, "is_protected_zone", False):
                event.accept()
            return

        stored_pos = test_pos
        with self.shape_lock:
            if self.current_tool in ("pen", "highlighter"):
                self.temp_shape.add_point(stored_pos)
            elif self.current_tool in ("arrow", "line"):
                self.temp_shape.p2 = stored_pos
            elif self.current_tool in ("rect", "circle", "mosaic"):
                self.temp_shape.rect = QRectF(self.drag_start, stored_pos).normalized()

        self.update()

    def mouseReleaseEvent(self, event):
        pos = event.position()
        offset = QPointF(float(self.rec_win.inner_x), float(self.rec_win.inner_y)) if self.is_pinned else QPointF(0.0, 0.0)
        test_pos = pos + offset

        # 0. Завершение трансформации фигуры
        if getattr(self, "is_transforming", False):
            self.is_transforming = False
            self.right_hold_timer.stop()
            old_state, new_state = self.transform_box.finish_drag()
            target_shape = self.transform_box.shape
            self.dragged_shape = None
            self.right_clicked_shape = None

            total_dx = pos.x() - getattr(self, "shape_drag_initial_pos", pos).x()
            total_dy = pos.y() - getattr(self, "shape_drag_initial_pos", pos).y()
            is_click_only = (abs(total_dx) <= 3 and abs(total_dy) <= 3)

            if old_state and new_state and not is_click_only and target_shape:
                cmd = HistoryCommand(
                    tr("hist_cmd_transform", "Трансформация {name}", name=target_shape.name),
                    do_func=lambda s=target_shape, ns=new_state: (self._apply_shape_geometry(s, ns), self.update()),
                    undo_func=lambda s=target_shape, os=old_state: (self._apply_shape_geometry(s, os), self.update())
                )
                with self.shape_lock:
                    self.history_manager.push_already_done(cmd)
                self.update()
            elif is_click_only and event.button() == Qt.MouseButton.RightButton and target_shape:
                self._show_shape_context_menu(target_shape, event.globalPosition().toPoint())
            return

        # 1. Отпускание ПКМ: завершение перемещения или вызов контекстного меню свойств
        if event.button() == Qt.MouseButton.RightButton:
            self.right_hold_timer.stop()
            if self.dragged_shape is not None:
                total_dx = test_pos.x() - self.shape_drag_initial_pos.x()
                total_dy = test_pos.y() - self.shape_drag_initial_pos.y()
                target_shape = self.dragged_shape
                self.dragged_shape = None
                self.right_clicked_shape = None

                if abs(total_dx) > 3 or abs(total_dy) > 3:
                    cmd = HistoryCommand(
                        tr("hist_cmd_move", "Перемещение {name}", name=target_shape.name),
                        do_func=lambda s=target_shape, dx=total_dx, dy=total_dy: (s.translate(dx, dy), self.update()),
                        undo_func=lambda s=target_shape, dx=total_dx, dy=total_dy: (s.translate(-dx, -dy), self.update())
                    )
                    self.history_manager.push_already_done(cmd)
                else:
                    # Клик ПКМ без движения -> открываем меню свойств
                    self._show_shape_context_menu(target_shape, event.globalPosition().toPoint())

                self.setCursor(Qt.CursorShape.CrossCursor if self.current_tool != "cursor" else Qt.CursorShape.ArrowCursor)
                self.update()
                return

        # 2. Отпускание ЛКМ: сохранение нарисованной фигуры в историю
        if self.temp_shape is not None:
            with self.shape_lock:
                shape = self.temp_shape
                self.temp_shape = None
                self.layer_manager.add_shape(shape)
            cmd = HistoryCommand(
                shape.name,
                do_func=lambda s=shape: self.layer_manager.add_shape(s) if s not in self.layer_manager.shapes else None,
                undo_func=lambda s=shape: self.layer_manager.remove_shape(s.id)
            )
            self.history_manager.push_already_done(cmd)
            self.update()

        if self.current_tool == "cursor" and getattr(self, "is_protected_zone", False):
            event.accept()

    def wheelEvent(self, event):
        if getattr(self, "is_protected_zone", False):
            event.accept()
            return
        super().wheelEvent(event)

    def mouseDoubleClickEvent(self, event):
        if getattr(self, "is_protected_zone", False):
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _on_right_hold_timeout(self):
        """Вызывается по таймеру удержания ПКМ на фигуре (350 мс)."""
        if self.right_clicked_shape is not None:
            shape = self.right_clicked_shape
            pos = self.right_press_global_pos or QCursor.pos()
            self.dragged_shape = None
            self.right_clicked_shape = None
            self.setCursor(Qt.CursorShape.CrossCursor if self.current_tool != "cursor" else Qt.CursorShape.ArrowCursor)
            self._show_shape_context_menu(shape, pos)

    def _show_shape_context_menu(self, shape: BaseShape, global_pos: QPoint):
        if shape not in self.layer_manager.shapes:
            return

        self.active_editing_shape = shape
        self.update()

        menu = QMenu(self)
        menu.setStyleSheet(get_context_menu_style(True))

        act_edit = menu.addAction(create_themed_icon("edit", True, 14), tr("shape_menu_props", name=shape.name))
        menu.addSeparator()

        act_dup = menu.addAction(create_themed_icon("copy", True, 14), tr("shape_menu_dup"))

        menu_order = menu.addMenu(create_themed_icon("layers", True, 14), tr("shape_menu_order"))
        menu_order.setStyleSheet(get_context_menu_style(True))
        act_front = menu_order.addAction(create_themed_icon("bring_front", True, 14), tr("shape_menu_front"))
        act_up = menu_order.addAction(create_themed_icon("undo", True, 14), tr("shape_menu_up"))
        act_down = menu_order.addAction(create_themed_icon("redo", True, 14), tr("shape_menu_down"))
        act_back = menu_order.addAction(create_themed_icon("send_back", True, 14), tr("shape_menu_back"))

        menu.addSeparator()
        act_del = menu.addAction(create_themed_icon("trash", True, 14), tr("shape_menu_delete"))

        action = menu.exec(global_pos)
        if not action:
            self.active_editing_shape = None
            self.update()
            return

        if action == act_edit:
            self._open_shape_properties_editor(shape, global_pos)
        else:
            self.active_editing_shape = None
            self.update()
            if action == act_dup:
                self._duplicate_shape(shape)
            elif action == act_front:
                self.layer_manager.bring_to_front(shape)
            elif action == act_up:
                self.layer_manager.move_shape_up(shape)
            elif action == act_down:
                self.layer_manager.move_shape_down(shape)
            elif action == act_back:
                self.layer_manager.send_to_back(shape)
            elif action == act_del:
                self._delete_shape(shape)

    def _open_shape_properties_editor(self, shape: BaseShape, global_pos: QPoint):
        self.active_editing_shape = shape
        self.update()

        if self.shape_edit_popup is not None:
            try:
                self.shape_edit_popup.close()
            except Exception:
                pass
            self.shape_edit_popup = None

        self.shape_edit_popup = ShapeEditPopup(shape, self)
        self.shape_edit_popup.shape_modified.connect(self.update)
        def on_popup_closed():
            if self.active_editing_shape == shape:
                self.active_editing_shape = None
                self.update()
        self.shape_edit_popup.closed.connect(on_popup_closed)
        show_smart_popup(global_pos, self.shape_edit_popup)

    def _duplicate_shape(self, shape: BaseShape):
        if shape not in self.layer_manager.shapes:
            return
        clone = shape.clone()
        clone.translate(16, 16)
        clone.name = f"{shape.name} ({tr('shape_menu_dup').split()[0]})"
        self.layer_manager.add_shape(clone)
        self.update()

    def _delete_shape(self, shape: BaseShape):
        if shape in self.layer_manager.shapes:
            self.layer_manager.remove_shape(shape.id)
            self.update()

    def _commit_text(self):
        if not self.text_editor.isVisible():
            return
        txt = self.text_editor.text().strip()
        self.text_editor.clear()
        self.text_editor.hide()
        if txt:
            shape = TextShape(
                pos=self.text_pos,
                text=txt,
                color=self.current_color,
                font_size=16,
                is_bold=True,
                has_bg=True,
                bg_color="#18181b",
                bg_alpha=190
            )
            self.layer_manager.add_shape(shape)
            cmd = HistoryCommand(
                tr("hist_cmd_text", "Текст: '{text}'", text=txt[:10]),
                do_func=lambda s=shape: self.layer_manager.add_shape(s) if s not in self.layer_manager.shapes else None,
                undo_func=lambda s=shape: self.layer_manager.remove_shape(s.id)
            )
            self.history_manager.push_already_done(cmd)
        self.update()


class RecordingDrawingToolbar(QFrame):
    """
    Компактная панель инструментов живого аннотирования под шапкой записи.
    Включает выбор инструментов, цветовую палитру со слайдером толщины,
    окно слоёв (LayersDialog), отмену действий и переключатель Pin.
    """
    tool_selected = pyqtSignal(str)
    color_changed = pyqtSignal(str)
    stroke_changed = pyqtSignal(int)
    grain_changed = pyqtSignal(int)
    blur_changed = pyqtSignal(int)
    undo_requested = pyqtSignal()
    clear_requested = pyqtSignal()
    pin_toggled = pyqtSignal(bool)
    effect_mode_changed = pyqtSignal(str)  # mosaic, blur, grayscale, invert, vibrant, sepia

    # Иконки для каждого режима эффекта
    EFFECT_ICONS = {
        "mosaic": "mosaic",
        "blur": "blur",
        "grayscale": "grayscale",
        "invert": "invert",
        "vibrant": "vibrant",
        "sepia": "sepia",
    }
    # Подсказки
    EFFECT_TIPS = {
        "mosaic": "Мозаика (Пикселизация области)",
        "blur": "Размытие (Блюр области)",
        "grayscale": "Чёрно-белый (Grayscale области)",
        "invert": "Инверсия цветов (Область)",
        "vibrant": "Повышенная контрастность (Область)",
        "sepia": "Тёплая сепия (Область)",
    }

    PRESET_COLORS = ["#ef4444", "#f59e0b", "#22c55e", "#00C0FF", "#a855f7", "#ffffff"]

    def __init__(self, canvas=None, parent=None):
        if isinstance(canvas, QWidget) and not isinstance(canvas, RecordingDrawingCanvas) and parent is None:
            # Called as RecordingDrawingToolbar(parent_widget)
            parent = canvas
            canvas = getattr(parent, "canvas", None)
        super().__init__(parent)
        self.canvas = canvas
        self.setFixedHeight(30)
        self.setStyleSheet("""
            QFrame {
                background-color: #18181b;
                border-bottom: 1px solid #3f3f46;
            }
            QPushButton {
                background-color: #27272a;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 2px 4px;
            }
            QPushButton:hover {
                background-color: #3f3f46;
            }
            QPushButton:checked {
                background-color: #2563eb;
                border: 1px solid #3b82f6;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(4)

        # 1. Инструменты рисования (без мозаики — она добавляется отдельно с выпадающим меню)
        self.tool_buttons = {}
        self.current_effect_mode = "mosaic"  # текущий режим эффекта цензуры
        self._effect_flyout = None          # всплывающее меню эффектов
        tools = [
            ("cursor", "move", tr("draw_cursor")),
            ("pen", "pen", tr("draw_pen")),
            ("arrow", "arrow_barbed", tr("draw_arrow")),
            ("rect", "rect", tr("draw_rect")),
            ("highlighter", "highlighter", tr("draw_highlighter")),
            ("text", "text", tr("draw_text")),
        ]

        for t_name, ico_name, tip in tools:
            btn = QPushButton()
            btn.setCheckable(True)
            btn.setFixedSize(24, 24)
            btn.setIcon(create_themed_icon(ico_name, is_dark=True, size=14))
            btn.setIconSize(QSize(14, 14))
            btn.setToolTip(tip)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            if t_name == "cursor":
                btn.setChecked(True)
            btn.clicked.connect(lambda ch, t=t_name: self._on_tool_clicked(t))
            layout.addWidget(btn)
            self.tool_buttons[t_name] = btn

        # Специальная кнопка «Эффекты/Цензура» с выбором режима через ПКМ-меню
        self.btn_effect = QPushButton()
        self.btn_effect.setCheckable(True)
        self.btn_effect.setFixedSize(32, 24)   # немного шире для размещения стрелки
        self.btn_effect.setIconSize(QSize(14, 14))
        self.btn_effect.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_effect_btn()
        self.btn_effect.clicked.connect(self._on_effect_btn_clicked)
        # ПКМ → открыть меню выбора эффекта
        self.btn_effect.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.btn_effect.customContextMenuRequested.connect(self._open_effect_flyout)
        layout.addWidget(self.btn_effect)
        self.tool_buttons["mosaic"] = self.btn_effect

        # Разделитель
        layout.addWidget(self._make_sep())


        # 2. Выбор цвета (открывает полноценное окно палитры с толщиной и QColorDialog)
        self.current_color = "#ef4444"
        self.stroke_width = 4
        self.btn_color = QPushButton()
        self.btn_color.setFixedSize(22, 22)
        self.btn_color.setToolTip(tr("draw_color"))
        self.btn_color.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_color_btn()
        self.btn_color.clicked.connect(self._open_color_palette)
        layout.addWidget(self.btn_color)

        # 2.5 Кнопка «Слои фигур» (LayersDialog)
        self.btn_layers = QPushButton()
        self.btn_layers.setFixedSize(24, 24)
        self.btn_layers.setIcon(create_themed_icon("layers", is_dark=True, size=14))
        self.btn_layers.setIconSize(QSize(14, 14))
        self.btn_layers.setToolTip(tr("draw_layers"))
        self.btn_layers.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_layers.clicked.connect(self._open_layers_dialog)
        layout.addWidget(self.btn_layers)

        # Разделитель
        layout.addWidget(self._make_sep())

        # 3. Отмена и очистить
        btn_undo = QPushButton()
        btn_undo.setFixedSize(24, 24)
        btn_undo.setIcon(create_themed_icon("undo", is_dark=True, size=14))
        btn_undo.setIconSize(QSize(14, 14))
        btn_undo.setToolTip(tr("draw_undo"))
        btn_undo.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_undo.clicked.connect(self.undo_requested.emit)
        layout.addWidget(btn_undo)

        btn_clear = QPushButton()
        btn_clear.setFixedSize(24, 24)
        btn_clear.setIcon(create_themed_icon("trash", is_dark=True, size=13, custom_color="#fca5a5"))
        btn_clear.setIconSize(QSize(13, 13))
        btn_clear.setToolTip(tr("draw_clear"))
        btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_clear.clicked.connect(self.clear_requested.emit)
        layout.addWidget(btn_clear)

        # Разделитель
        layout.addWidget(self._make_sep())

        # 4. Закрепление на экране (Pin) — по умолчанию выключено (привязано к рамке)
        self.is_pinned = False
        self.btn_pin = QPushButton()
        self.btn_pin.setFixedSize(24, 24)
        self.btn_pin.setCheckable(True)
        self.btn_pin.setChecked(False)
        self.btn_pin.setCursor(Qt.CursorShape.PointingHandCursor)
        self._update_pin_btn()
        self.btn_pin.clicked.connect(self._toggle_pin)
        layout.addWidget(self.btn_pin)

        layout.addStretch()

        self.palette_popup = None
        self.layers_dialog = None

    def _make_sep(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("background-color: #3f3f46; max-width: 1px;")
        sep.setFixedHeight(18)
        return sep

    def select_tool(self, tool_name: str):
        self._on_tool_clicked(tool_name)

    def _on_tool_clicked(self, tool_name: str):
        for name, btn in self.tool_buttons.items():
            btn.setChecked(name == tool_name)
        self.tool_selected.emit(tool_name)

    def _update_color_btn(self):
        if self.current_color == "blur":
            self.btn_color.setIcon(create_themed_icon("blur", is_dark=True, size=14))
            self.btn_color.setStyleSheet("""
                QPushButton {
                    background-color: #27272a;
                    border: 2px solid #38bdf8;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    border-color: #60a5fa;
                }
            """)
        elif self.current_color == "mosaic":
            self.btn_color.setIcon(create_themed_icon("mosaic", is_dark=True, size=14))
            self.btn_color.setStyleSheet("""
                QPushButton {
                    background-color: #27272a;
                    border: 2px solid #38bdf8;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    border-color: #60a5fa;
                }
            """)
        else:
            self.btn_color.setIcon(QIcon())
            self.btn_color.setStyleSheet(f"""
                QPushButton {{
                    background-color: {self.current_color};
                    border: 2px solid #ffffff;
                    border-radius: 4px;
                }}
                QPushButton:hover {{
                    border-color: #38bdf8;
                }}
            """)

    def _open_color_palette(self):
        """Открывает всплывающее меню выбора цвета и толщины пера."""
        if self.palette_popup is not None:
            try:
                self.palette_popup.close()
            except Exception:
                pass
            self.palette_popup = None

        canvas = self.canvas or getattr(self.parent(), "canvas", None)
        grain = getattr(canvas, "current_grain", 8) if canvas else 8
        blur_r = getattr(canvas, "current_blur", 15) if canvas else 15

        self.palette_popup = ColorPalettePopup(
            current_color=self.current_color,
            current_width=self.stroke_width,
            parent=self,
            grain=grain,
            blur=blur_r
        )
        self.palette_popup.color_selected.connect(self._on_color_chosen)
        self.palette_popup.stroke_changed.connect(self._on_stroke_chosen)
        self.palette_popup.grain_changed.connect(self._on_grain_chosen)
        self.palette_popup.blur_changed.connect(self._on_blur_chosen)

        p = self.btn_color.mapToGlobal(QPoint(0, self.btn_color.height() + 4))
        self.palette_popup.move(p)
        self.palette_popup.show()

    def _on_color_chosen(self, col: str):
        self.current_color = col
        self._update_color_btn()
        self.color_changed.emit(col)

    def _on_grain_chosen(self, grain: int):
        self.grain_changed.emit(grain)

    def _on_blur_chosen(self, blur_radius: int):
        self.blur_changed.emit(blur_radius)

    def _on_stroke_chosen(self, width: int):
        self.stroke_width = width
        self.stroke_changed.emit(width)

    def _open_layers_dialog(self):
        """Открывает диалог управления слоями рисования."""
        if self.layers_dialog is not None:
            try:
                self.layers_dialog.close()
            except Exception:
                pass
        canvas = self.canvas or getattr(self.parent(), "canvas", None)
        if canvas and canvas.layer_manager:
            self.layers_dialog = LayersDialog(canvas.layer_manager, self)
            p = self.btn_layers.mapToGlobal(QPoint(-60, self.btn_layers.height() + 4))
            self.layers_dialog.move(p)
            self.layers_dialog.show()

    def _toggle_pin(self):
        self.is_pinned = self.btn_pin.isChecked()
        self._update_pin_btn()
        self.pin_toggled.emit(self.is_pinned)

    def _update_pin_btn(self):
        if self.is_pinned:
            self.btn_pin.setIcon(create_themed_icon("pin", is_dark=True, size=14, custom_color="#38bdf8"))
            self.btn_pin.setToolTip(tr("draw_pin_on"))
            self.btn_pin.setStyleSheet("background-color: #0c4a6e; border: 1px solid #0284c7;")
        else:
            self.btn_pin.setIcon(create_themed_icon("pin", is_dark=True, size=14, custom_color="#71717a"))
            self.btn_pin.setToolTip(tr("draw_pin_off"))
            self.btn_pin.setStyleSheet("")

    # ------------------------------------------------------------------ #
    #   Кнопка «Эффекты / Цензура» с выбором режима через всплывающий QMenu
    # ------------------------------------------------------------------ #

    def _update_effect_btn(self):
        """Обновляет иконку и подсказку кнопки эффекта по текущему режиму."""
        mode = getattr(self, "current_effect_mode", "mosaic")
        ico_name = self.EFFECT_ICONS.get(mode, "mosaic")
        tip = self.EFFECT_TIPS.get(mode, "Эффект (Цензура)")
        self.btn_effect.setIcon(create_themed_icon(ico_name, is_dark=True, size=14))
        self.btn_effect.setToolTip(f"{tip}\n(ПКМ — сменить режим эффекта)")

    def _on_effect_btn_clicked(self):
        """ЛКМ: активируем инструмент мозаики/эффекта."""
        self._on_tool_clicked("mosaic")

    def _open_effect_flyout(self, _pos=None):
        """ПКМ: показываем меню выбора режима эффекта."""
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #18181b;
                color: #f4f4f5;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                padding: 5px 18px 5px 10px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #2563eb;
            }
        """)
        for mode, ico_name in self.EFFECT_ICONS.items():
            tip = self.EFFECT_TIPS.get(mode, mode)
            action = QAction(create_themed_icon(ico_name, is_dark=True, size=14), tip, self)
            if mode == getattr(self, "current_effect_mode", "mosaic"):
                action.setCheckable(True)
                action.setChecked(True)
            action.triggered.connect(lambda checked, m=mode: self._on_effect_mode_selected(m))
            menu.addAction(action)
        menu.exec(self.btn_effect.mapToGlobal(QPoint(0, self.btn_effect.height() + 2)))

    def _on_effect_mode_selected(self, mode: str):
        """Применяет выбранный режим эффекта."""
        self.current_effect_mode = mode
        self._update_effect_btn()
        # Активируем инструмент эффекта
        self._on_tool_clicked("mosaic")
        # Уведомляем холст о смене режима
        self.effect_mode_changed.emit(mode)
