# -*- coding: utf-8 -*-
"""
Интерактивная рамка трансформации фигур в стиле Adobe Photoshop / Figma.
Обеспечивает выделение активной фигуры контуром, 8 маркеров масштабирования
(сжатие / растяжение по осям и углам) и маркер свободного вращения.
"""

import math
from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QPainter, QPen, QColor, QBrush, QCursor, QPolygonF
from models.shapes import BaseShape


def rotate_point(pt: QPointF, center: QPointF, angle_deg: float) -> QPointF:
    """Поворачивает точку pt вокруг center на angle_deg градусов по часовой стрелке."""
    if angle_deg == 0:
        return pt
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    dx = pt.x() - center.x()
    dy = pt.y() - center.y()
    return QPointF(center.x() + dx * cos_a - dy * sin_a, center.y() + dx * sin_a + dy * cos_a)


def unrotate_point(pt: QPointF, center: QPointF, angle_deg: float) -> QPointF:
    """Поворачивает точку pt вокруг center на -angle_deg градусов (обратный поворот)."""
    return rotate_point(pt, center, -angle_deg)


class HandleType:
    NONE = 0
    TOP_LEFT = 1
    TOP = 2
    TOP_RIGHT = 3
    RIGHT = 4
    BOTTOM_RIGHT = 5
    BOTTOM = 6
    BOTTOM_LEFT = 7
    LEFT = 8
    ROTATE = 9
    INSIDE = 10


class ShapeTransformBox:
    """
    Управляет отрисовкой рамки трансформации вокруг выбранной фигуры
    и интерактивным изменением размера (сжатие/растяжение) и вращением.
    """
    HANDLE_SIZE = 8.0
    ROTATE_OFFSET = 24.0

    def __init__(self, shape: BaseShape = None):
        self.shape: BaseShape | None = shape
        self.active_handle = HandleType.NONE
        self.drag_start_pos = QPointF(0, 0)
        self.initial_bounding_rect = QRectF()
        self.initial_rotation = 0.0
        self.initial_state = None

    def set_shape(self, shape: BaseShape | None):
        self.shape = shape
        self.active_handle = HandleType.NONE

    def is_active(self) -> bool:
        return self.shape is not None and self.shape.visible

    def get_handles_positions(self, offset: QPointF = QPointF(0, 0)) -> dict[int, QPointF]:
        """Возвращает экранные координаты всех маркеров с учетом текущего угла поворота."""
        if not self.is_active():
            return {}
        br = self.shape.get_bounding_rect()
        if br.isEmpty():
            return {}

        center = br.center() - offset
        rot = getattr(self.shape, "rotation", 0.0)

        # Локальные (неповернутые) координаты маркеров относительно холста
        tl = br.topLeft() - offset
        tr = br.topRight() - offset
        bl = br.bottomLeft() - offset
        br_pt = br.bottomRight() - offset

        t = QPointF(center.x(), tl.y())
        b = QPointF(center.x(), bl.y())
        l = QPointF(tl.x(), center.y())
        r = QPointF(tr.x(), center.y())
        rot_pt = QPointF(center.x(), tl.y() - self.ROTATE_OFFSET)

        # Поворачиваем вокруг центра
        return {
            HandleType.TOP_LEFT: rotate_point(tl, center, rot),
            HandleType.TOP: rotate_point(t, center, rot),
            HandleType.TOP_RIGHT: rotate_point(tr, center, rot),
            HandleType.RIGHT: rotate_point(r, center, rot),
            HandleType.BOTTOM_RIGHT: rotate_point(br_pt, center, rot),
            HandleType.BOTTOM: rotate_point(b, center, rot),
            HandleType.BOTTOM_LEFT: rotate_point(bl, center, rot),
            HandleType.LEFT: rotate_point(l, center, rot),
            HandleType.ROTATE: rotate_point(rot_pt, center, rot),
        }

    def hit_test_handle(self, pt: QPointF, offset: QPointF = QPointF(0, 0)) -> int:
        """Определяет, над каким маркером (или внутри рамки) находится курсор мыши."""
        if not self.is_active():
            return HandleType.NONE

        handles = self.get_handles_positions(offset)
        tol = self.HANDLE_SIZE + 4.0

        # Сначала проверяем маркер вращения
        if HandleType.ROTATE in handles:
            if math.hypot(pt.x() - handles[HandleType.ROTATE].x(), pt.y() - handles[HandleType.ROTATE].y()) <= tol:
                return HandleType.ROTATE

        # Проверяем маркеры изменения размера
        for h_type in [
            HandleType.TOP_LEFT, HandleType.TOP_RIGHT, HandleType.BOTTOM_LEFT, HandleType.BOTTOM_RIGHT,
            HandleType.TOP, HandleType.BOTTOM, HandleType.LEFT, HandleType.RIGHT
        ]:
            h_pos = handles.get(h_type)
            if h_pos and math.hypot(pt.x() - h_pos.x(), pt.y() - h_pos.y()) <= tol:
                return h_type

        # Проверяем попадание внутрь повернутой рамки фигуры
        br = self.shape.get_bounding_rect()
        center = br.center() - offset
        rot = getattr(self.shape, "rotation", 0.0)
        unrot_pt = unrotate_point(pt, center, rot) + offset
        if br.adjusted(-4, -4, 4, 4).contains(unrot_pt):
            return HandleType.INSIDE

        return HandleType.NONE

    def get_cursor_for_handle(self, handle_type: int) -> Qt.CursorShape:
        """Возвращает соответствующий курсор мыши для маркера с учетом угла поворота."""
        if handle_type == HandleType.NONE:
            return Qt.CursorShape.ArrowCursor
        if handle_type == HandleType.INSIDE:
            return Qt.CursorShape.SizeAllCursor
        if handle_type == HandleType.ROTATE:
            return Qt.CursorShape.PointingHandCursor

        rot = getattr(self.shape, "rotation", 0.0) if self.shape else 0.0
        # Базовые углы нормалей маркеров
        base_angles = {
            HandleType.TOP: 0,
            HandleType.TOP_RIGHT: 45,
            HandleType.RIGHT: 90,
            HandleType.BOTTOM_RIGHT: 135,
            HandleType.BOTTOM: 180,
            HandleType.BOTTOM_LEFT: 225,
            HandleType.LEFT: 270,
            HandleType.TOP_LEFT: 315,
        }
        angle = (base_angles.get(handle_type, 0) + rot) % 180.0
        if 22.5 <= angle < 67.5:
            return Qt.CursorShape.SizeBDiagCursor
        elif 67.5 <= angle < 112.5:
            return Qt.CursorShape.SizeHorCursor
        elif 112.5 <= angle < 157.5:
            return Qt.CursorShape.SizeFDiagCursor
        else:
            return Qt.CursorShape.SizeVerCursor

    def start_drag(self, handle_type: int, pt: QPointF):
        """Начинает интерактивное перетаскивание маркера."""
        self.active_handle = handle_type
        self.drag_start_pos = pt
        if self.shape:
            self.initial_bounding_rect = self.shape.get_bounding_rect()
            self.initial_rotation = getattr(self.shape, "rotation", 0.0)
            self.initial_state = self.shape.clone()

    def drag_to(self, current_pt: QPointF, shift_pressed: bool = False, offset: QPointF = QPointF(0, 0)):
        """Вычисляет новое состояние фигуры при перетаскивании маркера."""
        if not self.is_active() or self.active_handle == HandleType.NONE or self.initial_bounding_rect.isEmpty():
            return

        br = self.initial_bounding_rect
        center = br.center() - offset
        rot = self.initial_rotation

        # 1. Перемещение всей фигуры
        if self.active_handle == HandleType.INSIDE:
            dx = current_pt.x() - self.drag_start_pos.x()
            dy = current_pt.y() - self.drag_start_pos.y()
            # Перемещаем относительно начального состояния
            if self.initial_state:
                state_copy = self.initial_state.clone()
                state_copy.translate(dx, dy)
                self._apply_shape_geometry(state_copy)

        # 2. Вращение
        elif self.active_handle == HandleType.ROTATE:
            dx = current_pt.x() - center.x()
            dy = current_pt.y() - center.y()
            # Угол от вертикали вверх (-Y)
            angle = math.degrees(math.atan2(dx, -dy))
            if shift_pressed:
                # Привязка к 15 градусам при зажатом Shift
                angle = round(angle / 15.0) * 15.0
            self.shape.rotation = angle % 360.0

        # 3. Изменение размера / масштабирование
        else:
            # Переводим точки в локальную систему координат фигуры (без поворота)
            unrot_start = unrotate_point(self.drag_start_pos, center, rot) + offset
            unrot_curr = unrotate_point(current_pt, center, rot) + offset
            dx = unrot_curr.x() - unrot_start.x()
            dy = unrot_curr.y() - unrot_start.y()

            w = max(10.0, br.width())
            h = max(10.0, br.height())

            sx = 1.0
            sy = 1.0
            origin = br.center()

            if self.active_handle == HandleType.BOTTOM_RIGHT:
                origin = br.topLeft()
                sx = max(0.05, (w + dx) / w)
                sy = max(0.05, (h + dy) / h)
            elif self.active_handle == HandleType.TOP_LEFT:
                origin = br.bottomRight()
                sx = max(0.05, (w - dx) / w)
                sy = max(0.05, (h - dy) / h)
            elif self.active_handle == HandleType.TOP_RIGHT:
                origin = br.bottomLeft()
                sx = max(0.05, (w + dx) / w)
                sy = max(0.05, (h - dy) / h)
            elif self.active_handle == HandleType.BOTTOM_LEFT:
                origin = br.topRight()
                sx = max(0.05, (w - dx) / w)
                sy = max(0.05, (h + dy) / h)
            elif self.active_handle == HandleType.RIGHT:
                origin = QPointF(br.left(), br.center().y())
                sx = max(0.05, (w + dx) / w)
                sy = 1.0
            elif self.active_handle == HandleType.LEFT:
                origin = QPointF(br.right(), br.center().y())
                sx = max(0.05, (w - dx) / w)
                sy = 1.0
            elif self.active_handle == HandleType.BOTTOM:
                origin = QPointF(br.center().x(), br.top())
                sx = 1.0
                sy = max(0.05, (h + dy) / h)
            elif self.active_handle == HandleType.TOP:
                origin = QPointF(br.center().x(), br.bottom())
                sx = 1.0
                sy = max(0.05, (h - dy) / h)

            if shift_pressed:
                # Пропорциональное масштабирование
                avg_s = (sx + sy) / 2.0
                sx = sy = avg_s

            if self.initial_state:
                state_copy = self.initial_state.clone()
                if hasattr(state_copy, "scale_from_origin"):
                    state_copy.scale_from_origin(sx, sy, origin)
                self._apply_shape_geometry(state_copy)

    def _apply_shape_geometry(self, src: BaseShape):
        """Копирует геометрические параметры из src в текущую фигуру."""
        if not self.shape or not src:
            return
        for attr in ("rect", "p1", "p2", "points", "path", "pos", "font_size", "rotation"):
            if hasattr(src, attr):
                setattr(self.shape, attr, getattr(src, attr))
        if hasattr(self.shape, "cached_pixmap"):
            self.shape.cached_pixmap = None
        if hasattr(self.shape, "cached_mosaic"):
            self.shape.cached_mosaic = None
        if hasattr(self.shape, "cached_blur"):
            self.shape.cached_blur = None
        if hasattr(self.shape, "_cached_rect"):
            self.shape._cached_rect = None

    def finish_drag(self) -> tuple[BaseShape | None, BaseShape | None]:
        """Завершает перетаскивание и возвращает кортеж (исходное состояние, итоговое состояние) для Undo/Redo."""
        old_state = self.initial_state
        new_state = self.shape.clone() if self.shape else None
        self.active_handle = HandleType.NONE
        self.initial_state = None
        return old_state, new_state

    def draw(self, painter: QPainter, offset: QPointF = QPointF(0, 0)):
        """Отрисовывает контур выделения и маркеры трансформации в стиле Photoshop / Figma."""
        if not self.is_active():
            return

        br = self.shape.get_bounding_rect()
        if br.isEmpty():
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        center = br.center() - offset
        rot = getattr(self.shape, "rotation", 0.0)

        # 1. Поворот контекста для отрисовки рамки
        painter.translate(center)
        painter.rotate(rot)

        local_w = br.width()
        local_h = br.height()
        local_rect = QRectF(-local_w / 2.0, -local_h / 2.0, local_w, local_h)

        # Контрастный пунктирный контур рамки (Photoshop-стиль)
        pen_outline = QPen(QColor("#0078d4"), 1.5, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_outline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(local_rect)

        # Линия-направляющая к маркеру вращения
        pen_rot_line = QPen(QColor("#0078d4"), 1.2, Qt.PenStyle.SolidLine)
        painter.setPen(pen_rot_line)
        rot_top = QPointF(0, local_rect.top())
        rot_center = QPointF(0, local_rect.top() - self.ROTATE_OFFSET)
        painter.drawLine(rot_top, rot_center)

        # Маркер вращения (зеленый / синий кружок)
        painter.setPen(QPen(QColor("#ffffff"), 1.5))
        painter.setBrush(QBrush(QColor("#10b981")))
        painter.drawEllipse(rot_center, 4.5, 4.5)

        # 2. Отрисовка 8 маркеров изменения размера (белые квадратики с синей рамкой)
        hs = self.HANDLE_SIZE
        painter.setPen(QPen(QColor("#0078d4"), 1.5))
        painter.setBrush(QBrush(QColor("#ffffff")))

        handle_pts = [
            local_rect.topLeft(),
            QPointF(0, local_rect.top()),
            local_rect.topRight(),
            QPointF(local_rect.right(), 0),
            local_rect.bottomRight(),
            QPointF(0, local_rect.bottom()),
            local_rect.bottomLeft(),
            QPointF(local_rect.left(), 0),
        ]
        for hp in handle_pts:
            painter.drawRoundedRect(QRectF(hp.x() - hs / 2.0, hp.y() - hs / 2.0, hs, hs), 1.0, 1.0)

        painter.restore()
