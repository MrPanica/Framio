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
    MAGNIFIER = "magnifier"

def get_localized_filter_names() -> dict:
    from utils.i18n import tr
    return {
        FilterType.NONE: tr("filter_none", "Без фильтра"),
        FilterType.GRAYSCALE: tr("filter_grayscale", "Оттенки серого (Чёрно-белый)"),
        FilterType.BLUR: tr("filter_blur", "Мягкое размытие (Блюр)"),
        FilterType.PIXELATE: tr("filter_pixelate", "Мозаика (Зернистость)"),
        FilterType.INVERT: tr("filter_invert", "Инверсия цветов (Негатив)"),
        FilterType.VIBRANT: tr("filter_vibrant", "Повышенная контрастность"),
        FilterType.SEPIA: tr("filter_sepia", "Тёплая сепия (Винтаж)"),
        FilterType.MAGNIFIER: tr("filter_magnifier", "Увеличение (Лупа / Зум)")
    }

FILTER_NAMES = {
    FilterType.NONE: "Без фильтра",
    FilterType.GRAYSCALE: "Оттенки серого (Чёрно-белый)",
    FilterType.INVERT: "Инверсия цветов (Негатив)",
    FilterType.BLUR: "Мягкое размытие (Блюр)",
    FilterType.PIXELATE: "Пикселизация (Цензура)",
    FilterType.VIBRANT: "Повышенная контрастность",
    FilterType.SEPIA: "Тёплая сепия (Винтаж)",
    FilterType.MAGNIFIER: "Увеличение (Лупа)"
}

def apply_filter(
    frame_bgr: np.ndarray,
    filter_type: str,
    filter_params: dict | None = None,
    *,
    blur_radius: int = 15,
    pixel_size: int = 12,
    **_kwargs,
) -> np.ndarray:
    """
    Применяет выбранный фильтр к кадру в формате BGR (uint8 numpy array).
    """
    if frame_bgr is None or frame_bgr.size == 0 or filter_type in (FilterType.NONE, "normal"):
        return frame_bgr
    if filter_type == "mosaic":
        filter_type = FilterType.PIXELATE

    if isinstance(filter_params, dict):
        if "blur_radius" in filter_params and blur_radius == 15:
            blur_radius = filter_params["blur_radius"]
        if "pixel_size" in filter_params and pixel_size == 12:
            pixel_size = filter_params["pixel_size"]
        merged_kwargs = dict(filter_params)
        merged_kwargs.update(_kwargs)
        _kwargs = merged_kwargs

    try:
        import cv2
        import numpy as np

        if filter_type == FilterType.GRAYSCALE:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)

        elif filter_type == FilterType.INVERT:
            return cv2.bitwise_not(frame_bgr)

        elif filter_type == FilterType.BLUR:
            # Размер ядра должен быть нечетным. Параметр меняется из UI
            # и применяется одинаково к предпросмотру и к записи.
            radius = max(1, min(99, int(blur_radius)))
            kernel = radius * 2 + 1
            return cv2.GaussianBlur(frame_bgr, (kernel, kernel), 0)

        elif filter_type == FilterType.PIXELATE:
            h, w = frame_bgr.shape[:2]
            block = max(2, min(64, int(pixel_size)))
            small = cv2.resize(frame_bgr, (max(1, w // block), max(1, h // block)), interpolation=cv2.INTER_LINEAR)
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

        elif filter_type in (FilterType.MAGNIFIER, "magnifier"):
            zoom = max(1.2, min(5.0, float(_kwargs.get("zoom_factor", _kwargs.get("intensity", 20) / 10.0 if _kwargs.get("intensity", 0) > 10 else 2.0))))
            h, w = frame_bgr.shape[:2]
            ch = max(2, int(round(h / zoom)))
            cw = max(2, int(round(w / zoom)))
            cy = max(0, (h - ch) // 2)
            cx = max(0, (w - cw) // 2)
            cropped = frame_bgr[cy:cy + ch, cx:cx + cw]
            return cv2.resize(cropped, (w, h), interpolation=cv2.INTER_LANCZOS4)

    except Exception as e:
        print(f"[ImageFilters] Ошибка применения фильтра {filter_type}: {e}")

    return frame_bgr
