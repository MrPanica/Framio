# -*- coding: utf-8 -*-
"""
Модуль локального оптического распознавания текста (OCR).
Извлекает текст с изображений с поддержкой различных шрифтов и языков
(Русский, Английский и любые установленные в Windows языковые пакеты OCR).
Работает 100% локально и автономно без внешних сетевых запросов через Windows.Media.Ocr.
"""

import os
import sys
from typing import Optional, Union
import numpy as np

try:
    import cv2
except Exception:
    cv2 = None

try:
    from PyQt6.QtGui import QImage
except Exception:
    QImage = None

_WINOCR_AVAILABLE = False
try:
    import winocr
    from winrt.windows.media.ocr import OcrEngine
    _WINOCR_AVAILABLE = True
except Exception:
    _WINOCR_AVAILABLE = False


def is_ocr_available() -> bool:
    """Проверяет доступность локального OCR в системе."""
    return _WINOCR_AVAILABLE


def get_available_ocr_languages() -> list[dict]:
    """
    Возвращает список установленных в Windows языковых пакетов распознавания текста.
    Каждый элемент: {"tag": "ru", "name": "Русский"}
    """
    languages = []
    if _WINOCR_AVAILABLE:
        try:
            for lang in OcrEngine.available_recognizer_languages:
                languages.append({
                    "tag": str(lang.language_tag),
                    "name": str(lang.display_name),
                })
        except Exception as e:
            print(f"[OCR] Ошибка получения языков OcrEngine: {e}")

    if not languages:
        # Резервные стандартные языки
        languages = [
            {"tag": "ru", "name": "Русский"},
            {"tag": "en-US", "name": "English"},
        ]
    return languages


def _convert_to_bgr(image: Union["QImage", np.ndarray]) -> Optional[np.ndarray]:
    """Конвертирует QImage или numpy-массив в стандартный 3-канальный BGR numpy-массив."""
    if image is None:
        return None

    if QImage is not None and isinstance(image, QImage):
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return None
        from utils.screen_lock import qimage_to_cv2_bgr
        return qimage_to_cv2_bgr(image)

    if isinstance(image, np.ndarray):
        if image.size == 0 or len(image.shape) < 2:
            return None
        if len(image.shape) == 2:
            # Grayscale to BGR
            if cv2 is not None:
                return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            return np.stack([image, image, image], axis=-1)
        if len(image.shape) == 3 and image.shape[2] == 4:
            # BGRA to BGR
            if cv2 is not None:
                return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
            return image[:, :, :3]
        if len(image.shape) == 3 and image.shape[2] == 3:
            return image

    return None


def extract_text_from_image(image: Union["QImage", np.ndarray], lang: str = "auto") -> tuple[str, str]:
    """
    Локально извлекает текст из изображения.
    
    Параметры:
        image: QImage или numpy BGR массив изображения.
        lang: Тег языка ('auto', 'ru', 'en-US', etc.).
    
    Возвращает:
        tuple[str, str]: (распознанный_текст, использованный_язык)
    """
    bgr = _convert_to_bgr(image)
    if bgr is None or bgr.size == 0:
        return "", ""

    h, w = bgr.shape[:2]

    # Для мелкого текста или небольших выделений (высота или ширина < 150px)
    # масштабирование 2x с бикубической интерполяцией заметно повышает точность OCR
    if cv2 is not None and (h < 150 or w < 150):
        scale = max(2.0, min(3.0, 300.0 / max(1, min(h, w))))
        new_w = int(round(w * scale))
        new_h = int(round(h * scale))
        bgr = cv2.resize(bgr, (new_w, new_h), interpolation=cv2.INTER_CUBIC)

    if _WINOCR_AVAILABLE:
        return _extract_with_winocr(bgr, lang)

    # Резервный поиск через pytesseract, если установлен
    try:
        import pytesseract
        pil_img = None
        if cv2 is not None:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            from PIL import Image
            pil_img = Image.fromarray(rgb)
        if pil_img is not None:
            t_lang = "rus+eng" if lang == "auto" else ("rus" if "ru" in lang else "eng")
            text = pytesseract.image_to_string(pil_img, lang=t_lang).strip()
            if text:
                return text, t_lang
    except Exception:
        pass

    return "", ""


def _extract_with_winocr(bgr: np.ndarray, lang: str = "auto") -> tuple[str, str]:
    """Распознавание через встроенный в Windows движок Windows.Media.Ocr."""
    installed = get_available_ocr_languages()
    installed_tags = [item["tag"] for item in installed]

    # Выбор подходящего языка
    target_langs = []
    if lang == "auto":
        # Проверяем язык пользовательского профиля
        try:
            profile_engine = OcrEngine.try_create_from_user_profile_languages()
            if profile_engine and profile_engine.recognizer_language:
                ptag = str(profile_engine.recognizer_language.language_tag)
                target_langs.append(ptag)
        except Exception:
            pass

        # Русский движок в Windows отлично распознает и кириллицу, и латиницу, и цифры
        for preferred in ("ru", "en-US", "en"):
            for itag in installed_tags:
                if (preferred == itag or itag.startswith(preferred)) and itag not in target_langs:
                    target_langs.append(itag)

        # Добавляем все остальные установленные
        for itag in installed_tags:
            if itag not in target_langs:
                target_langs.append(itag)
    else:
        # Ищем точное или префиксное совпадение
        matched = None
        for itag in installed_tags:
            if itag.lower() == lang.lower() or itag.lower().startswith(lang.lower()):
                matched = itag
                break
        target_langs.append(matched or lang)

    best_text = ""
    best_lang = target_langs[0] if target_langs else "ru"
    best_lines_count = 0

    for current_lang in target_langs:
        try:
            result = winocr.recognize_cv2_sync(bgr, lang=current_lang)
            if not result:
                continue

            raw_lines = result.get("lines", [])
            lines = [line.get("text", "").strip() for line in raw_lines if line.get("text", "").strip()]
            full_text = "\n".join(lines) if lines else result.get("text", "").strip()

            if len(lines) > best_lines_count or (len(lines) == best_lines_count and len(full_text) > len(best_text)):
                best_text = full_text
                best_lang = current_lang
                best_lines_count = len(lines)

            # Если распознали хороший осмысленный текст (например, больше 5 символов),
            # для первого же подходящего языка не тратим время на дальнейший перебор
            if len(best_text) >= 10:
                break
        except Exception as e:
            print(f"[OCR] Предупреждение при распознавании с языком '{current_lang}': {e}")
            continue

    return best_text.strip(), best_lang
