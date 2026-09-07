# -*- coding: utf-8 -*-
"""
Полноэкранный оверлей захвата экрана в стиле Lightshot.
Оптимизирован для гладкого рисования (60+ FPS), поддерживает живую запись экрана,
инструмент перемещения по умолчанию, мозаичную цензуру текста и память настроек для каждого инструмента.
"""

import sys
import os
import time
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import requests
import math
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QPoint, QRect, QBuffer, QIODevice, QTimer
from PyQt6.QtWidgets import (
    QWidget, QApplication, QLineEdit, QFileDialog, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPixmap, QImage, QCursor, QFont, QRegion, QFontMetrics
)

from config import ConfigManager
from models.shapes import (
    BaseShape, PenShape, LineShape, ArrowShape,
    RectangleShape, CircleShape, TextShape, MosaicShape, BlurShape, RegionalEffectShape
)
from models.layers import LayerManager
from models.history import HistoryManager, HistoryCommand
from recorder.capture_worker import CaptureWorker
from utils.image_filters import apply_filter, FilterType
from utils.sound import play_capture_sound
from utils.screen_lock import safe_grab_screen_pixmap, qimage_to_cv2_bgr, user32, enumerate_recordable_windows
from utils.window_icon import get_window_qicon
from utils.scrolling_capture import ScrollingCaptureEngine, ScrollingCaptureHUD
from utils.image_search import search_by_image
from utils.win32_helper import release_mouse_traps, force_foreground_window
from utils.i18n import tr
import ctypes
from ctypes import wintypes

from .toolbars import (
    RightDrawingToolbar, BottomActionToolbar, ToolType, get_theme_styles,
    show_smart_popup, get_context_menu_style
)
from .icons import create_themed_icon, create_tool_cursor
from .shape_editor import ShapeEditPopup
from .widgets import DimensionBadge
from .layers_dialog import LayersDialog
from .history_dialog import HistoryDialog
from .settings_dialog import SettingsDialog
from .recording_window import RecordingFrameWindow
from .transform_box import ShapeTransformBox, HandleType

HANDLE_NONE = 0
HANDLE_TL = 1
HANDLE_T = 2
HANDLE_TR = 3
HANDLE_R = 4
HANDLE_BR = 5
HANDLE_B = 6
HANDLE_BL = 7
HANDLE_L = 8
HANDLE_MOVE = 9
HANDLE_SIZE = 8

class OverlayWindow(QWidget):
    capture_closed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass

        # Таймер постоянного освобождения мыши от игровых ограничений (ClipCursor/ReleaseCapture)
        self._unclip_timer = QTimer(self)
        self._unclip_timer.timeout.connect(release_mouse_traps)

        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config

        # Состояния
        self.background_pixmap = None
        self.selection_rect = QRectF()
        self.is_selecting = False
        self.is_resizing = False
        self.active_handle = HANDLE_NONE
        self.drag_start_pos = QPointF()
        self.initial_selection = QRectF()
        self.is_locked = False
        self.dynamic_bg = False  # По умолчанию фон замирает (статический режим)
        self.is_passthrough = False  # Режим неосязаемой рамки (сквозные клики в фоновые окна)

        # Модели
        self.layer_manager = LayerManager(self)
        self.history_manager = HistoryManager(self)
        self.current_tool = ToolType.MOVE
        self.current_filter = FilterType.NONE
        self.is_highlighting_objects = False

        # Кэш отрисовки слоев для мгновенного отклика (60+ FPS)
        self.layers_cache_pixmap = None
        self.temp_shape = None

        # Интерактивное наведение и прилипание к окнам
        self.hovered_window_rect = QRectF()
        self.hovered_window_title = ""
        self.hovered_window_hwnd = 0
        self.target_hwnd = None

        # Длинный скриншот
        self.scrolling_engine = None
        self.scrolling_hud = None

        # Запись видео/GIF
        self.is_recording = False
        self.capture_worker = None

        # Виджеты
        self.badge = DimensionBadge(self)
        self.right_toolbar = RightDrawingToolbar(self)
        self.bottom_toolbar = BottomActionToolbar(self)
        self.recording_hud = None
        self.layers_dialog = None
        self.history_dialog = None

        # Редактор текста
        self.text_editor = QLineEdit(self)
        self.text_editor.hide()
        self.text_editor.returnPressed.connect(self._commit_text)

        # Контекстное меню и удержание правой кнопки мыши
        self.right_clicked_shape = None
        self.right_press_global_pos = None
        self.right_hold_timer = QTimer(self)
        self.right_hold_timer.setSingleShot(True)
        self.right_hold_timer.timeout.connect(self._on_right_hold_timeout)
        self.shape_edit_popup = None
        self.last_active_shape = None
        self.active_editing_shape = None

        # Интерактивная рамка трансформации фигур (Photoshop / Figma style)
        self.transform_box = ShapeTransformBox()
        self.is_transforming = False

        if sys.platform == "win32":
            try:
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass

        self._connect_signals()
        self._hide_toolbars()

    def _notify(self, title: str, message: str, icon=None, timeout: int = 4000, target_path: str = None):
        """Вызывает системное всплывающее уведомление Windows через системный трей приложения."""
        app_inst = getattr(QApplication.instance(), "app_instance", None)
        if app_inst and hasattr(app_inst, "show_notification"):
            app_inst.show_notification(title, message, icon, timeout, target_path=target_path)

    def _connect_signals(self):
        self.layer_manager.layers_changed.connect(self._on_layers_changed)
        self.history_manager.history_changed.connect(self._on_layers_changed)

        # Правая панель
        self.right_toolbar.tool_changed.connect(self._on_tool_changed)
        self.right_toolbar.tool_settings_updated.connect(self.update)
        self.right_toolbar.layers_clicked.connect(self._show_layers_dialog)
        self.right_toolbar.history_clicked.connect(self._show_history_dialog)
        self.right_toolbar.undo_clicked.connect(self.history_manager.undo)
        self.right_toolbar.redo_clicked.connect(self.history_manager.redo)
        self.right_toolbar.filter_selected.connect(self._on_filter_changed)

        # Нижняя панель
        self.bottom_toolbar.save_clicked.connect(self.save_screenshot)
        self.bottom_toolbar.copy_clicked.connect(self.copy_screenshot)
        self.bottom_toolbar.scrolling_screenshot_requested.connect(self.start_scrolling_screenshot)
        self.bottom_toolbar.search_image_requested.connect(self.search_image)
        self.bottom_toolbar.record_video_started.connect(lambda p: self.start_recording("video", p))
        self.bottom_toolbar.record_gif_started.connect(lambda p: self.start_recording("gif", p))
        self.bottom_toolbar.filter_selected.connect(self._on_filter_changed)
        self.bottom_toolbar.lock_toggled.connect(self._on_lock_toggled)
        self.bottom_toolbar.dynamic_bg_toggled.connect(self._on_dynamic_bg_toggled)
        self.bottom_toolbar.passthrough_toggled.connect(self._on_passthrough_toggled)
        self.bottom_toolbar.settings_clicked.connect(self._open_settings)
        self.bottom_toolbar.close_clicked.connect(self.close_overlay)

        # Бейдж размеров (кнопка разворота на весь экран)
        self.badge.fullscreen_clicked.connect(self.select_entire_screen)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass

    def _on_passthrough_toggled(self, is_passthrough: bool):
        self.is_passthrough = is_passthrough
        self._sync_passthrough_mask()
        self.update()

    def _sync_passthrough_mask(self):
        """
        Режим «Неосязаемая рамка»: клики мыши внутри рамки выделения
        на 100% проходят сквозь окно в активные программы, сайты и игры.
        При этом контурная рамка, маркеры изменения размера и панели инструментов
        остаются на экране и доступны для управления.
        """
        if getattr(self, "is_passthrough", False) and self.selection_rect.isValid() and not self.selection_rect.isEmpty():
            r = self.selection_rect.normalized().toRect()
            # Область для сквозных кликов (оставляем 4px границы для перемещения и масштабирования)
            inner_clickable = r.adjusted(4, 4, -4, -4)
            if inner_clickable.width() > 0 and inner_clickable.height() > 0:
                outer_region = QRegion(self.rect())
                inner_region = QRegion(inner_clickable)
                mask = outer_region.subtracted(inner_region)
                # Сохраняем панели инструментов и бейдж в маске окна, чтобы они оставались кликабельными
                if self.right_toolbar.isVisible():
                    mask = mask.united(QRegion(self.right_toolbar.geometry()))
                if self.bottom_toolbar.isVisible():
                    mask = mask.united(QRegion(self.bottom_toolbar.geometry()))
                if self.badge.isVisible():
                    mask = mask.united(QRegion(self.badge.geometry()))
                self.setMask(mask)
                return
        self.clearMask()

    def _set_background_pixmap(self, pixmap):
        self.background_pixmap = pixmap
        if pixmap is not None:
            dimmed = pixmap.copy()
            dim_painter = QPainter(dimmed)
            dim_painter.fillRect(dimmed.rect(), QColor(0, 0, 0, 115))
            dim_painter.end()
            self.dimmed_background_pixmap = dimmed
        else:
            self.dimmed_background_pixmap = None

    def _on_dynamic_bg_toggled(self, is_dynamic: bool):
        self.dynamic_bg = is_dynamic
        if not is_dynamic:
            # При отключении динамического фона замораживаем текущий кадр
            self.hide()
            QApplication.processEvents()
            geo = self.geometry()
            self._set_background_pixmap(safe_grab_screen_pixmap(geo.x(), geo.y(), geo.width(), geo.height()))
            self.show()
        self.update()

    def _on_layers_changed(self):
        if hasattr(self, "transform_box") and self.transform_box.shape:
            if self.transform_box.shape not in self.layer_manager.shapes:
                self.transform_box.set_shape(None)
        self._invalidate_layers_cache()
        self.update()

    def _apply_shape_geometry(self, target: BaseShape, source: BaseShape):
        """Копирует геометрию из сохраненного состояния в целевую фигуру для Undo/Redo."""
        if not target or not source:
            return
        import copy
        for attr in ("rect", "p1", "p2", "points", "path", "pos", "font_size", "rotation"):
            if hasattr(source, attr):
                val = getattr(source, attr)
                setattr(target, attr, copy.copy(val) if attr in ("points", "path", "rect") else val)
        if hasattr(target, "cached_mosaic"):
            target.cached_mosaic = None
        if hasattr(target, "cached_blur"):
            target.cached_blur = None
        if hasattr(target, "cached_pixmap"):
            target.cached_pixmap = None
        if hasattr(target, "_cached_rect"):
            target._cached_rect = None

    def _invalidate_layers_cache(self):
        self.layers_cache_pixmap = None

    def start_capture(self, preselected_recording_mode: str = None):
        """Захватывает экран и открывает оверлей без артефактов и задержек."""
        import time
        self.preselected_recording_mode = preselected_recording_mode
        self.clearMask()
        self.is_passthrough = False
        self.current_filter = FilterType.NONE
        self.is_highlighting_objects = False
        if hasattr(self, "bottom_toolbar") and hasattr(self.bottom_toolbar, "reset_filter"):
            self.bottom_toolbar.reset_filter()
        self.selection_rect = QRectF()
        self.initial_selection = QRectF()
        if hasattr(self, "transform_box"):
            self.transform_box.set_shape(None)
        self.is_transforming = False
        self._set_background_pixmap(None)
        self.layers_cache_pixmap = None
        self.temp_shape = None
        self.dragged_shape = None
        self.active_editing_shape = None
        self.layer_manager.clear()
        self.history_manager.clear()
        self.is_selecting = False
        self.is_resizing = False
        self.is_recording = False
        self.dynamic_bg = False
        self.bottom_toolbar.chk_dynamic_bg.setChecked(False)
        self.bottom_toolbar.chk_passthrough.setChecked(False)
        self.active_handle = HANDLE_NONE
        self._hide_toolbars()
        self.badge.hide()
        self.text_editor.hide()
        self._invalidate_layers_cache()

        # Сбрасываем ловушки мыши перед скрытием и захватом
        release_mouse_traps()

        # Если оверлей был открыт — гарантированно скрываем его и даем DWM завершить перерисовку рабочего стола
        if self.isVisible():
            self.hide()
            try:
                import ctypes
                ctypes.windll.dwmapi.DwmFlush()
            except Exception:
                pass
            for _ in range(3):
                QApplication.processEvents()

        # Освобождаем курсор мыши еще раз перед снимком экрана
        release_mouse_traps()

        # Полный захват экрана
        screen = QApplication.primaryScreen()
        geo = screen.virtualGeometry()
        self.setGeometry(geo)

        # Моментальный чистый снимок экрана для статического режима скриншотов
        fresh_pix = safe_grab_screen_pixmap(geo.x(), geo.y(), geo.width(), geo.height())
        self._set_background_pixmap(fresh_pix)

        # По умолчанию инструмент перемещения (как в Lightshot)
        self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)

        self.setWindowOpacity(0.0)
        self.show()
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.user32.SetWindowDisplayAffinity(int(self.winId()), 0x00000011)
            except Exception:
                pass
        self.raise_()
        force_foreground_window(int(self.winId()))
        self.activateWindow()
        self.setFocus()
        self.repaint()
        # Синхронно восстанавливаем 100% непрозрачность после того, как новый чистый кадр выведен в буфер
        self.setWindowOpacity(1.0)
        self.repaint()

        # Снимаем любые принудительные глобальные оверрайд-курсоры, чтобы виджет мог сам динамически менять курсор
        while QApplication.overrideCursor():
            QApplication.restoreOverrideCursor()
        self.setCursor(Qt.CursorShape.CrossCursor)

        # Запускаем таймер непрерывного снятия ограничений мыши
        self._unclip_timer.start(50)
        self.update()

    def _hide_toolbars(self):
        self.right_toolbar.hide()
        self.bottom_toolbar.hide()
        self.right_toolbar.properties_flyout.hide()

    def _show_toolbars(self):
        if self.selection_rect.isValid() and self.selection_rect.width() > 20 and self.selection_rect.height() > 20:
            self._update_toolbar_positions()
            self.right_toolbar.show()
            self.bottom_toolbar.show()
            self.badge.show()

    def _update_toolbar_positions(self):
        r = self.selection_rect.normalized()
        w_right = self.right_toolbar.sizeHint().width()
        h_right = self.right_toolbar.sizeHint().height()
        w_bot = self.bottom_toolbar.sizeHint().width()
        h_bot = self.bottom_toolbar.sizeHint().height()

        scr_w = self.width()
        scr_h = self.height()

        # Правая панель инструментов
        rx = r.right() + 6
        if rx + w_right > scr_w:
            rx = r.right() - w_right - 6
        ry = r.top()
        if ry + h_right > scr_h:
            ry = scr_h - h_right - 6
        self.right_toolbar.move(int(rx), int(ry))

        # Нижняя панель действий
        bx = r.right() - w_bot
        if bx + w_bot > scr_w:
            bx = scr_w - w_bot - 6
        if bx < 6:
            bx = 6
        by = r.bottom() + 8
        if by + h_bot > scr_h:
            by = r.bottom() - h_bot - 8
        self.bottom_toolbar.move(int(bx), int(by))

        # Бейдж размеров
        self.badge.update_dimension(r.width(), r.height())
        badge_x = r.left()
        badge_y = r.top() - self.badge.height() - 6
        if badge_y < 4:
            badge_y = r.top() + 6
        self.badge.move(int(badge_x), int(badge_y))

        # HUD записи
        if self.recording_hud:
            hud_x = r.left()
            hud_y = r.top() - self.recording_hud.height() - 8
            if hud_y < 4:
                hud_y = r.top() + 8
            self.recording_hud.move(int(hud_x), int(hud_y))

    def _get_tool_cursor(self) -> QCursor:
        """Возвращает специализированный курсор для текущего активного инструмента."""
        if self.current_tool == ToolType.TEXT:
            return QCursor(Qt.CursorShape.IBeamCursor)
        elif self.current_tool == ToolType.MOVE:
            return QCursor(Qt.CursorShape.OpenHandCursor)
        elif self.current_tool in (ToolType.PEN, ToolType.HIGHLIGHTER, ToolType.MOSAIC):
            return create_tool_cursor(self.current_tool)
        else:
            return QCursor(Qt.CursorShape.CrossCursor)

    def _set_cursor_if_needed(self, cursor):
        """Эффективно обновляет курсор мыши без спама событий Windows WM_SETCURSOR."""
        if isinstance(cursor, Qt.CursorShape):
            if self.cursor().shape() != cursor:
                self.setCursor(cursor)
        elif isinstance(cursor, QCursor):
            self.setCursor(cursor)

    # --- Обработка мыши ---
    def mousePressEvent(self, event):
        release_mouse_traps()
        pos = event.position()

        # Обработка правой кнопки мыши: трансформация фигур (масштабирование, вращение, перемещение) или контекстное меню
        if event.button() == Qt.MouseButton.RightButton:
            if self.selection_rect.isValid() and self.selection_rect.contains(pos):
                # 1. Если активна рамка трансформации фигуры, проверяем клик по ее маркерам
                if self.transform_box.is_active():
                    h = self.transform_box.hit_test_handle(pos)
                    if h != HandleType.NONE:
                        self.is_transforming = True
                        self.transform_box.start_drag(h, pos)
                        self.shape_drag_initial_pos = pos
                        self.right_press_global_pos = event.globalPosition().toPoint()
                        self.dragged_shape = self.transform_box.shape
                        self.right_clicked_shape = self.transform_box.shape
                        self.right_hold_timer.start(350)
                        self._set_cursor_if_needed(self.transform_box.get_cursor_for_handle(h))
                        self.update()
                        return

                # 2. Проверяем попадание в фигуру под курсором с учетом угла вращения
                for shape in reversed(self.layer_manager.shapes):
                    if shape.visible and shape.hit_test_rotated(pos):
                        self.transform_box.set_shape(shape)
                        self.is_transforming = True
                        self.transform_box.start_drag(HandleType.INSIDE, pos)
                        self.right_clicked_shape = shape
                        self.last_active_shape = shape
                        self.shape_drag_initial_pos = pos
                        self.right_press_global_pos = event.globalPosition().toPoint()
                        self.dragged_shape = shape
                        self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                        self.right_hold_timer.start(350)
                        self.update()
                        return

                # 3. Клик ПКМ на пустом месте внутри выделения — снимаем рамку трансформации
                if self.transform_box.is_active():
                    self.transform_box.set_shape(None)
                    self.update()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # Если рамка ещё не создана — начинаем выделение
        if not self.selection_rect.isValid() or self.selection_rect.isEmpty():
            self.is_selecting = True
            self.drag_start_pos = pos
            self.selection_rect = QRectF(pos, pos)
            self._hide_toolbars()
            self.update()
            return

        # Если активна рамка трансформации фигуры — проверяем ЛКМ по маркерам
        if self.transform_box.is_active():
            h = self.transform_box.hit_test_handle(pos)
            if h != HandleType.NONE:
                self.is_transforming = True
                self.transform_box.start_drag(h, pos)
                self.shape_drag_initial_pos = pos
                self._set_cursor_if_needed(self.transform_box.get_cursor_for_handle(h))
                self.update()
                return

        # Если рамка есть — проверяем манипуляторы изменения размера или перемещение выделения
        handle = self._hit_test_handles(pos)
        if not self.is_locked and handle != HANDLE_NONE:
            self.is_resizing = True
            self.active_handle = handle
            self.drag_start_pos = pos
            self.initial_selection = QRectF(self.selection_rect)
            self._set_handle_cursor(self.active_handle)
            return

        # Инструмент MOVE при клике внутри рамки
        if self.current_tool == ToolType.MOVE and self.selection_rect.contains(pos):
            # Проверяем клик по фигуре для выделения в рамку трансформации
            for shape in reversed(self.layer_manager.shapes):
                if shape.visible and shape.hit_test_rotated(pos):
                    self.transform_box.set_shape(shape)
                    self.is_transforming = True
                    self.transform_box.start_drag(HandleType.INSIDE, pos)
                    self.shape_drag_initial_pos = pos
                    self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                    self.update()
                    return
            # Если клик по фону рамки — перемещение всей рамки выделения
            if not self.is_locked:
                if self.transform_box.is_active():
                    self.transform_box.set_shape(None)
                    self.update()
                self.is_resizing = True
                self.active_handle = HANDLE_MOVE
                self.drag_start_pos = pos
                self.initial_selection = QRectF(self.selection_rect)
                self._set_cursor_if_needed(Qt.CursorShape.ClosedHandCursor)
                return

        # Рисование фигур внутри рамки
        if self.selection_rect.contains(pos) and self.current_tool != ToolType.MOVE:
            if self.transform_box.is_active():
                self.transform_box.set_shape(None)
                self.update()
            self._start_drawing_shape(pos)
            return

        # Если кликнули снаружи существующей рамки — защищаем от случайного сброса!
        # Сброс и новое выделение начинаются ТОЛЬКО если пользователь зажал и потянул (> 6 px).
        if not self.selection_rect.contains(pos):
            self.pending_outside_drag = True
            self.pending_outside_pos = pos
            return

    def mouseMoveEvent(self, event):
        pos = event.position()

        # 0. Проверка зажатия мыши снаружи рамки с порогом > 6 px
        if getattr(self, "pending_outside_drag", False):
            dx = pos.x() - self.pending_outside_pos.x()
            dy = pos.y() - self.pending_outside_pos.y()
            if (dx * dx + dy * dy) > 36:
                self.pending_outside_drag = False
                self.selection_rect = QRectF(self.pending_outside_pos, pos).normalized()
                self.is_selecting = True
                self.drag_start_pos = self.pending_outside_pos
                self.layer_manager.clear()
                self.history_manager.clear()
                self._invalidate_layers_cache()
                self.clearMask()
                self.is_passthrough = False
                self.bottom_toolbar.chk_passthrough.setChecked(False)
                self._hide_toolbars()
                self.badge.show()
                self.badge.update_dimension(self.selection_rect.width(), self.selection_rect.height())
                self.badge.move(int(self.selection_rect.left()), int(max(4, self.selection_rect.top() - 26)))
                self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)
                self.update()
            return

        # 1. Трансформация фигуры (масштабирование, вращение, перемещение)
        if getattr(self, "is_transforming", False) and self.transform_box.is_active():
            shift_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self.transform_box.drag_to(pos, shift_pressed=shift_pressed)
            total_dx = pos.x() - getattr(self, "shape_drag_initial_pos", pos).x()
            total_dy = pos.y() - getattr(self, "shape_drag_initial_pos", pos).y()
            if abs(total_dx) > 3 or abs(total_dy) > 3:
                self.right_hold_timer.stop()
            self._invalidate_layers_cache()
            self.update()
            return

        # 2. Первичное выделение (гладкое 60+ FPS без лишних вызовов)
        if self.is_selecting:
            self.selection_rect = QRectF(self.drag_start_pos, pos).normalized()
            self.badge.show()
            self.badge.update_dimension(self.selection_rect.width(), self.selection_rect.height())
            self.badge.move(int(self.selection_rect.left()), int(max(4, self.selection_rect.top() - 26)))
            self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)
            self.update()
            return

        # 3. Перемещение или изменение размера рамки
        if self.is_resizing and not self.is_locked:
            self._perform_resize(pos)
            self._update_toolbar_positions()
            if getattr(self, "is_passthrough", False):
                self._sync_passthrough_mask()
            if self.active_handle == HANDLE_MOVE:
                self._set_cursor_if_needed(Qt.CursorShape.ClosedHandCursor)
            else:
                self._set_handle_cursor(self.active_handle)
            self.update()
            return

        # 4. Рисование фигуры в процессе (высокопроизводительный режим)
        if self.temp_shape is not None:
            self._update_drawing_shape(pos)
            self._set_cursor_if_needed(self._get_tool_cursor())
            self.update()
            return

        # 5. Обновление курсора при наведении (с учетом выбранного инструмента)
        if self.selection_rect.isValid() and not self.selection_rect.isEmpty():
            if self.transform_box.is_active():
                h = self.transform_box.hit_test_handle(pos)
                if h != HandleType.NONE:
                    self._set_cursor_if_needed(self.transform_box.get_cursor_for_handle(h))
                    return
            handle = self._hit_test_handles(pos)
            if not self.is_locked and handle != HANDLE_NONE:
                self._set_handle_cursor(handle)
            elif self.selection_rect.contains(pos):
                self._set_cursor_if_needed(self._get_tool_cursor())
            else:
                self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)
        else:
            if not self.is_selecting:
                self._detect_hovered_window(event.globalPosition().toPoint())
            self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)

    def _detect_hovered_window(self, gpos: QPoint):
        try:
            raw_hwnd = user32.WindowFromPoint(gpos.x(), gpos.y())
            if raw_hwnd:
                root_hwnd = user32.GetAncestor(raw_hwnd, 2)  # GA_ROOT
                if not root_hwnd:
                    root_hwnd = raw_hwnd
                if root_hwnd != int(self.winId()) and user32.IsWindowVisible(root_hwnd):
                    r = wintypes.RECT()
                    if user32.GetWindowRect(root_hwnd, ctypes.byref(r)):
                        ww = r.right - r.left
                        wh = r.bottom - r.top
                        if ww > 60 and wh > 60:
                            length = user32.GetWindowTextLengthW(root_hwnd)
                            buf = ctypes.create_unicode_buffer(length + 1)
                            user32.GetWindowTextW(root_hwnd, buf, length + 1)
                            title = buf.value.strip()
                            if title and title != "Program Manager":
                                if self.hovered_window_hwnd != root_hwnd:
                                    self.hovered_window_rect = QRectF(r.left, r.top, ww, wh)
                                    self.hovered_window_title = title
                                    self.hovered_window_hwnd = root_hwnd
                                    self.update()
                                return
        except Exception:
            pass
        if self.hovered_window_hwnd != 0:
            self.hovered_window_rect = QRectF()
            self.hovered_window_title = ""
            self.hovered_window_hwnd = 0
            self.update()

    def mouseReleaseEvent(self, event):
        pos = event.position()

        # 1. Завершение интерактивной трансформации фигуры
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
                    do_func=lambda s=target_shape, ns=new_state: (self._apply_shape_geometry(s, ns), self._on_layers_changed()),
                    undo_func=lambda s=target_shape, os=old_state: (self._apply_shape_geometry(s, os), self._on_layers_changed())
                )
                self.history_manager.push_already_done(cmd)
                self._invalidate_layers_cache()
                self.update()
            elif is_click_only and event.button() == Qt.MouseButton.RightButton and target_shape:
                if self.selection_rect.contains(pos):
                    self.setCursor(Qt.CursorShape.OpenHandCursor if self.current_tool == ToolType.MOVE else Qt.CursorShape.CrossCursor)
                self._show_shape_context_menu(target_shape, event.globalPosition().toPoint())
            return

        # Отпускание правой кнопки мыши (запасной выход)
        if event.button() == Qt.MouseButton.RightButton:
            self.right_hold_timer.stop()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if getattr(self, "pending_outside_drag", False):
            self.pending_outside_drag = False
            return

        if self.is_selecting:
            self.is_selecting = False
            self.selection_rect = self.selection_rect.normalized()

            # Если был одиночный клик без протяжки по подсвеченному окну — прилипаем (snap) к этому окну!
            if (pos - self.drag_start_pos).manhattanLength() < 8 and getattr(self, "hovered_window_rect", None) and self.hovered_window_rect.isValid() and not self.hovered_window_rect.isEmpty():
                self.selection_rect = QRectF(self.hovered_window_rect)
                self.target_hwnd = self.hovered_window_hwnd
                self.hovered_window_rect = QRectF()
                self.hovered_window_hwnd = 0

            if self.selection_rect.width() > 20 and self.selection_rect.height() > 20:
                # В стиле Lightshot: сразу после выделения выбирается инструмент перемещения!
                self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)
                self._show_toolbars()
                self.setCursor(Qt.CursorShape.OpenHandCursor)

                # Если запуск был инициирован для конкретного режима записи (видео или GIF), сразу открываем настройки!
                if getattr(self, "preselected_recording_mode", None) == "video":
                    self.bottom_toolbar._show_video_popup()
                    self.preselected_recording_mode = None
                elif getattr(self, "preselected_recording_mode", None) == "gif":
                    self.bottom_toolbar._show_gif_popup()
                    self.preselected_recording_mode = None
            else:
                self.selection_rect = QRectF()
                self.clearMask()
                self.is_passthrough = False
                if hasattr(self, "bottom_toolbar"):
                    self.bottom_toolbar.chk_passthrough.setChecked(False)
                self._hide_toolbars()
                self.badge.hide()
            self.update()
            return

        if self.is_resizing:
            self.is_resizing = False
            self.active_handle = HANDLE_NONE
            self.selection_rect = self.selection_rect.normalized()
            self._update_toolbar_positions()
            if getattr(self, "is_passthrough", False):
                self._sync_passthrough_mask()
            if self.selection_rect.contains(pos):
                self.setCursor(Qt.CursorShape.OpenHandCursor if self.current_tool == ToolType.MOVE else Qt.CursorShape.CrossCursor)
            self.update()
            return

        if self.temp_shape is not None:
            self._finish_drawing_shape()
            self.update()

    def _on_right_hold_timeout(self):
        if self.right_clicked_shape is not None:
            shape = self.right_clicked_shape
            pos = self.right_press_global_pos or QCursor.pos()
            self.dragged_shape = None
            self.right_clicked_shape = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.current_tool == ToolType.MOVE else Qt.CursorShape.CrossCursor)
            self._show_shape_context_menu(shape, pos)

    def _show_shape_context_menu(self, shape: BaseShape, global_pos: QPoint):
        if shape not in self.layer_manager.shapes:
            return

        self.last_active_shape = shape
        self.active_editing_shape = shape
        self.update()
        theme = get_theme_styles()
        is_dark = theme["is_dark"]

        menu = QMenu(self)
        menu.setStyleSheet(get_context_menu_style(is_dark))

        # 1. Изменить свойства
        act_edit = menu.addAction(create_themed_icon("edit", is_dark, 14), tr("shape_menu_props", name=shape.name))
        menu.addSeparator()

        # 2. Дублировать
        act_dup = menu.addAction(create_themed_icon("copy", is_dark, 14), tr("shape_menu_dup"))

        # 3. Порядок слоёв
        menu_order = menu.addMenu(create_themed_icon("layers", is_dark, 14), tr("shape_menu_order"))
        menu_order.setStyleSheet(get_context_menu_style(is_dark))
        act_front = menu_order.addAction(create_themed_icon("bring_front", is_dark, 14), tr("shape_menu_front"))
        act_up = menu_order.addAction(create_themed_icon("undo", is_dark, 14), tr("shape_menu_up"))
        act_down = menu_order.addAction(create_themed_icon("redo", is_dark, 14), tr("shape_menu_down"))
        act_back = menu_order.addAction(create_themed_icon("send_back", is_dark, 14), tr("shape_menu_back"))

        menu.addSeparator()
        # 4. Удалить
        act_del = menu.addAction(create_themed_icon("trash", is_dark, 14), tr("shape_menu_delete"))

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
                self._change_shape_order(shape, "front")
            elif action == act_up:
                self._change_shape_order(shape, "up")
            elif action == act_down:
                self._change_shape_order(shape, "down")
            elif action == act_back:
                self._change_shape_order(shape, "back")
            elif action == act_del:
                self._delete_shape(shape)

    def _open_shape_properties_editor(self, shape: BaseShape, global_pos: QPoint):
        self.last_active_shape = shape
        self.active_editing_shape = shape
        self.update()
        if self.shape_edit_popup is not None:
            try:
                self.shape_edit_popup.close()
            except Exception:
                pass
            self.shape_edit_popup = None

        self.shape_edit_popup = ShapeEditPopup(shape, self)
        self.shape_edit_popup.shape_modified.connect(self._on_layers_changed)
        self.shape_edit_popup.editing_finished.connect(
            lambda old_p, new_p, s=shape: self._on_shape_props_finished(s, old_p, new_p)
        )
        def on_popup_closed():
            if getattr(self, "active_editing_shape", None) == shape:
                self.active_editing_shape = None
                self.update()
        self.shape_edit_popup.closed.connect(on_popup_closed)
        show_smart_popup(global_pos, self.shape_edit_popup)

    def _on_shape_props_finished(self, shape: BaseShape, old_props: dict, new_props: dict):
        def apply_props(s, props):
            for k, v in props.items():
                setattr(s, k, v)
            if isinstance(s, TextShape):
                txt = getattr(s, "text", "")
                s.name = f"Текст: '{txt[:10]}'" if txt else "Текст"
            if self.background_pixmap is not None:
                if hasattr(s, "update_effect"):
                    s.update_effect(self.background_pixmap)
                elif hasattr(s, "update_mosaic"):
                    s.update_mosaic(self.background_pixmap)
                elif hasattr(s, "update_blur"):
                    s.update_blur(self.background_pixmap)
            self._on_layers_changed()

        cmd = HistoryCommand(
            tr("hist_cmd_props", "Свойства: {name}", name=shape.name),
            do_func=lambda s=shape, p=new_props: apply_props(s, p),
            undo_func=lambda s=shape, p=old_props: apply_props(s, p)
        )
        self.history_manager.push_already_done(cmd)

    def _duplicate_shape(self, shape: BaseShape):
        if shape not in self.layer_manager.shapes:
            return
        clone = shape.clone()
        clone.translate(16, 16)
        clone.name = tr("obj_clone_name", "{name} (копия)", name=shape.name)
        self.layer_manager.add_shape(clone)
        self.last_active_shape = clone

        cmd = HistoryCommand(
            tr("hist_cmd_dup", "Дублирование {name}", name=shape.name),
            do_func=lambda s=clone: (self.layer_manager.add_shape(s) if s not in self.layer_manager.shapes else None, self._on_layers_changed()),
            undo_func=lambda s=clone: (self.layer_manager.remove_shape(s.id), self._on_layers_changed())
        )
        self.history_manager.push_already_done(cmd)
        self._on_layers_changed()

    def _delete_shape(self, shape: BaseShape):
        if shape not in self.layer_manager.shapes:
            return
        idx = self.layer_manager.shapes.index(shape)
        self.layer_manager.remove_shape(shape.id)
        if self.last_active_shape == shape:
            self.last_active_shape = None

        def restore_shape(s, i):
            if s not in self.layer_manager.shapes:
                insert_pos = min(max(0, i), len(self.layer_manager.shapes))
                self.layer_manager.shapes.insert(insert_pos, s)
                self.layer_manager.layers_changed.emit()

        cmd = HistoryCommand(
            tr("hist_cmd_delete", "Удаление {name}", name=shape.name),
            do_func=lambda s=shape: (self.layer_manager.remove_shape(s.id), self._on_layers_changed()),
            undo_func=lambda s=shape, i=idx: (restore_shape(s, i), self._on_layers_changed())
        )
        self.history_manager.push_already_done(cmd)
        self._on_layers_changed()

    def _change_shape_order(self, shape: BaseShape, action: str):
        if shape not in self.layer_manager.shapes:
            return
        old_idx = self.layer_manager.shapes.index(shape)
        if action == "front":
            self.layer_manager.bring_to_front(shape)
            desc = tr("hist_cmd_front", "{name} на передний план", name=shape.name)
        elif action == "back":
            self.layer_manager.send_to_back(shape)
            desc = tr("hist_cmd_back", "{name} на задний план", name=shape.name)
        elif action == "up":
            self.layer_manager.move_shape_up(shape)
            desc = tr("hist_cmd_up", "{name} выше", name=shape.name)
        elif action == "down":
            self.layer_manager.move_shape_down(shape)
            desc = tr("hist_cmd_down", "{name} ниже", name=shape.name)
        else:
            return

        new_idx = self.layer_manager.shapes.index(shape)
        if old_idx != new_idx:
            def restore_idx(s, target_idx):
                if s in self.layer_manager.shapes:
                    self.layer_manager.shapes.remove(s)
                    self.layer_manager.shapes.insert(min(max(0, target_idx), len(self.layer_manager.shapes)), s)
                    self.layer_manager.layers_changed.emit()

            cmd = HistoryCommand(
                desc,
                do_func=lambda s=shape, i=new_idx: (restore_idx(s, i), self._on_layers_changed()),
                undo_func=lambda s=shape, i=old_idx: (restore_idx(s, i), self._on_layers_changed())
            )
            self.history_manager.push_already_done(cmd)
        self._on_layers_changed()

    def _hit_test_handles(self, pt: QPointF):
        r = self.selection_rect.normalized()
        hs = HANDLE_SIZE

        # Проверка угловых манипуляторов
        if QRectF(r.left() - hs, r.top() - hs, hs*2, hs*2).contains(pt): return HANDLE_TL
        if QRectF(r.right() - hs, r.top() - hs, hs*2, hs*2).contains(pt): return HANDLE_TR
        if QRectF(r.right() - hs, r.bottom() - hs, hs*2, hs*2).contains(pt): return HANDLE_BR
        if QRectF(r.left() - hs, r.bottom() - hs, hs*2, hs*2).contains(pt): return HANDLE_BL

        # Проверка боковых граней
        if QRectF(r.left() + hs, r.top() - hs, r.width() - hs*2, hs*2).contains(pt): return HANDLE_T
        if QRectF(r.right() - hs, r.top() + hs, hs*2, r.height() - hs*2).contains(pt): return HANDLE_R
        if QRectF(r.left() + hs, r.bottom() - hs, r.width() - hs*2, hs*2).contains(pt): return HANDLE_B
        if QRectF(r.left() - hs, r.top() + hs, hs*2, r.height() - hs*2).contains(pt): return HANDLE_L

        return HANDLE_NONE

    def _set_handle_cursor(self, handle):
        if handle in (HANDLE_TL, HANDLE_BR):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif handle in (HANDLE_TR, HANDLE_BL):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif handle in (HANDLE_T, HANDLE_B):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif handle in (HANDLE_L, HANDLE_R):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif handle == HANDLE_MOVE:
            self.setCursor(Qt.CursorShape.ClosedHandCursor if self.is_resizing else Qt.CursorShape.OpenHandCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def _perform_resize(self, pos: QPointF):
        dx = pos.x() - self.drag_start_pos.x()
        dy = pos.y() - self.drag_start_pos.y()
        r = QRectF(self.initial_selection)

        if self.active_handle == HANDLE_MOVE:
            r.translate(dx, dy)
        elif self.active_handle == HANDLE_TL:
            r.setTopLeft(r.topLeft() + QPointF(dx, dy))
        elif self.active_handle == HANDLE_TR:
            r.setTopRight(r.topRight() + QPointF(dx, dy))
        elif self.active_handle == HANDLE_BR:
            r.setBottomRight(r.bottomRight() + QPointF(dx, dy))
        elif self.active_handle == HANDLE_BL:
            r.setBottomLeft(r.bottomLeft() + QPointF(dx, dy))
        elif self.active_handle == HANDLE_T:
            r.setTop(r.top() + dy)
        elif self.active_handle == HANDLE_B:
            r.setBottom(r.bottom() + dy)
        elif self.active_handle == HANDLE_L:
            r.setLeft(r.left() + dx)
        elif self.active_handle == HANDLE_R:
            r.setRight(r.right() + dx)

        self.selection_rect = r.normalized()

    # --- Рисование фигур с учетом индивидуальных настроек инструмента ---
    def _start_drawing_shape(self, pos: QPointF):
        self.shape_origin_pos = QPointF(pos)
        cfg = self.right_toolbar.get_current_settings()
        col = cfg.get("color", "#FF2E2E")
        size = cfg.get("size", 4)
        censor_mode = cfg.get("censor_mode", "mosaic")
        grain = cfg.get("pixel_size", 8)
        blur_r = cfg.get("blur_radius", 15)

        if self.current_tool == ToolType.PEN:
            self.temp_shape = PenShape(color=col, stroke_width=size, is_highlighter=False)
            self.temp_shape.pixel_size = grain
            self.temp_shape.blur_radius = blur_r
            self.temp_shape.add_point(pos)
        elif self.current_tool == ToolType.HIGHLIGHTER:
            alpha = cfg.get("alpha", 90)
            self.temp_shape = PenShape(color=col, stroke_width=size, is_highlighter=True, alpha=alpha)
            self.temp_shape.add_point(pos)
        elif self.current_tool == ToolType.SHAPES:
            sub = cfg.get("subshape", "arrow")
            filled = cfg.get("filled", True)
            fill_alpha = cfg.get("fill_alpha", 255)
            is_grad = cfg.get("is_gradient", False)
            col1 = cfg.get("gradient_color1", col)
            col2 = cfg.get("gradient_color2", "#00C0FF")

            if sub == "line":
                line_style = cfg.get("line_style", "solid")
                self.temp_shape = LineShape(pos, pos, color=col, stroke_width=size, line_style=line_style)
            elif sub == "arrow":
                style = cfg.get("arrow_style", "barbed")
                filled_arrow = cfg.get("filled", True)
                self.temp_shape = ArrowShape(pos, pos, color=col, stroke_width=size, arrow_style=style, filled=filled_arrow)
            elif sub == "rect":
                is_rounded = cfg.get("is_rounded", False)
                self.temp_shape = RectangleShape(
                    QRectF(pos, pos), color=col, stroke_width=size,
                    filled=filled, fill_alpha=fill_alpha,
                    is_gradient=is_grad, gradient_color1=col1, gradient_color2=col2,
                    is_rounded=is_rounded
                )
            elif sub == "circle":
                self.temp_shape = CircleShape(
                    QRectF(pos, pos), color=col, stroke_width=size,
                    filled=filled, fill_alpha=fill_alpha,
                    is_gradient=is_grad, gradient_color1=col1, gradient_color2=col2
                )
            if self.temp_shape is not None:
                self.temp_shape.pixel_size = grain
                self.temp_shape.blur_radius = blur_r
        elif self.current_tool == ToolType.LINE:
            self.temp_shape = LineShape(pos, pos, color=col, stroke_width=size)
        elif self.current_tool == ToolType.ARROW:
            self.temp_shape = ArrowShape(pos, pos, color=col, stroke_width=size, arrow_style=cfg.get("arrow_style", "classic"))
        elif self.current_tool == ToolType.RECT:
            self.temp_shape = RectangleShape(QRectF(pos, pos), color=col, stroke_width=size, filled=False)
        elif self.current_tool == ToolType.FILLED_RECT:
            self.temp_shape = RectangleShape(QRectF(pos, pos), color=col, stroke_width=size, filled=True, fill_alpha=cfg.get("alpha", 80))
        elif self.current_tool == ToolType.CIRCLE:
            self.temp_shape = CircleShape(QRectF(pos, pos), color=col, stroke_width=size, filled=False)
        elif self.current_tool == ToolType.MOSAIC:
            # Инструмент региональных эффектов и цензуры: мозаика, блюр, ч/б, инверсия, насыщенность, сепия
            if censor_mode == "blur":
                self.temp_shape = BlurShape(QRectF(pos, pos), blur_radius=blur_r)
            elif censor_mode in ("mosaic", "pixelate"):
                self.temp_shape = MosaicShape(QRectF(pos, pos), pixel_size=grain if grain else size)
            else:
                self.temp_shape = RegionalEffectShape(QRectF(pos, pos), effect_type=censor_mode, intensity=size)
            if self.background_pixmap is not None:
                self.temp_shape.update_effect(self.background_pixmap)
        elif self.current_tool == ToolType.TEXT:
            self._open_text_editor(pos, col, size)

    def _update_drawing_shape(self, pos: QPointF):
        origin = getattr(self, "shape_origin_pos", None)
        if isinstance(self.temp_shape, PenShape):
            self.temp_shape.add_point(pos)
        elif isinstance(self.temp_shape, (LineShape, ArrowShape)):
            self.temp_shape.p2 = pos
        elif isinstance(self.temp_shape, (RectangleShape, CircleShape)):
            if origin is None:
                origin = self.temp_shape.rect.topLeft()
            self.temp_shape.rect = QRectF(origin, pos).normalized()
        elif isinstance(self.temp_shape, (RegionalEffectShape, MosaicShape, BlurShape)):
            if origin is None:
                origin = self.temp_shape.rect.topLeft()
            self.temp_shape.rect = QRectF(origin, pos).normalized()
            if self.background_pixmap is not None:
                self.temp_shape.update_effect(self.background_pixmap)

    def _finish_drawing_shape(self):
        self.shape_origin_pos = None
        shape = self.temp_shape
        self.temp_shape = None

        if shape is not None:
            if isinstance(shape, (RegionalEffectShape, MosaicShape, BlurShape)) and self.background_pixmap is not None:
                shape.update_effect(self.background_pixmap)

            self.layer_manager.add_shape(shape)
            cmd = HistoryCommand(
                tr("hist_cmd_add", "Добавлен {name}", name=shape.name),
                do_func=lambda s=shape: self.layer_manager.add_shape(s),
                undo_func=lambda s=shape: self.layer_manager.remove_shape(s.id)
            )
            self.history_manager.push_already_done(cmd)

    def _open_text_editor(self, pos: QPointF, color: str, font_size: int):
        cfg = self.right_toolbar.get_current_settings()
        self.text_editor_pos = pos
        self.text_editor_color = cfg.get("color", color)
        self.text_editor_font_size = cfg.get("size", font_size)
        self.text_editor_font_family = cfg.get("font_family", "Segoe UI")
        self.text_editor_is_bold = cfg.get("is_bold", True)
        self.text_editor_is_underline = cfg.get("is_underline", False)
        self.text_editor_has_bg = cfg.get("has_bg", False)
        self.text_editor_bg_color = cfg.get("bg_color", "#000000")
        self.text_editor_bg_alpha = cfg.get("bg_alpha", 180)

        weight = "bold" if self.text_editor_is_bold else "normal"
        decor = "underline" if self.text_editor_is_underline else "none"
        bg_css = "rgba(0, 0, 0, 200)" if self.text_editor_has_bg else "rgba(20, 22, 28, 160)"

        self.text_editor.setStyleSheet(f"""
            QLineEdit {{
                background-color: {bg_css};
                color: {self.text_editor_color};
                font-family: '{self.text_editor_font_family}', sans-serif;
                font-size: {self.text_editor_font_size}pt;
                font-weight: {weight};
                text-decoration: {decor};
                border: 1px dashed {self.text_editor_color};
                padding: 2px 4px;
            }}
        """)
        self.text_editor.setFixedWidth(280)
        self.text_editor.move(int(pos.x()), int(pos.y()))
        self.text_editor.clear()
        self.text_editor.show()
        self.text_editor.setFocus()

    def _commit_text(self):
        text = self.text_editor.text().strip()
        self.text_editor.hide()
        if text:
            from PyQt6.QtGui import QFontMetrics
            font = QFont(self.text_editor_font_family, int(self.text_editor_font_size))
            font.setBold(self.text_editor_is_bold)
            font.setUnderline(self.text_editor_is_underline)
            fm = QFontMetrics(font)
            # Точный baseline с учетом ascent шрифта
            baseline_pos = self.text_editor_pos + QPointF(4, fm.ascent() + 2)

            shape = TextShape(
                baseline_pos,
                text,
                color=self.text_editor_color,
                font_size=self.text_editor_font_size,
                font_family=self.text_editor_font_family,
                is_bold=self.text_editor_is_bold,
                is_underline=self.text_editor_is_underline,
                has_bg=getattr(self, "text_editor_has_bg", False),
                bg_color=getattr(self, "text_editor_bg_color", "#000000"),
                bg_alpha=getattr(self, "text_editor_bg_alpha", 180)
            )
            self.layer_manager.add_shape(shape)
            cmd = HistoryCommand(
                tr("hist_cmd_text", "Текст: '{text}'", text=text[:12]),
                do_func=lambda s=shape: self.layer_manager.add_shape(s),
                undo_func=lambda s=shape: self.layer_manager.remove_shape(s.id)
            )
            self.history_manager.push_already_done(cmd)
            self.update()

    # --- Отрисовка с аппаратной оптимизацией ---
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Режим 1: Живая запись видео или GIF
        if self.is_recording:
            r = self.selection_rect.normalized()
            w, h = self.width(), self.height()

            # Затемнение ВСЕГО экрана ВОКРУГ области записи
            dim = QBrush(QColor(0, 0, 0, 110))
            painter.fillRect(QRectF(0, 0, w, r.top()), dim)
            painter.fillRect(QRectF(0, r.bottom(), w, h - r.bottom()), dim)
            painter.fillRect(QRectF(0, r.top(), r.left(), r.height()), dim)
            painter.fillRect(QRectF(r.right(), r.top(), w - r.right(), r.height()), dim)

            # Область записи ВНУТРИ остается прозрачной — виден живой рабочий стол!
            # Рамка записи рисуется СНАРУЖИ, чтобы не попадать в кадр!
            outer_border = QRectF(r.left() - 2, r.top() - 2, r.width() + 4, r.height() + 4)
            pen_rec = QPen(QColor(235, 59, 90), 2, Qt.PenStyle.SolidLine)
            painter.setPen(pen_rec)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(outer_border)

            # Отрисовываем векторные слои аннотаций прямо поверх живого видео
            self.layer_manager.draw_all(painter)
            if self.temp_shape is not None:
                self.temp_shape.draw(painter)
            self._draw_dragged_shape_indicator(painter)
            return

        # Режим 2: Режим скриншота и аннотаций
        r = self.selection_rect.normalized() if (self.selection_rect.isValid() and not self.selection_rect.isEmpty()) else None
        w, h = self.width(), self.height()

        # Если фоновые буферы сброшены и окно скрывается — очищаем видеопамять в прозрачный цвет, исключая артефакты в DWM
        if self.dimmed_background_pixmap is None and self.background_pixmap is None and not self.dynamic_bg and not getattr(self, "is_passthrough", False):
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
            return

        if getattr(self, "is_passthrough", False):
            # --- РЕЖИМ НЕОСЯЗАЕМОЙ РАМКИ (СКВОЗНЫЕ КЛИКИ) ---
            # Внутренняя область вырезана маской setMask, рабочий стол виден и активен для мыши.
            # Снаружи области выделения затемняем экран.
            if r is not None:
                dim_brush = QBrush(QColor(0, 0, 0, 120))
                painter.fillRect(QRectF(0, 0, w, r.top()), dim_brush)
                painter.fillRect(QRectF(0, r.bottom(), w, h - r.bottom()), dim_brush)
                painter.fillRect(QRectF(0, r.top(), r.left(), r.height()), dim_brush)
                painter.fillRect(QRectF(r.right(), r.top(), w - r.right(), r.height()), dim_brush)
            else:
                painter.fillRect(self.rect(), QColor(0, 0, 0, 70))
        elif not self.dynamic_bg:
            # --- СТАТИЧЕСКИЙ РЕЖИМ (ПО УМОЛЧАНИЮ) ---
            # Весь экран замер / заморожен как в классическом скриншотере
            if self.dimmed_background_pixmap is not None:
                painter.drawPixmap(0, 0, self.dimmed_background_pixmap)
                if r is not None:
                    rx, ry, rw, rh = int(r.x()), int(r.y()), int(r.width()), int(r.height())
                    if rw > 0 and rh > 0 and self.background_pixmap is not None:
                        painter.drawPixmap(rx, ry, rw, rh, self.background_pixmap, rx, ry, rw, rh)
            else:
                if self.background_pixmap is not None:
                    painter.drawPixmap(0, 0, self.background_pixmap)

                if r is not None:
                    dim_brush = QBrush(QColor(0, 0, 0, 120))
                    painter.fillRect(QRectF(0, 0, w, r.top()), dim_brush)
                    painter.fillRect(QRectF(0, r.bottom(), w, h - r.bottom()), dim_brush)
                    painter.fillRect(QRectF(0, r.top(), r.left(), r.height()), dim_brush)
                    painter.fillRect(QRectF(r.right(), r.top(), w - r.right(), r.height()), dim_brush)
                else:
                    painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        else:
            # --- ДИНАМИЧЕСКИЙ РЕЖИМ (ВКЛЮЧЕН ЧЕКБОКС «ДИНАМИЧЕСКИЙ ФОН») ---
            # Фон не замирает! Живой рабочий стол воспроизводится сквозь оверлей
            if r is not None:
                dim_brush = QBrush(QColor(0, 0, 0, 120))
                painter.fillRect(QRectF(0, 0, w, r.top()), dim_brush)
                painter.fillRect(QRectF(0, r.bottom(), w, h - r.bottom()), dim_brush)
                painter.fillRect(QRectF(0, r.top(), r.left(), r.height()), dim_brush)
                painter.fillRect(QRectF(r.right(), r.top(), w - r.right(), r.height()), dim_brush)

                # Заполняем область выделения цветом с альфа = 1:
                # 99.6% прозрачно (живое видео видно без помех),
                # но Windows DWM регистрирует пиксель и перехватывает клики (рисование карандашом
                # и фигурами не нажимает на программы и видео на заднем плане!)
                painter.fillRect(r, QColor(0, 0, 0, 1))
            else:
                painter.fillRect(self.rect(), QColor(0, 0, 0, 70))

        if r is not None:
            # Если выбран общий фильтр
            if self.current_filter != FilterType.NONE:
                self._paint_filtered_region(painter, r)

            # Пунктирная контурная рамка выделения Lightshot (снаружи на 1px)
            border_rect = QRectF(r.left() - 1, r.top() - 1, r.width() + 2, r.height() + 2)
            pen_border = QPen(QColor(56, 139, 253), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen_border)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(border_rect)

            # 8 маркеров изменения размера (рисуются строго снаружи выделения)
            if not self.is_locked:
                self._draw_handles(painter, r)

            # Отрисовываем все слои (мозаика, стрелки, текст, карандаш, круги)
            self.layer_manager.draw_all(painter, source_pixmap=self.background_pixmap)

            # Временная фигура при рисовании
            if self.temp_shape is not None:
                self.temp_shape.draw(painter, source_pixmap=self.background_pixmap)

            # Рамка и плашка с номером фигуры при перемещении ПКМ
            self._draw_dragged_shape_indicator(painter)

            # Подсветка всех интерактивных объектов при удержании клавиши Alt
            if getattr(self, "is_highlighting_objects", False):
                self._draw_interactive_objects_highlight(painter)
        elif getattr(self, "hovered_window_rect", None) and self.hovered_window_rect.isValid() and not self.hovered_window_rect.isEmpty():
            self._draw_hovered_window_indicator(painter)

    def _draw_dragged_shape_indicator(self, painter: QPainter):
        """Отрисовывает рамку трансформации в стиле Photoshop / Figma вокруг активной фигуры."""
        active_shape = None
        if hasattr(self, "transform_box") and self.transform_box.is_active():
            active_shape = self.transform_box.shape
        elif getattr(self, "active_editing_shape", None):
            active_shape = self.active_editing_shape

        if active_shape is None or not active_shape.visible or active_shape not in self.layer_manager.shapes:
            return

        bbox = active_shape.get_bounding_rect()
        if not bbox.isValid() or bbox.isEmpty():
            return

        try:
            shape_idx = self.layer_manager.shapes.index(active_shape) + 1
        except (ValueError, AttributeError):
            shape_idx = 1

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Отрисовка интерактивной рамки с 8 маркерами и маркером вращения
        if hasattr(self, "transform_box") and self.transform_box.is_active():
            self.transform_box.draw(painter)
        else:
            pad = 6.0
            frame_rect = bbox.adjusted(-pad, -pad, pad, pad)
            pen_border = QPen(QColor(59, 130, 246, 200), 1.5, Qt.PenStyle.DashLine)
            pen_border.setDashPattern([5, 3])
            painter.setPen(pen_border)
            painter.setBrush(QColor(59, 130, 246, 20))
            painter.drawRoundedRect(frame_rect, 4, 4)

        # 2. Плашка с названием, номером и углом поворота (например «Стрелка #1 (45°)»)
        rot = getattr(active_shape, "rotation", 0.0)
        rot_str = f" ({round(rot)}°)" if rot != 0.0 else ""
        badge_text = f"{active_shape.name} #{shape_idx}{rot_str}"
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(badge_text)
        text_h = fm.height()

        badge_w = text_w + 16
        badge_h = max(20, text_h + 4)

        top_y = bbox.top() - 32.0 - badge_h
        if top_y < 4:
            top_y = bbox.bottom() + 8.0

        badge_rect = QRectF(bbox.center().x() - badge_w / 2.0, top_y, badge_w, badge_h)

        # Фон плашки
        painter.setPen(QPen(QColor(59, 130, 246, 180), 1))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(badge_rect, 4, 4)

        # Текст плашки
        painter.setPen(QColor(248, 250, 252))
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

        painter.restore()

    def _draw_hovered_window_indicator(self, painter: QPainter):
        """Отрисовывает аккуратную подсветку и бейдж окна при наведении курсора до создания выделения."""
        hw = self.hovered_window_rect
        if not hw.isValid() or hw.isEmpty():
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Строгая контурная рамка и легкая подсветка окна
        pen = QPen(QColor(59, 130, 246, 200), 1.5, Qt.PenStyle.SolidLine)
        painter.setPen(pen)
        painter.setBrush(QColor(59, 130, 246, 15))
        painter.drawRect(hw)

        # 2. Бейдж с иконкой и названием окна в строгом минималистичном стиле
        title = self.hovered_window_title
        short_title = title if len(title) <= 35 else title[:33] + "…"
        badge_text = tr("rec_window_hover_badge", title=short_title)

        font = QFont("Segoe UI", 9)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(badge_text) + 32
        badge_h = 26.0

        bx = max(8.0, hw.left() + 6.0)
        by = max(8.0, hw.top() + 6.0)
        badge_rect = QRectF(bx, by, text_w, badge_h)

        painter.setPen(QPen(QColor(63, 63, 70), 1.0))
        painter.setBrush(QColor(24, 24, 27, 235))
        painter.drawRoundedRect(badge_rect, 4, 4)

        # Иконка окна
        ico = get_window_qicon(self.hovered_window_hwnd, size=16)
        painter.drawPixmap(int(bx + 6), int(by + 5), ico.pixmap(16, 16))

        painter.setPen(QColor(244, 244, 245))
        text_rect = QRectF(bx + 26, by, text_w - 26, badge_h)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, badge_text)

        painter.restore()

    def _paint_filtered_region(self, painter: QPainter, rect: QRectF):
        try:
            rx, ry, rw, rh = int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())
            if rw <= 0 or rh <= 0: return
            crop = self.background_pixmap.copy(rx, ry, rw, rh)
            qimg = crop.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            ptr = qimg.bits()
            ptr.setsize(qimg.sizeInBytes())
            arr = np.frombuffer(ptr, np.uint8).reshape((rh, rw, 4))
            bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
            fbgr = apply_filter(bgr, self.current_filter)
            frgb = cv2.cvtColor(fbgr, cv2.COLOR_BGR2RGB)
            fqimg = QImage(frgb.data, rw, rh, rw * 3, QImage.Format.Format_RGB888)
            painter.drawPixmap(rx, ry, QPixmap.fromImage(fqimg))
        except Exception as e:
            print(f"[Overlay] Ошибка фильтра: {e}")

    def _draw_handles(self, painter: QPainter, r: QRectF):
        painter.save()
        painter.setPen(QPen(QColor(56, 139, 253), 1.5))
        painter.setBrush(QBrush(QColor(255, 255, 255)))

        hs = HANDLE_SIZE
        points = [
            r.topLeft(),
            QPointF(r.center().x(), r.top()),
            r.topRight(),
            QPointF(r.right(), r.center().y()),
            r.bottomRight(),
            QPointF(r.center().x(), r.bottom()),
            r.bottomLeft(),
            QPointF(r.left(), r.center().y()),
        ]

        for pt in points:
            painter.drawRect(QRectF(pt.x() - hs/2, pt.y() - hs/2, hs, hs))

        painter.restore()

    def _is_highlight_key(self, event, is_release: bool = False) -> bool:
        hl_cfg = getattr(self.cfg, "hotkey_highlight_objects", "Alt").strip()
        hl_lower = hl_cfg.lower()
        key = event.key()
        if "alt" in hl_lower:
            if is_release:
                return key in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr)
            return key in (Qt.Key.Key_Alt, Qt.Key.Key_AltGr) or bool(event.modifiers() & Qt.KeyboardModifier.AltModifier)
        elif "ctrl" in hl_lower:
            if is_release:
                return key == Qt.Key.Key_Control
            return key == Qt.Key.Key_Control or bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        elif "shift" in hl_lower:
            if is_release:
                return key == Qt.Key.Key_Shift
            return key == Qt.Key.Key_Shift or bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        else:
            txt = event.text().strip().lower()
            return txt == hl_lower or (hasattr(Qt.Key, f"Key_{hl_cfg}") and key == getattr(Qt.Key, f"Key_{hl_cfg}"))

    def _draw_interactive_objects_highlight(self, painter: QPainter):
        """Подсвечивает все интерактивные/перемещаемые объекты рамками и бейджами с названиями при удержании Alt."""
        if not hasattr(self, "layer_manager") or not self.layer_manager.shapes:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        fm = QFontMetrics(font)

        for s in self.layer_manager.shapes:
            if not getattr(s, "visible", True):
                continue
            br = s.get_bounding_rect()
            if not br.isValid() or br.isEmpty():
                continue

            # 1. Контурная рамка вокруг объекта
            pen_box = QPen(QColor(56, 189, 248, 220), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen_box)
            painter.setBrush(QBrush(QColor(56, 189, 248, 30)))
            adj_rect = br.adjusted(-4, -4, 4, 4)
            painter.drawRoundedRect(adj_rect, 4, 4)

            # 2. Локализованное название фигуры
            tag_text = self._get_shape_display_name(s)
            text_w = fm.horizontalAdvance(tag_text)
            badge_w = text_w + 14
            badge_h = 20
            badge_x = adj_rect.left()
            badge_y = max(4.0, adj_rect.top() - 24.0)
            badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)

            # Отрисовка плашки бейджа
            painter.setPen(QPen(QColor(56, 189, 248), 1))
            painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
            painter.drawRoundedRect(badge_rect, 4, 4)

            # Текст бейджа
            painter.setPen(QColor(241, 245, 249))
            painter.setFont(font)
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, tag_text)

        painter.restore()

    def _get_shape_display_name(self, shape) -> str:
        if isinstance(shape, ArrowShape):
            return tr("obj_arrow", "Стрелка")
        elif isinstance(shape, LineShape):
            return tr("obj_line", "Линия")
        elif isinstance(shape, RectangleShape):
            return tr("obj_rect", "Прямоугольник")
        elif isinstance(shape, CircleShape):
            return tr("obj_circle", "Круг / Овал")
        elif isinstance(shape, TextShape):
            txt = getattr(shape, "text", "").strip()
            return f'{tr("obj_text", "Текст")}: "{txt[:10]}"' if txt else tr("obj_text", "Текст")
        elif isinstance(shape, MosaicShape):
            return tr("obj_mosaic", "Мозаика (Цензура)")
        elif isinstance(shape, BlurShape):
            return tr("obj_blur", "Размытие (Блюр)")
        elif isinstance(shape, RegionalEffectShape):
            etype = getattr(shape, "effect_type", "mosaic")
            key = f"censor_{etype}"
            return tr(key, getattr(shape, "name", tr("obj_interactive", "Объект")))
        elif isinstance(shape, PenShape):
            return tr("obj_highlighter", "Маркер") if getattr(shape, "is_highlighter", False) else tr("obj_pen", "Карандаш")
        return getattr(shape, "name", tr("obj_interactive", "Объект"))

    # --- Клавиатура ---
    def keyPressEvent(self, event):
        key = event.key()
        modifiers = event.modifiers()

        if self._is_highlight_key(event, is_release=False):
            if not getattr(self, "is_highlighting_objects", False):
                self.is_highlighting_objects = True
                self.update()

        if key == Qt.Key.Key_Escape:
            if self.is_recording:
                self.stop_recording()
            else:
                self.close_overlay()
        elif key == Qt.Key.Key_Delete:
            if getattr(self, "last_active_shape", None) is not None and self.last_active_shape in self.layer_manager.shapes:
                self._delete_shape(self.last_active_shape)
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_A:
                self.select_entire_screen()
            elif key == Qt.Key.Key_D:
                if getattr(self, "last_active_shape", None) is not None and self.last_active_shape in self.layer_manager.shapes:
                    self._duplicate_shape(self.last_active_shape)
            elif key == Qt.Key.Key_Z:
                self.history_manager.undo()
            elif key == Qt.Key.Key_Y:
                self.history_manager.redo()
            elif key == Qt.Key.Key_S:
                self.save_screenshot()
            elif key == Qt.Key.Key_C:
                self.copy_screenshot("standard")
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._is_highlight_key(event, is_release=True):
            if getattr(self, "is_highlighting_objects", False):
                self.is_highlighting_objects = False
                self.update()
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        if getattr(self, "is_highlighting_objects", False):
            self.is_highlighting_objects = False
            self.update()
        super().focusOutEvent(event)

    def select_entire_screen(self):
        """Выбирает всю доступную область экрана (эквивалент Ctrl+A или клика по иконке разворота на бейдже)."""
        self.selection_rect = QRectF(self.rect())
        self.hovered_window_rect = QRectF()
        self.hovered_window_hwnd = 0
        self.target_hwnd = None
        self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)
        self._show_toolbars()
        self._update_toolbar_positions()
        self.update()

    # --- Инструменты и панели ---
    def _on_tool_changed(self, tool_type):
        self.current_tool = tool_type
        if tool_type == ToolType.MOVE:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.selection_rect.isValid() else Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def _on_filter_changed(self, filter_type):
        self.current_filter = filter_type
        if self.capture_worker:
            self.capture_worker.set_filter(filter_type)
        self.update()

    def _on_lock_toggled(self, is_locked):
        self.is_locked = is_locked
        self.update()

    def _show_layers_dialog(self):
        if self.layers_dialog is None:
            self.layers_dialog = LayersDialog(self.layer_manager, self)
        pos = self.right_toolbar.mapToGlobal(QPoint(-self.layers_dialog.width() - 8, 40))
        self.layers_dialog.move(pos)
        self.layers_dialog.show()

    def _show_history_dialog(self):
        if self.history_dialog is None:
            self.history_dialog = HistoryDialog(self.history_manager, self)
        pos = self.right_toolbar.mapToGlobal(QPoint(-self.history_dialog.width() - 8, 80))
        self.history_dialog.move(pos)
        self.history_dialog.show()

    def _open_settings(self):
        dlg = SettingsDialog(self)
        dlg.exec()

    # --- Чистый захват скриншота без интерфейсных рамок ---
    def get_cropped_image(self) -> QImage:
        r = self.selection_rect.normalized()
        rx, ry, rw, rh = int(r.x()), int(r.y()), int(r.width()), int(r.height())
        if rw <= 0 or rh <= 0:
            return QImage()

        result = QImage(rw, rh, QImage.Format.Format_RGB32)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Для динамического режима и режима неосязаемой рамки считываем свежий живой кадр
        crop_pix = None
        if self.dynamic_bg or getattr(self, "is_passthrough", False):
            try:
                geo = self.geometry()
                fresh = safe_grab_screen_pixmap(rx + geo.x(), ry + geo.y(), rw, rh)
                if not fresh.isNull() and fresh.width() > 0:
                    crop_pix = fresh
            except Exception:
                crop_pix = None

        if crop_pix is None and self.background_pixmap is not None:
            crop_pix = self.background_pixmap.copy(rx, ry, rw, rh)

        if crop_pix is None:
            crop_pix = QPixmap(rw, rh)
            crop_pix.fill(Qt.GlobalColor.white)

        if self.current_filter != FilterType.NONE:
            bgr = qimage_to_cv2_bgr(crop_pix.toImage())
            fbgr = apply_filter(bgr, self.current_filter)
            frgb = cv2.cvtColor(fbgr, cv2.COLOR_BGR2RGB)
            crop_pix = QPixmap.fromImage(QImage(frgb.data, rw, rh, rw * 3, QImage.Format.Format_RGB888))

        painter.drawPixmap(0, 0, crop_pix)

        # Рисуем только пользовательские слои (без рамок ресайза и кнопок!)
        self.layer_manager.draw_all(painter, offset=QPointF(rx, ry), source_pixmap=crop_pix)
        painter.end()

        return result

    def save_screenshot(self, fmt="png"):
        self.hide()
        QApplication.processEvents()
        img = self.get_cropped_image()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        
        ext = fmt.lower() if fmt in ("png", "jpg", "webp") else "png"
        filename = f"Screenshot_{timestamp}.{ext}"
        default_path = str(Path(self.cfg.save_dir_screenshots) / filename)

        filter_str = f"{ext.upper()} Image (*.{ext})"
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить скриншот", default_path, f"{filter_str};;Все файлы (*.*)")
        if path:
            img.save(path)
            if self.cfg.auto_copy_to_clipboard:
                QApplication.clipboard().setImage(img)
            if self.cfg.play_sound:
                play_capture_sound()

            p = Path(path)
            self._notify(
                tr("notif_screen_saved_title"),
                tr("notif_screen_saved_body", filename=p.name, folder=str(p.parent)),
                QSystemTrayIcon.MessageIcon.Information,
                4000,
                target_path=path
            )
            self.close_overlay()
        else:
            self.show()

    def copy_screenshot(self, fmt="standard"):
        self.hide()
        QApplication.processEvents()
        img = self.get_cropped_image()
        clipboard = QApplication.clipboard()

        if fmt == "png":
            from PyQt6.QtCore import QMimeData, QBuffer, QIODevice
            mime = QMimeData()
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            ba = buf.data()
            buf.close()
            mime.setData("image/png", ba)
            mime.setImageData(img)
            clipboard.setMimeData(mime)
            desc = "Изображение (PNG) скопировано в буфер обмена."
        elif fmt == "jpg":
            from PyQt6.QtCore import QMimeData, QBuffer, QIODevice
            mime = QMimeData()
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "JPEG", 95)
            ba = buf.data()
            buf.close()
            mime.setData("image/jpeg", ba)
            mime.setImageData(img)
            clipboard.setMimeData(mime)
            desc = "Изображение (JPEG) скопировано в буфер обмена."
        elif fmt == "data_uri":
            import base64
            from PyQt6.QtCore import QBuffer, QIODevice
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            img.save(buf, "PNG")
            b64 = base64.b64encode(buf.data().data()).decode("ascii")
            buf.close()
            data_uri = f"data:image/png;base64,{b64}"
            clipboard.setText(data_uri)
            desc = "Data URI (Base64) скопирован в буфер в виде текста."
        else:
            clipboard.setImage(img)
            desc = "Изображение скопировано в буфер обмена (готово для вставки Ctrl+V)."

        if self.cfg.play_sound:
            play_capture_sound()
        self._notify(
            tr("notif_clipboard_copied", "Скриншот скопирован в буфер обмена"),
            desc,
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )
        self.close_overlay()

    def search_image(self, engine="google"):
        self.hide()
        QApplication.processEvents()
        img = self.get_cropped_image()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

        saved_info = ""
        # 1. Сохранение скриншота на диск, если включена опция
        if getattr(self.cfg, "save_screenshot_on_search", True):
            filename = f"Screenshot_{timestamp}.png"
            path = str(Path(self.cfg.save_dir_screenshots) / filename)
            img.save(path)
            saved_info = f"\nСохранён: {filename}"

        # 2. Копирование в буфер обмена
        if self.cfg.auto_copy_to_clipboard:
            QApplication.clipboard().setImage(img)

        # 3. Звук захвата
        if self.cfg.play_sound:
            play_capture_sound()

        # 4. Преобразование в PNG байты для загрузки в поисковик
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buffer, "PNG")
        png_bytes = bytes(buffer.data().data())
        buffer.close()

        # 5. Уведомление
        engine_name = "Google Lens" if engine == "google" else "Яндекс Картинки"
        self._notify(
            f"Поиск в {engine_name}",
            f"Скриншот отправлен в {engine_name}.{saved_info}\nРезультаты открываются в браузере.",
            QSystemTrayIcon.MessageIcon.Information,
            3500
        )

        # 6. Запуск поиска в отдельном потоке
        threading.Thread(target=self._perform_image_search, args=(engine, png_bytes), daemon=True).start()
        self.close_overlay()

    def _perform_image_search(self, engine: str, png_bytes: bytes):
        search_by_image(engine, png_bytes, notify_func=self._notify)

    # --- Длинный скриншот (Scrolling Screenshot) ---
    def start_scrolling_screenshot(self):
        r = self.selection_rect.normalized()
        rx, ry, rw, rh = int(r.x()), int(r.y()), int(r.width()), int(r.height())
        if rw < 30 or rh < 30:
            return

        self._hide_toolbars()
        self.badge.hide()
        self.text_editor.hide()

        # Создаем плавающий HUD управления
        self.scrolling_hud = ScrollingCaptureHUD()
        self.scrolling_hud.adjustSize()
        hud_w = max(420, self.scrolling_hud.width())
        hud_h = max(42, self.scrolling_hud.height())

        screen_geo = QApplication.primaryScreen().geometry()
        # Выравниваем по горизонтали по центру выделения, строго удерживая в границах экрана
        hud_x = max(screen_geo.left() + 16, min(screen_geo.right() - hud_w - 16, rx + (rw - hud_w) // 2))

        # Вычисляем Y: над рамкой, под рамкой, или если весь экран — у верхнего края экрана
        if ry - hud_h - 12 >= screen_geo.top() + 8:
            hud_y = ry - hud_h - 12
        elif ry + rh + 12 + hud_h <= screen_geo.bottom() - 8:
            hud_y = ry + rh + 12
        else:
            # При выделении всего экрана размещаем HUD аккуратно у верхнего края
            hud_y = screen_geo.top() + 24

        self.scrolling_hud.move(hud_x, hud_y)
        self.scrolling_hud.show()

        self.scrolling_engine = ScrollingCaptureEngine(
            region=(rx, ry, rw, rh),
            target_hwnd=self.target_hwnd,
            parent=self
        )
        self.scrolling_engine.progress.connect(self.scrolling_hud.update_progress)
        self.scrolling_engine.finished.connect(self._on_scrolling_screenshot_finished)
        self.scrolling_engine.error.connect(self._on_scrolling_screenshot_error)
        self.scrolling_engine.cancelled.connect(self._on_scrolling_screenshot_cancelled)
        self.scrolling_hud.done_clicked.connect(self.scrolling_engine.finish)
        self.scrolling_hud.pause_toggled.connect(self._toggle_scrolling_pause)
        self.scrolling_hud.step_clicked.connect(self.scrolling_engine.capture_step)
        self.scrolling_hud.cancel_clicked.connect(self.scrolling_engine.cancel)

        # Сбрасываем старую рамку и слои, скрываем оверлей для чистого захвата содержимого экрана
        self.selection_rect = QRectF()
        self.initial_selection = QRectF()
        self.clearMask()
        self.is_passthrough = False
        self.layer_manager.clear()
        self.history_manager.clear()
        self._set_background_pixmap(None)
        self.repaint()
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
        self.hide()
        for _ in range(3):
            QApplication.processEvents()

        # Активируем окно под областью выделения для приема ввода колеса мыши
        try:
            hwnd_under = user32.WindowFromPoint(int(rx + rw // 2), int(ry + rh // 2))
            if hwnd_under:
                root_hwnd = user32.GetAncestor(hwnd_under, 2)  # GA_ROOT
                target = root_hwnd if root_hwnd else hwnd_under
                user32.SetForegroundWindow(target)
        except Exception:
            pass

        self.scrolling_engine.start()

    def _toggle_scrolling_pause(self):
        if self.scrolling_engine and self.scrolling_hud:
            is_paused = self.scrolling_engine.toggle_pause()
            self.scrolling_hud.set_paused_state(is_paused)

    def _on_scrolling_screenshot_finished(self, accumulated_bgr: np.ndarray):
        if self.scrolling_engine:
            self.scrolling_engine.cleanup()
            self.scrolling_engine = None
        if self.scrolling_hud:
            self.scrolling_hud.close()
            self.scrolling_hud = None

        H, W = accumulated_bgr.shape[:2]
        rgb = cv2.cvtColor(accumulated_bgr, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, W, H, W * 3, QImage.Format.Format_RGB888).copy()

        # 1. Буфер обмена
        if self.cfg.auto_copy_to_clipboard:
            QApplication.clipboard().setImage(qimg)

        # 2. Сохранение на диск
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"Screenshot_Long_{timestamp}.png"
        save_dir = Path(self.cfg.save_dir_screenshots)
        save_dir.mkdir(parents=True, exist_ok=True)
        path = str(save_dir / filename)
        qimg.save(path)

        if self.cfg.play_sound:
            play_capture_sound()

        self._notify(
            tr("notif_scroll_saved_title", "Длинный скриншот сохранен"),
            tr("notif_scroll_saved_body", filename=filename, height=H),
            QSystemTrayIcon.MessageIcon.Information,
            4000,
            target_path=path
        )
        self.close_overlay()

    def _on_scrolling_screenshot_error(self, err_msg: str):
        if self.scrolling_engine:
            self.scrolling_engine.cleanup()
            self.scrolling_engine = None
        if self.scrolling_hud:
            self.scrolling_hud.close()
            self.scrolling_hud = None
        self._notify(
            tr("scroll_err_title", "Ошибка длинного скриншота"),
            err_msg,
            QSystemTrayIcon.MessageIcon.Warning,
            3000
        )
        self.close_overlay()

    def _on_scrolling_screenshot_cancelled(self):
        if self.scrolling_engine:
            self.scrolling_engine.cleanup()
            self.scrolling_engine = None
        if self.scrolling_hud:
            self.scrolling_hud.close()
            self.scrolling_hud = None
        self.close_overlay()

    # --- Запись живого видео и GIF ---
    def start_recording(self, mode="video", params=None):
        params = params or {}
        r = self.selection_rect.normalized()
        self._hide_toolbars()
        self.badge.hide()
        self.text_editor.hide()
        
        mic = params.get("mic", getattr(self.cfg, "record_mic", True))
        system = params.get("system", getattr(self.cfg, "record_system", True))
        codec = params.get("codec", self.cfg.video_codec)
        target_hwnd = params.get("target_hwnd", self.target_hwnd)
        countdown = params.get("countdown", getattr(self.cfg, "record_countdown_enabled", False))
        countdown_seconds = params.get("countdown_seconds", getattr(self.cfg, "record_countdown_seconds", 3))

        if mode == "gif":
            fps = params.get("fps", self.cfg.gif_fps)
            self.cfg.gif_fps = fps
            if "colors" in params:
                self.cfg.gif_colors = params["colors"]
            if "dither" in params:
                self.cfg.gif_dither = params["dither"]
            self.config_mgr.save()

        rec_window = RecordingFrameWindow(
            mode=mode,
            rect=r,
            record_mic=mic,
            record_system=system,
            codec=codec,
            target_hwnd=target_hwnd,
            countdown=countdown,
            countdown_seconds=countdown_seconds
        )
        
        # Показываем окно записи сразу без мигания и задержек
        rec_window.show()
        rec_window.raise_()
        rec_window.activateWindow()

        # Регистрируем окно записи в приложении
        app_inst = getattr(QApplication.instance(), "app_instance", None)
        if app_inst:
            app_inst.add_recording(rec_window)

        if hasattr(self, "_unclip_timer"):
            self._unclip_timer.stop()
        while QApplication.overrideCursor():
            QApplication.restoreOverrideCursor()
        release_mouse_traps()

        # Сбрасываем старую рамку и скрываем оверлей
        self.clearMask()
        self.is_passthrough = False
        self.selection_rect = QRectF()
        self.initial_selection = QRectF()
        self._set_background_pixmap(None)
        self.layers_cache_pixmap = None
        self.temp_shape = None
        self.dragged_shape = None
        self.layer_manager.clear()
        self.history_manager.clear()
        self._invalidate_layers_cache()
        self.hovered_window_rect = QRectF()
        self.hovered_window_title = ""
        self.hovered_window_hwnd = 0
        self.target_hwnd = None
        self.repaint()
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
        self.hide()
        for _ in range(5):
            QApplication.processEvents()
        self.capture_closed.emit()

    def close_overlay(self):
        if hasattr(self, "_unclip_timer"):
            self._unclip_timer.stop()
        if hasattr(self, "right_hold_timer"):
            self.right_hold_timer.stop()
        if getattr(self, "shape_edit_popup", None) is not None:
            try:
                self.shape_edit_popup.close()
            except Exception:
                pass
            self.shape_edit_popup = None
        if getattr(self, "scrolling_hud", None) is not None:
            try:
                self.scrolling_hud.close()
            except Exception:
                pass
            self.scrolling_hud = None
        if getattr(self, "scrolling_engine", None) is not None:
            try:
                self.scrolling_engine.cancel()
            except Exception:
                pass
            self.scrolling_engine = None
        self.hovered_window_rect = QRectF()
        self.hovered_window_title = ""
        self.hovered_window_hwnd = 0
        self.target_hwnd = None
        while QApplication.overrideCursor():
            QApplication.restoreOverrideCursor()
        release_mouse_traps()
        self.right_toolbar.properties_flyout.hide()
        self._hide_toolbars()
        self.badge.hide()
        self.text_editor.hide()
        self.clearMask()
        self.is_passthrough = False
        if hasattr(self, "bottom_toolbar"):
            self.bottom_toolbar.chk_passthrough.setChecked(False)
            self.bottom_toolbar.chk_dynamic_bg.setChecked(False)
        self.selection_rect = QRectF()
        self.initial_selection = QRectF()
        self._set_background_pixmap(None)
        self.layers_cache_pixmap = None
        self.temp_shape = None
        self.dragged_shape = None
        self.layer_manager.clear()
        self.history_manager.clear()
        self.repaint()
        self.hide()
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
        for _ in range(3):
            QApplication.processEvents()
        self.capture_closed.emit()
