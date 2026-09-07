# -*- coding: utf-8 -*-
"""
Интерактивный плавающий редактор текста на холсте (InteractiveTextEditor).
Поддерживает перемещение за верхнюю ручку-плашку еще до и во время набора текста,
а также масштабирование кегля шрифта растягиванием за нижний правый маркер.
"""

from PyQt6.QtCore import Qt, QPoint, QPointF, QRect, pyqtSignal, QSize
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QFrame, QApplication
)
from PyQt6.QtGui import QFont, QFontMetrics, QColor, QCursor, QPainter, QPen, QBrush

from utils.i18n import tr


class InteractiveTextEditor(QFrame):
    """
    Плавающий контейнер текстового поля на холсте с ручкой перемещения
    и угловым маркером изменения размера (масштабирования кегля шрифта).
    """
    committed = pyqtSignal()
    cancelled = pyqtSignal()
    font_size_changed = pyqtSignal(int)
    moved = pyqtSignal(QPointF)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.SubWindow)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Свойства текста
        self.font_family = "Segoe UI"
        self.font_size = 18
        self.text_color = "#FF2E2E"
        self.is_bold = True
        self.is_italic = False
        self.is_underline = False
        self.has_bg = False
        self.bg_color = "#000000"
        self.bg_alpha = 180

        # Состояние перетаскивания и ресайза
        self._is_dragging = False
        self._drag_start_pos = QPoint()
        self._is_resizing = False
        self._resize_start_pos = QPoint()
        self._resize_start_size = QSize()
        self._resize_start_font_size = 18

        self._init_ui()

    def _init_ui(self):
        self.setObjectName("InteractiveTextEditor")
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(2, 2, 2, 2)
        main_layout.setSpacing(0)

        # 1. Верхняя панель перемещения (Drag Header)
        self.header_bar = QWidget(self)
        self.header_bar.setFixedHeight(22)
        self.header_bar.setCursor(Qt.CursorShape.SizeAllCursor)
        self.header_bar.setStyleSheet("""
            QWidget {
                background-color: rgba(15, 23, 42, 230);
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
                border-bottom: 1px solid rgba(56, 189, 248, 120);
            }
        """)

        h_layout = QHBoxLayout(self.header_bar)
        h_layout.setContentsMargins(6, 0, 4, 0)
        h_layout.setSpacing(4)

        self.lbl_grip = QLabel("::: " + tr("text_editor_move", "Текст"), self.header_bar)
        self.lbl_grip.setStyleSheet("color: #38bdf8; font-family: 'Segoe UI', sans-serif; font-size: 10px; font-weight: bold; background: transparent; border: none;")
        h_layout.addWidget(self.lbl_grip)

        self.lbl_font_info = QLabel(f"{self.font_size} pt", self.header_bar)
        self.lbl_font_info.setStyleSheet("color: #94a3b8; font-family: 'Segoe UI', sans-serif; font-size: 10px; background: transparent; border: none;")
        h_layout.addWidget(self.lbl_font_info)

        h_layout.addStretch()

        self.btn_close = QPushButton("×", self.header_bar)
        self.btn_close.setFixedSize(16, 16)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setToolTip(tr("text_editor_close_tip", "Отмена (Esc)"))
        self.btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #94a3b8;
                border: none;
                font-family: 'Segoe UI', sans-serif;
                font-size: 12px;
                font-weight: bold;
                padding-bottom: 2px;
            }
            QPushButton:hover {
                color: #f87171;
            }
        """)
        self.btn_close.clicked.connect(self._on_close_clicked)
        h_layout.addWidget(self.btn_close)

        main_layout.addWidget(self.header_bar)

        # 2. Поле ввода текста
        self.line_edit = QLineEdit(self)
        self.line_edit.setPlaceholderText(tr("prop_placeholder_text", "Введите текст..."))
        self.line_edit.returnPressed.connect(self._on_return_pressed)
        self.line_edit.textChanged.connect(self._on_text_changed)
        main_layout.addWidget(self.line_edit)

        self.setMinimumWidth(220)
        self._apply_styling()

    def _on_close_clicked(self):
        self.cancelled.emit()

    def _on_return_pressed(self):
        self.committed.emit()

    def _on_text_changed(self, txt: str):
        # Автоматическое расширение ширины при длинном тексте
        fm = QFontMetrics(self.line_edit.font())
        req_w = fm.horizontalAdvance(txt if txt else " ") + 48
        if req_w > self.width():
            self.setFixedWidth(req_w)

    def update_style(self, font_family=None, font_size=None, color=None,
                     is_bold=None, is_italic=None, is_underline=None,
                     has_bg=None, bg_color=None, bg_alpha=None):
        """Обновляет оформление и шрифт редактора в реальном времени при выборе в панели."""
        if font_family is not None:
            self.font_family = font_family
        if font_size is not None:
            self.font_size = max(10, min(120, int(font_size)))
        if color is not None:
            self.text_color = color
        if is_bold is not None:
            self.is_bold = is_bold
        if is_italic is not None:
            self.is_italic = is_italic
        if is_underline is not None:
            self.is_underline = is_underline
        if has_bg is not None:
            self.has_bg = has_bg
        if bg_color is not None:
            self.bg_color = bg_color
        if bg_alpha is not None:
            self.bg_alpha = bg_alpha

        self._apply_styling()

    def _apply_styling(self):
        weight = "bold" if self.is_bold else "normal"
        style = "italic" if self.is_italic else "normal"
        decor = "underline" if self.is_underline else "none"

        bg_css = "rgba(0, 0, 0, 200)" if self.has_bg else "rgba(20, 24, 33, 175)"
        border_col = self.text_color if self.text_color and not self.text_color.startswith("#000") else "#38bdf8"

        self.setStyleSheet(f"""
            QFrame#InteractiveTextEditor {{
                background-color: {bg_css};
                border: 1.5px dashed {border_col};
                border-radius: 4px;
            }}
        """)

        self.line_edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: transparent;
                color: {self.text_color};
                font-family: '{self.font_family}', sans-serif;
                font-size: {self.font_size}pt;
                font-weight: {weight};
                font-style: {style};
                text-decoration: {decor};
                border: none;
                padding: 4px 6px;
            }}
            QLineEdit::placeholder {{
                color: rgba(255, 255, 255, 120);
            }}
        """)

        self.lbl_font_info.setText(f"{self.font_size} pt")

        # Пересчитываем минимальную высоту в зависимости от кегля шрифта
        fm = QFontMetrics(self.line_edit.font())
        needed_h = fm.height() + 32
        self.setFixedHeight(max(54, needed_h))

    # --- Интерфейс совместимости с QLineEdit ---
    def text(self) -> str:
        return self.line_edit.text()

    def setText(self, txt: str):
        self.line_edit.setText(txt)
        self._on_text_changed(txt)

    def selectAll(self):
        self.line_edit.selectAll()

    def clear(self):
        self.line_edit.clear()

    def setFocus(self):
        self.line_edit.setFocus()

    # --- Обработка перемещения за верхнюю плашку и масштабирования за угол ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.position().toPoint()
            # Проверяем клик в нижний правый угол (ручка ресайза 16x16)
            if pos.x() >= self.width() - 16 and pos.y() >= self.height() - 16:
                self._is_resizing = True
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_size = self.size()
                self._resize_start_font_size = self.font_size
                event.accept()
                return

            # Проверяем клик в заголовок для перетаскивания
            if self.header_bar.geometry().contains(pos):
                self._is_dragging = True
                self._drag_start_pos = event.globalPosition().toPoint() - self.pos()
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()

        # Ресайз за угол
        if self._is_resizing:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            new_w = max(180, self._resize_start_size.width() + delta.x())
            new_h = max(50, self._resize_start_size.height() + delta.y())
            self.resize(new_w, new_h)

            # Пропорционально масштабируем размер шрифта (растягивание меняет размер)
            scale_factor = max(0.4, (new_w / max(1, self._resize_start_size.width()) +
                                     new_h / max(1, self._resize_start_size.height())) / 2.0)
            new_font_size = int(round(self._resize_start_font_size * scale_factor))
            new_font_size = max(10, min(96, new_font_size))
            if new_font_size != self.font_size:
                self.update_style(font_size=new_font_size)
                self.font_size_changed.emit(new_font_size)
            event.accept()
            return

        # Перемещение за заголовок
        if self._is_dragging:
            new_pos = event.globalPosition().toPoint() - self._drag_start_pos
            self.move(new_pos)
            self.moved.emit(QPointF(new_pos))
            event.accept()
            return

        # Курсор над правым нижним углом
        if pos.x() >= self.width() - 16 and pos.y() >= self.height() - 16:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.unsetCursor()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._is_resizing:
            self._is_resizing = False
            self.unsetCursor()
        if self._is_dragging:
            self._is_dragging = False
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        # Отрисовываем аккуратный треугольный маркер ресайза в правом нижнем углу
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        pen = QPen(QColor(56, 189, 248, 180), 1.5)
        painter.setPen(pen)

        # 3 диагональные засечки маркера ресайза
        painter.drawLine(w - 4, h - 12, w - 12, h - 4)
        painter.drawLine(w - 4, h - 8, w - 8, h - 4)
        painter.drawLine(w - 4, h - 4, w - 4, h - 4)
        painter.end()
