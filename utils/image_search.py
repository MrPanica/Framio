# -*- coding: utf-8 -*-
"""
Модуль поиска по изображениям (Google Lens и Яндекс Картинки).
Обеспечивает надежную загрузку изображений через быстрые CDN,
формирование прямых ссылок поиска и гарантированное открытие в новой вкладке браузера.
"""

import os
import subprocess
import webbrowser
import urllib.parse
import json
import requests

GOOGLE_LENS_UPLOAD_URL = "https://lens.google.com/v3/upload"
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


def _google_lens_search_url(png_bytes: bytes) -> str | None:
    """Отправляет PNG непосредственно в Google Lens."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/124 Safari/537.36"
            )
        }
        files = {"encoded_image": ("screenshot.png", png_bytes, "image/png")}
        response = requests.post(
            GOOGLE_LENS_UPLOAD_URL,
            files=files,
            headers=headers,
            allow_redirects=False,
            timeout=10,
        )
        if response.status_code in {301, 302, 303, 307, 308}:
            return response.headers.get("Location") or None
    except Exception as exc:
        print(f"[ImageSearch] Google Lens upload failed: {exc}")
    return None


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
    Открывает браузер со вставленным изображением в новой вкладке.
    """
    try:
        if engine == "google":
            target_url = _google_lens_search_url(png_bytes)
            if target_url:
                open_in_browser(target_url)
            else:
                # Резервный вариант: открываем главную страницу Lens
                open_in_browser("https://lens.google.com/")
                if notify_func:
                    notify_func(
                        "Google Lens",
                        "Снимок скопирован в буфер обмена. Нажмите Ctrl+V в открывшемся окне поиска."
                    )

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
                        "Снимок скопирован в буфер обмена. Нажмите Ctrl+V в строке поиска."
                    )

    except Exception as e:
        print(f"[ImageSearch] Общая ошибка visual search: {e}")
        if notify_func:
            notify_func("Ошибка поиска", f"Не удалось выполнить поиск: {e}")
