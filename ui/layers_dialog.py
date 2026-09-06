# -*- coding: utf-8 -*-
"""
Окно управления слоями рисования.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget, QSizePolicy
)
from models.layers import LayerManager
from utils.i18n import tr
from ui.icons import create_themed_icon

class LayerRow(QWidget):
    visibility_toggled = pyqtSignal(int)
    move_up_clicked = pyqtSignal(int)
    move_down_clicked = pyqtSignal(int)
    delete_clicked = pyqtSignal(int)

    def __init__(self, index: int, shape, parent=None):
        super().__init__(parent)
        self.index = index
        self.shape = shape

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        # Цветовой маркер
        dot = QLabel("●")
        dot.setStyleSheet(f"color: {shape.color}; font-size: 14px;")
        layout.addWidget(dot)

        # Название слоя
        name_lbl = QLabel(shape.name)
        name_lbl.setStyleSheet("color: #e6e8ee; font-size: 12px; font-weight: 500;")
        layout.addWidget(name_lbl, stretch=1)

        # Кнопка видимости
        self.btn_eye = QPushButton()
        self.btn_eye.setIcon(create_themed_icon("eye" if shape.visible else "eye_off", is_dark=True, size=13))
        self.btn_eye.setFixedSize(24, 22)
        self.btn_eye.setToolTip(tr("layers_visible_tip", "Вкл/Выкл видимость слоя"))
        self.btn_eye.clicked.connect(lambda: self.visibility_toggled.emit(self.index))
        layout.addWidget(self.btn_eye)

        # Вверх
        btn_up = QPushButton("▲")
        btn_up.setFixedSize(20, 22)
        btn_up.setToolTip(tr("layers_up_tip", "Переместить выше"))
        btn_up.clicked.connect(lambda: self.move_up_clicked.emit(self.index))
        layout.addWidget(btn_up)

        # Вниз
        btn_down = QPushButton("▼")
        btn_down.setFixedSize(20, 22)
        btn_down.setToolTip(tr("layers_down_tip", "Переместить ниже"))
        btn_down.clicked.connect(lambda: self.move_down_clicked.emit(self.index))
        layout.addWidget(btn_down)

        # Удалить
        btn_del = QPushButton("✕")
        btn_del.setFixedSize(20, 22)
        btn_del.setToolTip(tr("layers_del_tip", "Удалить слой"))
        btn_del.setStyleSheet("color: #ff5555;")
        btn_del.clicked.connect(lambda: self.delete_clicked.emit(self.index))
        layout.addWidget(btn_del)


class LayersDialog(QFrame):
    def __init__(self, layer_manager: LayerManager, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.layer_manager = layer_manager
        self.setFixedWidth(260)
        self.setMaximumHeight(340)

        self.setStyleSheet("""
            QFrame {
                background-color: #1a1d24;
                border: 1px solid #3c4250;
                border-radius: 8px;
            }
            QLabel {
                color: #abb2bf;
                font-size: 12px;
            }
            QPushButton {
                background-color: #2b303c;
                color: #e6e8ee;
                border: 1px solid #444c5d;
                border-radius: 3px;
                font-size: 10px;
            }
            QPushButton:hover {
                background-color: #3e4658;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # Заголовок
        header = QHBoxLayout()
        title = QLabel(tr("layers_dialog_title", "Слои аннотаций"))
        title.setStyleSheet("font-weight: bold; color: #61afef;")
        header.addWidget(title)

        btn_clear = QPushButton(tr("layers_clear_all", "Очистить все"))
        btn_clear.clicked.connect(self.layer_manager.clear if hasattr(self.layer_manager, "clear") else lambda: None)
        header.addWidget(btn_clear)
        main_layout.addLayout(header)

        # Прокручиваемый список слоев
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")
        
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(3)
        self.list_layout.addStretch()

        self.scroll.setWidget(self.list_container)
        main_layout.addWidget(self.scroll)

        self.layer_manager.layers_changed.connect(self.refresh_list)
        self.refresh_list()

    @property
    def rows(self):
        result = []
        for i in range(self.list_layout.count() - 1):
            item = self.list_layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), LayerRow):
                result.append(item.widget())
        return result

    def refresh_list(self):
        # Очищаем текущие элементы
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        shapes = self.layer_manager.shapes
        if not shapes:
            lbl_empty = QLabel(tr("layers_empty", "Нет слоев (нарисуйте фигуру)"))
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_empty.setStyleSheet("color: #7f848e; font-style: italic; padding: 10px;")
            self.list_layout.insertWidget(0, lbl_empty)
            return

        # Показываем слои сверху вниз (верхний слой первый)
        for i in reversed(range(len(shapes))):
            shape = shapes[i]
            row = LayerRow(i, shape, self)
            row.visibility_toggled.connect(self.layer_manager.toggle_visibility)
            row.move_up_clicked.connect(self.layer_manager.move_up)
            row.move_down_clicked.connect(self.layer_manager.move_down)
            row.delete_clicked.connect(self.layer_manager.remove_at)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)
