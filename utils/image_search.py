# -*- coding: utf-8 -*-
"""
Поиск по изображениям через Google Lens и Яндекс.Картинки.

Изображение никогда не отправляется на промежуточный хостинг. Для Google Lens
используется его обычная страница поиска с вставкой изображения из буфера: URL
с ``vsrid``, который Google выдаёт внутреннему загрузчику, привязан к сессии и
может стать недействительным сразу после открытия в другом контексте браузера.
"""

import os
import subprocess
import threading
import time
import webbrowser
import urllib.parse
import json
import requests
from utils.i18n import tr

GOOGLE_LENS_URL = "https://lens.google.com/"
YANDEX_IMAGE_SEARCH_URL = "https://yandex.ru/images/search"

def open_in_browser(url: str) -> bool:
    """Гарантированно открывает URL в браузере по умолчанию в новой вкладке."""
    # 1. Попытка через стандартный webbrowser.open_new_tab
    try:
        if webbrowser.open_new_tab(url):
            return True
    except Exception:
        pass

    # 2. Попытка через os.startfile
    try:
        os.startfile(url)
        return True
    except Exception:
        pass

    # 3. Попытка через Windows Shell cmd start
    try:
        subprocess.Popen(f'cmd /c start "" "{url}"', shell=True)
        return True
    except Exception:
        pass

    return False


def _foreground_window_is_browser() -> bool:
    """Проверяет, что активное окно похоже на окно браузера.

    Это защита от случайной вставки в другое приложение, если пользователь
    успел переключиться во время открытия Google Lens. На других ОС вставка
    через системные клавиши не используется.
    """
    if os.name != "nt":
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buffer, len(buffer))
        title = buffer.value.casefold()
        return any(
            marker in title
            for marker in ("google", "lens", "chrome", "edge", "firefox", "opera", "яндекс")
        )
    except Exception:
        return False


def _paste_into_browser_after_open(delay: float = 1.2) -> None:
    """Пытается вставить уже скопированное изображение в открытую вкладку.

    Вызов выполняется только после проверки активного окна. Если браузер не
    успел получить фокус или система не Windows, пользователь может вставить
    изображение обычным Ctrl+V из уведомления.
    """
    if os.name != "nt":
        return

    def paste() -> None:
        time.sleep(delay)
        if not _foreground_window_is_browser():
            return
        try:
            import ctypes

            user32 = ctypes.windll.user32
            key_event = user32.keybd_event
            key_event(0x11, 0, 0, 0)  # Ctrl down
            key_event(0x56, 0, 0, 0)  # V down
            key_event(0x56, 0, 2, 0)  # V up
            key_event(0x11, 0, 2, 0)  # Ctrl up
        except Exception:
            pass

    threading.Thread(target=paste, daemon=True, name="framio-google-lens-paste").start()


def _open_google_lens_from_clipboard(notify_func=None) -> None:
    """Открывает Google Lens и запускает безопасную вставку из буфера."""
    opened = open_in_browser(GOOGLE_LENS_URL)
    if opened:
        _paste_into_browser_after_open()
    if notify_func:
        notify_func(
            "Google Lens",
            tr("image_search_google_ready"),
        )


def _yandex_image_search_url(png_bytes: bytes) -> str | None:
    """Отправляет PNG непосредственно в загрузчик Яндекс.Картинок."""
    params = {
        "rpt": "imageview",
        "format": "json",
        "request": json.dumps(
            {"blocks": [{"block": "b-page_type_search-by-image__link"}]},
            separators=(",", ":"),
        ),
    }
    files = {"upfile": ("screenshot.png", png_bytes, "image/png")}
    try:
        response = requests.post(
            YANDEX_IMAGE_SEARCH_URL,
            params=params,
            files=files,
            timeout=10,
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        for block in payload.get("blocks", []):
            block_params = block.get("params", {})
            cbir_id = block_params.get("cbirId")
            original_url = block_params.get("originalImageUrl")
            if cbir_id and original_url:
                query = urllib.parse.urlencode(
                    {
                        "rpt": "imageview",
                        "cbir_id": cbir_id,
                        "url": original_url,
                    }
                )
                return f"{YANDEX_IMAGE_SEARCH_URL}?{query}"
    except Exception as exc:
        print(f"[ImageSearch] Yandex Images upload failed: {exc}")
    return None


def search_by_image(engine: str, png_bytes: bytes, notify_func=None):
    """
    Выполняет поиск по картинке в указанном поисковике (Google Lens или Яндекс Картинки).
    Открывает браузер с изображением в новой вкладке.

    Google Lens не использует выданную сервером временную ссылку: такие ссылки
    Google помечает как недействительные при открытии вне исходной сессии.
    Вместо этого приложение открывает официальный интерфейс Lens и вставляет
    PNG из системного буфера.
    """
    try:
        if engine == "google":
            _open_google_lens_from_clipboard(notify_func=notify_func)

        else:
            # Яндекс Картинки
            target_url = _yandex_image_search_url(png_bytes)
            if target_url:
                open_in_browser(target_url)
            else:
                # Резервный вариант: открываем Яндекс Картинки
                open_in_browser("https://yandex.ru/images/search?rpt=imageview")
                if notify_func:
                    notify_func(
                        "Яндекс Картинки",
                        tr("image_search_yandex_fallback"),
                    )

    except Exception as e:
        print(f"[ImageSearch] Общая ошибка visual search: {e}")
        if notify_func:
            notify_func("Ошибка поиска", f"Не удалось выполнить поиск: {e}")
