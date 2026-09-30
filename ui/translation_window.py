# -*- coding: utf-8 -*-
"""
Плавающая интерактивная рамка живого перевода экрана в реальном времени (Live Screen Translator).
Позволяет свободно перемещать и масштабировать область на экране, непрерывно сканирует
текст под рамкой с помощью локального OCR и динамически отображает перевод:
1. В виде аккуратных субтитров (HUD) внизу рамки (в стиле игровых субтитров).
2. Либо прямо поверх оригинального текста (In-place замена) с точным соответствием:
   - размера шрифта (кегль)
   - цвета слов (извлечение доминантного цвета штриха с экрана)
   - гарнитуры и жирности начертания

Особенности эргономики:
- Панель управления и кнопки вынесены ПОЛНОСТЬЮ СНАРУЖИ (сверху рамки отдельным плавающим тулбаром).
- Рамка в режиме перевода является НЕОСЯЗАЕМОЙ (сквозной клик WS_EX_TRANSPARENT) — клики внутри рамки
  проходят прямо в игру или приложение за ней.
- В настройках и на тулбаре есть удобный переключатель между неосязаемым режимом и режимом настройки размера.
- Все кнопки тулбара имеют нормальный курсор руки/указателя (без курсора стрелочек изменения размера).
- Режим полной маскировки (Глазик): скрывает тулбар и рамку, оставляя лишь миниатюрную иконку глазика (16x16 px).
- Иконку глазика можно свободно перемещать по всему экрану как ЛЕВОЙ, так и ПРАВОЙ кнопкой мыши. Одиночный
  клик ЛКМ возвращает панель настроек.
- Высокоскоростной пакетный перевод (translate_batch) всех блоков за один сетевой запрос (<200 мс).
- Аппаратно исключена из захвата (WDA_EXCLUDEFROMCAPTURE), не грузит CPU на статичных кадрах (Smart Diff).
"""

from __future__ import annotations
import sys
import os
import time
import re
import difflib
import numpy as np

from PyQt6.QtCore import (
    Qt, QRect, QRectF, QPoint, QPointF, QSize, QThread, pyqtSignal, pyqtSlot,
    QMutex, QMutexLocker, QTimer, QEvent
)
from PyQt6.QtWidgets import (
    QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QMenu,
    QApplication, QGraphicsDropShadowEffect, QToolTip, QFileDialog
)
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QCursor, QPaintEvent, QMouseEvent,
    QPainterPath, QLinearGradient, QFontMetrics, QAction, QPixmap
)

from utils.screen_lock import safe_grab_screen_bgr, user32
from utils.ocr_helper import extract_text_and_blocks, extract_text_from_image, is_valid_ocr_text
from utils.translator import translate_text, translate_batch, get_available_translation_languages
from utils.i18n import tr
from utils.window_icon import get_window_qicon
from .icons import create_themed_icon, get_svg_pixmap


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


def is_desktop_or_taskbar(hwnd: int) -> bool:
    """Проверяет, принадлежит ли окно рабочему столу или панели задач Windows."""
    if not hwnd or sys.platform != "win32":
        return False
    try:
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetClassNameW(hwnd, buf, 256)
        cls_name = buf.value
        if cls_name in ("Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"):
            return True
    except Exception:
        pass
    return False


def is_window_above_us(target_hwnd: int, base_hwnd: int) -> bool:
    """Проверяет, находится ли целевое окно выше нашего окна в z-order стеке Windows."""
    if not target_hwnd or not base_hwnd or target_hwnd == base_hwnd or sys.platform != "win32":
        return False
    try:
        GW_HWNDPREV = 3
        curr = ctypes.windll.user32.GetWindow(base_hwnd, GW_HWNDPREV)
        count = 0
        while curr and count < 60:
            if curr == target_hwnd:
                return True
            curr = ctypes.windll.user32.GetWindow(curr, GW_HWNDPREV)
            count += 1
    except Exception:
        pass
    return False


def resolve_window_info(hwnd: int) -> dict | None:
    """Извлекает информацию об окне и его процессе: title, pid, process_name."""
    if not hwnd or sys.platform != "win32":
        return None
    try:
        import psutil
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()

        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        p_val = int(pid.value)
        if p_val == os.getpid():
            return None

        try:
            pname = psutil.Process(p_val).name()
        except Exception:
            pname = "Application"

        return {
            "hwnd": int(hwnd),
            "pid": p_val,
            "process_name": pname,
            "title": title or pname
        }
    except Exception:
        return None


def get_running_gui_windows() -> list[dict]:
    """
    Возвращает список запущенных пользовательских приложений с графическим интерфейсом:
    [{"hwnd": hwnd, "title": title, "pid": pid, "process_name": pname}, ...]
    """
    if sys.platform != "win32":
        return []
    from utils.screen_lock import enumerate_recordable_windows
    import psutil

    results = []
    seen_keys = set()
    my_pid = os.getpid()

    try:
        raw_wins = enumerate_recordable_windows()
        for h, raw_title in raw_wins:
            clean_title = raw_title.replace(" [свёрнуто]", "").strip()
            if not clean_title:
                continue
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
            p_val = int(pid.value)
            if p_val == my_pid:
                continue
            try:
                pname = psutil.Process(p_val).name()
            except Exception:
                pname = "Application"

            key = (pname.lower(), clean_title.lower())
            if key in seen_keys:
                continue
            seen_keys.add(key)

            results.append({
                "hwnd": int(h),
                "title": clean_title,
                "pid": p_val,
                "process_name": pname
            })
    except Exception:
        pass

    results.sort(key=lambda item: item["process_name"].lower())
    return results


FRAMIO_UI_KEYWORDS = (
    # Меню и режимы
    "все приложения", "активное окно", "запущенные окна", "запущенные процессы",
    "поверх текста (in-place)", "поверх текста", "субтитры (hud", "субтитры (hud внизу)",
    "субтитры перевода", "неосязаемая рамка", "повторять цвет текста", "повторять шрифт",
    "прозрачность фона", "цвет фона", "интервал сканирования", "умная пауза",
    "исходный язык", "язык перевода", "копировать текст перевода",
    "копировать изображение", "сохранить изображение", "размер шрифта",
    "привязать к приложению", "клики сквозь рамку",
    # Тексты всплывающих подсказок (tooltips)
    "привязать рамку", "привязать", "рамку перевода", "рамка перевода",
    "авто-пауза", "скрытие вне окна", "окну/процессу", "процессу",
    "перевод отключен", "кликните пкм", "кликните лкм", "перетащите лкм",
    "зажмите для перемещения", "выбора языков", "переключения режима",
    "настройки прозрачности", "приостановить сканирование", "возобновить сканирование",
    "копировать перевод", "скрыть рамку", "закрыть рамку", "сквозной клик",
    "режим настройки", "позволяет нажимать", "окрашивать переведенные",
    "подбирать жирность", "значок глазика", "глазика",
    # English keywords
    "all applications", "active window", "running windows", "passthrough frame",
    "match text color", "match font", "background opacity", "background theme",
    "scan interval", "smart diff", "source language", "target language",
    "copy translation", "save translation", "font size", "freeze scanning",
    "resume scanning", "pin translation frame", "pin frame", "auto-pause",
    "hide outside window", "drag to move", "close translation", "stealth mode",
    "toggle passthrough", "select languages", "display mode"
)


def _is_framio_ui_text(text: str) -> bool:
    """Проверяет, не содержит ли распознанный текст элементы меню, подсказок и настроек Framio."""
    if not text:
        return False
    tl = text.lower()
    return any(kw in tl for kw in FRAMIO_UI_KEYWORDS)


def _apply_tooltip_stealth():
    """Аппаратно исключает системное окно всплывающей подсказки QToolTip из захвата экрана (WDA_EXCLUDEFROMCAPTURE)."""
    if sys.platform != "win32":
        return
    try:
        for w in QApplication.topLevelWidgets():
            try:
                flags = w.windowFlags()
                cname = w.metaObject().className().lower()
                if (flags & Qt.WindowType.ToolTip) or w.inherits("QTipLabel") or "tiplabel" in cname:
                    hwnd = int(w.winId())
                    ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
            except Exception:
                pass
    except Exception:
        pass


def show_stealth_tooltip(pos: QPoint, text: str, parent: QWidget = None):
    """Отображает всплывающую подсказку QToolTip с немедленным аппаратным скрытием от захвата экрана."""
    if not text:
        return
    QToolTip.showText(pos, text, parent)
    _apply_tooltip_stealth()
    QTimer.singleShot(0, _apply_tooltip_stealth)
    QTimer.singleShot(15, _apply_tooltip_stealth)
    QTimer.singleShot(50, _apply_tooltip_stealth)


class StealthMenu(QMenu):
    """
    QMenu с аппаратным исключением из захвата экрана (WDA_EXCLUDEFROMCAPTURE)
    и гарантированным удержанием поверх рамки.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                # WDA_EXCLUDEFROMCAPTURE = 0x00000011
                ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            except Exception:
                pass


def create_stealth_menu(parent=None) -> StealthMenu:
    """Создает StealthMenu с аппаратным исключением из захвата экрана."""
    return StealthMenu(parent)


def restore_punctuation(orig: str, tr: str) -> str:
    """
    Восстанавливает и сохраняет пунктуацию оригинала (восклицательные/вопросительные знаки,
    двоеточия, многоточия, точки, запятые, кавычки, диалоговые тире),
    предотвращая их потерю сетевым переводчиком.
    """
    if not orig or not tr:
        return str(tr or "")
    orig = str(orig).strip()
    tr = str(tr).strip()
    if not orig or not tr:
        return tr

    # Диалоговые тире
    if orig.startswith("- ") and not tr.startswith("- "):
        tr = "- " + tr
    elif orig.startswith("— ") and not tr.startswith("— "):
        tr = "— " + tr

    # Кавычки
    if (orig.startswith('"') and orig.endswith('"')) and not (tr.startswith('"') and tr.endswith('"')):
        tr = f'"{tr}"'
    elif (orig.startswith('«') and orig.endswith('»')) and not (tr.startswith('«') and tr.endswith('»')):
        tr = f'«{tr}»'

    # Знаки препинания в конце строки
    for p in ["?!", "!?", "...", "…", "!", "?", ":", ";", ",", "."]:
        if orig.endswith(p):
            if not tr.endswith(p):
                tr = tr.rstrip(".,!?:;") + p
            break
    return tr


def extract_visual_props(crop_bgr: np.ndarray) -> dict:
    """
    Анализирует вырезку текста из экрана и определяет:
    - color_rgb: кортеж (r, g, b) оригинального цвета текста
    - bg_color_rgb: кортеж (r, g, b) фонового цвета
    - is_bold: логический флаг жирности начертания
    - is_italic: логический флаг курсива
    - font_family: семейство шрифта ('Segoe UI', 'Trebuchet MS', 'Impact', 'Arial Black')
    """
    import cv2
    if crop_bgr is None or crop_bgr.size == 0 or crop_bgr.shape[0] < 4 or crop_bgr.shape[1] < 4:
        return {
            "color_rgb": (248, 250, 252),
            "bg_color_rgb": (15, 23, 42),
            "is_bold": True,
            "is_italic": False,
            "font_family": "Segoe UI"
        }

    try:
        h, w = crop_bgr.shape[:2]
        # Оценка фона по внешним пикселям границы вырезки
        top = crop_bgr[0, :]
        bot = crop_bgr[-1, :]
        left = crop_bgr[:, 0]
        right = crop_bgr[:, -1]
        border_bgr = np.concatenate([top, bot, left, right], axis=0)
        bg_med_bgr = np.median(border_bgr, axis=0).astype(int)
        bg_rgb = (int(bg_med_bgr[2]), int(bg_med_bgr[1]), int(bg_med_bgr[0]))
        bg_lum = 0.299 * bg_rgb[0] + 0.587 * bg_rgb[1] + 0.114 * bg_rgb[2]

        # Цветовое расстояние от фона
        diff = np.linalg.norm(crop_bgr.astype(float) - bg_med_bgr.astype(float), axis=2)
        max_d = float(np.max(diff)) if diff.size > 0 else 0.0

        if max_d >= 25.0:
            # Отбираем именно ядро глифов (самые контрастные пиксели, отсекая полутени)
            text_thresh = max(24.0, max_d * 0.52)
            text_mask = diff >= text_thresh
            if np.count_nonzero(text_mask) >= 3:
                text_pixels = crop_bgr[text_mask]
                pts_bgr = np.median(text_pixels, axis=0).astype(int)
                color_rgb = (int(pts_bgr[2]), int(pts_bgr[1]), int(pts_bgr[0]))
                fg_lum = 0.299 * color_rgb[0] + 0.587 * color_rgb[1] + 0.114 * color_rgb[2]
                if abs(fg_lum - bg_lum) < 45:
                    color_rgb = (255, 255, 255) if bg_lum < 128 else (15, 23, 42)
            else:
                color_rgb = (255, 255, 255) if bg_lum < 128 else (15, 23, 42)
        else:
            color_rgb = (255, 255, 255) if bg_lum < 128 else (15, 23, 42)

        # Определение жирности шрифта (жирный только при плотном заполнении глифами)
        text_density = float(np.count_nonzero(diff > 25.0)) / float(max(1, h * w))
        is_bold = (text_density > 0.32)

        return {
            "color_rgb": color_rgb,
            "bg_color_rgb": bg_rgb,
            "is_bold": is_bold,
            "is_italic": False,
            "font_family": "Segoe UI"
        }
    except Exception:
        return {
            "color_rgb": (248, 250, 252),
            "bg_color_rgb": (15, 23, 42),
            "is_bold": False,
            "is_italic": False,
            "font_family": "Segoe UI"
        }


def should_group_lines(prev_line: dict, curr_line: dict) -> bool:
    """
    Определяет, являются ли две последовательные строки экрана
    частями одного предложения/абзаца для совместного перевода.
    """
    prev_txt = prev_line.get("text", "").strip()
    curr_txt = curr_line.get("text", "").strip()
    if not prev_txt or not curr_txt:
        return False

    # 1. Если предыдущая строка заканчивается точкой, вопросительным или восклицательным знаком — это конец предложения
    if prev_txt.rstrip().endswith((".", "!", "?", ":", ";", "…", "—", "-")):
        return False

    prev_y = float(prev_line.get("y", 0))
    prev_h = float(prev_line.get("height", 20))
    prev_lh = float(prev_line.get("word_height", prev_h))
    prev_bottom = prev_y + prev_h

    curr_y = float(curr_line.get("y", 0))
    curr_h = float(curr_line.get("height", 20))
    curr_lh = float(curr_line.get("word_height", curr_h))

    gap_y = curr_y - prev_bottom

    # 2. Межстрочный интервал: строка должна быть прямо под предыдущей
    max_gap = max(18.0, prev_lh * 1.05)
    if not (-4.0 <= gap_y <= max_gap):
        return False

    # 3. Кегли шрифтов должны быть близки (не объединять заголовок с мелким текстом)
    if abs(prev_lh - curr_lh) > max(7.0, prev_lh * 0.45):
        return False

    # 4. Горизонтальное перекрытие или выравнивание (по левому краю или по центру)
    prev_x = float(prev_line.get("x", 0))
    prev_w = float(prev_line.get("width", 50))
    curr_x = float(curr_line.get("x", 0))
    curr_w = float(curr_line.get("width", 50))

    h_overlap = (curr_x < (prev_x + prev_w + 25.0)) and ((curr_x + curr_w) > (prev_x - 25.0))
    left_aligned = abs(curr_x - prev_x) < max(40.0, prev_w * 0.30)
    prev_center = prev_x + prev_w / 2.0
    curr_center = curr_x + curr_w / 2.0
    center_aligned = abs(curr_center - prev_center) < max(50.0, prev_w * 0.30)

    if not (h_overlap or left_aligned or center_aligned):
        return False

    # 5. Семантическая проверка: не объединять пункты меню/списков (1-2 слова с заглавной буквы)
    prev_words = prev_txt.split()
    curr_words = curr_txt.split()

    # Случай А: curr_txt начинается со строчной буквы (явное продолжение фразы)
    if curr_txt[0].islower():
        return True

    # Случай Б: prev_txt заканчивается служебным словом (предлог, союз, местоимение, вспомогательный глагол)
    CONNECTIVE_ENDINGS = {
        "to", "of", "in", "on", "for", "with", "at", "by", "from", "as", "into", "about",
        "and", "or", "but", "so", "because", "that", "which", "who", "whom", "whose",
        "a", "an", "the",
        "your", "my", "his", "her", "their", "our", "its", "this", "these", "those",
        "is", "are", "was", "were", "be", "been", "being", "have", "has", "had",
        "will", "would", "can", "could", "should", "may", "might", "must",
        "what", "where", "when", "why", "how", "which", "favorite",
        "и", "в", "на", "с", "по", "к", "для", "о", "об", "от", "из", "за", "у", "до",
        "что", "как", "где", "когда", "который", "которая", "которое", "которые",
        "твой", "ваш", "мой", "наш", "его", "ее", "их", "этот", "эта", "это", "эти"
    }
    last_word_clean = prev_words[-1].lower().strip(" ,;:-—'\"")
    if last_word_clean in CONNECTIVE_ENDINGS:
        return True

    # Случай В: Длинная строка без точки (3+ слова), переходящая в следующую строку (2+ слова)
    if len(prev_words) >= 3 and len(curr_words) >= 2:
        return True

    # Случай Г: Субтитры с выравниванием по центру (две строки диалога)
    if center_aligned and len(prev_words) >= 2:
        return True

    # Иначе — скорее всего разные кнопки/пункты меню, не объединяем
    return False


def split_translation_to_lines(line_texts: list[str], full_translation: str) -> list[str]:
    """
    Распределяет слова единого перевода пропорционально строкам оригинала.
    Сохраняет контекст перевода всего предложения, но возвращает отдельный
    текст для каждой физической строки экрана, исключая отрисовку в межстрочном интервале.
    """
    if not line_texts:
        return []
    if len(line_texts) == 1:
        return [full_translation.strip()]

    words = full_translation.strip().split()
    if not words:
        return ["" for _ in line_texts]

    if len(words) <= len(line_texts):
        res = []
        for i in range(len(line_texts)):
            if i < len(words):
                res.append(words[i])
            else:
                res.append("")
        return res

    # Длины строк оригинала в символах (без краевых пробелов)
    lengths = [max(1, len(t.strip())) for t in line_texts]
    total_len = sum(lengths)
    ratios = [l / float(total_len) for l in lengths]

    total_words = len(words)
    res = []
    curr_idx = 0

    for i, ratio in enumerate(ratios):
        if i == len(ratios) - 1:
            line_words = words[curr_idx:]
        else:
            count = max(1, int(round(total_words * ratio)))
            remaining_lines = len(ratios) - 1 - i
            count = min(count, len(words) - curr_idx - remaining_lines)
            count = max(1, count)
            line_words = words[curr_idx : curr_idx + count]
            curr_idx += count
        res.append(" ".join(line_words))

    return res


def group_multiline_blocks(raw_blocks: list[dict]) -> list[dict]:
    """
    Интеллектуально объединяет строки OCR, принадлежащие одному абзацу/предложению,
    в единые смысловые группы с общим контекстом перевода.
    Каждая группа содержит объединенный 'text' для качественного перевода,
    а также список исходных физических строк 'lines' для раздельной точной отрисовки
    непосредственно поверх каждой строки экрана без перекрытия межстрочного интервала.
    """
    if not raw_blocks:
        return []

    # Сортируем блоки сверху вниз, затем слева направо
    sorted_blocks = sorted(raw_blocks, key=lambda b: (float(b.get("y", 0)), float(b.get("x", 0))))
    merged = []

    for b in sorted_blocks:
        txt = b.get("text", "").strip()
        if not txt:
            continue
        bx = float(b.get("x", 0))
        by = float(b.get("y", 0))
        bw = float(b.get("width", 50))
        bh = float(b.get("height", 20))
        lh = float(b.get("word_height", bh))

        initial_line = {
            "text": txt,
            "x": bx,
            "y": by,
            "width": bw,
            "height": bh,
            "word_height": lh
        }

        if not merged:
            merged.append({
                "text": txt,
                "x": bx,
                "y": by,
                "width": bw,
                "height": bh,
                "line_height": lh,
                "lines_count": 1,
                "lines": [initial_line]
            })
            continue

        prev = merged[-1]
        prev_last_line = prev["lines"][-1]
        prev_cnt = prev.get("lines_count", 1)

        if prev_cnt < 3 and should_group_lines(prev_last_line, initial_line):
            new_x = min(prev["x"], bx)
            new_y = min(prev["y"], by)
            new_r = max(prev["x"] + prev["width"], bx + bw)
            new_b = max(prev["y"] + prev["height"], by + bh)
            prev["text"] = prev["text"] + " " + txt
            prev["x"] = new_x
            prev["y"] = new_y
            prev["width"] = new_r - new_x
            prev["height"] = new_b - new_y
            prev["line_height"] = (prev["line_height"] * prev_cnt + lh) / float(prev_cnt + 1)
            prev["lines_count"] = prev_cnt + 1
            prev["lines"].append(initial_line)
        else:
            merged.append({
                "text": txt,
                "x": bx,
                "y": by,
                "width": bw,
                "height": bh,
                "line_height": lh,
                "lines_count": 1,
                "lines": [initial_line]
            })

    return merged


def _clean_match_str(s: str) -> str:
    return re.sub(r"[\s\W_]+", "", s.lower())


def _is_hover_match(new_text: str, prev_text: str) -> bool:
    """Определяет, является ли распознанный блок искажением стабильного текста при наведении мыши (hover)."""
    if not new_text or not prev_text:
        return False
    n_lower = new_text.strip().lower()
    p_lower = prev_text.strip().lower()
    if n_lower == p_lower:
        return True

    c_n = _clean_match_str(n_lower)
    c_p = _clean_match_str(p_lower)
    if not c_n or not c_p:
        return False
    if c_n == c_p:
        return True

    len_min = min(len(c_n), len(c_p))
    len_max = max(len(c_n), len(c_p))
    ratio = difflib.SequenceMatcher(None, n_lower, p_lower).ratio()

    # 1. Характерные одинаковые окончания ('training.', 'office.', 'ing.')
    for suffix in ("training.", "training", "office.", "office", "ing.", "ing"):
        if n_lower.endswith(suffix) and p_lower.endswith(suffix):
            if len_min >= 4:
                return True

    # 2. Если один является подстрокой другого при схожей длине (>= 48%)
    if len_min >= 5 and (len_min / float(len_max)) >= 0.48:
        if c_n in c_p or c_p in c_n:
            return True

    # 3. Высокое лексическое сходство
    if ratio >= 0.65 and (len_min / float(len_max)) >= 0.60:
        return True

    # 4. Характерные глитчи Windows OCR при наведении на кнопки меню
    has_mixed = bool(re.search(r"[a-zA-Z]", new_text) and re.search(r"[\u0400-\u04FF]", new_text))
    has_hover_glitch = bool(
        re.search(r"[&—–_üöä~#@|/\\«»]", new_text) or
        "днд" in new_text.lower() or
        "уэд" in new_text.lower() or
        "oc" in new_text.lower() or
        "marqa" in new_text.lower() or
        "marvial" in new_text.lower()
    )
    if (has_mixed or has_hover_glitch) and (len_min / float(len_max)) >= 0.35:
        if ratio >= 0.35 or any(n_lower.endswith(s) for s in ("ing.", "ing", "s", ".")):
            return True

    return False


class TranslationScannerWorker(QThread):
    """
    Фоновый рабочий поток сканирования и перевода.
    Работает с оптимизацией Smart Diff: не тратит ресурсы процессора,
    когда изображение в рамке остаётся неизменным.
    """
    translation_ready = pyqtSignal(str, str, list)  # original, translated, blocks
    scan_status = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._paused = False
        self._smart_diff = True
        self._interval_ms = 300
        self._mutex = QMutex()
        self._rect = QRect(100, 100, 480, 240)
        self._src_lang = "auto"
        self._tgt_lang = "ru"
        self._mode = "inplace"
        self._last_frame_small = None
        self._last_ocr_text = ""
        self._stable_blocks = []
        self._menu_open = False
        self._tooltip_active = False
        self._menu_cooldown_until = 0.0

    def set_tooltip_active(self, active: bool):
        with QMutexLocker(self._mutex):
            self._tooltip_active = active
            if active:
                self._menu_cooldown_until = time.time() + 999999.0
            else:
                self._menu_cooldown_until = time.time() + 0.35
                self._last_frame_small = None

    def set_menu_open(self, is_open: bool):
        with QMutexLocker(self._mutex):
            self._menu_open = is_open
            if is_open:
                self._menu_cooldown_until = time.time() + 999999.0
            else:
                self._menu_cooldown_until = time.time() + 0.40
                self._last_frame_small = None
                self._last_ocr_text = ""
                self._stable_blocks = []

    def reset_cache(self):
        with QMutexLocker(self._mutex):
            self._last_frame_small = None
            self._last_ocr_text = ""
            self._stable_blocks = []

    def set_mode(self, mode: str):
        with QMutexLocker(self._mutex):
            self._mode = mode
            self._last_ocr_text = ""
            self._last_frame_small = None
            self._stable_blocks = []

    def update_geometry(self, rect: QRect):
        with QMutexLocker(self._mutex):
            self._rect = QRect(rect)
            self._last_frame_small = None
            self._last_ocr_text = ""
            self._stable_blocks = []

    def set_languages(self, src: str, tgt: str):
        with QMutexLocker(self._mutex):
            self._src_lang = src
            self._tgt_lang = tgt
            self._last_ocr_text = ""
            self._last_frame_small = None
            self._stable_blocks = []

    def set_paused(self, paused: bool):
        with QMutexLocker(self._mutex):
            self._paused = paused
            if not paused:
                self._last_frame_small = None
                self._last_ocr_text = ""
                self._stable_blocks = []

    def set_interval(self, interval_ms: int):
        with QMutexLocker(self._mutex):
            self._interval_ms = max(30, interval_ms)

    def set_smart_diff(self, enabled: bool):
        with QMutexLocker(self._mutex):
            self._smart_diff = enabled
            if not enabled:
                self._last_frame_small = None

    def stop(self):
        self._running = False
        self.wait(1500)

    def run(self):
        import cv2
        interval = self._interval_ms
        while self._running:
            try:
                now = time.time()
                with QMutexLocker(self._mutex):
                    paused = self._paused or self._menu_open or self._tooltip_active or (now < self._menu_cooldown_until)
                    r = QRect(self._rect)
                    src = self._src_lang
                    tgt = self._tgt_lang
                    interval = self._interval_ms
                    use_diff = self._smart_diff
                    mode = self._mode

                if paused or r.width() < 30 or r.height() < 30:
                    self.msleep(150)
                    continue

                rx, ry, rw, rh = r.x(), r.y(), r.width(), r.height()
                frame_bgr = safe_grab_screen_bgr(rx, ry, rw, rh)

                with QMutexLocker(self._mutex):
                    if self._paused or self._menu_open or self._tooltip_active or (time.time() < self._menu_cooldown_until):
                        self.msleep(100)
                        continue

                if frame_bgr is None or frame_bgr.size == 0:
                    if self._last_ocr_text != "" or self._stable_blocks:
                        self._last_ocr_text = ""
                        self._stable_blocks = []
                        self.translation_ready.emit("", "", [])
                    self.msleep(interval)
                    continue

                # Smart Diff: уменьшаем кадр до 240x135 и проверяем изменение
                if use_diff:
                    try:
                        small_gray = cv2.cvtColor(cv2.resize(frame_bgr, (240, 135), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
                    except Exception:
                        small_gray = None

                    if self._last_frame_small is not None and small_gray is not None:
                        diff = cv2.absdiff(small_gray, self._last_frame_small)
                        mean_diff = float(np.mean(diff))
                        max_diff = int(np.max(diff))
                        # Пропускаем кадры с крайне низким изменением (экономия CPU)
                        if mean_diff < 0.25 and max_diff < 15:
                            self.msleep(interval)
                            continue

                    self._last_frame_small = small_gray

                # Запуск OCR
                raw_blocks = extract_text_and_blocks(frame_bgr, lang=src)
                raw_blocks = [
                    b for b in raw_blocks
                    if is_valid_ocr_text(b.get("text", ""), b.get("width", 50), b.get("height", 20))
                ]
                blocks = group_multiline_blocks(raw_blocks)
                full_text = " ".join(b["text"] for b in blocks if b.get("text")).strip()

                if not full_text:
                    ft, _ = extract_text_from_image(frame_bgr, lang=src)
                    if is_valid_ocr_text(ft):
                        full_text = ft

                has_letters = bool(re.search(r"[\w\u0400-\u04FF\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]", full_text)) if full_text else False
                is_valid = is_valid_ocr_text(full_text) if full_text else False

                if not full_text or not has_letters or not is_valid:
                    if self._last_ocr_text != "" or self._stable_blocks:
                        self._last_ocr_text = ""
                        self._stable_blocks = []
                        self.translation_ready.emit("", "", [])
                    self.msleep(interval)
                    continue

                # Проверка на текст элементов интерфейса Framio (чтобы исключить случайный перевод выпадающих списков)
                if _is_framio_ui_text(full_text):
                    self.msleep(interval)
                    continue

                if full_text == self._last_ocr_text:
                    self.msleep(interval)
                    continue

                # Проверяем, изменился ли текст существенно (смена реплики, новый кадр диалога или смена меню)
                text_changed_significantly = False
                if self._last_ocr_text:
                    ratio = difflib.SequenceMatcher(None, full_text.lower(), self._last_ocr_text.lower()).ratio()
                    # Если текст кадра изменился кардинально (смена диалога или экрана)
                    if ratio < 0.40:
                        text_changed_significantly = True

                # Если кадр/текст существенно изменился — сразу скрываем старый перевод,
                # чтобы он не оставался на новом кадре во время сетевого перевода
                if text_changed_significantly and self._stable_blocks:
                    self._stable_blocks = []
                    self.translation_ready.emit("", "", [])

                self._last_ocr_text = full_text

                if mode == "hud":
                    # В режиме субтитров HUD переводим только общий текст одним быстрым вызовом (~50-150 мс)
                    translated_full = translate_text(full_text, source_lang=src, target_lang=tgt)
                    translated_full = restore_punctuation(full_text, translated_full)
                    with QMutexLocker(self._mutex):
                        if self._paused or self._menu_open or self._tooltip_active or (time.time() < self._menu_cooldown_until):
                            continue
                    self.translation_ready.emit(full_text, translated_full, [])
                else:
                    # В режиме In-place собираем все непустые блоки (исключая элементы интерфейса Framio)
                    valid_blocks = []
                    for b in blocks:
                        b_text = b.get("text", "").strip()
                        if b_text and not _is_framio_ui_text(b_text):
                            valid_blocks.append(b)

                    # Проверка пространственной стабильности (защита от искажений при наведении мыши / hover)
                    block_texts_to_translate = []
                    indices_to_translate = []
                    translated_list = [None] * len(valid_blocks)
                    matched_prev_indices = set()

                    for idx, b in enumerate(valid_blocks):
                        b_text = b.get("text", "").strip()
                        bx = float(b.get("x", 0))
                        by = float(b.get("y", 0))
                        bw = float(b.get("width", 50))
                        bh = float(b.get("height", 20))

                        matched_stable = None
                        matched_p_idx = None
                        for p_i, prev_s in enumerate(self._stable_blocks):
                            px, py, pw, ph = prev_s["x"], prev_s["y"], prev_s["width"], prev_s["height"]
                            if abs((by + bh / 2.0) - (py + ph / 2.0)) < max(18.0, bh * 0.55):
                                if abs((bx + bw / 2.0) - (px + pw / 2.0)) < max(60.0, bw * 0.50):
                                    matched_stable = prev_s
                                    matched_p_idx = p_i
                                    break

                        if matched_stable is not None:
                            prev_orig = matched_stable["orig"]
                            prev_trans = matched_stable["trans"]
                            if _is_hover_match(b_text, prev_orig):
                                translated_list[idx] = prev_trans
                                b["text"] = prev_orig
                                prev_s["ttl"] = 4
                                matched_prev_indices.add(matched_p_idx)
                                continue

                        block_texts_to_translate.append(b_text)
                        indices_to_translate.append(idx)
                        if matched_p_idx is not None:
                            matched_prev_indices.add(matched_p_idx)

                    # Hysteresis retention: если большинство блоков экрана стабильны,
                    # удерживаем временно пропавшие из-за курсора или артефактов блоки на 3 кадра
                    if self._stable_blocks and len(matched_prev_indices) >= max(1, len(self._stable_blocks) // 2):
                        for p_i, prev_s in enumerate(self._stable_blocks):
                            if p_i not in matched_prev_indices:
                                curr_ttl = prev_s.get("ttl", 3) - 1
                                if curr_ttl > 0:
                                    prev_s["ttl"] = curr_ttl
                                    valid_blocks.append({
                                        "text": prev_s["orig"],
                                        "x": prev_s["x"],
                                        "y": prev_s["y"],
                                        "width": prev_s["width"],
                                        "height": prev_s["height"],
                                        "word_height": prev_s.get("word_height", 20),
                                        "lines": prev_s.get("lines", [])
                                    })
                                    translated_list.append(prev_s["trans"])

                if block_texts_to_translate:
                    # Если большинство или все блоки новые (смена экрана/меню) — немедленно гасим старый оверлей
                    if len(block_texts_to_translate) == len(valid_blocks) or len(block_texts_to_translate) >= max(2, int(len(valid_blocks) * 0.7)):
                        if self._stable_blocks:
                            self._stable_blocks = []
                            self.translation_ready.emit("", "", [])

                    batch_res = translate_batch(block_texts_to_translate, source_lang=src, target_lang=tgt)
                    for target_idx, tr_res in zip(indices_to_translate, batch_res):
                        translated_list[target_idx] = tr_res

                new_stable = []
                for b, tr_val in zip(valid_blocks, translated_list):
                    if tr_val:
                        new_stable.append({
                            "orig": b.get("text", "").strip(),
                            "trans": tr_val,
                            "x": float(b.get("x", 0)),
                            "y": float(b.get("y", 0)),
                            "width": float(b.get("width", 50)),
                            "height": float(b.get("height", 20)),
                            "word_height": float(b.get("word_height", 20)),
                            "lines": b.get("lines", []),
                            "ttl": 4
                        })
                self._stable_blocks = new_stable

                translated_blocks = []
                f_h, f_w = frame_bgr.shape[:2]
                to_log_x = float(rw) / float(f_w)
                to_log_y = float(rh) / float(f_h)

                for b, b_tr_raw in zip(valid_blocks, translated_list):
                    b_orig = b.get("text", "").strip()
                    b_tr = restore_punctuation(b_orig, b_tr_raw)

                    # Получаем физические строки оригинала для этого смыслового блока
                    constituent_lines = b.get("lines", [])
                    if not constituent_lines:
                        constituent_lines = [b]

                    # Распределяем слова перевода по физическим строкам экрана
                    if len(constituent_lines) > 1:
                        line_orig_texts = [l.get("text", "").strip() for l in constituent_lines]
                        line_tr_texts = split_translation_to_lines(line_orig_texts, b_tr)
                    else:
                        line_tr_texts = [b_tr]

                    for line_item, line_tr in zip(constituent_lines, line_tr_texts):
                        l_text = line_item.get("text", "").strip()
                        l_tr = line_tr.strip()
                        if not l_tr:
                            continue

                        # Физические координаты для вырезки в frame_bgr с безопасным паддингом
                        crop_x = int(max(0, min(f_w - 2, line_item.get("x", 0))))
                        crop_y = int(max(0, min(f_h - 2, line_item.get("y", 0))))
                        crop_w = int(max(10, min(f_w - crop_x, line_item.get("width", 50))))
                        crop_h = int(max(10, min(f_h - crop_y, line_item.get("height", 20))))

                        pad = 4
                        px = max(0, crop_x - pad)
                        py = max(0, crop_y - pad)
                        pw = min(f_w - px, crop_w + pad * 2)
                        ph = min(f_h - py, crop_h + pad * 2)
                        crop = frame_bgr[py:py+ph, px:px+pw]
                        visual = extract_visual_props(crop)

                        # Логические экранные координаты виджета (DPI-aware) для ЭТОЙ строки
                        bx = float(line_item.get("x", 0)) * to_log_x
                        by = float(line_item.get("y", 0)) * to_log_y
                        bw = float(line_item.get("width", 50)) * to_log_x
                        bh = float(line_item.get("height", 20)) * to_log_y

                        raw_word_h = float(line_item.get("word_height", line_item.get("height", 20)))
                        lh = max(bh, raw_word_h * to_log_y)

                        translated_blocks.append({
                            "original": l_text,
                            "translated": l_tr,
                            "x": bx,
                            "y": by,
                            "width": bw,
                            "height": bh,
                            "lines_count": 1,
                            "line_height": lh,
                            "color_rgb": visual["color_rgb"],
                            "bg_color_rgb": visual.get("bg_color_rgb", (15, 23, 42)),
                            "is_bold": visual["is_bold"],
                            "is_italic": visual.get("is_italic", False),
                            "font_family": visual["font_family"]
                        })

                translated_full = " ".join(item["translated"] for item in translated_blocks) if translated_blocks else restore_punctuation(full_text, translate_text(full_text, source_lang=src, target_lang=tgt))
                if not translated_blocks and full_text and translated_full:
                    lines = [ln for ln in translated_full.split("\n") if ln.strip()]
                    lines_cnt = max(1, len(lines))
                    translated_blocks.append({
                        "original": full_text,
                        "translated": translated_full,
                        "x": 20.0,
                        "y": max(10.0, min(float(rh) * 0.35, 60.0)),
                        "width": min(float(rw) - 40.0, max(140.0, float(rw) * 0.75)),
                        "height": float(lines_cnt * 24.0),
                        "lines_count": lines_cnt,
                        "line_height": 22.0,
                        "color_rgb": (248, 250, 252),
                        "bg_color_rgb": (15, 23, 42),
                        "is_bold": True,
                        "is_italic": False,
                        "font_family": "Segoe UI"
                    })
                with QMutexLocker(self._mutex):
                    if self._paused or self._menu_open or self._tooltip_active or (time.time() < self._menu_cooldown_until):
                        continue
                self.translation_ready.emit(full_text, translated_full, translated_blocks)

                self.msleep(interval)
            except Exception as e:
                try:
                    print(f"[TranslationScannerWorker] Error in scan loop: {e}")
                except Exception:
                    pass
                self.msleep(interval)


class EyeUnlockPill(QPushButton):
    """
    Автономная миниатюрная плавающая кнопка разблокировки глазика.
    Сделана сверхкомпактной (16x16 px, ровно в 2 раза меньше обычной 28x28 px).
    Свободно перемещается по экрану как ЛЕВОЙ, так и ПРАВОЙ кнопкой мыши.
    Одиночный клик левой кнопкой (без перемещения) возвращает панель управления.
    Одиночный клик правой кнопкой (без перемещения) отключает/включает режим перевода (зачеркнутый глазик).
    """
    def __init__(self, target_window: TranslationFrameWindow):
        super().__init__()
        self.target_window = target_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(16, 16)
        self.setIconSize(QSize(10, 10))
        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.update_state()

        self._drag_start = QPoint()
        self._start_pos = QPoint()
        self._has_dragged = False
        self._user_moved = False

    def update_state(self):
        """Обновляет иконку и стиль в зависимости от активности функции перевода."""
        is_paused = getattr(self.target_window, "is_paused", False)
        if is_paused:
            self.setIcon(create_themed_icon("eye_off", is_dark=True, size=10, custom_color="#f87171"))
            self.setToolTip(tr("trans_unlock_disabled_tooltip", "Перевод отключен. Кликните ПКМ для включения, ЛКМ для возврата настроек, зажмите для перемещения."))
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(15, 23, 42, 245);
                    border: 1px solid rgba(248, 113, 113, 230);
                    border-radius: 3px;
                    padding: 0px;
                    margin: 0px;
                }
                QPushButton:hover {
                    background-color: #991b1b;
                    border-color: #fca5a5;
                }
            """)
        else:
            self.setIcon(create_themed_icon("eye", is_dark=True, size=10, custom_color="#38bdf8"))
            self.setToolTip(tr("trans_unlock_tooltip", "Перетащите ЛКМ/ПКМ в любое место. Кликните ЛКМ для возврата настроек, ПКМ для отключения перевода."))
            self.setStyleSheet("""
                QPushButton {
                    background-color: rgba(15, 23, 42, 235);
                    border: 1px solid rgba(56, 189, 248, 220);
                    border-radius: 3px;
                    padding: 0px;
                    margin: 0px;
                }
                QPushButton:hover {
                    background-color: #0284c7;
                    border-color: #7dd3fc;
                }
            """)

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.target_window and hasattr(self.target_window, "worker"):
            self.target_window.worker.set_tooltip_active(True)
        tip = self.toolTip()
        if tip:
            show_stealth_tooltip(QCursor.pos(), tip, self)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        QToolTip.hideText()
        if self.target_window and hasattr(self.target_window, "worker"):
            self.target_window.worker.set_tooltip_active(False)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self._drag_start = event.globalPosition().toPoint()
            self._start_pos = self.pos()
            self._has_dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if event.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton):
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() >= 3:
                self._has_dragged = True
                self._user_moved = True
                new_pos = self._start_pos + delta
                screen = QGuiApplication.screenAt(new_pos) or (self.target_window.screen() if self.target_window else None) or QGuiApplication.primaryScreen()
                screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
                pill_w = self.width() if self.width() > 0 else 16
                pill_h = self.height() if self.height() > 0 else 16
                px = max(screen_geo.left() + 2, min(new_pos.x(), screen_geo.right() - pill_w - 2))
                py = max(screen_geo.top() + 2, min(new_pos.y(), screen_geo.bottom() - pill_h - 2))
                self.move(px, py)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._has_dragged:
                self._on_clicked()
            event.accept()
            return
        elif event.button() == Qt.MouseButton.RightButton:
            if not self._has_dragged:
                self._on_right_clicked()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_clicked(self):
        self.hide()
        if self.target_window:
            self.target_window.set_stealth_lock(False)

    def _on_right_clicked(self):
        """Отключает или возобновляет функцию перевода с отображением перечеркнутого глазика."""
        if self.target_window:
            self.target_window._toggle_pause()

    def update_position(self, force: bool = False):
        if not self.target_window:
            return
        screen = self.target_window.screen()
        if not screen:
            screen = QGuiApplication.screenAt(self.target_window.geometry().center())
        if not screen:
            screen = QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        pill_w = self.width() if self.width() > 0 else 16
        pill_h = self.height() if self.height() > 0 else 16

        if getattr(self, "_user_moved", False) and not force:
            cur_x = self.x()
            cur_y = self.y()
            px = max(screen_geo.left() + 2, min(cur_x, screen_geo.right() - pill_w - 2))
            py = max(screen_geo.top() + 2, min(cur_y, screen_geo.bottom() - pill_h - 2))
            if px != cur_x or py != cur_y:
                self.move(px, py)
            return

        geo = self.target_window.geometry()
        # Всегда размещаем ВНУТРИ рамки в правом верхнем углу
        px = geo.right() - pill_w - 8
        py = geo.top() + 8

        # Гарантируем, что кнопка глазика останется в пределах экрана
        if px + pill_w > screen_geo.right() - 4:
            px = screen_geo.right() - pill_w - 4
        if px < screen_geo.left() + 4:
            px = screen_geo.left() + 4

        if py + pill_h > screen_geo.bottom() - 4:
            py = screen_geo.bottom() - pill_h - 4
        if py < screen_geo.top() + 4:
            py = screen_geo.top() + 4

        self.move(px, py)

    def raise_to_topmost(self):
        if not self.isVisible():
            return
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            except Exception:
                pass
        self.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_state()
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
                style = ctypes.windll.user32.GetWindowLongW(wid, -20)
                ctypes.windll.user32.SetWindowLongW(wid, -20, style | 0x08000000)
            except Exception:
                pass


class TranslationHudWindow(QWidget):
    """
    Автономное плавающее окно HUD-субтитров живого перевода.
    Размещается поверх экрана, свободно перемещается мышью по всему экрану (на любой монитор),
    не обрезается границами рамки захвата, не перехватывает фокус (WS_EX_NOACTIVATE).
    """
    def __init__(self, frame_window: TranslationFrameWindow):
        super().__init__()
        self.frame_window = frame_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMinimumSize(220, 48)
        self.resize(460, 84)

        self._user_moved = False
        self._is_dragging = False
        self._drag_start = QPoint()
        self._win_start_pos = QPoint()

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        self.hud_frame = QFrame(self)
        hud_layout = QVBoxLayout(self.hud_frame)
        hud_layout.setContentsMargins(12, 8, 12, 8)

        self.lbl_hud_text = QLabel(tr("trans_waiting_text", "Ожидание текста в рамке..."), self.hud_frame)
        self.lbl_hud_text.setWordWrap(True)
        self.lbl_hud_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        hud_layout.addWidget(self.lbl_hud_text)

        main_layout.addWidget(self.hud_frame)

        self.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.setToolTip(tr("trans_hud_move_tooltip", "Субтитры перевода. Зажмите ЛКМ для свободного перемещения по всему экрану."))

        self.sync_style()
        self.sync_to_frame()

    def sync_to_frame(self):
        """Синхронизирует начальное положение под рамкой перевода, если пользователь еще не перемещал HUD вручную."""
        if self._user_moved or not self.frame_window:
            return
        geo = self.frame_window.geometry()
        w = max(340, min(640, geo.width()))
        h = max(50, self.height())
        x = geo.x() + (geo.width() - w) // 2

        screen = self.frame_window.screen()
        if not screen:
            screen = QGuiApplication.screenAt(geo.center())
        if not screen:
            screen = QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        # Если снизу рамки достаточно места до границы экрана
        if geo.bottom() + 8 + h <= screen_geo.bottom() - 10:
            y = geo.bottom() + 8
        elif geo.top() - h - 8 >= screen_geo.top() + 10:
            # Места снизу нет, но есть сверху
            y = geo.top() - h - 8
        else:
            # Полный экран или рамка вплотную к краям: субтитры внутри рамки внизу
            y = max(screen_geo.top() + 10, geo.bottom() - h - 30)

        # Ограничиваем X границами экрана
        if x + w > screen_geo.right():
            x = screen_geo.right() - w
        if x < screen_geo.left():
            x = screen_geo.left()

        self.setGeometry(x, y, w, h)

    def sync_style(self):
        if not self.frame_window:
            return
        r, g, b = self.frame_window.THEME_COLORS.get(self.frame_window.bg_theme, (15, 23, 42))
        alpha = int(self.frame_window.bg_opacity * 255)
        font_px = self.frame_window.hud_font_size if self.frame_window.hud_font_size > 0 else 13

        if self.frame_window.bg_opacity <= 0.05:
            self.hud_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: transparent;
                    border: none;
                }}
                QLabel {{
                    color: #ffffff;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: {font_px}px;
                    font-weight: 600;
                    line-height: 1.35;
                }}
            """)
        else:
            self.hud_frame.setStyleSheet(f"""
                QFrame {{
                    background-color: rgba({r}, {g}, {b}, {alpha});
                    border: 1px solid rgba(59, 130, 246, {min(220, alpha + 40)});
                    border-radius: 8px;
                }}
                QLabel {{
                    color: #ffffff;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: {font_px}px;
                    font-weight: 500;
                    line-height: 1.35;
                }}
            """)

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start = event.globalPosition().toPoint()
            self._win_start_pos = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_start
            if delta.manhattanLength() >= 2:
                self._user_moved = True
                self.move(self._win_start_pos + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
                style = ctypes.windll.user32.GetWindowLongW(wid, -20)
                ctypes.windll.user32.SetWindowLongW(wid, -20, style | 0x08000000)
            except Exception:
                pass

    def raise_to_topmost(self):
        if not self.isVisible():
            return
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            except Exception:
                pass
        self.raise_()


class TranslationControlBar(QWidget):
    """
    Автономная внешняя панель управления рамки перевода.
    Размещается полностью СНАРУЖИ (сверху) над рамкой перевода.
    Содержит все кнопки и настройки, перемещается вместе с рамкой,
    имеет нормальные курсоры-указатели (без стрелок изменения размера).
    """
    def __init__(self, frame_window: TranslationFrameWindow):
        super().__init__()
        self.frame_window = frame_window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedHeight(32)

        self._is_dragging = False
        self._drag_start = QPoint()
        self._bar_start_pos = QPoint()
        self._frame_start_pos = QPoint()

        self._setup_ui()

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
                style = ctypes.windll.user32.GetWindowLongW(wid, -20)
                ctypes.windll.user32.SetWindowLongW(wid, -20, style | 0x08000000)
            except Exception:
                pass

    def raise_to_topmost(self):
        """Гарантирует удержание панели управления поверх рамки перевода и окон других приложений."""
        if not self.isVisible():
            return
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            except Exception:
                pass
        self.raise_()

    def enterEvent(self, event):
        super().enterEvent(event)
        if self.frame_window and hasattr(self.frame_window, "worker"):
            self.frame_window.worker.set_tooltip_active(True)

    def _show_banner_tooltip(self, obj: QWidget, tip: str):
        if not tip or not hasattr(self, "tooltip_banner"):
            return
        clean_tip = tip.replace("\n", " — ").strip()
        fm = QFontMetrics(self.tooltip_banner.font())
        text_w = fm.horizontalAdvance(clean_tip) + 16
        bar_w = self.width()
        banner_h = 22
        banner_y = (self.height() - banner_h) // 2
        # Компактный размер: не более 170 px и не более 35% панели, чтобы не перегружать интерфейс
        max_w = min(170, max(70, int(bar_w * 0.35)))
        banner_w = min(text_w, max_w)
        if text_w > max_w:
            elided = fm.elidedText(clean_tip, Qt.TextElideMode.ElideRight, banner_w - 12)
            self.tooltip_banner.setText(elided)
        else:
            self.tooltip_banner.setText(clean_tip)

        obj_pos = obj.mapTo(self, QPoint(0, 0))
        obj_center_x = obj_pos.x() + obj.width() // 2
        if obj_center_x > bar_w // 2:
            banner_x = 6
        else:
            banner_x = max(6, bar_w - banner_w - 6)

        self.tooltip_banner.setGeometry(banner_x, banner_y, banner_w, banner_h)
        self.tooltip_banner.show()
        self.tooltip_banner.raise_()

    def _hide_banner_tooltip(self):
        if hasattr(self, "tooltip_banner") and self.tooltip_banner.isVisible():
            self.tooltip_banner.hide()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.ToolTip):
            if isinstance(obj, QWidget):
                tip = obj.toolTip()
                if tip:
                    self._show_banner_tooltip(obj, tip)
                    if self.frame_window and hasattr(self.frame_window, "worker"):
                        self.frame_window.worker.set_tooltip_active(True)
                    if event.type() == QEvent.Type.ToolTip:
                        return True
        elif event.type() == QEvent.Type.Leave:
            if not self.rect().contains(self.mapFromGlobal(QCursor.pos())):
                self._hide_banner_tooltip()
                if self.frame_window and hasattr(self.frame_window, "worker"):
                    self.frame_window.worker.set_tooltip_active(False)
            else:
                under = QApplication.widgetAt(QCursor.pos())
                w = under
                has_tip = False
                while w and w != self:
                    if getattr(w, "toolTip", lambda: "")():
                        has_tip = True
                        break
                    w = w.parent()
                if not has_tip:
                    self._hide_banner_tooltip()
        return super().eventFilter(obj, event)

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._hide_banner_tooltip()
        if self.frame_window and hasattr(self.frame_window, "worker"):
            self.frame_window.worker.set_tooltip_active(False)

    def sync_to_frame(self):
        if not self.frame_window:
            return
        self._hide_banner_tooltip()
        geo = self.frame_window.geometry()
        screen = self.frame_window.screen()
        if not screen:
            screen = QGuiApplication.screenAt(geo.center())
        if not screen:
            screen = QGuiApplication.primaryScreen()
        screen_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)

        bar_h = 32
        actual_min_w = max(self.layout().minimumSize().width(), self.sizeHint().width()) if self.layout() else 440
        bar_w = min(max(actual_min_w, min(geo.width(), 640)), screen_geo.width())

        space_above = geo.top() - screen_geo.top()
        space_below = screen_geo.bottom() - geo.bottom()

        if space_above >= bar_h + 4:
            # Сверху снаружи достаточно места
            bar_y = geo.top() - bar_h - 4
            bar_x = geo.x() + (geo.width() - bar_w) // 2
        elif space_below >= bar_h + 4:
            # Снизу снаружи достаточно места
            bar_y = geo.bottom() + 4
            bar_x = geo.x() + (geo.width() - bar_w) // 2
        else:
            # Полный экран или рамка вплотную к верхнему и нижнему краю
            # Размещаем ВНУТРИ рамки сверху с небольшим отступом
            bar_y = geo.top() + 8
            bar_x = geo.x() + (geo.width() - bar_w) // 2

        # Ограничиваем координаты границами экрана
        if bar_x + bar_w - 1 > screen_geo.right():
            bar_x = screen_geo.right() - bar_w + 1
        if bar_x < screen_geo.left():
            bar_x = screen_geo.left()

        if bar_y + bar_h - 1 > screen_geo.bottom():
            bar_y = screen_geo.bottom() - bar_h + 1
        if bar_y < screen_geo.top():
            bar_y = screen_geo.top()

        self.setGeometry(bar_x, bar_y, bar_w, bar_h)
        real_geo = self.geometry()
        if real_geo.right() > screen_geo.right():
            self.move(max(screen_geo.left(), screen_geo.right() - real_geo.width() + 1), real_geo.y())

    def _menu_stylesheet(self) -> str:
        return """
            QMenu {
                background-color: #18181b;
                border: 1px solid #3f3f46;
                border-radius: 6px;
                padding: 4px;
            }
            QMenu::item {
                color: #f4f4f5;
                padding: 5px 14px;
                border-radius: 4px;
                font-size: 11px;
                font-family: 'Segoe UI', sans-serif;
            }
            QMenu::item:selected {
                background-color: #2563eb;
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background-color: #27272a;
                margin: 4px 6px;
            }
        """

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.inner_frame = QFrame(self)
        self.inner_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(18, 20, 28, 245);
                border: 1px solid rgba(59, 130, 246, 180);
                border-radius: 6px;
            }
            QLabel {
                color: #f4f4f5;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton {
                background-color: rgba(39, 39, 42, 210);
                color: #e4e4e7;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 500;
                padding: 3px 7px;
            }
            QPushButton:hover {
                background-color: #2563eb;
                color: #ffffff;
                border-color: #60a5fa;
            }
        """)

        inner_layout = QHBoxLayout(self.inner_frame)
        inner_layout.setContentsMargins(6, 3, 6, 3)
        inner_layout.setSpacing(6)

        # 1. Заголовок и маркер перемещения всей системы
        self.title_container = QWidget()
        title_box = QHBoxLayout(self.title_container)
        title_box.setContentsMargins(5, 2, 5, 2)
        title_box.setSpacing(4)
        self.title_container.setStyleSheet("background-color: rgba(30, 41, 59, 160); border-radius: 4px;")
        self.title_container.setCursor(QCursor(Qt.CursorShape.SizeAllCursor))
        self.title_container.setToolTip(tr("trans_drag_tooltip", "Перемещение"))

        self.drag_grip = QLabel(self)
        self.drag_grip.setPixmap(get_svg_pixmap("move", color="#94a3b8", size=13))
        self.drag_grip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_box.addWidget(self.drag_grip)

        self.lbl_icon = QLabel(self)
        self.lbl_icon.setPixmap(get_svg_pixmap("translate", color="#60a5fa", size=14))
        self.lbl_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_box.addWidget(self.lbl_icon)

        self.lbl_title = QLabel("")  # Сохраняем атрибут для совместимости
        inner_layout.addWidget(self.title_container)

        # 2. Кнопка выбора языков
        self.btn_lang = QPushButton()
        self.btn_lang.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_lang.setMaximumWidth(160)
        self._update_lang_button_text()
        self.btn_lang.setToolTip(tr("trans_lang_tooltip", "Выбор языков"))
        self.btn_lang.clicked.connect(self._show_lang_menu)
        inner_layout.addWidget(self.btn_lang)

        # 3. Кнопка выбора режима (Субтитры / Поверх текста)
        self.btn_mode = QPushButton()
        self.btn_mode.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_mode.setMaximumWidth(170)
        self._update_mode_button_text()
        self.btn_mode.setToolTip(tr("trans_mode_tooltip", "Режим перевода"))
        self.btn_mode.clicked.connect(self._show_mode_menu)
        inner_layout.addWidget(self.btn_mode)

        # 4. Кнопка привязки к окну / процессу
        self.btn_pin = QPushButton()
        self.btn_pin.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_pin.setFixedSize(26, 24)
        self.btn_pin.clicked.connect(self._show_pin_menu)
        inner_layout.addWidget(self.btn_pin)
        self.update_pinned_ui()

        # 5. Кнопка переключения неосязаемости (Passthrough / Edit mode)
        self.btn_passthrough = QPushButton()
        self.btn_passthrough.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_passthrough.setFixedSize(26, 24)
        self.btn_passthrough.clicked.connect(self._toggle_passthrough)
        inner_layout.addWidget(self.btn_passthrough)
        self.update_passthrough_ui()

        # 6. Кнопка расширенных настроек (только иконка шестерёнки)
        self.btn_settings = QPushButton()
        self.btn_settings.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_settings.setIcon(create_themed_icon("settings", is_dark=True, size=13))
        self.btn_settings.setToolTip(tr("trans_settings_tooltip", "Настройки"))
        self.btn_settings.setFixedSize(26, 24)
        self.btn_settings.clicked.connect(self._show_settings_menu)
        inner_layout.addWidget(self.btn_settings)

        # 7. Пауза / Пуск
        self.btn_pause = QPushButton()
        self.btn_pause.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=13))
        self.btn_pause.setToolTip(tr("trans_pause_tip", "Пауза"))
        self.btn_pause.setFixedSize(26, 24)
        self.btn_pause.clicked.connect(self.frame_window._toggle_pause)
        inner_layout.addWidget(self.btn_pause)

        # 8. Копировать перевод
        self.btn_copy = QPushButton()
        self.btn_copy.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_copy.setIcon(create_themed_icon("copy", is_dark=True, size=13))
        self.btn_copy.setToolTip(tr("trans_copy_menu_tooltip", "Копировать"))
        self.btn_copy.setFixedSize(26, 24)
        self.btn_copy.clicked.connect(self._show_copy_menu)
        inner_layout.addWidget(self.btn_copy)

        # 9. Кнопка «Глазик» — переход в скрытый режим
        self.btn_eye = QPushButton()
        self.btn_eye.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_eye.setIcon(create_themed_icon("eye", is_dark=True, size=14, custom_color="#38bdf8"))
        self.btn_eye.setToolTip(tr("trans_eye_tooltip", "Скрытый режим"))
        self.btn_eye.setFixedSize(26, 24)
        self.btn_eye.setStyleSheet("QPushButton { border-color: rgba(56, 189, 248, 140); } QPushButton:hover { background-color: #0284c7; }")
        self.btn_eye.clicked.connect(lambda: self.frame_window.set_stealth_lock(True))
        inner_layout.addWidget(self.btn_eye)

        # 10. Кнопка «+» — создать еще одну рамку перевода
        self.btn_add_frame = QPushButton()
        self.btn_add_frame.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_add_frame.setIcon(create_themed_icon("plus", is_dark=True, size=13, custom_color="#38bdf8"))
        self.btn_add_frame.setToolTip(tr("trans_add_frame_tip", "Добавить еще одну рамку перевода"))
        self.btn_add_frame.setFixedSize(26, 24)
        self.btn_add_frame.setStyleSheet("QPushButton { border-color: rgba(56, 189, 248, 140); } QPushButton:hover { background-color: #0284c7; }")
        self.btn_add_frame.clicked.connect(lambda: self.frame_window.spawn_another_frame())
        inner_layout.addWidget(self.btn_add_frame)

        # 11. Закрыть
        self.btn_close = QPushButton()
        self.btn_close.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_close.setIcon(create_themed_icon("close", is_dark=True, size=13))
        self.btn_close.setToolTip(tr("trans_close_tooltip", "Закрыть"))
        self.btn_close.setFixedSize(24, 24)
        self.btn_close.setStyleSheet("QPushButton:hover { background-color: #ef4444; border-color: #f87171; }")
        self.btn_close.clicked.connect(self.frame_window.close)
        inner_layout.addWidget(self.btn_close)

        for w in (
            self.title_container, self.btn_lang, self.btn_mode, self.btn_pin,
            self.btn_passthrough, self.btn_settings, self.btn_pause, self.btn_copy,
            self.btn_eye, self.btn_add_frame, self.btn_close
        ):
            w.installEventFilter(self)

        layout.addWidget(self.inner_frame)

        self.tooltip_banner = QLabel(self)
        self.tooltip_banner.setStyleSheet("""
            QLabel {
                background-color: rgba(15, 23, 42, 250);
                color: #f8fafc;
                border: 1px solid rgba(56, 189, 248, 220);
                border-radius: 4px;
                padding: 0px 6px;
                font-family: 'Segoe UI', sans-serif;
                font-size: 11px;
                font-weight: 500;
            }
        """)
        self.tooltip_banner.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.tooltip_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tooltip_banner.hide()

    def _toggle_passthrough(self):
        new_state = not getattr(self.frame_window, "passthrough_enabled", True)
        self.frame_window.set_passthrough(new_state)
        msg = tr("trans_pass_on_tip", "Сквозной клик (в игре)") if new_state else tr("trans_pass_off_tip", "Режим настройки")
        self._show_banner_tooltip(self.btn_passthrough, msg)
        QTimer.singleShot(2500, self._hide_banner_tooltip)
        if hasattr(self.frame_window, "worker"):
            self.frame_window.worker.set_tooltip_active(True)

    def update_passthrough_ui(self):
        if not hasattr(self, "btn_passthrough"):
            return
        is_pass = getattr(self.frame_window, "passthrough_enabled", True)
        if is_pass:
            self.btn_passthrough.setIcon(create_themed_icon("passthrough", is_dark=True, size=14, custom_color="#38bdf8"))
            self.btn_passthrough.setToolTip(tr("trans_pass_on_tip", "Сквозной клик (в игре)"))
            self.btn_passthrough.setStyleSheet("QPushButton { border-color: rgba(56, 189, 248, 160); background-color: rgba(14, 165, 233, 40); }")
        else:
            self.btn_passthrough.setIcon(create_themed_icon("maximize_2", is_dark=True, size=13, custom_color="#eab308"))
            self.btn_passthrough.setToolTip(tr("trans_pass_off_tip", "Режим настройки"))
            self.btn_passthrough.setStyleSheet("QPushButton { border-color: #eab308; background-color: rgba(234, 179, 8, 40); }")

    def update_pinned_ui(self):
        if not hasattr(self, "btn_pin"):
            return
        pinned = getattr(self.frame_window, "pinned_app", None)
        if pinned:
            pname = pinned.get("process_name", "")
            title = pinned.get("title", "")
            short_t = (title[:24] + "...") if len(title) > 24 else title
            self.btn_pin.setIcon(create_themed_icon("pin", is_dark=True, size=13, custom_color="#38bdf8"))
            tip = tr("trans_pin_active_tip", "Привязано: {pname}").format(pname=pname)
            self.btn_pin.setToolTip(tip)
            self.btn_pin.setStyleSheet("QPushButton { border-color: rgba(56, 189, 248, 160); background-color: rgba(14, 165, 233, 40); }")
        else:
            self.btn_pin.setIcon(create_themed_icon("pin", is_dark=True, size=13, custom_color="#94a3b8"))
            self.btn_pin.setToolTip(tr("trans_pin_tooltip", "Привязать к окну"))
            self.btn_pin.setStyleSheet("")

    def _populate_pin_menu(self, menu: QMenu):
        menu.setStyleSheet(self._menu_stylesheet())

        act_all = menu.addAction(create_themed_icon("dynamic_bg", is_dark=True, size=16), tr("trans_pin_all", "Все приложения (всегда активно)"))
        act_all.setCheckable(True)
        act_all.setChecked(self.frame_window.pinned_app is None)
        act_all.triggered.connect(lambda: self.frame_window.set_pinned_app(None))

        c_app = getattr(self.frame_window, "creation_app", None)
        if c_app:
            pname = c_app.get("process_name", "")
            title = c_app.get("title", "")
            short_title = (title[:28] + "...") if len(title) > 28 else title
            lbl = tr("trans_pin_current", "Активное окно: {pname} — {title}").format(pname=pname, title=short_title)
            ico = get_window_qicon(c_app.get("hwnd", 0), size=16)
            act_cur = menu.addAction(ico, lbl)
            act_cur.setCheckable(True)
            is_cur_pinned = False
            if self.frame_window.pinned_app:
                is_cur_pinned = (
                    self.frame_window.pinned_app.get("pid") == c_app.get("pid") or
                    self.frame_window.pinned_app.get("process_name", "").lower() == pname.lower()
                )
            act_cur.setChecked(is_cur_pinned)
            act_cur.triggered.connect(lambda ch, app=c_app: self.frame_window.set_pinned_app(app))

        menu.addSeparator()

        gui_apps = get_running_gui_windows()
        if gui_apps:
            apps_sub = create_stealth_menu(menu)
            apps_sub.setTitle(tr("trans_pin_running_title", "Запущенные окна и процессы:"))
            apps_sub.setIcon(create_themed_icon("window", is_dark=True, size=14))
            apps_sub.setStyleSheet(self._menu_stylesheet())
            for app in gui_apps:
                pname = app["process_name"]
                t = app["title"]
                display = f"{pname} — {t[:28]}..." if len(t) > 28 else f"{pname} — {t}"
                app_ico = get_window_qicon(app.get("hwnd", 0), size=16)
                act_app = apps_sub.addAction(app_ico, display)
                act_app.setCheckable(True)
                is_pinned = False
                if self.frame_window.pinned_app:
                    if self.frame_window.pinned_app.get("pid") == app.get("pid"):
                        is_pinned = True
                    elif self.frame_window.pinned_app.get("process_name", "").lower() == pname.lower():
                        is_pinned = True
                act_app.setChecked(is_pinned)
                act_app.triggered.connect(lambda ch, a=app: self.frame_window.set_pinned_app(a))
            menu.addMenu(apps_sub)

    def _exec_menu_safe(self, menu: QMenu, button: QWidget):
        """Безопасно отображает всплывающее меню с аппаратной защитой и кулдауном для полного исчезновения."""
        self._hide_banner_tooltip()
        was_paused = getattr(self.frame_window, "is_paused", False)
        worker = getattr(self.frame_window, "worker", None)
        if worker:
            worker.set_menu_open(True)
            worker.set_paused(True)
        if self.frame_window:
            self.frame_window._menu_open = True

        try:
            menu.exec(button.mapToGlobal(QPoint(0, button.height() + 2)))
        finally:
            if self.frame_window:
                self.frame_window._menu_open = False
                self.frame_window._menu_closed_time = time.time()
            if worker:
                worker.set_menu_open(False)
                worker.set_paused(was_paused)
                worker.reset_cache()
            if self.frame_window:
                self.frame_window.raise_to_topmost()
            self.raise_to_topmost()

    def _show_pin_menu(self):
        menu = create_stealth_menu(self)
        self._populate_pin_menu(menu)
        self._exec_menu_safe(menu, self.btn_pin)

    def _show_copy_menu(self):
        menu = create_stealth_menu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        # 1. Текст перевода
        act_text = menu.addAction(create_themed_icon("copy", is_dark=True, size=14), tr("trans_copy_text", "Копировать текст перевода"))
        act_text.triggered.connect(self.frame_window._copy_translation_text)

        # 2. Изображение в буфер
        act_img = menu.addAction(create_themed_icon("crop", is_dark=True, size=14), tr("trans_copy_image", "Копировать изображение с переводом в буфер"))
        act_img.triggered.connect(self.frame_window._copy_translation_image)

        menu.addSeparator()

        # 3. Сохранить изображение как файл
        act_save = menu.addAction(create_themed_icon("save", is_dark=True, size=14), tr("trans_save_image", "Сохранить изображение с переводом..."))
        act_save.triggered.connect(self.frame_window._save_translation_image)

        self._exec_menu_safe(menu, self.btn_copy)

    def _update_lang_button_text(self):
        s = self.frame_window.src_lang.upper() if self.frame_window.src_lang != "auto" else tr("lang_auto", "Auto")
        t = self.frame_window.tgt_lang.upper()
        self.btn_lang.setText(tr("trans_lang_btn", "{src} → {tgt} ▾").format(src=s, tgt=t))

    def _update_mode_button_text(self):
        if self.frame_window.current_mode == "inplace":
            self.btn_mode.setText(tr("trans_mode_btn", "{mode} ▾").format(mode=tr("trans_mode_inplace", "Поверх текста")))
            self.btn_mode.setIcon(create_themed_icon("scan_text", is_dark=True, size=13))
        else:
            self.btn_mode.setText(tr("trans_mode_btn", "{mode} ▾").format(mode=tr("trans_mode_hud", "Субтитры")))
            self.btn_mode.setIcon(create_themed_icon("text", is_dark=True, size=13))

    def _show_lang_menu(self):
        menu = create_stealth_menu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        pairs = [
            ("Auto → RU", "auto", "ru", "Auto → Русский"),
            ("EN → RU", "en", "ru", "English → Русский"),
            ("JA → RU", "ja", "ru", "Japanese → Русский"),
            ("ZH → RU", "zh-CN", "ru", "Chinese → Русский"),
            ("DE → RU", "de", "ru", "German → Русский"),
            ("FR → RU", "fr", "ru", "French → Русский"),
            ("RU → EN", "ru", "en", "Русский → English"),
        ]
        for _, s, t, label in pairs:
            act = menu.addAction(label)
            act.triggered.connect(lambda ch, src=s, tgt=t: self.frame_window._set_languages(src, tgt))

        menu.addSeparator()

        src_menu = create_stealth_menu(menu)
        src_menu.setTitle(tr("trans_menu_src", "Исходный язык"))
        src_menu.setStyleSheet(self._menu_stylesheet())
        all_src = [("auto", "Auto"), ("en", "English"), ("ja", "Japanese"), ("zh-CN", "Chinese"),
                   ("de", "German"), ("fr", "French"), ("es", "Spanish"), ("ko", "Korean"), ("ru", "Русский")]
        for tag, name in all_src:
            act = src_menu.addAction(name)
            act.triggered.connect(lambda ch, s=tag: self.frame_window._set_languages(s, self.frame_window.tgt_lang))
        menu.addMenu(src_menu)

        tgt_menu = create_stealth_menu(menu)
        tgt_menu.setTitle(tr("trans_menu_tgt", "Язык перевода"))
        tgt_menu.setStyleSheet(self._menu_stylesheet())
        all_tgt = [("ru", "Русский"), ("en", "English"), ("de", "Deutsch"), ("fr", "Français"),
                   ("es", "Español"), ("zh-CN", "中文"), ("ja", "日本語")]
        for tag, name in all_tgt:
            act = tgt_menu.addAction(name)
            act.triggered.connect(lambda ch, t=tag: self.frame_window._set_languages(self.frame_window.src_lang, t))
        menu.addMenu(tgt_menu)
        self._exec_menu_safe(menu, self.btn_lang)

    def _show_mode_menu(self):
        menu = create_stealth_menu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        act_hud = menu.addAction(tr("trans_mode_hud", "Субтитры (HUD внизу)"))
        act_hud.setIcon(create_themed_icon("text", is_dark=True, size=13))
        act_hud.triggered.connect(lambda: self.frame_window._set_display_mode("hud"))

        act_inplace = menu.addAction(tr("trans_mode_inplace", "Поверх текста (In-place)"))
        act_inplace.setIcon(create_themed_icon("scan_text", is_dark=True, size=13))
        act_inplace.triggered.connect(lambda: self.frame_window._set_display_mode("inplace"))

        self._exec_menu_safe(menu, self.btn_mode)

    def _show_settings_menu(self):
        menu = create_stealth_menu(self)
        menu.setStyleSheet(self._menu_stylesheet())

        # 1. Неосязаемая рамка (сквозные клики)
        act_pass = menu.addAction(tr("trans_opt_passthrough", "Неосязаемая рамка (клики сквозь рамку)"))
        act_pass.setIcon(create_themed_icon("passthrough", is_dark=True, size=13))
        act_pass.setCheckable(True)
        act_pass.setChecked(self.frame_window.passthrough_enabled)
        act_pass.setToolTip(tr("trans_opt_pass_tip", "Позволяет нажимать сквозь рамку прямо в игру или программу"))
        act_pass.triggered.connect(lambda ch: self.frame_window.set_passthrough(ch))

        menu.addSeparator()

        # 2. Повторение цвета оригинального текста
        act_color = menu.addAction(tr("trans_opt_match_color", "Повторять цвет текста оригинала"))
        act_color.setCheckable(True)
        act_color.setChecked(self.frame_window.match_text_color)
        act_color.setToolTip(tr("trans_opt_match_color_tip", "Окрашивать переведенные слова в цвета оригинала с экрана"))
        act_color.triggered.connect(self.frame_window._toggle_match_color)

        # 3. Повторение шрифта и жирности оригинала
        act_font = menu.addAction(tr("trans_opt_match_font", "Повторять шрифт и начертание оригинала"))
        act_font.setCheckable(True)
        act_font.setChecked(self.frame_window.match_font_family)
        act_font.setToolTip(tr("trans_opt_match_font_tip", "Подбирать жирность и гарнитуру шрифта, как в исходном тексте"))
        act_font.triggered.connect(self.frame_window._toggle_match_font)

        menu.addSeparator()

        # 4. Прозрачность фона
        op_menu = create_stealth_menu(menu)
        op_menu.setTitle(tr("trans_menu_opacity", "Прозрачность фона"))
        op_menu.setStyleSheet(self._menu_stylesheet())
        op_levels = [
            (1.0, tr("trans_op_100", "100% (Непрозрачный)")),
            (0.85, tr("trans_op_85", "85% (Оптимальный)")),
            (0.60, tr("trans_op_60", "60% (Полупрозрачный)")),
            (0.30, tr("trans_op_30", "30% (Слабый)")),
            (0.0, tr("trans_op_0", "0% (Без фона)")),
        ]
        for val, label in op_levels:
            act = op_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(abs(self.frame_window.bg_opacity - val) < 0.05)
            act.triggered.connect(lambda ch, v=val: self.frame_window._set_opacity(v))
        menu.addMenu(op_menu)

        # 5. Стиль / Цвет фона
        theme_menu = create_stealth_menu(menu)
        theme_menu.setTitle(tr("trans_menu_theme", "Цвет фона"))
        theme_menu.setStyleSheet(self._menu_stylesheet())
        themes = [
            ("slate", tr("trans_theme_slate", "Тёмный сланец (Slate)")),
            ("oled", tr("trans_theme_oled", "Глубокий чёрный (OLED)")),
            ("cyber", tr("trans_theme_cyber", "Кибер-синий (Cyber)")),
        ]
        for key, name in themes:
            act = theme_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(self.frame_window.bg_theme == key)
            act.triggered.connect(lambda ch, k=key: self.frame_window._set_theme(k))
        menu.addMenu(theme_menu)

        # 6. Размер шрифта субтитров
        font_menu = create_stealth_menu(menu)
        font_menu.setTitle(tr("trans_menu_font_size", "Размер шрифта субтитров"))
        font_menu.setStyleSheet(self._menu_stylesheet())
        fonts = [
            (0, tr("trans_font_auto", "Авто (по тексту)")),
            (11, tr("trans_font_small", "Мелкий (11 px)")),
            (14, tr("trans_font_medium", "Средний (14 px)")),
            (18, tr("trans_font_large", "Крупный (18 px)")),
            (22, tr("trans_font_xlarge", "Очень крупный (22 px)")),
        ]
        for sz, label in fonts:
            act = font_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.frame_window.hud_font_size == sz)
            act.triggered.connect(lambda ch, s=sz: self.frame_window._set_font_size(s))
        menu.addMenu(font_menu)

        menu.addSeparator()

        # 7. Скорость сканирования (FPS)
        fps_menu = create_stealth_menu(menu)
        fps_menu.setTitle(tr("trans_menu_fps", "Скорость сканирования"))
        fps_menu.setStyleSheet(self._menu_stylesheet())
        speeds = [
            (50, tr("trans_fps_extreme", "Экстремально (50 мс)")),
            (100, tr("trans_fps_ultra", "Ультра (100 мс)")),
            (150, tr("trans_fps_fast", "Быстро (150 мс)")),
            (300, tr("trans_fps_opt", "Оптимально (300 мс)")),
            (600, tr("trans_fps_eco", "Энергосбережение (600 мс)")),
        ]
        for ms, label in speeds:
            act = fps_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.frame_window.scan_interval == ms)
            act.triggered.connect(lambda ch, m=ms: self.frame_window._set_scan_interval(m))
        menu.addMenu(fps_menu)

        # 8. Привязка к приложению / окну
        pin_menu = create_stealth_menu(menu)
        pin_menu.setTitle(tr("trans_pin_btn", "Привязка к приложению"))
        pin_menu.setIcon(create_themed_icon("pin", is_dark=True, size=13))
        self._populate_pin_menu(pin_menu)
        menu.addMenu(pin_menu)

        # 9. Smart Diff (Оптимизация CPU)
        act_diff = menu.addAction(tr("trans_menu_smart_diff", "Умная пауза при статичном кадре"))
        act_diff.setCheckable(True)
        act_diff.setChecked(self.frame_window.smart_diff_enabled)
        act_diff.triggered.connect(self.frame_window._toggle_smart_diff)

        self._exec_menu_safe(menu, self.btn_settings)

    # ------------------ Перемещение всей связки (панель + рамка) ------------------

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._is_dragging = True
            self._drag_start = event.globalPosition().toPoint()
            self._bar_start_pos = self.pos()
            self._frame_start_pos = self.frame_window.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and (event.buttons() & Qt.MouseButton.LeftButton):
            delta = event.globalPosition().toPoint() - self._drag_start
            self.move(self._bar_start_pos + delta)
            self.frame_window.move(self._frame_start_pos + delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            was_dragging = self._is_dragging
            self._is_dragging = False
            if was_dragging and hasattr(self.frame_window, "worker") and self.frame_window.worker.isRunning():
                self.frame_window.worker.update_geometry(self.frame_window.geometry())
            event.accept()
            return
        super().mouseReleaseEvent(event)


class TranslationFrameWindow(QWidget):
    """
    Интерактивное окно рамки живого перевода (Capture Region).
    Поддерживает:
    - Область захвата 1:1 без лишних элементов внутри (панель управления находится снаружи).
    - Неосязаемый режим (Passthrough / WS_EX_TRANSPARENT) по умолчанию при переводе.
    - Переключение в режим изменения размера и перемещения по кнопке или настройкам.
    - Аппаратное исключение из захвата экрана (WDA_EXCLUDEFROMCAPTURE).
    - Динамический расчет размера шрифта, цвета слов и гарнитуры оригинала.
    - Адаптивный перенос и расчет многострочного текста без вылезания за границы.
    - Автономный перетаскиваемый значок глазика (ЛКМ / ПКМ).
    """
    closed = pyqtSignal()
    frame_closed = pyqtSignal()

    HANDLE_SIZE = 8
    HANDLE_NONE = 0
    HANDLE_TL = 1
    HANDLE_T = 2
    HANDLE_TR = 3
    HANDLE_R = 4
    HANDLE_BR = 5
    HANDLE_B = 6
    HANDLE_BL = 7
    HANDLE_L = 8

    THEME_COLORS = {
        "slate": (15, 23, 42),
        "oled": (0, 0, 0),
        "cyber": (10, 25, 47),
    }

    HEADER_OFFSET = 0
    HEADER_BAR_HEIGHT = 32

    def get_capture_rect(self) -> QRect:
        """Возвращает прямоугольник захвата (в новой архитектуре вся область рамки является захватом)."""
        return self.geometry()

    def __init__(self, initial_rect: QRect | None = None, parent=None, target_hwnd: int | None = None):
        super().__init__(parent)
        self.target_hwnd = target_hwnd
        self.creation_app = resolve_window_info(target_hwnd) if target_hwnd else None
        self.pinned_app: dict | None = None
        self._pinned_hidden: bool = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool |
            Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 100)

        if initial_rect is not None and initial_rect.isValid() and not initial_rect.isEmpty():
            w = max(240, initial_rect.width())
            h = max(100, initial_rect.height())
            x = initial_rect.x()
            y = initial_rect.y()
            screen = QApplication.screenAt(initial_rect.center())
        else:
            screen = QApplication.primaryScreen()
            avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1040)
            w, h = 520, 240
            x = avail_geo.x() + (avail_geo.width() - w) // 2
            y = avail_geo.y() + (avail_geo.height() - h) // 2

        if not screen:
            screen = QApplication.primaryScreen()
        avail_geo = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1040)

        # Ограничиваем геометрию строго в пределах рабочей области экрана (availableGeometry),
        # чтобы рамка никогда не перекрывала панель задач Windows (Taskbar)
        if avail_geo.isValid():
            w = min(w, avail_geo.width())
            h = min(h, avail_geo.height())
            if x + w > avail_geo.right() + 1:
                x = avail_geo.right() + 1 - w
            if x < avail_geo.left():
                x = avail_geo.left()
            if y + h > avail_geo.bottom() + 1:
                y = avail_geo.bottom() + 1 - h
            if y < avail_geo.top():
                y = avail_geo.top()

        self.setGeometry(x, y, w, h)

        # Конфигурация параметров оформления и работы
        self.current_mode = "inplace"  # По умолчанию режим "поверх текста" (inplace)
        self.src_lang = "auto"
        self.tgt_lang = "ru"
        self.is_paused = False
        self.is_locked_stealth = False  # Режим скрытия по кнопке глазика
        self.passthrough_enabled = False # По умолчанию рамка создаётся осязаемой и готовой к изменению размера

        self.bg_opacity = 0.85
        self.bg_theme = "slate"
        self.hud_font_size = 0   # 0 = Авто
        try:
            from config import ConfigManager
            cfg = ConfigManager.get_instance().config
            self.scan_interval = int(getattr(cfg, "live_translator_interval_ms", 300))
        except Exception:
            self.scan_interval = 300
        self.smart_diff_enabled = True

        self.match_text_color = True    # Соответствие цвета слов
        self.match_font_family = True   # Соответствие шрифта и начертания

        self.translated_text = ""
        self.original_text = ""
        self.translated_blocks = []

        # Состояния мыши для ресайза в режиме настройки
        self.active_handle = self.HANDLE_NONE
        self.is_resizing = False
        self.is_moving_window = False
        self.drag_start_pos = QPoint()
        self.initial_geometry = QRect()

        # Создаем автономное окно HUD для субтитров (перемещается по всему экрану)
        self._setup_hud_ui()

        # Внешняя автономная панель управления (снаружи над рамкой)
        self.control_bar = TranslationControlBar(self)
        self.header_frame = self.control_bar  # Для обратной совместимости с тестами
        self.control_bar.sync_to_frame()
        self.control_bar.show()

        # Автономная кнопка разблокировки глазика
        self.unlock_pill = EyeUnlockPill(self)

        # Рабочий поток OCR и перевода
        self.worker = TranslationScannerWorker(self)
        self.worker.set_interval(self.scan_interval)
        self.worker.set_mode(self.current_mode)
        self.worker.translation_ready.connect(self._on_translation_ready)
        self.worker.update_geometry(self.geometry())
        self.worker.start()

        # Применяем неосязаемость рамки
        self.set_passthrough(self.passthrough_enabled)

        # Регистрируем в глобальный список активных окон перевода
        app_inst = QApplication.instance()
        if app_inst:
            if not hasattr(app_inst, "_active_translation_windows"):
                app_inst._active_translation_windows = []
            if self not in app_inst._active_translation_windows:
                app_inst._active_translation_windows.append(self)

        # Автоматическое удержание поверх активных полноэкранных игр и окон при переключении Alt+Tab
        self._topmost_timer = None
        self._win_event_hook = None
        self._hook_proc = None
        self._smart_zorder_timer = None
        self._setup_zorder_sentinel()

    def _setup_zorder_sentinel(self):
        """
        Инициализирует отслеживание смены активного окна Windows (EVENT_SYSTEM_FOREGROUND = 0x0003)
        и умный монитор z-order, гарантируя удержание рамки поверх игр при переключении через Alt+Tab.
        """
        if sys.platform != "win32":
            return
        try:
            WINEVENTPROC = ctypes.WINFUNCTYPE(
                None,
                wintypes.HANDLE,
                wintypes.DWORD,
                wintypes.HWND,
                wintypes.LONG,
                wintypes.LONG,
                wintypes.DWORD,
                wintypes.DWORD
            )

            def _hook_callback(hHook, event, hwnd, idObject, idChild, dwEventThread, dwmsEventTime):
                if idObject == 0 and idChild == 0 and hwnd:
                    self._on_foreground_window_changed(int(hwnd))

            self._hook_proc = WINEVENTPROC(_hook_callback)
            self._win_event_hook = ctypes.windll.user32.SetWinEventHook(
                0x0003, 0x0003, 0, self._hook_proc, 0, 0, 0
            )
        except Exception:
            self._win_event_hook = None

        # Фоновый монитор z-order (срабатывает ТОЛЬКО если игра/приложение реально перекрыли рамку)
        self._smart_zorder_timer = QTimer(self)
        self._smart_zorder_timer.setInterval(750)
        self._smart_zorder_timer.timeout.connect(self._check_zorder_smart)
        self._smart_zorder_timer.start()

    def set_pinned_app(self, app_info: dict | None):
        """
        Устанавливает привязку рамки перевода к конкретному процессу или окну.
        app_info: {"hwnd": hwnd, "title": title, "pid": pid, "process_name": pname} или None.
        """
        self.pinned_app = app_info
        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.update_pinned_ui()
        self._check_pinned_visibility()

    def _check_pinned_visibility(self):
        """
        Проверяет соответствие активного окна в Windows привязанному приложению.
        Если активно привязанное приложение — рамка отображается и активна.
        Если пользователь свернул окно или переключился на другое — рамка скрывается и ставится на паузу.
        """
        if sys.platform != "win32":
            return
        if not self.pinned_app:
            if self._pinned_hidden:
                self._pinned_hidden = False
                self.show()
                if hasattr(self, "control_bar") and self.control_bar and not self.is_locked_stealth:
                    self.control_bar.show()
                if hasattr(self, "hud_window") and self.hud_window and self.current_mode == "hud" and not self.is_locked_stealth:
                    self.hud_window.show()
                if hasattr(self, "worker") and self.worker:
                    self.worker.set_paused(self.is_paused)
                self.raise_to_topmost()
            return

        try:
            if getattr(self, "_menu_open", False):
                return
            if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
                return

            fg = ctypes.windll.user32.GetForegroundWindow()
            if not fg:
                return

            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if pid.value == os.getpid():
                return

            pinned_pname = (self.pinned_app.get("process_name") or "").lower()
            pinned_pid = self.pinned_app.get("pid")

            curr_pname = ""
            try:
                import psutil
                curr_pname = psutil.Process(pid.value).name().lower()
            except Exception:
                pass

            is_match = False
            if pinned_pid and pid.value == pinned_pid:
                is_match = True
            elif pinned_pname and curr_pname == pinned_pname:
                is_match = True

            if is_match:
                if self._pinned_hidden:
                    self._pinned_hidden = False
                    self.show()
                    if hasattr(self, "control_bar") and self.control_bar and not self.is_locked_stealth:
                        self.control_bar.show()
                    if hasattr(self, "hud_window") and self.hud_window and self.current_mode == "hud" and not self.is_locked_stealth:
                        self.hud_window.show()
                    if hasattr(self, "worker") and self.worker:
                        self.worker.set_paused(self.is_paused)
                    self.raise_to_topmost()
            else:
                if not self._pinned_hidden:
                    self._pinned_hidden = True
                    if hasattr(self, "worker") and self.worker:
                        self.worker.set_paused(True)
                    self.hide()
                    if hasattr(self, "control_bar") and self.control_bar:
                        self.control_bar.hide()
                    if hasattr(self, "hud_window") and self.hud_window:
                        self.hud_window.hide()
        except Exception:
            pass

    def _on_foreground_window_changed(self, hwnd: int):
        """Вызывается моментально при переключении окон в Windows (Alt+Tab или клик в игру)."""
        if self.pinned_app:
            self._check_pinned_visibility()
            if self._pinned_hidden:
                return

        if not self.isVisible():
            return
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return
        if getattr(self, "_menu_open", False):
            return
        if not hwnd or is_desktop_or_taskbar(hwnd):
            return
        try:
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == os.getpid():
                return
        except Exception:
            pass

        # Пользователь переключился на игру или внешнее приложение:
        # Гарантируем, что рамка не окажется под окном игры
        self.raise_to_topmost()
        # Игры на DirectX/Vulkan восстанавливают swapchain и зовут HWND_TOPMOST с задержкой в несколько миллисекунд
        QTimer.singleShot(40, self.raise_to_topmost)
        QTimer.singleShot(150, self.raise_to_topmost)
        QTimer.singleShot(350, self.raise_to_topmost)

    def _check_zorder_smart(self):
        """Проверяет, не перекрыло ли активное окно нашу рамку в z-order."""
        if self.pinned_app:
            self._check_pinned_visibility()
            if self._pinned_hidden:
                return

        if not self.isVisible():
            return
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return
        if getattr(self, "_menu_open", False):
            return
        if sys.platform != "win32":
            return
        try:
            fg = ctypes.windll.user32.GetForegroundWindow()
            if not fg or is_desktop_or_taskbar(fg):
                return
            pid = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
            if pid.value == os.getpid():
                return

            my_hwnd = int(self.winId()) if hasattr(self, "winId") else 0
            if is_window_above_us(fg, my_hwnd):
                self.raise_to_topmost()
        except Exception:
            pass

    def _setup_hud_ui(self):
        self.hud_window = TranslationHudWindow(self)
        self.hud_frame = self.hud_window.hud_frame
        self.lbl_hud_text = self.hud_window.lbl_hud_text
        self._update_hud_style()
        self._update_hud_geometry()

    def _update_hud_geometry(self):
        if hasattr(self, "hud_window") and self.hud_window:
            self.hud_window.sync_to_frame()
            is_visible = (self.current_mode == "hud") and not self.is_paused
            self.hud_window.setVisible(is_visible)
            self.hud_frame.setVisible(is_visible)

    def _update_hud_style(self):
        if hasattr(self, "hud_window") and self.hud_window:
            self.hud_window.sync_style()

    def raise_to_topmost(self):
        """Гарантирует удержание рамки и её элементов на самом верху z-order стека Windows."""
        if not self.isVisible():
            return
        # Если открыто всплывающее меню или диалог — не перехватываем z-order
        if QApplication.activePopupWidget() is not None or QApplication.activeModalWidget() is not None:
            return
        if getattr(self, "_menu_open", False):
            return
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            except Exception:
                pass
        self.raise_()
        if hasattr(self, "hud_window") and self.hud_window and self.hud_window.isVisible():
            self.hud_window.raise_to_topmost()
        if hasattr(self, "control_bar") and self.control_bar and not self.is_locked_stealth:
            if not self.control_bar.isVisible():
                self.control_bar.show()
            self.control_bar.raise_to_topmost()
        if hasattr(self, "unlock_pill") and self.unlock_pill and self.is_locked_stealth and self.unlock_pill.isVisible():
            self.unlock_pill.raise_to_topmost()

    def set_passthrough(self, enabled: bool):
        """
        Включает или выключает неосязаемость рамки (WS_EX_TRANSPARENT).
        При enabled=True клики мыши проходят сквозь рамку прямо в приложение или игру под ней.
        При enabled=False рамку можно изменять за границы и перемещать.
        """
        self.passthrough_enabled = enabled
        if sys.platform == "win32":
            try:
                hwnd = int(self.winId())
                style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
                style |= 0x08000000  # WS_EX_NOACTIVATE: не забирать фокус у полноэкранных игр/видео
                style |= 0x00000008  # WS_EX_TOPMOST: поверх всех окон
                if enabled:
                    ctypes.windll.user32.SetWindowLongW(hwnd, -20, style | 0x00000020)  # WS_EX_TRANSPARENT
                else:
                    ctypes.windll.user32.SetWindowLongW(hwnd, -20, style & ~0x00000020)
                # SWP_NOSIZE (1) | SWP_NOMOVE (2) | SWP_NOACTIVATE (0x10) | SWP_FRAMECHANGED (0x20) = 0x33
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0033)
            except Exception:
                pass

        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.update_passthrough_ui()
            if not self.is_locked_stealth and self.control_bar.isVisible():
                self.control_bar.raise_to_topmost()
        self.update()

    def set_stealth_lock(self, locked: bool):
        """
        Включает или выключает режим маскировки по кнопке «Глазик».
        В скрытом режиме тулбар скрыт, отображается только мини-глазик 16x16,
        а клики проходят сквозь рамку.
        """
        self.is_locked_stealth = locked
        if locked:
            if hasattr(self, "control_bar"):
                self.control_bar.hide()
            if hasattr(self, "hud_window"):
                self.hud_window.hide()
            self.unlock_pill.update_position()
            self.unlock_pill.update_state()
            self.unlock_pill.show()
            self.unlock_pill.raise_to_topmost()
            self.set_passthrough(True)
        else:
            self.unlock_pill.hide()
            if hasattr(self, "control_bar"):
                self.control_bar.sync_to_frame()
                self.control_bar.show()
                self.control_bar.raise_to_topmost()
            if hasattr(self, "hud_window"):
                self.hud_window.setVisible(self.current_mode == "hud")
                if self.hud_window.isVisible():
                    self.hud_window.raise_to_topmost()
            self.set_passthrough(self.passthrough_enabled)
        self.update()

    def unfold_controls(self):
        """Разворачивает панель управления и снимает скрытие."""
        self.set_stealth_lock(False)

    def _set_languages(self, src: str, tgt: str):
        self.src_lang = src
        self.tgt_lang = tgt
        if hasattr(self, "control_bar"):
            self.control_bar._update_lang_button_text()
            self.control_bar.raise_to_topmost()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.set_languages(src, tgt)
        self.raise_to_topmost()

    def _set_display_mode(self, mode: str):
        self.current_mode = mode
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.set_mode(mode)
        if hasattr(self, "control_bar"):
            self.control_bar._update_mode_button_text()
            self.control_bar.raise_to_topmost()
        self._update_hud_geometry()
        self.raise_to_topmost()
        self.update()

    def _toggle_display_mode(self):
        new_mode = "inplace" if self.current_mode == "hud" else "hud"
        self._set_display_mode(new_mode)

    def _toggle_pause(self):
        self.is_paused = not self.is_paused
        self.worker.set_paused(self.is_paused)

        if self.is_paused:
            self.translated_blocks = []
            self.translated_text = ""
            self.original_text = ""
            if hasattr(self, "lbl_hud_text"):
                self.lbl_hud_text.setText("")
            if hasattr(self, "hud_window") and self.hud_window:
                self.hud_window.hide()
            if hasattr(self, "hud_frame"):
                self.hud_frame.hide()
        else:
            if hasattr(self, "worker"):
                self.worker.reset_cache()
            if hasattr(self, "hud_window") and self.hud_window:
                self.hud_window.setVisible(self.current_mode == "hud")
                if self.hud_window.isVisible():
                    self.hud_window.raise_to_topmost()
            if hasattr(self, "hud_frame"):
                self.hud_frame.setVisible(self.current_mode == "hud")

        if hasattr(self, "control_bar") and hasattr(self.control_bar, "btn_pause"):
            if self.is_paused:
                self.control_bar.btn_pause.setIcon(create_themed_icon("play", is_dark=True, size=13))
                self.control_bar.btn_pause.setToolTip(tr("trans_resume_tip", "Возобновить сканирование"))
                self.control_bar.btn_pause.setStyleSheet("background-color: #059669; border-color: #10b981; color: white;")
            else:
                self.control_bar.btn_pause.setIcon(create_themed_icon("pause", is_dark=True, size=13))
                self.control_bar.btn_pause.setToolTip(tr("trans_pause_tip", "Приостановить сканирование"))
                self.control_bar.btn_pause.setStyleSheet("")

        if hasattr(self, "unlock_pill") and self.unlock_pill:
            self.unlock_pill.update_state()

        self.update()

    def spawn_another_frame(self):
        """Создает и показывает еще одну независимую рамку перевода со смещением."""
        geo = self.geometry()
        new_geo = QRect(geo.x() + 40, geo.y() + 40, geo.width(), geo.height())
        screen = self.screen()
        if not screen:
            screen = QGuiApplication.screenAt(geo.center()) or QGuiApplication.primaryScreen()
        if screen:
            s_geo = screen.availableGeometry()
            if new_geo.right() > s_geo.right() or new_geo.bottom() > s_geo.bottom():
                new_geo.moveTopLeft(QPoint(s_geo.left() + 50, s_geo.top() + 50))

        app_inst = getattr(QApplication.instance(), "app_instance", None)
        if app_inst and hasattr(app_inst, "start_translation_frame"):
            win = app_inst.start_translation_frame(new_geo)
        else:
            win = TranslationFrameWindow(initial_rect=new_geo)
            win.show()
            win.raise_to_topmost()
        if win:
            win.set_passthrough(False)
            if hasattr(win, "control_bar") and win.control_bar:
                win.control_bar.update_passthrough_ui()
        return win

    def _copy_translation(self):
        """Совместимость со старыми вызовами: копирует текст перевода."""
        self._copy_translation_text()

    def _copy_translation_text(self):
        """Копирует распознанный переведенный текст в буфер обмена."""
        txt = self.translated_text.strip()
        if txt:
            QApplication.clipboard().setText(txt)
            show_stealth_tooltip(QCursor.pos(), tr("trans_copied", "Перевод скопирован в буфер!"), self)
        else:
            show_stealth_tooltip(QCursor.pos(), tr("trans_no_text_copy", "Нет текста для копирования"), self)
        if hasattr(self, "worker"):
            self.worker.set_tooltip_active(False)

    def _copy_translation_image(self):
        """Копирует изображение экрана с наложенным переводом в буфер обмена."""
        pix = self.get_translated_composite_pixmap()
        if pix and not pix.isNull():
            QApplication.clipboard().setPixmap(pix)
            show_stealth_tooltip(QCursor.pos(), tr("trans_image_copied", "Изображение с переводом скопировано в буфер!"), self)
        if hasattr(self, "worker"):
            self.worker.set_tooltip_active(False)

    def _save_translation_image(self):
        """Открывает диалог сохранения изображения экрана с наложенным переводом в файл (PNG, JPG, WebP)."""
        pix = self.get_translated_composite_pixmap()
        if not pix or pix.isNull():
            return

        from datetime import datetime
        default_name = f"Translation_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.png"

        save_dir = os.path.expanduser("~/Pictures")
        try:
            from config import ConfigManager
            save_dir = ConfigManager.get_instance().config.save_directory or save_dir
        except Exception:
            pass

        initial_path = os.path.join(save_dir, default_name)

        was_paused = self.is_paused
        if hasattr(self, "worker") and self.worker:
            self.worker.set_paused(True)
        self._menu_open = True
        try:
            file_path, selected_filter = QFileDialog.getSaveFileName(
                self,
                tr("trans_save_dlg_title", "Сохранить изображение с переводом"),
                initial_path,
                "PNG (*.png);;JPEG (*.jpg *.jpeg);;WebP (*.webp);;Все файлы (*.*)"
            )
        finally:
            self._menu_open = False
            if hasattr(self, "worker") and self.worker:
                self.worker.set_paused(was_paused)
            self.raise_to_topmost()

        if file_path:
            ext = os.path.splitext(file_path)[1].lower().lstrip(".")
            fmt = ext.upper() if ext in ("png", "jpg", "jpeg", "webp") else "PNG"
            if fmt == "JPEG":
                fmt = "JPG"
            if pix.save(file_path, fmt):
                show_stealth_tooltip(QCursor.pos(), tr("trans_image_saved", "Изображение сохранено!"), self)
                try:
                    from ui.toast_notification import ToastManager
                    ToastManager.get_instance().show_toast(
                        tr("trans_saved_toast", "Изображение перевода сохранено"),
                        os.path.basename(file_path),
                        icon_name="save"
                    )
                except Exception:
                    pass

    def get_translated_composite_pixmap(self) -> QPixmap:
        """Создает чистое композитное изображение экрана под рамкой с отрисованным поверх переводом."""
        rect = self.geometry()
        screen = self.screen()
        if not screen:
            screen = QGuiApplication.screenAt(rect.center())
        if not screen:
            screen = QGuiApplication.primaryScreen()

        pix = screen.grabWindow(0, rect.x(), rect.y(), rect.width(), rect.height())
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self._draw_translated_content(painter, rect.width(), rect.height(), ignore_control_bar=True)
        painter.end()
        return pix

    def _toggle_match_color(self, checked: bool):
        self.match_text_color = checked
        self.update()

    def _toggle_match_font(self, checked: bool):
        self.match_font_family = checked
        self.update()

    def _set_opacity(self, val: float):
        self.bg_opacity = val
        self._update_hud_style()
        self.update()

    def _set_theme(self, theme: str):
        self.bg_theme = theme
        self._update_hud_style()
        self.update()

    def _set_font_size(self, sz: int):
        self.hud_font_size = sz
        self._update_hud_style()
        self.update()

    def _set_scan_interval(self, ms: int):
        self.scan_interval = ms
        if hasattr(self, "worker"):
            self.worker.set_interval(ms)
        try:
            from config import ConfigManager
            cfg_mgr = ConfigManager.get_instance()
            cfg_mgr.config.live_translator_interval_ms = ms
            cfg_mgr.save()
        except Exception:
            pass

    def _toggle_smart_diff(self, checked: bool):
        self.smart_diff_enabled = checked
        if hasattr(self, "worker"):
            self.worker.set_smart_diff(checked)

    @pyqtSlot(str, str, list)
    def _on_translation_ready(self, original: str, translated: str, blocks: list):
        if self.is_paused or getattr(self, "_menu_open", False):
            return
        if hasattr(self, "worker") and getattr(self.worker, "_tooltip_active", False):
            return
        if time.time() < getattr(self, "_menu_closed_time", 0.0) + 0.40:
            return
        if _is_framio_ui_text(original):
            return

        clean_blocks = []
        for b in blocks:
            b_txt = b.get("orig", "") or b.get("text", "")
            if not _is_framio_ui_text(b_txt):
                clean_blocks.append(b)

        self.original_text = original
        self.translated_text = translated
        self.translated_blocks = clean_blocks

        if translated:
            self.lbl_hud_text.setText(translated)
        else:
            self.lbl_hud_text.setText(tr("trans_no_text", "Текст не обнаружен"))
        self.update()

    # ------------------ Обработка перемещения и изменения размера ------------------

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_hud_geometry()
        if hasattr(self, "control_bar") and self.control_bar and not getattr(self.control_bar, "_is_dragging", False):
            self.control_bar.sync_to_frame()
        if hasattr(self, "unlock_pill") and self.unlock_pill.isVisible():
            self.unlock_pill.update_position()
        if not self.is_moving_window and not self.is_resizing and not getattr(getattr(self, "control_bar", None), "_is_dragging", False):
            if hasattr(self, "worker") and self.worker.isRunning():
                self.worker.update_geometry(self.geometry())

    def moveEvent(self, event):
        super().moveEvent(event)
        if hasattr(self, "control_bar") and self.control_bar and not getattr(self.control_bar, "_is_dragging", False):
            self.control_bar.sync_to_frame()
        if hasattr(self, "unlock_pill") and self.unlock_pill.isVisible():
            self.unlock_pill.update_position()
        if not self.is_moving_window and not self.is_resizing and not getattr(getattr(self, "control_bar", None), "_is_dragging", False):
            if hasattr(self, "worker") and self.worker.isRunning():
                self.worker.update_geometry(self.geometry())

    def _hit_test(self, pos: QPoint) -> int:
        margin = self.HANDLE_SIZE
        w, h = self.width(), self.height()
        x, y = pos.x(), pos.y()

        on_left = x <= margin
        on_right = x >= w - margin
        on_top = y <= margin
        on_bottom = y >= h - margin

        if on_top and on_left:
            return self.HANDLE_TL
        if on_top and on_right:
            return self.HANDLE_TR
        if on_bottom and on_left:
            return self.HANDLE_BL
        if on_bottom and on_right:
            return self.HANDLE_BR
        if on_top:
            return self.HANDLE_T
        if on_bottom:
            return self.HANDLE_B
        if on_left:
            return self.HANDLE_L
        if on_right:
            return self.HANDLE_R
        return self.HANDLE_NONE

    def _update_cursor(self, handle: int):
        if self.passthrough_enabled:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        cursors = {
            self.HANDLE_TL: Qt.CursorShape.SizeFDiagCursor,
            self.HANDLE_BR: Qt.CursorShape.SizeFDiagCursor,
            self.HANDLE_TR: Qt.CursorShape.SizeBDiagCursor,
            self.HANDLE_BL: Qt.CursorShape.SizeBDiagCursor,
            self.HANDLE_T: Qt.CursorShape.SizeVerCursor,
            self.HANDLE_B: Qt.CursorShape.SizeVerCursor,
            self.HANDLE_L: Qt.CursorShape.SizeHorCursor,
            self.HANDLE_R: Qt.CursorShape.SizeHorCursor,
            self.HANDLE_NONE: Qt.CursorShape.ArrowCursor
        }
        self.setCursor(cursors.get(handle, Qt.CursorShape.ArrowCursor))

    def mousePressEvent(self, event: QMouseEvent):
        if self.passthrough_enabled or self.is_locked_stealth:
            return

        if event.button() == Qt.MouseButton.LeftButton:
            handle = self._hit_test(event.pos())
            if handle != self.HANDLE_NONE:
                self.is_resizing = True
                self.active_handle = handle
                self.drag_start_pos = event.globalPosition().toPoint()
                self.initial_geometry = self.geometry()
                event.accept()
                return

            self.is_moving_window = True
            self.drag_start_pos = event.globalPosition().toPoint()
            self.initial_geometry = self.geometry()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.passthrough_enabled or self.is_locked_stealth:
            return

        if self.is_resizing:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            rect = QRect(self.initial_geometry)
            min_w, min_h = 240, 100

            if self.active_handle in (self.HANDLE_TL, self.HANDLE_L, self.HANDLE_BL):
                new_w = max(min_w, rect.width() - delta.x())
                rect.setLeft(rect.right() - new_w)
            elif self.active_handle in (self.HANDLE_TR, self.HANDLE_R, self.HANDLE_BR):
                rect.setWidth(max(min_w, rect.width() + delta.x()))

            if self.active_handle in (self.HANDLE_TL, self.HANDLE_T, self.HANDLE_TR):
                new_h = max(min_h, rect.height() - delta.y())
                rect.setTop(rect.bottom() - new_h)
            elif self.active_handle in (self.HANDLE_BL, self.HANDLE_B, self.HANDLE_BR):
                rect.setHeight(max(min_h, rect.height() + delta.y()))

            screen = self.screen()
            if not screen:
                screen = QApplication.screenAt(rect.center())
            if screen:
                avail = screen.availableGeometry()
                if rect.bottom() > avail.bottom():
                    rect.setBottom(avail.bottom())
                if rect.right() > avail.right():
                    rect.setRight(avail.right())
                if rect.top() < avail.top():
                    rect.setTop(avail.top())
                if rect.left() < avail.left():
                    rect.setLeft(avail.left())

            self.setGeometry(rect)
            event.accept()
            return

        if self.is_moving_window:
            delta = event.globalPosition().toPoint() - self.drag_start_pos
            new_pos = self.initial_geometry.topLeft() + delta
            screen = self.screen()
            if not screen:
                screen = QApplication.screenAt(new_pos)
            if screen:
                avail = screen.availableGeometry()
                new_pos.setX(max(avail.left(), min(new_pos.x(), avail.right() - self.width() + 1)))
                new_pos.setY(max(avail.top(), min(new_pos.y(), avail.bottom() - self.height() + 1)))
            self.move(new_pos)
            event.accept()
            return

        handle = self._hit_test(event.pos())
        self._update_cursor(handle)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            was_busy = self.is_resizing or self.is_moving_window
            self.is_resizing = False
            self.is_moving_window = False
            self.active_handle = self.HANDLE_NONE
            self.setCursor(Qt.CursorShape.ArrowCursor)
            if was_busy:
                if hasattr(self, "control_bar") and self.control_bar:
                    self.control_bar.sync_to_frame()
                if hasattr(self, "worker") and self.worker.isRunning():
                    self.worker.update_geometry(self.geometry())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------ Отрисовка рамки и In-place текста ------------------

    @staticmethod
    def _layout_text_block(
        text: str,
        family: str,
        weight: QFont.Weight,
        is_italic: bool,
        ideal_ps: int,
        bw: float,
        bh: float,
        lines_cnt: int,
        line_h: float,
        max_frame_w: float,
        max_frame_h: float
    ) -> tuple[QFont, float, float, bool]:
        """
        Интеллектуальный расчет шрифта и габаритов перевода:
        1. Если оригинал был в 1 строку и перевод помещается в 1 строку (с естественным расширением под русский язык)
           при целевом кегле — отображаем на 1 строке без сжатия шрифта.
        2. Если текст не помещается на 1 строке или оригинал многострочный — переносим по словам (word wrap),
           сохраняя естественный кегль (допустимо уменьшение максимум на 15-20% от идеала, но никогда не мельче 11 px).
        3. Возвращает (QFont, eff_w, eff_h, is_multiline).
        """
        # 1. Пробуем уместить на 1 строке, если исходный текст был 1 строка
        if lines_cnt == 1:
            target_1line_w = min(max_frame_w, max(bw * 1.65, bw + 60.0))
            f = QFont(family, 10, weight)
            f.setPixelSize(ideal_ps)
            f.setItalic(is_italic)
            fm = QFontMetrics(f)
            adv = float(fm.horizontalAdvance(text))
            fh = float(fm.height())
            if adv <= target_1line_w and fh <= max_frame_h:
                eff_w = min(max_frame_w, max(bw, adv + 10.0))
                eff_h = min(max_frame_h, max(bh, fh + 4.0))
                return f, eff_w, eff_h, False

            # Если немного не влезло, пробуем уместить на 1 строке с минимальным уменьшением шрифта (до 85%)
            min_1line_ps = max(12, int(round(ideal_ps * 0.85)))
            for ps in range(ideal_ps - 1, min_1line_ps - 1, -1):
                f_cand = QFont(family, 10, weight)
                f_cand.setPixelSize(ps)
                f_cand.setItalic(is_italic)
                fm_cand = QFontMetrics(f_cand)
                cand_adv = float(fm_cand.horizontalAdvance(text))
                cand_fh = float(fm_cand.height())
                if cand_adv <= target_1line_w and cand_fh <= max_frame_h:
                    eff_w = min(max_frame_w, max(bw, cand_adv + 10.0))
                    eff_h = min(max_frame_h, max(bh, cand_fh + 4.0))
                    return f_cand, eff_w, eff_h, False

        # 2. Многострочный режим переноса по словам (если текст длиннее или уже был многострочным)
        min_ps = max(10, int(round(ideal_ps * 0.80)))
        wrap_w = min(max_frame_w, max(bw * 1.50, bw + 50.0, 110.0))
        allowed_h = min(max_frame_h, max(bh * 1.5, float(lines_cnt + 2) * (ideal_ps * 1.35) + 12.0, 48.0))

        flags = int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap)
        best_f = None
        best_w = wrap_w
        best_h = allowed_h

        for ps in range(ideal_ps, min_ps - 1, -1):
            f = QFont(family, 10, weight)
            f.setPixelSize(ps)
            f.setItalic(is_italic)
            fm = QFontMetrics(f)
            r = fm.boundingRect(QRect(0, 0, int(wrap_w), 9999), flags, text)
            if r.height() <= allowed_h and r.width() <= wrap_w:
                best_f = f
                cand_w = min(wrap_w, max(bw, float(r.width()) + 12.0))
                r_check = fm.boundingRect(QRect(0, 0, int(cand_w), 9999), flags, text)
                best_w = cand_w
                best_h = min(allowed_h, max(bh, float(r_check.height()) + 6.0))
                break

        if best_f is None:
            best_f = QFont(family, 10, weight)
            best_f.setPixelSize(min_ps)
            best_f.setItalic(is_italic)
            fm = QFontMetrics(best_f)
            r = fm.boundingRect(QRect(0, 0, int(wrap_w), 9999), flags, text)
            best_w = min(max_frame_w, max(bw, float(r.width()) + 12.0))
            r_check = fm.boundingRect(QRect(0, 0, int(best_w), 9999), flags, text)
            best_h = min(max_frame_h, max(bh, float(r_check.height()) + 6.0))

        return best_f, best_w, best_h, True

    @staticmethod
    def _fit_font_to_box(
        text: str,
        family: str,
        weight: QFont.Weight,
        is_italic: bool,
        max_w: float,
        max_h: float,
        ideal_ps: int,
        is_multiline: bool = False,
        min_ps: int = 8
    ) -> tuple[QFont, QFontMetrics, float, float]:
        flags = int(Qt.AlignmentFlag.AlignCenter | (Qt.TextFlag.TextWordWrap if is_multiline else 0))
        low = min_ps
        high = max(min_ps, ideal_ps)
        best_ps = min_ps

        while low <= high:
            mid = (low + high) // 2
            f = QFont(family, 10, weight)
            f.setPixelSize(mid)
            f.setItalic(is_italic)
            fm = QFontMetrics(f)
            if is_multiline:
                r = fm.boundingRect(QRect(0, 0, int(max_w), 9999), flags, text)
                tw = float(r.width())
                th = float(r.height())
            else:
                tw = float(fm.horizontalAdvance(text))
                th = float(fm.height())

            if tw <= max_w and th <= max_h:
                best_ps = mid
                low = mid + 1
            else:
                high = mid - 1

        f = QFont(family, 10, weight)
        f.setPixelSize(best_ps)
        f.setItalic(is_italic)
        fm = QFontMetrics(f)
        if is_multiline:
            r = fm.boundingRect(QRect(0, 0, int(max_w), 9999), flags, text)
            tw = float(r.width())
            th = float(r.height())
        else:
            tw = float(fm.horizontalAdvance(text))
            th = float(fm.height())
        return f, fm, tw, th

    def paintEvent(self, event: QPaintEvent):
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            w, h = self.width(), self.height()

            # 1. Отрисовка границы рамки
            if not self.is_locked_stealth:
                if not self.passthrough_enabled:
                    # Режим настройки: яркая граница с маркерами ресайза
                    pen = QPen(QColor(234, 179, 8, 230), 2.0, Qt.PenStyle.SolidLine)
                    painter.setPen(pen)
                    painter.setBrush(QColor(15, 23, 42, 25))
                    painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

                    painter.setBrush(QColor(250, 204, 21))
                    painter.setPen(Qt.PenStyle.NoPen)
                    m = 6
                    painter.drawRect(0, 0, m, m)
                    painter.drawRect(w - m, 0, m, m)
                    painter.drawRect(0, h - m, m, m)
                    painter.drawRect(w - m, h - m, m, m)
                else:
                    # Неосязаемый режим перевода: аккуратная стильная рамка
                    pen = QPen(QColor(59, 130, 246, 200), 1.8, Qt.PenStyle.SolidLine)
                    painter.setPen(pen)
                    painter.setBrush(QColor(15, 23, 42, 15))
                    painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

                    painter.setBrush(QColor(96, 165, 250))
                    painter.setPen(Qt.PenStyle.NoPen)
                    m = 6
                    painter.drawRect(0, 0, m, m)
                    painter.drawRect(w - m, 0, m, m)
                    painter.drawRect(0, h - m, m, m)
                    painter.drawRect(w - m, h - m, m, m)

            # 2. Отрисовка перевода поверх текста
            if not self.is_paused:
                self._draw_translated_content(painter, w, h, ignore_control_bar=False)
        except Exception:
            pass

    def _draw_translated_content(self, painter: QPainter, w: int, h: int, ignore_control_bar: bool = False):
        """Отрисовывает переведенный текст: либо in-place блоки поверх оригинала, либо аккуратные HUD-субтитры."""
        r, g, b = self.THEME_COLORS.get(self.bg_theme, (15, 23, 42))
        alpha = int(self.bg_opacity * 255)

        if self.current_mode == "inplace" and self.translated_blocks:
            max_frame_w = max(30.0, float(w - 12.0))
            max_frame_h = max(20.0, float(h - 12.0))

            for item in list(self.translated_blocks):
                try:
                    bx = float(item.get("x", 0))
                    by = float(item.get("y", 0))
                    bw = float(item.get("width", 50))
                    bh = float(item.get("height", 20))
                    txt = str(item.get("translated", "") or "")
                    if not txt:
                        continue

                    lines_cnt = max(1, int(item.get("lines_count", 1)))
                    line_h = float(item.get("line_height", bh / float(lines_cnt)))

                    if self.hud_font_size > 0:
                        ideal_ps = self.hud_font_size
                    else:
                        base_h = line_h if (line_h > 5 and line_h < 40) else min(24.0, max(12.0, bh / float(lines_cnt)))
                        ideal_ps = max(11, min(26, int(round(base_h * 0.85))))

                    if self.match_font_family:
                        family = item.get("font_family", "Segoe UI")
                        is_bold = item.get("is_bold", False)
                        is_italic = item.get("is_italic", False)
                        weight = QFont.Weight.Bold if is_bold else QFont.Weight.Normal
                    else:
                        family = "Segoe UI"
                        weight = QFont.Weight.Normal
                        is_italic = False

                    font, eff_w, eff_h, is_multiline = self._layout_text_block(
                        txt, family, weight, is_italic,
                        ideal_ps=ideal_ps, bw=bw, bh=bh,
                        lines_cnt=lines_cnt, line_h=line_h,
                        max_frame_w=max_frame_w, max_frame_h=max_frame_h
                    )

                    orig_cx = bx + bw / 2.0
                    orig_cy = by + bh / 2.0

                    draw_x = max(2.0, min(float(w - eff_w - 2.0), orig_cx - eff_w / 2.0))
                    draw_y = max(2.0, min(float(h - eff_h - 2.0), orig_cy - eff_h / 2.0))

                    bg_rect = QRectF(draw_x, draw_y, eff_w, eff_h)

                    if not ignore_control_bar and hasattr(self, "control_bar") and self.control_bar and not self.control_bar.isHidden() and not self.is_locked_stealth:
                        cb_geo = self.control_bar.geometry()
                        cb_local = QRectF(float(cb_geo.x() - self.x()), float(cb_geo.y() - self.y()), float(cb_geo.width()), float(cb_geo.height()))
                        if bg_rect.intersects(cb_local):
                            continue

                    if self.match_text_color and "bg_color_rgb" in item and item["bg_color_rgb"]:
                        br, bg, bb = item["bg_color_rgb"]
                    else:
                        br, bg, bb = r, g, b

                    if self.bg_opacity > 0.05:
                        painter.setBrush(QColor(int(br), int(bg), int(bb), alpha))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.drawRoundedRect(bg_rect, 4.0, 4.0)

                    if self.match_text_color and "color_rgb" in item and item["color_rgb"]:
                        cr, cg, cb = item["color_rgb"]
                        t_lum = 0.299 * cr + 0.587 * cg + 0.114 * cb
                        b_lum = 0.299 * br + 0.587 * bg + 0.114 * bb
                        if abs(t_lum - b_lum) < 55:
                            text_color = QColor(255, 255, 255) if b_lum < 128 else QColor(15, 23, 42)
                        else:
                            text_color = QColor(int(cr), int(cg), int(cb))
                    else:
                        text_color = QColor(248, 250, 252)

                    painter.setFont(font)
                    painter.setPen(text_color)
                    flags = int(Qt.AlignmentFlag.AlignCenter | (Qt.TextFlag.TextWordWrap if is_multiline else 0))
                    painter.drawText(bg_rect, flags, txt)
                except Exception:
                    pass

        elif (self.current_mode == "hud" or not self.translated_blocks) and self.translated_text.strip():
            # Режим субтитров: отрисовка аккуратной плашки перевода внизу
            txt = self.translated_text.strip()
            hud_h = min(float(h) * 0.45, 60.0)
            hud_y = float(h) - hud_h - 10.0
            hud_w = min(float(w) - 20.0, 520.0)
            hud_x = (float(w) - hud_w) / 2.0
            bg_rect = QRectF(hud_x, hud_y, hud_w, hud_h)

            painter.setBrush(QColor(15, 23, 42, 230))
            painter.setPen(QPen(QColor(59, 130, 246, 200), 1.5))
            painter.drawRoundedRect(bg_rect, 6.0, 6.0)

            painter.setPen(QColor(248, 250, 252))
            f = QFont("Segoe UI", 11)
            f.setBold(True)
            painter.setFont(f)
            painter.drawText(bg_rect.adjusted(10, 4, -10, -4), int(Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap), txt)

    def showEvent(self, event):
        super().showEvent(event)
        if sys.platform == "win32":
            try:
                wid = int(self.winId())
                ctypes.windll.user32.SetWindowDisplayAffinity(wid, 0x00000011)
            except Exception:
                pass
        self.set_passthrough(self.passthrough_enabled)
        self.raise_to_topmost()
        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.sync_to_frame()
            self.control_bar.show()
            self.control_bar.raise_to_topmost()

    def closeEvent(self, event):
        if getattr(self, "_win_event_hook", None) is not None:
            try:
                ctypes.windll.user32.UnhookWinEvent(self._win_event_hook)
            except Exception:
                pass
            self._win_event_hook = None
        self._hook_proc = None
        if getattr(self, "_smart_zorder_timer", None) is not None and self._smart_zorder_timer.isActive():
            self._smart_zorder_timer.stop()
        if getattr(self, "_topmost_timer", None) is not None and self._topmost_timer.isActive():
            self._topmost_timer.stop()
        app_inst = QApplication.instance()
        if hasattr(app_inst, "_active_translation_windows") and self in app_inst._active_translation_windows:
            try:
                app_inst._active_translation_windows.remove(self)
            except Exception:
                pass
        if hasattr(self, "control_bar") and self.control_bar:
            self.control_bar.close()
        if hasattr(self, "hud_window") and self.hud_window:
            self.hud_window.close()
        if hasattr(self, "unlock_pill") and self.unlock_pill:
            self.unlock_pill.close()
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.stop()
        self.closed.emit()
        self.frame_closed.emit()
        super().closeEvent(event)
