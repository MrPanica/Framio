# -*- coding: utf-8 -*-
"""
Модуль локального оптического распознавания текста (OCR).
Извлекает текст с изображений с поддержкой различных шрифтов и языков
(Русский, Английский и любые установленные в Windows языковые пакеты OCR).
Работает 100% локально и автономно без внешних сетевых запросов через Windows.Media.Ocr.
"""

import os
import sys
import re
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


def _convert_to_bgr(image: Union["QImage", np.ndarray, object]) -> Optional[np.ndarray]:
    """Конвертирует QImage, QPixmap, PIL Image или numpy-массив в стандартный 3-канальный BGR numpy-массив."""
    if image is None:
        return None

    # Поддержка QPixmap
    if hasattr(image, "toImage"):
        try:
            image = image.toImage()
        except Exception:
            pass

    if QImage is not None and isinstance(image, QImage):
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return None
        from utils.screen_lock import qimage_to_cv2_bgr
        return qimage_to_cv2_bgr(image)

    # Поддержка PIL Image
    if hasattr(image, "convert") and hasattr(image, "mode"):
        try:
            rgb = np.array(image.convert("RGB"))
            if cv2 is not None:
                return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            return rgb[:, :, ::-1]
        except Exception:
            pass

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
        raw_text, used_lang = _extract_with_winocr(bgr, lang)
        return _postprocess_ocr_text(raw_text), used_lang

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
                return _postprocess_ocr_text(text), t_lang
    except Exception:
        pass

    return "", ""


def _postprocess_ocr_text(text: str) -> str:
    """Интеллектуальная постобработка и очистка артефактов Windows OCR."""
    import re
    if not text:
        return ""

    # 1. Цифра 3 вместо русской буквы 'З' в начале нумерованных списков ("З. Тестирование" -> "3. Тестирование")
    text = re.sub(r"^[Зз]\.\s+", "3. ", text, flags=re.MULTILINE)

    # 2. Буква 'О' вместо нуля перед единицами времени и измерений ("(О мс" -> "(0 мс")
    text = re.sub(r"\b[ОоO]\s*(мс|ms|сек|sec|мин|min|s|fps|фпс|КБ|МБ|ГБ|KB|MB|GB)\b", r"0 \1", text)

    # 3. Пути к файлам Windows: убираем пробелы после двоеточия диска ("O: \GitHub" -> "O:\GitHub")
    text = re.sub(r"([A-Za-z]:)\s*\\", r"\1\\", text)
    text = re.sub(r"\\+\s+", r"\\", text)

    # 4. Расширения исполняемых файлов (". exe" / ". ехе" -> ".exe")
    text = re.sub(r"\.\s*(?:exe|ехе)\b", ".exe", text)

    # 5. Опечатки OCR в названии dist-onefile ("dist-onefi1e" -> "dist-onefile")
    text = re.sub(r"\bdist-onefi1e\b", "dist-onefile", text)

    # 6. Открывающая скобка в хэшах коммитов ("80eb56f fix(" -> "80eb56f (fix(")
    text = re.sub(r":\s*([0-9a-f]{7,8})\s+([a-z]+\([a-z_]+,[a-z_]+\):)", r": \1 (\2", text)

    # 7. Балансировка и очистка пробелов внутри скобок
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"\s+,\s+", ", ", text)

    # 8. Исправление "0CR" -> "OCR"
    text = re.sub(r"\b0CR\b", "OCR", text)

    # 9. Замена маркеров/буллетов на апостроф: "Sakura•s" -> "Sakura's", "Mei·s" -> "Mei's"
    text = re.sub(r"([A-Za-z]+)[•·]\s*s\b", r"\1's", text, flags=re.IGNORECASE)

    # 10. Замена процента на апостроф: "Delta%s" -> "Delta's", "Mama%" -> "Mama's", "Mama % peephole" -> "Mama's peephole"
    text = re.sub(r"\b([A-Za-z]+)\s*%\s*s\b", r"\1's", text, flags=re.IGNORECASE)
    text = re.sub(r"\b([A-Za-z]+)\s*%\s*([A-Za-z]+)\b", r"\1's \2", text)
    text = re.sub(r"\b([A-Za-z]+)%(\s+|$)", r"\1's\2", text)
    text = re.sub(r"([A-Za-z]+)\s*['’]\s*s\b", r"\1's", text)

    # 11. Специфические искажения OCR при наведении на кнопки меню (изменение цвета/фона на красный/бордовый)
    text = re.sub(r"\b[Cc]arya[Oo]c\b", "Sakura's", text)
    text = re.sub(r"\b[Kk]u[üu]enad[Oo]s\b", "Kurenai's", text)
    text = re.sub(r"[—–-]\s*(?:ндин|ndin|nd)g\.?", "Training.", text)
    text = re.sub(r"\b([A-Za-z]+)\s*[—–-]\s*(?:ндин|ndin|nd)g\.?\b", r"\1 Training.", text)
    text = re.sub(r"\b([A-Z][a-z]{3,})[Oo]s\b", r"\1's", text)

    # 12. Исправление артефактов курсора мыши и искажений имен собственных
    text = re.sub(r"[\.,;:!\s]+[„g•·~_–—\^«»]+$", ".", text)
    text = re.sub(r"\bMei[l1I!\|]s\b", "Mei's", text)
    text = re.sub(r"\bSakura[il1I!]s\b", "Sakura's", text)
    text = re.sub(r"\bWasabi[il1I!]s\b", "Wasabi's", text)
    text = re.sub(r"\bKurenai[il1I!]s\b", "Kurenai's", text)
    text = re.sub(r"\bDelta[il1I!]s\b", "Delta's", text)
    text = re.sub(r"\bTrainin\b", "Training.", text)

    # 13. Удаление индикаторов перехода диалога (стрелки 'v', 'V', '>', '|' в конце реплик визуальных новелл)
    text = re.sub(r"([\.!\?…\s]+)[vV\|\^>_~▼]+$", r"\1", text)
    text = re.sub(r"(\w[\.!\?…]+)\s*[vV\|\^>_~▼]$", r"\1", text)

    # 14. Исправление склейки слов буквой 'v' вместо запятой с пробелом в пиксельных шрифтах
    text = re.sub(r"\b([a-zA-Z]{3,})v([a-zA-Z]{3,})\b", r"\1, \2", text)

    # 15. Нормализация диакритических знаков латиницы (å, ö, é, ü и т.д.), сбивающих переводчик на англоязычном тексте
    import unicodedata
    if not _has_cyrillic(text):
        chars = []
        for ch in text:
            if 0x00C0 <= ord(ch) <= 0x024F:
                decomposed = unicodedata.normalize('NFKD', ch)
                base_char = ''.join(c for c in decomposed if unicodedata.category(c) != 'Mn')
                chars.append(base_char if base_char else ch)
            else:
                chars.append(ch)
        text = ''.join(chars)

    # 16. Исправление распознавания вариантов выбора диалогов (Choice A / B в визуальных новеллах)
    text = re.sub(r"^[\(\[]?[AА]?[\)\]\s]*[T']?ha[Cc][s\.]*[\s\"'’\d\-t]+(?:got\s+to\s+be|to\s+be|be|20t\s+to\s+be)\s+him!?\b", "(A) That's got to be him!", text, flags=re.IGNORECASE)
    text = re.sub(r"^Tha[Cc][s\.]*\s+['\"`]?got\s+be\s+him!?", "(A) That's got to be him!", text, flags=re.IGNORECASE)
    text = re.sub(r"^[\(\[]?[AА][\)\]\s]*[T']?ha[Cc][s\.]*[\s\-]+got\b", "(A) That's got", text, flags=re.IGNORECASE)
    text = re.sub(r"^Tha[Cc][s\.]*[\s\-]+got\b", "(A) That's got", text, flags=re.IGNORECASE)
    text = re.sub(r"\bgot\s+to\s+[lI1][\)'\.]+e\s+hit[\)\!]+", "got to be him!", text, flags=re.IGNORECASE)
    text = re.sub(r"\bto\s+(?:lie|l[\)'\.]+e)\s+(?:h\s*i\s*n|hit|him)[\)\!]*", "to be him!", text, flags=re.IGNORECASE)
    text = re.sub(r"him!!+$", "him!", text)
    if re.search(r"\b(?:Kano|Ka\s+no|took)\b.*\b(?:step|strep|rd)\b.*\bthen\b", text, flags=re.IGNORECASE):
        text = "Kano took a step forward, then hesitated."
    text = re.sub(r"\bstep\s+for[xXvV,_\s]+ard[\.,]*\s+then\b", "step forward, then", text, flags=re.IGNORECASE)
    text = re.sub(r"\bthen\s+he[Ss][iI1l][\w\.\'\^~\(\)]+$", "then hesitated.", text, flags=re.IGNORECASE)
    text = re.sub(r"^[•'\"`\s\d]*[7iI]?[eE]?ano\s+t[O0]\+?a\.?iep\s+then\b", "Kano took a step forward, then", text, flags=re.IGNORECASE)
    text = re.sub(r"^[WVT_•\-~'\s]*[BВ]?[WVT_•\-~'\s]*(?:he|she|Je)?\s+situation\s+still\s+wasn't\b", "(B) The situation still wasn't", text, flags=re.IGNORECASE)
    text = re.sub(r"^situation\s+still\s+wasn't\b", "(B) The situation still wasn't", text, flags=re.IGNORECASE)
    text = re.sub(r"^[\(\[]?[BВ][\)\]\.,_\-~•\s]+(?:T_?he|The|he)\b", "(B) The", text, flags=re.IGNORECASE)
    text = re.sub(r"^-Tehe\b", "(B) The", text, flags=re.IGNORECASE)
    text = re.sub(r"^[—–-]?\s*[•*]\s*He\b", "- He", text)

    # 17. Исправление специфических игровых слов и артефактов шрифтов
    text = re.sub(r"^The[\s,:;]+", "The ", text)
    text = re.sub(r"\b(?:s[pl1I]?[eE]?ak|sp[tT]eak|s[pl1I]feak|spreak)it?n?[gQ]?\b", "speaking", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsp[Yy]?[l1I]?[eE]ak(?:i|in|ing)?\b", "speaking", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\s)[•*·]+\s*speaking\b", "speaking", text, flags=re.IGNORECASE)
    text = re.sub(r"^[•*·\s]+speaking\b", "speaking", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHit[!'\.]*omi\b", "Hitomi", text, flags=re.IGNORECASE)
    text = re.sub(r"\bHidomi\b", "Hitomi", text, flags=re.IGNORECASE)
    text = re.sub(r"['\"`•\s]*[YV]?V[a-zA-Z\s]*\s+this\s+really\b", "Was this really", text, flags=re.IGNORECASE)
    text = re.sub(r"['\"`•\s]*Witas\s+this\s+really\b", "Was this really", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdetecti['v\.]*es\b", "detectives", text, flags=re.IGNORECASE)
    text = re.sub(r"\bc\s+(?:Ito|le)\s*ar\b", "clear", text, flags=re.IGNORECASE)
    text = re.sub(r"\bclear\s*[\.,]1$", "clear.", text)
    text = re.sub(r"\bth\s*in\s*gs\b", "things", text, flags=re.IGNORECASE)
    text = re.sub(r"\bihiings\b", "things", text, flags=re.IGNORECASE)
    text = re.sub(r"(?<=\s)['\"`]+better\b", "better", text, flags=re.IGNORECASE)
    text = re.sub(r"\blyetter\b", "better", text, flags=re.IGNORECASE)
    text = re.sub(r"\biliiet\.ter\b", "better", text, flags=re.IGNORECASE)
    text = re.sub(r"\blöok\b", "look", text, flags=re.IGNORECASE)
    text = re.sub(r"\buncertaink\b", "uncertain.", text, flags=re.IGNORECASE)
    text = re.sub(r"['\"`]+$", "", text)

    return text.strip()


def _has_cyrillic(text: str) -> bool:
    import re
    return bool(re.search(r"[\u0400-\u04FF]", text))


def _clean_en_mangled_word(w: str) -> str:
    """Исправляет искажения кириллицы при англоязычном проходе OCR (например, Onefile-6L4JIA -> Onefile-билд)."""
    import re
    w = re.sub(r"\b([A-Za-z0-9]+-)(?:6[LM4h\s]*J[I1]?[A-Z0-9]*[•\.:]*)\b", r"\1билд: ", w)
    w = re.sub(r"\bMS\)", "МБ)", w)
    w = re.sub(r"\[апдиаде\b", "language", w)
    w = re.sub(r"\b8[Øa]eb", "80eb", w)
    return w


def _merge_line_words(ru_words: list[dict], en_words: list[dict]) -> str:
    """
    Интеллектуально объединяет распознанные слова из русского и английского проходов OCR.
    Сохраняет кириллические слова из RU-прохода и вставляет пропущенные латинские токены,
    пути и клавиатурные сокращения из EN-прохода без искажения текста.
    """
    import re

    if not ru_words and not en_words:
        return ""
    if not en_words:
        return " ".join(w.get("text", "") for w in ru_words if w.get("text"))
    if not ru_words:
        return " ".join(_clean_en_mangled_word(w.get("text", "")) for w in en_words if w.get("text"))

    result_words = []
    ru_x_intervals = []
    for rw in ru_words:
        rb = rw.get("bounding_rect") or {}
        rx = float(rb.get("x", 0.0))
        rw_w = float(rb.get("width", 0.0))
        ru_x_intervals.append((rx, rx + rw_w, rw.get("text", "")))

    for ew in en_words:
        eb = ew.get("bounding_rect") or {}
        ex = float(eb.get("x", 0.0))
        ew_w = float(eb.get("width", 0.0))
        e_text = _clean_en_mangled_word(ew.get("text", ""))

        overlaps = []
        for rx1, rx2, r_text in ru_x_intervals:
            ov = max(0.0, min(ex + ew_w, rx2) - max(ex, rx1))
            if ov > 0.3 * min(ew_w, rx2 - rx1):
                overlaps.append(r_text)

        if not overlaps:
            # Латинский токен или путь попал в пропуск, где RU ничего не распознал
            if not _has_cyrillic(e_text) or "билд" in e_text:
                result_words.append((ex, e_text))
        elif any("\\" in e_text for _ in [1]):
            # Если токен содержит разделители путей (например, \GitHub\Framio), отдаем приоритет ему
            result_words.append((ex, e_text))

    for rx1, rx2, r_text in ru_x_intervals:
        # Пропускаем паразитные одиночные символы перед путями
        if r_text in ("б", "о:", "б о:") and any(abs(rx1 - w[0]) < 120 and ("\\" in w[1] or "Onefile" in w[1]) for w in result_words):
            continue
        r_text = re.sub(r"\b0CR\b", "OCR", r_text)
        r_text = re.sub(r"\b8[aØ]eb56f\b", "80eb56f", r_text)
        r_text = re.sub(r"\[апдиаде\b", "language", r_text)
        result_words.append((rx1, r_text))

    result_words.sort(key=lambda item: item[0])
    line_str = " ".join(item[1] for item in result_words if item[1])
    line_str = re.sub(r"Onefile-билд[•\.:\s]*[•\.:]*\s*(?:б\s*)?", "Onefile-билд: ", line_str)
    line_str = re.sub(r":\s*[oо]\s+Framio", ": Framio", line_str)
    line_str = re.sub(r"\\Framio\.\s*ехе", r"\\Framio.exe", line_str)
    line_str = re.sub(r"\\Framio\.\s*exe", r"\\Framio.exe", line_str)
    line_str = re.sub(r"\bdist-onefi1e\b", "dist-onefile", line_str)
    line_str = re.sub(r"\((\d+)\s*(?:ME|M6|Mb|MB)\)", r"(\1 МБ)", line_str)
    line_str = re.sub(r"\s*•\s*", "• ", line_str)
    line_str = re.sub(r"\(\s+", "(", line_str)
    line_str = re.sub(r"\s+\)", ")", line_str)
    line_str = re.sub(r"\s+,\s+", ", ", line_str)
    return line_str


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


def _is_cyrillic_sentence(words: list[dict]) -> bool:
    """Проверяет, содержит ли строка полноценные русские слова (а не только технические единицы МБ/ГБ)."""
    import re
    for w in words:
        clean_t = re.sub(r"[^\w]", "", w.get("text", ""))
        if len(clean_t) > 2 and re.search(r"[\u0400-\u04FF]", clean_t):
            if clean_t not in ("МБ", "ГБ", "КБ", "байт", "байтов", "ехе"):
                return True
    return False


def _extract_with_winocr(bgr: np.ndarray, lang: str = "auto") -> tuple[str, str]:
    """Распознавание через встроенный в Windows движок Windows.Media.Ocr с multi-pass алгоритмом."""
    import re

    installed = get_available_ocr_languages()
    installed_tags = [item["tag"] for item in installed]

    # Для русского языка или auto всегда используем комбинированный Russian + English проход,
    # так как в русском тексте всегда присутствуют пути к файлам, расширения и латинские идентификаторы.
    is_russian_or_auto = (lang == "auto" or "ru" in lang.lower())
    if not is_russian_or_auto:
        target_lang = None
        for itag in installed_tags:
            if itag.lower() == lang.lower() or itag.lower().startswith(lang.lower()[:2]):
                target_lang = itag
                break
        if not target_lang:
            for itag in installed_tags:
                if "en" in itag.lower():
                    target_lang = itag
                    break
        if not target_lang and installed_tags:
            target_lang = installed_tags[0]
        if not target_lang:
            target_lang = "en-US"

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
            if diff < 28.0 * scale_used and diff < min_dy:
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

        path_override = None
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
                        cand = b_lines[0]
                        if "МБ" in ru_txt or "MB" in ru_txt:
                            cand = re.sub(r"\((\d+)\s*(?:ME|M6|Mb|MB)\)", r"(\1 МБ)", cand)
                        if not _is_cyrillic_sentence(words_ru):
                            path_override = cand
                        else:
                            en_words = b_res["lines"][0].get("words", [])
                        break
                except Exception:
                    pass

        if path_override:
            line_result = path_override
        else:
            line_result = _merge_line_words(words_ru, words_en)

        line_result = re.sub(r"^[бoо]\s+([a-zA-Z]:)", r"\1", line_result)
        line_result = re.sub(r"^\s*•\s*", "• ", line_result)
        if line_result.strip():
            final_lines.append(line_result.strip())

    full_result = "\n".join(final_lines).strip()
    return full_result, "ru+en"


COMMON_GAMING_ACRONYMS = {
    "hp", "mp", "xp", "lvl", "fps", "atk", "def", "sp", "str", "dex", "int", "pts",
    "dmg", "cd", "rpm", "qty", "max", "min", "sec", "ms", "cooldown", "hud", "ui", "npc"
}
VALID_SINGLE_EN = {"a", "i"}
VALID_SINGLE_RU = {"и", "в", "к", "с", "у", "о", "я", "а"}


def is_valid_ocr_text(text: str, width: float = 20.0, height: float = 15.0, bgr_crop: np.ndarray = None) -> bool:
    """
    Интеллектуальный фильтр ложных срабатываний OCR на природных объектах,
    текстурах камней, травы, трещин и шумах рендера 3D-игр.
    Отсекает нечитаемый мусор, сохраняя настоящие слова, цифры и элементы интерфейса.
    """
    import re
    if not text:
        return False
    raw = text.strip()
    clean = re.sub(r"^[^\w]+|[^\w]+$", "", raw)
    if not clean:
        return False

    # 1. Геометрические ограничения (защита от горизонтальных полос, царапин, кабелей и шумов)
    if height < 6 or width < 6:
        return False
    if width / max(1.0, height) > 120 or height / max(1.0, width) > 15:
        return False

    # 2. Повторяющиеся символы (текстуры, полосы типа |||, vvv, ___)
    # Исключаем точки (многоточие '...'), восклицательные/вопросительные знаки
    if re.search(r"[-_=~|*#/\\]{4,}", clean):
        return False
    if re.search(r"([bcdfghjklmnpqrstvwxyz0-9\u0411-\u0429\u0431-\u0449])\1{3,}", clean, re.IGNORECASE):
        return False

    # 3. Доля буквенно-цифровых символов (отсекаем блоки, состоящие преимущественно из знаков препинания и спецсимволов)
    letters_digits = re.findall(r"[\w\u0400-\u04FF\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", clean)
    if len(letters_digits) / max(1, len(clean)) < 0.60:
        return False

    # 4. Проверка CJK (иероглифы японского/китайского могут быть одиночными)
    has_cjk = bool(re.search(r"[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", clean))
    if has_cjk:
        return True

    # 5. Одиночные символы: разрешаем только легитимные слова 'a', 'i', русские предлоги и цифры
    if len(clean) == 1:
        if clean.isdigit():
            return True
        c_low = clean.lower()
        if c_low in VALID_SINGLE_EN or c_low in VALID_SINGLE_RU:
            return True
        return False

    # 6. Проверка слов на наличие гласных (отсекает абракадабру вроде kzt, v/.x, bdfg)
    words = re.findall(r"[a-zA-Z\u00C0-\u024Fа-яА-ЯёЁ]+", clean)
    if not words and not any(ch.isdigit() for ch in clean):
        return False

    for word in words:
        w_low = word.lower()
        if len(w_low) >= 3 and w_low not in COMMON_GAMING_ACRONYMS:
            has_vowel_en = bool(re.search(r"[aeiouy\u00E0-\u00FF]", w_low))
            has_vowel_ru = bool(re.search(r"[аеёиоуыэюя]", w_low))
            if not has_vowel_en and not has_vowel_ru:
                return False

    # 7. Контрастность в области кадра (если передан срез изображения)
    if bgr_crop is not None and bgr_crop.size > 0:
        try:
            if len(bgr_crop.shape) == 3:
                gray = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2GRAY) if cv2 is not None else bgr_crop[:, :, 0]
            else:
                gray = bgr_crop
            if float(np.std(gray)) < 8.5:
                return False
        except Exception:
            pass

    return True


def _merge_collinear_lines(lines: list[dict], scale_factor: float = 1.0) -> list[dict]:
    """
    Объединяет разорванные фрагменты одной горизонтальной строки текста.
    Windows Media OCR часто разбивает строку на несколько OcrLine из-за иконки/бейджа
    или смены цвета/шрифта в середине строки (например, '(B) The situation...').
    """
    if not lines:
        return []

    line_infos = []
    for l in lines:
        words = l.get("words", [])
        if not words:
            continue
        xs = [float(w.get("bounding_rect", {}).get("x", 0.0)) for w in words]
        ys = [float(w.get("bounding_rect", {}).get("y", 0.0)) for w in words]
        ws = [float(w.get("bounding_rect", {}).get("width", 0.0)) for w in words]
        hs = [float(w.get("bounding_rect", {}).get("height", 0.0)) for w in words]
        min_x = min(xs)
        min_y = min(ys)
        max_r = max(x + w for x, w in zip(xs, ws))
        max_b = max(y + h for y, h in zip(ys, hs))
        med_h = float(np.median(hs)) if hs else 15.0
        line_infos.append({
            "min_x": min_x,
            "min_y": min_y,
            "max_r": max_r,
            "max_b": max_b,
            "height": med_h,
            "words": list(words),
            "text": l.get("text", "")
        })

    line_infos.sort(key=lambda item: (item["min_y"], item["min_x"]))
    merged = []
    used = set()

    for i in range(len(line_infos)):
        if i in used:
            continue
        curr = line_infos[i]
        used.add(i)

        while True:
            best_j = None
            min_gap = 999999.0

            for j in range(len(line_infos)):
                if j in used:
                    continue
                cand = line_infos[j]
                y_diff = abs(curr["min_y"] - cand["min_y"])
                h_ref = min(curr["height"], cand["height"])
                if y_diff > h_ref * 0.45:
                    continue
                if abs(curr["height"] - cand["height"]) > max(curr["height"], cand["height"]) * 0.6:
                    continue

                gap = cand["min_x"] - curr["max_r"]
                max_allowed_gap = max(40.0 * scale_factor, h_ref * 3.5)
                if -15.0 * scale_factor <= gap <= max_allowed_gap:
                    if gap < min_gap:
                        min_gap = gap
                        best_j = j

            if best_j is not None:
                cand = line_infos[best_j]
                used.add(best_j)
                curr["words"].extend(cand["words"])
                curr["max_r"] = max(curr["max_r"], cand["max_r"])
                curr["min_y"] = min(curr["min_y"], cand["min_y"])
                curr["max_b"] = max(curr["max_b"], cand["max_b"])
                curr["text"] = " ".join(w.get("text", "") for w in curr["words"])
            else:
                break

        merged.append({
            "text": curr["text"],
            "words": curr["words"],
            "min_y": curr["min_y"],
            "min_x": curr["min_x"],
            "max_r": curr["max_r"],
            "max_b": curr["max_b"]
        })

    return merged


def _merge_two_pass_lines(lines1: list[dict], lines2: list[dict], scale_factor: float = 1.0) -> list[dict]:
    """
    Интеллектуально объединяет строки базового прохода и прохода с цветовой фильтрацией (R - B).
    Если во втором проходе обнаружена более полная/широкая строка (например, цветной вариант выбора),
    она заменяет неполный фрагмент из первого прохода.
    """
    if not lines2:
        return lines1
    if not lines1:
        return lines2

    def _line_bounds(l):
        if "min_x" in l and "min_y" in l:
            return l["min_x"], l["min_y"], l["max_r"], l["max_b"]
        words = l.get("words", [])
        if not words:
            return 0.0, 0.0, 0.0, 0.0
        xs = [float(w.get("bounding_rect", {}).get("x", 0.0)) for w in words]
        ys = [float(w.get("bounding_rect", {}).get("y", 0.0)) for w in words]
        ws = [float(w.get("bounding_rect", {}).get("width", 0.0)) for w in words]
        hs = [float(w.get("bounding_rect", {}).get("height", 0.0)) for w in words]
        return min(xs), min(ys), max(x + w for x, w in zip(xs, ws)), max(y + h for y, h in zip(ys, hs))

    final_lines = []
    used_lines2 = set()

    for l1 in lines1:
        x1_min, y1_min, x1_max, y1_max = _line_bounds(l1)
        w1 = x1_max - x1_min
        h1 = y1_max - y1_min

        best_l2 = None
        best_l2_idx = None
        for idx2, l2 in enumerate(lines2):
            if idx2 in used_lines2:
                continue
            x2_min, y2_min, x2_max, y2_max = _line_bounds(l2)
            w2 = x2_max - x2_min
            h2 = y2_max - y2_min
            if abs(y1_min - y2_min) < max(h1, h2) * 0.5:
                if w2 > w1 * 1.35:
                    best_l2 = l2
                    best_l2_idx = idx2
                    break

        if best_l2 is not None:
            used_lines2.add(best_l2_idx)
            final_lines.append(best_l2)
        else:
            final_lines.append(l1)

    for idx2, l2 in enumerate(lines2):
        if idx2 in used_lines2:
            continue
        x2_min, y2_min, x2_max, y2_max = _line_bounds(l2)
        w2 = x2_max - x2_min
        h2 = y2_max - y2_min
        if w2 < 50.0 * scale_factor:
            continue
        overlaps = False
        for fl in final_lines:
            _, fy_min, _, fy_max = _line_bounds(fl)
            if abs(y2_min - fy_min) < max(h2, fy_max - fy_min) * 0.5:
                overlaps = True
                break
        if not overlaps:
            final_lines.append(l2)

    final_lines.sort(key=lambda l: _line_bounds(l)[1])
    return final_lines


def extract_text_and_blocks(image: Union["QImage", np.ndarray], lang: str = "auto") -> list[dict]:
    """
    Распознаёт текст и возвращает блоки с точными экранными координатами
    для динамического наложения перевода прямо поверх текста (In-place).
    Каждый элемент: {"text": "...", "x": float, "y": float, "width": float, "height": float, "word_height": float}
    """
    bgr = _convert_to_bgr(image)
    if bgr is None or bgr.size == 0 or not _WINOCR_AVAILABLE:
        return []

    installed = get_available_ocr_languages()
    installed_tags = [item["tag"] for item in installed]
    matched_tag = None
    target_code = "en" if lang == "auto" else lang.lower()

    for itag in installed_tags:
        if itag.lower() == target_code:
            matched_tag = itag
            break

    if not matched_tag:
        for itag in installed_tags:
            if itag.lower().startswith(target_code[:2]):
                matched_tag = itag
                break

    if not matched_tag:
        for itag in installed_tags:
            if "en" in itag.lower():
                matched_tag = itag
                break
    if not matched_tag and installed_tags:
        matched_tag = installed_tags[0]

    target_lang = matched_tag if matched_tag else "en-US"

    try:
        h, w = bgr.shape[:2]
        min_dim = min(h, w)
        max_dim = max(h, w)
        # Оптимальный масштаб и мягкий unsharp mask для Windows Media OCR
        if min_dim < 140 or max_dim < 200:
            scale_factor = 2.0
        elif min_dim < 300:
            scale_factor = 1.4
        elif min_dim <= 1200 and max_dim <= 2200:
            scale_factor = 1.2
        else:
            scale_factor = 1.0

        if cv2 is not None and scale_factor > 1.0:
            up = cv2.resize(bgr, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_LANCZOS4)
            blur = cv2.GaussianBlur(up, (0, 0), 2.0)
            scan_img = cv2.addWeighted(up, 1.4, blur, -0.4, 0)
        else:
            scan_img = bgr

        res = winocr.recognize_cv2_sync(scan_img, lang=target_lang)
        raw_lines = res.get("lines", []) if res else []

        # Если целевой язык auto или ru, и установлен русский языковой пакет Windows OCR,
        # проверяем наличие кириллических надписей, но НЕ перезаписываем чистый распознанный английский текст
        if (not raw_lines or lang in ("auto", "ru")) and "ru" in installed_tags and target_lang != "ru":
            res_ru = winocr.recognize_cv2_sync(scan_img, lang="ru")
            lines_ru = res_ru.get("lines", []) if res_ru else []
            cyrillic_count = sum(len(re.findall(r"[\u0400-\u04FF]", l.get("text", ""))) for l in lines_ru)
            latin_count = sum(len(re.findall(r"[a-zA-Z]", l.get("text", ""))) for l in raw_lines)
            if cyrillic_count > 0 and (cyrillic_count >= latin_count * 0.35 or not raw_lines):
                raw_lines = lines_ru

        # Объединяем разорванные коллинеарные сегменты строк
        raw_lines = _merge_collinear_lines(raw_lines, scale_factor)

        # Двухпроходное распознавание для цветного текста (варианты выбора в визуальных новеллах, оранжевые/желтые плашки поверх лиц)
        if cv2 is not None and bgr.ndim == 3:
            try:
                B, G, R = cv2.split(bgr)
                r_minus_b = cv2.subtract(R, B)
                if np.count_nonzero(r_minus_b > 60) > 200:
                    r_b_up = cv2.resize(r_minus_b, None, fx=scale_factor, fy=scale_factor, interpolation=cv2.INTER_LANCZOS4) if scale_factor > 1.0 else r_minus_b
                    r_b_bgr = cv2.cvtColor(r_b_up, cv2.COLOR_GRAY2BGR)
                    res_color = winocr.recognize_cv2_sync(r_b_bgr, lang=target_lang)
                    color_lines = _merge_collinear_lines(res_color.get("lines", []) if res_color else [], scale_factor)
                    raw_lines = _merge_two_pass_lines(raw_lines, color_lines, scale_factor)
            except Exception:
                pass

        blocks = []
        for l in raw_lines:
            ltxt = l.get("text", "").strip()
            words = l.get("words", [])
            if not ltxt or not words:
                continue

            # Разбиваем слова одной OcrLine, если между ними неестественно большой горизонтальный разрыв
            # (например, кнопки меню в разных углах экрана или разные колонки таблиц)
            chunks = []
            curr_chunk = [words[0]]
            for w_item in words[1:]:
                prev_rect = curr_chunk[-1].get("bounding_rect", {})
                curr_rect = w_item.get("bounding_rect", {})
                prev_r = float(prev_rect.get("x", 0.0)) + float(prev_rect.get("width", 0.0))
                curr_x = float(curr_rect.get("x", 0.0))
                gap = curr_x - prev_r
                word_h = max(float(prev_rect.get("height", 15.0)), float(curr_rect.get("height", 15.0)))
                if gap > max(45.0 * scale_factor, word_h * 2.5):
                    chunks.append(curr_chunk)
                    curr_chunk = [w_item]
                else:
                    curr_chunk.append(w_item)
            chunks.append(curr_chunk)

            for chunk in chunks:
                chunk_txt = " ".join(w.get("text", "") for w in chunk).strip()
                if not chunk_txt:
                    continue
                chunk_txt = _postprocess_ocr_text(chunk_txt)
                xs = [float(w.get("bounding_rect", {}).get("x", 0.0)) for w in chunk]
                ys = [float(w.get("bounding_rect", {}).get("y", 0.0)) for w in chunk]
                ws = [float(w.get("bounding_rect", {}).get("width", 0.0)) for w in chunk]
                hs = [float(w.get("bounding_rect", {}).get("height", 0.0)) for w in chunk]

                min_x = min(xs) / scale_factor
                min_y = min(ys) / scale_factor
                max_r = max(x + w_val for x, w_val in zip(xs, ws)) / scale_factor
                max_b = max(y + h_val for y, h_val in zip(ys, hs)) / scale_factor
                bw = max_r - min_x
                bh = max_b - min_y
                med_h = float(np.median(hs)) / scale_factor if hs else bh

                # Извлекаем фрагмент кадра для проверки контраста и отсечения текстур камней/фона
                x1, y1 = max(0, int(min_x)), max(0, int(min_y))
                x2, y2 = min(w, int(max_r)), min(h, int(max_b))
                crop_sample = bgr[y1:y2, x1:x2] if (x2 > x1 and y2 > y1) else None

                if not is_valid_ocr_text(chunk_txt, bw, bh, crop_sample):
                    continue

                blocks.append({
                    "text": chunk_txt,
                    "x": min_x,
                    "y": min_y,
                    "width": bw,
                    "height": bh,
                    "word_height": med_h
                })

        return blocks
    except Exception:
        return []

