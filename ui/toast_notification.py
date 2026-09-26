# -*- coding: utf-8 -*-
"""
Кастомные всплывающие уведомления Framio в правом нижнем углу экрана (над панелью задач / системным треем).
Современный компактный дизайн, превью скриншотов, кнопки быстрых действий и плавная анимация.
"""

import os
import sys
import subprocess
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QPoint, QRect, QRectF, QSize, QTimer, QPropertyAnimation,
    QEasingCurve, QParallelAnimationGroup, pyqtSignal, QObject
)
from PyQt6.QtGui import (
    QColor, QPainter, QPainterPath, QPixmap, QIcon, QFont, QCursor,
    QGuiApplication, QClipboard
)
from PyQt6.QtWidgets import (
    QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QGraphicsDropShadowEffect, QApplication
)

from ui.icons import create_themed_icon
from utils.i18n import tr


class ActionButton(QPushButton):
    """Стильная компактная кнопка действия для уведомления."""
    def __init__(self, text: str, icon_name: str = None, parent=None):
        super().__init__(text, parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(26)
        if icon_name:
            self.setIcon(create_themed_icon(icon_name, is_dark=True, size=14))
            self.setIconSize(QSize(14, 14))
        self.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.08);
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 6px;
                padding: 0 10px;
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: rgba(56, 189, 248, 0.2);
                border-color: rgba(56, 189, 248, 0.5);
                color: #ffffff;
            }
            QPushButton:pressed {
                background: rgba(56, 189, 248, 0.35);
            }
        """)


class ToastNotification(QWidget):
    """
    Компактная карточка всплывающего уведомления над панелью задач.
    """
    dismissed = pyqtSignal(object)

    def __init__(
        self,
        title: str,
        message: str,
        icon_name: str = "camera",
        thumbnail_pixmap: QPixmap = None,
        target_path: str = None,
        copy_data = None,
        timeout: int = 4000,
        parent = None
    ):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self.title_text = title
        self.message_text = message
        self.icon_name = icon_name
        self.thumbnail_pixmap = thumbnail_pixmap
        self.target_path = target_path
        self.copy_data = copy_data
        self.timeout_ms = timeout
        self.is_closing = False
        self._target_pos = QPoint()

        self.setFixedWidth(340)

        # Главный контейнер
        self.card = QWidget(self)
        self.card.setObjectName("toastCard")
        self.card.setStyleSheet("""
            QWidget#toastCard {
                background-color: #0f172a;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 12px;
            }
        """)

        # Тень
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setOffset(0, 4)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.card.setGraphicsEffect(shadow)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(12, 12, 12, 12)
        card_layout.setSpacing(8)

        # Верхняя строка: Иконка/превью + Текст + Кнопка закрытия
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(10)

        # Превью / Иконка
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(44, 44)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._setup_icon()
        top_row.addWidget(self.icon_label)

        # Текстовый блок
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(2)

        self.lbl_title = QLabel(self.title_text)
        self.lbl_title.setStyleSheet("font-size: 12px; font-weight: 700; color: #f8fafc;")
        self.lbl_title.setWordWrap(True)
        text_layout.addWidget(self.lbl_title)

        clean_msg = (self.message_text or "").replace("\r\n", " ").replace("\n", " ").strip()
        if len(clean_msg) > 130:
            clean_msg = clean_msg[:127] + "..."
        self.lbl_msg = QLabel(clean_msg)
        self.lbl_msg.setStyleSheet("font-size: 11px; color: #94a3b8; line-height: 1.3;")
        self.lbl_msg.setWordWrap(True)
        text_layout.addWidget(self.lbl_msg)

        top_row.addLayout(text_layout, 1)

        # Кнопка закрытия
        self.btn_close = QPushButton("✕")
        self.btn_close.setFixedSize(20, 20)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #64748b;
                border: none;
                border-radius: 4px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
                color: #f8fafc;
            }
        """)
        self.btn_close.clicked.connect(self.close_animated)
        top_row.addWidget(self.btn_close, 0, Qt.AlignmentFlag.AlignTop)

        card_layout.addLayout(top_row)

        # Нижняя строка кнопок действий
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(6)

        has_actions = False

        if self.copy_data is not None:
            btn_copy = ActionButton(tr("notif_action_copy", "Копировать"), "copy")
            btn_copy.clicked.connect(self._copy_again)
            actions_layout.addWidget(btn_copy)
            has_actions = True

        if self.target_path and Path(self.target_path).exists():
            btn_open = ActionButton(tr("notif_action_open_folder", "В папке"), "folder")
            btn_open.clicked.connect(self._open_in_explorer)
            actions_layout.addWidget(btn_open)
            has_actions = True

        if has_actions:
            actions_layout.addStretch()
            card_layout.addLayout(actions_layout)

        # Таймер закрытия
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.close_animated)

        self.adjustSize()

    def _setup_icon(self):
        try:
            if self.thumbnail_pixmap is not None and not self.thumbnail_pixmap.isNull():
                pm = self.thumbnail_pixmap
                if isinstance(pm, QImage):
                    pm = QPixmap.fromImage(pm)
                size = 44
                scaled = pm.scaled(
                    size, size,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                rx = max(0, (scaled.width() - size) // 2)
                ry = max(0, (scaled.height() - size) // 2)
                cropped = scaled.copy(rx, ry, size, size)

                rounded = QPixmap(size, size)
                rounded.fill(Qt.GlobalColor.transparent)
                painter = QPainter(rounded)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                path = QPainterPath()
                path.addRoundedRect(0, 0, size, size, 8, 8)
                painter.setClipPath(path)
                painter.drawPixmap(0, 0, cropped)
                painter.setPen(QColor(255, 255, 255, 40))
                painter.drawRoundedRect(0, 0, size - 1, size - 1, 8, 8)
                painter.end()

                self.icon_label.setPixmap(rounded)
                return
        except Exception as e:
            pass

        try:
            # Векторная иконка в стильной подложке
            size = 44
            pix = QPixmap(size, size)
            pix.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pix)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(QColor(30, 41, 59, 220))
            painter.setPen(QColor(255, 255, 255, 25))
            painter.drawRoundedRect(0, 0, size - 1, size - 1, 8, 8)

            ico = create_themed_icon(self.icon_name or "camera", is_dark=True, size=22)
            ico_pix = ico.pixmap(22, 22)
            painter.drawPixmap((size - 22) // 2, (size - 22) // 2, ico_pix)
            painter.end()

            self.icon_label.setPixmap(pix)
        except Exception as e:
            pass

    def _copy_again(self):
        try:
            clipboard = QApplication.clipboard()
            if isinstance(self.copy_data, str):
                clipboard.setText(self.copy_data)
            elif isinstance(self.copy_data, QPixmap):
                clipboard.setPixmap(self.copy_data)
            elif isinstance(self.copy_data, QImage):
                clipboard.setImage(self.copy_data)
            elif isinstance(self.copy_data, Path) or (isinstance(self.copy_data, str) and Path(self.copy_data).exists()):
                pass
        except Exception:
            pass
        # Визуальный отклик
        sender = self.sender()
        if isinstance(sender, QPushButton):
            orig_text = sender.text()
            sender.setText(tr("notif_action_copied", "Скопировано!"))
            QTimer.singleShot(1200, lambda: sender.setText(orig_text))

    def _open_in_explorer(self):
        if self.target_path and Path(self.target_path).exists():
            p = os.path.normpath(str(self.target_path))
            try:
                subprocess.Popen(f'explorer /select,"{p}"')
            except Exception:
                try:
                    os.startfile(str(Path(p).parent))
                except Exception:
                    pass
        self.close_animated()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            # Клик по телу карточки открывает файл, если он есть
            if self.target_path and Path(self.target_path).exists():
                self._open_in_explorer()
                return
        super().mousePressEvent(event)

    def enterEvent(self, event):
        # При наведении мыши ставим таймер на паузу
        if self.timer.isActive():
            self.timer.stop()
        super().enterEvent(event)

    def leaveEvent(self, event):
        # При уходе курсора возобновляем таймер
        if not self.is_closing:
            self.timer.start(2000)
        super().leaveEvent(event)

    def show_at(self, target_pos: QPoint):
        """Плавное появление со сдвигом снизу вверх."""
        self._target_pos = target_pos
        start_pos = QPoint(target_pos.x(), target_pos.y() + 20)
        self.move(start_pos)
        self.setWindowOpacity(0.0)
        self.show()

        # Анимация появления
        anim_group = QParallelAnimationGroup(self)

        anim_pos = QPropertyAnimation(self, b"pos")
        anim_pos.setDuration(220)
        anim_pos.setStartValue(start_pos)
        anim_pos.setEndValue(target_pos)
        anim_pos.setEasingCurve(QEasingCurve.Type.OutCubic)

        anim_opacity = QPropertyAnimation(self, b"windowOpacity")
        anim_opacity.setDuration(220)
        anim_opacity.setStartValue(0.0)
        anim_opacity.setEndValue(1.0)
        anim_opacity.setEasingCurve(QEasingCurve.Type.OutCubic)

        anim_group.addAnimation(anim_pos)
        anim_group.addAnimation(anim_opacity)
        anim_group.start()

        self.anim_in = anim_group
        if self.timeout_ms > 0:
            self.timer.start(self.timeout_ms)

    def move_to(self, new_pos: QPoint):
        """Плавное смещение при перестроении стека уведомлений."""
        self._target_pos = new_pos
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(180)
        anim.setStartValue(self.pos())
        anim.setEndValue(new_pos)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()
        self.anim_shift = anim

    def close_animated(self):
        """Плавное исчезновение и удаление."""
        if self.is_closing:
            return
        self.is_closing = True
        self.timer.stop()

        anim_group = QParallelAnimationGroup(self)

        anim_pos = QPropertyAnimation(self, b"pos")
        anim_pos.setDuration(160)
        anim_pos.setStartValue(self.pos())
        anim_pos.setEndValue(QPoint(self.pos().x(), self.pos().y() + 10))

        anim_opacity = QPropertyAnimation(self, b"windowOpacity")
        anim_opacity.setDuration(160)
        anim_opacity.setStartValue(self.windowOpacity())
        anim_opacity.setEndValue(0.0)

        anim_group.addAnimation(anim_pos)
        anim_group.addAnimation(anim_opacity)

        def _cleanup():
            self.dismissed.emit(self)
            self.close()
            self.deleteLater()

        anim_group.finished.connect(_cleanup)
        anim_group.start()
        self.anim_out = anim_group


class ToastManager(QObject):
    """
    Глобальный менеджер всплывающих уведомлений Framio.
    Обеспечивает стек уведомлений над панелью задач Windows.
    """
    _instance = None

    def __init__(self, parent=None):
        super().__init__(parent)
        self.active_toasts = []

    @classmethod
    def instance(cls) -> "ToastManager":
        if cls._instance is None:
            cls._instance = ToastManager()
        return cls._instance

    def show_toast(
        self,
        title: str,
        message: str,
        icon_name: str = "camera",
        thumbnail_pixmap: QPixmap = None,
        target_path: str = None,
        copy_data = None,
        timeout: int = 4000
    ):
        """Создает и отображает всплывающее уведомление над системным треем."""
        toast = ToastNotification(
            title=title,
            message=message,
            icon_name=icon_name,
            thumbnail_pixmap=thumbnail_pixmap,
            target_path=target_path,
            copy_data=copy_data,
            timeout=timeout
        )
        toast.dismissed.connect(self._on_toast_dismissed)

        # Ограничиваем максимум 3 активных уведомления
        if len(self.active_toasts) >= 3:
            oldest = self.active_toasts[0]
            oldest.close_animated()

        self.active_toasts.append(toast)
        self._reposition_toasts()

        # Воспроизведение звука уведомления
        try:
            from config import ConfigManager
            cfg = ConfigManager.get_instance().config
            if getattr(cfg, "play_sound", True):
                if icon_name != "camera":
                    from utils.sound import play_notification_sound
                    play_notification_sound()
        except Exception:
            pass

    def _reposition_toasts(self):
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        geo = screen.availableGeometry()

        margin_right = 16
        margin_bottom = 16
        spacing = 8

        # Вычисляем позиции снизу вверх
        current_y = geo.bottom() - margin_bottom

        for toast in reversed(self.active_toasts):
            t_width = toast.width()
            t_height = toast.height()
            target_x = geo.right() - t_width - margin_right
            target_y = current_y - t_height

            target_pos = QPoint(target_x, target_y)
            if not toast.isVisible():
                toast.show_at(target_pos)
            else:
                toast.move_to(target_pos)

            current_y = target_y - spacing

    def _on_toast_dismissed(self, toast):
        if toast in self.active_toasts:
            self.active_toasts.remove(toast)
            self._reposition_toasts()

    def close_all(self):
        """Немедленно закрывает все активные уведомления."""
        for toast in list(self.active_toasts):
            try:
                toast.close()
                toast.deleteLater()
            except Exception:
                pass
        self.active_toasts.clear()
