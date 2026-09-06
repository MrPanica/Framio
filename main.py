# -*- coding: utf-8 -*-
"""
Главная точка входа в приложение Framio.
Поддерживает системный трей, глобальные хоткеи, захват экрана,
а также живую запись видео и GIF в виде нативного Windows приложения.
"""

import sys
import os
import ctypes
import subprocess
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from PyQt6.QtCore import Qt, QObject, pyqtSlot, QTimer, QPointF, QRectF
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu
)
from PyQt6.QtGui import (
    QIcon, QPixmap, QPainter, QColor, QFont, QAction, QPen, QBrush, QLinearGradient
)

from datetime import datetime
from config import ConfigManager
from ui.overlay import OverlayWindow
from ui.recording_window import RecordingFrameWindow
from ui.settings_dialog import SettingsDialog
from utils.hotkey_manager import GlobalHotkeyManager
from utils.screen_lock import safe_grab_screen_pixmap
from utils.sound import play_capture_sound
from utils.i18n import tr

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
        self._last_notification_path = ""

        # Системный трей
        self.icon = make_app_icon(is_recording=False)
        self.tray = QSystemTrayIcon(self.icon)
        self._update_tray_state()
        self.tray.show()

        # Глобальные горячие клавиши
        self.hotkey_mgr = GlobalHotkeyManager(
            hotkey_capture=self.cfg.hotkey_capture,
            hotkey_quick_fullscreen=getattr(self.cfg, "hotkey_quick_fullscreen", "Ctrl+Print Screen"),
            hotkey_record_fullscreen=getattr(self.cfg, "hotkey_record_fullscreen", "Ctrl+Shift+F9"),
            hotkey_stop_recording=getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
        )
        self.hotkey_mgr.capture_triggered.connect(self.trigger_capture)
        self.hotkey_mgr.quick_fullscreen_triggered.connect(self.quick_fullscreen_capture)
        self.hotkey_mgr.record_fullscreen_triggered.connect(self.start_fullscreen_recording)
        self.hotkey_mgr.stop_recording_triggered.connect(self.stop_all_recordings)
        self.hotkey_mgr.start()

        # Клик по трею и по уведомлениям
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.messageClicked.connect(self._on_tray_notification_clicked)

    def add_recording(self, rec_window: RecordingFrameWindow):
        if rec_window not in self.active_recordings:
            self.active_recordings.append(rec_window)
            rec_window.recording_closed.connect(lambda path, w=rec_window: self._on_recording_saved(w, path))
            rec_window.save_progress.connect(self._on_recording_progress)
            self._update_tray_state()

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
            fs_key = getattr(self.cfg, "hotkey_record_fullscreen", "Ctrl+Shift+F9")
            stop_key = getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
            self.tray.setToolTip(
                f"{tr('app_title')}\n"
                f"{tr('tray_hotkey_capture', key=cap_key)}\n"
                f"{tr('tray_hotkey_quick_screen', key=quick_key)}\n"
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
            record_fullscreen=getattr(self.cfg, "hotkey_record_fullscreen", "Ctrl+Shift+F9"),
            stop_recording=getattr(self.cfg, "hotkey_stop_recording", "Ctrl+Shift+F10")
        )
        self._update_tray_state()

    def _open_folder(self, path_str):
        p = Path(path_str)
        p.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(p))

    def quit_app(self):
        self.hotkey_mgr.stop()
        for rec in list(self.active_recordings):
            try:
                rec.cancel_recording()
            except Exception:
                pass
        self.active_recordings.clear()
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
