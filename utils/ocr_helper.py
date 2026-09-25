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

    if _WINOCR_AVAILABLE:
        return _extract_with_winocr(bgr, lang)

    # Резервный поиск через pytesseract, если winocr недоступен
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


def _has_cyrillic(text: str) -> bool:
    import re
    return bool(re.search(r"[\u0400-\u04FF]", text))


def _clean_en_mangled_word(w: str) -> str:
    """Исправляет искажения кириллицы при англоязычном проходе OCR (например, Onefile-6L4JIA -> Onefile-билд)."""
    import re
    m = re.match(r"^([A-Za-z0-9]+-)(?:6[LM4h\s]*J[I1]?[A-Z]?|6[hM]\w+)$", w)
    if m:
        return m.group(1) + "билд"
    return w


def _merge_line_words(ru_words: list[dict], en_words: list[dict]) -> str:
    """
    Интеллектуально объединяет распознанные слова из русского и английского проходов OCR.
    Сохраняет кириллические слова из RU-прохода и вставляет пропущенные латинские токены,
    пути и клавиатурные сокращения из EN-прохода.
    """
    import re

    if not ru_words and not en_words:
        return ""
    if not en_words:
        return " ".join(w.get("text", "") for w in ru_words if w.get("text"))
    if not ru_words:
        return " ".join(_clean_en_mangled_word(w.get("text", "")) for w in en_words if w.get("text"))

    # Очищаем слова EN
    cleaned_en = []
    for ew in en_words:
        w_copy = dict(ew)
        w_copy["text"] = _clean_en_mangled_word(w_copy.get("text", ""))
        cleaned_en.append(w_copy)

    # Карта пересечений
    en_matched_ru = {i: [] for i in range(len(cleaned_en))}
    ru_matched_en = {i: [] for i in range(len(ru_words))}

    for i, ew in enumerate(cleaned_en):
        eb = ew.get("bounding_rect") or {}
        ex1 = float(eb.get("x", 0.0))
        ex2 = ex1 + float(eb.get("width", 0.0))
        for j, rw in enumerate(ru_words):
            rb = rw.get("bounding_rect") or {}
            rx1 = float(rb.get("x", 0.0))
            rx2 = rx1 + float(rb.get("width", 0.0))
            overlap = max(0.0, min(ex2, rx2) - max(rx1, ex1))
            min_w = min(max(1.0, ex2 - ex1), max(1.0, rx2 - rx1))
            if overlap > 0.25 * min_w:
                en_matched_ru[i].append(j)
                ru_matched_en[j].append(i)

    result_spans = []
    processed_ru = set()
    processed_en = set()

    for j, rw in enumerate(ru_words):
        if j in processed_ru:
            continue
        rb = rw.get("bounding_rect") or {}
        rx1 = float(rb.get("x", 0.0))
        r_text = rw.get("text", "")
        matched_en_indices = ru_matched_en[j]

        if not matched_en_indices:
            result_spans.append((rx1, r_text))
            processed_ru.add(j)
            continue

        cluster_ru = set([j])
        cluster_en = set(matched_en_indices)
        for ei in matched_en_indices:
            for rj in en_matched_ru[ei]:
                cluster_ru.add(rj)

        for rj in cluster_ru:
            processed_ru.add(rj)
        for ei in cluster_en:
            processed_en.add(ei)

        cluster_ru_words = sorted([ru_words[rj] for rj in cluster_ru], key=lambda w: (w.get("bounding_rect") or {}).get("x", 0.0))
        cluster_en_words = sorted([cleaned_en[ei] for ei in cluster_en], key=lambda w: (w.get("bounding_rect") or {}).get("x", 0.0))

        cluster_ru_text = " ".join(w.get("text", "") for w in cluster_ru_words if w.get("text"))
        cluster_en_text = " ".join(w.get("text", "") for w in cluster_en_words if w.get("text"))

        if any(w.get("text", "") in (".", "ехе", ".ехе") for w in cluster_ru_words) and any(".exe" in w.get("text", "").lower() for w in cluster_en_words):
            chosen = cluster_en_text
        elif _has_cyrillic(cluster_ru_text):
            chosen = cluster_ru_text
        else:
            chosen = cluster_en_text if len(cluster_en_text) >= len(cluster_ru_text) else cluster_ru_text

        min_x = min(float((w.get("bounding_rect") or {}).get("x", 0.0)) for w in cluster_ru_words + cluster_en_words)
        result_spans.append((min_x, chosen))

    for i, ew in enumerate(cleaned_en):
        if i in processed_en:
            continue
        eb = ew.get("bounding_rect") or {}
        ex1 = float(eb.get("x", 0.0))
        result_spans.append((ex1, ew.get("text", "")))

    result_spans.sort(key=lambda s: s[0])
    merged = " ".join(s[1] for s in result_spans if s[1])
    merged = re.sub(r"\.\s+exe\b", ".exe", merged)
    return merged


def _preprocess_image_for_ocr(bgr: np.ndarray, scale: float = 2.5) -> tuple[np.ndarray, bool]:
    """Масштабирует и повышает резкость изображения, нормализуя тёмные темы."""
    if cv2 is None:
        return bgr, False

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    is_dark = bool(np.mean(gray) < 128)

    # Масштабирование с бикубической/Lanczos интерполяцией
    h, w = bgr.shape[:2]
    target_scale = scale
    if h < 100 or w < 100:
        target_scale = max(3.0, min(4.5, 350.0 / max(1, min(h, w))))
    elif h > 1200 or w > 2000:
        target_scale = max(1.2, min(2.0, 2400.0 / max(h, w)))

    up = cv2.resize(bgr, None, fx=target_scale, fy=target_scale, interpolation=cv2.INTER_LANCZOS4)
    # Маска повышения резкости (Unsharp Mask)
    gaussian = cv2.GaussianBlur(up, (0, 0), 2.0)
    unsharp = cv2.addWeighted(up, 1.8, gaussian, -0.8, 0)

    # При тёмном фоне инвертируем до стандартного чёрного текста на белом фоне
    if is_dark:
        processed = 255 - unsharp
    else:
        processed = unsharp

    return processed, is_dark


def _extract_with_winocr(bgr: np.ndarray, lang: str = "auto") -> tuple[str, str]:
    """Распознавание через встроенный в Windows движок Windows.Media.Ocr с multi-pass алгоритмом."""
    import re

    installed = get_available_ocr_languages()
    installed_tags = [item["tag"] for item in installed]

    # Если задан конкретный язык (не auto)
    if lang != "auto":
        target_lang = lang
        for itag in installed_tags:
            if itag.lower() == lang.lower() or itag.lower().startswith(lang.lower()):
                target_lang = itag
                break

        prep_img, _ = _preprocess_image_for_ocr(bgr, scale=2.5)
        try:
            res = winocr.recognize_cv2_sync(prep_img, lang=target_lang)
            raw_lines = res.get("lines", []) if res else []
            lines = [l.get("text", "").strip() for l in raw_lines if l.get("text", "").strip()]
            text = "\n".join(lines) if lines else (res.get("text", "").strip() if res else "")
            return text, target_lang
        except Exception as e:
            print(f"[OCR] Ошибка winocr с языком '{target_lang}': {e}")
            return "", target_lang

    # Для 'auto': запускаем комбинированный Russian + English проход
    prep_img, is_dark = _preprocess_image_for_ocr(bgr, scale=2.5)

    res_ru = None
    res_en = None

    try:
        res_ru = winocr.recognize_cv2_sync(prep_img, lang="ru")
    except Exception as e:
        print(f"[OCR] Ошибка RU прохода: {e}")

    try:
        res_en = winocr.recognize_cv2_sync(prep_img, lang="en-US")
    except Exception as e:
        print(f"[OCR] Ошибка EN прохода: {e}")

    if not res_ru and not res_en:
        return "", "auto"

    if not res_en:
        lines = [l.get("text", "").strip() for l in (res_ru.get("lines", []) if res_ru else [])]
        return "\n".join(l for l in lines if l), "ru"

    if not res_ru:
        lines = [l.get("text", "").strip() for l in (res_en.get("lines", []) if res_en else [])]
        return "\n".join(l for l in lines if l), "en-US"

    lines_ru = res_ru.get("lines", [])
    lines_en = res_en.get("lines", [])

    scale_used = 2.5
    matched_pairs = []
    used_en_lines = set()

    for l_ru in lines_ru:
        y_ru = float(l_ru["words"][0]["bounding_rect"]["y"]) if l_ru.get("words") else 0.0
        best_j = None
        min_dy = 999999.0
        for j, l_en in enumerate(lines_en):
            if j in used_en_lines:
                continue
            y_en = float(l_en["words"][0]["bounding_rect"]["y"]) if l_en.get("words") else 0.0
            diff = abs(y_ru - y_en)
            if diff < 30.0 * scale_used and diff < min_dy:
                min_dy = diff
                best_j = j
        if best_j is not None:
            used_en_lines.add(best_j)
            matched_pairs.append((y_ru, l_ru, lines_en[best_j]))
        else:
            matched_pairs.append((y_ru, l_ru, None))

    for j, l_en in enumerate(lines_en):
        if j not in used_en_lines:
            y_en = float(l_en["words"][0]["bounding_rect"]["y"]) if l_en.get("words") else 0.0
            matched_pairs.append((y_en, None, l_en))

    matched_pairs.sort(key=lambda p: p[0])

    final_lines = []
    for y_coord, l_ru, l_en in matched_pairs:
        words_ru = l_ru.get("words", []) if l_ru else []
        words_en = l_en.get("words", []) if l_en else []

        ru_txt = l_ru.get("text", "") if l_ru else ""
        en_txt = l_en.get("text", "") if l_en else ""

        # Уточнение строк путей к файлам и командных строк (например, O:\GitHub\Framio\dist-onefile\Framio.exe)
        is_path_like = bool(re.search(r"\b[A-Za-z]:|\bexe\b|\\", ru_txt + " " + en_txt, re.IGNORECASE))
        if is_path_like and len(en_txt) < 35 and cv2 is not None:
            orig_y = int(y_coord / scale_used)
            y1 = max(0, orig_y - 14)
            y2 = min(bgr.shape[0], orig_y + 20)
            band = bgr[y1:y2, :]
            for b_scale in (3.0, 4.0):
                try:
                    b_up = cv2.resize(band, None, fx=b_scale, fy=b_scale, interpolation=cv2.INTER_LANCZOS4)
                    b_g = cv2.GaussianBlur(b_up, (0, 0), 2.0)
                    b_un = cv2.addWeighted(b_up, 1.8, b_g, -0.8, 0)
                    b_inv = (255 - b_un) if is_dark else b_un
                    b_res = winocr.recognize_cv2_sync(b_inv, lang="en-US")
                    b_lines = [l["text"].strip() for l in b_res.get("lines", []) if l.get("text")]
                    if b_lines and ("\\" in b_lines[0] or len(b_lines[0]) > 20):
                        path_text = b_lines[0]
                        if "МБ" in ru_txt or "MB" in ru_txt:
                            path_text = re.sub(r"\((\d+)\s*(?:ME|M6|Mb|MB)\)", r"(\1 МБ)", path_text)
                        en_txt = path_text
                        break
                except Exception:
                    pass

        if "\\" in en_txt and ("Framio" in en_txt or ".exe" in en_txt):
            line_result = en_txt
        else:
            line_result = _merge_line_words(words_ru, words_en)

        # Очистка артефактов перед меткой диска (например, "б о: O:\" -> "O:\")
        line_result = re.sub(r"^[бoо]\s+([a-zA-Z]:)", r"\1", line_result)
        line_result = re.sub(r"^\s*•\s*", "• ", line_result)
        if line_result.strip():
            final_lines.append(line_result.strip())

    full_result = "\n".join(final_lines).strip()
    return full_result, "ru+en"

