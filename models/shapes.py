# -*- coding: utf-8 -*-
"""
Векторные геометрические фигуры, аннотации и инструмент мозаики (цензуры).
Поддерживает перемещение фигур правой кнопкой мыши (hit_test / translate),
разнообразные виды стрелок (classic, barbed, double, stealth, dashed)
и аккуратное заполнение эффектом мозаики без белых артефактов на границах.
"""

import math
import uuid
import copy
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QPainter, QPen, QColor, QBrush, QFont, QFontMetrics, QPolygonF,
    QPainterPath, QPixmap, QImage, QPainterPathStroker, QLinearGradient,
    QTransform
)
from utils.i18n import tr


def point_to_segment_distance(p: QPointF, a: QPointF, b: QPointF) -> float:
    """Вычисляет кратчайшее евклидово расстояние от точки p до отрезка [a, b]."""
    ab = b - a
    ab_len_sq = ab.x() * ab.x() + ab.y() * ab.y()
    if ab_len_sq < 1e-6:
        return math.hypot(p.x() - a.x(), p.y() - a.y())
    t = ((p.x() - a.x()) * ab.x() + (p.y() - a.y()) * ab.y()) / ab_len_sq
    t = max(0.0, min(1.0, t))
    proj = a + ab * t
    return math.hypot(p.x() - proj.x(), p.y() - proj.y())


def get_pixelated_pixmap(source_pixmap: QPixmap, pixel_size: int = 5) -> QPixmap:
    """
    Генерирует аккуратную пикселизированную версию исходного фонового изображения.
    Размер блока по умолчанию уменьшен в 2.5-3 раза (5 px) для детальной мозаики текста.
    """
    if source_pixmap is None or source_pixmap.isNull():
        return None
    w, h = source_pixmap.width(), source_pixmap.height()
    if w <= 0 or h <= 0:
        return None
    ps = max(3, min(24, pixel_size))
    block_w = max(1, w // ps)
    block_h = max(1, h // ps)
    small = source_pixmap.scaled(block_w, block_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.FastTransformation)


def get_blurred_pixmap(source_pixmap: QPixmap, blur_radius: int = 15) -> QPixmap:
    """
    Генерирует аккуратно размытую версию исходного фонового изображения (Gaussian/Smooth blur).
    """
    if source_pixmap is None or source_pixmap.isNull():
        return None
    w, h = source_pixmap.width(), source_pixmap.height()
    if w <= 0 or h <= 0:
        return None
    rad = max(2, min(50, blur_radius))
    scale_factor = max(2, rad // 2)
    small_w = max(1, w // scale_factor)
    small_h = max(1, h // scale_factor)
    small = source_pixmap.scaled(small_w, small_h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return small.scaled(w, h, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def get_filtered_pixmap(source_pixmap: QPixmap, filter_type: str, intensity: int = 10) -> QPixmap:
    """
    Применяет указанный эффект к фрагменту изображения:
    'mosaic'/'pixelate', 'blur', 'grayscale', 'invert', 'vibrant', 'sepia'.
    """
    if source_pixmap is None or source_pixmap.isNull():
        return None
    ft = str(filter_type).lower()
    if ft in ("mosaic", "pixelate"):
        return get_pixelated_pixmap(source_pixmap, intensity)
    elif ft == "blur":
        return get_blurred_pixmap(source_pixmap, intensity)
    else:
        try:
            from utils.image_filters import apply_filter
            import numpy as np
            qimg = source_pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
            w, h = qimg.width(), qimg.height()
            bpl = qimg.bytesPerLine()
            ptr = qimg.bits()
            ptr.setsize(h * bpl)
            arr_bgra = np.frombuffer(ptr, np.uint8).reshape((h, bpl // 4, 4))[:, :w, :]
            arr_bgr = arr_bgra[:, :, :3]
            res_bgr = apply_filter(arr_bgr.copy(), ft)
            res_bgra = np.empty((h, w, 4), dtype=np.uint8)
            res_bgra[:, :, :3] = res_bgr
            res_bgra[:, :, 3] = 255
            res_img = QImage(res_bgra.data, w, h, res_bgra.strides[0], QImage.Format.Format_RGB32).copy()
            return QPixmap.fromImage(res_img)
        except Exception:
            return source_pixmap


class BaseShape:
    def __init__(self, color="#FF2E2E", stroke_width=4):
        self.id = str(uuid.uuid4())[:8]
        self.color = color
        self.stroke_width = stroke_width
        self.visible = True
        self.name = tr("obj_shape", "Фигура")
        self.is_gradient = False
        self.gradient_color1 = color
        self.gradient_color2 = "#00C0FF"
        self.fill_alpha = 255
        self.pixel_size = 8
        self.blur_radius = 15
        self.rotation = 0.0

    @property
    def is_mosaic(self) -> bool:
        return str(self.color).lower() == "mosaic"

    @property
    def is_blur(self) -> bool:
        return str(self.color).lower() == "blur"

    def get_fill_brush(self, rect: QRectF) -> QBrush:
        """Создает кисть заливки (сплошной цвет или двухцветный градиент с настраиваемой прозрачностью)."""
        alpha = getattr(self, "fill_alpha", 255)
        if getattr(self, "is_gradient", False) and getattr(self, "gradient_color2", None):
            r = rect.normalized()
            grad = QLinearGradient(r.topLeft(), r.bottomRight())
            c1 = QColor(getattr(self, "gradient_color1", self.color))
            c2 = QColor(self.gradient_color2)
            c1.setAlpha(alpha)
            c2.setAlpha(alpha)
            grad.setColorAt(0.0, c1)
            grad.setColorAt(1.0, c2)
            return QBrush(grad)
        else:
            c = QColor(self.color)
            c.setAlpha(alpha)
            return QBrush(c)

    def clone(self):
        """Создает независимую копию фигуры с новым уникальным идентификатором."""
        new_shape = copy.copy(self)
        new_shape.id = str(uuid.uuid4())[:8]
        return new_shape

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        """Проверяет попадание точки pt в фигуру для перетаскивания правой кнопкой мыши."""
        return False

    def hit_test_rotated(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        """Проверяет попадание с учетом угла поворота фигуры."""
        rot = getattr(self, "rotation", 0.0)
        if rot != 0.0:
            c = self.get_bounding_rect().center()
            rad = math.radians(-rot)
            cos_a = math.cos(rad)
            sin_a = math.sin(rad)
            dx = pt.x() - c.x()
            dy = pt.y() - c.y()
            unrot_pt = QPointF(c.x() + dx * cos_a - dy * sin_a, c.y() + dx * sin_a + dy * cos_a)
            return self.hit_test(unrot_pt, tolerance)
        return self.hit_test(pt, tolerance)

    def translate(self, dx: float, dy: float):
        """Перемещает фигуру на вектор (dx, dy)."""
        pass

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        """Масштабирует фигуру относительно опорной точки origin."""
        pass

    def rotate_by(self, angle_delta: float):
        """Поворачивает фигуру на угол angle_delta (в градусах)."""
        self.rotation = (getattr(self, "rotation", 0.0) + angle_delta) % 360.0

    def get_bounding_rect(self) -> QRectF:
        """Возвращает ограничивающий прямоугольник (bounding box) фигуры."""
        return QRectF()

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        raise NotImplementedError


class PenShape(BaseShape):
    def __init__(self, color="#FF2E2E", stroke_width=4, is_highlighter=False, alpha: int = 95):
        super().__init__(color, stroke_width)
        self.path = QPainterPath()
        self.points = []
        self.is_highlighter = is_highlighter
        self.alpha = max(10, min(255, alpha))
        self.name = tr("obj_highlighter", "Маркер") if is_highlighter else tr("obj_pen", "Карандаш")

    def add_point(self, pt: QPointF):
        if not self.points:
            self.points.append(pt)
            self.path.moveTo(pt)
        else:
            last = self.points[-1]
            if abs(pt.x() - last.x()) + abs(pt.y() - last.y()) >= 1.5:
                self.points.append(pt)
                self.path.lineTo(pt)

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible or len(self.points) < 2:
            return False
        max_dist = max(tolerance, self.stroke_width / 2.0 + 3.0)
        for i in range(len(self.points) - 1):
            if point_to_segment_distance(pt, self.points[i], self.points[i+1]) <= max_dist:
                return True
        return False

    def translate(self, dx: float, dy: float):
        delta = QPointF(dx, dy)
        self.points = [p + delta for p in self.points]
        self.path = QPainterPath()
        if self.points:
            self.path.moveTo(self.points[0])
            for p in self.points[1:]:
                self.path.lineTo(p)

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        self.points = [
            QPointF(origin.x() + (p.x() - origin.x()) * sx, origin.y() + (p.y() - origin.y()) * sy)
            for p in self.points
        ]
        self.path = QPainterPath()
        if self.points:
            self.path.moveTo(self.points[0])
            for p in self.points[1:]:
                self.path.lineTo(p)

    def get_bounding_rect(self) -> QRectF:
        if not self.points:
            return QRectF()
        br = self.path.boundingRect()
        effective_width = max(16, self.stroke_width * 2) if (self.is_mosaic or self.is_blur) else (max(12, self.stroke_width) if self.is_highlighter else self.stroke_width)
        pad = max(float(effective_width) / 2.0, 4.0)
        return br.adjusted(-pad, -pad, pad, pad)

    def clone(self):
        new_shape = super().clone()
        new_shape.points = [QPointF(p) for p in self.points]
        new_shape.path = QPainterPath(self.path)
        new_shape.rotation = getattr(self, "rotation", 0.0)
        return new_shape

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        if not self.visible or self.path.isEmpty():
            return
        painter.save()
        rot = getattr(self, "rotation", 0.0)
        if rot != 0.0:
            c = self.get_bounding_rect().center() - offset
            painter.translate(c)
            painter.rotate(rot)
            painter.translate(-c)
        shifted_path = self.path.translated(-offset.x(), -offset.y())
        effective_width = max(16, self.stroke_width * 2) if (self.is_mosaic or self.is_blur) else (max(12, self.stroke_width) if self.is_highlighter else self.stroke_width)

        if (self.is_mosaic or self.is_blur) and source_pixmap is not None:
            stroker = QPainterPathStroker()
            stroker.setWidth(effective_width)
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            stroker.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            clip_path = stroker.createStroke(shifted_path)

            px_size = getattr(self, "pixel_size", 8)
            blur_rad = getattr(self, "blur_radius", 15)
            censor_pix = get_pixelated_pixmap(source_pixmap, px_size) if self.is_mosaic else get_blurred_pixmap(source_pixmap, blur_rad)
            if censor_pix is not None:
                painter.setClipPath(clip_path)
                painter.drawPixmap(-int(offset.x()), -int(offset.y()), censor_pix)
                painter.setClipping(False)
        else:
            if self.is_highlighter:
                c = QColor(self.color)
                c.setAlpha(self.alpha)
                pen = QPen(c, effective_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(shifted_path)
            elif self.is_mosaic:
                c = QColor(56, 189, 248, 160)
                pen = QPen(c, effective_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(shifted_path)
            elif self.is_blur:
                c = QColor(147, 197, 253, 160)
                pen = QPen(c, effective_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(shifted_path)
            else:
                c = QColor(self.color)
                pen = QPen(c, effective_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(shifted_path)

        painter.restore()


class LineShape(BaseShape):
    def __init__(self, p1: QPointF, p2: QPointF, color="#FF2E2E", stroke_width=4, line_style="solid"):
        super().__init__(color, stroke_width)
        self.p1 = p1
        self.p2 = p2
        self.line_style = line_style  # "solid", "dashed", "dotted"
        self.name = tr("obj_line", "Линия")

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible:
            return False
        max_dist = max(tolerance, self.stroke_width / 2.0 + 3.0)
        return point_to_segment_distance(pt, self.p1, self.p2) <= max_dist

    def translate(self, dx: float, dy: float):
        delta = QPointF(dx, dy)
        self.p1 += delta
        self.p2 += delta

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        self.p1 = QPointF(origin.x() + (self.p1.x() - origin.x()) * sx, origin.y() + (self.p1.y() - origin.y()) * sy)
        self.p2 = QPointF(origin.x() + (self.p2.x() - origin.x()) * sx, origin.y() + (self.p2.y() - origin.y()) * sy)

    def get_bounding_rect(self) -> QRectF:
        r = QRectF(self.p1, self.p2).normalized()
        effective_width = max(16, self.stroke_width * 2) if (self.is_mosaic or self.is_blur) else self.stroke_width
        pad = max(float(effective_width) / 2.0, 4.0)
        return r.adjusted(-pad, -pad, pad, pad)

    def clone(self):
        new_shape = super().clone()
        new_shape.p1 = QPointF(self.p1)
        new_shape.p2 = QPointF(self.p2)
        new_shape.rotation = getattr(self, "rotation", 0.0)
        return new_shape

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        if not self.visible:
            return
        painter.save()
        rot = getattr(self, "rotation", 0.0)
        if rot != 0.0:
            c = self.get_bounding_rect().center() - offset
            painter.translate(c)
            painter.rotate(rot)
            painter.translate(-c)
        p1 = self.p1 - offset
        p2 = self.p2 - offset
        effective_width = max(16, self.stroke_width * 2) if (self.is_mosaic or self.is_blur) else self.stroke_width

        if (self.is_mosaic or self.is_blur) and source_pixmap is not None:
            line_path = QPainterPath()
            line_path.moveTo(p1)
            line_path.lineTo(p2)
            stroker = QPainterPathStroker()
            stroker.setWidth(effective_width)
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            clip_path = stroker.createStroke(line_path)

            px_size = getattr(self, "pixel_size", 8)
            blur_rad = getattr(self, "blur_radius", 15)
            censor_pix = get_pixelated_pixmap(source_pixmap, px_size) if self.is_mosaic else get_blurred_pixmap(source_pixmap, blur_rad)
            if censor_pix is not None:
                painter.setClipPath(clip_path)
                painter.drawPixmap(-int(offset.x()), -int(offset.y()), censor_pix)
                painter.setClipping(False)
        else:
            if self.is_mosaic:
                pen = QPen(QColor(56, 189, 248, 160), effective_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            elif self.is_blur:
                pen = QPen(QColor(147, 197, 253, 160), effective_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            else:
                p_style = Qt.PenStyle.DashLine if self.line_style == "dashed" else (Qt.PenStyle.DotLine if self.line_style == "dotted" else Qt.PenStyle.SolidLine)
                pen = QPen(QColor(self.color), effective_width, p_style, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(p1, p2)
        painter.restore()


class ArrowShape(BaseShape):
    def __init__(self, p1: QPointF, p2: QPointF, color="#FF2E2E", stroke_width=4, arrow_style="classic", filled=True):
        super().__init__(color, stroke_width)
        self.p1 = p1
        self.p2 = p2
        self.arrow_style = arrow_style  # "classic", "barbed", "double", "stealth", "dashed"
        self.filled = filled
        self.name = tr("obj_arrow", "Стрелка")

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible:
            return False
        max_dist = max(tolerance, self.stroke_width / 2.0 + 4.0)
        # Проверка вдоль линии
        if point_to_segment_distance(pt, self.p1, self.p2) <= max_dist:
            return True
        # Проверка у наконечников
        head_radius = max(14.0, self.stroke_width * 3.5)
        if math.hypot(pt.x() - self.p2.x(), pt.y() - self.p2.y()) <= head_radius:
            return True
        if self.arrow_style == "double" and math.hypot(pt.x() - self.p1.x(), pt.y() - self.p1.y()) <= head_radius:
            return True
        return False

    def translate(self, dx: float, dy: float):
        delta = QPointF(dx, dy)
        self.p1 += delta
        self.p2 += delta

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        self.p1 = QPointF(origin.x() + (self.p1.x() - origin.x()) * sx, origin.y() + (self.p1.y() - origin.y()) * sy)
        self.p2 = QPointF(origin.x() + (self.p2.x() - origin.x()) * sx, origin.y() + (self.p2.y() - origin.y()) * sy)

    def get_bounding_rect(self) -> QRectF:
        r = QRectF(self.p1, self.p2).normalized()
        pad = max(float(self.stroke_width) * 3.5, 14.0)
        return r.adjusted(-pad, -pad, pad, pad)

    def clone(self):
        new_shape = super().clone()
        new_shape.p1 = QPointF(self.p1)
        new_shape.p2 = QPointF(self.p2)
        new_shape.rotation = getattr(self, "rotation", 0.0)
        return new_shape

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        if not self.visible:
            return
        painter.save()
        rot = getattr(self, "rotation", 0.0)
        if rot != 0.0:
            c = self.get_bounding_rect().center() - offset
            painter.translate(c)
            painter.rotate(rot)
            painter.translate(-c)
        p1 = self.p1 - offset
        p2 = self.p2 - offset
        effective_width = max(14, self.stroke_width * 2) if (self.is_mosaic or self.is_blur) else self.stroke_width

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()
        length = math.hypot(dx, dy)

        if (self.is_mosaic or self.is_blur) and source_pixmap is not None:
            arrow_path = QPainterPath()
            arrow_path.moveTo(p1)
            arrow_path.lineTo(p2)
            stroker = QPainterPathStroker()
            stroker.setWidth(effective_width)
            stroker.setCapStyle(Qt.PenCapStyle.RoundCap)
            clip_path = stroker.createStroke(arrow_path)

            if length > 4:
                angle = math.atan2(dy, dx)
                arrow_size = max(16, effective_width * 2.5)
                arrow_angle = math.pi / 6
                pt1 = QPointF(p2.x() - arrow_size * math.cos(angle - arrow_angle), p2.y() - arrow_size * math.sin(angle - arrow_angle))
                pt2 = QPointF(p2.x() - arrow_size * math.cos(angle + arrow_angle), p2.y() - arrow_size * math.sin(angle + arrow_angle))
                head_poly = QPolygonF([p2, pt1, pt2])
                head_path = QPainterPath()
                head_path.addPolygon(head_poly)
                clip_path = clip_path.united(head_path)

            px_size = getattr(self, "pixel_size", 8)
            blur_rad = getattr(self, "blur_radius", 15)
            censor_pix = get_pixelated_pixmap(source_pixmap, px_size) if self.is_mosaic else get_blurred_pixmap(source_pixmap, blur_rad)
            if censor_pix is not None:
                painter.setClipPath(clip_path)
                painter.drawPixmap(-int(offset.x()), -int(offset.y()), censor_pix)
                painter.setClipping(False)
        else:
            col = QColor(56, 189, 248, 160) if self.is_mosaic else (QColor(147, 197, 253, 160) if self.is_blur else QColor(self.color))
            pen_style = Qt.PenStyle.DashLine if (self.arrow_style == "dashed" or self.is_mosaic or self.is_blur) else Qt.PenStyle.SolidLine
            pen = QPen(col, effective_width, pen_style, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(p1, p2)

            if length > 4:
                angle = math.atan2(dy, dx)
                arrow_size = max(12, self.stroke_width * 3.5)
                arrow_angle = math.pi / 6

                # Функция отрисовки наконечника
                def draw_arrow_head(tip: QPointF, ang: float, style: str):
                    pt1 = QPointF(tip.x() - arrow_size * math.cos(ang - arrow_angle), tip.y() - arrow_size * math.sin(ang - arrow_angle))
                    pt2 = QPointF(tip.x() - arrow_size * math.cos(ang + arrow_angle), tip.y() - arrow_size * math.sin(ang + arrow_angle))

                    if style == "barbed":
                        # Открытые усики
                        h_pen = QPen(col, effective_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                        painter.setPen(h_pen)
                        painter.drawLine(tip, pt1)
                        painter.drawLine(tip, pt2)
                    elif style == "stealth":
                        # Заостренная форма с вырезанной задней гранью
                        indent = QPointF(tip.x() - arrow_size * 0.65 * math.cos(ang), tip.y() - arrow_size * 0.65 * math.sin(ang))
                        poly = QPolygonF([tip, pt1, indent, pt2])
                        if self.filled:
                            painter.setPen(Qt.PenStyle.NoPen)
                            painter.setBrush(QBrush(col))
                        else:
                            h_pen = QPen(col, effective_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                            painter.setPen(h_pen)
                            painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawPolygon(poly)
                    else:
                        # Классический треугольник (classic / dashed / double)
                        poly = QPolygonF([tip, pt1, pt2])
                        if self.filled:
                            painter.setPen(Qt.PenStyle.NoPen)
                            painter.setBrush(QBrush(col))
                        else:
                            h_pen = QPen(col, effective_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                            painter.setPen(h_pen)
                            painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawPolygon(poly)

                # Основной наконечник (у точки p2)
                draw_arrow_head(p2, angle, self.arrow_style)

                # Дополнительный наконечник у p1 (для двусторонней стрелки)
                if self.arrow_style == "double":
                    draw_arrow_head(p1, angle + math.pi, self.arrow_style)

        painter.restore()


class RectangleShape(BaseShape):
    def __init__(self, rect: QRectF, color="#FF2E2E", stroke_width=4, filled=False, fill_alpha=255,
                 is_gradient=False, gradient_color1="#FF2E2E", gradient_color2="#00C0FF", is_rounded=False):
        super().__init__(color, stroke_width)
        self.rect = rect
        self.filled = filled
        self.fill_alpha = fill_alpha
        self.is_gradient = is_gradient
        self.gradient_color1 = gradient_color1 or color
        self.gradient_color2 = gradient_color2
        self.is_rounded = is_rounded
        self.name = tr("obj_filled_rect", "Залитый прямоугольник") if filled else tr("obj_rect", "Прямоугольник")

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible:
            return False
        r = self.rect.normalized()
        if self.filled or self.is_mosaic or self.is_blur:
            return r.contains(pt)
        # Проверка 4 границ контура
        max_dist = max(tolerance, self.stroke_width / 2.0 + 3.0)
        tl, tr, bl, br = r.topLeft(), r.topRight(), r.bottomLeft(), r.bottomRight()
        return (
            point_to_segment_distance(pt, tl, tr) <= max_dist or
            point_to_segment_distance(pt, tr, br) <= max_dist or
            point_to_segment_distance(pt, br, bl) <= max_dist or
            point_to_segment_distance(pt, bl, tl) <= max_dist
        )

    def translate(self, dx: float, dy: float):
        self.rect.translate(dx, dy)

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        r = self.rect.normalized()
        nl = origin.x() + (r.left() - origin.x()) * sx
        nt = origin.y() + (r.top() - origin.y()) * sy
        nr = origin.x() + (r.right() - origin.x()) * sx
        nb = origin.y() + (r.bottom() - origin.y()) * sy
        self.rect = QRectF(min(nl, nr), min(nt, nb), abs(nr - nl), abs(nb - nt))

    def get_bounding_rect(self) -> QRectF:
        r = self.rect.normalized()
        pad = max(float(self.stroke_width) / 2.0, 2.0)
        return r.adjusted(-pad, -pad, pad, pad)

    def clone(self):
        new_shape = super().clone()
        new_shape.rect = QRectF(self.rect)
        new_shape.rotation = getattr(self, "rotation", 0.0)
        return new_shape

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        if not self.visible:
            return
        r = self.rect.translated(-offset.x(), -offset.y()).normalized()
        rot = getattr(self, "rotation", 0.0)

        if (self.is_mosaic or self.is_blur) and source_pixmap is not None:
            px_size = getattr(self, "pixel_size", 8)
            blur_rad = getattr(self, "blur_radius", 15)
            censor_pix = get_pixelated_pixmap(source_pixmap, px_size) if self.is_mosaic else get_blurred_pixmap(source_pixmap, blur_rad)
            if censor_pix is not None:
                clip_path = QPainterPath()
                if rot != 0.0:
                    c = r.center()
                    t = QTransform()
                    t.translate(c.x(), c.y())
                    t.rotate(rot)
                    t.translate(-c.x(), -c.y())
                    local_p = QPainterPath()
                    if self.is_rounded:
                        local_p.addRoundedRect(r, 6, 6)
                    else:
                        local_p.addRect(r)
                    clip_path = t.map(local_p)
                else:
                    if self.is_rounded:
                        clip_path.addRoundedRect(r, 6, 6)
                    else:
                        clip_path.addRect(r)

                painter.save()
                painter.setClipPath(clip_path)
                painter.drawPixmap(-int(offset.x()), -int(offset.y()), censor_pix)
                painter.restore()
                return

        painter.save()
        if rot != 0.0:
            c = self.get_bounding_rect().center() - offset
            painter.translate(c)
            painter.rotate(rot)
            painter.translate(-c)

        if self.is_mosaic:
            pen = QPen(QColor(56, 189, 248, 180), self.stroke_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(QColor(15, 23, 42, 120))
        elif self.is_blur:
            pen = QPen(QColor(147, 197, 253, 180), self.stroke_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            painter.setBrush(QColor(30, 41, 59, 120))
        else:
            pen = QPen(QColor(self.color), self.stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.SquareCap, Qt.PenJoinStyle.MiterJoin)
            painter.setPen(pen)
            if self.filled:
                painter.setBrush(self.get_fill_brush(r))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
        if self.is_rounded:
            painter.drawRoundedRect(r, 6, 6)
        else:
            painter.drawRect(r)

        painter.restore()


class CircleShape(BaseShape):
    def __init__(self, rect: QRectF, color="#FF2E2E", stroke_width=4, filled=False, fill_alpha=255,
                 is_gradient=False, gradient_color1="#FF2E2E", gradient_color2="#00C0FF"):
        super().__init__(color, stroke_width)
        self.rect = rect
        self.filled = filled
        self.fill_alpha = fill_alpha
        self.is_gradient = is_gradient
        self.gradient_color1 = gradient_color1 or color
        self.gradient_color2 = gradient_color2
        self.name = tr("obj_filled_circle", "Залитый круг") if filled else tr("obj_circle", "Круг / Овал")

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible:
            return False
        r = self.rect.normalized()
        rx = r.width() / 2.0
        ry = r.height() / 2.0
        if rx <= 1 or ry <= 1:
            return False
        cx = r.center().x()
        cy = r.center().y()
        norm_dist = ((pt.x() - cx) / rx) ** 2 + ((pt.y() - cy) / ry) ** 2
        if self.filled or self.is_mosaic or self.is_blur:
            return norm_dist <= 1.05
        # Граница
        dist_px = abs(math.sqrt(norm_dist) - 1.0) * min(rx, ry)
        return dist_px <= max(tolerance, self.stroke_width / 2.0 + 3.0)

    def translate(self, dx: float, dy: float):
        self.rect.translate(dx, dy)

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        r = self.rect.normalized()
        nl = origin.x() + (r.left() - origin.x()) * sx
        nt = origin.y() + (r.top() - origin.y()) * sy
        nr = origin.x() + (r.right() - origin.x()) * sx
        nb = origin.y() + (r.bottom() - origin.y()) * sy
        self.rect = QRectF(min(nl, nr), min(nt, nb), abs(nr - nl), abs(nb - nt))

    def get_bounding_rect(self) -> QRectF:
        r = self.rect.normalized()
        pad = max(float(self.stroke_width) / 2.0, 2.0)
        return r.adjusted(-pad, -pad, pad, pad)

    def clone(self):
        new_shape = super().clone()
        new_shape.rect = QRectF(self.rect)
        new_shape.rotation = getattr(self, "rotation", 0.0)
        return new_shape

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        if not self.visible:
            return
        r = self.rect.translated(-offset.x(), -offset.y()).normalized()
        rot = getattr(self, "rotation", 0.0)

        if (self.is_mosaic or self.is_blur) and source_pixmap is not None:
            px_size = getattr(self, "pixel_size", 8)
            blur_rad = getattr(self, "blur_radius", 15)
            censor_pix = get_pixelated_pixmap(source_pixmap, px_size) if self.is_mosaic else get_blurred_pixmap(source_pixmap, blur_rad)
            if censor_pix is not None:
                clip_path = QPainterPath()
                if rot != 0.0:
                    c = r.center()
                    t = QTransform()
                    t.translate(c.x(), c.y())
                    t.rotate(rot)
                    t.translate(-c.x(), -c.y())
                    local_p = QPainterPath()
                    local_p.addEllipse(r)
                    clip_path = t.map(local_p)
                else:
                    clip_path.addEllipse(r)

                painter.save()
                painter.setClipPath(clip_path)
                painter.drawPixmap(-int(offset.x()), -int(offset.y()), censor_pix)
                painter.restore()
                return

        painter.save()
        if rot != 0.0:
            c = self.get_bounding_rect().center() - offset
            painter.translate(c)
            painter.rotate(rot)
            painter.translate(-c)

        if self.is_mosaic:
            pen = QPen(QColor(56, 189, 248, 180), self.stroke_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QColor(15, 23, 42, 120))
        elif self.is_blur:
            pen = QPen(QColor(147, 197, 253, 180), self.stroke_width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(QColor(30, 41, 59, 120))
        else:
            pen = QPen(QColor(self.color), self.stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            if self.filled:
                painter.setBrush(self.get_fill_brush(r))
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(r)

        painter.restore()


class TextShape(BaseShape):
    def __init__(self, pos: QPointF, text: str = "", color="#FF2E2E", font_size=18,
                 font_family="Segoe UI", is_bold=True, is_italic=False, is_underline=False,
                 has_bg=False, bg_color="#000000", bg_alpha=180):
        super().__init__(color, stroke_width=1)
        self.pos = pos
        self.text = text
        self.font_size = font_size
        self.font_family = font_family
        self.is_bold = is_bold
        self.is_italic = is_italic
        self.is_underline = is_underline
        self.has_bg = has_bg
        self.bg_color = bg_color
        self.bg_alpha = bg_alpha
        self.name = tr("obj_text", "Текст")

    def hit_test_rotated(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible:
            return False
        rot = getattr(self, "rotation", 0.0)
        br = self.get_bounding_rect()
        if rot == 0.0:
            return br.adjusted(-tolerance, -tolerance, tolerance, tolerance).contains(pt)
        c = br.center()
        rad = math.radians(-rot)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)
        dx = pt.x() - c.x()
        dy = pt.y() - c.y()
        unrot_pt = QPointF(c.x() + dx * cos_a - dy * sin_a, c.y() + dx * sin_a + dy * cos_a)
        return br.adjusted(-tolerance, -tolerance, tolerance, tolerance).contains(unrot_pt)

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        return self.hit_test_rotated(pt, tolerance)

    def translate(self, dx: float, dy: float):
        delta = QPointF(dx, dy)
        self.pos += delta

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        self.pos = QPointF(origin.x() + (self.pos.x() - origin.x()) * sx, origin.y() + (self.pos.y() - origin.y()) * sy)
        avg_scale = (abs(sx) + abs(sy)) / 2.0
        new_size = int(round(self.font_size * avg_scale))
        self.font_size = max(8, min(120, new_size))

    def get_bounding_rect(self) -> QRectF:
        font = QFont(self.font_family, self.font_size)
        font.setBold(self.is_bold)
        font.setItalic(getattr(self, "is_italic", False))
        font.setUnderline(self.is_underline)
        fm = QFontMetrics(font)
        br = fm.boundingRect(self.text if self.text else " ")
        return QRectF(self.pos.x() + br.left() - 4, self.pos.y() + br.top() - 3, max(12.0, br.width() + 8), max(12.0, br.height() + 6))

    def clone(self):
        new_shape = super().clone()
        new_shape.pos = QPointF(self.pos)
        new_shape.text = self.text
        new_shape.font_size = self.font_size
        new_shape.font_family = self.font_family
        new_shape.is_bold = self.is_bold
        new_shape.is_italic = getattr(self, "is_italic", False)
        new_shape.is_underline = self.is_underline
        new_shape.has_bg = self.has_bg
        new_shape.bg_color = self.bg_color
        new_shape.bg_alpha = self.bg_alpha
        new_shape.rotation = getattr(self, "rotation", 0.0)
        return new_shape

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        if not self.visible or not self.text:
            return
        painter.save()
        rot = getattr(self, "rotation", 0.0)
        if rot != 0.0:
            c = self.get_bounding_rect().center() - offset
            painter.translate(c)
            painter.rotate(rot)
            painter.translate(-c)
        p = self.pos - offset
        font = QFont(self.font_family, self.font_size)
        font.setBold(self.is_bold)
        font.setItalic(getattr(self, "is_italic", False))
        font.setUnderline(self.is_underline)
        painter.setFont(font)

        fm = QFontMetrics(font)
        br = fm.boundingRect(self.text)

        if self.has_bg:
            bg_rect = QRectF(p.x() + br.left() - 4, p.y() + br.top() - 3, br.width() + 8, br.height() + 6)
            bg_c = QColor(self.bg_color)
            bg_c.setAlpha(self.bg_alpha)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bg_c)
            painter.drawRoundedRect(bg_rect, 4, 4)

        if (self.is_mosaic or self.is_blur) and source_pixmap is not None:
            text_rect = QRectF(p.x() + br.left() - 4, p.y() + br.top() - 3, br.width() + 8, br.height() + 6)
            px_size = getattr(self, "pixel_size", 8)
            blur_rad = getattr(self, "blur_radius", 15)
            censor_pix = get_pixelated_pixmap(source_pixmap, px_size) if self.is_mosaic else get_blurred_pixmap(source_pixmap, blur_rad)
            if censor_pix is not None:
                clip_path = QPainterPath()
                clip_path.addRoundedRect(text_rect, 4, 4)
                painter.setClipPath(clip_path)
                painter.drawPixmap(-int(offset.x()), -int(offset.y()), censor_pix)
                painter.setClipping(False)
        else:
            if not self.has_bg:
                painter.setPen(QColor(0, 0, 0, 180))
                painter.drawText(int(p.x() + 1), int(p.y() + 1), self.text)

            painter.setPen(QColor(self.color))
            painter.drawText(int(p.x()), int(p.y()), self.text)

        painter.restore()


class RegionalEffectShape(BaseShape):
    """
    Инструмент применения эффекта к прямоугольной области:
    мозаика, размытие, ч/б, инверсия, повышенная контрастность, сепия.
    Работает как физическая диафрагма/апертура: вращается и масштабируется
    только граница рамки, в то время как изображение рабочего стола под ней
    остаётся неподвижным и фильтруется на месте 1:1.
    """
    def __init__(self, rect: QRectF, effect_type: str = "mosaic", intensity: int = 8, cached_pixmap: QPixmap = None):
        super().__init__(color=effect_type, stroke_width=1)
        self.rect = rect
        self.effect_type = effect_type
        self.intensity = intensity
        self.cached_pixmap = cached_pixmap
        self._cached_rect = None
        self._cached_needed_rect = None
        self._cache_key = None
        self.name = self._format_name(effect_type)

    @staticmethod
    def _format_name(etype: str) -> str:
        names = {
            "mosaic": tr("obj_mosaic", "Мозаика (Цензура)"),
            "pixelate": tr("obj_mosaic", "Мозаика (Цензура)"),
            "blur": tr("obj_blur", "Размытие (Блюр)"),
            "grayscale": tr("obj_grayscale", "Чёрно-белый (Область)"),
            "invert": tr("obj_invert", "Инверсия (Область)"),
            "vibrant": tr("obj_vibrant", "Насыщенность (Область)"),
            "sepia": tr("obj_sepia", "Сепия (Область)")
        }
        return names.get(etype, f"Effect: {etype}")

    def get_rotated_polygon(self, offset: QPointF = QPointF(0, 0)) -> QPolygonF:
        r = self.rect.translated(-offset.x(), -offset.y()).normalized()
        rot = getattr(self, "rotation", 0.0)
        if rot == 0.0:
            return QPolygonF(r)
        c = r.center()
        t = QTransform()
        t.translate(c.x(), c.y())
        t.rotate(rot)
        t.translate(-c.x(), -c.y())
        return t.map(QPolygonF(r))

    def hit_test(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible:
            return False
        return self.get_rotated_polygon().containsPoint(pt, Qt.FillRule.OddEvenFill)

    def hit_test_rotated(self, pt: QPointF, tolerance: float = 6.0) -> bool:
        if not self.visible:
            return False
        return self.get_rotated_polygon().containsPoint(pt, Qt.FillRule.OddEvenFill)

    def translate(self, dx: float, dy: float):
        self.rect.translate(dx, dy)
        self.cached_pixmap = None
        self._cached_rect = None
        self._cached_needed_rect = None
        self._cache_key = None

    def scale_from_origin(self, sx: float, sy: float, origin: QPointF):
        r = self.rect.normalized()
        nl = origin.x() + (r.left() - origin.x()) * sx
        nt = origin.y() + (r.top() - origin.y()) * sy
        nr = origin.x() + (r.right() - origin.x()) * sx
        nb = origin.y() + (r.bottom() - origin.y()) * sy
        self.rect = QRectF(min(nl, nr), min(nt, nb), abs(nr - nl), abs(nb - nt))
        self.cached_pixmap = None
        self._cached_rect = None
        self._cached_needed_rect = None
        self._cache_key = None

    def get_bounding_rect(self) -> QRectF:
        return self.rect.normalized()

    def clone(self):
        new_shape = super().clone()
        new_shape.rect = QRectF(self.rect)
        new_shape.effect_type = self.effect_type
        new_shape.intensity = self.intensity
        new_shape.rotation = getattr(self, "rotation", 0.0)
        new_shape.cached_pixmap = None
        new_shape._cached_rect = None
        new_shape._cached_needed_rect = None
        new_shape._cache_key = None
        return new_shape

    def set_intensity(self, intensity: int, background_pixmap: QPixmap = None):
        self.intensity = max(2, min(50, intensity))
        self.cached_pixmap = None
        self._cached_rect = None
        self._cached_needed_rect = None
        self._cache_key = None
        if background_pixmap is not None:
            self.update_effect(background_pixmap)

    def update_effect(self, background_pixmap: QPixmap):
        if background_pixmap is None or background_pixmap.isNull():
            self.cached_pixmap = None
            self._cached_rect = None
            self._cached_needed_rect = None
            self._cache_key = None
            return

        r = self.rect.normalized()
        if r.width() < 2 or r.height() < 2:
            self.cached_pixmap = None
            self._cached_rect = None
            self._cached_needed_rect = None
            self._cache_key = None
            return

        rot = getattr(self, "rotation", 0.0)
        global_poly = self.get_rotated_polygon(QPointF(0, 0))
        poly_br = global_poly.boundingRect().toRect()
        needed_rect = poly_br.intersected(background_pixmap.rect())

        if needed_rect.width() < 2 or needed_rect.height() < 2:
            self.cached_pixmap = None
            self._cached_rect = None
            self._cached_needed_rect = None
            self._cache_key = None
            return

        cropped = background_pixmap.copy(needed_rect)
        self.cached_pixmap = get_filtered_pixmap(cropped, self.effect_type, self.intensity)
        self._cached_needed_rect = needed_rect
        self._cached_rect = QRectF(r)
        self._cache_key = (
            needed_rect.x(), needed_rect.y(), needed_rect.width(), needed_rect.height(),
            round(rot, 2), self.intensity, self.effect_type, id(background_pixmap)
        )

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap: QPixmap = None):
        if not self.visible:
            return

        canvas_poly = self.get_rotated_polygon(offset)

        if source_pixmap is not None:
            rot = getattr(self, "rotation", 0.0)
            global_poly = self.get_rotated_polygon(QPointF(0, 0))
            poly_br = global_poly.boundingRect().toRect()

            # Определяем систему координат source_pixmap:
            # 1) Если source_pixmap полноэкранный (overlay):
            if poly_br.intersects(source_pixmap.rect()):
                needed_rect = poly_br.intersected(source_pixmap.rect())
                draw_x = needed_rect.x() - int(offset.x())
                draw_y = needed_rect.y() - int(offset.y())
            else:
                # 2) Если source_pixmap уже обрезан со смещением offset (локальный crop_pix):
                local_br = canvas_poly.boundingRect().toRect()
                needed_rect = local_br.intersected(source_pixmap.rect())
                draw_x = needed_rect.x()
                draw_y = needed_rect.y()

            cur_key = (
                needed_rect.x(), needed_rect.y(), needed_rect.width(), needed_rect.height(),
                round(rot, 2), self.intensity, self.effect_type, id(source_pixmap),
                int(offset.x()), int(offset.y())
            )

            if cur_key != getattr(self, "_cache_key", None) or self.cached_pixmap is None:
                if needed_rect.width() >= 2 and needed_rect.height() >= 2:
                    cropped = source_pixmap.copy(needed_rect)
                    self.cached_pixmap = get_filtered_pixmap(cropped, self.effect_type, self.intensity)
                    self._cached_draw_x = draw_x
                    self._cached_draw_y = draw_y
                    self._cache_key = cur_key
                else:
                    self.cached_pixmap = None
            else:
                self._cached_draw_x = draw_x
                self._cached_draw_y = draw_y

        if self.cached_pixmap is not None and hasattr(self, "_cached_draw_x"):
            painter.save()
            clip_path = QPainterPath()
            clip_path.addPolygon(canvas_poly)
            painter.setClipPath(clip_path)
            painter.drawPixmap(self._cached_draw_x, self._cached_draw_y, self.cached_pixmap)
            painter.restore()
        elif source_pixmap is None:
            painter.save()
            pen_color = QColor(147, 197, 253, 200) if self.effect_type == "blur" else QColor(56, 189, 248, 200)
            brush_color = QColor(30, 41, 59, 140) if self.effect_type == "blur" else QColor(15, 23, 42, 160)
            painter.setPen(QPen(pen_color, 1.5, Qt.PenStyle.DashLine))
            painter.setBrush(brush_color)
            painter.drawPolygon(canvas_poly)
            painter.restore()


class MosaicShape(RegionalEffectShape):
    """
    Инструмент мозаичной цензуры прямоугольных блоков (аккуратный блочный блюр).
    """
    def __init__(self, rect: QRectF, pixel_size: int = 5, cached_pixmap: QPixmap = None):
        super().__init__(rect, effect_type="mosaic", intensity=pixel_size, cached_pixmap=cached_pixmap)
        self.pixel_size = self.intensity
        self.name = tr("obj_mosaic", "Мозаика (Цензура)")

    @property
    def cached_mosaic(self):
        return self.cached_pixmap

    @cached_mosaic.setter
    def cached_mosaic(self, val):
        self.cached_pixmap = val

    def set_pixel_size(self, pixel_size: int, background_pixmap: QPixmap = None):
        self.intensity = max(3, min(30, pixel_size))
        self.pixel_size = self.intensity
        self.cached_pixmap = None
        self._cached_rect = None
        self._cached_needed_rect = None
        self._cache_key = None
        if background_pixmap is not None:
            self.update_effect(background_pixmap)

    def update_mosaic(self, background_pixmap: QPixmap):
        self.update_effect(background_pixmap)

    def clone(self):
        new_shape = super().clone()
        new_shape.pixel_size = self.pixel_size
        return new_shape


class BlurShape(RegionalEffectShape):
    """
    Инструмент гладкого размытия (Gaussian Blur / Цензура).
    """
    def __init__(self, rect: QRectF, blur_radius: int = 15, cached_pixmap: QPixmap = None):
        super().__init__(rect, effect_type="blur", intensity=blur_radius, cached_pixmap=cached_pixmap)
        self.blur_radius = self.intensity
        self.name = tr("obj_blur", "Размытие (Блюр)")

    @property
    def cached_blur(self):
        return self.cached_pixmap

    @cached_blur.setter
    def cached_blur(self, val):
        self.cached_pixmap = val

    def set_blur_radius(self, radius: int, background_pixmap: QPixmap = None):
        self.intensity = max(3, min(50, radius))
        self.blur_radius = self.intensity
        self.cached_pixmap = None
        self._cached_rect = None
        self._cached_needed_rect = None
        self._cache_key = None
        if background_pixmap is not None:
            self.update_effect(background_pixmap)

    def update_blur(self, background_pixmap: QPixmap):
        self.update_effect(background_pixmap)

    def clone(self):
        new_shape = super().clone()
        new_shape.blur_radius = self.blur_radius
        return new_shape

