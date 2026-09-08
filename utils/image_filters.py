# -*- coding: utf-8 -*-
"""
Фильтры реального времени для обработки скриншотов и кадров видео/GIF.
"""

from __future__ import annotations

class FilterType:
    NONE = "none"
    GRAYSCALE = "grayscale"
    INVERT = "invert"
    BLUR = "blur"
    PIXELATE = "pixelate"
    VIBRANT = "vibrant"
    SEPIA = "sepia"

def get_localized_filter_names() -> dict:
    from utils.i18n import tr
    return {
        FilterType.NONE: tr("filter_none", "Без фильтра"),
        FilterType.GRAYSCALE: tr("filter_grayscale", "Оттенки серого (Чёрно-белый)"),
        FilterType.BLUR: tr("filter_blur", "Мягкое размытие (Блюр)"),
        FilterType.INVERT: tr("filter_invert", "Инверсия цветов (Негатив)"),
        FilterType.VIBRANT: tr("filter_vibrant", "Повышенная контрастность"),
        FilterType.SEPIA: tr("filter_sepia", "Тёплая сепия (Винтаж)")
    }

FILTER_NAMES = {
    FilterType.NONE: "Без фильтра",
    FilterType.GRAYSCALE: "Оттенки серого (Чёрно-белый)",
    FilterType.INVERT: "Инверсия цветов (Негатив)",
    FilterType.BLUR: "Мягкое размытие (Блюр)",
    FilterType.PIXELATE: "Пикселизация (Цензура)",
    FilterType.VIBRANT: "Повышенная контрастность",
    FilterType.SEPIA: "Тёплая сепия (Винтаж)"
}

def apply_filter(frame_bgr: np.ndarray, filter_type: str) -> np.ndarray:
    """
    Применяет выбранный фильтр к кадру в формате BGR (uint8 numpy array).
    """
    if frame_bgr is None or frame_bgr.size == 0 or filter_type == FilterType.NONE:
        return frame_bgr

    try:
        import cv2
        import numpy as np

        if filter_type == FilterType.GRAYSCALE:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        elif filter_type == FilterType.INVERT:
            return cv2.bitwise_not(frame_bgr)

        elif filter_type == FilterType.BLUR:
            # Быстрый гауссов блюр
            return cv2.GaussianBlur(frame_bgr, (21, 21), 0)

        elif filter_type == FilterType.PIXELATE:
            h, w = frame_bgr.shape[:2]
            pixel_size = max(8, min(h, w) // 30)
            small = cv2.resize(frame_bgr, (max(1, w // pixel_size), max(1, h // pixel_size)), interpolation=cv2.INTER_LINEAR)
            return cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)

        elif filter_type == FilterType.VIBRANT:
            # Увеличение насыщенности и контраста
            hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV).astype(np.float32)
            hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.35, 0, 255)
            hsv[:, :, 2] = np.clip(hsv[:, :, 2] * 1.1, 0, 255)
            return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

        elif filter_type == FilterType.SEPIA:
            kernel = np.array([[0.272, 0.534, 0.131],
                               [0.349, 0.686, 0.168],
                               [0.393, 0.769, 0.189]])
            sepia = cv2.transform(frame_bgr, kernel)
            return np.clip(sepia, 0, 255).astype(np.uint8)

    except Exception as e:
        print(f"[ImageFilters] Ошибка применения фильтра {filter_type}: {e}")

    return frame_bgr
