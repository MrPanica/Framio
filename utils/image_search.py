# -*- coding: utf-8 -*-
"""
Поиск по изображениям через Google Lens и Яндекс.Картинки.

Изображение никогда не отправляется на промежуточный хостинг. Для Google Lens
приложение открывает одноразовую локальную HTML-форму, которая отправляет PNG
браузерной multipart-навигацией прямо на официальный upload-адрес Google.
"""

import os
import subprocess
import base64
import html
import tempfile
import threading
import time
import webbrowser
import urllib.parse
import json
from pathlib import Path
import requests
from utils.i18n import get_current_language, tr

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


def _png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    """Возвращает размеры PNG для параметров официальной формы Lens."""
    if len(png_bytes) >= 24 and png_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            return int.from_bytes(png_bytes[16:20], "big"), int.from_bytes(png_bytes[20:24], "big")
        except (TypeError, ValueError):
            pass
    return 0, 0


def _create_google_lens_upload_page(png_bytes: bytes) -> str:
    """Создаёт локальную форму, которая отправляет PNG прямо в Google Lens.

    Google выдаёт временный ``vsrid`` только после браузерной навигации из
    формы. POST из requests получает похожий redirect, но результат оказывается
    привязан к другой сессии браузера и становится недействительным. Локальная
    HTML-форма сохраняет прямую загрузку в Google и даёт браузеру самому
    сохранить cookies и контекст навигации.
    """
    width, height = _png_dimensions(png_bytes)
    params = {
        "ep": "cntpubb",
        "hl": get_current_language(),
        "st": str(int(time.time() * 1000)),
        "cd": "",
        "re": "df",
        "s": "4",
        "vph": str(height) if height else "",
        "vpw": str(width) if width else "",
    }
    upload_url = f"{GOOGLE_LENS_UPLOAD_URL}?{urllib.parse.urlencode(params)}"
    encoded_png = base64.b64encode(png_bytes).decode("ascii")
    page = f"""<!doctype html>
<meta charset="utf-8">
<title>Framio - Google Lens upload</title>
<form id="framio-google-lens-upload" action="{html.escape(upload_url, quote=True)}" method="post" enctype="multipart/form-data">
  <input id="framio-google-lens-file" name="encoded_image" type="file">
</form>
<script>
(() => {{
  const bytes = Uint8Array.from(atob("{encoded_png}"), c => c.charCodeAt(0));
  const transfer = new DataTransfer();
  transfer.items.add(new File([bytes], "screenshot.png", {{type: "image/png"}}));
  document.getElementById("framio-google-lens-file").files = transfer.files;
  document.getElementById("framio-google-lens-upload").submit();
}})();
</script>
"""
    descriptor, path = tempfile.mkstemp(prefix="framio-google-lens-", suffix=".html")
    with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as stream:
        stream.write(page)
    return path


def _schedule_upload_page_cleanup(path: str, delay: float = 60.0) -> None:
    """Удаляет одноразовую локальную форму после отправки."""
    def cleanup() -> None:
        time.sleep(delay)
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    threading.Thread(target=cleanup, daemon=True, name="framio-google-lens-cleanup").start()


def _open_google_lens_upload(png_bytes: bytes, notify_func=None) -> None:
    """Открывает браузер с прямой multipart-загрузкой PNG в Google Lens."""
    upload_page = _create_google_lens_upload_page(png_bytes)
    _schedule_upload_page_cleanup(upload_page)
    opened = open_in_browser(Path(upload_page).as_uri())
    if notify_func:
        if opened:
            notify_func("Google Lens", tr("image_search_google_ready"))
        else:
            notify_func("Google Lens", tr("image_search_google_failed"))


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

    Google получает PNG прямой multipart-загрузкой из локальной формы браузера.
    Серверный POST из requests здесь намеренно не используется: его временный
    redirect привязан к другой сессии и Google помечает его недействительным.
    """
    try:
        if engine == "google":
            _open_google_lens_upload(png_bytes, notify_func=notify_func)

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
