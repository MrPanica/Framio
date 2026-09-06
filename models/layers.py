# -*- coding: utf-8 -*-
"""
Менеджер слоёв рисования.
"""

from typing import List
from PyQt6.QtCore import QObject, pyqtSignal, QPointF
from PyQt6.QtGui import QPainter
from .shapes import BaseShape

class LayerManager(QObject):
    layers_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.shapes: List[BaseShape] = []
        self.is_pinned: bool = False

    def add_shape(self, shape: BaseShape):
        self.shapes.append(shape)
        self.layers_changed.emit()

    def remove_shape(self, shape_id: str):
        self.shapes = [s for s in self.shapes if s.id != shape_id]
        self.layers_changed.emit()

    def remove_at(self, index: int):
        if 0 <= index < len(self.shapes):
            del self.shapes[index]
            self.layers_changed.emit()

    def toggle_visibility(self, index: int):
        if 0 <= index < len(self.shapes):
            self.shapes[index].visible = not self.shapes[index].visible
            self.layers_changed.emit()

    def move_up(self, index: int):
        if 0 <= index < len(self.shapes) - 1:
            self.shapes[index], self.shapes[index + 1] = self.shapes[index + 1], self.shapes[index]
            self.layers_changed.emit()

    def move_down(self, index: int):
        if 1 <= index < len(self.shapes):
            self.shapes[index], self.shapes[index - 1] = self.shapes[index - 1], self.shapes[index]
            self.layers_changed.emit()

    def bring_to_front(self, shape: BaseShape):
        if shape in self.shapes:
            self.shapes.remove(shape)
            self.shapes.append(shape)
            self.layers_changed.emit()

    def send_to_back(self, shape: BaseShape):
        if shape in self.shapes:
            self.shapes.remove(shape)
            self.shapes.insert(0, shape)
            self.layers_changed.emit()

    def move_shape_up(self, shape: BaseShape):
        if shape in self.shapes:
            idx = self.shapes.index(shape)
            if idx < len(self.shapes) - 1:
                self.shapes[idx], self.shapes[idx + 1] = self.shapes[idx + 1], self.shapes[idx]
                self.layers_changed.emit()

    def move_shape_down(self, shape: BaseShape):
        if shape in self.shapes:
            idx = self.shapes.index(shape)
            if idx > 0:
                self.shapes[idx], self.shapes[idx - 1] = self.shapes[idx - 1], self.shapes[idx]
                self.layers_changed.emit()

    def clear(self):
        self.shapes.clear()
        self.layers_changed.emit()

    def draw_all(self, painter: QPainter, offset: QPointF = QPointF(0, 0), source_pixmap=None):
        for shape in self.shapes:
            shape.draw(painter, offset, source_pixmap=source_pixmap)
