# -*- coding: utf-8 -*-
"""
Конфигурация и управление настройками приложения Framio.
Поддерживает истинный портативный режим с автоматической релокацией путей при переносе
программы на другой диск, флешку или в другой каталог.
"""

import os
import json
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass, asdict

DEFAULT_HOTKEY = "Ctrl+Shift+Print Screen"
DEFAULT_HOTKEY_QUICK_FULLSCREEN = "Ctrl+Print Screen"
DEFAULT_HOTKEY_RECORD_FULLSCREEN = "Ctrl+Shift+F9"
DEFAULT_HOTKEY_STOP_RECORDING = "Ctrl+Shift+F10"
DEFAULT_HOTKEY_HIGHLIGHT_OBJECTS = "Alt"

def get_base_dir() -> Path:
    """Возвращает базовую директорию приложения (папку с .exe или скриптом)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent.resolve()
    return Path(__file__).parent.resolve()


def normalize_portable_path(path_str: str, subfolder: str, is_portable: bool) -> str:
    """
    Нормализует путь сохранения.
    В портативном режиме автоматически перепривязывает путь к текущей папке запуска программы,
    если программа была перемещена (например, с диска O: на диск E: или в другую папку).
    """
    base = get_base_dir()
    if is_portable:
        if not path_str or "Pictures\\Framio" in path_str or "Videos\\Framio" in path_str or "Pictures\\LightCap" in path_str or "Videos\\LightCap" in path_str:
            return str(base / "Captures" / subfolder)

        p = Path(path_str)
        if not p.is_absolute():
            return str((base / p).resolve())

        # Если путь абсолютный, проверяем, находится ли он внутри текущего base_dir
        try:
            p.relative_to(base)
            return str(p)
        except ValueError:
            # Путь принадлежит другому диску или прежней папке
            if "Captures" in p.parts:
                captures_idx = p.parts.index("Captures")
                rel_parts = p.parts[captures_idx:]
                return str(base.joinpath(*rel_parts))
            return str(base / "Captures" / subfolder)
    else:
        user_home = Path.home()
        if not path_str:
            folder = "Pictures" if subfolder == "Screenshots" else "Videos"
            return str(user_home / folder / "Framio" / subfolder)
        return str(Path(path_str).resolve())


@dataclass
class AppConfig:
    # Портативный режим: все настройки и файлы хранятся в папке приложения
    is_portable: bool = True

    # Директории сохранения по типам
    save_dir_screenshots: str = ""
    save_dir_videos: str = ""
    save_dir_gifs: str = ""
    
    # Язык интерфейса: "auto" (системный), "ru" (русский), "en" (английский)
    language: str = "auto"

    # Горячие клавиши
    hotkey_capture: str = DEFAULT_HOTKEY
    hotkey_quick_fullscreen: str = DEFAULT_HOTKEY_QUICK_FULLSCREEN
    hotkey_record_fullscreen: str = DEFAULT_HOTKEY_RECORD_FULLSCREEN
    hotkey_stop_recording: str = DEFAULT_HOTKEY_STOP_RECORDING
    hotkey_highlight_objects: str = DEFAULT_HOTKEY_HIGHLIGHT_OBJECTS
    
    # Видео настройки
    video_fps: int = 30
    video_codec: str = "mp4v"  # mp4v, avc1, XVID
    video_quality: str = "Высокое"
    
    # GIF настройки
    gif_fps: int = 15
    gif_optimize: bool = True
    compress_gif: bool = True  # Сжатие GIF после записи
    gif_colors: int = 64       # Количество цветов (256, 128, 64, 32). Меньше цветов = существенно меньше размер файла
    gif_dither: str = "none"   # "none" (минимальный вес) или "bayer" (мягкие полутона)
    
    # Сжатие видео
    compress_video: bool = True  # Сжатие видео после записи
    
    # Аудио настройки
    record_mic: bool = True      # Запись микрофона
    record_system: bool = True   # Запись звука из игр/системы (WASAPI loopback)
    
    # Таймер перед началом записи (обратный отсчет)
    record_countdown_enabled: bool = False
    record_countdown_seconds: int = 3
    
    # Рисование
    default_color: str = "#FF2E2E"
    default_stroke_width: int = 4
    default_font_size: int = 18
    default_font_family: str = "Segoe UI"
    default_font_bold: bool = True
    default_font_underline: bool = False
    highlighter_alpha: int = 90
    
    # Поведение
    auto_copy_to_clipboard: bool = True
    default_copy_format: str = "standard"  # standard, png, jpg, data_uri
    last_save_format: str = "png"          # png, jpg, webp
    target_window_title: str = ""          # Название целевого окна для записи (пусто = весь экран)
    play_sound: bool = True
    open_folder_after_save: bool = False
    save_screenshot_on_search: bool = True  # Сохранять ли скриншот на диск при поиске по картинке
    autostart: bool = False                 # Автозапуск вместе с Windows (в трей)
    
    def __post_init__(self):
        self.save_dir_screenshots = normalize_portable_path(self.save_dir_screenshots, "Screenshots", self.is_portable)
        self.save_dir_videos = normalize_portable_path(self.save_dir_videos, "Videos", self.is_portable)
        self.save_dir_gifs = normalize_portable_path(self.save_dir_gifs, "GIFs", self.is_portable)

    @property
    def screenshots_path(self) -> Path:
        return Path(self.save_dir_screenshots)

    @property
    def videos_path(self) -> Path:
        return Path(self.save_dir_videos)

    @property
    def gifs_path(self) -> Path:
        return Path(self.save_dir_gifs)


class ConfigManager:
    _instance = None

    def __init__(self):
        base = get_base_dir()
        self.base_dir = base
        portable_file = base / "settings.json"

        # Проверяем, доступна ли запись в каталог программы
        can_write_base = self._is_directory_writable(base)
        self.portable_directory_writable = can_write_base
        self.storage_warning = not can_write_base

        if can_write_base:
            self.config_dir = base
            self.config_file = portable_file
        else:
            # Если папка защищена от записи (например, C:\Program Files), используем AppData
            app_data = os.getenv("APPDATA") or str(Path.home() / ".config")
            self.config_dir = Path(app_data) / "Framio"
            self.config_file = self.config_dir / "settings.json"
        self.storage_path = self.config_dir / "Captures"

        self.config = AppConfig()
        if not can_write_base:
            self._use_user_storage_defaults()
        self.load()
        if not can_write_base:
            self._rebase_protected_default_paths()
            # Сохраняем результат один раз, чтобы при следующем запуске не
            # возвращать защищённый portable-путь обратно в настройки.
            self.save()

    @staticmethod
    def _is_directory_writable(directory: Path) -> bool:
        """Проверяет запись временным файлом и не меняет ACL каталога."""
        name = None
        try:
            directory = Path(directory)
            directory.mkdir(parents=True, exist_ok=True)
            fd, name = tempfile.mkstemp(prefix=".framio_write_test_", dir=str(directory))
            os.close(fd)
            Path(name).unlink(missing_ok=True)
            return True
        except Exception:
            try:
                if name:
                    Path(name).unlink(missing_ok=True)
            except Exception:
                pass
            return False

    def _use_user_storage_defaults(self):
        root = self.storage_path
        self.config.is_portable = False
        self.config.save_dir_screenshots = str(root / "Screenshots")
        self.config.save_dir_videos = str(root / "Videos")
        self.config.save_dir_gifs = str(root / "GIFs")

    def _rebase_protected_default_paths(self):
        """Переносит только стандартные portable-пути, не трогая выбор пользователя."""
        defaults = {
            "save_dir_screenshots": "Screenshots",
            "save_dir_videos": "Videos",
            "save_dir_gifs": "GIFs",
        }
        for field, subfolder in defaults.items():
            current = Path(getattr(self.config, field, ""))
            try:
                current.relative_to(self.base_dir)
                is_under_base = True
            except (ValueError, TypeError):
                is_under_base = False
            if not str(current) or is_under_base:
                setattr(self.config, field, str(self.storage_path / subfolder))
        self.config.is_portable = False

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls._instance = ConfigManager()
        return cls._instance

    def load(self):
        needs_save = False
        try:
            if self.config_file.exists():
                with open(self.config_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.config = AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
                
                # Проверяем, изменились ли пути при переносе программы
                if self.config.is_portable:
                    new_screens = normalize_portable_path(self.config.save_dir_screenshots, "Screenshots", True)
                    new_videos = normalize_portable_path(self.config.save_dir_videos, "Videos", True)
                    new_gifs = normalize_portable_path(self.config.save_dir_gifs, "GIFs", True)
                    if (new_screens != self.config.save_dir_screenshots or
                        new_videos != self.config.save_dir_videos or
                        new_gifs != self.config.save_dir_gifs):
                        self.config.save_dir_screenshots = new_screens
                        self.config.save_dir_videos = new_videos
                        self.config.save_dir_gifs = new_gifs
                        needs_save = True
            else:
                # Если в портативной папке файла нет, проверяем APPDATA для импорта
                app_data = os.getenv("APPDATA")
                if app_data:
                    for old_name in ("Framio", "LightCap"):
                        old_cfg = Path(app_data) / old_name / "settings.json"
                        if old_cfg.exists():
                            try:
                                with open(old_cfg, "r", encoding="utf-8") as f:
                                    data = json.load(f)
                                    self.config = AppConfig(**{k: v for k, v in data.items() if k in AppConfig.__dataclass_fields__})
                                break
                            except Exception:
                                pass
                needs_save = True
        except Exception as e:
            print(f"[ConfigManager] Ошибка чтения конфигурации: {e}")

        if needs_save:
            self.save()
        else:
            self.ensure_directories()

    def save(self):
        try:
            self.config_dir.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(asdict(self.config), f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"[ConfigManager] Ошибка сохранения конфигурации: {e}")
        self.ensure_directories()

    def ensure_directories(self):
        try:
            Path(self.config.save_dir_screenshots).mkdir(parents=True, exist_ok=True)
            Path(self.config.save_dir_videos).mkdir(parents=True, exist_ok=True)
            Path(self.config.save_dir_gifs).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"[ConfigManager] Ошибка создания каталогов: {e}")
