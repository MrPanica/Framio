# -*- coding: utf-8 -*-
"""
Полноэкранный оверлей захвата экрана в стиле Lightshot.
Оптимизирован для гладкого рисования (60+ FPS), поддерживает живую запись экрана,
инструмент перемещения по умолчанию, мозаичную цензуру текста и память настроек для каждого инструмента.
"""

from __future__ import annotations

import sys
import os
import time
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import math
import copy
import numpy as np
from PyQt6.QtCore import Qt, QRectF, QPointF, pyqtSignal, QPoint, QRect, QBuffer, QIODevice, QTimer, QUrl
from PyQt6.QtWidgets import (
    QWidget, QApplication, QLineEdit, QFileDialog, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPixmap, QImage, QCursor, QFont, QRegion, QFontMetrics, QPainterPath
)

from config import ConfigManager
from models.shapes import (
    BaseShape, PenShape, LineShape, ArrowShape,
    RectangleShape, CircleShape, TextShape, MosaicShape, BlurShape, RegionalEffectShape, CaptureMaskShape
)
from models.layers import LayerManager
from models.history import HistoryManager, HistoryCommand
from utils.image_filters import apply_filter, FilterType
from utils.sound import play_capture_sound
from utils.screen_lock import safe_grab_screen_pixmap, qimage_to_cv2_bgr, user32, enumerate_recordable_windows, POINT
from utils.window_icon import get_window_qicon
from utils.capture_mask import apply_mask_to_qimage
from utils.image_search import search_by_image
from utils.win32_helper import release_mouse_traps, force_foreground_window
from utils.i18n import tr
import ctypes
from ctypes import wintypes

from .toolbars import (
    RightDrawingToolbar, BottomActionToolbar, RegionActionHeader, ToolType, get_theme_styles,
    show_smart_popup, get_context_menu_style
)
from .icons import create_themed_icon, create_tool_cursor
from .shape_editor import ShapeEditPopup
from .widgets import DimensionBadge, MassRecordingHud
from .text_widget import InteractiveTextEditor
from .layers_dialog import LayersDialog
from .history_dialog import HistoryDialog
from .settings_dialog import SettingsDialog
from .transform_box import ShapeTransformBox, HandleType, rotate_point, ShapeGroup

# Загружается только при фактическом старте записи. Оставляем имя на уровне
# модуля для совместимости с тестами и внешними интеграциями, которые его
# подменяют через unittest.mock.patch.
RecordingFrameWindow = None

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
        self.dimmed_background_pixmap = None
        self.regions = []
        # У каждой зоны может быть несколько независимых контуров маски.
        self.capture_masks: dict[int, list[CaptureMaskShape]] = {}
        self.active_region_idx = 0
        self.is_adding_region = False
        self.is_selecting = False
        self.pending_outside_drag = False
        self.pending_outside_pos = QPointF()
        self.pending_outside_region_idx = None
        self.is_resizing = False
        self.active_handle = HANDLE_NONE
        self.drag_start_pos = QPointF()
        self.initial_selection = QRectF()
        self.region_transform_initial_idx = None
        self.region_transform_initial_rect = QRectF()
        self.region_transform_initial_masks = []
        self.region_transform_initial_shapes = []
        self.region_selection_history_before = None
        self.is_locked = False
        self.region_locks: dict[int, bool] = {}
        self.dynamic_bg = False  # По умолчанию фон замирает (статический режим)
        self.is_passthrough = False  # Режим неосязаемой рамки (сквозные клики в фоновые окна)

        # Модели
        self.layer_manager = LayerManager(self)
        self.history_manager = HistoryManager(self)
        self.current_tool = ToolType.MOVE
        self.current_filter = FilterType.NONE
        self.filter_params = {"blur_radius": 15, "pixel_size": 12}
        # Эффект хранится отдельно для каждой зоны. Нижняя панель меняет
        # только активную зону, а верхняя панель может изменить доступные зоны
        # одной операцией.
        self.region_filters: dict[int, str] = {}
        self.region_filter_params: dict[int, dict] = {}
        self.mass_filter = FilterType.NONE
        self.mass_filter_params = {"blur_radius": 15, "pixel_size": 12}
        self.is_highlighting_objects = False

        # Кэш отрисовки слоев для мгновенного отклика (60+ FPS)
        self.layers_cache_pixmap = None
        self.temp_shape = None
        self.selected_shapes: list[BaseShape] = []
        self.is_selecting_objects = False
        self.is_moving_objects = False
        self.object_selection_start = QPointF()
        self.object_selection_rect = QRectF()
        self.object_drag_initial_states = []
        self.object_drag_moved = False
        self.object_drag_started = False

        # HWND выбранного окна записи; устанавливается двойным кликом.
        self.target_hwnd = None

        # Длинный скриншот
        self.scrolling_engine = None
        self.scrolling_hud = None

        # Запись видео/GIF
        self.is_recording = False
        self._mass_recording = False
        self._recording_overlay_closed = False
        self.capture_worker = None
        self.recording_windows: list[RecordingFrameWindow] = []
        self.recording_region_indices: set[int] = set()

        # Виджеты
        self.badge = DimensionBadge(self)
        self.right_toolbar = RightDrawingToolbar(self)
        self.bottom_toolbar = BottomActionToolbar(self)
        self.region_header = RegionActionHeader(self)
        self.region_header.hide()
        self.recording_hud = None
        # Снимок выбранных зон на время одиночной записи. Он не зависит от
        # текущего активного индекса и не даёт свободным зонам исчезнуть из
        # overlay, пока окно записи меняет свою геометрию.
        self._recording_selection_regions: list[tuple[int, QRectF]] = []
        self.layers_dialog = None
        self.history_dialog = None

        # Интерактивный редактор текста с ручкой перемещения и ресайзом
        self.text_editor = InteractiveTextEditor(self)
        self.text_editor.hide()
        self.text_editor.committed.connect(self._commit_text)
        self.text_editor.cancelled.connect(self._cancel_text_editing)
        self.text_editor.font_size_changed.connect(self._on_editor_font_size_changed)
        self.text_editor.moved.connect(self._on_editor_moved)
        self.editing_existing_text_shape = None

        # Режим пипетки (взятие цвета с экрана)
        self.is_eyedropper_active = False
        self.eyedropper_callback = None
        self.eyedropper_pos = QPointF()

        # Контекстное меню и удержание правой кнопки мыши
        self.right_clicked_shape = None
        self.right_press_global_pos = None
        self.right_hold_timer = QTimer(self)
        self.right_hold_timer.setSingleShot(True)
        self.right_hold_timer.timeout.connect(self._on_right_hold_timeout)
        self.shape_edit_popup = None
        self.last_active_shape = None
        self.active_editing_shape = None
        self.interactive_badge_expand_rect = QRectF()
        self.selected_shapes = []
        self.is_selecting_objects = False
        self.is_moving_objects = False
        self.object_selection_rect = QRectF()
        self.object_drag_initial_states = []
        self.object_drag_started = False

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
        self.right_toolbar.capture_mask_chosen.connect(self._on_capture_mask_chosen)
        self.right_toolbar.tool_settings_updated.connect(self._on_tool_settings_updated)
        self.right_toolbar.layers_clicked.connect(self._show_layers_dialog)
        self.right_toolbar.history_clicked.connect(self._show_history_dialog)
        self.right_toolbar.undo_clicked.connect(self.history_manager.undo)
        self.right_toolbar.redo_clicked.connect(self.history_manager.redo)
        self.right_toolbar.filter_selected.connect(self._on_filter_changed)
        self.right_toolbar.pipette_requested.connect(self.start_eyedropper)

        # Нижняя панель
        self.bottom_toolbar.save_clicked.connect(self.save_screenshot)
        self.bottom_toolbar.copy_clicked.connect(self.copy_screenshot)
        self.bottom_toolbar.scrolling_screenshot_requested.connect(self.start_scrolling_screenshot)
        self.bottom_toolbar.search_image_requested.connect(self.search_image)
        self.bottom_toolbar.record_video_started.connect(lambda p: self.start_recording("video", p))
        self.bottom_toolbar.record_gif_started.connect(lambda p: self.start_recording("gif", p))
        self.bottom_toolbar.filter_selected.connect(self._on_filter_changed)
        self.bottom_toolbar.filter_parameters_changed.connect(self._on_filter_parameters_changed)
        self.bottom_toolbar.lock_toggled.connect(self._on_lock_toggled)
        self.bottom_toolbar.dynamic_bg_toggled.connect(self._on_dynamic_bg_toggled)
        self.bottom_toolbar.passthrough_toggled.connect(self._on_passthrough_toggled)
        self.right_toolbar.passthrough_toggled.connect(self._on_passthrough_toggled)
        self.bottom_toolbar.settings_clicked.connect(self._open_settings)
        self.bottom_toolbar.add_region_clicked.connect(self.start_adding_region)
        self.bottom_toolbar.all_regions_action.connect(self._run_all_regions_action)
        self.bottom_toolbar.close_clicked.connect(self.handle_close_clicked)
        self.region_header.add_mode_toggled.connect(self._on_region_header_add_toggled)
        self.region_header.close_clicked.connect(self._on_region_header_close)
        self.region_header.save_clicked.connect(lambda fmt: self.save_screenshot(fmt, all_regions=True))
        self.region_header.copy_clicked.connect(lambda fmt: self.copy_screenshot(fmt, all_regions=True))
        self.region_header.record_video_started.connect(lambda p: self.start_recording("video", p, all_regions=True))
        self.region_header.record_gif_started.connect(lambda p: self.start_recording("gif", p, all_regions=True))
        self.region_header.mass_filter_selected.connect(self._on_mass_filter_changed)
        self.region_header.mass_filter_parameters_changed.connect(self._on_mass_filter_parameters_changed)
        self.region_header.ui_snapshot_requested.connect(self.capture_ui_snapshot)

        # Бейдж размеров (кнопка разворота на весь экран)
        self.badge.fullscreen_clicked.connect(self.select_entire_screen)

    @property
    def selection_rect(self) -> QRectF:
        if 0 <= self.active_region_idx < len(self.regions):
            return self.regions[self.active_region_idx]
        return QRectF()

    @selection_rect.setter
    def selection_rect(self, val: QRectF):
        if not val.isValid() or val.isEmpty():
            if len(self.regions) <= 1:
                self.regions.clear()
                self.active_region_idx = 0
            elif 0 <= self.active_region_idx < len(self.regions):
                self.regions[self.active_region_idx] = QRectF()
            return

        if not self.regions:
            self.regions.append(val)
            self.active_region_idx = 0
        elif 0 <= self.active_region_idx < len(self.regions):
            self.regions[self.active_region_idx] = val
        else:
            self.regions.append(val)
            self.active_region_idx = len(self.regions) - 1

    def _capture_masks_for_region(self, region_idx: int | None = None) -> list[CaptureMaskShape]:
        """Возвращает копию списка масок выбранной зоны."""
        idx = self.active_region_idx if region_idx is None else region_idx
        return list(self.capture_masks.get(idx, []))

    def _refresh_region_geometry(self, region_idx: int):
        """Обновляет интерфейс после изменения рамки зоны или её масок."""
        if region_idx == getattr(self, "active_region_idx", region_idx):
            self._update_toolbar_positions()
        self._sync_close_button_tooltip()
        if getattr(self, "is_passthrough", False) and hasattr(self, "_sync_passthrough_mask"):
            self._sync_passthrough_mask()
        self._invalidate_layers_cache()
        self.update()

    def _shapes_for_region(self, region_idx: int | None = None) -> list:
        """Возвращает список фигур, принадлежащих указанной зоне."""
        idx = self.active_region_idx if region_idx is None else region_idx
        if not hasattr(self, "regions") or not (0 <= idx < len(self.regions)):
            return []
        reg = self.regions[idx]
        if not isinstance(reg, QRectF) or not reg.isValid() or reg.isEmpty():
            return []
        result = []
        for s in getattr(self.layer_manager, "shapes", []):
            s_idx = getattr(s, "region_idx", None)
            if s_idx is not None:
                if s_idx == idx:
                    result.append(s)
            else:
                b = s.get_bounding_rect() if hasattr(s, "get_bounding_rect") else QRectF()
                if reg.contains(b.center()) or reg.intersects(b):
                    s.region_idx = idx
                    result.append(s)
        return result

    def _apply_region_history_state(self, region_idx: int, region: QRectF, mask_states=None, shape_states=None):
        """Применяет сохранённое состояние рамки, геометрии её масок и фигур."""
        if not (0 <= region_idx < len(self.regions)):
            return
        self.regions[region_idx] = QRectF(region)
        if mask_states is not None:
            for target, source in zip(self._capture_masks_for_region(region_idx), mask_states):
                self._apply_shape_geometry(target, source)
        if shape_states is not None:
            for target, source in shape_states:
                self._apply_shape_geometry(target, source)
                if hasattr(target, "update_effect") and self.background_pixmap is not None:
                    try:
                        target.update_effect(self.background_pixmap)
                    except Exception:
                        pass
        self._refresh_region_geometry(region_idx)

    def _push_region_geometry_history(
        self,
        region_idx: int,
        old_region: QRectF,
        new_region: QRectF,
        old_masks=None,
        new_masks=None,
        old_shapes=None,
        new_shapes=None,
        description: str | None = None,
    ):
        """Добавляет одно Undo/Redo-действие для итогового жеста зоны."""
        history = getattr(self, "history_manager", None)
        if history is None:
            return
        cmd = HistoryCommand(
            description or tr("hist_cmd_region_transform", "Изменение области записи"),
            do_func=lambda: self._apply_region_history_state(region_idx, new_region, new_masks, new_shapes),
            undo_func=lambda: self._apply_region_history_state(region_idx, old_region, old_masks, old_shapes),
        )
        history.push_already_done(cmd)

    def _capture_region_layout_state(self):
        """Сохраняет список зон и привязанные к ним маски для Undo/Redo."""
        return {
            "regions": [QRectF(region) for region in self.regions],
            "capture_masks": {
                idx: list(masks) for idx, masks in self.capture_masks.items()
            },
            "region_filters": dict(self.__dict__.get("region_filters", {})),
            "region_filter_params": {
                idx: dict(params)
                for idx, params in self.__dict__.get("region_filter_params", {}).items()
            },
            "active_region_idx": self.active_region_idx,
        }

    def _restore_region_layout_state(self, state):
        """Восстанавливает список зон без создания новой команды истории."""
        self.regions = [QRectF(region) for region in state["regions"]]
        self.capture_masks = {
            idx: list(masks) for idx, masks in state["capture_masks"].items()
        }
        self.region_filters = dict(state.get("region_filters", {}))
        self.region_filter_params = {
            idx: dict(params)
            for idx, params in state.get("region_filter_params", {}).items()
        }
        self.active_region_idx = state["active_region_idx"]
        self._sync_active_region_filter_ui()
        if hasattr(self, "transform_box"):
            masks = self._capture_masks_for_region()
            self.transform_box.set_shape(masks[-1] if masks else None)
        if self.regions:
            self._update_toolbar_positions()
        self._sync_close_button_tooltip()
        self._invalidate_layers_cache()
        self.update()

    def _push_region_layout_history(self, before, after, description: str):
        """Добавляет одно действие для добавления или удаления зон."""
        history = getattr(self, "history_manager", None)
        if history is None:
            return
        cmd = HistoryCommand(
            description,
            do_func=lambda state=after: self._restore_region_layout_state(state),
            undo_func=lambda state=before: self._restore_region_layout_state(state),
        )
        history.push_already_done(cmd)

    def _commit_region_selection_history(self):
        """Фиксирует добавление зоны после завершения её выделения."""
        before = self.region_selection_history_before
        self.region_selection_history_before = None
        if before is None:
            return False
        self._push_region_layout_history(
            before,
            self._capture_region_layout_state(),
            tr("hist_cmd_add_region", "Добавление области записи"),
        )
        return True

    def _begin_region_transform_history(self):
        """Запоминает начало ресайза/перемещения до первого события мыши."""
        self.region_transform_initial_idx = self.active_region_idx
        self.region_transform_initial_rect = QRectF(self.selection_rect)
        self.region_transform_initial_masks = [
            mask.clone() for mask in self._capture_masks_for_region(self.active_region_idx)
        ]
        self.region_transform_initial_shapes = [
            (shape, shape.clone())
            for shape in self._shapes_for_region(self.active_region_idx)
        ]

    def _begin_region_move(self, pos: QPointF) -> bool:
        """Начинает перемещение текущей зоны одним непрерывным жестом."""
        if self._active_region_is_locked():
            return False
        self.is_resizing = True
        self.active_handle = HANDLE_MOVE
        self.drag_start_pos = QPointF(pos)
        self.initial_selection = QRectF(self.selection_rect)
        self._begin_region_transform_history()
        self._set_cursor_if_needed(Qt.CursorShape.ClosedHandCursor)
        try:
            self.grabMouse()
        except Exception:
            pass
        return True

    def _active_region_is_locked(self) -> bool:
        """Возвращает замок только активной зоны, а не глобальное состояние."""
        idx = getattr(self, "active_region_idx", 0)
        locks = self.__dict__.get("region_locks", {})
        return bool(locks.get(idx, False))

    def _commit_region_transform_history(self) -> bool:
        """Фиксирует весь жест изменения зоны одной командой истории."""
        idx = getattr(self, "region_transform_initial_idx", None)
        old_region = getattr(self, "region_transform_initial_rect", QRectF())
        if idx is None or not (0 <= idx < len(self.regions)):
            return False

        new_region = QRectF(self.regions[idx])
        changed = any(
            abs(left - right) > 0.01
            for left, right in (
                (old_region.left(), new_region.left()),
                (old_region.top(), new_region.top()),
                (old_region.width(), new_region.width()),
                (old_region.height(), new_region.height()),
            )
        )
        old_masks = list(self.__dict__.get("region_transform_initial_masks", []))
        new_masks = [mask.clone() for mask in self._capture_masks_for_region(idx)]
        old_shapes = list(self.__dict__.get("region_transform_initial_shapes", []))
        new_shapes = [
            (shape, shape.clone())
            for shape, _ in old_shapes
        ]
        self.region_transform_initial_idx = None
        self.region_transform_initial_rect = QRectF()
        self.region_transform_initial_masks = []
        self.region_transform_initial_shapes = []
        if not changed:
            return False

        self._push_region_geometry_history(
            idx,
            old_region,
            new_region,
            old_masks=old_masks,
            new_masks=new_masks,
            old_shapes=old_shapes,
            new_shapes=new_shapes,
        )
        return True

    def _fit_region_to_capture_masks(self, region_idx: int | None = None) -> bool:
        """Подгоняет рамку зоны под общий bounding box всех её масок.

        Маски хранятся в координатах overlay, поэтому нельзя использовать
        ``selection_rect``: его setter масштабирует фигуры вместе с рамкой.
        Прямое обновление ``regions`` меняет только область последующего
        захвата и оставляет контуры неизменными.
        """
        idx = self.active_region_idx if region_idx is None else region_idx
        if not hasattr(self, "regions") or not (0 <= idx < len(self.regions)):
            return False

        bounds = QRectF()
        for mask in self._capture_masks_for_region(idx):
            if not getattr(mask, "visible", True):
                continue
            path = mask.path()
            if path.isEmpty():
                continue
            mask_bounds = path.boundingRect().normalized()
            if mask_bounds.isEmpty():
                continue
            bounds = mask_bounds if bounds.isEmpty() else bounds.united(mask_bounds)

        if bounds.isEmpty():
            return False

        old_region = QRectF(self.regions[idx])
        self.regions[idx] = bounds
        self._refresh_region_geometry(idx)
        self._push_region_geometry_history(
            idx,
            old_region,
            bounds,
            description=tr("hist_cmd_mask_fit_region", "Область по размеру маски"),
        )
        return True

    def _capture_mask_at(self, pos: QPointF, region_idx: int | None = None):
        """Находит верхнюю маску под точкой, чтобы её можно было редактировать."""
        for mask in reversed(self._capture_masks_for_region(region_idx)):
            if mask.visible and mask.hit_test_rotated(pos):
                return mask
        return None

    def _set_capture_mask_presence(self, region_idx: int, mask: CaptureMaskShape, present: bool, index: int):
        """Восстанавливает наличие маски без создания новой команды истории."""
        masks = list(self.capture_masks.get(region_idx, []))
        if present:
            if not any(item is mask for item in masks):
                masks.insert(min(max(0, index), len(masks)), mask)
            self.capture_masks[region_idx] = masks
            if hasattr(self, "transform_box"):
                self.transform_box.set_shape(mask)
            self.active_editing_shape = mask
            self.last_active_shape = mask
        else:
            masks = [item for item in masks if item is not mask]
            if masks:
                self.capture_masks[region_idx] = masks
            else:
                self.capture_masks.pop(region_idx, None)
            if getattr(self, "transform_box", None) is not None and self.transform_box.shape is mask:
                self.transform_box.set_shape(masks[-1] if masks else None)
            if getattr(self, "active_editing_shape", None) is mask:
                self.active_editing_shape = None
            if getattr(self, "last_active_shape", None) is mask:
                self.last_active_shape = None
        self._invalidate_layers_cache()
        self.update()

    def _delete_capture_mask(self, mask: CaptureMaskShape):
        """Удаляет один контур маски, не затрагивая остальные контуры зоны."""
        if mask is None:
            return
        for region_idx, masks in list(self.capture_masks.items()):
            mask_index = next((idx for idx, item in enumerate(masks) if item is mask), None)
            if mask_index is None:
                continue
            masks = [item for item in masks if item is not mask]
            if masks:
                self.capture_masks[region_idx] = masks
            else:
                self.capture_masks.pop(region_idx, None)

            if getattr(self, "transform_box", None) is not None and self.transform_box.shape is mask:
                self.transform_box.set_shape(masks[-1] if masks else None)
            if getattr(self, "active_editing_shape", None) is mask:
                self.active_editing_shape = None
            if getattr(self, "last_active_shape", None) is mask:
                self.last_active_shape = None
            self._invalidate_layers_cache()
            self.update()
            history = getattr(self, "history_manager", None)
            if history is not None:
                cmd = HistoryCommand(
                    tr("hist_cmd_delete", "Удаление {name}", name=mask.name),
                    do_func=lambda i=region_idx, m=mask, n=mask_index: self._set_capture_mask_presence(i, m, False, n),
                    undo_func=lambda i=region_idx, m=mask, n=mask_index: self._set_capture_mask_presence(i, m, True, n),
                )
                history.push_already_done(cmd)
            return

    def get_valid_regions(self) -> list[QRectF]:
        """Возвращает список всех корректных нормализованных зон выделения."""
        return [region for _, region in self.get_valid_region_items()]

    def get_valid_region_items(self) -> list[tuple[int, QRectF]]:
        """Возвращает исходные индексы и нормализованные корректные зоны."""
        regions = self.__dict__.get("regions", [])
        return [
            (idx, region.normalized())
            for idx, region in enumerate(regions)
            if region.isValid() and not region.isEmpty() and region.width() > 1 and region.height() > 1
        ]

    def get_action_region_items(self, all_regions: bool = False) -> list[tuple[int, QRectF]]:
        """Возвращает зоны для действия, исключая уже записывающиеся зоны."""
        recording = set(self.__dict__.get("recording_region_indices", set()))
        valid_items = self.get_valid_region_items()
        if all_regions:
            return [(idx, region) for idx, region in valid_items if idx not in recording]

        active_idx = self.__dict__.get("active_region_idx", 0)
        for idx, region in valid_items:
            if idx == active_idx and idx not in recording:
                return [(idx, region)]
        return []

    def clear_regions(self):
        """Очищает все зоны выделения и сбрасывает состояние мульти-выделения."""
        self.regions.clear()
        self.capture_masks.clear()
        self.__dict__.setdefault("region_locks", {}).clear()
        self.region_filters.clear()
        self.region_filter_params.clear()
        self._reset_mass_filter_state()
        self.active_region_idx = 0
        self.region_selection_history_before = None
        if hasattr(self, "recording_region_indices"):
            self.recording_region_indices.clear()
        self._set_add_region_mode(False)
        self._sync_close_button_tooltip()

    def _set_add_region_mode(self, active: bool):
        """Переключает постоянный режим добавления зон и синхронизирует UI."""
        self.is_adding_region = bool(active)
        if hasattr(self, "bottom_toolbar") and hasattr(self.bottom_toolbar, "update_add_region_state"):
            self.bottom_toolbar.update_add_region_state(self.is_adding_region)
        self._sync_region_header()
        self._set_cursor_if_needed(Qt.CursorShape.CrossCursor if self.is_adding_region else self._get_tool_cursor())
        self.update()

    def _on_region_header_add_toggled(self, active: bool):
        self._set_add_region_mode(active)

    def _on_region_header_close(self):
        """Крестик сверху отменяет arm-mode, а иначе закрывает все зоны."""
        if self.is_adding_region:
            self._set_add_region_mode(False)
        else:
            self.close_overlay()

    def set_active_region(self, idx: int):
        """Переключает активный фокус на выбранную зону выделения."""
        regions = self.__dict__.get("regions", [])
        if 0 <= idx < len(regions):
            self.active_region_idx = idx
            self.is_locked = self._active_region_is_locked()
            bottom_toolbar = self.__dict__.get("bottom_toolbar")
            if bottom_toolbar is not None and hasattr(bottom_toolbar, "set_lock_state"):
                bottom_toolbar.set_lock_state(self.is_locked)
            self.selection_rect = QRectF(regions[idx]).normalized()
            self.is_resizing = False
            self.active_handle = HANDLE_NONE
            try:
                self.releaseMouse()
            except Exception:
                pass
            transform_box = self.__dict__.get("transform_box")
            if transform_box is not None and hasattr(self, "_capture_masks_for_region"):
                masks = self._capture_masks_for_region(idx)
                transform_box.set_shape(masks[-1] if masks else None)
            if hasattr(self, "_update_toolbar_positions"):
                self._update_toolbar_positions()
            if hasattr(self, "_sync_close_button_tooltip"):
                self._sync_close_button_tooltip()
            if hasattr(self, "_sync_active_region_filter_ui"):
                self._sync_active_region_filter_ui()
            if hasattr(self, "_show_toolbars"):
                self._show_toolbars()
            if (
                self.__dict__.get("is_recording", False)
                and not self.__dict__.get("_mass_recording", False)
                and hasattr(self, "_refresh_single_recording_overlay_mask")
            ):
                self._refresh_single_recording_overlay_mask()
            try:
                self.update()
            except Exception:
                pass

    def start_adding_region(self):
        """Включает/выключает режим, в котором новые drag добавляются, а не заменяют зоны."""
        self._set_add_region_mode(not self.is_adding_region)

    def close_active_region(self):
        """Закрывает только активную зону, если их несколько, или закрывает весь оверлей."""
        valid = self.get_valid_regions()
        if len(valid) > 1 and 0 <= self.active_region_idx < len(self.regions):
            layout_before = self._capture_region_layout_state()
            removed_idx = self.active_region_idx
            if removed_idx in getattr(self, "recording_region_indices", set()):
                return
            self.regions.pop(self.active_region_idx)
            self.capture_masks.pop(self.active_region_idx, None)
            self.capture_masks = {
                (idx - 1 if idx > removed_idx else idx): mask
                for idx, mask in self.capture_masks.items()
                if idx != removed_idx
            }
            current_region_locks = self.__dict__.get("region_locks", {})
            self.region_locks = {
                (idx - 1 if idx > removed_idx else idx): locked
                for idx, locked in current_region_locks.items()
                if idx != removed_idx
            }
            current_region_filters = self.__dict__.get("region_filters", {})
            current_region_filter_params = self.__dict__.get("region_filter_params", {})
            self.region_filters = {
                (idx - 1 if idx > removed_idx else idx): filter_type
                for idx, filter_type in current_region_filters.items()
                if idx != removed_idx
            }
            self.region_filter_params = {
                (idx - 1 if idx > removed_idx else idx): params
                for idx, params in current_region_filter_params.items()
                if idx != removed_idx
            }
            self.recording_region_indices = {
                idx - 1 if idx > removed_idx else idx
                for idx in getattr(self, "recording_region_indices", set())
                if idx != removed_idx
            }
            for rec_window in getattr(self, "recording_windows", []):
                if getattr(rec_window, "region_index", 0) > removed_idx + 1:
                    rec_window.region_index -= 1
            self.active_region_idx = max(0, min(self.active_region_idx, len(self.regions) - 1))
            self._update_toolbar_positions()
            self._sync_close_button_tooltip()
            self._sync_active_region_filter_ui()
            self.update()
            self._push_region_layout_history(
                layout_before,
                self._capture_region_layout_state(),
                tr("hist_cmd_delete_region", "Удаление области записи"),
            )
        else:
            self.close_overlay()

    def activate_next_region(self):
        """Циклически активирует следующую зону выделения (Tab)."""
        valid = self.get_valid_regions()
        if len(valid) > 1:
            self.set_active_region((self.active_region_idx + 1) % len(self.regions))

    def activate_previous_region(self):
        """Циклически активирует предыдущую зону выделения (Shift+Tab)."""
        valid = self.get_valid_regions()
        if len(valid) > 1:
            self.set_active_region((self.active_region_idx - 1) % len(self.regions))

    def handle_close_clicked(self):
        """Обработка нажатия на кнопку закрытия в нижней панели."""
        if len(self.get_valid_regions()) > 1:
            self.close_active_region()
        else:
            self.close_overlay()

    def _run_all_regions_action(self, action: str):
        """Запускает явное групповое действие, не меняя поведение активной зоны."""
        self._set_add_region_mode(False)
        if action == "save":
            self.save_screenshot(all_regions=True)
        elif action == "copy":
            self.copy_screenshot("standard", all_regions=True)
        elif action == "video":
            self.start_recording("video", all_regions=True)
        elif action == "gif":
            self.start_recording("gif", all_regions=True)
        elif action == "ui_snapshot_save":
            self.capture_ui_snapshot("save")
        elif action == "ui_snapshot_copy":
            self.capture_ui_snapshot("copy")

    def _sync_close_button_tooltip(self):
        """Синхронизирует подсказку кнопки закрытия с количеством активных зон."""
        valid_count = len(self.get_valid_regions())
        if hasattr(self, "bottom_toolbar") and hasattr(self.bottom_toolbar, "update_multi_region_state"):
            self.bottom_toolbar.update_multi_region_state(valid_count > 1)
        self._sync_region_header()

    def _sync_region_header(self):
        header = self.__dict__.get("region_header")
        if header is None:
            return
        header.set_region_state(
            bool(getattr(self, "is_adding_region", False)),
            len(self.get_valid_regions()),
            len(self.get_action_region_items(all_regions=True)),
        )

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
        if hasattr(self, "bottom_toolbar") and hasattr(self.bottom_toolbar, "chk_passthrough"):
            self.bottom_toolbar.chk_passthrough.blockSignals(True)
            self.bottom_toolbar.chk_passthrough.setChecked(is_passthrough)
            self.bottom_toolbar.chk_passthrough.blockSignals(False)
        if hasattr(self, "right_toolbar") and hasattr(self.right_toolbar, "chk_passthrough"):
            self.right_toolbar.chk_passthrough.blockSignals(True)
            self.right_toolbar.chk_passthrough.setChecked(is_passthrough)
            self.right_toolbar.chk_passthrough.blockSignals(False)
        self._sync_passthrough_mask()
        self.update()

    def _sync_passthrough_mask(self):
        """
        Режим защищённой/осязаемой зоны: клики мыши внутри рамки
        выделения перехватываются Framio и НЕ взаимодействуют с фоновыми программами,
        окнами, видео или браузером позади неё.
        """
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

    def _clear_interactive_selection(self):
        """Сбрасывает все ссылки на выбранные объекты между захватами."""
        if getattr(self, "right_hold_timer", None) is not None:
            self.right_hold_timer.stop()
        popup = getattr(self, "shape_edit_popup", None)
        if popup is not None:
            try:
                popup.close()
            except Exception:
                pass
        self.shape_edit_popup = None
        self.last_active_shape = None
        self.active_editing_shape = None
        self.right_clicked_shape = None
        self.right_press_global_pos = None
        self.dragged_shape = None
        self.is_transforming = False
        self.interactive_badge_expand_rect = QRectF()
        if getattr(self, "transform_box", None) is not None:
            self.transform_box.set_shape(None)

    def _reset_mass_filter_state(self):
        """Сбрасывает применение массового эффекта при закрытии набора зон."""
        self.mass_filter = FilterType.NONE
        self.mass_filter_params = {"blur_radius": 15, "pixel_size": 12}
        header = self.__dict__.get("region_header")
        if header is not None and hasattr(header, "reset_mass_filter"):
            header.reset_mass_filter()

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
            shape = self.transform_box.shape
            is_mask = getattr(shape, "is_capture_mask", False)
            if isinstance(shape, ShapeGroup):
                if not any(s in self.layer_manager.shapes for s in shape.shapes):
                    self.transform_box.set_shape(None)
            elif not is_mask and shape not in self.layer_manager.shapes:
                self.transform_box.set_shape(None)
        self._invalidate_layers_cache()
        self.update()

    def _apply_shape_geometry(self, target: BaseShape, source: BaseShape):
        """Копирует геометрию из сохраненного состояния в целевую фигуру для Undo/Redo."""
        if not target or not source:
            return
        if hasattr(target, "shapes") and hasattr(source, "shapes"):
            for t_shape, s_shape in zip(target.shapes, source.shapes):
                self._apply_shape_geometry(t_shape, s_shape)
            target.rotation = getattr(source, "rotation", 0.0)
            if hasattr(source, "_base_rect"):
                target._base_rect = QRectF(source._base_rect)
            return
        import copy
        for attr in ("rect", "p1", "p2", "points", "path", "pos", "font_size", "rotation"):
            if hasattr(source, attr):
                val = getattr(source, attr)
                if callable(val):
                    continue
                if attr == "rect" and isinstance(val, QRectF):
                    copied = QRectF(val)
                elif attr in ("p1", "p2", "pos") and isinstance(val, QPointF):
                    copied = QPointF(val)
                elif attr == "points" and isinstance(val, list):
                    copied = [QPointF(p) for p in val]
                elif attr == "path" and isinstance(val, QPainterPath):
                    copied = QPainterPath(val)
                else:
                    copied = copy.copy(val)
                setattr(target, attr, copied)
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
        self.filter_params = {"blur_radius": 15, "pixel_size": 12}
        self.region_filters.clear()
        self.region_filter_params.clear()
        self._reset_mass_filter_state()
        self.is_highlighting_objects = False
        if hasattr(self, "bottom_toolbar") and hasattr(self.bottom_toolbar, "reset_filter"):
            self.bottom_toolbar.reset_filter()
        self.clear_regions()
        self.initial_selection = QRectF()
        self._clear_interactive_selection()
        self._set_background_pixmap(None)
        self.layers_cache_pixmap = None
        self.temp_shape = None
        self.setUpdatesEnabled(True)
        self.update()
        self.repaint()
        self.layer_manager.clear()
        self.history_manager.clear()
        self.is_selecting = False
        self.pending_outside_drag = False
        self.pending_outside_region_idx = None
        self.is_resizing = False
        self.is_recording = False
        self._mass_recording = False
        self.recording_region_indices.clear()
        self._recording_selection_regions = []
        self.dynamic_bg = False
        self.bottom_toolbar.chk_dynamic_bg.setChecked(False)
        self.bottom_toolbar.chk_passthrough.setChecked(False)
        if hasattr(self, "right_toolbar") and hasattr(self.right_toolbar, "chk_passthrough"):
            self.right_toolbar.chk_passthrough.setChecked(False)
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
        self.layers_cache_pixmap = None

        # По умолчанию инструмент перемещения (как в Lightshot)
        self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)

        # Ставим пустую маску перед show, чтобы Windows DWM не отображал старый буфер backing store
        self.setMask(QRegion())
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
        self.clearMask()
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
        if hasattr(self, "region_header"):
            self.region_header.hide()

    def _show_selection_overlay_during_single_recording(self):
        """Оставляет свободные зоны видимыми при записи только одной зоны.

        Отверстие в маске убирает overlay из записываемых прямоугольников,
        поэтому оставшиеся зоны можно видеть и выбирать, а интерфейс Framio
        не попадает в кадр записи.
        """
        self._sync_region_header()
        if hasattr(self, "region_header"):
            self.region_header.show()
        self.show()
        self.raise_()
        self._show_toolbars()
        self._update_toolbar_positions()
        self._refresh_single_recording_overlay_mask()
        # Не откладываем первый кадр overlay до следующего цикла событий:
        # окно записи создаётся сразу после этого метода и может забрать
        # фокус, поэтому свободные зоны должны быть отрисованы уже сейчас.
        try:
            self.repaint()
        except RuntimeError:
            # Лёгкие тестовые экземпляры создаются через __new__ без Qt
            # backing object; для них достаточно запланировать обновление.
            self.update()

    def _recording_selection_items(self) -> list[tuple[int, QRectF]]:
        """Возвращает стабильный набор зон для overlay во время записи."""
        snapshot = self.__dict__.get("_recording_selection_regions", [])
        if snapshot:
            return [
                (idx, QRectF(region).normalized())
                for idx, region in snapshot
                if region.isValid() and not region.isEmpty()
            ]
        return self.get_valid_region_items()

    def _sync_recording_selection_region_geometry(self, region_idx: int, region: QRectF):
        """Обновляет снимок зоны после её перемещения в overlay.

        Во время одиночной записи свободные зоны рисуются из отдельного
        снимка, чтобы запуск записи одной зоны не потерял остальные. Этот
        снимок должен следовать за обычным drag свободной зоны так же, как
        следуют панели инструментов и ``self.regions``.
        """
        snapshot = self.__dict__.get("_recording_selection_regions", [])
        if not snapshot:
            return
        normalized = QRectF(region).normalized()
        self._recording_selection_regions = [
            (idx, QRectF(normalized) if idx == region_idx else QRectF(old_region))
            for idx, old_region in snapshot
        ]

    def _should_preserve_selection_after_single_region_action(self, all_regions: bool) -> bool:
        """Определяет, нужно ли оставить набор зон после действия над одной зоной."""
        if all_regions:
            return False
        if getattr(self, "is_recording", False) and not getattr(self, "_mass_recording", False):
            return True
        return len(self.get_valid_region_items()) > 1

    def _restore_single_recording_overlay_after_action(self):
        """Возвращает интерактивный overlay после действия над одной зоной.

        Снимок временно скрывает overlay, чтобы его панели не попали в кадр.
        После завершения статического снимка или копирования возвращаем
        затемнение и все зоны. При уже идущей записи дополнительно сохраняем
        отверстия для окон записи и их z-порядок.
        """
        self.show()
        self._show_toolbars()
        self._sync_region_header()
        if hasattr(self, "region_header"):
            self.region_header.show()
        if getattr(self, "is_recording", False) and not getattr(self, "_mass_recording", False):
            self._refresh_single_recording_overlay_mask()
            # Top-level окна записи должны остаться выше overlay, чтобы их
            # Stop и Draw продолжали принимать клики после clipboard-действия.
            for rec_window in getattr(self, "recording_windows", []):
                try:
                    rec_window.raise_()
                except Exception:
                    pass
        else:
            self.raise_()
            self.activateWindow()

    def _refresh_single_recording_overlay_mask(self):
        """Обновляет кликабельные отверстия overlay во время одиночной записи.

        Внутри записываемой области не должно быть Framio, а вся рамка окна
        записи, включая шапку и панель рисования, должна получать клики сама.
        """
        if not getattr(self, "is_recording", False) or getattr(self, "_mass_recording", False):
            return

        recording_indices = set(getattr(self, "recording_region_indices", set()))
        try:
            mask = QRegion(self.rect())
        except Exception:
            mask = QRegion()
        window_holes = 0

        # Вырезаем фактические окна записи целиком. frameGeometry() у
        # top-level QWidget возвращается в экранных координатах, поэтому
        # переводим левый верхний угол в систему координат overlay.
        for rec_window in getattr(self, "recording_windows", []):
            try:
                frame_rect = rec_window.frameGeometry()
            except Exception:
                frame_rect = None
            if not isinstance(frame_rect, QRect) or not frame_rect.isValid():
                continue
            try:
                local_top_left = self.mapFromGlobal(frame_rect.topLeft())
            except Exception:
                continue
            local_rect = QRect(local_top_left, frame_rect.size())
            if local_rect.isValid():
                mask = mask.subtracted(QRegion(local_rect))
                window_holes += 1

        # До показа первого окна сохраняем отверстие по геометрии зоны записи.
        for region_idx, region in self._recording_selection_items():
            if region_idx in recording_indices and window_holes == 0:
                mask = mask.subtracted(QRegion(region.toRect().adjusted(-2, -2, 2, 2)))

        try:
            self.setMask(mask)
        except Exception:
            pass
        try:
            self.update()
        except Exception:
            pass

    def _on_recording_window_geometry_changed(self, rec_window=None):
        """Синхронизирует выбранную зону с перемещаемым окном записи.

        Окно записи живёт отдельно от полноэкранного overlay. Если обновлять
        только отверстие в маске, ``paintEvent`` продолжает рисовать контур
        исходной зоны на старом месте. Храним актуальную геометрию в том же
        списке зон, чтобы при каждом шаге drag старый контур и его фон сразу
        исчезали.
        """
        if rec_window is not None:
            region_index = getattr(rec_window, "region_index", None)
            try:
                region_idx = int(region_index) - 1
            except (TypeError, ValueError):
                region_idx = -1

            values = (
                getattr(rec_window, "inner_x", None),
                getattr(rec_window, "inner_y", None),
                getattr(rec_window, "inner_w", None),
                getattr(rec_window, "inner_h", None),
            )
            if (
                0 <= region_idx < len(self.regions)
                and all(isinstance(value, (int, float)) for value in values)
                and values[2] > 0
                and values[3] > 0
            ):
                self.regions[region_idx] = QRectF(*map(float, values))
                snapshot = self.__dict__.get("_recording_selection_regions", [])
                if snapshot:
                    self._recording_selection_regions = [
                        (idx, QRectF(values[0], values[1], values[2], values[3]))
                        if idx == region_idx else (idx, QRectF(region))
                        for idx, region in snapshot
                    ]
                # The ordinary selection controls are separate child
                # windows/widgets. Keep the badge and right/bottom toolbars
                # attached to the moved recording region as well; otherwise
                # they remain as a stale cluster at the old coordinates.
                if region_idx == getattr(self, "active_region_idx", -1):
                    self._update_toolbar_positions()

        self._refresh_single_recording_overlay_mask()

    def _raise_toolbar(self, toolbar):
        """Поднимает наведённую панель над второй панелью.

        Панели могут пересекаться у маленькой зоны. Обычный ``raise_`` меняет
        z-порядок только среди дочерних виджетов overlay и не влияет на окна
        записи, поэтому он безопасен для текущего режима захвата.
        """
        right_toolbar = self.__dict__.get("right_toolbar")
        bottom_toolbar = self.__dict__.get("bottom_toolbar")
        if toolbar is not right_toolbar and toolbar is not bottom_toolbar:
            return
        toolbar.raise_()

    def _hide_selection_toolbars(self):
        """Скрывает обычные инструменты выделения, не закрывая сами зоны."""
        right_toolbar = self.__dict__.get("right_toolbar")
        bottom_toolbar = self.__dict__.get("bottom_toolbar")
        badge = self.__dict__.get("badge")
        if right_toolbar is not None:
            right_toolbar.hide()
            flyout = getattr(right_toolbar, "properties_flyout", None)
            if flyout is not None:
                flyout.hide()
        if bottom_toolbar is not None:
            bottom_toolbar.hide()
        if badge is not None:
            badge.hide()

    def _show_toolbars(self):
        # Во время записи обычные панели скрыты только для зоны, которая
        # сейчас записывается. Свободная выбранная зона должна оставаться
        # полноценной и показывать инструменты справа и снизу.
        if getattr(self, "is_recording", False):
            recording_indices = set(getattr(self, "recording_region_indices", set()))
            if getattr(self, "active_region_idx", -1) in recording_indices:
                self._hide_selection_toolbars()
                return
            if (
                not self.selection_rect.isValid()
                or self.selection_rect.width() <= 20
                or self.selection_rect.height() <= 20
            ):
                self._hide_selection_toolbars()
                return
            self._update_toolbar_positions()
            self.right_toolbar.show()
            self.bottom_toolbar.show()
            self.badge.show()
            self._sync_region_header()
            return
        if self.selection_rect.isValid() and self.selection_rect.width() > 20 and self.selection_rect.height() > 20:
            self._update_toolbar_positions()
            self.right_toolbar.show()
            self.bottom_toolbar.show()
            self.badge.show()
            self._sync_region_header()

    def _update_toolbar_positions(self):
        r = self.selection_rect.normalized()
        try:
            scr_w = self.width()
            scr_h = self.height()
        except (RuntimeError, AttributeError):
            try:
                rect = self.rect()
                scr_w, scr_h = rect.width(), rect.height()
            except Exception:
                scr_w, scr_h = 1920, 1080

        # Адаптивная видимость дополнительных инструментов правой панели:
        # если зона достаточно высока, чтобы вместить полный тулбар — раскрываем.
        if hasattr(self.right_toolbar, "_set_more_tools_visible"):
            try:
                self.right_toolbar._set_more_tools_visible(True)
                h_right_full = float(self.right_toolbar.sizeHint().height())
                self.right_toolbar._set_more_tools_visible(False)
                h_right_compact = float(self.right_toolbar.sizeHint().height())
                zone_h = r.height()
                auto_expand_right = zone_h >= h_right_full + 20 or r.height() == 0
                user_expanded_tools = getattr(self.right_toolbar, "_user_expanded_tools", None)
                if user_expanded_tools is not None:
                    self.right_toolbar._set_more_tools_visible(user_expanded_tools)
                else:
                    self.right_toolbar._set_more_tools_visible(auto_expand_right)
                h_right = float(self.right_toolbar.sizeHint().height())
                w_right = float(self.right_toolbar.sizeHint().width())
            except (TypeError, ValueError, AttributeError):
                w_right, h_right = 40.0, 300.0
        else:
            try:
                w_right = float(self.right_toolbar.sizeHint().width())
                h_right = float(self.right_toolbar.sizeHint().height())
            except (TypeError, ValueError, AttributeError):
                w_right, h_right = 40.0, 300.0

        # Адаптивная видимость дополнительных действий нижней панели:
        # если зона достаточно широка, чтобы вместить полную нижнюю панель — раскрываем.
        if hasattr(self.bottom_toolbar, "_set_more_actions_visible"):
            try:
                self.bottom_toolbar._set_more_actions_visible(True)
                w_bot_full = float(self.bottom_toolbar.sizeHint().width())
                self.bottom_toolbar._set_more_actions_visible(False)
                w_bot_compact = float(self.bottom_toolbar.sizeHint().width())
                zone_w = r.width()
                auto_expand_bottom = zone_w >= w_bot_full + 20 or r.width() == 0
                user_expanded_actions = getattr(self.bottom_toolbar, "_user_expanded_actions", None)
                if user_expanded_actions is not None:
                    self.bottom_toolbar._set_more_actions_visible(user_expanded_actions)
                else:
                    self.bottom_toolbar._set_more_actions_visible(auto_expand_bottom)
                w_bot = float(self.bottom_toolbar.sizeHint().width())
                h_bot = float(self.bottom_toolbar.sizeHint().height())
            except (TypeError, ValueError, AttributeError):
                w_bot, h_bot = 300.0, 40.0
        else:
            try:
                w_bot = float(self.bottom_toolbar.sizeHint().width())
                h_bot = float(self.bottom_toolbar.sizeHint().height())
            except (TypeError, ValueError, AttributeError):
                w_bot, h_bot = 300.0, 40.0

        # Правая панель инструментов: по возможности снаружи справа, иначе снаружи слева, иначе внутри
        rx = r.right() + 6
        if rx + w_right > scr_w:
            if r.left() - w_right - 6 >= 0:
                rx = r.left() - w_right - 6
            else:
                rx = r.right() - w_right - 6
        ry = r.top()
        if ry + h_right > scr_h:
            ry = scr_h - h_right - 6
        if ry < 6:
            ry = 6
        self.right_toolbar.move(int(rx), int(ry))

        # Нижняя панель действий: по возможности снаружи снизу, иначе снаружи сверху, иначе внутри
        bx = r.right() - w_bot
        if bx + w_bot > scr_w:
            bx = scr_w - w_bot - 6
        if bx < 6:
            bx = 6
        by = r.bottom() + 8
        if by + h_bot > scr_h:
            if r.top() - h_bot - 8 >= 0:
                by = r.top() - h_bot - 8
            else:
                by = r.bottom() - h_bot - 8
        if by < 6:
            by = 6
        self.bottom_toolbar.move(int(bx), int(by))

        # Бейдж размеров
        valid_items = self.get_valid_region_items()
        valid_regions = [region for _, region in valid_items]
        region_info = f"(#{self.active_region_idx + 1}/{len(valid_regions)})" if len(valid_regions) > 1 else ""
        try:
            self.badge.update_dimension(r.width(), r.height(), region_info)
        except Exception:
            pass
        try:
            badge_h = float(self.badge.height())
        except (TypeError, ValueError, AttributeError):
            badge_h = 24.0
        badge_x = r.left()
        badge_y = r.top() - badge_h - 6
        if badge_y < 4:
            badge_y = r.top() + 6
        try:
            self.badge.move(int(badge_x), int(badge_y))
        except Exception:
            pass

        # Верхняя панель добавления и массовых действий не привязана к одной зоне.
        if hasattr(self, "region_header"):
            self._sync_region_header()
            if hasattr(self.region_header, "isVisible") and self.region_header.isVisible():
                try:
                    header_w = float(self.region_header.sizeHint().width())
                except (TypeError, ValueError, AttributeError):
                    header_w = 400.0
                header_x = max(6.0, min((scr_w - header_w) / 2.0, scr_w - header_w - 6.0))
                try:
                    self.region_header.move(int(header_x), 12)
                except Exception:
                    pass

        # HUD записи
        recording_hud = self.__dict__.get("recording_hud")
        if recording_hud:
            hud_x = r.left()
            hud_y = r.top() - recording_hud.height() - 8
            if hud_y < 4:
                hud_y = r.top() + 8
            recording_hud.move(int(hud_x), int(hud_y))

    def _get_tool_cursor(self) -> QCursor:
        """Возвращает специализированный курсор для текущего активного инструмента."""
        if self.current_tool == ToolType.TEXT:
            return QCursor(Qt.CursorShape.IBeamCursor)
        elif self.current_tool == ToolType.MOVE:
            return QCursor(Qt.CursorShape.OpenHandCursor)
        elif self.current_tool == ToolType.SELECT:
            return QCursor(Qt.CursorShape.ArrowCursor)
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

    def _region_index_at(self, pos: QPointF) -> int | None:
        """Возвращает зону под курсором, отдавая приоритет активной и верхней зоне."""
        if 0 <= getattr(self, "active_region_idx", -1) < len(self.regions):
            active = self.regions[self.active_region_idx]
            if active.isValid() and active.contains(pos):
                return self.active_region_idx
        for idx in range(len(self.regions) - 1, -1, -1):
            region = self.regions[idx]
            if region.isValid() and region.contains(pos):
                return idx
        return None

    # --- Обработка мыши ---
    def mousePressEvent(self, event):
        release_mouse_traps()
        pos = event.position()

        # Режим «Пипетка»: выбор цвета кликом ЛКМ или отмена кликом ПКМ
        if getattr(self, "is_eyedropper_active", False):
            if event.button() == Qt.MouseButton.LeftButton:
                color = self._sample_screen_color(pos)
                self.is_eyedropper_active = False
                cb = getattr(self, "eyedropper_callback", None)
                self.eyedropper_callback = None
                if cb:
                    cb(color)
                else:
                    self.right_toolbar.set_tool_color(color.name())
                self._set_cursor_if_needed(self._get_tool_cursor())
                self.update()
                return
            elif event.button() == Qt.MouseButton.RightButton:
                self.is_eyedropper_active = False
                self.eyedropper_callback = None
                self._set_cursor_if_needed(self._get_tool_cursor())
                self.update()
                return

        # Автоматическое сохранение текста при клике мимо редактора
        if self.text_editor.isVisible() and not self.text_editor.geometry().contains(pos.toPoint()):
            self._commit_text()

        # Кнопка в плашке объекта растягивает выбранный объект на текущую
        # зону. Проверяем её раньше общего hit-test, чтобы клик не стал
        # началом перемещения фигуры.
        if (
            event.button() == Qt.MouseButton.LeftButton
            and getattr(self, "interactive_badge_expand_rect", QRectF()).contains(pos)
            and (
                self._active_interactive_shape() is not None
                or len(self._selected_interactive_shapes()) > 1
            )
        ):
            self._expand_active_interactive_object_to_region()
            return

        # Обработка правой кнопки мыши: трансформация фигур, перемещение зоны ПКМ или контекстное меню
        if event.button() == Qt.MouseButton.RightButton:
            # Сначала проверяем клик ПКМ по номерному бейджу любой зоны:
            # гарантированно начинает перемещение этой зоны с любым инструментом!
            for idx in range(len(self.regions)):
                if self._region_index_badge_rect(idx).contains(pos):
                    self.set_active_region(idx)
                    if not self._active_region_is_locked():
                        if self.transform_box.is_active():
                            self.transform_box.set_shape(None)
                        self._begin_region_move(pos)
                        return

            if self.selection_rect.isValid() and self.selection_rect.contains(pos):
                # Для маски ПКМ открывает меню сразу: удаление не должно
                # зависеть от таймера удержания и не должно запускать drag.
                mask = self._capture_mask_at(pos)
                if mask is not None:
                    self.transform_box.set_shape(mask)
                    self._show_capture_mask_context_menu(mask, event.globalPosition().toPoint())
                    return

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
                        self._sync_toolbar_to_text_shape(shape)
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

                # 3. Клик ПКМ на свободном месте внутри зоны: с любым инструментом
                # (карандаш, прямоугольник и т.д.) начинает перемещение зоны!
                if not self._active_region_is_locked():
                    if self.transform_box.is_active():
                        self.transform_box.set_shape(None)
                    self._begin_region_move(pos)
                    return

            # Клик ПКМ по любой другой зоне: переключает на неё и начинает перемещение!
            for idx, reg in enumerate(self.regions):
                if idx != self.active_region_idx and reg.contains(pos):
                    self.set_active_region(idx)
                    if not self._active_region_is_locked():
                        if self.transform_box.is_active():
                            self.transform_box.set_shape(None)
                        self._begin_region_move(pos)
                        return

            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        valid_items = self.get_valid_region_items()
        valid_regions = [region for _, region in valid_items]

        # Клик ЛКМ по номерному бейджу любой зоны начинает её перемещение с любым инструментом!
        for idx in range(len(self.regions)):
            if self._region_index_badge_rect(idx).contains(pos):
                self.set_active_region(idx)
                if not self._active_region_is_locked():
                    self._begin_region_move(pos)
                    return

        # Если рамок ещё нет — начинаем первое выделение
        if not valid_regions:
            self.clear_regions()
            self.region_selection_history_before = self._capture_region_layout_state()
            self.is_selecting = True
            self.drag_start_pos = pos
            self.selection_rect = QRectF(pos, pos)
            self._hide_toolbars()
            self.update()
            return

        ctrl_held = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)

        # Клик по существующей зоне всегда означает работу с ней, даже если
        # включён режим добавления. Это позволяет перемещать старые зоны,
        # не создавая третью рамку поверх номера или центра выделения.
        hit_region_idx = self._region_index_at(pos)
        if hit_region_idx is not None:
            if hit_region_idx != self.active_region_idx:
                self.set_active_region(hit_region_idx)

        # Если активна рамка трансформации фигуры (или группы фигур) — проверяем ЛКМ по маркерам
        if self.transform_box.is_active():
            h = self.transform_box.hit_test_handle(pos)
            if h != HandleType.NONE:
                self.is_transforming = True
                self.transform_box.start_drag(h, pos)
                self.shape_drag_initial_pos = pos
                self._set_cursor_if_needed(self.transform_box.get_cursor_for_handle(h))
                self.update()
                return

        # Отдельный инструмент выделения объектов: клик по объекту выбирает
        # его, drag по пустому месту рисует рамку группового выделения.
        if self.current_tool == ToolType.SELECT:
            if self.selection_rect.contains(pos):
                self._begin_object_selection(pos)
                return
            else:
                self._clear_shape_selection()
                self.update()
                return

        # Если рамка есть — проверяем манипуляторы изменения размера активной зоны
        handle = self._hit_test_handles(pos)
        if not self._active_region_is_locked() and handle != HANDLE_NONE:
            self.is_resizing = True
            self.active_handle = handle
            self.drag_start_pos = pos
            self.initial_selection = QRectF(self.selection_rect)
            self._begin_region_transform_history()
            self._set_handle_cursor(self.active_handle)
            try:
                self.grabMouse()
            except Exception:
                pass
            return

        # Клик по другой свободной зоне одновременно переключает фокус и
        # начинает её перемещение. Раньше здесь выполнялся только выбор зоны,
        # поэтому следующий MouseMove уже не имел состояния drag.
        recording_indices = (
            set(getattr(self, "recording_region_indices", set()))
            if getattr(self, "is_recording", False)
            else set()
        )
        for idx, reg in enumerate(self.regions):
            if (
                idx != self.active_region_idx
                and idx not in recording_indices
                and reg.contains(pos)
            ):
                self.set_active_region(idx)
                if self.current_tool == ToolType.MOVE and not self._active_region_is_locked():
                    self._begin_region_move(pos)
                    return
                # В режиме рисования — переключились на зону, продолжаем обработку
                # (не делаем return, чтобы сразу начать рисование)
                break

        # Инструмент MOVE при клике внутри активной зоны
        if self.current_tool == ToolType.MOVE and self.selection_rect.contains(pos):
            mask = self._capture_mask_at(pos)
            if mask is not None:
                self.transform_box.set_shape(mask)
                self.is_transforming = True
                self.transform_box.start_drag(HandleType.INSIDE, pos)
                self.shape_drag_initial_pos = pos
                self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                self.update()
                return

            # Если есть выделенная группа (multi-select) — любой клик ЛКМ
            # по любой точке внутри объединённого bbox группы запускает перемещение.
            multi = getattr(self, "selected_shapes", [])
            if len(multi) > 1:
                clicked = self._selected_shape_at(pos) or self._shape_at(pos)
                # Вычисляем объединённый ограничивающий прямоугольник группы
                union_bbox = None
                for shape in multi:
                    try:
                        br = shape.get_bounding_rect()
                    except Exception:
                        br = None
                    if br is not None:
                        union_bbox = br if union_bbox is None else union_bbox.united(br)
                in_bbox = (union_bbox is not None and union_bbox.contains(pos))
                if (clicked is not None and clicked in multi) or in_bbox:
                    self.is_moving_objects = True
                    self.object_selection_start = QPointF(pos)
                    self.object_drag_initial_states = [
                        (shape, shape.clone()) for shape in multi
                    ]
                    self.object_drag_moved = False
                    self.object_drag_started = False
                    try:
                        self.grabMouse()
                    except Exception:
                        pass
                    self.update()
                    return

            # Проверяем клик по фигуре для выделения в рамку трансформации
            for shape in reversed(self.layer_manager.shapes):
                if shape.visible and shape.hit_test_rotated(pos):
                    self.transform_box.set_shape(shape)
                    self.last_active_shape = shape
                    self._sync_toolbar_to_text_shape(shape)
                    if isinstance(shape, TextShape):
                        self.right_toolbar.select_tool(ToolType.TEXT, show_options=True)
                    self.is_transforming = True
                    self.transform_box.start_drag(HandleType.INSIDE, pos)
                    self.shape_drag_initial_pos = pos
                    self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                    self.update()
                    return
            # Если клик по фону рамки — перемещение всей рамки выделения
            if not self._active_region_is_locked():
                if self.transform_box.is_active():
                    self.transform_box.set_shape(None)
                    self.update()
                self._begin_region_move(pos)
                return

        # Рисование фигур внутри активной зоны
        if self.selection_rect.contains(pos) and self.current_tool != ToolType.MOVE:
            mask = self._capture_mask_at(pos)
            if mask is not None:
                # В режиме capture_mask — ЛКМ всегда начинает НОВУЮ маску,
                # перемещение существующей — только ПКМ (уже реализовано выше).
                if self.current_tool == ToolType.CAPTURE_MASK:
                    if self.transform_box.is_active():
                        self.transform_box.set_shape(None)
                    self._start_drawing_shape(pos)
                    return
                self.transform_box.set_shape(mask)
                self.is_transforming = True
                self.transform_box.start_drag(HandleType.INSIDE, pos)
                self.shape_drag_initial_pos = pos
                self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                self.update()
                return

            # В режиме рисования (PEN, RECT, SHAPES, MOSAIC и т.д.)
            # ЛКМ по фигуре начинает рисование поверх, а НЕ перемещение.
            # Перемещение фигур доступно только через ПКМ или инструмент MOVE/SELECT.
            # Исключение: текстовые фигуры — клик по ним переключает в режим Text.
            if self.current_tool not in (ToolType.SELECT,):
                for shape in reversed(self.layer_manager.shapes):
                    if shape.visible and shape.hit_test_rotated(pos):
                        if isinstance(shape, TextShape):
                            # Клик по тексту всегда открывает редактор текста
                            self.transform_box.set_shape(shape)
                            self.last_active_shape = shape
                            self._sync_toolbar_to_text_shape(shape)
                            self.right_toolbar.select_tool(ToolType.TEXT, show_options=True)
                            self.is_transforming = True
                            self.transform_box.start_drag(HandleType.INSIDE, pos)
                            self.shape_drag_initial_pos = pos
                            self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                            self.update()
                            return
                        # Для не-текстовых фигур в режиме рисования — начинаем рисование
                        break  # Есть фигура под курсором, но просто рисуем поверх

            if self.transform_box.is_active() or getattr(self, "selected_shapes", None):
                self._clear_shape_selection()
                self.update()
            self._start_drawing_shape(pos)
            return


        # Во время записи одиночной зоны — никаких новых зон и перевыделения.
        if getattr(self, "is_recording", False) and not getattr(self, "_mass_recording", False):
            return

        # Новая зона создаётся только явным режимом «+» или удержанием Ctrl.
        # Обычный drag снаружи существующих зон ничего не сбрасывает.
        if self.is_adding_region or ctrl_held:
            self.region_selection_history_before = self._capture_region_layout_state()
            self.is_selecting = True
            self.drag_start_pos = pos
            self.regions.append(QRectF(pos, pos))
            self.active_region_idx = len(self.regions) - 1
            self.pending_outside_region_idx = None
            self._hide_toolbars()
            self.update()
            return

        # В режиме рисования клик вне всех зон не должен рисовать фигуры в пустоте.
        if self.current_tool not in (ToolType.MOVE, ToolType.SELECT):
            if self.transform_box.is_active() or getattr(self, "selected_shapes", None):
                self._clear_shape_selection()
                self.update()
            return

        # В обычном режиме MOVE / SELECT внешний drag перевыделяет активную зону.
        if valid_regions:
            self.pending_outside_drag = True
            self.pending_outside_pos = QPointF(pos)
            self.pending_outside_region_idx = self.active_region_idx
            return

    def mouseMoveEvent(self, event):
        pos = event.position()

        # A recording window can temporarily cover the overlay while its
        # native mask is rebuilt. If Windows delivers the mouse-up to that
        # window, stop the ordinary-zone resize on the first subsequent move
        # instead of dragging the zone forever.
        event_buttons = getattr(event, "buttons", None)
        left_is_down = bool(event_buttons() & Qt.MouseButton.LeftButton) if callable(event_buttons) else True
        buttons_down = bool(event_buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)) if callable(event_buttons) else True
        if self.is_resizing and not buttons_down:
            self._finish_region_resize(pos)
            return

        # Не оставляем захваченное выделение в режиме drag, если система
        # доставила отпускание кнопки другому окну.
        if not left_is_down:
            if self.is_selecting_objects:
                self._finish_object_selection()
                return
            if self.is_moving_objects:
                self._finish_object_move()
                return

        # Режим «Пипетка»: перемещение лупы вместе с курсором
        if getattr(self, "is_eyedropper_active", False):
            self.eyedropper_pos = pos
            self.update()
            return

        if (
            getattr(self, "interactive_badge_expand_rect", QRectF()).contains(pos)
            and self._active_interactive_shape() is not None
        ):
            self._set_cursor_if_needed(Qt.CursorShape.PointingHandCursor)
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

        if self.is_selecting_objects:
            self._update_object_selection(pos)
            self.update()
            return

        if self.is_moving_objects:
            self._update_object_move(pos)
            self.update()
            return

        # 2. Первичное выделение (гладкое 60+ FPS без лишних вызовов)
        if getattr(self, "pending_outside_drag", False):
            start = self.pending_outside_pos
            dx = pos.x() - start.x()
            dy = pos.y() - start.y()
            if (dx * dx + dy * dy) <= 36:
                return

            self.pending_outside_drag = False
            replace_idx = getattr(self, "pending_outside_region_idx", None)
            self.pending_outside_region_idx = None
            replacement = QRectF(start, pos).normalized()
            if replace_idx is None or not (0 <= replace_idx < len(self.regions)):
                replace_idx = 0
            if len(self.regions) > 1:
                self.capture_masks.pop(replace_idx, None)
                self.region_filters.pop(replace_idx, None)
                self.region_filter_params.pop(replace_idx, None)
                self.regions[replace_idx] = replacement
                self.active_region_idx = replace_idx
            else:
                self.capture_masks.clear()
                self.region_filters.clear()
                self.region_filter_params.clear()
                self.regions = [replacement]
                self.active_region_idx = 0
            self.layer_manager.clear()
            self.history_manager.clear()
            self._invalidate_layers_cache()
            self.clearMask()
            self.is_passthrough = False
            if hasattr(self, "bottom_toolbar"):
                self.bottom_toolbar.chk_passthrough.setChecked(False)
            if hasattr(self, "right_toolbar") and hasattr(self.right_toolbar, "chk_passthrough"):
                self.right_toolbar.chk_passthrough.setChecked(False)
            self.is_selecting = True
            self.drag_start_pos = start
            self.selection_rect = QRectF(start, pos).normalized()
            self._hide_toolbars()

        if self.is_selecting:
            self.selection_rect = QRectF(self.drag_start_pos, pos).normalized()
            self.badge.show()
            valid_count = len(self.get_valid_regions())
            info = f"(#{self.active_region_idx + 1}/{valid_count})" if valid_count > 1 else ""
            self.badge.update_dimension(self.selection_rect.width(), self.selection_rect.height(), info)
            self.badge.move(int(self.selection_rect.left()), int(max(4, self.selection_rect.top() - 26)))
            self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)
            self.update()
            return

        # 3. Перемещение или изменение размера рамки
        if self.is_resizing and not self._active_region_is_locked():
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
            shift_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._update_drawing_shape(pos, shift_pressed=shift_pressed)
            self._set_cursor_if_needed(self._get_tool_cursor())
            self.update()
            return

        # 5. Обновление курсора при наведении (с учетом выбранного инструмента)
        valid_regions = self.get_valid_regions()
        if valid_regions:
            # Наведение на номерной бейдж любой зоны показывает курсор перемещения
            if any(self._region_index_badge_rect(idx).contains(pos) for idx in range(len(self.regions))):
                self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                return

            if self.transform_box.is_active():
                h = self.transform_box.hit_test_handle(pos)
                if h != HandleType.NONE:
                    self._set_cursor_if_needed(self.transform_box.get_cursor_for_handle(h))
                    return
            handle = self._hit_test_handles(pos)
            if not self._active_region_is_locked() and handle != HANDLE_NONE:
                self._set_handle_cursor(handle)
            elif self.selection_rect.contains(pos):
                self._set_cursor_if_needed(self._get_tool_cursor())
            elif any(r.contains(pos) for r in valid_regions):
                self._set_cursor_if_needed(Qt.CursorShape.PointingHandCursor)
            else:
                self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)
        else:
            # Выбор окна выполняется только по явному двойному клику.
            # Простое движение мыши не рисует контур и не меняет состояние.
            self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)

    def _window_at_global_point(self, gpos: QPoint):
        """Возвращает (HWND, локальный QRectF, заголовок) под точкой."""
        point = POINT(int(gpos.x()), int(gpos.y()))
        overlay_hwnd = int(self.winId())
        raw_hwnd = user32.WindowFromPoint(point)
        root_hwnd = user32.GetAncestor(raw_hwnd, 2) if raw_hwnd else 0
        root_hwnd = root_hwnd or raw_hwnd

        # Overlay topmost, поэтому WindowFromPoint видит его самого. Идём
        # вниз по z-порядку через GW_HWNDNEXT и ищем первое окно под точкой.
        if root_hwnd and int(root_hwnd) == overlay_hwnd:
            candidate = user32.GetWindow(overlay_hwnd, 2)  # GW_HWNDNEXT
            root_hwnd = 0
            while candidate:
                candidate_root = user32.GetAncestor(candidate, 2) or candidate
                if int(candidate_root) != overlay_hwnd and user32.IsWindowVisible(candidate_root):
                    candidate_rect = wintypes.RECT()
                    if user32.GetWindowRect(candidate_root, ctypes.byref(candidate_rect)):
                        inside = (
                            candidate_rect.left <= point.x < candidate_rect.right
                            and candidate_rect.top <= point.y < candidate_rect.bottom
                        )
                        if inside:
                            root_hwnd = candidate_root
                            break
                candidate = user32.GetWindow(candidate, 2)  # следующее окно ниже

        if not root_hwnd or int(root_hwnd) == overlay_hwnd or not user32.IsWindowVisible(root_hwnd):
            return None

        window_rect = wintypes.RECT()
        if not user32.GetWindowRect(root_hwnd, ctypes.byref(window_rect)):
            return None

        title_buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(root_hwnd, title_buf, 512)
        title = title_buf.value.strip()

        # DWM возвращает истинные границы без невидимых теней Windows 10/11
        extended_rect = wintypes.RECT()
        hr = -1
        try:
            dwmapi = getattr(ctypes.windll, "dwmapi", None)
            if dwmapi is not None:
                hr = dwmapi.DwmGetWindowAttribute(
                    root_hwnd,
                    9,  # DWMWA_EXTENDED_FRAME_BOUNDS
                    ctypes.byref(extended_rect),
                    ctypes.sizeof(extended_rect)
                )
        except Exception:
            hr = -1
        final_rect = extended_rect if hr == 0 else window_rect

        geom = self.geometry()
        local_rect = QRectF(
            float(final_rect.left - geom.x()),
            float(final_rect.top - geom.y()),
            float(max(20, final_rect.right - final_rect.left)),
            float(max(20, final_rect.bottom - final_rect.top)),
        )
        return int(root_hwnd), local_rect, title

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

        # Отпускание правой кнопки мыши (завершает перемещение зоны, если оно было начато ПКМ)
        if event.button() == Qt.MouseButton.RightButton:
            self.right_hold_timer.stop()
            if self.is_resizing:
                self._finish_region_resize(pos)
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        if self.is_selecting_objects:
            self._finish_object_selection()
            event.accept()
            return

        if self.is_moving_objects:
            self._finish_object_move()
            event.accept()
            return

        if self.temp_shape is not None:
            self._finish_drawing_shape()
            self.update()
            event.accept()
            return

        if getattr(self, "pending_outside_drag", False):
            self.pending_outside_drag = False
            self.pending_outside_region_idx = None
            return

        if self.is_selecting:
            self.is_selecting = False
            self.selection_rect = self.selection_rect.normalized()
            add_mode_was_active = bool(self.is_adding_region)

            if self.selection_rect.width() > 20 and self.selection_rect.height() > 20:
                self._commit_region_selection_history()
                if add_mode_was_active:
                    self._set_add_region_mode(False)
                # В стиле Lightshot: сразу после выделения выбирается инструмент перемещения!
                self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)
                self._show_toolbars()
                self._update_toolbar_positions()
                self._sync_close_button_tooltip()
                self._sync_active_region_filter_ui()
                self._set_cursor_if_needed(
                    Qt.CursorShape.CrossCursor if self.is_adding_region else Qt.CursorShape.OpenHandCursor
                )

                # Если запуск был инициирован для конкретного режима записи (видео или GIF), сразу открываем настройки!
                if getattr(self, "preselected_recording_mode", None) == "video":
                    self.bottom_toolbar._show_video_popup()
                    self.preselected_recording_mode = None
                elif getattr(self, "preselected_recording_mode", None) == "gif":
                    self.bottom_toolbar._show_gif_popup()
                    self.preselected_recording_mode = None
            else:
                self.region_selection_history_before = None
                if len(self.regions) > 1 and 0 <= self.active_region_idx < len(self.regions):
                    self.regions.pop(self.active_region_idx)
                    self.region_filters.pop(self.active_region_idx, None)
                    self.region_filter_params.pop(self.active_region_idx, None)
                    self.active_region_idx = max(0, len(self.regions) - 1)
                    self._show_toolbars()
                    self._update_toolbar_positions()
                    self._sync_close_button_tooltip()
                    self._sync_active_region_filter_ui()
                else:
                    self.clear_regions()
                    self.clearMask()
                    self.is_passthrough = False
                    if hasattr(self, "bottom_toolbar"):
                        self.bottom_toolbar.chk_passthrough.setChecked(False)
                    if hasattr(self, "right_toolbar") and hasattr(self.right_toolbar, "chk_passthrough"):
                        self.right_toolbar.chk_passthrough.setChecked(False)
                    self._hide_toolbars()
                    self.badge.hide()
            self.update()
            return

        if self.is_resizing:
            self._finish_region_resize(pos)
            return

    def _finish_region_resize(self, pos: QPointF | None = None):
        """Finish a zone resize even when the native overlay missed mouse-up."""
        self._commit_region_transform_history()
        self.is_resizing = False
        self.active_handle = HANDLE_NONE
        self.selection_rect = self.selection_rect.normalized()
        try:
            self.releaseMouse()
        except Exception:
            pass
        self._update_toolbar_positions()
        self._sync_close_button_tooltip()
        if getattr(self, "is_passthrough", False):
            self._sync_passthrough_mask()
        if pos is not None and self.selection_rect.contains(pos):
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.current_tool == ToolType.MOVE else Qt.CursorShape.CrossCursor)
        self.update()

        if self.temp_shape is not None:
            self._finish_drawing_shape()
            self.update()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # В начале захвата двойной клик по любой части окна выбирает всё
            # окно. Этот обработчик расположен рядом с обработкой фигур, чтобы
            # не переопределять существующее двойное нажатие по аннотациям.
            if not self.get_valid_regions():
                match = self._window_at_global_point(
                    event.globalPosition().toPoint(),
                )
                if match is not None:
                    hwnd, local_rect, _title = match
                    self.clear_regions()
                    self.is_selecting = False
                    self.pending_outside_drag = False
                    self.pending_outside_region_idx = None
                    self.selection_rect = QRectF(local_rect)
                    self.initial_selection = QRectF(local_rect)
                    self.regions = [QRectF(local_rect)]
                    self.active_region_idx = 0
                    self.target_hwnd = hwnd
                    self._set_add_region_mode(False)
                    self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)
                    self._show_toolbars()
                    self._update_toolbar_positions()
                    self._sync_close_button_tooltip()
                    self.update()
                    event.accept()
                    return

            pos = event.position()
            for shape in reversed(self.layer_manager.shapes):
                if shape.visible and shape.hit_test_rotated(pos):
                    if isinstance(shape, TextShape):
                        self._edit_existing_text(shape)
                        return
                    else:
                        self._open_shape_properties_editor(shape, event.globalPosition().toPoint())
                        return
        super().mouseDoubleClickEvent(event)

    def _on_right_hold_timeout(self):
        if self.right_clicked_shape is not None:
            shape = self.right_clicked_shape
            pos = self.right_press_global_pos or QCursor.pos()
            self.dragged_shape = None
            self.right_clicked_shape = None
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.current_tool == ToolType.MOVE else Qt.CursorShape.CrossCursor)
            self._show_shape_context_menu(shape, pos)

    def _show_capture_mask_context_menu(self, mask: CaptureMaskShape, global_pos: QPoint):
        """Контекстное меню отдельного контура маски области записи."""
        theme = get_theme_styles()
        menu = QMenu(self)
        menu.setStyleSheet(get_context_menu_style(theme["is_dark"]))
        fit_action = menu.addAction(
            tr("capture_mask_fit_region", "Установить область по размеру маски"),
        )
        menu.addSeparator()
        delete_action = menu.addAction(
            create_themed_icon("trash", theme["is_dark"], 14),
            tr("shape_menu_delete", "Удалить"),
        )
        selected_action = menu.exec(global_pos)
        if selected_action == fit_action:
            self._fit_region_to_capture_masks()
        elif selected_action == delete_action:
            self._delete_capture_mask(mask)

    def _show_shape_context_menu(self, shape: BaseShape, global_pos: QPoint):
        if getattr(shape, "is_capture_mask", False):
            self._show_capture_mask_context_menu(shape, global_pos)
            return
        if shape not in self.layer_manager.shapes:
            return

        self.last_active_shape = shape
        self.active_editing_shape = shape
        self.update()
        theme = get_theme_styles()
        is_dark = theme["is_dark"]

        menu = QMenu(self)
        menu.setStyleSheet(get_context_menu_style(is_dark))

        # 0. Редактировать текст (для TextShape)
        act_text_edit = None
        if isinstance(shape, TextShape):
            act_text_edit = menu.addAction(create_themed_icon("edit", is_dark, 14), tr("action_edit_text", "Редактировать текст..."))
            act_edit = menu.addAction(create_themed_icon("settings", is_dark, 14), tr("shape_menu_text_props", "Параметры текста (шрифт, цвет)..."))
            menu.addSeparator()
        else:
            # 1. Изменить свойства
            act_edit = menu.addAction(create_themed_icon("settings", is_dark, 14), tr("shape_menu_props_clean", "Параметры фигуры..."))
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

        if action == act_text_edit:
            self.active_editing_shape = None
            self.update()
            self._edit_existing_text(shape)
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
        if getattr(self, "transform_box", None) is not None and self.transform_box.shape is shape:
            self.transform_box.set_shape(None)
        if self.last_active_shape is shape:
            self.last_active_shape = None
        if self.active_editing_shape is shape:
            self.active_editing_shape = None
        if self.right_clicked_shape is shape:
            self.right_clicked_shape = None

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

    def _selected_interactive_object(self):
        """Возвращает объект, который пользователь видит выбранным сейчас."""
        candidates = (
            getattr(getattr(self, "transform_box", None), "shape", None),
            getattr(self, "active_editing_shape", None),
            getattr(self, "last_active_shape", None),
            getattr(self, "right_clicked_shape", None),
        )
        for candidate in candidates:
            if candidate is None:
                continue
            if candidate in getattr(self.layer_manager, "shapes", []):
                return candidate
            if getattr(candidate, "is_capture_mask", False):
                return candidate
        return None

    def _shape_at(self, pos: QPointF):
        for shape in reversed(getattr(self.layer_manager, "shapes", [])):
            if not getattr(shape, "visible", True):
                continue
            if shape.hit_test_rotated(pos):
                return shape
            # У контурных прямоугольников/овалов пользователь обычно кликает
            # внутри объекта, а не точно по тонкой линии.
            if isinstance(shape, (RectangleShape, CircleShape, TextShape)) and self._shape_bounds(shape).contains(pos):
                return shape
        return None

    def _selected_shape_at(self, pos: QPointF):
        """Возвращает выбранную фигуру под курсором раньше остальных слоёв."""
        selected = [
            shape for shape in getattr(self, "selected_shapes", [])
            if shape in getattr(self.layer_manager, "shapes", [])
            and getattr(shape, "visible", True)
        ]
        for shape in reversed(selected):
            if shape.hit_test_rotated(pos, tolerance=12.0):
                return shape
            bounds = self._shape_bounds(shape)
            if bounds.isValid() and bounds.adjusted(-4, -4, 4, 4).contains(pos):
                return shape
        return None

    @staticmethod
    def _shape_bounds(shape: BaseShape) -> QRectF:
        try:
            return QRectF(shape.get_bounding_rect()).normalized()
        except Exception:
            return QRectF()

    def _clear_shape_selection(self):
        self.selected_shapes = []
        self.object_selection_rect = QRectF()
        self.is_selecting_objects = False
        self.is_moving_objects = False
        self.object_selection_start = QPointF()
        self.object_drag_initial_states = []
        self.object_drag_moved = False
        self.object_drag_started = False
        try:
            self.releaseMouse()
        except Exception:
            pass
        self.last_active_shape = None
        self.active_editing_shape = None
        if getattr(self, "transform_box", None) is not None:
            self.transform_box.set_shape(None)

    def _begin_object_selection(self, pos: QPointF):
        """Начинает выделение рамкой или перенос уже выбранной группы."""
        # При пересечении объектов верхний невыбранный слой не должен
        # перехватывать drag уже выделенной группы.
        clicked = self._selected_shape_at(pos) or self._shape_at(pos)
        # Для группы из нескольких объектов — проверяем попадание в объединённый bbox
        if len(self.selected_shapes) > 1:
            union_bbox = None
            for shape in self.selected_shapes:
                try:
                    br = shape.get_bounding_rect()
                except Exception:
                    br = None
                if br is not None:
                    union_bbox = br if union_bbox is None else union_bbox.united(br)
            in_bbox = (union_bbox is not None and union_bbox.contains(pos))
        else:
            in_bbox = False
        if (clicked is not None and clicked in self.selected_shapes) or in_bbox:
            self.is_moving_objects = True
            self.object_selection_start = QPointF(pos)
            self.object_drag_initial_states = [
                (shape, shape.clone()) for shape in self.selected_shapes
            ]
            self.object_drag_moved = False
            self.object_drag_started = False
            try:
                self.grabMouse()
            except Exception:
                pass
            return

        # Клик снаружи активной группы/фигуры: сбрасываем предыдущее выделение
        self.selected_shapes = []
        self.last_active_shape = None
        self.active_editing_shape = None
        if getattr(self, "transform_box", None) is not None:
            self.transform_box.set_shape(None)

        if clicked is not None:
            # Одиночный клик по фигуре: активируем для неё рамку трансформации
            self.selected_shapes = [clicked]
            self.last_active_shape = clicked
            self.active_editing_shape = clicked
            if getattr(self, "transform_box", None) is not None:
                self.transform_box.set_shape(clicked)
                self.is_transforming = True
                self.transform_box.start_drag(HandleType.INSIDE, pos)
                self.shape_drag_initial_pos = pos
                self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
            self.update()
            return

        self.is_selecting_objects = True
        self.object_selection_start = QPointF(pos)
        self.object_selection_rect = QRectF(pos, pos)
        try:
            self.grabMouse()
        except Exception:
            pass
        self.update()

    def _update_object_selection(self, pos: QPointF):
        self.object_selection_rect = QRectF(self.object_selection_start, pos).normalized()

    def _finish_object_selection(self):
        rect = self.object_selection_rect.normalized()
        if rect.width() <= 4 and rect.height() <= 4:
            shape = self._shape_at(self.object_selection_start)
            if shape is not None:
                self.selected_shapes = [shape]
                self.last_active_shape = shape
                self.active_editing_shape = shape
            else:
                self.selected_shapes = []
                self.last_active_shape = None
                self.active_editing_shape = None
        else:
            selected = [
                shape for shape in getattr(self.layer_manager, "shapes", [])
                if getattr(shape, "visible", True) and rect.intersects(self._shape_bounds(shape))
            ]
            self.selected_shapes = selected
            self.last_active_shape = self.selected_shapes[-1] if self.selected_shapes else None
            self.active_editing_shape = self.last_active_shape
        self.is_selecting_objects = False
        self.object_selection_rect = QRectF()
        try:
            self.releaseMouse()
        except Exception:
            pass
        if getattr(self, "transform_box", None) is not None:
            if len(self.selected_shapes) == 1:
                self.transform_box.set_shape(self.selected_shapes[0])
            elif len(self.selected_shapes) > 1:
                self.transform_box.set_shape(ShapeGroup(self.selected_shapes))
            else:
                self.transform_box.set_shape(None)
        self.update()

    def _update_object_move(self, pos: QPointF):
        """Перемещает группу от фактической точки нажатия плавно 1:1 с ограничением зоной."""
        raw_dx = pos.x() - self.object_selection_start.x()
        raw_dy = pos.y() - self.object_selection_start.y()
        drag_distance_sq = raw_dx * raw_dx + raw_dy * raw_dy
        drag_threshold = max(6, QApplication.startDragDistance())
        if not self.object_drag_started and drag_distance_sq < drag_threshold * drag_threshold:
            return
        self.object_drag_started = True
        dx, dy = raw_dx, raw_dy

        target_rect = getattr(self, "selection_rect", QRectF())
        if target_rect.isValid() and not target_rect.isEmpty() and self.object_drag_initial_states:
            union_rect = QRectF()
            for _, initial in self.object_drag_initial_states:
                b = self._shape_bounds(initial)
                if b.isValid() and not b.isEmpty():
                    union_rect = union_rect.united(b) if not union_rect.isNull() and union_rect.isValid() else QRectF(b)
            if union_rect.isValid() and not union_rect.isEmpty():
                min_dx = target_rect.left() - union_rect.left()
                max_dx = target_rect.right() - union_rect.right()
                min_dy = target_rect.top() - union_rect.top()
                max_dy = target_rect.bottom() - union_rect.bottom()
                if min_dx <= max_dx:
                    dx = max(min_dx, min(dx, max_dx))
                if min_dy <= max_dy:
                    dy = max(min_dy, min(dy, max_dy))

        for shape, initial in self.object_drag_initial_states:
            self._apply_shape_geometry(shape, initial)
            shape.translate(dx, dy)
        self.object_drag_moved = abs(dx) > 2 or abs(dy) > 2
        self._invalidate_layers_cache()

    def _finish_object_move(self):
        states = list(self.object_drag_initial_states)
        drag_started = self.object_drag_started
        self.is_moving_objects = False
        self.object_drag_initial_states = []
        self.object_selection_start = QPointF()
        self.object_drag_started = False
        try:
            self.releaseMouse()
        except Exception:
            pass
        if drag_started and self.object_drag_moved and states:
            new_states = [(shape, shape.clone()) for shape, _ in states]

            def apply_group(snapshot):
                for shape, state in snapshot:
                    self._apply_shape_geometry(shape, state)
                self._on_layers_changed()

            self.history_manager.push_already_done(HistoryCommand(
                tr("hist_cmd_move_group", "Перемещение группы объектов"),
                do_func=lambda snapshot=new_states: apply_group(snapshot),
                undo_func=lambda snapshot=states: apply_group(snapshot),
            ))
        self.object_drag_moved = False
        self.update()

    def _delete_selected_interactive_objects(self):
        selected = list(self.selected_shapes)
        self._clear_shape_selection()
        for shape in reversed(selected):
            self._delete_shape(shape)

    def _draw_object_selection_overlay(self, painter: QPainter):
        if self.is_recording and not self.is_selecting_objects:
            return
        if self.is_selecting_objects and not self.object_selection_rect.isNull():
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(96, 165, 250, 220), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(QColor(96, 165, 250, 35))
            painter.drawRect(self.object_selection_rect)
            painter.restore()

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
        # Make the complete perimeter interactive. The visible corner
        # squares are only visual affordances; users should be able to grab
        # any point along a side and still receive the correct resize cursor.
        hs = max(HANDLE_SIZE, 10)

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
        old_region = QRectF(r)

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
        new_region = self.selection_rect.normalized()

        if self.active_handle == HANDLE_MOVE:
            shift_x = dx
            shift_y = dy
            for shape, initial_shape in self.__dict__.get("region_transform_initial_shapes", []):
                self._apply_shape_geometry(shape, initial_shape)
                shape.translate(shift_x, shift_y)
                bg_pix = self.__dict__.get("background_pixmap", None)
                if hasattr(shape, "update_effect") and bg_pix is not None:
                    try:
                        shape.update_effect(bg_pix)
                    except Exception:
                        pass
            if hasattr(self, "_invalidate_layers_cache"):
                self._invalidate_layers_cache()

        masks = self._capture_masks_for_region()
        if masks and old_region.width() > 0 and old_region.height() > 0:
            for mask in masks:
                mask.scale_from_origin(
                    new_region.width() / old_region.width(),
                    new_region.height() / old_region.height(),
                    old_region.topLeft(),
                )
                mask.translate(new_region.left() - old_region.left(), new_region.top() - old_region.top())
        self._sync_recording_selection_region_geometry(self.active_region_idx, new_region)

    # --- Рисование фигур с учетом индивидуальных настроек инструмента ---
    def _start_drawing_shape(self, pos: QPointF):
        if getattr(self, "selected_shapes", None):
            self._clear_shape_selection()
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
        elif self.current_tool == ToolType.CAPTURE_MASK:
            kind = cfg.get("mask_kind", "freeform")
            self.temp_shape = CaptureMaskShape(kind=kind, rect=QRectF(pos, pos), color="#38BDF8")
            if kind == "freeform":
                self.temp_shape.add_point(pos)

        # Захватываем мышь у всех drag-инструментов. Иначе отпускание кнопки
        # за пределами окна/панели теряется, и стрелка или контур продолжают
        # рисоваться бесконечно.
        if self.temp_shape is not None:
            try:
                self.grabMouse()
            except Exception:
                pass

    def _update_drawing_shape(self, pos: QPointF, shift_pressed: bool = False):
        origin = getattr(self, "shape_origin_pos", None)
        if isinstance(self.temp_shape, PenShape):
            self.temp_shape.add_point(pos)
        elif isinstance(self.temp_shape, (LineShape, ArrowShape)):
            if shift_pressed and origin is not None:
                dx = pos.x() - origin.x()
                dy = pos.y() - origin.y()
                length = math.hypot(dx, dy)
                if length > 0:
                    angle = round(math.atan2(dy, dx) / (math.pi / 12.0)) * (math.pi / 12.0)
                    pos = QPointF(
                        origin.x() + math.cos(angle) * length,
                        origin.y() + math.sin(angle) * length,
                    )
            self.temp_shape.p2 = pos
        elif isinstance(self.temp_shape, (RectangleShape, CircleShape, RegionalEffectShape, MosaicShape, BlurShape)):
            if origin is None:
                origin = self.temp_shape.rect.topLeft()
            if shift_pressed:
                pos = self._constrain_square_endpoint(origin, pos)
            self.temp_shape.rect = QRectF(origin, pos).normalized()
        elif isinstance(self.temp_shape, (RegionalEffectShape, MosaicShape, BlurShape)):
            if origin is None:
                origin = self.temp_shape.rect.topLeft()
            self.temp_shape.rect = QRectF(origin, pos).normalized()
            if self.background_pixmap is not None:
                self.temp_shape.update_effect(self.background_pixmap)
        elif isinstance(self.temp_shape, CaptureMaskShape):
            if self.temp_shape.kind == "freeform":
                self.temp_shape.add_point(pos)
            else:
                if origin is None:
                    origin = self.temp_shape.rect.topLeft()
                if shift_pressed:
                    pos = self._constrain_square_endpoint(origin, pos)
                self.temp_shape.rect = QRectF(origin, pos).normalized()

    @staticmethod
    def _constrain_square_endpoint(origin: QPointF, pos: QPointF) -> QPointF:
        """Возвращает конечную точку квадратного drag от origin."""
        dx = pos.x() - origin.x()
        dy = pos.y() - origin.y()
        size = max(abs(dx), abs(dy))
        return QPointF(
            origin.x() + (size if dx >= 0 else -size),
            origin.y() + (size if dy >= 0 else -size),
        )

    def _finish_drawing_shape(self):
        self.shape_origin_pos = None
        shape = self.temp_shape
        self.temp_shape = None
        try:
            self.releaseMouse()
        except Exception:
            pass

        if shape is not None:
            if isinstance(shape, CaptureMaskShape):
                valid = (len(shape.points) >= 3) if shape.kind == "freeform" else not shape.rect.normalized().isEmpty()
                if valid and shape.get_bounding_rect().width() > 4 and shape.get_bounding_rect().height() > 4:
                    region_idx = self.active_region_idx
                    mask_index = len(self.capture_masks.setdefault(region_idx, []))
                    self.capture_masks[region_idx].append(shape)
                    self.transform_box.set_shape(shape)
                    self._invalidate_layers_cache()
                    history = getattr(self, "history_manager", None)
                    if history is not None:
                        cmd = HistoryCommand(
                            tr("hist_cmd_add", "Добавлен {name}", name=shape.name),
                            do_func=lambda i=region_idx, m=shape, n=mask_index: self._set_capture_mask_presence(i, m, True, n),
                            undo_func=lambda i=region_idx, m=shape, n=mask_index: self._set_capture_mask_presence(i, m, False, n),
                        )
                        history.push_already_done(cmd)
                self.update()
                return
            if isinstance(shape, (RegionalEffectShape, MosaicShape, BlurShape)) and self.background_pixmap is not None:
                shape.update_effect(self.background_pixmap)

            shape.region_idx = self.active_region_idx
            self.layer_manager.add_shape(shape)
            cmd = HistoryCommand(
                tr("hist_cmd_add", "Добавлен {name}", name=shape.name),
                do_func=lambda s=shape: self.layer_manager.add_shape(s),
                undo_func=lambda s=shape: self.layer_manager.remove_shape(s.id)
            )
            self.history_manager.push_already_done(cmd)

    def _on_tool_settings_updated(self):
        cfg = self.right_toolbar.tools_config.get(self.current_tool, {})
        col = self.right_toolbar.current_color

        # 1. Если сейчас открыт встроенный интерактивный текстовый редактор:
        if hasattr(self, "text_editor") and self.text_editor.isVisible():
            self.text_editor.update_style(
                font_family=cfg.get("font_family"),
                font_size=cfg.get("size"),
                color=col if col and col not in ("mosaic", "blur") else None,
                is_bold=cfg.get("is_bold"),
                is_italic=cfg.get("is_italic"),
                is_underline=cfg.get("is_underline"),
                has_bg=cfg.get("has_bg"),
                bg_color=cfg.get("bg_color"),
                bg_alpha=cfg.get("bg_alpha")
            )

        # При групповом выделении общие параметры применяются ко всем
        # объектам одного типа одним действием.
        selected = [
            shape for shape in getattr(self, "selected_shapes", [])
            if shape in getattr(self.layer_manager, "shapes", [])
        ]
        if len(selected) > 1 and len({type(shape) for shape in selected}) == 1:
            for shape in selected:
                if col and col not in ("mosaic", "blur") and hasattr(shape, "color"):
                    shape.color = col
                if "size" in cfg and hasattr(shape, "stroke_width"):
                    shape.stroke_width = cfg["size"]
                if hasattr(shape, "pixel_size") and "pixel_size" in cfg:
                    shape.pixel_size = cfg["pixel_size"]
                if hasattr(shape, "blur_radius") and "blur_radius" in cfg:
                    shape.blur_radius = cfg["blur_radius"]
            self._on_layers_changed()
            return

        # 2. Если на холсте выбрана текстовая фигура (в transform_box или last_active_shape):
        target_shape = None
        if hasattr(self, "transform_box") and self.transform_box.is_active():
            target_shape = self.transform_box.shape
        elif getattr(self, "last_active_shape", None):
            target_shape = self.last_active_shape

        if target_shape and isinstance(target_shape, TextShape) and target_shape in self.layer_manager.shapes:
            text_cfg = self.right_toolbar.tools_config.get(ToolType.TEXT, {})
            eff_cfg = {**text_cfg, **cfg}
            if "font_family" in eff_cfg:
                target_shape.font_family = eff_cfg["font_family"]
            if "size" in eff_cfg:
                target_shape.font_size = eff_cfg["size"]
            if "color" in eff_cfg and eff_cfg["color"] not in ("mosaic", "blur"):
                target_shape.color = eff_cfg["color"]
            elif col and col not in ("mosaic", "blur"):
                target_shape.color = col
            if "is_bold" in eff_cfg:
                target_shape.is_bold = eff_cfg["is_bold"]
            if "is_italic" in eff_cfg:
                target_shape.is_italic = eff_cfg["is_italic"]
            if "is_underline" in eff_cfg:
                target_shape.is_underline = eff_cfg["is_underline"]
            if "has_bg" in eff_cfg:
                target_shape.has_bg = eff_cfg["has_bg"]
            if "bg_color" in eff_cfg:
                target_shape.bg_color = eff_cfg["bg_color"]
            if "bg_alpha" in eff_cfg:
                target_shape.bg_alpha = eff_cfg["bg_alpha"]
            self._invalidate_layers_cache()

        self.update()

    def _sync_toolbar_to_text_shape(self, shape: TextShape):
        if not isinstance(shape, TextShape):
            return
        tcfg = self.right_toolbar.tools_config.setdefault(ToolType.TEXT, {})
        tcfg["font_family"] = shape.font_family
        tcfg["size"] = shape.font_size
        tcfg["color"] = shape.color
        tcfg["is_bold"] = shape.is_bold
        tcfg["is_italic"] = getattr(shape, "is_italic", False)
        tcfg["is_underline"] = shape.is_underline
        tcfg["has_bg"] = getattr(shape, "has_bg", False)
        tcfg["bg_color"] = getattr(shape, "bg_color", "#000000")
        tcfg["bg_alpha"] = getattr(shape, "bg_alpha", 180)
        self.right_toolbar._update_color_swatch()
        if hasattr(self.right_toolbar, "properties_flyout") and self.right_toolbar.properties_flyout.isVisible():
            self.right_toolbar.properties_flyout.load_tool(ToolType.TEXT, tcfg)

    def _on_editor_font_size_changed(self, new_size: int):
        tcfg = self.right_toolbar.tools_config.setdefault(ToolType.TEXT, {})
        tcfg["size"] = new_size
        if hasattr(self.right_toolbar, "properties_flyout") and self.right_toolbar.properties_flyout.isVisible():
            self.right_toolbar.properties_flyout.slider_size.blockSignals(True)
            self.right_toolbar.properties_flyout.slider_size.setValue(new_size)
            self.right_toolbar.properties_flyout.slider_size.blockSignals(False)
            self.right_toolbar.properties_flyout.lbl_size.setText(tr("prop_font_size", "Размер шрифта: {val} pt", val=new_size))

    def _on_editor_moved(self, new_pos: QPointF):
        self.text_editor_pos = new_pos

    def _cancel_text_editing(self):
        self.text_editor.clear()
        self.text_editor.hide()
        self.editing_existing_text_shape = None
        self.update()

    def _open_text_editor(self, pos: QPointF, color: str, font_size: int):
        if self.text_editor.isVisible() and self.text_editor.text().strip():
            self._commit_text()

        cfg = self.right_toolbar.get_current_settings()
        self.editing_existing_text_shape = None
        self.text_editor_pos = pos

        col = cfg.get("color", color)
        if not col or col in ("mosaic", "blur"):
            col = "#FF2E2E"

        self.text_editor.update_style(
            font_family=cfg.get("font_family", "Segoe UI"),
            font_size=cfg.get("size", font_size),
            color=col,
            is_bold=cfg.get("is_bold", True),
            is_italic=cfg.get("is_italic", False),
            is_underline=cfg.get("is_underline", False),
            has_bg=cfg.get("has_bg", False),
            bg_color=cfg.get("bg_color", "#000000"),
            bg_alpha=cfg.get("bg_alpha", 180)
        )
        self.text_editor.clear()
        self.text_editor.move(int(pos.x()), int(pos.y()))
        self.text_editor.show()
        self.text_editor.setFocus()

    def _edit_existing_text(self, shape: TextShape):
        """Открывает встроенный интерактивный текстовый редактор для редактирования существующей надписи."""
        if not shape:
            return
        if self.text_editor.isVisible() and self.text_editor.text().strip():
            self._commit_text()

        self.editing_existing_text_shape = shape
        self._sync_toolbar_to_text_shape(shape)
        br = shape.get_bounding_rect()
        editor_pos = QPointF(br.left() - 4, max(4.0, br.top() - 24))
        self.text_editor_pos = editor_pos

        self.text_editor.update_style(
            font_family=shape.font_family,
            font_size=shape.font_size,
            color=shape.color,
            is_bold=shape.is_bold,
            is_italic=getattr(shape, "is_italic", False),
            is_underline=shape.is_underline,
            has_bg=getattr(shape, "has_bg", False),
            bg_color=getattr(shape, "bg_color", "#000000"),
            bg_alpha=getattr(shape, "bg_alpha", 180)
        )
        self.text_editor.setText(shape.text)
        self.text_editor.selectAll()
        self.text_editor.move(int(editor_pos.x()), int(editor_pos.y()))
        self.text_editor.show()
        self.text_editor.setFocus()

    def _commit_text(self):
        existing_shape = getattr(self, "editing_existing_text_shape", None)
        if not self.text_editor.isVisible() and existing_shape is None:
            return
        text = self.text_editor.text().strip()
        f_fam = self.text_editor.font_family
        f_size = self.text_editor.font_size
        f_col = self.text_editor.text_color
        f_bold = self.text_editor.is_bold
        f_italic = self.text_editor.is_italic
        f_under = self.text_editor.is_underline
        f_has_bg = self.text_editor.has_bg
        f_bg_col = self.text_editor.bg_color
        f_bg_alpha = self.text_editor.bg_alpha

        self.text_editor.clear()
        self.text_editor.hide()
        self.editing_existing_text_shape = None

        # Запоминаем параметры шрифта для всех последующих надписей
        tcfg = self.right_toolbar.tools_config.setdefault(ToolType.TEXT, {})
        tcfg["font_family"] = f_fam
        tcfg["size"] = f_size
        tcfg["color"] = f_col
        tcfg["is_bold"] = f_bold
        tcfg["is_italic"] = f_italic
        tcfg["is_underline"] = f_under
        tcfg["has_bg"] = f_has_bg
        tcfg["bg_color"] = f_bg_col
        tcfg["bg_alpha"] = f_bg_alpha

        if existing_shape:
            if not text:
                cmd = HistoryCommand(
                    tr("hist_cmd_delete", "Удаление фигуры"),
                    do_func=lambda s=existing_shape: self.layer_manager.remove_shape(s.id),
                    undo_func=lambda s=existing_shape: self.layer_manager.add_shape(s)
                )
                self.layer_manager.remove_shape(existing_shape.id)
                self.history_manager.push_already_done(cmd)
            elif text != existing_shape.text or f_size != existing_shape.font_size or f_fam != existing_shape.font_family:
                old_text = existing_shape.text
                old_size = existing_shape.font_size
                old_fam = existing_shape.font_family
                cmd = HistoryCommand(
                    tr("hist_cmd_text", "Текст: '{text}'", text=text[:12]),
                    do_func=lambda s=existing_shape, t=text, sz=f_size, fm=f_fam: (setattr(s, "text", t), setattr(s, "font_size", sz), setattr(s, "font_family", fm)),
                    undo_func=lambda s=existing_shape, t=old_text, sz=old_size, fm=old_fam: (setattr(s, "text", t), setattr(s, "font_size", sz), setattr(s, "font_family", fm))
                )
                existing_shape.text = text
                existing_shape.font_size = f_size
                existing_shape.font_family = f_fam
                self.history_manager.push_already_done(cmd)
            self.last_active_shape = existing_shape
            self.transform_box.set_shape(existing_shape)
            self._sync_toolbar_to_text_shape(existing_shape)
            self._invalidate_layers_cache()
            self.update()
            return

        if text:
            from PyQt6.QtGui import QFontMetrics
            font = QFont(f_fam, int(f_size))
            font.setBold(f_bold)
            font.setItalic(f_italic)
            font.setUnderline(f_under)
            fm = QFontMetrics(font)
            # Точный baseline с учетом ascent шрифта и высоты заголовка виджета (24px)
            baseline_pos = self.text_editor_pos + QPointF(6, 24 + fm.ascent())

            shape = TextShape(
                baseline_pos,
                text,
                color=f_col,
                font_size=f_size,
                font_family=f_fam,
                is_bold=f_bold,
                is_italic=f_italic,
                is_underline=f_under,
                has_bg=f_has_bg,
                bg_color=f_bg_col,
                bg_alpha=f_bg_alpha
            )
            shape.region_idx = self.active_region_idx
            self.layer_manager.add_shape(shape)
            self.last_active_shape = shape
            self.transform_box.set_shape(shape)
            self._sync_toolbar_to_text_shape(shape)
            cmd = HistoryCommand(
                tr("hist_cmd_text", "Текст: '{text}'", text=text[:12]),
                do_func=lambda s=shape: self.layer_manager.add_shape(s),
                undo_func=lambda s=shape: self.layer_manager.remove_shape(s.id)
            )
            self.history_manager.push_already_done(cmd)
            self._invalidate_layers_cache()
            self.update()

    # --- Инструмент «Пипетка» (Eyedropper) ---
    def start_eyedropper(self, callback=None):
        """Активирует интерактивный режим пипетки для взятия цвета с экрана."""
        self.is_eyedropper_active = True
        self.eyedropper_callback = callback
        self.setCursor(create_tool_cursor("pipette", getattr(self, "is_dark", True)))
        self.eyedropper_pos = self.mapFromGlobal(QCursor.pos())
        self.update()

    def _sample_screen_color(self, pos: QPointF) -> QColor:
        """Считывает точный цвет пикселя с холста или экрана в указанных координатах."""
        px = int(pos.x())
        py = int(pos.y())
        if self.background_pixmap and not self.background_pixmap.isNull():
            img = self.background_pixmap.toImage()
            if 0 <= px < img.width() and 0 <= py < img.height():
                # Если поверх нарисованы векторные фигуры — рендерим пиксель с учетом слоев
                if self.layer_manager and self.layer_manager.shapes:
                    canvas = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
                    p = QPainter(canvas)
                    p.drawPixmap(0, 0, self.background_pixmap, px, py, 1, 1)
                    for s in self.layer_manager.shapes:
                        if getattr(s, "visible", True):
                            s.draw(p, offset=QPointF(px, py), source_pixmap=self.background_pixmap)
                    p.end()
                    return canvas.pixelColor(0, 0)
                return img.pixelColor(px, py)
        try:
            screen = QApplication.primaryScreen()
            if screen:
                pix = screen.grabWindow(0, px, py, 1, 1)
                return pix.toImage().pixelColor(0, 0)
        except Exception:
            pass
        return QColor(255, 0, 0)

    def _draw_eyedropper_loupe(self, painter: QPainter):
        """Отрисовывает экранную лупу 9x9 пикселей с HEX/RGB кодом цвета возле курсора пипетки."""
        pos = getattr(self, "eyedropper_pos", None)
        if pos is None:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        cur_x = pos.x()
        cur_y = pos.y()
        center_col = self._sample_screen_color(pos)

        loupe_radius = 44.0
        loupe_diam = loupe_radius * 2.0

        lx = cur_x + 24.0
        ly = cur_y - 50.0

        if lx + loupe_diam + 40 > self.width():
            lx = cur_x - loupe_diam - 24.0
        if ly - 20 < 0:
            ly = cur_y + 24.0

        center_loupe = QPointF(lx + loupe_radius, ly + loupe_radius)

        clip_path = QPainterPath()
        clip_path.addEllipse(center_loupe, loupe_radius, loupe_radius)
        painter.save()
        painter.setClipPath(clip_path)

        pixel_size = 10.0
        grid_half = 4
        start_x = center_loupe.x() - (grid_half + 0.5) * pixel_size
        start_y = center_loupe.y() - (grid_half + 0.5) * pixel_size

        for gx in range(-grid_half, grid_half + 1):
            for gy in range(-grid_half, grid_half + 1):
                sample_pt = QPointF(cur_x + gx, cur_y + gy)
                pix_col = self._sample_screen_color(sample_pt)
                cell_rect = QRectF(start_x + (gx + grid_half) * pixel_size,
                                   start_y + (gy + grid_half) * pixel_size,
                                   pixel_size, pixel_size)
                painter.fillRect(cell_rect, pix_col)
                painter.setPen(QColor(255, 255, 255, 30))
                painter.drawRect(cell_rect)

        center_cell = QRectF(center_loupe.x() - pixel_size / 2.0,
                             center_loupe.y() - pixel_size / 2.0,
                             pixel_size, pixel_size)
        painter.setPen(QPen(QColor(255, 255, 255, 220), 1.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(center_cell)
        painter.restore()

        painter.setPen(QPen(QColor(255, 255, 255), 2.5))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center_loupe, loupe_radius, loupe_radius)
        painter.setPen(QPen(QColor(15, 23, 42, 180), 1.0))
        painter.drawEllipse(center_loupe, loupe_radius + 1.5, loupe_radius + 1.5)

        badge_w = 140.0
        badge_h = 24.0
        badge_rect = QRectF(center_loupe.x() - badge_w / 2.0, center_loupe.y() + loupe_radius + 8.0, badge_w, badge_h)

        painter.setPen(QPen(QColor(56, 189, 248, 200), 1))
        painter.setBrush(QBrush(QColor(15, 23, 42, 235)))
        painter.drawRoundedRect(badge_rect, 4, 4)

        swatch_rect = QRectF(badge_rect.left() + 5, badge_rect.top() + 5, 14, 14)
        painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
        painter.setBrush(QBrush(center_col))
        painter.drawRoundedRect(swatch_rect, 2, 2)

        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(QColor(241, 245, 249))
        text_rect = QRectF(swatch_rect.right() + 6, badge_rect.top(), badge_w - 28, badge_h)
        hex_text = f"{center_col.name().upper()} ({center_col.red()},{center_col.green()},{center_col.blue()})"
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, hex_text)

        painter.restore()

    def _region_index_badge_rect(self, region_idx: int) -> QRectF:
        """Возвращает прямоугольник бейджа с номером зоны в координатах overlay."""
        if not hasattr(self, "regions") or not (0 <= region_idx < len(self.regions)):
            return QRectF()
        reg = self.regions[region_idx]
        if not isinstance(reg, QRectF) or not reg.isValid() or reg.isEmpty():
            return QRectF()
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        fm = QFontMetrics(font)
        text = f"#{region_idx + 1}"
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        bw = tw + 12
        bh = th + 2
        bx = reg.left() + 4
        by = reg.top() + 4
        return QRectF(bx - 2, by - 2, bw + 4, bh + 4)

    def _draw_region_index_badge(self, painter: QPainter, reg: QRectF, index: int, is_active: bool):
        """Отрисовывает стильный компактный бейдж с номером зоны (#1, #2...)."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        text = f"#{index}"
        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)
        tw = fm.horizontalAdvance(text)
        th = fm.height()

        bw = tw + 12
        bh = th + 2
        bx = reg.left() + 4
        by = reg.top() + 4

        badge_rect = QRectF(bx, by, bw, bh)

        if is_active:
            bg_col = QColor(37, 99, 235, 235)
            border_col = QColor(96, 165, 250, 240)
            txt_col = QColor(255, 255, 255)
        else:
            bg_col = QColor(15, 23, 42, 205)
            border_col = QColor(255, 255, 255, 120)
            txt_col = QColor(226, 232, 240, 220)

        painter.setPen(QPen(border_col, 1))
        painter.setBrush(QBrush(bg_col))
        painter.drawRoundedRect(badge_rect, 4, 4)

        painter.setPen(txt_col)
        painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def _draw_add_region_hint(self, painter: QPainter):
        """Явно объясняет пользователю, что следующие drag добавят новые зоны."""
        font = QFont("Segoe UI", 10, QFont.Weight.Bold)
        fm = QFontMetrics(font)
        text = tr("region_add_mode", "Режим добавления зон: выделите следующую область")
        width = min(self.width() - 24, max(280, fm.horizontalAdvance(text) + 28))
        rect = QRectF((self.width() - width) / 2.0, 12, width, 30)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(font)
        painter.setPen(QPen(QColor(147, 197, 253), 1))
        painter.setBrush(QBrush(QColor(15, 23, 42, 235)))
        painter.drawRoundedRect(rect, 6, 6)
        painter.setPen(QColor(239, 246, 255))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    # --- Отрисовка с аппаратной оптимизацией ---
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        valid_items = (
            self._recording_selection_items()
            if not getattr(self, "_mass_recording", False)
            else self.get_valid_region_items()
        )
        valid_regions = [region for _, region in valid_items]
        r = self.selection_rect.normalized() if (self.selection_rect.isValid() and not self.selection_rect.isEmpty()) else None
        w, h = self.width(), self.height()

        # Режим 1: Живая запись видео или GIF
        if self.is_recording:
            recording_indices = set(getattr(self, "recording_region_indices", set()))
            # Статический режим (по умолчанию): нераспределённые зоны сохраняют статичный фон
            if not self.dynamic_bg and not getattr(self, "is_passthrough", False) and self.background_pixmap is not None:
                if self.dimmed_background_pixmap is not None:
                    painter.drawPixmap(0, 0, self.dimmed_background_pixmap)
                else:
                    painter.drawPixmap(0, 0, self.background_pixmap)
                    dim_brush = QBrush(QColor(0, 0, 0, 110))
                    screen_region = QRegion(0, 0, w, h)
                    for _, reg in valid_items:
                        screen_region = screen_region.subtracted(QRegion(reg.toRect()))
                    painter.save()
                    painter.setClipRegion(screen_region)
                    painter.fillRect(self.rect(), dim_brush)
                    painter.restore()

                for region_idx, reg in valid_items:
                    if region_idx not in recording_indices:
                        rx, ry, rw, rh = int(reg.x()), int(reg.y()), int(reg.width()), int(reg.height())
                        if rw > 0 and rh > 0:
                            painter.drawPixmap(rx, ry, rw, rh, self.background_pixmap, rx, ry, rw, rh)
                    else:
                        # Область записи очищаем до прозрачности для живого экрана
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                        painter.fillRect(reg, Qt.GlobalColor.transparent)
                        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            else:
                # Динамический режим: весь оверлей прозрачный
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

                dim = QBrush(QColor(0, 0, 0, 110))
                if valid_items:
                    screen_region = QRegion(0, 0, w, h)
                    for _, reg in valid_items:
                        screen_region = screen_region.subtracted(QRegion(reg.toRect()))
                    painter.save()
                    painter.setClipRegion(screen_region)
                    painter.fillRect(self.rect(), dim)
                    painter.restore()

                    for region_idx, reg in valid_items:
                        if region_idx not in recording_indices:
                            painter.fillRect(reg, QColor(0, 0, 0, 1))
                else:
                    painter.fillRect(self.rect(), dim)

            # Свободные зоны остаются видимыми и выбираемыми. Рисуем только
            # их тонкий контур и номер: слои, маски и временные фигуры здесь
            # намеренно не рисуются, чтобы они не попали в GIF/MP4. Область
            # активной записи вырезана из маски overlay и обслуживается её
            # отдельным окном записи.
            recording_indices = set(getattr(self, "recording_region_indices", set()))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for display_idx, (region_idx, reg) in enumerate(valid_items):
                if region_idx in recording_indices:
                    continue
                painter.setPen(QPen(QColor(96, 165, 250), 1.2, Qt.PenStyle.DashLine))
                outer_border = QRectF(
                    reg.left() - 2,
                    reg.top() - 2,
                    reg.width() + 4,
                    reg.height() + 4,
                )
                painter.drawRect(outer_border)
                if len(valid_items) > 1:
                    self._draw_region_index_badge(
                        painter,
                        reg,
                        display_idx + 1,
                        region_idx == self.active_region_idx,
                    )

            active_recording = self.active_region_idx in recording_indices
            if not active_recording and not self._active_region_is_locked() and r is not None:
                self._draw_handles(painter, r)

            # Отрисовываем слои аннотаций на свободных зонах во время записи
            self.layer_manager.draw_all(painter, source_pixmap=self.background_pixmap)

            active_masks = self._capture_masks_for_region()
            for active_mask in active_masks:
                active_mask.draw(painter)

            # Временная фигура при рисовании (карандаш, прямоугольник и т.д.)
            if self.temp_shape is not None:
                self.temp_shape.draw(painter, source_pixmap=self.background_pixmap)
                self._draw_temp_shape_outline(painter)

            # Рамка трансформации и индикатор фигур
            self._draw_dragged_shape_indicator(painter)
            self._draw_object_selection_overlay(painter)
            return

        # Режим 2: Режим скриншота и аннотаций
        dimmed_bg = getattr(self, "dimmed_background_pixmap", None)
        if dimmed_bg is None and self.background_pixmap is None and not self.dynamic_bg and not getattr(self, "is_passthrough", False):
            if valid_regions:
                dim_brush = QBrush(QColor(0, 0, 0, 120))
                screen_region = QRegion(0, 0, w, h)
                for reg in valid_regions:
                    screen_region = screen_region.subtracted(QRegion(reg.toRect()))
                painter.save()
                painter.setClipRegion(screen_region)
                painter.fillRect(self.rect(), dim_brush)
                painter.restore()
                for reg in valid_regions:
                    painter.fillRect(reg, QColor(0, 0, 0, 1))
            else:
                painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.fillRect(self.rect(), Qt.GlobalColor.transparent)
                return

        if getattr(self, "is_passthrough", False):
            if valid_regions:
                dim_brush = QBrush(QColor(0, 0, 0, 120))
                screen_region = QRegion(0, 0, w, h)
                for reg in valid_regions:
                    screen_region = screen_region.subtracted(QRegion(reg.toRect()))
                painter.save()
                painter.setClipRegion(screen_region)
                painter.fillRect(self.rect(), dim_brush)
                painter.restore()
                for reg in valid_regions:
                    painter.fillRect(reg, QColor(0, 0, 0, 1))
            else:
                painter.fillRect(self.rect(), QColor(0, 0, 0, 70))
        elif not self.dynamic_bg:
            if dimmed_bg is not None:
                painter.drawPixmap(0, 0, dimmed_bg)
                if self.background_pixmap is not None:
                    for reg in valid_regions:
                        rx, ry, rw, rh = int(reg.x()), int(reg.y()), int(reg.width()), int(reg.height())
                        if rw > 0 and rh > 0:
                            painter.drawPixmap(rx, ry, rw, rh, self.background_pixmap, rx, ry, rw, rh)
            else:
                if self.background_pixmap is not None:
                    painter.drawPixmap(0, 0, self.background_pixmap)

                if valid_regions:
                    dim_brush = QBrush(QColor(0, 0, 0, 120))
                    screen_region = QRegion(0, 0, w, h)
                    for reg in valid_regions:
                        screen_region = screen_region.subtracted(QRegion(reg.toRect()))
                    painter.save()
                    painter.setClipRegion(screen_region)
                    painter.fillRect(self.rect(), dim_brush)
                    painter.restore()
                else:
                    painter.fillRect(self.rect(), QColor(0, 0, 0, 110))
        else:
            if valid_regions:
                dim_brush = QBrush(QColor(0, 0, 0, 120))
                screen_region = QRegion(0, 0, w, h)
                for reg in valid_regions:
                    screen_region = screen_region.subtracted(QRegion(reg.toRect()))
                painter.save()
                painter.setClipRegion(screen_region)
                painter.fillRect(self.rect(), dim_brush)
                painter.restore()

                for reg in valid_regions:
                    painter.fillRect(reg, QColor(0, 0, 0, 1))
            else:
                painter.fillRect(self.rect(), QColor(0, 0, 0, 70))

        if valid_regions:
            # Нижняя панель применяет эффект только к соответствующей зоне.
            for region_idx, reg in valid_items:
                filter_type, filter_params = self._filter_state_for_region(region_idx)
                if filter_type != FilterType.NONE:
                    self._paint_filtered_region(painter, reg, filter_type, filter_params)

            # Контуры и бейджи для всех зон
            for idx, reg in enumerate(valid_regions):
                is_active = (idx == self.active_region_idx)
                border_rect = QRectF(reg.left() - 1, reg.top() - 1, reg.width() + 2, reg.height() + 2)
                if is_active:
                    pen_border = QPen(QColor(56, 139, 253), 1.5, Qt.PenStyle.DashLine)
                else:
                    pen_border = QPen(QColor(147, 197, 253, 160), 1.2, Qt.PenStyle.DashLine)
                painter.setPen(pen_border)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(border_rect)

                if len(valid_regions) > 1:
                    self._draw_region_index_badge(painter, reg, idx + 1, is_active)

            # 8 маркеров изменения размера для активной зоны
            if not self._active_region_is_locked() and r is not None:
                self._draw_handles(painter, r)

            # Отрисовываем слои аннотаций
            self.layer_manager.draw_all(painter, source_pixmap=self.background_pixmap)

            active_masks = self._capture_masks_for_region()
            for active_mask in active_masks:
                active_mask.draw(painter)

            # Временная фигура при рисовании
            if self.temp_shape is not None:
                self.temp_shape.draw(painter, source_pixmap=self.background_pixmap)
                self._draw_temp_shape_outline(painter)

            # Рамка и плашка с номером фигуры при перемещении ПКМ
            self._draw_dragged_shape_indicator(painter)

            # Подсветка всех интерактивных объектов при удержании клавиши Alt
            if getattr(self, "is_highlighting_objects", False):
                self._draw_interactive_objects_highlight(painter)
            self._draw_object_selection_overlay(painter)
        # Экранная лупа с палитрой при активной пипетке
        if getattr(self, "is_eyedropper_active", False):
            self._draw_eyedropper_loupe(painter)

    def _draw_temp_shape_outline(self, painter: QPainter):
        """Отрисовывает контрастную пунктирную рамку во время выделения мозаики и эффектов цензуры."""
        if self.temp_shape is None:
            return
        if hasattr(self.temp_shape, "rect") and (
            isinstance(self.temp_shape, (MosaicShape, BlurShape, RegionalEffectShape))
            or getattr(self.temp_shape, "is_mosaic", False)
            or getattr(self.temp_shape, "is_blur", False)
        ):
            r = self.temp_shape.rect.normalized()
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

    def _draw_dragged_shape_indicator(self, painter: QPainter):
        """Отрисовывает рамку трансформации в стиле Photoshop / Figma вокруг активной фигуры."""
        self.interactive_badge_expand_rect = QRectF()
        if self.current_tool not in (ToolType.SELECT, ToolType.MOVE, ToolType.CAPTURE_MASK):
            return
        if not hasattr(self, "transform_box") or not self.transform_box.is_active():
            return

        active_shape = self._active_interactive_shape()
        selected_shapes = self._selected_interactive_shapes()
        is_group = len(selected_shapes) > 1

        if active_shape is None and not is_group:
            return

        if is_group:
            if hasattr(self, "transform_box") and self.transform_box.is_active() and isinstance(self.transform_box.shape, ShapeGroup):
                bbox = self.transform_box.shape.get_bounding_rect()
            else:
                bbox = None
                for selected_shape in selected_shapes:
                    shape_bounds = self._shape_bounds(selected_shape)
                    if shape_bounds.isEmpty():
                        continue
                    bbox = QRectF(shape_bounds) if bbox is None else bbox.united(shape_bounds)
            if bbox is None:
                return
        else:
            bbox = active_shape.get_bounding_rect()
        if not bbox.isValid() or bbox.isEmpty():
            return

        try:
            shape_idx = self.layer_manager.shapes.index(active_shape) + 1
        except (ValueError, AttributeError):
            shape_idx = self.active_region_idx + 1 if getattr(active_shape, "is_capture_mask", False) else 1

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Отрисовка интерактивной рамки с 8 маркерами и маркером вращения
        self.transform_box.draw(painter)

        # 2. Плашка с названием, номером и углом поворота (например «Стрелка #1 (45°)»)
        tb_shape = getattr(self.transform_box, "shape", None) if hasattr(self, "transform_box") else None
        rot = getattr(tb_shape, "rotation", 0.0) if tb_shape is not None else (getattr(active_shape, "rotation", 0.0) if active_shape is not None else 0.0)
        rot_str = f" ({round(rot)}°)" if rot != 0.0 else ""
        if is_group:
            badge_text = (
                f"Группа · {len(selected_shapes)} объектов{rot_str} · "
                f"{round(bbox.width())} × {round(bbox.height())} px"
            )
        else:
            badge_text = self._interactive_shape_badge_text(active_shape, shape_idx, rot_str=rot_str)
        font = QFont("Segoe UI", 9)
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()
        text_w = fm.horizontalAdvance(badge_text)
        text_h = fm.height()

        expand_button_w = 22.0
        badge_w = text_w + 16 + expand_button_w
        badge_h = max(20, text_h + 4)

        top_y = bbox.top() - 32.0 - badge_h
        if top_y < 4:
            top_y = bbox.bottom() + 8.0

        badge_rect = QRectF(bbox.center().x() - badge_w / 2.0, top_y, badge_w, badge_h)

        # Фон плашки
        painter.setPen(QPen(QColor(59, 130, 246, 180), 1))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(badge_rect, 4, 4)

        # Кнопка разворота справа внутри плашки.
        button_rect = QRectF(
            badge_rect.right() - expand_button_w + 2,
            badge_rect.top() + 2,
            expand_button_w - 4,
            badge_h - 4,
        )
        self.interactive_badge_expand_rect = QRectF(badge_rect)
        painter.setPen(QPen(QColor(148, 163, 184, 180), 1))
        painter.setBrush(QColor(30, 41, 59, 220))
        painter.drawRoundedRect(button_rect, 3, 3)

        painter.setPen(QPen(QColor(241, 245, 249), 1.5))
        cx = button_rect.center().x()
        cy = button_rect.center().y()
        painter.drawLine(QPointF(cx - 3.5, cy - 1.5), QPointF(cx, cy + 2.0))
        painter.drawLine(QPointF(cx, cy + 2.0), QPointF(cx + 3.5, cy - 1.5))

        painter.setPen(QColor(255, 255, 255))
        painter.drawText(
            QRectF(badge_rect.left() + 8, badge_rect.top(), text_w, badge_h),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            badge_text,
        )

        painter.restore()

    def _active_interactive_shape(self):
        """Возвращает выбранный объект слоя, исключая маски зон захвата."""
        shape = None
        if hasattr(self, "transform_box") and self.transform_box.is_active():
            shape = self.transform_box.shape
        elif getattr(self, "active_editing_shape", None) is not None:
            shape = self.active_editing_shape
        manager = getattr(self, "layer_manager", None)
        if shape is None or not getattr(shape, "visible", False) or manager is None:
            return None
        if getattr(shape, "is_capture_mask", False):
            return None
        if isinstance(shape, ShapeGroup):
            if not any(s in manager.shapes for s in shape.shapes):
                return None
            return shape
        if shape not in manager.shapes:
            return None
        return shape

    def _selected_interactive_shapes(self):
        """Возвращает видимые выбранные фигуры, исключая маски зон захвата."""
        manager = getattr(self, "layer_manager", None)
        if manager is None:
            return []
        return [
            shape
            for shape in getattr(self, "selected_shapes", [])
            if shape in manager.shapes
            and getattr(shape, "visible", False)
            and not getattr(shape, "is_capture_mask", False)
        ]

    @staticmethod
    def _expanded_shape_state(shape: BaseShape, target: QRectF):
        """Создаёт состояние фигуры, растянутое на target без изменения фона."""
        state = shape.clone()
        target = target.normalized()
        state.rotation = 0.0

        if hasattr(state, "rect"):
            state.rect = QRectF(target)
            return state

        if hasattr(state, "p1") and hasattr(state, "p2"):
            state.p1 = QPointF(target.topLeft())
            state.p2 = QPointF(target.bottomRight())
            return state

        bounds = state.get_bounding_rect().normalized()
        if bounds.isValid() and not bounds.isEmpty() and hasattr(state, "scale_from_origin"):
            sx = target.width() / max(1.0, bounds.width())
            sy = target.height() / max(1.0, bounds.height())
            state.scale_from_origin(sx, sy, bounds.topLeft())
            moved = state.get_bounding_rect()
            state.translate(target.left() - moved.left(), target.top() - moved.top())
        return state

    @staticmethod
    def _expanded_group_states(shapes: list[BaseShape], target: QRectF):
        """Масштабирует выбранную группу целиком, сохраняя взаимное расположение."""
        base_states = [(shape, shape.clone()) for shape in shapes]
        target = target.normalized()
        if not base_states or not target.isValid() or target.isEmpty():
            return base_states

        group_bounds = None
        for _, state in base_states:
            bounds = QRectF(state.get_bounding_rect()).normalized()
            if not bounds.isValid() or bounds.isEmpty():
                continue
            group_bounds = QRectF(bounds) if group_bounds is None else group_bounds.united(bounds)
        if group_bounds is None or group_bounds.isEmpty():
            return base_states

        origin = group_bounds.topLeft()
        sx = target.width() / max(1.0, group_bounds.width())
        sy = target.height() / max(1.0, group_bounds.height())

        def build_states(scale_x: float, scale_y: float):
            scaled_states = []
            scaled_bounds = None
            for shape, base_state in base_states:
                state = base_state.clone()
                if hasattr(state, "scale_from_origin"):
                    state.scale_from_origin(scale_x, scale_y, origin)
                scaled_states.append((shape, state))
                bounds = QRectF(state.get_bounding_rect()).normalized()
                if not bounds.isValid() or bounds.isEmpty():
                    continue
                scaled_bounds = QRectF(bounds) if scaled_bounds is None else scaled_bounds.united(bounds)
            return scaled_states, scaled_bounds

        # У фигур внешняя граница включает постоянный padding пера. Несколько
        # итераций компенсируют его и дают точное заполнение зоны, не меняя
        # толщину обводки и не смещая взаимное расположение объектов.
        scaled_states = base_states
        scaled_bounds = group_bounds
        for _ in range(8):
            scaled_states, scaled_bounds = build_states(sx, sy)
            if scaled_bounds is None or scaled_bounds.isEmpty():
                return scaled_states
            width_ratio = target.width() / max(1.0, scaled_bounds.width())
            height_ratio = target.height() / max(1.0, scaled_bounds.height())
            if abs(width_ratio - 1.0) < 0.0001 and abs(height_ratio - 1.0) < 0.0001:
                break
            sx *= width_ratio
            sy *= height_ratio

        if scaled_bounds is not None and not scaled_bounds.isEmpty():
            dx = target.left() - scaled_bounds.left()
            dy = target.top() - scaled_bounds.top()
            for _, state in scaled_states:
                state.translate(dx, dy)
        return scaled_states

    def _expand_active_interactive_object_to_region(self):
        """Растягивает объект или выбранную группу на всю активную зону с Undo/Redo."""
        selected = self._selected_interactive_shapes()
        shape = self._active_interactive_shape()
        targets = selected if len(selected) > 1 else ([shape] if shape is not None else selected)
        target = self.selection_rect.normalized()
        if not targets or not target.isValid() or target.isEmpty():
            return False

        old_states = [(item, item.clone()) for item in targets]
        if len(targets) == 1:
            new_states = [(targets[0], self._expanded_shape_state(targets[0], target))]
        else:
            new_states = self._expanded_group_states(targets, target)
        for item, state in new_states:
            self._apply_shape_geometry(item, state)
        if hasattr(self, "transform_box"):
            self.transform_box.set_shape(targets[0] if len(targets) == 1 else ShapeGroup(targets))
        self._invalidate_layers_cache()
        self.update()
        history = getattr(self, "history_manager", None)
        if history is not None:
            def apply_snapshot(snapshot):
                for item, state in snapshot:
                    self._apply_shape_geometry(item, state)
                if hasattr(self, "transform_box"):
                    self.transform_box.set_shape(targets[0] if len(targets) == 1 else ShapeGroup(targets))
                self._on_layers_changed()

            cmd = HistoryCommand(
                tr(
                    "hist_cmd_expand_group" if len(targets) > 1 else "hist_cmd_expand_object",
                    "Группа объектов развёрнута на зону" if len(targets) > 1 else "Объект развёрнут на зону",
                ),
                do_func=lambda snapshot=new_states: apply_snapshot(snapshot),
                undo_func=lambda snapshot=old_states: apply_snapshot(snapshot),
            )
            history.push_already_done(cmd)
        return True

    def _interactive_shape_badge_text(self, shape, shape_idx: int, rot_str: str = "") -> str:
        """Формирует подпись объекта с его фактическим размером в пикселях."""
        name = getattr(shape, "name", self._get_shape_display_name(shape))
        return f"{name} #{shape_idx} · {self._interactive_shape_size_text(shape)}{rot_str}"

    @staticmethod
    def _interactive_shape_size_text(shape) -> str:
        bounds = getattr(shape, "rect", None)
        if bounds is None or not bounds.isValid() or bounds.isEmpty():
            bounds = shape.get_bounding_rect()
        width = max(1, round(bounds.width()))
        height = max(1, round(bounds.height()))
        return f"{width} × {height} px"

    def _paint_filtered_region(
        self,
        painter: QPainter,
        rect: QRectF,
        filter_type: str | None = None,
        filter_params: dict | None = None,
    ):
        try:
            import cv2
            import numpy as np

            rx, ry, rw, rh = int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height())
            if rw <= 0 or rh <= 0: return
            crop = self.background_pixmap.copy(rx, ry, rw, rh)
            qimg = crop.toImage().convertToFormat(QImage.Format.Format_ARGB32)
            ptr = qimg.bits()
            ptr.setsize(qimg.sizeInBytes())
            arr = np.frombuffer(ptr, np.uint8).reshape((rh, rw, 4))
            bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
            if filter_type is None:
                filter_type, filter_params = self._filter_state_for_region(self.active_region_idx)
            fbgr = apply_filter(bgr, filter_type, **(filter_params or {}))
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
        layer_shapes = list(getattr(getattr(self, "layer_manager", None), "shapes", []) or [])
        capture_masks = [
            mask
            for masks in getattr(self, "capture_masks", {}).values()
            for mask in (masks or [])
        ]
        interactive_shapes = layer_shapes + capture_masks
        if not interactive_shapes:
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        font = QFont("Segoe UI", 9, QFont.Weight.Bold)
        fm = QFontMetrics(font)

        for s in interactive_shapes:
            if not getattr(s, "visible", True):
                continue
            is_capture_mask = isinstance(s, CaptureMaskShape)
            mask_path = s.path() if is_capture_mask else None
            br = mask_path.boundingRect() if mask_path is not None else s.get_bounding_rect()
            if not br.isValid() or br.isEmpty():
                continue

            rot = 0.0 if is_capture_mask else getattr(s, "rotation", 0.0)
            c = br.center()

            # 1. Контурная рамка вокруг объекта с учетом угла вращения.
            painter.save()
            if is_capture_mask:
                # У маски путь уже содержит поворот. Рисуем именно контур,
                # чтобы Alt показывал фактическую область захвата, включая
                # несколько масок одной зоны.
                pen_box = QPen(QColor(250, 204, 21, 235), 1.8, Qt.PenStyle.DashLine)
                painter.setPen(pen_box)
                painter.setBrush(QBrush(QColor(250, 204, 21, 35)))
                painter.drawPath(mask_path)
            else:
                if abs(rot) > 0.01:
                    painter.translate(c)
                    painter.rotate(rot)
                    painter.translate(-c)

                pen_box = QPen(QColor(56, 189, 248, 220), 1.5, Qt.PenStyle.DashLine)
                painter.setPen(pen_box)
                painter.setBrush(QBrush(QColor(56, 189, 248, 30)))
                adj_rect = br.adjusted(-4, -4, 4, 4)
                painter.drawRoundedRect(adj_rect, 4, 4)
            painter.restore()

            # 2. Локализованное название фигуры (бейдж держим всегда строго горизонтально)
            tag_text = f"{self._get_shape_display_name(s)} · {self._interactive_shape_size_text(s)}"
            text_w = fm.horizontalAdvance(tag_text)
            badge_w = text_w + 14
            badge_h = 20

            # Вычисляем крайнюю верхнюю точку повернутой фигуры, чтобы плашка была над ней
            corners = [
                br.topLeft(),
                br.topRight(),
                br.bottomRight(),
                br.bottomLeft()
            ]
            rot_corners = [rotate_point(p, c, rot) for p in corners] if abs(rot) > 0.01 else corners
            min_y = min(p.y() for p in rot_corners)
            mean_x = c.x()

            badge_x = mean_x - badge_w / 2.0
            badge_y = min_y - badge_h - 6.0
            if badge_y < 4.0:
                max_y = max(p.y() for p in rot_corners)
                badge_y = max_y + 6.0

            badge_rect = QRectF(badge_x, badge_y, badge_w, badge_h)

            # Цвет бейджа совпадает с контуром маски, чтобы её можно было
            # отличить от обычных аннотаций.
            highlight_color = QColor(250, 204, 21) if is_capture_mask else QColor(56, 189, 248)

            painter.setPen(QPen(highlight_color, 1))
            painter.setBrush(QBrush(QColor(15, 23, 42, 230)))
            painter.drawRoundedRect(badge_rect, 4, 4)

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
            if getattr(self, "is_eyedropper_active", False):
                self.is_eyedropper_active = False
                self.eyedropper_callback = None
                self._set_cursor_if_needed(self._get_tool_cursor())
                self.update()
                return
            if self.text_editor.isVisible():
                self.text_editor.clear()
                self.text_editor.hide()
                self.editing_existing_text_shape = None
                self.update()
                return
            if self.temp_shape is not None:
                self.temp_shape = None
                self.shape_origin_pos = None
                try:
                    self.releaseMouse()
                except Exception:
                    pass
                self.update()
                return
            if self.is_selecting_objects or self.is_moving_objects or self.selected_shapes:
                self._clear_shape_selection()
                self.update()
                return
            if self.is_recording:
                self.dismiss_unused_selection()
                return
            if self.is_adding_region:
                self._set_add_region_mode(False)
                return
            if len(self.get_valid_regions()) > 1:
                # Escape отменяет весь текущий набор, а не только последнюю
                # активную зону. Удаление одной зоны остаётся доступно через
                # крестик/контекстное действие.
                self.close_overlay()
                return
            self.close_overlay()
            return
        elif key == Qt.Key.Key_Tab:
            if len(self.get_valid_regions()) > 1:
                if modifiers & Qt.KeyboardModifier.ShiftModifier:
                    self.activate_previous_region()
                else:
                    self.activate_next_region()
                return
        elif key == Qt.Key.Key_Delete:
            if len(getattr(self, "selected_shapes", [])) > 1:
                self._delete_selected_interactive_objects()
                event.accept()
                return
            selected = self._selected_interactive_object()
            if selected is not None:
                if getattr(selected, "is_capture_mask", False):
                    self._delete_capture_mask(selected)
                else:
                    self._delete_shape(selected)
                event.accept()
                return
        elif modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_A:
                self.select_entire_screen()
            elif key == Qt.Key.Key_N:
                self.start_adding_region()
            elif key == Qt.Key.Key_W:
                if len(self.get_valid_regions()) > 1:
                    self.close_active_region()
                else:
                    self.close_overlay()
            elif key == Qt.Key.Key_R:
                self.start_recording("video")
            elif key == Qt.Key.Key_G:
                self.start_recording("gif")
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
                # Ctrl+C — явное групповое копирование: каждая зона сохраняется
                # отдельным PNG внутри custom clipboard payload.
                self.copy_screenshot("standard", all_regions=True)
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
        self.regions = [QRectF(self.rect())]
        self.capture_masks.clear()
        self.region_filters.clear()
        self.region_filter_params.clear()
        self.active_region_idx = 0
        self._set_add_region_mode(False)
        self.target_hwnd = None
        self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)
        self._show_toolbars()
        self._update_toolbar_positions()
        self._sync_close_button_tooltip()
        self._sync_active_region_filter_ui()
        self.update()

    # --- Инструменты и панели ---
    def _on_tool_changed(self, tool_type):
        if hasattr(self, "text_editor") and self.text_editor.isVisible():
            self._commit_text()
        self._clear_shape_selection()
        self.current_tool = tool_type
        if tool_type == ToolType.CAPTURE_MASK:
            masks = self._capture_masks_for_region()
            self.transform_box.set_shape(masks[-1] if masks else None)
        if tool_type == ToolType.MOVE:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.selection_rect.isValid() else Qt.CursorShape.CrossCursor)
        elif tool_type == ToolType.SELECT:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        elif tool_type == ToolType.TEXT:
            self.setCursor(Qt.CursorShape.IBeamCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def _on_capture_mask_chosen(self, kind: str):
        """Синхронизирует выбранную форму маски с текущим активным инструментом."""
        self._clear_shape_selection()
        self.right_toolbar.tools_config.setdefault(ToolType.CAPTURE_MASK, {})["mask_kind"] = kind
        self.current_tool = ToolType.CAPTURE_MASK
        masks = self._capture_masks_for_region()
        self.transform_box.set_shape(masks[-1] if masks else None)
        self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)
        self.update()

    def _filter_state_for_region(self, region_idx: int | None = None) -> tuple[str, dict]:
        """Возвращает независимый фильтр и его параметры для одной зоны."""
        idx = self.active_region_idx if region_idx is None else region_idx
        region_filters = self.__dict__.get("region_filters")
        region_params = self.__dict__.get("region_filter_params")
        if region_filters is None or region_params is None:
            # Совместимость с лёгкими тестовыми объектами OverlayWindow.__new__.
            return (
                self.__dict__.get("current_filter", FilterType.NONE),
                dict(self.__dict__.get("filter_params", {"blur_radius": 15, "pixel_size": 12})),
            )
        return (
            region_filters.get(idx, FilterType.NONE),
            dict(region_params.get(idx, {"blur_radius": 15, "pixel_size": 12})),
        )

    def _sync_active_region_filter_ui(self):
        """Показывает в нижней панели настройки только активной зоны."""
        filter_type, params = self._filter_state_for_region(self.active_region_idx)
        self.current_filter = filter_type
        self.filter_params = dict(params)
        toolbar = self.__dict__.get("bottom_toolbar")
        if toolbar is not None and hasattr(toolbar, "sync_filter"):
            toolbar.sync_filter(filter_type)
            if hasattr(toolbar, "sync_filter_params"):
                toolbar.sync_filter_params(params)

    def _apply_region_filter_state(
        self,
        region_idx: int,
        filter_type: str,
        filter_params: dict | None = None,
    ):
        """Устанавливает фильтр одной зоны и обновляет её визуальное состояние."""
        if not hasattr(self, "region_filters"):
            self.region_filters = {}
        if not hasattr(self, "region_filter_params"):
            self.region_filter_params = {}
        self.region_filters[region_idx] = filter_type
        self.region_filter_params[region_idx] = dict(
            filter_params or {"blur_radius": 15, "pixel_size": 12}
        )
        # A recording window owns the live CaptureWorker for its zone. Keep
        # the model, button state, and worker synchronized even after capture
        # has started; otherwise the mass-effects panel only changes the
        # preview behind an already-running recording.
        for rec_window in self.__dict__.get("recording_windows", []):
            try:
                rec_idx = int(getattr(rec_window, "region_index", 0)) - 1
            except (TypeError, ValueError):
                rec_idx = -1
            if rec_idx != region_idx:
                continue
            rec_window.filter_type = filter_type
            rec_window.filter_params = dict(self.region_filter_params[region_idx])
            worker = getattr(rec_window, "capture_worker", None)
            if worker is not None and hasattr(worker, "set_filter"):
                worker.set_filter(filter_type, dict(rec_window.filter_params))
            sync_button = getattr(rec_window, "_sync_filter_button", None)
            if callable(sync_button):
                sync_button()
        if region_idx == self.active_region_idx:
            self._sync_active_region_filter_ui()
            if self.__dict__.get("capture_worker"):
                self.capture_worker.set_filter(filter_type, self.filter_params)
        self._invalidate_layers_cache()
        self.update()

    def _apply_filter_state(self, filter_type: str, filter_params: dict | None = None):
        """Совместимый фасад: нижняя панель меняет активную зону."""
        self._apply_region_filter_state(self.active_region_idx, filter_type, filter_params)

    def _on_filter_changed(self, filter_type: str, from_history: bool = False):
        region_idx = self.active_region_idx
        old_filter, old_params = self._filter_state_for_region(region_idx)
        if filter_type == old_filter:
            return
        self._apply_region_filter_state(region_idx, filter_type, old_params)

        if not from_history:
            from utils.image_filters import get_localized_filter_names
            flt_names = get_localized_filter_names()
            f_label = flt_names.get(filter_type, filter_type)
            cmd = HistoryCommand(
                tr("hist_cmd_filter", "Фильтр: {name}", name=f_label),
                do_func=lambda i=region_idx, f=filter_type, p=dict(old_params): self._apply_region_filter_state(i, f, p),
                undo_func=lambda i=region_idx, f=old_filter, p=dict(old_params): self._apply_region_filter_state(i, f, p),
            )
            self.history_manager.push_already_done(cmd)

    def _on_filter_parameters_changed(self, params: dict):
        """Меняет силу эффекта только у активной зоны."""
        region_idx = self.active_region_idx
        filter_type, current_params = self._filter_state_for_region(region_idx)
        current_params.update(params or {})
        self._apply_region_filter_state(region_idx, filter_type, current_params)

    def _apply_filter_states(self, states: list[tuple[int, str, dict]]):
        for region_idx, filter_type, params in states:
            self._apply_region_filter_state(region_idx, filter_type, params)

    def _on_mass_filter_changed(self, filter_type: str):
        """Применяет верхний массовый эффект ко всем зонам, включая запись."""
        self.mass_filter = filter_type
        # Mass actions intentionally include regions whose recording has
        # already started. Their CaptureWorkers are updated by
        # _apply_region_filter_state between frames.
        target_items = self.get_valid_region_items()
        if not target_items:
            return
        old_states = [
            (idx, *self._filter_state_for_region(idx))
            for idx, _ in target_items
        ]
        new_states = [
            (idx, filter_type, dict(self.mass_filter_params))
            for idx, _ in target_items
        ]
        self._apply_filter_states(new_states)
        if hasattr(self, "history_manager"):
            from utils.image_filters import get_localized_filter_names
            name = get_localized_filter_names().get(filter_type, filter_type)
            self.history_manager.push_already_done(HistoryCommand(
                tr("hist_cmd_filter", "Фильтр: {name}", name=f"{name} · все зоны"),
                do_func=lambda states=list(new_states): self._apply_filter_states(states),
                undo_func=lambda states=list(old_states): self._apply_filter_states(states),
            ))

    def _on_mass_filter_parameters_changed(self, params: dict):
        """Меняет параметры массового эффекта во всех доступных зонах."""
        self.mass_filter_params.update(params or {})
        if self.mass_filter == FilterType.NONE:
            return
        target_items = self.get_valid_region_items()
        for region_idx, _ in target_items:
            filter_type, current_params = self._filter_state_for_region(region_idx)
            current_params.update(params or {})
            self._apply_region_filter_state(region_idx, filter_type, current_params)

    def _on_lock_toggled(self, is_locked):
        self.is_locked = bool(is_locked)
        self.__dict__.setdefault("region_locks", {})[self.active_region_idx] = self.is_locked
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

    # --- Чистый захват скриншотов без интерфейсных рамок ---
    def get_action_regions(self, all_regions: bool = False) -> list[QRectF]:
        """Возвращает активную зону или все зоны для явного группового действия."""
        return [region for _, region in self.get_action_region_items(all_regions=all_regions)]

    def _is_fullscreen_region(self, region: QRectF) -> bool:
        """Определяет выбор всего экрана в локальных координатах overlay."""
        r = region.normalized()
        overlay_w = max(0, int(self.width()))
        overlay_h = max(0, int(self.height()))
        if overlay_w and overlay_h and r.left() <= 1 and r.top() <= 1:
            if r.width() >= overlay_w - 2 and r.height() >= overlay_h - 2:
                return True

        screen = QApplication.primaryScreen()
        if screen is None:
            return False
        geo = screen.geometry()
        return (
            abs(r.left() - geo.left()) <= 1
            and abs(r.top() - geo.top()) <= 1
            and abs(r.width() - geo.width()) <= 2
            and abs(r.height() - geo.height()) <= 2
        )

    def _render_region_image(self, region: QRectF, region_idx: int | None = None) -> QImage:
        """Рендерит одну зону в логических координатах overlay.

        Win32-захват возвращает физический QPixmap с devicePixelRatio > 1,
        тогда как QRectF зон живёт в логических координатах Qt. Сначала
        приводим источник к DPR=1, иначе QPixmap.copy() читает другую часть
        экрана и отдельные зоны превращаются в пустые/белые изображения.
        """
        r = region.normalized()
        rx, ry, rw, rh = int(r.x()), int(r.y()), int(r.width()), int(r.height())
        if rw <= 0 or rh <= 0:
            return QImage()

        base_pix = self._to_logical_pixmap(self.background_pixmap)
        crop_pix = QPixmap()

        # При живом фоне читаем свежую область, но всё равно нормализуем DPR.
        if self.dynamic_bg or getattr(self, "is_passthrough", False):
            try:
                geo = self.geometry()
                fresh = safe_grab_screen_pixmap(geo.x() + rx, geo.y() + ry, rw, rh)
                crop_pix = self._to_logical_pixmap(fresh)
            except Exception:
                crop_pix = QPixmap()

        # В обычном режиме режем уже сохранённый полный кадр в логических px.
        if crop_pix.isNull() and not base_pix.isNull():
            if QRectF(0, 0, base_pix.width(), base_pix.height()).contains(r):
                crop_pix = base_pix.copy(rx, ry, rw, rh)

        # Если исходный кадр был потерян, делаем локальный повторный захват,
        # но не подставляем белую заглушку: лучше отменить экспорт, чем
        # silently сохранить повреждённый скриншот.
        if crop_pix.isNull():
            try:
                geo = self.geometry()
                fresh = safe_grab_screen_pixmap(geo.x() + rx, geo.y() + ry, rw, rh)
                crop_pix = self._to_logical_pixmap(fresh)
            except Exception:
                crop_pix = QPixmap()

        if crop_pix.isNull() or crop_pix.width() <= 0 or crop_pix.height() <= 0:
            return QImage()
        if crop_pix.width() != rw or crop_pix.height() != rh:
            crop_pix = crop_pix.scaled(
                rw, rh,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        masks = self.__dict__.get("capture_masks", {})
        capture_masks = masks.get(region_idx, []) if region_idx is not None else []
        result_format = QImage.Format.Format_ARGB32 if capture_masks else QImage.Format.Format_RGB32
        result = QImage(rw, rh, result_format)
        if capture_masks:
            result.fill(0)
        painter = QPainter(result)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        filter_type, filter_params = self._filter_state_for_region(
            self.active_region_idx if region_idx is None else region_idx
        )
        if filter_type != FilterType.NONE:
            import cv2

            bgr = qimage_to_cv2_bgr(crop_pix.toImage())
            fbgr = apply_filter(bgr, filter_type, **filter_params)
            frgb = cv2.cvtColor(fbgr, cv2.COLOR_BGR2RGB)
            crop_pix = QPixmap.fromImage(QImage(frgb.data, rw, rh, rw * 3, QImage.Format.Format_RGB888))

        painter.drawPixmap(0, 0, crop_pix)
        source_for_layers = base_pix if not base_pix.isNull() else None
        self.layer_manager.draw_all(painter, offset=QPointF(rx, ry), source_pixmap=source_for_layers)
        painter.end()
        if capture_masks:
            result = apply_mask_to_qimage(result, capture_masks, (rx, ry, rw, rh))
        return result

    @staticmethod
    def _to_logical_pixmap(pixmap: QPixmap | None) -> QPixmap:
        """Приводит физический QPixmap захвата к координатам Qt overlay."""
        if pixmap is None or pixmap.isNull():
            return QPixmap()
        dpr = float(pixmap.devicePixelRatio() or 1.0)
        if abs(dpr - 1.0) < 0.01:
            return pixmap
        logical_size = pixmap.deviceIndependentSize().toSize()
        if logical_size.width() <= 0 or logical_size.height() <= 0:
            return QPixmap()
        normalized = pixmap.scaled(
            logical_size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        normalized.setDevicePixelRatio(1.0)
        return normalized

    def get_cropped_image(self, region: QRectF | None = None) -> QImage:
        """Возвращает снимок только одной зоны — активной, если зона не указана."""
        if region is None:
            action_regions = self.get_action_regions()
            if not action_regions:
                return QImage()
            region = action_regions[0]
        return self._render_region_image(region, self.active_region_idx)

    def get_cropped_images(self, all_regions: bool = True) -> list[QImage]:
        """Возвращает отдельный QImage для каждой выбранной зоны."""
        images = [
            self._render_region_image(region, region_idx)
            for region_idx, region in self.get_action_region_items(all_regions=all_regions)
        ]
        return images if images and all(not image.isNull() for image in images) else []

    @staticmethod
    def _save_images_to_paths(images: list[QImage], path: Path, ext: str) -> list[str]:
        """Сохраняет одну или несколько зон, добавляя расширение при необходимости."""
        if not images:
            return []
        path = Path(path)
        ext = ext.lower().lstrip(".") or "png"
        if not path.suffix:
            path = path.with_suffix(f".{ext}")
        path.parent.mkdir(parents=True, exist_ok=True)
        image_format = "JPEG" if ext in {"jpg", "jpeg"} else ext.upper()
        saved_paths = []
        for index, image in enumerate(images, start=1):
            target = path
            if len(images) > 1:
                target = path.with_name(f"{path.stem}_zone-{index}{path.suffix}")
            if image.save(str(target), image_format):
                saved_paths.append(str(target))
        return saved_paths

    def save_screenshot(self, fmt="png", all_regions: bool = False):
        preserve_selection = self._should_preserve_selection_after_single_region_action(all_regions)
        images = self.get_cropped_images(all_regions=all_regions)
        if not images:
            return

        # Снимки уже отрисованы из фонового pixmap. Не делаем скрытый overlay
        # родителем native QFileDialog: на Windows такой родитель иногда
        # оставляет диалог модальным после Save/Cancel до закрытия Проводника.
        self.hide()
        QApplication.processEvents()
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        
        ext = fmt.lower() if fmt in ("png", "jpg", "webp") else "png"
        filename = f"Screenshot_{timestamp}.{ext}"
        default_path = str(Path(self.cfg.save_dir_screenshots) / filename)

        filter_str = f"{ext.upper()} Image (*.{ext})"
        path, _ = QFileDialog.getSaveFileName(
            None,
            tr("dialog_save_screenshot", "Сохранить скриншоты выбранных зон"),
            default_path,
            f"{filter_str};;Все файлы (*.*)",
            options=QFileDialog.Option.DontUseNativeDialog,
        )
        if path:
            saved_paths = self._save_images_to_paths(images, Path(path), ext)
            if not saved_paths:
                if preserve_selection:
                    self._restore_single_recording_overlay_after_action()
                else:
                    self.show()
                    self.raise_()
                    self.activateWindow()
                self._notify(
                    tr("notif_screen_save_failed_title", "Не удалось сохранить скриншот"),
                    tr("notif_screen_save_failed_body", "Не удалось записать выбранные изображения на диск."),
                    QSystemTrayIcon.MessageIcon.Warning,
                    4000,
                )
                return
            if self.cfg.auto_copy_to_clipboard:
                self._set_images_on_clipboard(images, ext)
            if self.cfg.play_sound:
                play_capture_sound()

            app_inst = getattr(QApplication.instance(), "app_instance", None)
            if app_inst and hasattr(app_inst, "add_recent_media"):
                for index, saved_path in enumerate(saved_paths, start=1):
                    app_inst.add_recent_media(path=saved_path, label=Path(saved_path).name, refresh=False)
                if hasattr(app_inst, "_setup_tray_menu"):
                    app_inst._setup_tray_menu()

            p = Path(saved_paths[0] if saved_paths else path)
            self._notify(
                tr("notif_screen_saved_title"),
                tr("notif_screen_saved_body", filename=p.name, folder=str(p.parent))
                + (tr("notif_screen_saved_count", count=len(saved_paths)) if len(saved_paths) > 1 else ""),
                QSystemTrayIcon.MessageIcon.Information,
                4000,
                target_path=saved_paths[0]
            )
            if preserve_selection:
                self._restore_single_recording_overlay_after_action()
            else:
                self.close_overlay()
        else:
            if preserve_selection:
                self._restore_single_recording_overlay_after_action()
            else:
                self.show()
                self.raise_()
                self.activateWindow()

    def _set_images_on_clipboard(self, images: list[QImage], fmt: str = "png"):
        """Кладёт одну картинку или все зоны как список файлов clipboard."""
        from PyQt6.QtCore import QMimeData, QBuffer, QIODevice
        import base64
        import json

        if not images:
            return
        if len(images) == 1:
            if fmt == "jpg":
                mime_type, image_format = "image/jpeg", "JPEG"
            else:
                mime_type, image_format = "image/png", "PNG"
            mime = QMimeData()
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            images[0].save(buf, image_format, 95 if image_format == "JPEG" else -1)
            mime.setData(mime_type, buf.data())
            mime.setImageData(images[0])
            buf.close()
            QApplication.clipboard().setMimeData(mime)
            return

        encoded = []
        for image in images:
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            image.save(buf, "PNG")
            encoded.append(base64.b64encode(bytes(buf.data())).decode("ascii"))
            buf.close()

        mime = QMimeData()
        payload = json.dumps({"format": "png", "images": encoded}, separators=(",", ":"))
        mime.setData("application/x-framio-image-list", payload.encode("ascii"))
        # Windows has only one standard image slot. Keep the first zone as a
        # real image for ordinary Ctrl+V targets, while the custom Framio
        # payload and file URLs carry all zones for multi-file-aware targets.
        # Do not publish the cache paths as text: text-aware applications were
        # pasting those paths instead of an image.
        first_buffer = QBuffer()
        first_buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        images[0].save(first_buffer, "PNG")
        mime.setData("image/png", first_buffer.data())
        mime.setImageData(images[0])
        first_buffer.close()
        cfg = self.__dict__.get("cfg")
        cache_base = getattr(cfg, "save_dir_screenshots", None) or (Path.cwd() / "Captures" / "Screenshots")
        cache_root = Path(cache_base) / ".clipboard"
        cache_root.mkdir(parents=True, exist_ok=True)
        cache_paths = []
        stamp = time.time_ns()
        for index, image in enumerate(images, start=1):
            cache_path = cache_root / f"Framio_Clipboard_{stamp}_{index}.png"
            if image.save(str(cache_path), "PNG"):
                cache_paths.append(cache_path)
        if cache_paths:
            mime.setUrls([QUrl.fromLocalFile(str(path)) for path in cache_paths])
        QApplication.clipboard().setMimeData(mime)

    def copy_screenshot(self, fmt="standard", all_regions: bool = False):
        preserve_selection = self._should_preserve_selection_after_single_region_action(all_regions)
        # Рендер строится из сохранённого фонового кадра и слоёв, поэтому
        # скрывать overlay перед копированием не нужно. Скрытие с processEvents
        # давало заметное краткое исчезновение затемнения на экране.
        images = self.get_cropped_images(all_regions=all_regions)
        if not images:
            if preserve_selection:
                self._restore_single_recording_overlay_after_action()
            else:
                self.show()
            return
        clipboard = QApplication.clipboard()

        if fmt in ("png", "jpg", "standard"):
            self._set_images_on_clipboard(images, "jpg" if fmt == "jpg" else "png")
            if len(images) > 1:
                desc = tr("notif_clipboard_multi_images", count=len(images))
            elif fmt == "png":
                desc = tr("notif_clipboard_png")
            elif fmt == "jpg":
                desc = tr("notif_clipboard_jpeg")
            else:
                desc = tr("notif_clipboard_standard")
        elif fmt == "data_uri":
            import base64
            from PyQt6.QtCore import QBuffer, QIODevice
            values = []
            for image in images:
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                image.save(buf, "PNG")
                values.append("data:image/png;base64," + base64.b64encode(bytes(buf.data())).decode("ascii"))
                buf.close()
            clipboard.setText("\n".join(values))
            desc = (
                tr("notif_clipboard_data_uri_many", count=len(values))
                if len(values) > 1 else tr("notif_clipboard_data_uri_one")
            )
        else:
            self._set_images_on_clipboard(images, "png")
            desc = tr("notif_clipboard_standard")

        if self.cfg.play_sound:
            play_capture_sound()
        app_inst = getattr(QApplication.instance(), "app_instance", None)
        if app_inst and hasattr(app_inst, "add_recent_media"):
            for index, image in enumerate(images, start=1):
                app_inst.add_recent_media(
                    image=image,
                    label=tr("region_recent_image", "Скриншот зоны {index}", index=index),
                    refresh=False,
                )
            if hasattr(app_inst, "_setup_tray_menu"):
                app_inst._setup_tray_menu()
        self._notify(
            tr("notif_clipboard_copied", "Скриншот скопирован в буфер обмена"),
            desc,
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )
        if preserve_selection:
            self._restore_single_recording_overlay_after_action()
        else:
            self.close_overlay()

    def grab_ui_snapshot_image(self) -> QImage:
        """
        Захватывает полный снимок экрана с текущим оверлеем программы.
        Включает фон, затемнение, границы зон, маркеры трансформации, аннотации и панели инструментов.
        """
        # Скрываем временные всплывающие меню перед снимком, чтобы они не попали в кадр
        if hasattr(self, "region_header") and hasattr(self.region_header, "popup_ui_snapshot"):
            try:
                self.region_header.popup_ui_snapshot.hide()
            except Exception:
                pass
        for popup_name in ("popup_formats", "popup_copy", "popup_video", "popup_gif", "popup_filter"):
            p = getattr(getattr(self, "region_header", None), popup_name, None)
            if p is not None and hasattr(p, "isVisible") and p.isVisible():
                try:
                    p.hide()
                except Exception:
                    pass
        QApplication.processEvents()

        # Базовый захват оверлея со всеми дочерними виджетами (тулбарами, бейджами)
        overlay_pix = self.grab()
        if overlay_pix.isNull() or overlay_pix.width() <= 0 or overlay_pix.height() <= 0:
            return QImage()

        # Если включён динамический режим или фоновый кадр отсутствует,
        # под прозрачный оверлей нужно подложить свежий кадр рабочего стола.
        if getattr(self, "dynamic_bg", False) or self.background_pixmap is None or self.background_pixmap.isNull():
            geo = self.geometry()
            desktop_pix = safe_grab_screen_pixmap(geo.x(), geo.y(), geo.width(), geo.height())
            if desktop_pix is not None and not desktop_pix.isNull():
                composed = QPixmap(desktop_pix.size())
                p = QPainter(composed)
                p.drawPixmap(0, 0, desktop_pix)
                if overlay_pix.size() != desktop_pix.size():
                    p.drawPixmap(0, 0, overlay_pix.scaled(
                        desktop_pix.size(),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    ))
                else:
                    p.drawPixmap(0, 0, overlay_pix)
                p.end()
                return composed.toImage()

        return overlay_pix.toImage()

    def capture_ui_snapshot(self, action: str = "copy"):
        """
        Делает снимок обычного экрана с видимым текущим оверлеем программы.
        Поддерживает выбор: сохранить в файл ('save') или скопировать в буфер обмена ('copy').
        После сохранения или копирования интерфейс и зоны НЕ закрываются.
        """
        snapshot_image = self.grab_ui_snapshot_image()
        if snapshot_image.isNull() or snapshot_image.width() <= 0 or snapshot_image.height() <= 0:
            return

        if action == "copy":
            from PyQt6.QtCore import QMimeData, QBuffer, QIODevice

            mime = QMimeData()
            buf = QBuffer()
            buf.open(QIODevice.OpenModeFlag.WriteOnly)
            snapshot_image.save(buf, "PNG")
            mime.setData("image/png", buf.data())
            mime.setImageData(snapshot_image)
            buf.close()
            QApplication.clipboard().setMimeData(mime)

            if getattr(self.cfg, "auto_save_on_copy", False):
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                save_dir = Path(getattr(self.cfg, "save_dir_screenshots", Path.cwd() / "Captures" / "Screenshots"))
                save_dir.mkdir(parents=True, exist_ok=True)
                snapshot_image.save(str(save_dir / f"UI_Snapshot_{timestamp}.png"), "PNG")

            if getattr(self.cfg, "play_sound", True):
                play_capture_sound()

            app_inst = getattr(QApplication.instance(), "app_instance", None)
            if app_inst and hasattr(app_inst, "add_recent_media"):
                app_inst.add_recent_media(
                    image=snapshot_image,
                    label=tr("region_ui_snapshot_title", "Снимок интерфейса"),
                    refresh=False,
                )
                if hasattr(app_inst, "_setup_tray_menu"):
                    app_inst._setup_tray_menu()

            self._notify(
                tr("notif_ui_snapshot_copied_title", "Снимок интерфейса скопирован"),
                tr("notif_ui_snapshot_copied_body", "Снимок экрана с текущим оверлеем программы помещён в буфер обмена."),
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )

            # После копирования интерфейс и зоны гарантированно НЕ закрываются
            self.show()
            self.raise_()
            self.activateWindow()

        elif action == "save":
            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            ext = getattr(self.cfg, "last_save_format", "png").lower()
            if ext not in ("png", "jpg", "webp"):
                ext = "png"
            filename = f"UI_Snapshot_{timestamp}.{ext}"
            default_path = str(Path(self.cfg.save_dir_screenshots) / filename)
            filter_str = f"{ext.upper()} Image (*.{ext})"

            # Скрываем оверлей на время показа диалога выбора файла
            self.hide()
            QApplication.processEvents()
            try:
                path, _ = QFileDialog.getSaveFileName(
                    None,
                    tr("dialog_save_ui_snapshot", "Сохранить снимок интерфейса"),
                    default_path,
                    f"{filter_str};;Все файлы (*.*)",
                    options=QFileDialog.Option.DontUseNativeDialog,
                )
            finally:
                # После диалога интерфейс и зоны гарантированно восстанавливаются и НЕ закрываются
                self.show()
                self.raise_()
                self.activateWindow()

            if path:
                out_path = Path(path)
                out_ext = out_path.suffix.lstrip(".").lower() or ext
                fmt_tag = "JPEG" if out_ext in ("jpg", "jpeg") else ("WEBP" if out_ext == "webp" else "PNG")
                saved_ok = snapshot_image.save(str(out_path), fmt_tag)
                if not saved_ok:
                    self._notify(
                        tr("notif_screen_save_failed_title", "Не удалось сохранить скриншот"),
                        tr("notif_screen_save_failed_body", "Не удалось записать снимок интерфейса на диск."),
                        QSystemTrayIcon.MessageIcon.Warning,
                        4000,
                    )
                    return

                if getattr(self.cfg, "auto_copy_to_clipboard", False):
                    from PyQt6.QtCore import QMimeData, QBuffer, QIODevice
                    mime = QMimeData()
                    buf = QBuffer()
                    buf.open(QIODevice.OpenModeFlag.WriteOnly)
                    snapshot_image.save(buf, "PNG")
                    mime.setData("image/png", buf.data())
                    mime.setImageData(snapshot_image)
                    buf.close()
                    QApplication.clipboard().setMimeData(mime)

                if getattr(self.cfg, "play_sound", True):
                    play_capture_sound()

                app_inst = getattr(QApplication.instance(), "app_instance", None)
                if app_inst and hasattr(app_inst, "add_recent_media"):
                    app_inst.add_recent_media(path=str(out_path), label=out_path.name, refresh=False)
                    if hasattr(app_inst, "_setup_tray_menu"):
                        app_inst._setup_tray_menu()

                self._notify(
                    tr("notif_ui_snapshot_saved_title", "Снимок интерфейса сохранён"),
                    tr("notif_screen_saved_body", filename=out_path.name, folder=str(out_path.parent)),
                    QSystemTrayIcon.MessageIcon.Information,
                    4000,
                    target_path=str(out_path),
                )

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

        # 2. Копирование в буфер обмена (если включено в настройках).
        # Google Lens получает PNG отдельной прямой multipart-загрузкой.
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
        if engine == "google":
            notification_body = tr("image_search_google_preparing") + saved_info
        else:
            notification_body = tr(
                "image_search_direct_preparing",
                engine=engine_name,
            ) + saved_info
        self._notify(
            f"Поиск в {engine_name}",
            notification_body,
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
        from utils.scrolling_capture import ScrollingCaptureEngine, ScrollingCaptureHUD

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

        # Сбрасываем старую рамку и слои, освобождаем мышь и скрываем оверлей для чистого захвата содержимого экрана
        self.selection_rect = QRectF()
        self.initial_selection = QRectF()
        self.clearMask()
        self.is_passthrough = False
        self.layer_manager.clear()
        self.history_manager.clear()
        self._set_background_pixmap(None)
        try:
            self.releaseMouse()
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.user32.ReleaseCapture()
        except Exception:
            pass
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
            hwnd_under = user32.WindowFromPoint(POINT(int(rx + rw // 2), int(ry + rh // 2)))
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
        import cv2

        try:
            if self.scrolling_engine:
                self.scrolling_engine.cleanup()
                self.scrolling_engine = None
            if self.scrolling_hud:
                self.scrolling_hud.close()
                self.scrolling_hud = None

            if accumulated_bgr is None or accumulated_bgr.size == 0:
                return

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
        except Exception as e:
            print(f"[Overlay] Ошибка сохранения длинного скриншота: {e}")
        finally:
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

    def _cleanup_recorded_region(self, closed_idx: int):
        if 0 <= closed_idx < len(self.__dict__.get("regions", [])):
            self.regions.pop(closed_idx)
            self.capture_masks.pop(closed_idx, None)
            self.capture_masks = {
                (idx - 1 if idx > closed_idx else idx): mask
                for idx, mask in self.capture_masks.items()
                if idx != closed_idx
            }
            current_region_locks = self.__dict__.get("region_locks", {})
            self.region_locks = {
                (idx - 1 if idx > closed_idx else idx): locked
                for idx, locked in current_region_locks.items()
                if idx != closed_idx
            }
            current_region_filters = self.__dict__.get("region_filters", {})
            current_region_filter_params = self.__dict__.get("region_filter_params", {})
            self.region_filters = {
                (idx - 1 if idx > closed_idx else idx): filter_type
                for idx, filter_type in current_region_filters.items()
                if idx != closed_idx
            }
            self.region_filter_params = {
                (idx - 1 if idx > closed_idx else idx): params
                for idx, params in current_region_filter_params.items()
                if idx != closed_idx
            }
            self.recording_region_indices = {
                idx - 1 if idx > closed_idx else idx
                for idx in getattr(self, "recording_region_indices", set())
                if idx != closed_idx
            }
            self._recording_selection_regions = [
                (idx, QRectF(r)) for idx, r in enumerate(self.__dict__.get("regions", []))
            ]
            if hasattr(self, "layer_manager") and hasattr(self.layer_manager, "shapes"):
                self.layer_manager.shapes = [
                    s for s in self.layer_manager.shapes
                    if getattr(s, "region_idx", None) != closed_idx
                ]
                for s in self.layer_manager.shapes:
                    s_idx = getattr(s, "region_idx", None)
                    if s_idx is not None and s_idx > closed_idx:
                        s.region_idx = s_idx - 1
            self._invalidate_layers_cache()

    def _on_recording_stopped_for_region(self, rec_window: RecordingFrameWindow):
        """Немедленно устраняет брешь и возвращает статичный фон при нажатии Stop на окне записи."""
        if getattr(rec_window, "_region_already_cleaned", False):
            return
        rec_window._region_already_cleaned = True
        region_index = getattr(rec_window, "region_index", None)
        closed_idx = int(region_index) - 1 if region_index is not None else -1

        action_items = (
            self.get_action_region_items(all_regions=True)
            if hasattr(self, "get_action_region_items")
            else []
        )
        valid_items = self.get_valid_region_items() if hasattr(self, "get_valid_region_items") else []
        has_other_free_zones = any(idx != closed_idx for idx, _ in action_items) or any(idx != closed_idx for idx, _ in valid_items)
        if not has_other_free_zones:
            self.dismiss_unused_selection()
            return

        if closed_idx >= 0:
            self.recording_region_indices.discard(closed_idx)
            self._cleanup_recorded_region(closed_idx)

        still_recording = any(
            rw is not rec_window and not getattr(rw, "is_saving", False)
            for rw in getattr(self, "recording_windows", [])
        )
        if not still_recording:
            self.is_recording = False
            self.clearMask()
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
            regions = self.__dict__.get("regions", [])
            if regions and hasattr(self, "set_active_region"):
                self.set_active_region(0)
            self._show_toolbars()
            self._update_toolbar_positions()
        else:
            if hasattr(self, "_refresh_single_recording_overlay_mask"):
                self._refresh_single_recording_overlay_mask()

        try:
            if self.isVisible():
                self.repaint()
            else:
                self.update()
        except Exception:
            pass

    # --- Запись живого видео и GIF ---
    def _on_overlay_recording_closed(self, rec_window: RecordingFrameWindow):
        region_index = getattr(rec_window, "region_index", None)
        if region_index is not None:
            self.recording_region_indices.discard(int(region_index) - 1)
        if rec_window in self.recording_windows:
            self.recording_windows.remove(rec_window)
        if self.recording_windows:
            self._update_mass_recording_hud_state()
            self._sync_region_header()
            if hasattr(self, "_refresh_single_recording_overlay_mask"):
                self._refresh_single_recording_overlay_mask()
            self.update()
            return

        hud = self.__dict__.get("recording_hud")
        if hud:
            hud.hide()
        self.is_recording = False
        self._mass_recording = False
        self._recording_selection_regions = []
        if self.__dict__.get("_recording_overlay_closed", False):
            self._mass_recording = False
            self.recording_region_indices.clear()
            self.close_overlay()
            return
        if getattr(self, "_mass_recording", False):
            self._mass_recording = False
            self.close_overlay()
            return

        closed_idx = int(region_index) - 1 if region_index is not None else -1
        action_items = (
            self.get_action_region_items(all_regions=True)
            if hasattr(self, "get_action_region_items")
            else []
        )
        valid_items = self.get_valid_region_items() if hasattr(self, "get_valid_region_items") else []
        has_other_free_zones = any(idx != closed_idx for idx, _ in action_items) or any(idx != closed_idx for idx, _ in valid_items)
        if not has_other_free_zones:
            self.close_overlay()
            return

        if not getattr(rec_window, "_region_already_cleaned", False):
            rec_window._region_already_cleaned = True
            if 0 <= closed_idx < len(self.__dict__.get("regions", [])):
                self._cleanup_recorded_region(closed_idx)

        if hasattr(self, "clearMask"):
            self.clearMask()
        if hasattr(self, "setAttribute"):
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        regions = self.__dict__.get("regions", [])
        if regions:
            new_active = 0
            for idx in range(len(regions)):
                if idx not in getattr(self, "recording_region_indices", set()):
                    new_active = idx
                    break
            if hasattr(self, "set_active_region"):
                self.set_active_region(new_active)

        self._show_toolbars()
        self._update_toolbar_positions()
        self._sync_close_button_tooltip()
        # После финализации GIF окно записи закрывается и больше не может
        # принять Escape. Возвращаем фокус fullscreen overlay, чтобы нажатие
        # Escape закрывало оставшееся затемнение и выбранные зоны.
        self._focus_selection_overlay_after_recording()
        # recording_closed испускается до close() самого окна записи. Повтор
        # в следующем цикле событий нужен именно для Windows: иначе закрытие
        # top-level окна может сразу вернуть фокус в приложение под overlay.
        QTimer.singleShot(0, self._focus_selection_overlay_after_recording)
        self.update()
        try:
            if self.isVisible():
                self.repaint()
        except Exception:
            pass
        QTimer.singleShot(50, self._force_full_dimmed_repaint)

    def _force_full_dimmed_repaint(self):
        """Гарантирует полное восстановление затемненного фона без следов и брешей после закрытия записи."""
        if not self.isVisible() or getattr(self, "is_recording", False):
            return
        if hasattr(self, "clearMask"):
            self.clearMask()
        self.update()
        try:
            self.repaint()
        except Exception:
            pass

    def _focus_selection_overlay_after_recording(self):
        """Возвращает overlay фокус после закрытия отдельного окна записи."""
        if getattr(self, "is_recording", False) or getattr(self, "_recording_overlay_closed", False):
            return
        self.show()
        self.raise_()
        try:
            force_foreground_window(int(self.winId()))
        except Exception:
            pass
        self.activateWindow()
        self.setFocus()

    def dismiss_unused_selection(self):
        """Hide free selection UI while recording windows keep running."""
        if not getattr(self, "is_recording", False):
            self.close_overlay()
            return
        self._recording_overlay_closed = True
        self.clearMask()
        self._hide_toolbars()
        header = self.__dict__.get("region_header")
        if header is not None:
            header.hide()
        self.hide()
        self.update()

    def stop_recording(self, close_selection: bool = True):
        """Останавливает все записи, запущенные из текущего multi-selection."""
        hud = self.__dict__.get("recording_hud")
        if hud:
            hud.hide()
        for rec_window in list(self.recording_windows):
            try:
                rec_window.stop_and_save()
            except Exception:
                pass
        if close_selection:
            # GIF post-processing is asynchronous. Hide unused selection
            # regions immediately instead of keeping their dimming until the
            # encoder finishes writing the file.
            self._recording_overlay_closed = True
            self.close_overlay()

    def _mass_recording_counts(self):
        """Возвращает число активных видеозаписей и GIF отдельно."""
        video_count = 0
        gif_count = 0
        for rec_window in self.__dict__.get("recording_windows", []):
            # После Stop окно ещё живёт до завершения кодирования GIF/MP4,
            # но захват уже закончен и его нельзя показывать как активный.
            if getattr(rec_window, "is_saving", False) or getattr(rec_window, "is_finished", False):
                continue
            mode = getattr(rec_window, "mode", None)
            if mode == "video":
                video_count += 1
            elif mode == "gif":
                gif_count += 1
        return video_count, gif_count

    def _update_mass_recording_hud_state(self):
        """Синхронизирует HUD с реальным захватом, а не с постобработкой."""
        hud = self.__dict__.get("recording_hud")
        if hud is None:
            return
        video_count, gif_count = self._mass_recording_counts()
        if hasattr(hud, "update_counts"):
            hud.update_counts(video_count, gif_count)
        if video_count == 0 and gif_count == 0:
            hud.hide()

    def _toggle_mass_pause(self, mode: str):
        """Поставить на паузу или продолжить записи только одного типа."""
        windows = [
            rec_window for rec_window in self.__dict__.get("recording_windows", [])
            if getattr(rec_window, "mode", None) == mode
        ]
        if not windows:
            return

        should_pause = any(not bool(getattr(rec_window, "is_paused", False)) for rec_window in windows)
        for rec_window in windows:
            setter = getattr(rec_window, "set_paused", None)
            if callable(setter):
                setter(should_pause)
            else:
                worker = getattr(rec_window, "capture_worker", None)
                if worker is not None:
                    (worker.pause if should_pause else worker.resume)()
                rec_window.is_paused = should_pause

        hud = self.__dict__.get("recording_hud")
        if hud:
            hud.set_paused(mode, should_pause)

    def _stop_recording_mode(self, mode: str):
        """Остановить только видео или только GIF, не затрагивая другой тип."""
        for rec_window in list(self.__dict__.get("recording_windows", [])):
            if getattr(rec_window, "mode", None) != mode:
                continue
            try:
                rec_window.stop_and_save()
            except Exception:
                pass
        self._update_mass_recording_hud_state()

    def _keep_mass_recording_hud_on_top(self):
        """Поднимает общую панель после показа/активации окон записи."""
        hud = self.__dict__.get("recording_hud")
        if hud is None or not hud.isVisible():
            return
        hud.raise_()
        ensure_topmost = getattr(hud, "_ensure_topmost", None)
        if callable(ensure_topmost):
            ensure_topmost()

    def _show_mass_recording_hud(self):
        """Показывает независимое управление массовой записью поверх рабочего стола."""
        if self.recording_hud is None:
            self.recording_hud = MassRecordingHud()
            self.recording_hud.stop_clicked.connect(self.stop_recording)
            self.recording_hud.pause_video_clicked.connect(
                lambda: self._toggle_mass_pause("video")
            )
            self.recording_hud.pause_gif_clicked.connect(
                lambda: self._toggle_mass_pause("gif")
            )
            self.recording_hud.stop_video_clicked.connect(
                lambda: self._stop_recording_mode("video")
            )
            self.recording_hud.stop_gif_clicked.connect(
                lambda: self._stop_recording_mode("gif")
            )

        video_count, gif_count = self._mass_recording_counts()
        if video_count or gif_count or not self.recording_windows:
            self.recording_hud.update_counts(video_count, gif_count)
        else:
            # Совместимость со старыми/тестовыми объектами без поля mode.
            self.recording_hud.update_count(len(self.recording_windows))
        screen = QApplication.primaryScreen()
        geo = screen.availableGeometry() if screen is not None else self.geometry()
        self.recording_hud.adjustSize()
        hud_x = geo.left() + max(0, (geo.width() - self.recording_hud.width()) // 2)
        hud_y = geo.top() + 8
        self.recording_hud.move(int(hud_x), int(hud_y))
        self.recording_hud.show()
        self.recording_hud.raise_()
        self._keep_mass_recording_hud_on_top()

    def start_recording(self, mode="video", params=None, all_regions: bool = False):
        global RecordingFrameWindow
        if RecordingFrameWindow is None:
            from .recording_window import RecordingFrameWindow as _RecordingFrameWindow
            RecordingFrameWindow = _RecordingFrameWindow

        params = params or {}
        valid_items = self.get_valid_region_items()
        target_items = self.get_action_region_items(all_regions=all_regions)
        if not target_items:
            return

        self._set_add_region_mode(False)
        self.badge.hide()
        self.text_editor.hide()

        mic = params.get("mic", getattr(self.cfg, "record_mic", True))
        system = params.get("system", getattr(self.cfg, "record_system", True))
        codec = params.get("codec", self.cfg.video_codec)
        target_hwnd = params.get("target_hwnd", self.target_hwnd)
        countdown = params.get("countdown", getattr(self.cfg, "record_countdown_enabled", False))
        countdown_seconds = params.get("countdown_seconds", getattr(self.cfg, "record_countdown_seconds", 3))

        if mode == "gif":
            fps = params.get("fps", getattr(self.cfg, "gif_fps", 15))
            self.cfg.gif_fps = fps
            if "colors" in params:
                self.cfg.gif_colors = params["colors"]
            if "dither" in params:
                self.cfg.gif_dither = params["dither"]
            self.config_mgr.save()
        else:
            fps = params.get("fps", getattr(self.cfg, "video_fps", 30))
            self.cfg.video_fps = fps
            self.config_mgr.save()

        # Для обычной кнопки выбирается только активная зона. Групповое меню
        # запускает независимое окно записи на каждую доступную зону.
        self.is_recording = True
        self._mass_recording = bool(all_regions)
        self._recording_overlay_closed = False
        self._recording_selection_regions = [
            (idx, QRectF(region).normalized()) for idx, region in valid_items
        ]
        self.recording_region_indices.update(idx for idx, _ in target_items)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.clearMask()
        keep_selection_overlay = not all_regions and len(valid_items) > 1
        # RecordingFrameWindow запускает CaptureWorker уже в __init__. Для
        # массовой записи overlay должен исчезнуть до создания первого окна.
        # При записи одной зоны оставляем остальные выбранные зоны видимыми,
        # но вырезаем записываемые прямоугольники из маски overlay.
        if keep_selection_overlay:
            self._hide_selection_toolbars()
            free_indices = [idx for idx, _ in valid_items if idx not in self.recording_region_indices]
            if free_indices:
                self.set_active_region(free_indices[0])
            self._show_selection_overlay_during_single_recording()
        else:
            self._hide_toolbars()
            self.hide()
        QApplication.processEvents()
        self.update()
        QApplication.processEvents()

        region_count = len(valid_items)
        app_inst = getattr(QApplication.instance(), "app_instance", None)
        for region_index_zero, region in target_items:
            region_index = region_index_zero + 1
            region_filter, region_filter_params = self._filter_state_for_region(region_index_zero)
            capture_masks = self.__dict__.get("capture_masks", {}).get(region_index_zero, [])
            recording_masks = []
            for capture_mask in capture_masks:
                # RecordingFrameWindow works in local coordinates of its own
                # rectangle, while OverlayWindow stores masks globally.
                local_mask = capture_mask.clone()
                local_mask.translate(-region.x(), -region.y())
                recording_masks.append(local_mask)
            rec_window = RecordingFrameWindow(
                mode=mode,
                rect=region,
                region_index=region_index,
                region_count=region_count,
                record_mic=mic,
                record_system=system,
                codec=codec,
                target_hwnd=target_hwnd,
                capture_mask=recording_masks,
                filter_type=region_filter,
                filter_params=region_filter_params,
                is_fullscreen=self._is_fullscreen_region(region),
                countdown=countdown,
                countdown_seconds=countdown_seconds,
                overlay_owner=self,
            )
            self.recording_windows.append(rec_window)
            rec_window.recording_closed.connect(
                lambda _path, w=rec_window: self._on_overlay_recording_closed(w)
            )
            geometry_changed = getattr(rec_window, "geometry_changed", None)
            if geometry_changed is not None and hasattr(geometry_changed, "connect"):
                geometry_changed.connect(
                    lambda w=rec_window: self._on_recording_window_geometry_changed(w)
                )
            if app_inst:
                app_inst.add_recording(rec_window)
            rec_window.show()
            if keep_selection_overlay:
                # show() уже установил фактическую геометрию шапки; теперь
                # вырезаем именно её, а не только область захвата.
                self._refresh_single_recording_overlay_mask()

        if all_regions:
            self._show_mass_recording_hud()

        if hasattr(self, "_unclip_timer"):
            self._unclip_timer.stop()
        while QApplication.overrideCursor():
            QApplication.restoreOverrideCursor()
        release_mouse_traps()

        # После фактического старта записи overlay уже был скрыт до запуска
        # CaptureWorker, поэтому он не перехватывает мышь и не попадает в кадр.
        for rec_window in self.recording_windows:
            rec_window.raise_()
        self.recording_windows[-1].activateWindow()
        self._keep_mass_recording_hud_on_top()

    def close_overlay(self):
        try:
            if getattr(self, "recording_hud", None) is not None:
                self.recording_hud.hide()
            if self.__dict__.get("_unclip_timer") is not None:
                self._unclip_timer.stop()
            if self.__dict__.get("right_hold_timer") is not None:
                self.right_hold_timer.stop()
        except Exception:
            pass
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
        self.target_hwnd = None
        self.pending_outside_drag = False
        self.pending_outside_region_idx = None
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
        if hasattr(self, "right_toolbar") and hasattr(self.right_toolbar, "chk_passthrough"):
            self.right_toolbar.chk_passthrough.setChecked(False)
        self.clear_regions()
        self._recording_selection_regions = []
        self.initial_selection = QRectF()
        self._clear_interactive_selection()
        self._set_background_pixmap(None)
        self.dimmed_background_pixmap = None
        self.layers_cache_pixmap = None
        self.temp_shape = None
        self.dragged_shape = None
        self._recording_selection_regions = []
        self.layer_manager.clear()
        self.history_manager.clear()
        if self.isVisible():
            try:
                self.repaint()
            except Exception:
                pass
        self.hide()
        try:
            import ctypes
            ctypes.windll.dwmapi.DwmFlush()
        except Exception:
            pass
        for _ in range(3):
            QApplication.processEvents()
        self.capture_closed.emit()
