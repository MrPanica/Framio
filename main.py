# -*- coding: utf-8 -*-
"""
Главная точка входа в приложение Framio.
Поддерживает системный трей, глобальные хоткеи, захват экрана,
а также живую запись видео и GIF в виде нативного Windows приложения.
"""

from __future__ import annotations

import sys
import os
import ctypes
import subprocess
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from PyQt6.QtCore import (
    Qt, QObject, pyqtSignal, pyqtSlot, QTimer, QPointF, QRectF,
    QMimeData, QUrl, QBuffer, QIODevice, QPoint, QSize,
)
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QWidgetAction,
    QWidget, QFrame, QLabel, QPushButton, QScrollArea,
    QHBoxLayout, QVBoxLayout, QSizePolicy
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QAction, QPen, QBrush, QLinearGradient, QImage, QDrag
)

from datetime import datetime
from config import ConfigManager, DEFAULT_HOTKEY_SCREENSHOT
from ui.overlay import OverlayWindow
from ui.settings_dialog import SettingsDialog
from utils.hotkey_manager import GlobalHotkeyManager
from utils.screen_lock import safe_grab_screen_pixmap
from utils.sound import play_capture_sound
from utils.capture_temp_cleanup import cleanup_stale_capture_temp_files
from utils.pyinstaller_temp_cleanup import cleanup_stale_pyinstaller_temp_dirs
from utils.i18n import tr
from utils.image_search import search_by_image
from ui.icons import create_themed_icon

def make_app_icon(is_recording: bool = False):
    """Генерирует аккуратную векторную иконку приложения Framio (видоискатель с линзой фокуса)."""
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # 1. Скругленный фон с градиентом
    grad = QLinearGradient(0, 0, 64, 64)
    if is_recording:
        grad.setColorAt(0.0, QColor("#e11d48"))
        grad.setColorAt(1.0, QColor("#be123c"))
    else:
        grad.setColorAt(0.0, QColor("#2563eb"))
        grad.setColorAt(1.0, QColor("#06b6d4"))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(grad))
    painter.drawRoundedRect(QRectF(3, 3, 58, 58), 14, 14)

    # 2. Угловые скобки видоискателя (рамка захвата)
    pen_corners = QPen(QColor(255, 255, 255), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen_corners)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    c = 7.0
    # Верх-лево
    painter.drawLine(QPointF(17, 17 + c), QPointF(17, 17))
    painter.drawLine(QPointF(17, 17), QPointF(17 + c, 17))
    # Верх-право
    painter.drawLine(QPointF(47 - c, 17), QPointF(47, 17))
    painter.drawLine(QPointF(47, 17), QPointF(47, 17 + c))
    # Низ-лево
    painter.drawLine(QPointF(17, 47 - c), QPointF(17, 47))
    painter.drawLine(QPointF(17, 47), QPointF(17 + c, 47))
    # Низ-право
    painter.drawLine(QPointF(47 - c, 47), QPointF(47, 47))
    painter.drawLine(QPointF(47, 47), QPointF(47, 47 - c))

    # 3. Центральное кольцо фокуса и точка
    painter.setPen(QPen(QColor(255, 255, 255, 230), 2.0))
    painter.drawEllipse(QPointF(32, 32), 7.5, 7.5)

    dot_color = QColor("#ff2222") if is_recording else QColor(255, 255, 255)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(dot_color))
    painter.drawEllipse(QPointF(32, 32), 3.5, 3.5)

    # 4. Индикатор записи
    if is_recording:
        painter.setPen(QPen(QColor(255, 255, 255), 1.5))
        painter.setBrush(QBrush(QColor(255, 0, 0)))
        painter.drawEllipse(45, 7, 12, 12)

    painter.end()
    return QIcon(pix)


class MediaPreviewLabel(QLabel):
    clicked = pyqtSignal()
    context_menu_requested = pyqtSignal(QPoint)

    def __init__(self, item=None, image=None, parent=None):
        super().__init__(parent)
        self.item = item or {}
        self.drag_image = image
        self._drag_start = None
        self._drag_started = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._drag_start is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._drag_start).manhattanLength() >= QApplication.startDragDistance()
        ):
            path = str(self.item.get("path", "") or "")
            if not path and (self.drag_image is None or self.drag_image.isNull()):
                return

            mime = QMimeData()
            if path:
                mime.setUrls([QUrl.fromLocalFile(path)])
            if self.drag_image is not None and not self.drag_image.isNull():
                mime.setImageData(self.drag_image)

            drag = QDrag(self)
            drag.setMimeData(mime)
            pixmap = self.pixmap()
            if pixmap is not None and not pixmap.isNull():
                drag.setPixmap(pixmap)
            self._drag_started = True
            self._drag_start = None
            drag.exec(Qt.DropAction.CopyAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._drag_started:
                self.clicked.emit()
            self._drag_start = None
            self._drag_started = False
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event):
        self.context_menu_requested.emit(event.globalPosition().toPoint())
        event.accept()


class RecentMediaPanel(QWidget):
    """Панель последних материалов с фильтром, превью и действиями."""

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    GIF_SUFFIXES = {".gif"}
    VIDEO_SUFFIXES = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
    PREVIEW_WIDTH = 320
    PREVIEW_HEIGHT = 180

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.active_filter = "all"
        self.filter_buttons = {}
        self.setFixedWidth(360)
        self.setStyleSheet("""
            QWidget#recentMediaPanel { background: #18181b; color: #f4f4f5; }
            QPushButton {
                background: #27272a; color: #e4e4e7; border: 1px solid #3f3f46;
                border-radius: 4px; padding: 1px 3px; min-height: 22px; max-height: 22px;
            }
            QPushButton:hover { background: #3f3f46; }
            QPushButton:checked { background: #2563eb; border-color: #60a5fa; color: #fff; }
            QLabel#recentMediaTitle { color: #a1a1aa; font-size: 11px; }
            QFrame#recentMediaCard { background: #202023; border: 1px solid #3f3f46; border-radius: 6px; }
            QScrollArea { border: 0; background: #18181b; }
            QScrollBar:vertical { background: #27272a; width: 12px; margin: 2px; }
            QScrollBar::handle:vertical { background: #52525b; border-radius: 5px; min-height: 28px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        """)
        self.setObjectName("recentMediaPanel")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(7)

        title = QLabel(tr("recent_media_title", "Последние материалы"))
        title.setObjectName("recentMediaTitle")
        root.addWidget(title)

        filters = QHBoxLayout()
        filters.setSpacing(5)
        for key, text_key, fallback in (
            ("all", "recent_filter_all", "Все"),
            ("screenshots", "recent_filter_screenshots", "Скриншоты"),
            ("gifs", "recent_filter_gifs", "GIF"),
            ("videos", "recent_filter_videos", "Видео"),
        ):
            button = QPushButton(tr(text_key, fallback))
            button.setCheckable(True)
            button.clicked.connect(lambda checked=False, value=key: self._set_filter(value))
            filters.addWidget(button)
            self.filter_buttons[key] = button
        filters.addStretch()
        root.addLayout(filters)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFixedHeight(420)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(2, 2, 2, 2)
        self.content_layout.setSpacing(7)
        self.content_layout.addStretch()
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)
        self._set_filter("all")

    @classmethod
    def media_kind(cls, item: dict) -> str:
        suffix = Path(item.get("path", "")).suffix.lower()
        if suffix in cls.GIF_SUFFIXES:
            return "gifs"
        if suffix in cls.VIDEO_SUFFIXES:
            return "videos"
        return "screenshots"

    @classmethod
    def _load_preview(cls, item: dict) -> QImage | None:
        image = item.get("image")
        if image is not None and not image.isNull():
            return image
        path = item.get("path", "")
        if not path or not Path(path).exists():
            return None
        suffix = Path(path).suffix.lower()
        if suffix in cls.IMAGE_SUFFIXES or suffix in cls.GIF_SUFFIXES:
            loaded = QImage(path)
            return loaded if not loaded.isNull() else None
        if suffix in cls.VIDEO_SUFFIXES:
            try:
                import cv2
                capture = cv2.VideoCapture(path)
                ok, frame = capture.read()
                capture.release()
                if ok and frame is not None and frame.size:
                    height, width = frame.shape[:2]
                    return QImage(
                        frame.data, width, height, int(frame.strides[0]),
                        QImage.Format.Format_BGR888,
                    ).copy()
            except Exception:
                pass
        return None

    def _set_filter(self, value: str):
        self.active_filter = value
        for key, button in self.filter_buttons.items():
            button.setChecked(key == value)
        self._rebuild()

    def _clear_content(self):
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild(self):
        self._clear_content()
        items = [
            item for item in self.owner.recent_media
            if self.active_filter == "all" or self.media_kind(item) == self.active_filter
        ]
        if not items:
            empty = QLabel(tr("recent_filter_empty", "Нет материалов этого типа"))
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #71717a; padding: 24px;")
            self.content_layout.addWidget(empty)
        else:
            for item in items:
                self.content_layout.addWidget(self._make_card(item))
        self.content_layout.addStretch()

    def _make_card(self, item: dict) -> QFrame:
        card = QFrame()
        card.setObjectName("recentMediaCard")
        # Все действия находятся в одной компактной строке под превью.
        card.setFixedHeight(260)
        column = QVBoxLayout(card)
        column.setContentsMargins(6, 6, 6, 6)
        column.setSpacing(5)

        full_label = str(item.get("label", ""))
        title = QLabel()
        title.setObjectName("recentMediaLabel")
        title.setFixedHeight(19)
        title.setStyleSheet("color: #f4f4f5; font-weight: 600;")
        title.setText(title.fontMetrics().elidedText(full_label, Qt.TextElideMode.ElideRight, self.PREVIEW_WIDTH))
        path = str(item.get("path", "") or "")
        title.setToolTip(f"{full_label}\n{path}" if path else full_label)
        column.addWidget(title)

        image = self._load_preview(item)
        preview = MediaPreviewLabel(item=item, image=image)
        preview.setObjectName("recentMediaPreview")
        preview.setFixedSize(self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview.setStyleSheet("background: #09090b; border-radius: 4px; color: #71717a;")
        if image is not None and not image.isNull():
            pixmap = QPixmap.fromImage(image).scaled(
                preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            preview.setPixmap(pixmap)
        else:
            preview.setText(tr("recent_preview_unavailable", "Нет превью"))
        preview.clicked.connect(lambda entry=item: self.owner._view_recent_media(entry))
        preview.context_menu_requested.connect(
            lambda global_pos, entry=item: self.owner._show_recent_media_context_menu(entry, global_pos)
        )
        column.addWidget(preview)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        for method, icon_name, key, fallback, tip_key, tip_fallback, args in (
            (
                "_copy_recent_media", "copy", "recent_action_copy", "Копировать",
                "recent_action_copy_tip", "Копировать материал", (),
            ),
            (
                "_view_recent_media", "eye", "recent_action_view", "Просмотр",
                "recent_action_view_tip", "Открыть материал для просмотра", (),
            ),
            (
                "_search_recent_media", "search", "recent_action_google", "Google",
                "recent_action_google_tip", "Искать эту картинку в Google Lens", ("google",),
            ),
            (
                "_search_recent_media", "search", "recent_action_yandex", "Yandex",
                "recent_action_yandex_tip", "Искать эту картинку в Яндекс.Картинках", ("yandex",),
            ),
        ):
            button = QPushButton()
            button.setObjectName("recentMediaActionButton")
            button.setFixedSize(28, 26)
            button.setIcon(create_themed_icon(icon_name, is_dark=True, size=16))
            button.setIconSize(QSize(16, 16))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setToolTip(tr(tip_key, tip_fallback))
            button.setAccessibleName(tr(key, fallback))
            button.clicked.connect(
                lambda checked=False, entry=item, name=method, call_args=args:
                getattr(self.owner, name)(entry, *call_args)
            )
            actions.addWidget(button)

        # Текстовую кнопку оставляем последней: она понятнее иконки для
        # системного действия Windows «Открыть с помощью…».
        open_with = QPushButton(tr("recent_action_open_with", "Открыть с помощью..."))
        open_with.setObjectName("recentMediaOpenWithButton")
        open_with.setCursor(Qt.CursorShape.PointingHandCursor)
        open_with.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        open_with.setToolTip(tr("recent_action_open_with_tip", "Выбрать приложение для открытия файла"))
        open_with.clicked.connect(lambda checked=False, entry=item: self.owner._open_recent_media_with(entry))
        actions.addWidget(open_with)
        actions.addStretch(1)
        column.addLayout(actions)
        return card


class FramioApp(QObject):
    def __init__(self):
        super().__init__()
        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config
        self._last_notification_path: str | None = None

        # Окно оверлея
        self.overlay = OverlayWindow()
        self.active_recordings: list[RecordingFrameWindow] = []
        self.processing_tasks: dict[str, dict] = {}
        self.recent_media: list[dict] = []
        self._last_notification_path = ""
        self._mass_saved_pending: list[tuple[str, str]] = []
        self._mass_saved_notification_timer = QTimer(self)
        self._mass_saved_notification_timer.setSingleShot(True)
        self._mass_saved_notification_timer.timeout.connect(self._flush_mass_saved_notification)
        # Старые недописанные промежуточные MP4/WAV не должны накапливаться
        # в TEMP после аварийного завершения или принудительного закрытия.
        cleanup_stale_capture_temp_files()
        # One-file сборки PyInstaller оставляют _MEI* только после аварийных
        # завершений. Чистим старые каталоги при запуске, не затрагивая
        # текущий распакованный каталог и занятые файлы.
        cleanup_stale_pyinstaller_temp_dirs()
        self._load_recent_media()

        # Системный трей
        self.icon = make_app_icon(is_recording=False)
        self.tray = QSystemTrayIcon(self.icon)
        self._update_tray_state()
        self.tray.show()

        # Глобальные горячие клавиши
        self.hotkey_mgr = GlobalHotkeyManager(
            hotkey_capture=self.cfg.hotkey_capture,
            hotkey_quick_fullscreen=getattr(self.cfg, "hotkey_quick_fullscreen", "Ctrl+Print Screen"),
            hotkey_screenshot=getattr(self.cfg, "hotkey_screenshot", DEFAULT_HOTKEY_SCREENSHOT),
            hotkey_record_fullscreen=getattr(self.cfg, "hotkey_record_fullscreen", "Ctrl+Shift+F9"),
            hotkey_stop_recording=getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
        )
        self.hotkey_mgr.capture_triggered.connect(self.trigger_capture)
        self.hotkey_mgr.quick_fullscreen_triggered.connect(self.quick_fullscreen_capture)
        self.hotkey_mgr.screenshot_triggered.connect(self.quick_fullscreen_capture)
        self.hotkey_mgr.record_fullscreen_triggered.connect(self.start_fullscreen_recording)
        self.hotkey_mgr.stop_recording_triggered.connect(self.stop_all_recordings)
        self.hotkey_mgr.start()

        # Клик по трею и по уведомлениям
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self._on_tray_notification_clicked)

        if getattr(self.config_mgr, "storage_warning", False):
            QTimer.singleShot(700, self._show_storage_warning)

    def add_recording(self, rec_window: RecordingFrameWindow):
        if rec_window not in self.active_recordings:
            self.active_recordings.append(rec_window)
            rec_window.recording_closed.connect(lambda path, w=rec_window: self._on_recording_saved(w, path))
            rec_window.save_progress.connect(self._on_recording_progress)
            self._update_tray_state()

    def add_recent_media(self, path: str = None, image: QImage = None, label: str = None, refresh: bool = True):
        """Добавляет материал в историю последних файлов и снимков."""
        if not path and (image is None or image.isNull()):
            return
        path = str(path) if path else ""
        if not path and image is not None and not image.isNull():
            cfg = getattr(self, "cfg", None)
            cache_dir = Path(getattr(cfg, "save_dir_screenshots", "")) / ".recent"
            if str(cache_dir) != ".recent":
                try:
                    cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_path = cache_dir / f"Framio_Recent_{time.time_ns()}.png"
                    if image.save(str(cache_path), "PNG"):
                        path = str(cache_path)
                except Exception:
                    pass
        if not label:
            label = Path(path).name if path else tr("tray_menu_recent_copy_image", name="Screenshot")
        item = {
            "label": label,
            "path": path,
            "image": image.copy() if image is not None and not image.isNull() else None,
        }
        item["kind"] = RecentMediaPanel.media_kind(item)
        if path:
            self.recent_media = [entry for entry in self.recent_media if entry.get("path") != path]
        self.recent_media.insert(0, item)
        self.recent_media = self.recent_media[:50]
        if refresh:
            self._setup_tray_menu()

    def _load_recent_media(self):
        """Восстанавливает последние сохранённые файлы после перезапуска."""
        folders = (
            (getattr(self.cfg, "save_dir_screenshots", ""), RecentMediaPanel.IMAGE_SUFFIXES),
            (getattr(self.cfg, "save_dir_screenshots", ""), {".gif"}),
            (getattr(self.cfg, "save_dir_videos", ""), RecentMediaPanel.VIDEO_SUFFIXES),
            (getattr(self.cfg, "save_dir_gifs", ""), RecentMediaPanel.GIF_SUFFIXES),
        )
        candidates = []
        for folder, suffixes in folders:
            if not folder:
                continue
            try:
                candidates.extend(
                    path for path in Path(folder).rglob("*")
                    if path.is_file() and path.suffix.lower() in suffixes
                )
            except OSError:
                continue
        candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        seen = set()
        for path in candidates:
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            self.add_recent_media(path=str(path), label=path.name, refresh=False)
            if len(self.recent_media) >= 50:
                break

    def _copy_recent_media(self, item: dict):
        """Копирует изображение или URL медиафайла в системный буфер обмена."""
        image = item.get("image")
        path = item.get("path", "")
        if image is None and path and Path(path).suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            loaded = QImage(path)
            image = loaded if not loaded.isNull() else None
        if image is not None and not image.isNull():
            QApplication.clipboard().setImage(image)
            return
        if path:
            mime = QMimeData()
            mime.setUrls([QUrl.fromLocalFile(path)])
            QApplication.clipboard().setMimeData(mime)

    @staticmethod
    def _recent_media_png_bytes(item: dict) -> bytes | None:
        """Возвращает PNG текущего материала для прямого image-search."""
        image = RecentMediaPanel._load_preview(item)
        if image is None or image.isNull():
            return None
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            buffer.close()
            return None
        payload = bytes(buffer.data())
        buffer.close()
        return payload or None

    def _search_recent_media(self, item: dict, engine: str):
        """Отправляет материал напрямую в Google Lens или Яндекс.Картинки."""
        png_bytes = self._recent_media_png_bytes(item)
        if not png_bytes:
            return
        engine_name = "Google Lens" if engine == "google" else "Яндекс.Картинки"
        self.show_notification(
            tr("recent_search_title", "Поиск по изображению"),
            tr("recent_search_started", "Отправка материала в {engine}...", engine=engine_name),
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )
        threading.Thread(
            target=lambda: search_by_image(
                engine,
                png_bytes,
                notify_func=lambda title, message: self.show_notification(title, message),
            ),
            daemon=True,
            name=f"framio-recent-search-{engine}",
        ).start()

    def _recent_media_path(self, item: dict) -> Path | None:
        """Гарантирует локальный путь материала для открытия и Open With."""
        path = Path(item.get("path", "")) if item.get("path") else None
        if path is not None and path.exists():
            return path
        image = item.get("image")
        if image is None or image.isNull():
            return None
        cfg = getattr(self, "cfg", None)
        save_dir = getattr(cfg, "save_dir_screenshots", "")
        if not save_dir:
            return None
        cache_dir = Path(save_dir) / ".recent"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            path = cache_dir / f"Framio_Recent_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.png"
            return path if image.save(str(path), "PNG") else None
        except OSError:
            return None

    def _close_tray_menus(self):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMenu):
                widget.close()

    def _view_recent_media(self, item: dict):
        """Открывает файл стандартным приложением Windows."""
        path = self._recent_media_path(item)
        if path is None:
            return
        self._close_tray_menus()
        try:
            os.startfile(str(path))
        except AttributeError:
            subprocess.Popen([str(path)])
        except Exception as exc:
            print(f"[Framio] Ошибка открытия материала: {exc}")

    def _open_recent_media_with(self, item: dict):
        """Открывает системный диалог Windows «Открыть с помощью»."""
        path = self._recent_media_path(item)
        if path is None:
            return
        self._close_tray_menus()
        if sys.platform == "win32":
            try:
                subprocess.Popen([
                    "rundll32.exe",
                    "shell32.dll,OpenAs_RunDLL",
                    str(path),
                ])
            except Exception as exc:
                print(f"[Framio] Ошибка запуска «Открыть с помощью»: {exc}")
        else:
            self._view_recent_media({**item, "path": str(path)})

    def _show_recent_media_context_menu(self, item: dict, global_pos: QPoint):
        """Показывает меню карточки с системным диалогом Open With Windows."""
        path = self._recent_media_path(item)
        if path is None:
            return
        context_menu = QMenu()
        open_action = context_menu.addAction(tr("recent_context_open", "Открыть"))
        open_with_action = context_menu.addAction(
            tr("recent_context_open_with", "Открыть с помощью...")
        )
        context_menu.addSeparator()
        copy_action = context_menu.addAction(tr("recent_context_copy_path", "Копировать путь"))
        chosen = context_menu.exec(global_pos)
        if chosen == open_action:
            self._view_recent_media({**item, "path": str(path)})
        elif chosen == open_with_action:
            self._open_recent_media_with({**item, "path": str(path)})
        elif chosen == copy_action:
            QApplication.clipboard().setText(str(path))

    def _recent_media_menu(self, menu: QMenu):
        recent_menu = menu.addMenu(tr("tray_menu_recent_media", "Последние материалы Framio"))
        if not self.recent_media:
            empty = QAction(tr("tray_menu_recent_empty", "Пока нет сохранённых материалов"), recent_menu)
            empty.setEnabled(False)
            recent_menu.addAction(empty)
            return
        panel_action = QWidgetAction(recent_menu)
        panel_action.setDefaultWidget(RecentMediaPanel(self, recent_menu))
        recent_menu.addAction(panel_action)

    @pyqtSlot(str, int, str)
    def _on_recording_progress(self, filename: str, percent: int, stage: str):
        if percent >= 100:
            self.processing_tasks.pop(filename, None)
        else:
            self.processing_tasks[filename] = {"percent": percent, "stage": stage}
        self._update_tray_state()

    def show_notification(self, title: str, message: str, icon=None, timeout: int = 4000, target_path: str = None):
        if not self.tray or not self.tray.isVisible():
            return
        if icon is None:
            icon = QSystemTrayIcon.MessageIcon.Information
        if target_path:
            self._last_notification_path = str(target_path)
        QTimer.singleShot(0, lambda: self.tray.showMessage(title, message, icon, timeout))

    def _on_tray_notification_clicked(self):
        if self._last_notification_path:
            p = Path(self._last_notification_path)
            if p.exists():
                try:
                    subprocess.Popen(f'explorer /select,"{os.path.normpath(str(p))}"')
                except Exception as e:
                    print(f"[Main] Ошибка при открытии проводника: {e}")

    def _on_recording_saved(self, rec_window: RecordingFrameWindow, path: str):
        if rec_window in self.active_recordings:
            self.active_recordings.remove(rec_window)
        if path:
            fname = Path(path).name
            self.processing_tasks.pop(fname, None)
        self._update_tray_state()

        if path and Path(path).exists():
            p = Path(path)
            self.add_recent_media(path=str(p), label=p.name)
            if getattr(rec_window, "region_count", 1) > 1:
                self._queue_mass_saved_notification(
                    "gif" if p.suffix.lower() == ".gif" else "video",
                    str(p),
                )
                return
            fname = p.name
            folder = str(p.parent)
            is_gif = p.suffix.lower() == ".gif"
            title = tr("notif_gif_saved_title") if is_gif else tr("notif_video_saved_title")
            self.show_notification(
                title,
                tr("notif_video_saved_body", filename=fname, folder=folder),
                QSystemTrayIcon.MessageIcon.Information,
                5000,
                target_path=path
            )
        elif path == "":
            self.show_notification(
                tr("notif_rec_cancelled_title"),
                tr("notif_rec_cancelled_body"),
                QSystemTrayIcon.MessageIcon.Warning,
                3000
            )

    def _queue_mass_saved_notification(self, mode: str, path: str):
        self._mass_saved_pending.append((mode, path))
        self._mass_saved_notification_timer.start(600)

    def _flush_mass_saved_notification(self):
        """Показывает одно итоговое уведомление после завершения всей группы."""
        # Через __dict__ поддерживаем лёгкие unit-тесты, где QObject не
        # запускает свой конструктор и getattr может бросить RuntimeError.
        active = self.__dict__.get("active_recordings", [])
        if any(getattr(window, "region_count", 1) > 1 for window in active):
            self._mass_saved_notification_timer.start(600)
            return

        pending = list(self.__dict__.get("_mass_saved_pending", []))
        self._mass_saved_pending = []
        if not pending:
            return

        counts = {"video": 0, "gif": 0}
        for mode, _path in pending:
            counts[mode] = counts.get(mode, 0) + 1
        message = tr(
            "notif_mass_saved_body",
            "Видео: {video}\nGIF: {gif}",
            video=counts.get("video", 0),
            gif=counts.get("gif", 0),
        )
        self.show_notification(
            tr("notif_mass_saved_title", "Массовая запись сохранена"),
            message,
            QSystemTrayIcon.MessageIcon.Information,
            5000,
            target_path=pending[-1][1],
        )

    def _show_storage_warning(self):
        capture_root = getattr(self.config_mgr, "storage_path", None)
        location = str(capture_root) if capture_root else str(Path(self.cfg.save_dir_videos).parent)
        self.show_notification(
            tr("storage_warning_title", "Папка приложения защищена"),
            tr(
                "storage_warning_body",
                "Нет права записи рядом с приложением. Записи сохраняются в:\n{path}",
                path=location,
            ),
            QSystemTrayIcon.MessageIcon.Warning,
            7000,
        )

    @pyqtSlot()
    def quick_fullscreen_capture(self):
        """
        Быстрый снимок всего экрана сразу в папку со скриншотами.
        Без показа оверлея, рамок и лишних кликов (по глобальному хоткею).
        """
        if self.overlay and self.overlay.isVisible():
            self.overlay.close_overlay()

        screen = QApplication.primaryScreen()
        geo = screen.virtualGeometry()
        pix = safe_grab_screen_pixmap(geo.x(), geo.y(), geo.width(), geo.height())
        if pix is None or pix.isNull():
            return

        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        ext = getattr(self.cfg, "last_save_format", "png") or "png"
        save_dir = Path(self.cfg.save_dir_screenshots)
        save_dir.mkdir(parents=True, exist_ok=True)
        out_path = save_dir / f"Screenshot_{timestamp}.{ext}"

        img = pix.toImage()
        img.save(str(out_path))
        self.add_recent_media(path=str(out_path), image=img, label=out_path.name)

        if getattr(self.cfg, "auto_copy_to_clipboard", True):
            QApplication.clipboard().setImage(img)

        if getattr(self.cfg, "play_sound", True):
            play_capture_sound()

        self.show_notification(
            tr("notif_quick_screen_saved_title"),
            tr("notif_quick_screen_saved_body", filename=out_path.name),
            QSystemTrayIcon.MessageIcon.Information,
            4000,
            target_path=str(out_path)
        )

    def _update_tray_state(self):
        # Оставляем окна, которые ещё активны или сохраняются в фоне
        self.active_recordings = [w for w in self.active_recordings if not getattr(w, "is_finished", False)]
        rec_count = len([w for w in self.active_recordings if not getattr(w, "is_saving", False)])
        proc_count = len(self.processing_tasks)

        if proc_count > 0:
            self.tray.setIcon(make_app_icon(is_recording=True))
            lines = [tr("tray_processing", count=proc_count)]
            for fname, info in self.processing_tasks.items():
                short_name = fname if len(fname) <= 24 else fname[:10] + "…" + fname[-10:]
                lines.append(f"• {short_name}: {info['percent']}% ({info['stage']})")
            self.tray.setToolTip("\n".join(lines))
        elif rec_count > 0:
            self.tray.setIcon(make_app_icon(is_recording=True))
            stop_key = getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
            self.tray.setToolTip(f"{tr('tray_recording_active', count=rec_count)}\n{tr('tray_hotkey_stop', key=stop_key)}")
        else:
            self.tray.setIcon(make_app_icon(is_recording=False))
            cap_key = self.cfg.hotkey_capture
            quick_key = getattr(self.cfg, "hotkey_quick_fullscreen", "Ctrl+Print Screen")
            screenshot_key = getattr(self.cfg, "hotkey_screenshot", DEFAULT_HOTKEY_SCREENSHOT)
            fs_key = getattr(self.cfg, "hotkey_record_fullscreen", "Ctrl+Shift+F9")
            stop_key = getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
            self.tray.setToolTip(
                f"{tr('app_title')}\n"
                f"{tr('tray_hotkey_capture', key=cap_key)}\n"
                f"{tr('tray_hotkey_quick_screen', key=quick_key)}\n"
                f"{tr('tray_hotkey_screenshot', key=screenshot_key)}\n"
                f"{tr('tray_hotkey_fullscreen', key=fs_key)}\n"
                f"{tr('tray_hotkey_stop', key=stop_key)}\n"
                f"{tr('tray_ready')}"
            )

        self._setup_tray_menu()

    def _setup_tray_menu(self):
        menu = QMenu()
        menu.setStyleSheet("""
            QMenu {
                background-color: #18181b;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                color: #f4f4f5;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 22px;
                border-radius: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
            }
            QMenu::item:selected {
                background-color: #3b82f6;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #27272a;
                margin: 4px 8px;
            }
        """)

        # Если есть активные записи — выводим статус в меню
        if self.active_recordings:
            act_status = QAction(tr("tray_menu_status_rec", count=len(self.active_recordings)), menu)
            act_status.setEnabled(False)
            menu.addAction(act_status)

            stop_key = getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
            act_stop_all = QAction(tr("tray_menu_stop_all", key=stop_key), menu)
            act_stop_all.triggered.connect(self.stop_all_recordings)
            menu.addAction(act_stop_all)
            menu.addSeparator()

        act_capture = QAction(tr("tray_menu_capture", key=self.cfg.hotkey_capture), menu)
        act_capture.triggered.connect(self.trigger_capture)
        menu.addAction(act_capture)

        quick_key = getattr(self.cfg, "hotkey_quick_fullscreen", "Ctrl+Print Screen")
        act_quick_screen = QAction(tr("tray_menu_quick_fullscreen", key=quick_key), menu)
        act_quick_screen.triggered.connect(self.quick_fullscreen_capture)
        menu.addAction(act_quick_screen)

        screenshot_key = getattr(self.cfg, "hotkey_screenshot", DEFAULT_HOTKEY_SCREENSHOT)
        act_screenshot = QAction(tr("tray_menu_screenshot", key=screenshot_key), menu)
        act_screenshot.triggered.connect(self.quick_fullscreen_capture)
        menu.addAction(act_screenshot)

        fs_key = getattr(self.cfg, "hotkey_record_fullscreen", "Ctrl+Shift+F9")
        act_fullscreen = QAction(tr("tray_menu_rec_fullscreen", key=fs_key), menu)
        act_fullscreen.triggered.connect(self.start_fullscreen_recording)
        menu.addAction(act_fullscreen)

        act_video = QAction(tr("tray_menu_rec_video"), menu)
        act_video.triggered.connect(lambda: self.trigger_capture("video"))
        menu.addAction(act_video)

        act_gif = QAction(tr("tray_menu_rec_gif"), menu)
        act_gif.triggered.connect(lambda: self.trigger_capture("gif"))
        menu.addAction(act_gif)

        menu.addSeparator()
        self._recent_media_menu(menu)
        menu.addSeparator()

        act_open_screens = QAction(tr("tray_menu_folder_screens"), menu)
        act_open_screens.triggered.connect(lambda: self._open_folder(self.cfg.save_dir_screenshots))
        menu.addAction(act_open_screens)

        act_open_videos = QAction(tr("tray_menu_folder_videos"), menu)
        act_open_videos.triggered.connect(lambda: self._open_folder(self.cfg.save_dir_videos))
        menu.addAction(act_open_videos)

        act_open_gifs = QAction(tr("tray_menu_folder_gifs"), menu)
        act_open_gifs.triggered.connect(lambda: self._open_folder(self.cfg.save_dir_gifs))
        menu.addAction(act_open_gifs)

        menu.addSeparator()

        act_settings = QAction(tr("tray_menu_settings"), menu)
        act_settings.triggered.connect(self._open_settings)
        menu.addAction(act_settings)

        act_help = QAction(tr("tray_menu_help"), menu)
        act_help.triggered.connect(lambda: self._open_settings(initial_tab=4))
        menu.addAction(act_help)

        menu.addSeparator()

        act_exit = QAction(tr("tray_menu_exit"), menu)
        act_exit.triggered.connect(self.quit_app)
        menu.addAction(act_exit)

        self.tray.setContextMenu(menu)

    @pyqtSlot()
    def stop_all_recordings(self):
        active_recs = [w for w in list(self.active_recordings) if not getattr(w, "is_saving", False) and not getattr(w, "is_finished", False)]
        if not active_recs:
            return
        count = len(active_recs)
        print(f"[Main] Остановка {count} активных записей по запросу...")
        for rec in active_recs:
            try:
                rec.stop_and_save()
            except Exception as e:
                print(f"[Main] Ошибка при остановке записи: {e}")

    @pyqtSlot()
    def start_fullscreen_recording(self, mode="video"):
        from ui.recording_window import RecordingFrameWindow

        # Если оверлей открыт — скрываем его
        if self.overlay and self.overlay.isVisible():
            self.overlay.hide()

        screen = QApplication.primaryScreen()
        geo = screen.geometry()

        rec_window = RecordingFrameWindow(
            mode=mode,
            rect=geo,
            is_fullscreen=True,
            record_mic=getattr(self.cfg, "record_mic", True),
            record_system=getattr(self.cfg, "record_system", True)
        )
        self.add_recording(rec_window)
        rec_window.show()

        stop_key = getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
        self.show_notification(
            tr("notif_rec_started_title", "Запись всего экрана запущена"),
            tr("notif_rec_started_body", key=stop_key),
            QSystemTrayIcon.MessageIcon.Information,
            3500
        )

    def _on_tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self.trigger_capture()

    @pyqtSlot()
    def trigger_capture(self, mode=None):
        self.overlay.start_capture(preselected_recording_mode=mode)

    def start_direct_recording(self, mode="video"):
        from ui.recording_window import RecordingFrameWindow

        rec_window = RecordingFrameWindow(
            mode=mode,
            record_mic=getattr(self.cfg, "record_mic", True),
            record_system=getattr(self.cfg, "record_system", True)
        )
        self.add_recording(rec_window)
        rec_window.show()

    def _open_settings(self, initial_tab: int = 0):
        if hasattr(self, "_settings_dlg") and self._settings_dlg is not None:
            try:
                self._settings_dlg.tabs.setCurrentIndex(initial_tab)
                self._settings_dlg.show()
                self._settings_dlg.raise_()
                self._settings_dlg.activateWindow()
                return
            except Exception:
                pass
        self._settings_dlg = SettingsDialog(initial_tab=initial_tab)
        self._settings_dlg.settings_applied.connect(self._on_settings_applied)
        self._settings_dlg.show()
        self._settings_dlg.raise_()
        self._settings_dlg.activateWindow()

    def _on_settings_applied(self):
        self.hotkey_mgr.update_hotkeys(
            capture=self.cfg.hotkey_capture,
            quick_fullscreen=getattr(self.cfg, "hotkey_quick_fullscreen", "Ctrl+Print Screen"),
            screenshot=getattr(self.cfg, "hotkey_screenshot", DEFAULT_HOTKEY_SCREENSHOT),
            record_fullscreen=getattr(self.cfg, "hotkey_record_fullscreen", "Ctrl+Shift+F9"),
            stop_recording=getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
        )
        self._update_tray_state()

    def _open_folder(self, path_str):
        p = Path(path_str)
        p.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(p))

    def handle_secondary_instance_command(self, cmd: str):
        if cmd == "capture":
            self.trigger_capture()
        else:
            # Если оверлей или окно записи уже открыты, поднимаем их на передний план
            if self.overlay and self.overlay.isVisible():
                self.overlay.activateWindow()
                self.overlay.raise_()
                return
            for rec in self.active_recordings:
                if rec.isVisible():
                    rec.activateWindow()
                    rec.raise_()
                    return

            # Иначе открываем настройки и показываем уведомление
            self._open_settings()
            if self.tray and self.tray.isVisible():
                self.tray.showMessage(
                    tr("app_already_running_title", "Framio уже запущен"),
                    tr("app_already_running_msg", "Приложение уже работает в системном трее. Нажмите {hotkey} для захвата экрана.", hotkey=self.cfg.hotkey_capture),
                    QSystemTrayIcon.MessageIcon.Information,
                    3500
                )

    def quit_app(self):
        # Сначала запрещаем новые глобальные события, затем останавливаем
        # записи и только после этого завершаем Qt-приложение. Иначе
        # QApplication.quit() закрывает цикл событий раньше QThread/FFmpeg.
        try:
            self.hotkey_mgr.stop()
        except Exception:
            pass

        try:
            self.overlay.close_overlay()
        except Exception:
            pass

        recordings = list(self.active_recordings)
        for rec in list(getattr(self.overlay, "recording_windows", []) or []):
            if all(rec is not existing for existing in recordings):
                recordings.append(rec)

        for rec in recordings:
            try:
                rec.cancel_recording()
            except Exception:
                pass

            try:
                rec.wait_for_shutdown()
            except Exception:
                pass

        settings_dlg = getattr(self, "_settings_dlg", None)
        if settings_dlg is not None:
            try:
                settings_dlg.close()
            except Exception:
                pass

        history_dlg = getattr(self.overlay, "history_dialog", None)
        if history_dlg is not None:
            try:
                history_dlg.close()
            except Exception:
                pass

        self.active_recordings.clear()

        try:
            self.tray.hide()
        except Exception:
            pass

        if hasattr(self, "single_instance_mgr") and self.single_instance_mgr:
            try:
                self.single_instance_mgr.cleanup()
            except Exception:
                pass

        QApplication.quit()


# Алиас для обратной совместимости
LightCapApp = FramioApp


def setup_exception_handling():
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        import traceback
        from datetime import datetime
        err_msg = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        print(f"[Framio CRITICAL] Uncaught exception:\n{err_msg}")
        try:
            log_path = Path.home() / "Pictures" / "Framio" / "framio_error.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n--- {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n{err_msg}\n")
        except Exception:
            pass

    sys.excepthook = handle_exception


def main():
    setup_exception_handling()

    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    # Проверка на повторный запуск приложения (Single Instance)
    from utils.single_instance import SingleInstanceManager
    ipc_mgr = SingleInstanceManager()
    payload = "capture" if any(arg in sys.argv for arg in ("--capture", "-c")) else "activate"
    if not ipc_mgr.check_single_instance(payload):
        print("[Framio] Экземпляр приложения уже запущен. Команда передана работающему процессу.")
        sys.exit(0)

    app.setWindowIcon(make_app_icon())
    app.setStyleSheet("""
        QToolTip {
            background-color: #18181b;
            color: #ffffff;
            border: 1px solid #3f3f46;
            border-radius: 4px;
            padding: 4px 8px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11px;
        }
    """)

    framio = FramioApp()
    app.app_instance = framio
    framio.single_instance_mgr = ipc_mgr
    ipc_mgr.message_received.connect(framio.handle_secondary_instance_command)

    is_minimized_boot = any(arg in sys.argv for arg in ("--minimized", "-minimized", "--tray", "-m"))
    open_capture = any(arg in sys.argv for arg in ("--capture", "-c"))

    if open_capture:
        QTimer.singleShot(150, framio.trigger_capture)
    elif is_minimized_boot:
        pass
    else:
        # При интерактивном запуске открываем окно настроек, чтобы пользователь сразу видел запуск
        QTimer.singleShot(150, framio._open_settings)
        framio.tray.showMessage(
            "Framio запущен",
            f"Нажмите {framio.cfg.hotkey_capture} или иконку в трее для захвата экрана.",
            QSystemTrayIcon.MessageIcon.Information,
            3000
        )

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
