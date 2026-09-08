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
import base64
import requests


FREEIMAGE_API_KEY = os.environ.get("FRAMIO_FREEIMAGE_API_KEY", "").strip()

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


def upload_image_to_cdn(png_bytes: bytes) -> str | None:
    """Загружает PNG на быстрый анонимный CDN и возвращает прямую ссылку на файл."""
    # Провайдер 1: FreeImage.host. Его ключ задаётся только через окружение;
    # секреты не должны попадать в исходники и публичные сборки.
    if FREEIMAGE_API_KEY:
        try:
            b64 = base64.b64encode(png_bytes).decode("ascii")
            r = requests.post(
                "https://freeimage.host/api/1/upload",
                data={
                    "key": FREEIMAGE_API_KEY,
                    "action": "upload",
                    "source": b64,
                    "format": "json"
                },
                timeout=5
            )
            if r.status_code == 200:
                url = r.json().get("image", {}).get("url")
                if url:
                    return url
        except Exception as e:
            print(f"[ImageSearch] FreeImage upload failed: {e}")

    # Провайдер 2: Uguu.se (прямой CDN URL, без задержек)
    try:
        files = {"files[]": ("screenshot.png", png_bytes, "image/png")}
        r = requests.post("https://uguu.se/upload.php", files=files, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("success") and data.get("files"):
                file_url = data["files"][0].get("url")
                if file_url:
                    return file_url
    except Exception as e:
        print(f"[ImageSearch] Uguu upload failed: {e}")

    # Провайдер 3: tmpfiles.org (резервный прямой CDN)
    try:
        files = {"file": ("screenshot.png", png_bytes, "image/png")}
        r = requests.post("https://tmpfiles.org/api/v1/upload", files=files, timeout=5)
        if r.status_code == 200:
            raw_url = r.json().get("data", {}).get("url", "")
            if raw_url:
                return raw_url.replace("tmpfiles.org/", "tmpfiles.org/dl/")
    except Exception as e:
        print(f"[ImageSearch] tmpfiles upload failed: {e}")

    return None


def search_by_image(engine: str, png_bytes: bytes, notify_func=None):
    """
    Выполняет поиск по картинке в указанном поисковике (Google Lens или Яндекс Картинки).
    Открывает браузер со вставленным изображением в новой вкладке.
    """
    try:
        if engine == "google":
            target_url = None
            # Шаг 1: Загружаем на быстрый CDN и открываем uploadbyurl напрямую в браузере.
            # Это позволяет Google Lens в браузере создать собственную валидную сессию без ошибки истекшего токена.
            cdn_url = upload_image_to_cdn(png_bytes)
            if cdn_url:
                enc = urllib.parse.quote(cdn_url, safe="")
                target_url = f"https://lens.google.com/uploadbyurl?url={enc}"

            if not target_url:
                # Резервная попытка через прямой Google Lens API
                try:
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                    }
                    files = {"encoded_image": ("screenshot.png", png_bytes, "image/png")}
                    res = requests.post("https://lens.google.com/v3/upload", files=files, headers=headers, allow_redirects=False, timeout=6)
                    loc = res.headers.get("Location")
                    if loc:
                        target_url = loc
                except Exception as e:
                    print(f"[ImageSearch] Google direct upload failed: {e}")

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
            target_url = None
            cdn_url = upload_image_to_cdn(png_bytes)
            if cdn_url:
                enc = urllib.parse.quote(cdn_url, safe="")
                target_url = f"https://yandex.ru/images/search?rpt=imageview&url={enc}"

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
