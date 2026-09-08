# -*- coding: utf-8 -*-
"""
Диалоговое окно настроек приложения Framio.
Современный интерфейс в стиле Fluent / Windows 11 с левой панелью навигации,
карточками настроек (SettingCard), защитой от наложения текста,
поддержкой всех опций (видео, GIF, звук, таймер, язык, форматы, аннотации).
"""

import sys
import os
from pathlib import Path
from PyQt6.QtCore import Qt, pyqtSignal, QTimer, QSize
from PyQt6.QtGui import QKeyEvent, QColor, QIcon
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QFileDialog,
    QWidget, QMessageBox, QGridLayout, QTextBrowser,
    QScrollArea, QFrame, QStackedWidget, QListWidget, QListWidgetItem,
    QSpinBox, QColorDialog, QApplication
)
from config import (
    ConfigManager, AppConfig, get_base_dir, DEFAULT_HOTKEY,
    DEFAULT_HOTKEY_RECORD_FULLSCREEN, DEFAULT_HOTKEY_STOP_RECORDING,
    DEFAULT_HOTKEY_QUICK_FULLSCREEN, DEFAULT_HOTKEY_SCREENSHOT,
    DEFAULT_HOTKEY_HIGHLIGHT_OBJECTS, normalize_portable_path
)
from utils.autostart import set_windows_autostart, is_windows_autostart_enabled
from utils.i18n import tr, set_language, get_current_language
from ui.icons import create_themed_icon


import ctypes
from ctypes import wintypes

class _KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', wintypes.DWORD),
        ('scanCode', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.POINTER(wintypes.ULONG))
    ]
_HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_longlong, ctypes.c_int, wintypes.WPARAM, ctypes.POINTER(_KBDLLHOOKSTRUCT))


class HotkeyRecorderButton(QPushButton):
    """
    Интерактивная кнопка для перехвата и назначения комбинации клавиш.
    Отображает нажимаемые клавиши в реальном времени прямо в целевом поле ввода (target_line_edit):
    при нажатии Ctrl пишется 'Ctrl + ', при нажатии Shift — 'Ctrl + Shift + ',
    при нажатии клавиши — 'Ctrl + Shift + S'.
    При отпускании клавиш комбинация фиксируется.
    Поддерживает одиночные клавиши (Print Screen, F1-F12, буквы) и комбинации с модификаторами.
    """
    def __init__(self, target_line_edit: QLineEdit, parent=None):
        super().__init__(tr("settings_btn_record", "Назначить"), parent)
        self.target_line_edit = target_line_edit
        self.is_recording = False
        self._original_text = ""
        self._active_mods = []
        self._recorded_key = None
        self._keys_currently_down = set()
        self._ll_hook_id = None
        self._ll_hook_proc = None
        self._global_hotkey_manager = None
        self._global_hotkeys_were_active = False
        self.setToolTip(tr("settings_hk_tooltip", "Нажмите для записи комбинации клавиш (Ctrl, Shift, Alt, F1-F12, буквы, Print Screen)"))
        self.clicked.connect(self._toggle_recording)
        self.setFixedWidth(96)

    def _toggle_recording(self):
        if self.is_recording:
            self._cancel_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        self.is_recording = True
        self._original_text = self.target_line_edit.text().strip() if self.target_line_edit else ""
        self._active_mods = []
        self._recorded_key = None
        self._keys_currently_down = set()

        self.setText(tr("settings_btn_listening", "Нажмите..."))
        self.setStyleSheet("""
            QPushButton {
                background-color: #b45309;
                color: #ffffff;
                font-weight: 600;
                border: 1px solid #f59e0b;
            }
        """)

        if self.target_line_edit:
            self.target_line_edit.setText(tr("settings_btn_listening_key", "Нажмите клавишу..."))
            self.target_line_edit.setFocus()

        # Глобальный Print Screen не должен запускать снимок поверх записи
        # комбинации. Возвращаем обработчик после завершения или отмены.
        self._suspend_global_hotkeys()

        # 1. Слушаем только активное поле, а не всё приложение. Глобальный
        # фильтр конфликтует с нативным хуком Print Screen и может ломать ввод.
        try:
            if self.target_line_edit:
                self.target_line_edit.installEventFilter(self)
            self.installEventFilter(self)
        except Exception:
            pass

        # 2. Устанавливаем низкоуровневый Win32 хук WH_KEYBOARD_LL строго для перехвата Print Screen (VK_SNAPSHOT = 0x2C)
        self._ll_hook_id = None
        self._ll_hook_proc = None
        if sys.platform == "win32":
            try:
                user32 = ctypes.windll.user32
                def _ll_proc(nCode, wParam, lParam):
                    if nCode >= 0 and getattr(self, "is_recording", False):
                        # 0x0100=WM_KEYDOWN, 0x0101=WM_KEYUP, 0x0104=WM_SYSKEYDOWN, 0x0105=WM_SYSKEYUP
                        if wParam in (0x0100, 0x0104):
                            vk = lParam.contents.vkCode
                            if vk == 0x2C:  # VK_SNAPSHOT (Print Screen)
                                mods = self._get_win32_modifiers()
                                combo = "+".join(mods + ["Print Screen"]) if mods else "Print Screen"
                                self._recorded_key = "Print Screen"
                                self._active_mods = list(mods)
                                QTimer.singleShot(0, lambda c=combo: self._update_live_display(c))
                                return 1  # Подавляем системные «Ножницы» Windows!
                        elif wParam in (0x0101, 0x0105):
                            vk = lParam.contents.vkCode
                            if vk == 0x2C:
                                mods = self._active_mods
                                combo = "+".join(mods + ["Print Screen"]) if mods else "Print Screen"
                                QTimer.singleShot(0, lambda c=combo: self._finish_recording(c))
                                return 1
                    return user32.CallNextHookEx(None, nCode, wParam, lParam)

                self._ll_hook_proc = _HOOKPROC(_ll_proc)
                self._ll_hook_id = user32.SetWindowsHookExW(13, self._ll_hook_proc, None, 0)
            except Exception:
                self._ll_hook_id = None
                self._ll_hook_proc = None

    def _get_win32_modifiers(self) -> list:
        mods = []
        if sys.platform == "win32":
            try:
                u32 = ctypes.windll.user32
                if u32.GetAsyncKeyState(0x11) & 0x8000: mods.append("Ctrl")
                if u32.GetAsyncKeyState(0x12) & 0x8000: mods.append("Alt")
                if u32.GetAsyncKeyState(0x10) & 0x8000: mods.append("Shift")
                if (u32.GetAsyncKeyState(0x5B) & 0x8000) or (u32.GetAsyncKeyState(0x5C) & 0x8000): mods.append("Win")
            except Exception:
                pass
        return mods

    def _update_live_display(self, text: str):
        if self.target_line_edit:
            self.target_line_edit.setText(text)
        self.setText(text if len(text) <= 12 else text[:11] + "…")

    def _finish_recording(self, combo_str: str):
        self._cleanup_hooks()
        self.is_recording = False
        self.setText(tr("settings_btn_record", "Назначить"))
        self.setStyleSheet("")
        if combo_str and self.target_line_edit:
            self.target_line_edit.setText(combo_str)

    def _cancel_recording(self):
        self._cleanup_hooks()
        self.is_recording = False
        self.setText(tr("settings_btn_record", "Назначить"))
        self.setStyleSheet("")
        if getattr(self, "_original_text", None) and self.target_line_edit:
            self.target_line_edit.setText(self._original_text)

    def _cleanup_hooks(self):
        try:
            if self.target_line_edit:
                self.target_line_edit.removeEventFilter(self)
            self.removeEventFilter(self)
        except Exception:
            pass
        if getattr(self, "_ll_hook_id", None):
            try:
                ctypes.windll.user32.UnhookWindowsHookEx(self._ll_hook_id)
            except Exception:
                pass
            self._ll_hook_id = None
            self._ll_hook_proc = None

        self._restore_global_hotkeys()

    def _suspend_global_hotkeys(self):
        app = QApplication.instance()
        app_instance = getattr(app, "app_instance", None) if app else None
        manager = getattr(app_instance, "hotkey_mgr", None)
        if manager and hasattr(manager, "suspend"):
            self._global_hotkey_manager = manager
            self._global_hotkeys_were_active = manager.suspend()

    def _restore_global_hotkeys(self):
        manager = self._global_hotkey_manager
        was_active = self._global_hotkeys_were_active
        self._global_hotkey_manager = None
        self._global_hotkeys_were_active = False
        if manager and hasattr(manager, "resume"):
            try:
                manager.resume(was_active)
            except Exception:
                pass

    def eventFilter(self, watched, event):
        if self.is_recording and event is not None:
            etype = event.type()
            if etype == QEvent.Type.KeyPress:
                self._handle_key_press(event)
                return True
            elif etype == QEvent.Type.KeyRelease:
                self._handle_key_release(event)
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event: QKeyEvent):
        if self.is_recording:
            self._handle_key_press(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent):
        if self.is_recording:
            self._handle_key_release(event)
            event.accept()
            return
        super().keyReleaseEvent(event)

    def _handle_key_press(self, event: QKeyEvent):
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self._cancel_recording()
            return

        self._keys_currently_down.add(key)

        # Вычисляем активные модификаторы
        mods = []
        if (event.modifiers() & Qt.KeyboardModifier.ControlModifier) or key == Qt.Key.Key_Control:
            mods.append("Ctrl")
        if (event.modifiers() & Qt.KeyboardModifier.AltModifier) or key == Qt.Key.Key_Alt:
            mods.append("Alt")
        if (event.modifiers() & Qt.KeyboardModifier.ShiftModifier) or key == Qt.Key.Key_Shift:
            mods.append("Shift")
        if (event.modifiers() & Qt.KeyboardModifier.MetaModifier) or key == Qt.Key.Key_Meta:
            mods.append("Win")

        # Дополняем проверкой через Win32 GetAsyncKeyState
        for w_mod in self._get_win32_modifiers():
            if w_mod not in mods:
                mods.append(w_mod)

        is_modifier_only = key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta)

        if is_modifier_only:
            self._active_mods = list(mods)
            live_str = "+".join(mods) + "+" if mods else ""
            if live_str:
                self._update_live_display(live_str)
        else:
            kname = self._get_key_name(event)
            if kname:
                self._recorded_key = kname
                self._active_mods = list(mods)
                combo = "+".join(mods + [kname]) if mods else kname
                self._update_live_display(combo)
                if kname == "Print Screen" and not mods:
                    self._finish_recording("Print Screen")

    def _handle_key_release(self, event: QKeyEvent):
        key = event.key()
        self._keys_currently_down.discard(key)

        if key == Qt.Key.Key_Escape:
            return

        if key in (Qt.Key.Key_Print, 16777225):
            combo = "+".join(self._active_mods + ["Print Screen"]) if self._active_mods else "Print Screen"
            self._finish_recording(combo)
            return

        if self._recorded_key:
            combo = "+".join(self._active_mods + [self._recorded_key]) if self._active_mods else self._recorded_key
            self._finish_recording(combo)
            return

        if len(self._keys_currently_down) == 0:
            if not self._recorded_key and self._active_mods:
                combo = "+".join(self._active_mods)
                self._finish_recording(combo)

    def _get_key_name(self, event: QKeyEvent) -> str:
        key = event.key()
        if key == Qt.Key.Key_Print or key == 16777225:
            return "Print Screen"
        if sys.platform == "win32":
            vk = getattr(event, "nativeVirtualKey", lambda: 0)()
            if vk == 0x2C:
                return "Print Screen"
            if 0x70 <= vk <= 0x7B:
                return f"F{vk - 0x70 + 1}"
            if 0x41 <= vk <= 0x5A:
                return chr(vk).upper()
            if 0x30 <= vk <= 0x39:
                return chr(vk)
            if vk == 0x20: return "Space"
            if vk == 0x0D: return "Enter"
            if vk == 0x09: return "Tab"
            if vk == 0x08: return "Backspace"
            if vk == 0x2E: return "Delete"
            if vk == 0x2D: return "Insert"
            if vk == 0x24: return "Home"
            if vk == 0x23: return "End"
            if vk == 0x21: return "Page Up"
            if vk == 0x22: return "Page Down"

        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
            return f"F{key - Qt.Key.Key_F1 + 1}"
        if key == Qt.Key.Key_Space:
            return "Space"
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            return "Enter"
        if key == Qt.Key.Key_Tab:
            return "Tab"
        if key == Qt.Key.Key_Backspace:
            return "Backspace"
        if key == Qt.Key.Key_Delete:
            return "Delete"
        if key == Qt.Key.Key_Insert:
            return "Insert"
        if key == Qt.Key.Key_Home:
            return "Home"
        if key == Qt.Key.Key_End:
            return "End"
        if key == Qt.Key.Key_PageUp:
            return "Page Up"
        if key == Qt.Key.Key_PageDown:
            return "Page Down"
        if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            return chr(key).upper()
        if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            return chr(key)
        txt = event.text()
        if txt and len(txt) == 1 and txt.isprintable() and not txt.isspace():
            return txt.upper()
        return None


class SettingCard(QFrame):
    """
    Элегантная карточка настройки в стиле Windows 11 / Fluent.
    Группирует связанные параметры с четкими разделителями и читаемым текстом.
    """
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("SettingCard")
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(16, 14, 16, 14)
        self.layout.setSpacing(10)
        self.setStyleSheet("""
            QFrame#SettingCard {
                background-color: #171a23;
                border: 1px solid #272c3a;
                border-radius: 8px;
            }
        """)
        if title:
            lbl_title = QLabel(title)
            lbl_title.setStyleSheet("font-size: 13px; font-weight: 600; color: #38bdf8; margin-bottom: 2px;")
            self.layout.addWidget(lbl_title)
        self._item_count = 0

    def add_row(self, title: str, desc: str = "", control: QWidget = None, stretch_control: bool = False):
        if self._item_count > 0:
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setStyleSheet("background-color: #212634; border: none; max-height: 1px;")
            self.layout.addWidget(divider)

        row = QHBoxLayout()
        row.setSpacing(16)
        row.setContentsMargins(0, 2, 0, 2)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        lbl_t = QLabel(title)
        lbl_t.setStyleSheet("font-size: 12px; font-weight: 500; color: #f1f5f9;")
        lbl_t.setWordWrap(True)
        text_layout.addWidget(lbl_t)

        if desc:
            lbl_d = QLabel(desc)
            lbl_d.setStyleSheet("font-size: 11px; color: #94a3b8;")
            lbl_d.setWordWrap(True)
            text_layout.addWidget(lbl_d)

        row.addLayout(text_layout, 1)

        if control:
            if stretch_control:
                row.addWidget(control, 1)
            else:
                row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        self.layout.addLayout(row)
        self._item_count += 1

    def add_widget(self, widget: QWidget):
        if self._item_count > 0:
            divider = QFrame()
            divider.setFrameShape(QFrame.Shape.HLine)
            divider.setStyleSheet("background-color: #212634; border: none; max-height: 1px;")
            self.layout.addWidget(divider)
        self.layout.addWidget(widget)
        self._item_count += 1


def create_scroll_page(content_widget: QWidget) -> QScrollArea:
    """Оборачивает страницу настроек в отзывчивый скролл-контейнер с плавным скроллбаром."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setStyleSheet("""
        QScrollArea {
            background-color: transparent;
            border: none;
        }
        QScrollBar:vertical {
            background-color: #12151c;
            width: 7px;
            margin: 0;
            border-radius: 3px;
        }
        QScrollBar::handle:vertical {
            background-color: #293040;
            min-height: 24px;
            border-radius: 3px;
        }
        QScrollBar::handle:vertical:hover {
            background-color: #38bdf8;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0;
        }
    """)
    scroll.setWidget(content_widget)
    return scroll


class SettingsTabStack(QWidget):
    """
    Современная компоновка настроек: боковое меню навигации с иконками слева
    и стек карточек со скроллом справа.
    Полностью совместима с API QTabWidget (count, currentIndex, setCurrentIndex, addTab).
    """
    currentChanged = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14)

        # Боковое меню
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(200)
        self.sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.sidebar.setIconSize(QSize(16, 16))
        self.sidebar.setStyleSheet("""
            QListWidget {
                background-color: #141720;
                border: 1px solid #242a38;
                border-radius: 8px;
                padding: 6px;
                outline: none;
            }
            QListWidget::item {
                height: 40px;
                color: #94a3b8;
                font-size: 12px;
                font-weight: 500;
                padding-left: 10px;
                border-radius: 6px;
                margin-bottom: 3px;
                border: 1px solid transparent;
            }
            QListWidget::item:hover {
                background-color: #1e2330;
                color: #e2e8f0;
            }
            QListWidget::item:selected {
                background-color: #242c3d;
                color: #38bdf8;
                font-weight: 600;
                border-left: 3px solid #38bdf8;
            }
        """)

        # Правый стек страниц
        self.stack = QStackedWidget()

        self._layout.addWidget(self.sidebar, 0)
        self._layout.addWidget(self.stack, 1)

        self.sidebar.currentRowChanged.connect(self._on_row_changed)

    def _on_row_changed(self, idx: int):
        self.stack.setCurrentIndex(idx)
        self.currentChanged.emit(idx)

    def addTab(self, widget: QWidget, arg2, arg3=None) -> int:
        """Поддерживает addTab(widget, label) и addTab(widget, icon, label) / addTab(widget, label, icon)."""
        if isinstance(arg2, QIcon):
            icon = arg2
            label = str(arg3) if arg3 is not None else ""
        else:
            label = str(arg2)
            icon = arg3 if isinstance(arg3, QIcon) else None

        idx = self.stack.addWidget(widget)
        item = QListWidgetItem(label)
        if icon:
            item.setIcon(icon)
        self.sidebar.addItem(item)
        if self.sidebar.count() == 1:
            self.sidebar.setCurrentRow(0)
        return idx

    def count(self) -> int:
        return self.stack.count()

    def clear(self):
        """Удаляет страницы и подписи перед перестроением после смены языка."""
        self.sidebar.clear()
        while self.stack.count():
            widget = self.stack.widget(0)
            self.stack.removeWidget(widget)
            widget.deleteLater()

    def currentIndex(self) -> int:
        return self.stack.currentIndex()

    def setCurrentIndex(self, idx: int):
        if 0 <= idx < self.count():
            self.sidebar.setCurrentRow(idx)
            self.stack.setCurrentIndex(idx)


class SettingsDialog(QDialog):
    settings_applied = pyqtSignal()

    def __init__(self, initial_tab: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("settings_title", "Настройки Framio"))
        self.resize(900, 650)
        self.setMinimumSize(850, 560)
        self.config_mgr = ConfigManager.get_instance()
        self.cfg = self.config_mgr.config
        self.btn_apply = None
        self.btn_close = None

        self._init_styles()
        self._init_ui()
        if 0 <= initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(initial_tab)

    def _init_styles(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #111319;
                color: #f1f5f9;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QLabel {
                color: #cbd5e1;
                font-size: 12px;
            }
            QLineEdit, QComboBox, QSpinBox {
                background-color: #161923;
                color: #ffffff;
                border: 1px solid #2d3446;
                border-radius: 5px;
                padding: 4px 10px;
                min-height: 28px;
                font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
                border: 1px solid #38bdf8;
                background-color: #1b202c;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
            }
            QComboBox QAbstractItemView {
                background-color: #161923;
                color: #ffffff;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
                border: 1px solid #2d3446;
                padding: 4px;
            }
            QPushButton {
                background-color: #1e2330;
                color: #f1f5f9;
                border: 1px solid #2f374c;
                border-radius: 5px;
                padding: 5px 12px;
                min-height: 28px;
                font-size: 12px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #272e3f;
                border-color: #3b465f;
            }
            QPushButton:pressed {
                background-color: #161a24;
            }
            QCheckBox {
                color: #e2e8f0;
                font-size: 12px;
                spacing: 8px;
                min-height: 22px;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
                border-radius: 4px;
                border: 1px solid #384257;
                background-color: #161923;
            }
            QCheckBox::indicator:hover {
                border-color: #38bdf8;
            }
            QCheckBox::indicator:checked {
                background-color: #0284c7;
                border-color: #38bdf8;
            }
        """)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(12)

        # 5 логичных вкладок настроек с левой навигацией
        self.tabs = SettingsTabStack()
        self._rebuild_tabs()

        main_layout.addWidget(self.tabs, 1)

        # Нижняя панель
        bottom_layout = QHBoxLayout()
        bottom_layout.setContentsMargins(4, 0, 4, 0)
        
        base_dir = get_base_dir()
        cfg_mode = tr("settings_mode_portable", "Портативный") if self.cfg.is_portable else tr("settings_mode_system", "Системный")
        self.lbl_status = QLabel(tr("settings_portable_badge", "Режим: {mode} • {path}", mode=cfg_mode, path=str(base_dir)))
        self.lbl_status.setStyleSheet("color: #8892b0; font-size: 11px;")
        bottom_layout.addWidget(self.lbl_status, 1)

        self.btn_apply = QPushButton(tr("settings_btn_apply", "Применить"))
        self.btn_apply.setStyleSheet("""
            QPushButton {
                background-color: #0284c7;
                color: #ffffff;
                font-weight: 600;
                padding: 6px 22px;
                border: 1px solid #38bdf8;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #0369a1;
            }
        """)
        self.btn_apply.clicked.connect(self._apply_settings)
        bottom_layout.addWidget(self.btn_apply)

        self.btn_close = QPushButton(tr("settings_btn_close", "Закрыть"))
        self.btn_close.clicked.connect(self._on_close_clicked)
        bottom_layout.addWidget(self.btn_close)

        main_layout.addLayout(bottom_layout)

    def _rebuild_tabs(self, current_index: int | None = None):
        """Пересоздаёт страницы, чтобы смена языка применялась сразу ко всему окну."""
        if current_index is None and hasattr(self, "tabs"):
            current_index = self.tabs.currentIndex()
        current_index = max(0, int(current_index or 0))

        if hasattr(self, "tabs") and self.tabs.count():
            self.tabs.clear()

        ico_folder = create_themed_icon("folder", is_dark=True, size=16)
        ico_video = create_themed_icon("video", is_dark=True, size=16)
        ico_kbd = create_themed_icon("keyboard", is_dark=True, size=16)
        ico_settings = create_themed_icon("settings", is_dark=True, size=16)
        ico_help = create_themed_icon("help", is_dark=True, size=16)

        self.tabs.addTab(self._create_storage_tab(), tr("settings_tab_storage", "Папки сохранения"), ico_folder)
        self.tabs.addTab(self._create_media_tab(), tr("settings_tab_media", "Запись (Видео, GIF, Звук)"), ico_video)
        self.tabs.addTab(self._create_hotkeys_tab(), tr("settings_tab_hotkeys", "Горячие клавиши"), ico_kbd)
        self.tabs.addTab(self._create_general_tab(), tr("settings_tab_general", "Общие, язык и снимки"), ico_settings)
        self.tabs.addTab(self._create_help_tab(), tr("settings_tab_help", "Справка и инструкция"), ico_help)
        self.tabs.setCurrentIndex(min(current_index, self.tabs.count() - 1))

    # -------------------------------------------------------------
    # 1. ВКЛАДКА: ПАПКИ СОХРАНЕНИЯ
    # -------------------------------------------------------------
    def _create_storage_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        # Карточка 1: Портативный режим
        card_portable = SettingCard(tr("settings_storage_portable_group", "Портативный режим"))
        self.chk_portable = QCheckBox(tr("settings_storage_portable_chk", "Включить портативный режим (хранить настройки и снимки в папке программы)"))
        self.chk_portable.setChecked(self.cfg.is_portable)
        self.chk_portable.toggled.connect(self._on_portable_toggled)
        card_portable.add_widget(self.chk_portable)

        base = get_base_dir()
        row_base = QHBoxLayout()
        row_base.setContentsMargins(0, 4, 0, 0)
        lbl_base = QLabel(f"{tr('settings_base_dir', 'Каталог программы')}: <span style='color: #38bdf8; font-weight: 500;'>{base}</span>")
        lbl_base.setTextFormat(Qt.TextFormat.RichText)
        row_base.addWidget(lbl_base, 1)

        btn_open_base = QPushButton(tr("settings_btn_open_base", "Открыть папку программы"))
        btn_open_base.clicked.connect(lambda: self._open_dir(str(base)))
        row_base.addWidget(btn_open_base)
        
        w_base = QWidget()
        w_base.setLayout(row_base)
        card_portable.add_widget(w_base)
        layout.addWidget(card_portable)

        # Карточка 2: Каталоги сохранения файлов
        card_dirs = SettingCard(tr("settings_storage_group", "Папки для сохранения"))

        def _make_folder_widget(title_text: str, line_edit: QLineEdit, browse_cb, open_cb) -> QWidget:
            box = QWidget()
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(0, 2, 0, 2)
            box_layout.setSpacing(6)
            
            lbl = QLabel(title_text)
            lbl.setStyleSheet("font-size: 12px; font-weight: 500; color: #f1f5f9;")
            box_layout.addWidget(lbl)

            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(line_edit, 1)
            btn_b = QPushButton(tr("settings_btn_browse", "Обзор..."))
            btn_b.clicked.connect(browse_cb)
            btn_o = QPushButton(tr("settings_btn_open", "Открыть"))
            btn_o.clicked.connect(open_cb)
            row.addWidget(btn_b)
            row.addWidget(btn_o)
            box_layout.addLayout(row)
            return box

        # Скриншоты
        self.edit_screenshots_dir = QLineEdit(self.cfg.save_dir_screenshots)
        w_s = _make_folder_widget(tr("settings_screenshots_dir", "Папка скриншотов:"), self.edit_screenshots_dir, self._browse_screenshots, lambda: self._open_dir(self.edit_screenshots_dir.text()))
        card_dirs.add_widget(w_s)

        # Видео
        self.edit_videos_dir = QLineEdit(self.cfg.save_dir_videos)
        w_v = _make_folder_widget(tr("settings_videos_dir", "Папка видеозаписей (MP4):"), self.edit_videos_dir, self._browse_videos, lambda: self._open_dir(self.edit_videos_dir.text()))
        card_dirs.add_widget(w_v)

        # GIF
        self.edit_gifs_dir = QLineEdit(self.cfg.save_dir_gifs)
        w_g = _make_folder_widget(tr("settings_gifs_dir", "Папка GIF-анимаций:"), self.edit_gifs_dir, self._browse_gifs, lambda: self._open_dir(self.edit_gifs_dir.text()))
        card_dirs.add_widget(w_g)

        # Кнопки быстрого сброса
        quick_btns = QHBoxLayout()
        quick_btns.setContentsMargins(0, 4, 0, 0)
        btn_reset_portable = QPushButton(tr("settings_btn_reset_port", "Сбросить к папке Captures"))
        btn_reset_portable.clicked.connect(self._reset_paths_to_portable)
        quick_btns.addWidget(btn_reset_portable)

        btn_reset_standard = QPushButton(tr("settings_btn_reset_std", "Сбросить к стандартным Windows"))
        btn_reset_standard.clicked.connect(self._reset_paths_to_standard)
        quick_btns.addWidget(btn_reset_standard)
        quick_btns.addStretch()

        w_qb = QWidget()
        w_qb.setLayout(quick_btns)
        card_dirs.add_widget(w_qb)
        layout.addWidget(card_dirs)

        layout.addStretch()
        return create_scroll_page(container)

    # -------------------------------------------------------------
    # 2. ВКЛАДКА: ЗАПИСЬ (ВИДЕО, GIF, ЗВУК)
    # -------------------------------------------------------------
    def _create_media_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        # Карточка: Параметры видеозаписи (MP4)
        card_video = SettingCard(tr("settings_video_group", "Параметры видеозаписи (MP4)"))

        self.combo_fps = QComboBox()
        self.combo_fps.addItems(["15", "24", "30", "60"])
        self.combo_fps.setCurrentText(str(self.cfg.video_fps))
        self.combo_fps.setFixedWidth(120)
        card_video.add_row(tr("settings_video_fps", "Частота кадров (FPS):"), tr("settings_video_fps_desc", "Плавность записи видеопотока (рекомендуется 30 или 60 FPS)"), self.combo_fps)

        self.combo_codec = QComboBox()
        self.combo_codec.addItems(["mp4v", "avc1", "XVID"])
        self.combo_codec.setCurrentText(self.cfg.video_codec)
        self.combo_codec.setFixedWidth(120)
        card_video.add_row(tr("settings_video_codec", "Видеокодек:"), tr("settings_video_codec_desc", "Аппаратный или программный кодек упаковки кадров в MP4"), self.combo_codec)

        self.combo_quality = QComboBox()
        self.combo_quality.addItems([tr("settings_quality_high", "Высокое"), tr("settings_quality_medium", "Среднее"), tr("settings_quality_ultra", "Максимальное")])
        self.combo_quality.setCurrentText(getattr(self.cfg, "video_quality", "Высокое"))
        self.combo_quality.setFixedWidth(120)
        card_video.add_row(tr("settings_video_quality", "Качество записи:"), tr("settings_video_quality_desc", "Битрейт и четкость сжатия видео"), self.combo_quality)

        self.chk_compress_video = QCheckBox(tr("settings_compress_video", "Сжимать видео после записи (H.264 CRF)"))
        self.chk_compress_video.setChecked(getattr(self.cfg, "compress_video", True))
        card_video.add_row(tr("settings_compress_mp4_row", "Фоновая оптимизация MP4"), tr("settings_compress_video_desc", "Автоматическое сжатие FFmpeg без потери визуального качества"), self.chk_compress_video)

        layout.addWidget(card_video)

        # Карточка: Параметры GIF-анимаций
        card_gif = SettingCard(tr("settings_gif_group", "Параметры GIF-анимаций"))

        self.combo_gif_fps = QComboBox()
        self.combo_gif_fps.addItems(["10", "12", "15", "20", "24", "30"])
        self.combo_gif_fps.setCurrentText(str(self.cfg.gif_fps))
        self.combo_gif_fps.setFixedWidth(120)
        card_gif.add_row(tr("settings_gif_fps", "Частота кадров GIF (FPS):"), tr("settings_gif_fps_desc", "Меньше FPS значительно сокращает размер файла GIF"), self.combo_gif_fps)

        self.combo_gif_colors = QComboBox()
        self.combo_gif_colors.setFixedWidth(300)
        self.gif_options = [
            (tr("popup_gif_opt_max", "Максимальное сжатие (64 цвета)"), 64, "none"),
            (tr("popup_gif_opt_high", "Высокое сжатие (64 цвета, bayer)"), 64, "bayer"),
            (tr("popup_gif_opt_balance", "Баланс (128 цветов, сжатый)"), 128, "bayer"),
            (tr("popup_gif_opt_quality", "Высокое качество (256 цветов)"), 256, "bayer"),
            (tr("popup_gif_opt_extreme", "Экстремальное сжатие (32 цвета)"), 32, "none"),
        ]
        cur_colors = getattr(self.cfg, "gif_colors", 64)
        cur_dither = getattr(self.cfg, "gif_dither", "none")
        cur_idx = 0
        for i, (title, c, d) in enumerate(self.gif_options):
            self.combo_gif_colors.addItem(title, (c, d))
            if c == cur_colors and d == cur_dither:
                cur_idx = i
        self.combo_gif_colors.setCurrentIndex(cur_idx)
        card_gif.add_row(tr("settings_gif_colors", "Палитра и сжатие:"), tr("settings_gif_colors_desc", "Количество цветов и алгоритм сглаживания градиентов"), self.combo_gif_colors)

        self.chk_gif_opt = QCheckBox(tr("popup_gif_opt", "Оптимизировать размер GIF (LZW)"))
        self.chk_gif_opt.setChecked(self.cfg.gif_optimize)
        card_gif.add_row(tr("settings_lzw_row", "LZW сжатие"), tr("settings_gif_opt_desc", "Устранение повторяющихся цветовых блоков"), self.chk_gif_opt)

        self.chk_compress_gif = QCheckBox(tr("settings_compress_gif", "Сжимать GIF палитрой (PaletteGen)"))
        self.chk_compress_gif.setChecked(getattr(self.cfg, "compress_gif", True))
        card_gif.add_row(tr("settings_palettegen_row", "Двухпроходный PaletteGen"), tr("settings_compress_gif_desc", "Генерация адаптивной 256-цветной палитры под контент"), self.chk_compress_gif)

        layout.addWidget(card_gif)

        # Карточка: Звук
        card_audio = SettingCard(tr("settings_audio_group", "Запись звука"))

        self.chk_record_mic = QCheckBox(tr("settings_record_mic", "Записывать звук с микрофона (по умолчанию)"))
        self.chk_record_mic.setChecked(getattr(self.cfg, "record_mic", True))
        card_audio.add_row(tr("settings_record_mic_row", "Микрофон"), tr("settings_record_mic_desc", "Захват голоса пользователя через системный вход по умолчанию"), self.chk_record_mic)

        self.chk_record_system = QCheckBox(tr("settings_record_system", "Записывать звук из игр и системы (WASAPI loopback)"))
        self.chk_record_system.setChecked(getattr(self.cfg, "record_system", True))
        card_audio.add_row(tr("settings_record_system_row", "Системный звук"), tr("settings_record_system_desc", "Захват музыки, видео и звуков приложений без сторонних драйверов"), self.chk_record_system)

        layout.addWidget(card_audio)

        # Карточка: Таймер перед началом записи
        card_countdown = SettingCard(tr("settings_countdown_group", "Таймер перед началом записи"))

        self.chk_countdown = QCheckBox(tr("settings_countdown_enable", "Включить обратный отсчёт перед началом записи"))
        self.chk_countdown.setChecked(getattr(self.cfg, "record_countdown_enabled", False))
        card_countdown.add_row(tr("settings_countdown_title", "Обратный отсчет"), tr("settings_countdown_desc", "Показывает таймер в шапке рамки, давая время приготовиться"), self.chk_countdown)

        self.spin_countdown_sec = QSpinBox()
        self.spin_countdown_sec.setRange(1, 60)
        self.spin_countdown_sec.setSuffix(tr("popup_video_timer_sec", " сек"))
        cur_sec = getattr(self.cfg, "record_countdown_seconds", 3)
        self.spin_countdown_sec.setValue(cur_sec)
        self.spin_countdown_sec.setEnabled(self.chk_countdown.isChecked())
        self.chk_countdown.toggled.connect(self.spin_countdown_sec.setEnabled)
        self.spin_countdown_sec.setFixedWidth(120)
        card_countdown.add_row(tr("settings_countdown_delay", "Задержка таймера:"), tr("settings_countdown_delay_desc", "Длительность ожидания перед стартом захвата кадров (1–60 сек)"), self.spin_countdown_sec)

        layout.addWidget(card_countdown)

        layout.addStretch()
        return create_scroll_page(container)

    @property
    def combo_countdown_sec(self):
        from ui.toolbars import _ComboCountdownCompatProxy
        return _ComboCountdownCompatProxy(self.spin_countdown_sec)

    # -------------------------------------------------------------
    # 3. ВКЛАДКА: ГОРЯЧИЕ КЛАВИШИ
    # -------------------------------------------------------------
    def _create_hotkeys_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        card_hotkey = SettingCard(tr("settings_hotkeys_group", "Глобальные горячие клавиши"))

        # 1. Захват области
        w_cap = QWidget()
        l_cap = QHBoxLayout(w_cap)
        l_cap.setContentsMargins(0, 0, 0, 0)
        l_cap.setSpacing(6)
        self.edit_hotkey_capture = QLineEdit(self.cfg.hotkey_capture)
        self.edit_hotkey_capture.setFixedWidth(170)
        btn_rec_cap = HotkeyRecorderButton(self.edit_hotkey_capture)
        btn_rec_cap.setFixedWidth(82)
        btn_reset_cap = QPushButton(tr("settings_btn_reset", "Сбросить"))
        btn_reset_cap.setFixedWidth(72)
        btn_reset_cap.clicked.connect(lambda: self.edit_hotkey_capture.setText(DEFAULT_HOTKEY))
        l_cap.addWidget(self.edit_hotkey_capture)
        l_cap.addWidget(btn_rec_cap)
        l_cap.addWidget(btn_reset_cap)
        card_hotkey.add_row(tr("settings_hk_capture", "Захват области (скриншот):"), tr("settings_hk_capture_desc", "Выделение области мышью для аннотаций и сохранения"), w_cap)

        # 2. Быстрый снимок всего экрана
        w_quick = QWidget()
        l_quick = QHBoxLayout(w_quick)
        l_quick.setContentsMargins(0, 0, 0, 0)
        l_quick.setSpacing(6)
        self.edit_hotkey_quick_screen = QLineEdit(getattr(self.cfg, "hotkey_quick_fullscreen", DEFAULT_HOTKEY_QUICK_FULLSCREEN))
        self.edit_hotkey_quick_screen.setFixedWidth(170)
        btn_rec_quick = HotkeyRecorderButton(self.edit_hotkey_quick_screen)
        btn_rec_quick.setFixedWidth(82)
        btn_reset_quick = QPushButton(tr("settings_btn_reset", "Сбросить"))
        btn_reset_quick.setFixedWidth(72)
        btn_reset_quick.clicked.connect(lambda: self.edit_hotkey_quick_screen.setText(DEFAULT_HOTKEY_QUICK_FULLSCREEN))
        l_quick.addWidget(self.edit_hotkey_quick_screen)
        l_quick.addWidget(btn_rec_quick)
        l_quick.addWidget(btn_reset_quick)
        card_hotkey.add_row(tr("settings_hk_quick_screen", "Быстрый скриншот экрана:"), tr("settings_hk_quick_desc", "Мгновенно сохраняет изображение всех экранов без рамок"), w_quick)

        # 3. Обычный Print Screen без выделения
        w_screenshot = QWidget()
        l_screenshot = QHBoxLayout(w_screenshot)
        l_screenshot.setContentsMargins(0, 0, 0, 0)
        l_screenshot.setSpacing(6)
        self.edit_hotkey_screenshot = QLineEdit(getattr(self.cfg, "hotkey_screenshot", DEFAULT_HOTKEY_SCREENSHOT))
        self.edit_hotkey_screenshot.setFixedWidth(170)
        btn_rec_screenshot = HotkeyRecorderButton(self.edit_hotkey_screenshot)
        btn_rec_screenshot.setFixedWidth(82)
        btn_reset_screenshot = QPushButton(tr("settings_btn_reset", "Сбросить"))
        btn_reset_screenshot.setFixedWidth(72)
        btn_reset_screenshot.clicked.connect(lambda: self.edit_hotkey_screenshot.setText(DEFAULT_HOTKEY_SCREENSHOT))
        l_screenshot.addWidget(self.edit_hotkey_screenshot)
        l_screenshot.addWidget(btn_rec_screenshot)
        l_screenshot.addWidget(btn_reset_screenshot)
        card_hotkey.add_row(tr("settings_hk_screenshot", "Обычный скриншот всего экрана:"), tr("settings_hk_screenshot_desc", "Сохраняет весь экран сразу в папку скриншотов без выделения и диалога"), w_screenshot)

        # 4. Запись всего экрана
        w_rec = QWidget()
        l_rec = QHBoxLayout(w_rec)
        l_rec.setContentsMargins(0, 0, 0, 0)
        l_rec.setSpacing(6)
        self.edit_hotkey_record_fs = QLineEdit(getattr(self.cfg, "hotkey_record_fullscreen", DEFAULT_HOTKEY_RECORD_FULLSCREEN))
        self.edit_hotkey_record_fs.setFixedWidth(170)
        btn_rec_fs = HotkeyRecorderButton(self.edit_hotkey_record_fs)
        btn_rec_fs.setFixedWidth(82)
        btn_reset_fs = QPushButton(tr("settings_btn_reset", "Сбросить"))
        btn_reset_fs.setFixedWidth(72)
        btn_reset_fs.clicked.connect(lambda: self.edit_hotkey_record_fs.setText(DEFAULT_HOTKEY_RECORD_FULLSCREEN))
        l_rec.addWidget(self.edit_hotkey_record_fs)
        l_rec.addWidget(btn_rec_fs)
        l_rec.addWidget(btn_reset_fs)
        card_hotkey.add_row(tr("settings_hk_rec_fs", "Запись всего экрана (видео):"), tr("settings_hk_rec_desc", "Старт видеозаписи рабочего стола на полный экран"), w_rec)

        # 4. Остановка записи
        w_stop = QWidget()
        l_stop = QHBoxLayout(w_stop)
        l_stop.setContentsMargins(0, 0, 0, 0)
        l_stop.setSpacing(6)
        self.edit_hotkey_stop = QLineEdit(getattr(self.cfg, "hotkey_stop_recording", DEFAULT_HOTKEY_STOP_RECORDING))
        self.edit_hotkey_stop.setFixedWidth(170)
        btn_rec_stop = HotkeyRecorderButton(self.edit_hotkey_stop)
        btn_rec_stop.setFixedWidth(82)
        btn_reset_stop = QPushButton(tr("settings_btn_reset", "Сбросить"))
        btn_reset_stop.setFixedWidth(72)
        btn_reset_stop.clicked.connect(lambda: self.edit_hotkey_stop.setText(DEFAULT_HOTKEY_STOP_RECORDING))
        l_stop.addWidget(self.edit_hotkey_stop)
        l_stop.addWidget(btn_rec_stop)
        l_stop.addWidget(btn_reset_stop)
        card_hotkey.add_row(tr("settings_hk_stop_rec", "Остановка записи:"), tr("settings_hk_stop_desc", "Завершает любую активную запись экрана или рамки"), w_stop)

        # 5. Подсветка интерактивных объектов
        w_hl = QWidget()
        l_hl = QHBoxLayout(w_hl)
        l_hl.setContentsMargins(0, 0, 0, 0)
        l_hl.setSpacing(6)
        self.edit_hotkey_highlight = QLineEdit(getattr(self.cfg, "hotkey_highlight_objects", DEFAULT_HOTKEY_HIGHLIGHT_OBJECTS))
        self.edit_hotkey_highlight.setFixedWidth(170)
        btn_rec_hl = HotkeyRecorderButton(self.edit_hotkey_highlight)
        btn_rec_hl.setFixedWidth(82)
        btn_reset_hl = QPushButton(tr("settings_btn_reset", "Сбросить"))
        btn_reset_hl.setFixedWidth(72)
        btn_reset_hl.clicked.connect(lambda: self.edit_hotkey_highlight.setText(DEFAULT_HOTKEY_HIGHLIGHT_OBJECTS))
        l_hl.addWidget(self.edit_hotkey_highlight)
        l_hl.addWidget(btn_rec_hl)
        l_hl.addWidget(btn_reset_hl)
        card_hotkey.add_row(tr("settings_hk_highlight", "Подсветка объектов (Alt):"), tr("settings_hk_highlight_desc", "Зажмите горячую клавишу для подсветки всех интерактивных фигур на экране"), w_hl)

        layout.addWidget(card_hotkey)

        card_info = SettingCard(tr("settings_hk_info_title", "Справка по горячим клавишам"))
        lbl_info = QLabel(tr("settings_hk_info_text", 
            "• Поддерживаются сочетания с <b>Ctrl</b>, <b>Shift</b>, <b>Alt</b>, <b>Win</b> и клавишами <b>Print Screen</b>, <b>F1–F12</b>, буквами и цифрами.<br>"
            "• Чтобы назначить новую клавишу, нажмите кнопку <b>«Назначить»</b> и зажмите желаемую комбинацию на клавиатуре.<br>"
            "• Для возврата к стандартным значениям используйте кнопку <b>«Сбросить»</b>."
        ))
        lbl_info.setTextFormat(Qt.TextFormat.RichText)
        lbl_info.setStyleSheet("color: #94a3b8; font-size: 11px; line-height: 1.5;")
        card_info.add_widget(lbl_info)
        layout.addWidget(card_info)

        layout.addStretch()
        return create_scroll_page(container)

    # -------------------------------------------------------------
    # 4. ВКЛАДКА: ОБЩИЕ, ЯЗЫК И СНИМКИ
    # -------------------------------------------------------------
    def _create_general_tab(self) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(12)

        # Карточка 1: Язык интерфейса
        card_lang = SettingCard(tr("settings_lang_group", "Язык интерфейса / Interface Language"))
        self.combo_lang = QComboBox()
        self.combo_lang.addItem(tr("settings_lang_auto", "Автоматически (системный) / Auto (System)"), "auto")
        self.combo_lang.addItem(tr("settings_lang_ru", "Русский (Russian)"), "ru")
        self.combo_lang.addItem(tr("settings_lang_en", "English (Английский)"), "en")

        cur_lang = getattr(self.cfg, "language", "auto")
        idx = 0
        for i in range(self.combo_lang.count()):
            if self.combo_lang.itemData(i) == cur_lang:
                idx = i
                break
        self.combo_lang.setCurrentIndex(idx)
        self.combo_lang.currentIndexChanged.connect(self._on_language_changed)
        self.combo_lang.setFixedWidth(260)
        card_lang.add_row(
            tr("settings_lang_label", "Язык программы:"),
            tr("settings_lang_desc", "Язык меняется сразу после выбора"),
            self.combo_lang
        )
        layout.addWidget(card_lang)

        # Карточка 2: Скриншоты и буфер обмена
        card_screen = SettingCard(tr("settings_screenshots_group", "Скриншоты и буфер обмена"))

        self.combo_save_format = QComboBox()
        self.combo_save_format.addItem(tr("popup_fmt_png", "PNG (Высокое качество без потерь)"), "png")
        self.combo_save_format.addItem(tr("popup_fmt_jpg", "JPEG / JPG (Сжатый компактный)"), "jpg")
        self.combo_save_format.addItem(tr("popup_fmt_webp", "WebP (Современный формат)"), "webp")
        cur_fmt = getattr(self.cfg, "last_save_format", "png").lower()
        idx_fmt = self.combo_save_format.findData(cur_fmt)
        if idx_fmt >= 0:
            self.combo_save_format.setCurrentIndex(idx_fmt)
        self.combo_save_format.setFixedWidth(270)
        card_screen.add_row(tr("settings_save_format", "Формат скриншотов:"), tr("settings_save_format_desc", "Формат сохранения файлов на диск по умолчанию"), self.combo_save_format)

        self.combo_copy_format = QComboBox()
        self.combo_copy_format.addItem(tr("popup_copy_dib", "DIB / Растровый (Мессенджеры)"), "standard")
        self.combo_copy_format.addItem(tr("popup_copy_png", "PNG (С сохранением прозрачности)"), "png")
        self.combo_copy_format.addItem(tr("popup_copy_jpg", "JPEG (Компактный размер)"), "jpg")
        self.combo_copy_format.addItem(tr("popup_copy_data_uri", "Data URI (Base64 текст)"), "data_uri")
        cur_cp = getattr(self.cfg, "default_copy_format", "standard")
        idx_cp = self.combo_copy_format.findData(cur_cp)
        if idx_cp >= 0:
            self.combo_copy_format.setCurrentIndex(idx_cp)
        self.combo_copy_format.setFixedWidth(270)
        card_screen.add_row(tr("settings_copy_format", "Формат копирования:"), tr("settings_copy_format_desc", "Тип данных изображения, помещаемых в буфер обмена Windows"), self.combo_copy_format)

        self.chk_auto_copy = QCheckBox(tr("settings_auto_copy", "Автоматически копировать скриншот в буфер обмена"))
        self.chk_auto_copy.setChecked(self.cfg.auto_copy_to_clipboard)
        card_screen.add_row(tr("settings_auto_copy_row", "Копирование в буфер"), tr("settings_auto_copy_desc", "Сразу помещать изображение в буфер обмена после выделения"), self.chk_auto_copy)

        self.chk_save_on_search = QCheckBox(tr("settings_save_on_search", "Сохранять скриншот при поиске по картинке"))
        self.chk_save_on_search.setChecked(getattr(self.cfg, "save_screenshot_on_search", True))
        card_screen.add_row(tr("settings_save_search_row", "Поиск по картинке"), tr("settings_save_on_search_desc", "Автоматически сохранять файл на диск при отправке в Яндекс / Google"), self.chk_save_on_search)

        layout.addWidget(card_screen)

        # Карточка 3: Параметры аннотирования по умолчанию
        card_annot = SettingCard(tr("settings_annotations_group", "Аннотирование по умолчанию"))

        self._current_default_color = getattr(self.cfg, "default_color", "#FF2E2E")
        self.btn_default_color = QPushButton()
        self.btn_default_color.setFixedWidth(120)
        self._update_color_button()
        self.btn_default_color.clicked.connect(self._pick_default_color)
        card_annot.add_row(tr("settings_default_color", "Основной цвет инструментов:"), tr("settings_default_color_desc", "Цвет карандаша, стрелок, рамок и текста при запуске"), self.btn_default_color)

        self.spin_stroke_width = QSpinBox()
        self.spin_stroke_width.setRange(1, 24)
        self.spin_stroke_width.setValue(getattr(self.cfg, "default_stroke_width", 4))
        self.spin_stroke_width.setSuffix(" px")
        self.spin_stroke_width.setFixedWidth(120)
        card_annot.add_row(tr("settings_default_stroke", "Толщина линий:"), tr("settings_default_stroke_desc", "Базовая толщина обводки для векторных фигур"), self.spin_stroke_width)

        self.spin_highlighter_alpha = QSpinBox()
        self.spin_highlighter_alpha.setRange(10, 100)
        self.spin_highlighter_alpha.setSingleStep(5)
        self.spin_highlighter_alpha.setValue(getattr(self.cfg, "highlighter_alpha", 90))
        self.spin_highlighter_alpha.setSuffix(" %")
        self.spin_highlighter_alpha.setFixedWidth(120)
        card_annot.add_row(tr("settings_highlighter_alpha", "Прозрачность маркера:"), tr("settings_highlighter_alpha_desc", "Уровень прозрачности для инструмента маркер-хайлайтер"), self.spin_highlighter_alpha)

        self.spin_font_size = QSpinBox()
        self.spin_font_size.setRange(10, 72)
        self.spin_font_size.setValue(getattr(self.cfg, "default_font_size", 18))
        self.spin_font_size.setSuffix(" pt")
        self.spin_font_size.setFixedWidth(120)
        card_annot.add_row(tr("settings_font_size_label", "Размер шрифта:"), tr("settings_font_size_desc", "Базовый размер надписей для текстовых заметок"), self.spin_font_size)

        layout.addWidget(card_annot)

        # Карточка 4: Поведение и автозапуск Windows
        card_auto = SettingCard(tr("settings_system_group", "Автозапуск и поведение системы"))

        self.chk_autostart = QCheckBox(tr("settings_autostart", "Запускать Framio вместе с Windows (в трей)"))
        self.chk_autostart.setStyleSheet("font-weight: 600; color: #38bdf8;")
        self.chk_autostart.setChecked(getattr(self.cfg, "autostart", False) or is_windows_autostart_enabled())
        card_auto.add_row(tr("settings_autostart_row", "Автозапуск Windows"), tr("settings_autostart_desc", "Автоматически запускать свернутым в трей при входе в систему"), self.chk_autostart)

        reg_status = tr("settings_reg_enabled", "Включен в реестре") if is_windows_autostart_enabled() else tr("settings_reg_disabled", "Отключен в реестре")
        self.lbl_reg_status = QLabel(tr("settings_reg_status", "Текущий статус в реестре Windows: {status}", status=f"<span style='color: #38bdf8; font-weight: 500;'>{reg_status}</span>"))
        self.lbl_reg_status.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_reg_status.setStyleSheet("color: #94a3b8; font-size: 11px;")
        card_auto.add_widget(self.lbl_reg_status)

        self.chk_sound = QCheckBox(tr("settings_play_sound", "Воспроизводить звуки затвора и уведомлений"))
        self.chk_sound.setChecked(self.cfg.play_sound)
        card_auto.add_row(tr("settings_sound_row", "Звуковые эффекты"), tr("settings_play_sound_desc", "Звуковой щелчок затвора при снимке экрана"), self.chk_sound)

        self.chk_open_folder = QCheckBox(tr("settings_open_folder", "Открывать папку с файлом после сохранения"))
        self.chk_open_folder.setChecked(getattr(self.cfg, "open_folder_after_save", False))
        card_auto.add_row(tr("settings_folder_row", "Проводник"), tr("settings_open_folder_desc", "Показывать созданный файл в проводнике Windows"), self.chk_open_folder)

        layout.addWidget(card_auto)

        layout.addStretch()
        return create_scroll_page(container)

    def _on_language_changed(self, _index: int):
        """Применяет язык сразу, не требуя Apply или перезапуска окна."""
        language = self.combo_lang.currentData() or "auto"
        if language == getattr(self.cfg, "language", "auto"):
            return

        current_tab = self.tabs.currentIndex()
        self.cfg.language = language
        set_language(language)
        self.config_mgr.save()
        self.settings_applied.emit()

        # Нельзя удалять текущий QComboBox прямо из его собственного signal:
        # Qt может завершить приложение внутри native event dispatch. Отложенная
        # перестройка выполняется в ближайшем цикле событий и визуально остаётся
        # мгновенной для пользователя.
        if not getattr(self, "_language_rebuild_pending", False):
            self._language_rebuild_pending = True
            QTimer.singleShot(0, lambda: self._finish_language_change(current_tab))

    def _finish_language_change(self, current_tab: int):
        self._language_rebuild_pending = False
        self._rebuild_tabs(current_tab)
        self.setWindowTitle(tr("settings_title", "Настройки Framio"))
        self.btn_apply.setText(tr("settings_btn_apply", "Применить"))
        self.btn_close.setText(tr("settings_btn_close", "Закрыть"))
        self._restore_status_text()

    def _update_color_button(self):
        c = self._current_default_color
        self.btn_default_color.setText(c)
        self.btn_default_color.setStyleSheet(f"""
            QPushButton {{
                background-color: {c};
                color: #ffffff;
                font-weight: 600;
                font-size: 11px;
                border: 2px solid #ffffff;
                border-radius: 6px;
                padding: 4px;
            }}
            QPushButton:hover {{
                border-color: #38bdf8;
            }}
        """)

    def _pick_default_color(self):
        color = QColorDialog.getColor(QColor(self._current_default_color), self, tr("settings_annot_picker_title", "Выберите цвет аннотаций"))
        if color.isValid():
            self._current_default_color = color.name().upper()
            self._update_color_button()

    # -------------------------------------------------------------
    # 5. ВКЛАДКА: СПРАВКА И ИНСТРУКЦИЯ
    # -------------------------------------------------------------
    def _create_help_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet("""
            QTextBrowser {
                background-color: #141720;
                color: #e2e8f0;
                border: 1px solid #272c3a;
                border-radius: 8px;
                padding: 16px;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
                line-height: 1.6;
            }
        """)

        hk_cap = self.cfg.hotkey_capture
        hk_quick = getattr(self.cfg, "hotkey_quick_fullscreen", DEFAULT_HOTKEY_QUICK_FULLSCREEN)
        hk_screenshot = getattr(self.cfg, "hotkey_screenshot", DEFAULT_HOTKEY_SCREENSHOT)
        hk_rec = getattr(self.cfg, "hotkey_record_fullscreen", DEFAULT_HOTKEY_RECORD_FULLSCREEN)
        hk_stop = getattr(self.cfg, "hotkey_stop_recording", DEFAULT_HOTKEY_STOP_RECORDING)

        help_html = tr("help_content", hk_capture=hk_cap, hk_quick=hk_quick, hk_screenshot=hk_screenshot, hk_rec_fs=hk_rec, hk_stop=hk_stop)
        browser.setHtml(help_html)
        layout.addWidget(browser)

        return widget

    # -------------------------------------------------------------
    # ОБРАБОТЧИКИ И ЛОГИКА СОХРАНЕНИЯ
    # -------------------------------------------------------------
    def _on_portable_toggled(self, checked: bool):
        if checked:
            res = QMessageBox.question(
                self,
                tr("settings_mode_portable", "Портативный режим"),
                tr("settings_reset_confirm_msg", "Переключить пути сохранения в папку программы (Captures)?"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if res == QMessageBox.StandardButton.Yes:
                self._reset_paths_to_portable()

    def _reset_paths_to_portable(self):
        base = get_base_dir()
        self.edit_screenshots_dir.setText(str(base / "Captures" / "Screenshots"))
        self.edit_videos_dir.setText(str(base / "Captures" / "Videos"))
        self.edit_gifs_dir.setText(str(base / "Captures" / "GIFs"))
        self.chk_portable.setChecked(True)

    def _reset_paths_to_standard(self):
        user_home = Path.home()
        self.edit_screenshots_dir.setText(str(user_home / "Pictures" / "Framio" / "Screenshots"))
        self.edit_videos_dir.setText(str(user_home / "Videos" / "Framio" / "Videos"))
        self.edit_gifs_dir.setText(str(user_home / "Videos" / "Framio" / "GIFs"))
        self.chk_portable.setChecked(False)

    def _open_dir(self, path_str: str):
        try:
            p = Path(path_str.strip())
            p.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(p))
        except Exception as e:
            QMessageBox.warning(self, tr("settings_folder_error_title", "Ошибка открытия папки"), tr("settings_folder_error_msg", "Не удалось открыть папку:\n{path}\n\nОшибка: {err}", path=path_str, err=e))

    def _browse_screenshots(self):
        d = QFileDialog.getExistingDirectory(self, tr("settings_choose_folder_screens", "Выберите папку для скриншотов"), self.edit_screenshots_dir.text())
        if d:
            self.edit_screenshots_dir.setText(d)

    def _browse_videos(self):
        d = QFileDialog.getExistingDirectory(self, tr("settings_choose_folder_videos", "Выберите папку для видео"), self.edit_videos_dir.text())
        if d:
            self.edit_videos_dir.setText(d)

    def _browse_gifs(self):
        d = QFileDialog.getExistingDirectory(self, tr("settings_choose_folder_gifs", "Выберите папку для GIF"), self.edit_gifs_dir.text())
        if d:
            self.edit_gifs_dir.setText(d)

    def _apply_settings(self):
        self.cfg.is_portable = self.chk_portable.isChecked()
        self.cfg.save_dir_screenshots = self.edit_screenshots_dir.text().strip()
        self.cfg.save_dir_videos = self.edit_videos_dir.text().strip()
        self.cfg.save_dir_gifs = self.edit_gifs_dir.text().strip()
        old_language = getattr(self.cfg, "language", "auto")
        self.cfg.hotkey_capture = self.edit_hotkey_capture.text().strip()
        self.cfg.hotkey_quick_fullscreen = self.edit_hotkey_quick_screen.text().strip()
        self.cfg.hotkey_screenshot = self.edit_hotkey_screenshot.text().strip() or DEFAULT_HOTKEY_SCREENSHOT
        self.cfg.hotkey_record_fullscreen = self.edit_hotkey_record_fs.text().strip()
        self.cfg.hotkey_stop_recording = self.edit_hotkey_stop.text().strip()
        if hasattr(self, "edit_hotkey_highlight"):
            self.cfg.hotkey_highlight_objects = self.edit_hotkey_highlight.text().strip() or DEFAULT_HOTKEY_HIGHLIGHT_OBJECTS

        self.cfg.language = self.combo_lang.currentData() or "auto"
        set_language(self.cfg.language)
        
        self.cfg.video_fps = int(self.combo_fps.currentText())
        self.cfg.video_codec = self.combo_codec.currentText()
        self.cfg.video_quality = self.combo_quality.currentText()
        self.cfg.compress_video = self.chk_compress_video.isChecked()
        
        self.cfg.gif_fps = int(self.combo_gif_fps.currentText())
        data_gc = self.combo_gif_colors.currentData() or (64, "none")
        self.cfg.gif_colors = data_gc[0]
        self.cfg.gif_dither = data_gc[1]
        self.cfg.gif_optimize = self.chk_gif_opt.isChecked()
        self.cfg.compress_gif = self.chk_compress_gif.isChecked()

        self.cfg.record_mic = self.chk_record_mic.isChecked()
        self.cfg.record_system = self.chk_record_system.isChecked()
        self.cfg.record_countdown_enabled = self.chk_countdown.isChecked()
        self.cfg.record_countdown_seconds = self.spin_countdown_sec.value()

        self.cfg.auto_copy_to_clipboard = self.chk_auto_copy.isChecked()
        self.cfg.save_screenshot_on_search = self.chk_save_on_search.isChecked()
        self.cfg.play_sound = self.chk_sound.isChecked()
        self.cfg.open_folder_after_save = self.chk_open_folder.isChecked()
        self.cfg.autostart = self.chk_autostart.isChecked()
        set_windows_autostart(self.cfg.autostart)

        # Специфичные настройки скриншотов и аннотаций
        self.cfg.last_save_format = self.combo_save_format.currentData() or "png"
        self.cfg.default_copy_format = self.combo_copy_format.currentData() or "standard"
        self.cfg.default_color = self._current_default_color
        self.cfg.default_stroke_width = self.spin_stroke_width.value()
        self.cfg.highlighter_alpha = self.spin_highlighter_alpha.value()
        self.cfg.default_font_size = self.spin_font_size.value()

        self.config_mgr.save()
        self.settings_applied.emit()

        if old_language != self.cfg.language:
            current_tab = self.tabs.currentIndex()
            self._rebuild_tabs(current_tab)
            self.setWindowTitle(tr("settings_title", "Настройки Framio"))
            self.btn_apply.setText(tr("settings_btn_apply", "Применить"))
            self.btn_close.setText(tr("settings_btn_close", "Закрыть"))

        # Обновляем отображение статуса автозапуска
        reg_status = tr("settings_reg_enabled", "Включен в реестре") if is_windows_autostart_enabled() else tr("settings_reg_disabled", "Отключен в реестре")
        self.lbl_reg_status.setText(tr("settings_reg_status", "Текущий статус в реестре Windows: {status}", status=f"<span style='color: #38bdf8; font-weight: 500;'>{reg_status}</span>"))

        self.lbl_status.setText("✓ " + tr("settings_btn_apply", "Настройки успешно применены!"))
        self.lbl_status.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: 600;")
        QTimer.singleShot(3000, self._restore_status_text)

    def _restore_status_text(self):
        base_dir = get_base_dir()
        cfg_mode = tr("settings_mode_portable", "Портативный") if self.cfg.is_portable else tr("settings_mode_system", "Системный")
        self.lbl_status.setText(tr("settings_portable_badge", "Режим: {mode} • {path}", mode=cfg_mode, path=str(base_dir)))
        self.lbl_status.setStyleSheet("color: #8892b0; font-size: 11px;")

    def _on_close_clicked(self):
        self._apply_settings()
        self.accept()
