# -*- coding: utf-8 -*-
"""
Модуль локального и гибридного перевода текста (Translator).
Поддерживает:
1. Мгновенное кэширование переведённых фраз в памяти (0 мс).
2. Автономный оффлайн-перевод через argostranslate (если установлен).
3. Высокоскоростной легковесный API Google GTX (без ключей и лимитов, ~40-80 мс).
4. Встроенный оффлайн-глоссарий для базовых системных, игровых и технических фраз.
"""

from __future__ import annotations
import threading
import urllib.request
import urllib.parse
import json
import re
import concurrent.futures

_TRANSLATION_CACHE: dict[tuple[str, str, str], str] = {}
_CACHE_LOCK = threading.Lock()
_MAX_CACHE_ENTRIES = 2000

# Базовый встроенный словарь для оффлайн-режима
_OFFLINE_GLOSSARY_EN_RU = {
    "ok": "ОК",
    "cancel": "Отмена",
    "apply": "Применить",
    "save": "Сохранить",
    "load": "Загрузить",
    "start": "Старт",
    "stop": "Стоп",
    "play": "Играть",
    "pause": "Пауза",
    "resume": "Продолжить",
    "exit": "Выход",
    "quit": "Выйти",
    "settings": "Настройки",
    "options": "Опции",
    "help": "Справка",
    "warning": "Предупреждение",
    "error": "Ошибка",
    "info": "Информация",
    "yes": "Да",
    "no": "Нет",
    "next": "Далее",
    "back": "Назад",
    "finish": "Завершить",
    "done": "Готово",
    "loading": "Загрузка...",
    "please wait": "Пожалуйста, подождите",
    "press any key": "Нажмите любую клавишу",
    "game over": "Игра окончена",
    "continue": "Продолжить",
    "restart": "Начать сначала",
    "new game": "Новая игра",
    "mission accomplished": "Миссия выполнена",
    "defeat": "Поражение",
    "victory": "Победа",
    "level": "Уровень",
    "score": "Счёт",
    "health": "Здоровье",
    "attack": "Атака",
    "defense": "Защита",
    "speed": "Скорость",
    "item": "Предмет",
    "inventory": "Инвентарь",
    "quest": "Задание",
    "skills": "Навыки",
    "dialogue": "Диалог",
    "confirm": "Подтвердить",
    "accept": "Принять",
    "decline": "Отклонить",
}

AVAILABLE_LANGUAGES = [
    ("ru", "Русский"),
    ("en", "English"),
    ("ja", "日本語 (Japanese)"),
    ("zh-CN", "中文 (Chinese)"),
    ("de", "Deutsch (German)"),
    ("fr", "Français (French)"),
    ("es", "Español (Spanish)"),
    ("ko", "한국어 (Korean)"),
    ("uk", "Українська (Ukrainian)"),
    ("it", "Italiano (Italian)")
]


def get_available_translation_languages() -> list[tuple[str, str]]:
    """Возвращает список поддерживаемых языков перевода."""
    return list(AVAILABLE_LANGUAGES)


def _translate_offline_glossary(text: str, target_lang: str) -> str:
    """Простой локальный перевод по словарю, если сеть недоступна."""
    if target_lang != "ru":
        return text
    clean = text.strip().lower()
    if clean in _OFFLINE_GLOSSARY_EN_RU:
        return _OFFLINE_GLOSSARY_EN_RU[clean]
    words = re.findall(r"\w+|[^\w\s]", text)
    translated_words = []
    has_match = False
    for w in words:
        w_low = w.lower()
        if w_low in _OFFLINE_GLOSSARY_EN_RU:
            tr_w = _OFFLINE_GLOSSARY_EN_RU[w_low]
            translated_words.append(tr_w if w.islower() else tr_w.capitalize())
            has_match = True
        else:
            translated_words.append(w)
    if has_match:
        return " ".join(translated_words)
    return text


def _translate_argos(text: str, src_lang: str, target_lang: str) -> str | None:
    """Перевод через оффлайн-движок Argos Translate, если он установлен."""
    try:
        import argostranslate.translate
        src = "en" if src_lang == "auto" else src_lang[:2].lower()
        tgt = target_lang[:2].lower()
        installed_languages = argostranslate.translate.get_installed_languages()
        from_lang = next((lang for lang in installed_languages if lang.code == src), None)
        to_lang = next((lang for lang in installed_languages if lang.code == tgt), None)
        if from_lang and to_lang:
            translation = from_lang.get_translation(to_lang)
            if translation:
                return translation.translate(text)
    except Exception:
        pass
    return None


def _translate_google_gtx(text: str, src_lang: str, target_lang: str) -> str | None:
    """Высокоскоростной бесплатный HTTP перевод через Google GTX."""
    try:
        if src_lang == "auto":
            sl = "auto"
        elif "zh" in src_lang.lower():
            sl = "zh-CN"
        else:
            sl = src_lang.split("-")[0].lower()

        if "zh" in target_lang.lower():
            tl = "zh-CN"
        else:
            tl = target_lang.split("-")[0].lower()

        q = urllib.parse.quote(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=2.5) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data and data[0]:
                translated_parts = [segment[0] for segment in data[0] if segment and segment[0]]
                return "".join(translated_parts).strip()
    except Exception:
        pass
    return None


def translate_text(
    text: str,
    source_lang: str = "auto",
    target_lang: str = "ru",
    *,
    src_lang: str | None = None,
    dest_lang: str | None = None
) -> str:
    """
    Переводит текст на целевой язык с мгновенным кэшированием.
    
    Параметры:
        text: Исходный распознанный текст
        source_lang / src_lang: Исходный язык ('auto', 'en', 'ja', etc.)
        target_lang / dest_lang: Целевой язык ('ru', 'en', etc.)
    
    Возвращает:
        Переведённый текст (или исходный текст при отсутствии изменений)
    """
    if src_lang is not None:
        source_lang = src_lang
    if dest_lang is not None:
        target_lang = dest_lang

    stripped = text.strip()
    if not stripped:
        return ""

    # Если текст состоит только из цифр и знаков препинания — перевод не нужен
    if not re.search(r"[a-zA-Z\u0400-\u04FF\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", stripped):
        return stripped

    # Если целевой язык — русский, и текст уже полностью на кириллице (без латиницы) — перевод не требуется
    if target_lang == "ru" and re.search(r"[\u0400-\u04FF]", stripped) and not re.search(r"[a-zA-Z]", stripped):
        return stripped

    # Если целевой язык — английский, и текст уже полностью на латинице (без других письменностей) — перевод не требуется
    if target_lang == "en" and re.search(r"[a-zA-Z]", stripped) and not re.search(r"[\u0400-\u04FF\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", stripped):
        return stripped

    effective_sl = source_lang
    if source_lang == "auto":
        has_latin = bool(re.search(r"[a-zA-Z]", stripped))
        has_cyrillic = bool(re.search(r"[\u0400-\u04FF]", stripped))
        if has_latin and not has_cyrillic:
            effective_sl = "en"
        elif re.search(r"[\u3040-\u309F\u30A0-\u30FF]", stripped):
            effective_sl = "ja"
        elif re.search(r"[\u4E00-\u9FFF]", stripped):
            effective_sl = "zh-CN"

    cache_key = (stripped.lower(), source_lang, target_lang)
    with _CACHE_LOCK:
        if cache_key in _TRANSLATION_CACHE:
            return _TRANSLATION_CACHE[cache_key]

    # 1. Попытка перевода через оффлайн Argos Translate
    res = _translate_argos(stripped, effective_sl, target_lang)

    # 2. Если оффлайн-движок не установлен — используем быстрый Google GTX (~40-80 мс)
    if not res:
        res = _translate_google_gtx(stripped, effective_sl, target_lang)

    # 3. Резервный перевод с принудительным 'en', если перевод с 'auto' вернул исходный текст без изменений
    if target_lang == "ru" and re.search(r"[a-zA-Z]{2,}", stripped) and (not res or res.strip().lower() == stripped.lower()):
        res = _translate_google_gtx(stripped, "en", "ru")

    # 4. Если сеть недоступна — используем локальный глоссарий
    if not res:
        res = _translate_offline_glossary(stripped, target_lang)

    final_result = res if res else stripped

    with _CACHE_LOCK:
        if len(_TRANSLATION_CACHE) >= _MAX_CACHE_ENTRIES:
            # Вытесняем старейший элемент
            _TRANSLATION_CACHE.pop(next(iter(_TRANSLATION_CACHE)), None)
        _TRANSLATION_CACHE[cache_key] = final_result

    return final_result


def translate_batch(
    texts: list[str],
    source_lang: str = "auto",
    target_lang: str = "ru",
    *,
    src_lang: str | None = None,
    dest_lang: str | None = None
) -> list[str]:
    """
    Пакетный высокоскоростной перевод списка строк за один сетевой запрос.
    1. Проверяет кэш для каждого элемента (0 мс).
    2. Все ненайденные в кэше фразы объединяет через безопасный разделитель '\\n---\\n'
       и переводит одним запросом.
    3. При несовпадении количества частей переводит недостающие индивидуально.
    4. Сохраняет результаты в кэш.
    """
    if src_lang is not None:
        source_lang = src_lang
    if dest_lang is not None:
        target_lang = dest_lang

    if not texts:
        return []

    results: list[str | None] = [None] * len(texts)
    uncached_indices: list[int] = []
    uncached_texts: list[str] = []

    for idx, raw in enumerate(texts):
        s = raw.strip()
        if not s:
            results[idx] = raw
            continue
        if not re.search(r"[a-zA-Z\u0400-\u04FF\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", s):
            results[idx] = raw
            continue

        # Если целевой язык — русский, и строка уже полностью на кириллице (без латиницы) — перевод не требуется
        if target_lang == "ru" and re.search(r"[\u0400-\u04FF]", s) and not re.search(r"[a-zA-Z]", s):
            results[idx] = raw
            continue

        # Если целевой язык — английский, и строка уже на латинице (без других письменностей) — перевод не требуется
        if target_lang == "en" and re.search(r"[a-zA-Z]", s) and not re.search(r"[\u0400-\u04FF\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", s):
            results[idx] = raw
            continue

        cache_key = (s.lower(), source_lang, target_lang)
        with _CACHE_LOCK:
            if cache_key in _TRANSLATION_CACHE:
                results[idx] = _TRANSLATION_CACHE[cache_key]
                continue

        uncached_indices.append(idx)
        uncached_texts.append(s)

    if not uncached_texts:
        return [r if r is not None else "" for r in results]

    # Определяем эффективный исходный язык: если строки содержат латиницу при source_lang='auto',
    # явно указываем 'en', чтобы Google Translate не детектировал ошибочно 'ru'
    effective_sl = source_lang
    if source_lang == "auto":
        has_latin = any(re.search(r"[a-zA-Z]", t) for t in uncached_texts)
        has_cyrillic = any(re.search(r"[\u0400-\u04FF]", t) for t in uncached_texts)
        if has_latin and not has_cyrillic:
            effective_sl = "en"
        elif any(re.search(r"[\u3040-\u309F\u30A0-\u30FF]", t) for t in uncached_texts):
            effective_sl = "ja"
        elif any(re.search(r"[\u4E00-\u9FFF]", t) for t in uncached_texts):
            effective_sl = "zh-CN"

    if len(uncached_texts) == 1:
        res = translate_text(uncached_texts[0], source_lang=effective_sl, target_lang=target_lang)
        results[uncached_indices[0]] = res
        return [r if r is not None else "" for r in results]

    delimiter = "\n---\n"
    joined = delimiter.join(uncached_texts)
    batch_res = translate_text(joined, source_lang=effective_sl, target_lang=target_lang)

    parts = [p.strip() for p in re.split(r"\n\s*(?:---+|[—–]{1,}|={3,})\s*\n", batch_res)]
    if len(parts) > len(uncached_texts) and not parts[0]:
        parts.pop(0)
    if len(parts) > len(uncached_texts) and not parts[-1]:
        parts.pop()

    if len(parts) == len(uncached_texts):
        for idx_u, part in enumerate(parts):
            target_idx = uncached_indices[idx_u]
            orig_s = uncached_texts[idx_u]

            # Проверка: если строка на латинице не перевелась (вернулась как есть на английском),
            # делаем точечный индивидуальный перевод с принудительным sl=en
            if (target_lang == "ru" and re.search(r"[a-zA-Z]{2,}", orig_s)
                and (part.strip().lower() == orig_s.strip().lower() or not re.search(r"[\u0400-\u04FF]", part))):
                fallback = translate_text(orig_s, source_lang="en", target_lang="ru")
                if fallback and fallback.strip().lower() != orig_s.strip().lower():
                    part = fallback

            results[target_idx] = part
            if part.strip().lower() != orig_s.strip().lower():
                cache_key = (orig_s.lower(), source_lang, target_lang)
                with _CACHE_LOCK:
                    if len(_TRANSLATION_CACHE) >= _MAX_CACHE_ENTRIES:
                        _TRANSLATION_CACHE.pop(next(iter(_TRANSLATION_CACHE)), None)
                    _TRANSLATION_CACHE[cache_key] = part
    else:
        # Fallback при нарушении структуры разделителей: переводим параллельно в потоках (ThreadPoolExecutor)
        # за ~200-300 мс вместо последовательной задержки на 10-15 секунд!
        def _fetch_single(orig_s: str) -> str:
            item_sl = "en" if (source_lang == "auto" and re.search(r"[a-zA-Z]", orig_s)) else source_lang
            return translate_text(orig_s, source_lang=item_sl, target_lang=target_lang)

        max_workers = min(6, len(uncached_texts))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            fetched_results = list(executor.map(_fetch_single, uncached_texts))

        for idx_u, res_item in enumerate(fetched_results):
            target_idx = uncached_indices[idx_u]
            results[target_idx] = res_item

    return [r if r is not None else "" for r in results]


def clear_translation_cache():
    """Очищает кэш переводов в памяти."""
    with _CACHE_LOCK:
        _TRANSLATION_CACHE.clear()


def get_cache_size() -> int:
    """Возвращает текущее количество записей в кэше переводов."""
    with _CACHE_LOCK:
        return len(_TRANSLATION_CACHE)
