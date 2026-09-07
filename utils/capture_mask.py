# -*- coding: utf-8 -*-
"""Общие операции с маской захвата для скриншотов, MP4 и GIF."""

from PyQt6.QtCore import QPointF, QRectF
from PyQt6.QtGui import QImage, QPainter, QPainterPath, QTransform


def _mask_list(shapes):
    """Нормализует одну маску или список масок к списку валидных контуров."""
    if isinstance(shapes, (list, tuple)):
        values = shapes
    else:
        values = (shapes,)
    return [shape for shape in values if getattr(shape, "is_capture_mask", False)]


def mask_path_for_frame(shape, width: int, height: int, region) -> QPainterPath:
    """Переводит одну или несколько масок из координат overlay в кадр."""
    shapes = _mask_list(shape)
    if not shapes:
        return QPainterPath()
    rx, ry, rw, rh = [float(value) for value in region]
    if rw <= 0 or rh <= 0 or width <= 0 or height <= 0:
        return QPainterPath()
    transform = QTransform()
    transform.scale(float(width) / rw, float(height) / rh)
    # QTransform применяет операции к точкам в порядке, обратном записи:
    # сначала масштабируем координаты overlay, затем переносим начало региона.
    # Иначе при отличающихся размерах региона маска смещается вправо-вниз.
    transform.translate(-rx, -ry)
    combined = QPainterPath()
    for item in shapes:
        mapped = transform.map(item.path())
        combined = mapped if combined.isEmpty() else combined.united(mapped)
    return combined


def create_mask_image(shape, width: int, height: int, region) -> QImage:
    """Создаёт одноканальную маску: внутри контура 255, снаружи 0."""
    mask = QImage(max(1, int(width)), max(1, int(height)), QImage.Format.Format_Grayscale8)
    mask.fill(0)
    path = mask_path_for_frame(shape, mask.width(), mask.height(), region)
    if path.isEmpty():
        return mask
    painter = QPainter(mask)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.fillPath(path, 255)
    painter.end()
    return mask


def apply_mask_to_qimage(image: QImage, shape, region) -> QImage:
    """Оставляет пиксели внутри маски, внешний прямоугольник делает прозрачным."""
    if image.isNull() or not _mask_list(shape):
        return image
    result = QImage(image.size(), QImage.Format.Format_ARGB32)
    result.fill(0)
    path = mask_path_for_frame(shape, image.width(), image.height(), region)
    if path.isEmpty():
        return result
    painter = QPainter(result)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setClipPath(path)
    painter.drawImage(0, 0, image)
    painter.end()
    return result


def apply_mask_to_bgr(frame, shape, region):
    """Оставляет объединение масок в BGR-кадре, остальное заполняет чёрным."""
    if frame is None or not _mask_list(shape):
        return frame
    import cv2
    import numpy as np

    height, width = frame.shape[:2]
    qmask = create_mask_image(shape, width, height, region)
    bits = qmask.constBits()
    bits.setsize(qmask.bytesPerLine() * qmask.height())
    mask = np.frombuffer(bits, dtype=np.uint8).reshape((qmask.height(), qmask.bytesPerLine()))[:, :width]
    result = frame.copy()
    result[mask == 0] = 0
    return result
