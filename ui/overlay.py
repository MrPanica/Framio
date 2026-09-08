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
from .transform_box import ShapeTransformBox, HandleType, rotate_point

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
        self.region_selection_history_before = None
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

        # HWND выбранного окна записи; устанавливается двойным кликом.
        self.target_hwnd = None

        # Длинный скриншот
        self.scrolling_engine = None
        self.scrolling_hud = None

        # Запись видео/GIF
        self.is_recording = False
        self._mass_recording = False
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
        self.bottom_toolbar.lock_toggled.connect(self._on_lock_toggled)
        self.bottom_toolbar.dynamic_bg_toggled.connect(self._on_dynamic_bg_toggled)
        self.bottom_toolbar.passthrough_toggled.connect(self._on_passthrough_toggled)
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

    def _apply_region_history_state(self, region_idx: int, region: QRectF, mask_states=None):
        """Применяет сохранённое состояние рамки и геометрии её масок."""
        if not (0 <= region_idx < len(self.regions)):
            return
        self.regions[region_idx] = QRectF(region)
        if mask_states is not None:
            for target, source in zip(self._capture_masks_for_region(region_idx), mask_states):
                self._apply_shape_geometry(target, source)
        self._refresh_region_geometry(region_idx)

    def _push_region_geometry_history(
        self,
        region_idx: int,
        old_region: QRectF,
        new_region: QRectF,
        old_masks=None,
        new_masks=None,
        description: str | None = None,
    ):
        """Добавляет одно Undo/Redo-действие для итогового жеста зоны."""
        history = getattr(self, "history_manager", None)
        if history is None:
            return
        cmd = HistoryCommand(
            description or tr("hist_cmd_region_transform", "Изменение области записи"),
            do_func=lambda: self._apply_region_history_state(region_idx, new_region, new_masks),
            undo_func=lambda: self._apply_region_history_state(region_idx, old_region, old_masks),
        )
        history.push_already_done(cmd)

    def _capture_region_layout_state(self):
        """Сохраняет список зон и привязанные к ним маски для Undo/Redo."""
        return {
            "regions": [QRectF(region) for region in self.regions],
            "capture_masks": {
                idx: list(masks) for idx, masks in self.capture_masks.items()
            },
            "active_region_idx": self.active_region_idx,
        }

    def _restore_region_layout_state(self, state):
        """Восстанавливает список зон без создания новой команды истории."""
        self.regions = [QRectF(region) for region in state["regions"]]
        self.capture_masks = {
            idx: list(masks) for idx, masks in state["capture_masks"].items()
        }
        self.active_region_idx = state["active_region_idx"]
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
        old_masks = list(getattr(self, "region_transform_initial_masks", []))
        new_masks = [mask.clone() for mask in self._capture_masks_for_region(idx)]
        self.region_transform_initial_idx = None
        self.region_transform_initial_rect = QRectF()
        self.region_transform_initial_masks = []
        if not changed:
            return False

        self._push_region_geometry_history(
            idx,
            old_region,
            new_region,
            old_masks=old_masks,
            new_masks=new_masks,
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
        return [
            (idx, region.normalized())
            for idx, region in enumerate(self.regions)
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
        if 0 <= idx < len(self.regions):
            self.active_region_idx = idx
            self.is_resizing = False
            self.active_handle = HANDLE_NONE
            if hasattr(self, "transform_box"):
                masks = self._capture_masks_for_region(idx)
                self.transform_box.set_shape(masks[-1] if masks else None)
            self._update_toolbar_positions()
            self._sync_close_button_tooltip()
            self.update()

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
        self._sync_passthrough_mask()
        self.update()

    def _sync_passthrough_mask(self):
        """
        Режим «Неосязаемая рамка»: клики мыши внутри рамки выделения
        на 100% проходят сквозь окно в активные программы, сайты и игры.
        При этом контурная рамка, маркеры изменения размера и панели инструментов
        остаются на экране и доступны для управления.
        """
        if getattr(self, "is_passthrough", False):
            valid_regions = self.get_valid_regions()
            if valid_regions:
                mask = QRegion(self.rect())
                for reg in valid_regions:
                    r = reg.toRect()
                    inner_clickable = r.adjusted(4, 4, -4, -4)
                    if inner_clickable.width() > 0 and inner_clickable.height() > 0:
                        mask = mask.subtracted(QRegion(inner_clickable))
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
            is_mask = getattr(self.transform_box.shape, "is_capture_mask", False)
            if not is_mask and self.transform_box.shape not in self.layer_manager.shapes:
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
                # У CaptureMaskShape ``path`` — метод, а не поле геометрии.
                # Не затеняем им одноимённый метод целевого объекта после
                # Undo/Redo: иначе следующий drag меняет точки маски, а
                # отрисовка продолжает брать контур из старой копии.
                if callable(val):
                    continue
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
        self.clear_regions()
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
        self.pending_outside_drag = False
        self.pending_outside_region_idx = None
        self.is_resizing = False
        self.is_recording = False
        self._mass_recording = False
        self.recording_region_indices.clear()
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
        if hasattr(self, "region_header"):
            self.region_header.hide()

    def _show_toolbars(self):
        if self.selection_rect.isValid() and self.selection_rect.width() > 20 and self.selection_rect.height() > 20:
            self._update_toolbar_positions()
            self.right_toolbar.show()
            self.bottom_toolbar.show()
            self.badge.show()
            self._sync_region_header()

    def _update_toolbar_positions(self):
        r = self.selection_rect.normalized()
        w_right = self.right_toolbar.sizeHint().width()
        h_right = self.right_toolbar.sizeHint().height()
        w_bot = self.bottom_toolbar.sizeHint().width()
        h_bot = self.bottom_toolbar.sizeHint().height()

        scr_w = self.width()
        scr_h = self.height()

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
        valid_regions = self.get_valid_regions()
        region_info = f"(#{self.active_region_idx + 1}/{len(valid_regions)})" if len(valid_regions) > 1 else ""
        self.badge.update_dimension(r.width(), r.height(), region_info)
        badge_x = r.left()
        badge_y = r.top() - self.badge.height() - 6
        if badge_y < 4:
            badge_y = r.top() + 6
        self.badge.move(int(badge_x), int(badge_y))

        # Верхняя панель добавления и массовых действий не привязана к одной зоне.
        if hasattr(self, "region_header"):
            self._sync_region_header()
            if self.region_header.isVisible():
                header_w = self.region_header.sizeHint().width()
                header_x = max(6, min((scr_w - header_w) // 2, scr_w - header_w - 6))
                self.region_header.move(int(header_x), 8)

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

        # Обработка правой кнопки мыши: трансформация фигур (масштабирование, вращение, перемещение) или контекстное меню
        if event.button() == Qt.MouseButton.RightButton:
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

                # 3. Клик ПКМ на пустом месте внутри выделения — снимаем рамку трансформации
                if self.transform_box.is_active():
                    self.transform_box.set_shape(None)
                    self.update()
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        valid_regions = self.get_valid_regions()

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
                self.active_region_idx = hit_region_idx
                self.is_resizing = False
                self.active_handle = HANDLE_NONE
                self._update_toolbar_positions()
                self._sync_close_button_tooltip()
                self.update()

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

        # Если рамка есть — проверяем манипуляторы изменения размера активной зоны
        handle = self._hit_test_handles(pos)
        if not self.is_locked and handle != HANDLE_NONE:
            self.is_resizing = True
            self.active_handle = handle
            self.drag_start_pos = pos
            self.initial_selection = QRectF(self.selection_rect)
            self._begin_region_transform_history()
            self._set_handle_cursor(self.active_handle)
            return

        # Проверяем клик по любой неактивной зоне для мгновенного переключения фокуса!
        for idx, reg in enumerate(self.regions):
            if idx != self.active_region_idx and reg.contains(pos):
                self.set_active_region(idx)
                return

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
            if not self.is_locked:
                if self.transform_box.is_active():
                    self.transform_box.set_shape(None)
                    self.update()
                self.is_resizing = True
                self.active_handle = HANDLE_MOVE
                self.drag_start_pos = pos
                self.initial_selection = QRectF(self.selection_rect)
                self._begin_region_transform_history()
                self._set_cursor_if_needed(Qt.CursorShape.ClosedHandCursor)
                return

        # Рисование фигур внутри активной зоны
        if self.selection_rect.contains(pos) and self.current_tool != ToolType.MOVE:
            mask = self._capture_mask_at(pos)
            if mask is not None:
                self.transform_box.set_shape(mask)
                self.is_transforming = True
                self.transform_box.start_drag(HandleType.INSIDE, pos)
                self.shape_drag_initial_pos = pos
                self._set_cursor_if_needed(Qt.CursorShape.SizeAllCursor)
                self.update()
                return

            # 1. Если кликнули по существующему тексту или фигуре — активируем её вместо создания наложения!
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

            if self.transform_box.is_active():
                self.transform_box.set_shape(None)
                self.update()
            self._start_drawing_shape(pos)
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

        # В обычном режиме внешний drag перевыделяет активную зону. Это
        # сохраняет остальные зоны и не превращает каждый внешний drag в
        # третью/четвёртую зону. Ctrl остаётся единственным способом
        # создавать зоны без повторного нажатия «+».
        if valid_regions:
            self.pending_outside_drag = True
            self.pending_outside_pos = QPointF(pos)
            self.pending_outside_region_idx = self.active_region_idx
            return

    def mouseMoveEvent(self, event):
        pos = event.position()

        # Режим «Пипетка»: перемещение лупы вместе с курсором
        if getattr(self, "is_eyedropper_active", False):
            self.eyedropper_pos = pos
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
                self.regions[replace_idx] = replacement
                self.active_region_idx = replace_idx
            else:
                self.capture_masks.clear()
                self.regions = [replacement]
                self.active_region_idx = 0
            self.layer_manager.clear()
            self.history_manager.clear()
            self._invalidate_layers_cache()
            self.clearMask()
            self.is_passthrough = False
            if hasattr(self, "bottom_toolbar"):
                self.bottom_toolbar.chk_passthrough.setChecked(False)
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
            shift_pressed = bool(event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            self._update_drawing_shape(pos, shift_pressed=shift_pressed)
            self._set_cursor_if_needed(self._get_tool_cursor())
            self.update()
            return

        # 5. Обновление курсора при наведении (с учетом выбранного инструмента)
        valid_regions = self.get_valid_regions()
        if valid_regions:
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
        ww = window_rect.right - window_rect.left
        wh = window_rect.bottom - window_rect.top
        if ww <= 60 or wh <= 60:
            return None
        length = user32.GetWindowTextLengthW(root_hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(root_hwnd, buf, length + 1)
        title = buf.value.strip()
        if not title or title == "Program Manager":
            return None

        local_rect = QRectF(
            window_rect.left - self.geometry().x(),
            window_rect.top - self.geometry().y(),
            ww,
            wh,
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

        # Отпускание правой кнопки мыши (запасной выход)
        if event.button() == Qt.MouseButton.RightButton:
            self.right_hold_timer.stop()
            return

        if event.button() != Qt.MouseButton.LeftButton:
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
                    self.active_region_idx = max(0, len(self.regions) - 1)
                    self._show_toolbars()
                    self._update_toolbar_positions()
                    self._sync_close_button_tooltip()
                else:
                    self.clear_regions()
                    self.clearMask()
                    self.is_passthrough = False
                    if hasattr(self, "bottom_toolbar"):
                        self.bottom_toolbar.chk_passthrough.setChecked(False)
                    self._hide_toolbars()
                    self.badge.hide()
            self.update()
            return

        if self.is_resizing:
            self._commit_region_transform_history()
            self.is_resizing = False
            self.active_handle = HANDLE_NONE
            self.selection_rect = self.selection_rect.normalized()
            self._update_toolbar_positions()
            self._sync_close_button_tooltip()
            if getattr(self, "is_passthrough", False):
                self._sync_passthrough_mask()
            if self.selection_rect.contains(pos):
                self.setCursor(Qt.CursorShape.OpenHandCursor if self.current_tool == ToolType.MOVE else Qt.CursorShape.CrossCursor)
            self.update()
            return

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
        masks = self._capture_masks_for_region()
        if masks and old_region.width() > 0 and old_region.height() > 0:
            for mask in masks:
                mask.scale_from_origin(
                    new_region.width() / old_region.width(),
                    new_region.height() / old_region.height(),
                    old_region.topLeft(),
                )
                mask.translate(new_region.left() - old_region.left(), new_region.top() - old_region.top())

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
        elif self.current_tool == ToolType.CAPTURE_MASK:
            kind = cfg.get("mask_kind", "freeform")
            self.temp_shape = CaptureMaskShape(kind=kind, rect=QRectF(pos, pos), color="#38BDF8")
            if kind == "freeform":
                self.temp_shape.add_point(pos)

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

        valid_regions = self.get_valid_regions()
        r = self.selection_rect.normalized() if (self.selection_rect.isValid() and not self.selection_rect.isEmpty()) else None
        w, h = self.width(), self.height()

        # Режим 1: Живая запись видео или GIF
        if self.is_recording:
            dim = QBrush(QColor(0, 0, 0, 110))
            valid_items = self.get_valid_region_items()
            if valid_items:
                screen_region = QRegion(0, 0, w, h)
                for _, reg in valid_items:
                    screen_region = screen_region.subtracted(QRegion(reg.toRect()))
                painter.save()
                painter.setClipRegion(screen_region)
                painter.fillRect(self.rect(), dim)
                painter.restore()

                painter.setBrush(Qt.BrushStyle.NoBrush)
                recording_indices = set(getattr(self, "recording_region_indices", set()))
                for display_idx, (region_idx, reg) in enumerate(valid_items):
                    is_rec = region_idx in recording_indices
                    pen_rec = QPen(
                        QColor(235, 59, 90) if is_rec else QColor(96, 165, 250),
                        2 if is_rec else 1.2,
                        Qt.PenStyle.SolidLine if is_rec else Qt.PenStyle.DashLine,
                    )
                    painter.setPen(pen_rec)
                    outer_border = QRectF(reg.left() - 2, reg.top() - 2, reg.width() + 4, reg.height() + 4)
                    painter.drawRect(outer_border)
                    if len(valid_items) > 1:
                        self._draw_region_index_badge(painter, reg, display_idx + 1, region_idx == self.active_region_idx)
            else:
                painter.fillRect(self.rect(), dim)

            active_recording = self.active_region_idx in set(getattr(self, "recording_region_indices", set()))
            if not active_recording and not self.is_locked and r is not None:
                self._draw_handles(painter, r)

            self.layer_manager.draw_all(painter)
            active_masks = self._capture_masks_for_region()
            for active_mask in active_masks:
                active_mask.draw(painter)
            if self.temp_shape is not None:
                self.temp_shape.draw(painter)
            self._draw_dragged_shape_indicator(painter)
            return

        # Режим 2: Режим скриншота и аннотаций
        if self.dimmed_background_pixmap is None and self.background_pixmap is None and not self.dynamic_bg and not getattr(self, "is_passthrough", False):
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
            else:
                painter.fillRect(self.rect(), QColor(0, 0, 0, 70))
        elif not self.dynamic_bg:
            if self.dimmed_background_pixmap is not None:
                painter.drawPixmap(0, 0, self.dimmed_background_pixmap)
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
            # Если выбран общий фильтр
            if self.current_filter != FilterType.NONE:
                for reg in valid_regions:
                    self._paint_filtered_region(painter, reg)

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
            if not self.is_locked and r is not None:
                self._draw_handles(painter, r)

            # Отрисовываем слои аннотаций
            self.layer_manager.draw_all(painter, source_pixmap=self.background_pixmap)

            active_masks = self._capture_masks_for_region()
            for active_mask in active_masks:
                active_mask.draw(painter)

            # Временная фигура при рисовании
            if self.temp_shape is not None:
                self.temp_shape.draw(painter, source_pixmap=self.background_pixmap)

            # Рамка и плашка с номером фигуры при перемещении ПКМ
            self._draw_dragged_shape_indicator(painter)

            # Подсветка всех интерактивных объектов при удержании клавиши Alt
            if getattr(self, "is_highlighting_objects", False):
                self._draw_interactive_objects_highlight(painter)
        # Экранная лупа с палитрой при активной пипетке
        if getattr(self, "is_eyedropper_active", False):
            self._draw_eyedropper_loupe(painter)

    def _draw_dragged_shape_indicator(self, painter: QPainter):
        """Отрисовывает рамку трансформации в стиле Photoshop / Figma вокруг активной фигуры."""
        active_shape = None
        if hasattr(self, "transform_box") and self.transform_box.is_active():
            active_shape = self.transform_box.shape
        elif getattr(self, "active_editing_shape", None):
            active_shape = self.active_editing_shape

        if active_shape is None or not active_shape.visible:
            return
        if not getattr(active_shape, "is_capture_mask", False) and active_shape not in self.layer_manager.shapes:
            return

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

    def _paint_filtered_region(self, painter: QPainter, rect: QRectF):
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
            tag_text = self._get_shape_display_name(s)
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
            if self.is_recording:
                self.stop_recording()
                return
            if self.is_adding_region:
                self._set_add_region_mode(False)
                return
            if len(self.get_valid_regions()) > 1:
                self.close_active_region()
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
            if getattr(self, "last_active_shape", None) is not None and self.last_active_shape in self.layer_manager.shapes:
                self._delete_shape(self.last_active_shape)
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
        self.active_region_idx = 0
        self._set_add_region_mode(False)
        self.target_hwnd = None
        self.right_toolbar.select_tool(ToolType.MOVE, show_options=False)
        self._show_toolbars()
        self._update_toolbar_positions()
        self._sync_close_button_tooltip()
        self.update()

    # --- Инструменты и панели ---
    def _on_tool_changed(self, tool_type):
        self.current_tool = tool_type
        if tool_type == ToolType.CAPTURE_MASK:
            masks = self._capture_masks_for_region()
            self.transform_box.set_shape(masks[-1] if masks else None)
        if tool_type == ToolType.MOVE:
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.selection_rect.isValid() else Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def _on_capture_mask_chosen(self, kind: str):
        """Синхронизирует выбранную форму маски с текущим активным инструментом."""
        self.right_toolbar.tools_config.setdefault(ToolType.CAPTURE_MASK, {})["mask_kind"] = kind
        self.current_tool = ToolType.CAPTURE_MASK
        masks = self._capture_masks_for_region()
        self.transform_box.set_shape(masks[-1] if masks else None)
        self._set_cursor_if_needed(Qt.CursorShape.CrossCursor)
        self.update()

    def _apply_filter_state(self, filter_type: str):
        self.current_filter = filter_type
        if self.capture_worker:
            self.capture_worker.set_filter(filter_type)
        if hasattr(self, "bottom_toolbar") and hasattr(self.bottom_toolbar, "sync_filter"):
            self.bottom_toolbar.sync_filter(filter_type)
        self._invalidate_layers_cache()
        self.update()

    def _on_filter_changed(self, filter_type: str, from_history: bool = False):
        if filter_type == self.current_filter:
            return
        old_filter = self.current_filter
        self._apply_filter_state(filter_type)

        if not from_history:
            from utils.image_filters import get_localized_filter_names
            flt_names = get_localized_filter_names()
            f_label = flt_names.get(filter_type, filter_type)
            cmd = HistoryCommand(
                tr("hist_cmd_filter", "Фильтр: {name}", name=f_label),
                do_func=lambda f=filter_type: self._apply_filter_state(f),
                undo_func=lambda f=old_filter: self._apply_filter_state(f)
            )
            self.history_manager.push_already_done(cmd)

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

        if self.current_filter != FilterType.NONE:
            import cv2

            bgr = qimage_to_cv2_bgr(crop_pix.toImage())
            fbgr = apply_filter(bgr, self.current_filter)
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
            self.close_overlay()
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
        self.hide()
        QApplication.processEvents()
        images = self.get_cropped_images(all_regions=all_regions)
        if not images:
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
    def _on_overlay_recording_closed(self, rec_window: RecordingFrameWindow):
        region_index = getattr(rec_window, "region_index", None)
        if region_index is not None:
            self.recording_region_indices.discard(int(region_index) - 1)
        if rec_window in self.recording_windows:
            self.recording_windows.remove(rec_window)
        if self.recording_windows:
            hud = self.__dict__.get("recording_hud")
            if hud:
                video_count, gif_count = self._mass_recording_counts()
                hud.update_counts(video_count, gif_count)
            self._sync_region_header()
            self.update()
            return

        hud = self.__dict__.get("recording_hud")
        if hud:
            hud.hide()
        self.is_recording = False
        if getattr(self, "_mass_recording", False):
            self._mass_recording = False
            self.close_overlay()
            return
        # Обычная запись оставляет overlay только если действительно есть
        # другие свободные зоны для дальнейших действий. Иначе скрываем
        # затемнение, рамку и старый фон так же, как после массовой записи.
        if not self.get_action_region_items(all_regions=True):
            self.close_overlay()
            return
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._show_toolbars()
        self._update_toolbar_positions()
        self._sync_close_button_tooltip()
        self.update()

    def stop_recording(self):
        """Останавливает все записи, запущенные из текущего multi-selection."""
        hud = self.__dict__.get("recording_hud")
        if hud:
            hud.hide()
        for rec_window in list(self.recording_windows):
            try:
                rec_window.stop_and_save()
            except Exception:
                pass

    def _mass_recording_counts(self):
        """Возвращает число активных видеозаписей и GIF отдельно."""
        video_count = 0
        gif_count = 0
        for rec_window in self.__dict__.get("recording_windows", []):
            mode = getattr(rec_window, "mode", None)
            if mode == "video":
                video_count += 1
            elif mode == "gif":
                gif_count += 1
        return video_count, gif_count

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
        self.recording_region_indices.update(idx for idx, _ in target_items)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.clearMask()
        # RecordingFrameWindow запускает CaptureWorker уже в __init__. Поэтому
        # overlay и верхняя панель должны исчезнуть до создания первого окна,
        # иначе первые кадры массовой записи могут содержать интерфейс Framio.
        self._hide_toolbars()
        self.hide()
        QApplication.processEvents()
        self.update()
        QApplication.processEvents()

        region_count = len(valid_items)
        app_inst = getattr(QApplication.instance(), "app_instance", None)
        for region_index_zero, region in target_items:
            region_index = region_index_zero + 1
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
                is_fullscreen=self._is_fullscreen_region(region),
                countdown=countdown,
                countdown_seconds=countdown_seconds
            )
            self.recording_windows.append(rec_window)
            rec_window.recording_closed.connect(
                lambda _path, w=rec_window: self._on_overlay_recording_closed(w)
            )
            if app_inst:
                app_inst.add_recording(rec_window)
            rec_window.show()

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
        if getattr(self, "recording_hud", None) is not None:
            self.recording_hud.hide()
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
        self.clear_regions()
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
