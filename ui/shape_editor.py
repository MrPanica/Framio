# -*- coding: utf-8 -*-
"""
Всплывающее окно редактирования свойств конкретной фигуры (ShapeEditPopup).
Позволяет изменять цвет, толщину, прозрачность, градиенты, стили наконечников и текст
для любой уже нарисованной фигуры.
"""

from PyQt6.QtCore import Qt, pyqtSignal, QSize, QRectF, QPointF
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QColorDialog, QLineEdit, QWidget, QGridLayout, QCheckBox
)
from PyQt6.QtGui import QColor, QFont

from models.shapes import (
    BaseShape, LineShape, ArrowShape, RectangleShape, CircleShape, TextShape, PenShape, MosaicShape, BlurShape, RegionalEffectShape
)
from .toolbars import get_theme_styles, style_toggle_btn, show_smart_popup
from .icons import create_themed_icon, create_style_preview_icon
from utils.i18n import tr


class ShapeEditPopup(QFrame):
    shape_modified = pyqtSignal()
    editing_finished = pyqtSignal(dict, dict)  # (old_props, new_props)
    closed = pyqtSignal()

    PRESET_COLORS = [
        "#FF2E2E", "#00C0FF", "#2ECC71", "#FFD700",
        "#FF8C00", "#9B59B6", "#FFFFFF", "#1E1E1E"
    ]

    def __init__(self, shape: BaseShape, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.shape = shape
        self.old_props = self._snapshot_props(shape)
        
        theme = get_theme_styles()
        self.is_dark = theme["is_dark"]
        self.setStyleSheet(theme["popup_frame"] + """
            QLabel {
                font-size: 11px;
                font-weight: 500;
            }
            QPushButton {
                font-size: 11px;
            }
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(12, 10, 12, 10)
        self.layout.setSpacing(8)

        # Заголовок
        header = QHBoxLayout()
        lbl_title = QLabel(tr("shape_edit_title", "Свойства: {name}", name=shape.name))
        lbl_title.setStyleSheet("color: #0078d4; font-weight: bold; font-size: 12px;")
        header.addWidget(lbl_title, 1)

        btn_close = QPushButton("✕")
        btn_close.setFixedSize(20, 20)
        btn_close.setStyleSheet("background: transparent; border: none; color: #888; font-weight: bold;")
        btn_close.clicked.connect(self.close)
        header.addWidget(btn_close)
        self.layout.addLayout(header)

        # 1. Текст (если это TextShape)
        if isinstance(shape, TextShape):
            self._init_text_controls()

        is_censor_box = isinstance(shape, (RegionalEffectShape, MosaicShape, BlurShape))

        # 2. Цвет контура / основной цвет (для векторных фигур)
        if not is_censor_box:
            self._init_color_controls()

        # 3. Ползунки (толщина, зернистость / размытие)
        if not isinstance(shape, TextShape):
            self._init_size_controls()

        # 4. Специфика стрелки
        if isinstance(shape, ArrowShape):
            self._init_arrow_controls()

        # 5. Специфика линии
        elif isinstance(shape, LineShape):
            self._init_line_controls()

        # 6. Специфика прямоугольника
        elif isinstance(shape, RectangleShape):
            self._init_rect_controls()

        # 7. Специфика круга
        elif isinstance(shape, CircleShape):
            self._init_circle_controls()

        # 8. Специфика маркера
        elif isinstance(shape, PenShape) and shape.is_highlighter:
            self._init_highlighter_controls()

    def _snapshot_props(self, s: BaseShape) -> dict:
        props = {
            "color": getattr(s, "color", "#FF2E2E"),
            "stroke_width": getattr(s, "stroke_width", 4),
            "visible": getattr(s, "visible", True),
            "pixel_size": getattr(s, "pixel_size", 8),
            "blur_radius": getattr(s, "blur_radius", 15)
        }
        if isinstance(s, ArrowShape):
            props["arrow_style"] = s.arrow_style
            props["filled"] = s.filled
        elif isinstance(s, LineShape):
            props["line_style"] = getattr(s, "line_style", "solid")
        elif isinstance(s, RectangleShape):
            props["is_rounded"] = s.is_rounded
            props["filled"] = s.filled
            props["fill_alpha"] = s.fill_alpha
            props["is_gradient"] = s.is_gradient
            props["gradient_color1"] = s.gradient_color1
            props["gradient_color2"] = s.gradient_color2
        elif isinstance(s, CircleShape):
            props["filled"] = s.filled
            props["fill_alpha"] = s.fill_alpha
            props["is_gradient"] = s.is_gradient
            props["gradient_color1"] = s.gradient_color1
            props["gradient_color2"] = s.gradient_color2
        elif isinstance(s, TextShape):
            props["text"] = s.text
            props["font_size"] = s.font_size
            props["font_family"] = s.font_family
            props["is_bold"] = s.is_bold
            props["is_italic"] = getattr(s, "is_italic", False)
            props["is_underline"] = s.is_underline
            props["has_bg"] = getattr(s, "has_bg", False)
            props["bg_color"] = getattr(s, "bg_color", "#000000")
            props["bg_alpha"] = getattr(s, "bg_alpha", 180)
        elif isinstance(s, PenShape):
            props["alpha"] = getattr(s, "alpha", 255)
        return props

    def _init_color_controls(self):
        row_c = QHBoxLayout()
        row_c.setSpacing(5)
        row_c.addWidget(QLabel(tr("shape_edit_color_label", "Цвет:"))) 

        self.color_buttons = {}
        for col in self.PRESET_COLORS:
            btn = QPushButton()
            btn.setFixedSize(20, 20)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(f"background-color: {col}; border-radius: 10px; border: 1px solid #555;")
            btn.clicked.connect(lambda checked, c=col: self._set_color(c))
            row_c.addWidget(btn)
            self.color_buttons[col] = btn

        # Кнопка режима мозаики
        self.btn_col_mosaic = QPushButton()
        self.btn_col_mosaic.setFixedSize(20, 20)
        self.btn_col_mosaic.setToolTip(tr("prop_mosaic_tip", "Режим мозаики (Цензура)"))
        self.btn_col_mosaic.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_col_mosaic.setIcon(create_themed_icon("mosaic", self.is_dark, size=12))
        self.btn_col_mosaic.setStyleSheet(f"background-color: {'#3b82f6' if self.shape.color == 'mosaic' else '#27272a'}; border-radius: 10px; border: 1px solid #555;")
        self.btn_col_mosaic.clicked.connect(lambda: self._set_color("mosaic"))
        row_c.addWidget(self.btn_col_mosaic)
        self.color_buttons["mosaic"] = self.btn_col_mosaic

        # Кнопка режима блюра
        self.btn_col_blur = QPushButton()
        self.btn_col_blur.setFixedSize(20, 20)
        self.btn_col_blur.setToolTip(tr("prop_blur_tip", "Режим размытия (Блюр)"))
        self.btn_col_blur.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_col_blur.setIcon(create_themed_icon("blur", self.is_dark, size=12))
        self.btn_col_blur.setStyleSheet(f"background-color: {'#3b82f6' if self.shape.color == 'blur' else '#27272a'}; border-radius: 10px; border: 1px solid #555;")
        self.btn_col_blur.clicked.connect(lambda: self._set_color("blur"))
        row_c.addWidget(self.btn_col_blur)
        self.color_buttons["blur"] = self.btn_col_blur

        # Кнопка пипетки
        btn_pipette = QPushButton()
        btn_pipette.setFixedSize(22, 20)
        btn_pipette.setToolTip(tr("prop_eyedropper_tip", "Пипетка (выбрать цвет с экрана)"))
        btn_pipette.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_pipette.setIcon(create_themed_icon("pipette", self.is_dark, size=14))
        btn_pipette.setIconSize(QSize(14, 14))
        btn_pipette.clicked.connect(self._pick_screen_color)
        row_c.addWidget(btn_pipette)

        btn_more = QPushButton()
        btn_more.setFixedSize(22, 20)
        btn_more.setToolTip(tr("prop_color_custom", "Выбрать произвольный цвет"))
        btn_more.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_more.setIcon(create_themed_icon("palette", self.is_dark, size=14))
        btn_more.setIconSize(QSize(14, 14))
        btn_more.clicked.connect(self._pick_custom_color)
        row_c.addWidget(btn_more)

        self.layout.addLayout(row_c)

    def _init_size_controls(self):
        is_censor_box = isinstance(self.shape, (RegionalEffectShape, MosaicShape, BlurShape))

        # Сгруппированный контейнер для ползунков
        self.sliders_frame = QFrame()
        self.sliders_frame.setStyleSheet("""
            QFrame {
                background-color: rgba(24, 24, 27, 180);
                border: 1px solid #2d3139;
                border-radius: 6px;
                padding: 4px;
            }
        """)
        sliders_layout = QVBoxLayout(self.sliders_frame)
        sliders_layout.setContentsMargins(6, 6, 6, 6)
        sliders_layout.setSpacing(6)

        # 1. Толщина линии (скрываем для чисто прямоугольных блоков цензуры)
        self.lbl_size = QLabel(tr("prop_stroke_width", "Толщина: {val} px", val=self.shape.stroke_width))
        self.slider_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_size.setRange(1, 32)
        self.slider_size.setValue(int(self.shape.stroke_width))
        self.slider_size.valueChanged.connect(self._on_size_changed)

        sliders_layout.addWidget(self.lbl_size)
        sliders_layout.addWidget(self.slider_size)
        if is_censor_box:
            self.lbl_size.hide()
            self.slider_size.hide()

        # 2. Зернистость (для MosaicShape или фигур с цветом mosaic)
        cur_grain = getattr(self.shape, "pixel_size", 8)
        self.lbl_grain = QLabel(tr("prop_mosaic_size", "Зернистость: {val} px", val=cur_grain))
        self.slider_grain = QSlider(Qt.Orientation.Horizontal)
        self.slider_grain.setRange(3, 30)
        self.slider_grain.setValue(int(cur_grain))
        self.slider_grain.valueChanged.connect(self._on_grain_changed)

        sliders_layout.addWidget(self.lbl_grain)
        sliders_layout.addWidget(self.slider_grain)
        if not (isinstance(self.shape, MosaicShape) or getattr(self.shape, "is_mosaic", False)):
            self.lbl_grain.hide()
            self.slider_grain.hide()

        # 3. Степень размытия (для BlurShape или фигур с цветом blur)
        cur_blur = getattr(self.shape, "blur_radius", 15)
        self.lbl_blur = QLabel(tr("prop_blur_radius", "Степень размытия: {val} px", val=cur_blur))
        self.slider_blur = QSlider(Qt.Orientation.Horizontal)
        self.slider_blur.setRange(3, 45)
        self.slider_blur.setValue(int(cur_blur))
        self.slider_blur.valueChanged.connect(self._on_blur_changed)

        sliders_layout.addWidget(self.lbl_blur)
        sliders_layout.addWidget(self.slider_blur)
        if not (isinstance(self.shape, BlurShape) or getattr(self.shape, "is_blur", False)):
            self.lbl_blur.hide()
            self.slider_blur.hide()

        self.layout.addWidget(self.sliders_frame)

    def _on_grain_changed(self, val: int):
        self.shape.pixel_size = val
        self.lbl_grain.setText(tr("prop_mosaic_size", "Зернистость: {val} px", val=val))
        bg_pix = getattr(self.parent(), "background_pixmap", None)
        if hasattr(self.shape, "set_pixel_size"):
            self.shape.set_pixel_size(val, bg_pix)
        elif hasattr(self.shape, "update_mosaic") and bg_pix is not None:
            self.shape.update_mosaic(bg_pix)
        self.shape_modified.emit()

    def _on_blur_changed(self, val: int):
        self.shape.blur_radius = val
        self.lbl_blur.setText(tr("prop_blur_radius", "Степень размытия: {val} px", val=val))
        bg_pix = getattr(self.parent(), "background_pixmap", None)
        if hasattr(self.shape, "set_blur_radius"):
            self.shape.set_blur_radius(val, bg_pix)
        elif hasattr(self.shape, "update_blur") and bg_pix is not None:
            self.shape.update_blur(bg_pix)
        self.shape_modified.emit()

    def _init_arrow_controls(self):
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("prop_style", "Стиль:"))) 
        self.combo_arrow = QComboBox()
        self.combo_arrow.setIconSize(QSize(26, 18))
        arrow_styles = [
            ("classic", "arrow_classic", tr("prop_arrow_classic", "Классическая стрелка")),
            ("barbed", "arrow_barbed", tr("prop_arrow_barbed", "С вырезом (усиками)")),
            ("double", "arrow_double", tr("prop_arrow_double", "Двусторонняя стрелка")),
            ("stealth", "arrow_stealth", tr("prop_arrow_stealth", "Стелс-стрелка")),
            ("dashed", "arrow_dashed", tr("prop_arrow_dashed", "Пунктирная стрелка"))
        ]
        for s_key, ico_key, s_name in arrow_styles:
            ico = create_style_preview_icon(ico_key, self.is_dark)
            self.combo_arrow.addItem(ico, s_name, s_key)
        
        idx = self.combo_arrow.findData(self.shape.arrow_style)
        if idx >= 0:
            self.combo_arrow.setCurrentIndex(idx)
        self.combo_arrow.currentIndexChanged.connect(self._on_arrow_style_changed)
        row.addWidget(self.combo_arrow, 1)
        self.layout.addLayout(row)

        row_f = QHBoxLayout()
        row_f.addWidget(QLabel(tr("shape_edit_tip_label", "Наконечник:")))
        self.btn_arrow_out = QPushButton(tr("prop_outline", "Контур"))
        self.btn_arrow_out.setFixedSize(68, 24)
        self.btn_arrow_out.clicked.connect(lambda: self._set_arrow_filled(False))
        self.btn_arrow_fill = QPushButton(tr("prop_filled", "Заливка"))
        self.btn_arrow_fill.setFixedSize(68, 24)
        self.btn_arrow_fill.clicked.connect(lambda: self._set_arrow_filled(True))
        style_toggle_btn(self.btn_arrow_out, not self.shape.filled, self.is_dark)
        style_toggle_btn(self.btn_arrow_fill, self.shape.filled, self.is_dark)
        row_f.addWidget(self.btn_arrow_out)
        row_f.addWidget(self.btn_arrow_fill)
        row_f.addStretch()
        self.layout.addLayout(row_f)

    def _init_line_controls(self):
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("prop_style", "Стиль:"))) 
        self.combo_line = QComboBox()
        self.combo_line.setIconSize(QSize(26, 18))
        line_styles = [
            ("solid", "line_solid", tr("prop_line_solid", "Сплошная линия")),
            ("dashed", "line_dashed", tr("prop_line_dash", "Пунктирная линия")),
            ("dotted", "line_dotted", tr("prop_line_dot", "Точечная линия"))
        ]
        for l_key, ico_key, l_name in line_styles:
            ico = create_style_preview_icon(ico_key, self.is_dark)
            self.combo_line.addItem(ico, l_name, l_key)

        idx = self.combo_line.findData(getattr(self.shape, "line_style", "solid"))
        if idx >= 0:
            self.combo_line.setCurrentIndex(idx)
        self.combo_line.currentIndexChanged.connect(self._on_line_style_changed)
        row.addWidget(self.combo_line, 1)
        self.layout.addLayout(row)

    def _init_rect_controls(self):
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("prop_corners", "Углы:")))
        self.combo_rect = QComboBox()
        self.combo_rect.setIconSize(QSize(26, 18))
        rect_styles = [
            ("sharp", "rect_sharp", tr("prop_corners_sharp", "Прямые углы")),
            ("rounded", "rect_rounded", tr("prop_corners_rounded", "Скруглённые углы"))
        ]
        for r_key, ico_key, r_name in rect_styles:
            ico = create_style_preview_icon(ico_key, self.is_dark)
            self.combo_rect.addItem(ico, r_name, r_key)

        idx = self.combo_rect.findData("rounded" if self.shape.is_rounded else "sharp")
        if idx >= 0:
            self.combo_rect.setCurrentIndex(idx)
        self.combo_rect.currentIndexChanged.connect(self._on_rect_style_changed)
        row.addWidget(self.combo_rect, 1)
        self.layout.addLayout(row)

        self._init_fill_controls()

    def _init_circle_controls(self):
        self._init_fill_controls()

    def _init_fill_controls(self):
        row_f = QHBoxLayout()
        self.btn_out = QPushButton(tr("prop_outline", "Контур"))
        self.btn_out.setFixedSize(68, 24)
        self.btn_out.clicked.connect(lambda: self._set_filled(False))
        self.btn_fill = QPushButton(tr("prop_filled", "Заливка"))
        self.btn_fill.setFixedSize(68, 24)
        self.btn_fill.clicked.connect(lambda: self._set_filled(True))
        style_toggle_btn(self.btn_out, not self.shape.filled, self.is_dark)
        style_toggle_btn(self.btn_fill, self.shape.filled, self.is_dark)
        row_f.addWidget(self.btn_out)
        row_f.addWidget(self.btn_fill)
        row_f.addStretch()
        self.layout.addLayout(row_f)

        # Контейнер расширенных опций заливки
        self.fill_opt_container = QWidget()
        f_layout = QVBoxLayout(self.fill_opt_container)
        f_layout.setContentsMargins(0, 0, 0, 0)
        f_layout.setSpacing(6)

        row_gt = QHBoxLayout()
        self.btn_solid = QPushButton(tr("prop_fill_solid", "Сплошной"))
        self.btn_solid.setFixedSize(74, 22)
        self.btn_solid.clicked.connect(lambda: self._set_gradient(False))
        self.btn_grad = QPushButton(tr("prop_fill_gradient", "Градиент"))
        self.btn_grad.setFixedSize(74, 22)
        self.btn_grad.clicked.connect(lambda: self._set_gradient(True))
        style_toggle_btn(self.btn_solid, not self.shape.is_gradient, self.is_dark)
        style_toggle_btn(self.btn_grad, self.shape.is_gradient, self.is_dark)
        row_gt.addWidget(self.btn_solid)
        row_gt.addWidget(self.btn_grad)
        row_gt.addStretch()
        f_layout.addLayout(row_gt)

        # Градиент цвета
        self.grad_colors_widget = QWidget()
        g_col = QHBoxLayout(self.grad_colors_widget)
        g_col.setContentsMargins(0, 0, 0, 0)
        g_col.setSpacing(6)
        g_col.addWidget(QLabel(tr("prop_colors", "Цвета:")))
        self.btn_g1 = QPushButton("1")
        self.btn_g1.setFixedSize(26, 22)
        self.btn_g1.setStyleSheet(f"background-color: {self.shape.gradient_color1}; color: #fff; font-weight: bold; border-radius: 4px;")
        self.btn_g1.clicked.connect(lambda: self._pick_grad_col(1))
        g_col.addWidget(self.btn_g1)
        g_col.addWidget(QLabel("→"))
        self.btn_g2 = QPushButton("2")
        self.btn_g2.setFixedSize(26, 22)
        self.btn_g2.setStyleSheet(f"background-color: {self.shape.gradient_color2}; color: #fff; font-weight: bold; border-radius: 4px;")
        self.btn_g2.clicked.connect(lambda: self._pick_grad_col(2))
        g_col.addWidget(self.btn_g2)
        g_col.addStretch()
        f_layout.addWidget(self.grad_colors_widget)
        self.grad_colors_widget.setVisible(self.shape.is_gradient)

        # Прозрачность заливки
        pct = int(self.shape.fill_alpha * 100 / 255)
        self.lbl_opacity = QLabel(tr("prop_fill_opacity", "Прозрачность заливки: {pct}%", pct=pct))
        f_layout.addWidget(self.lbl_opacity)
        self.slider_opacity = QSlider(Qt.Orientation.Horizontal)
        self.slider_opacity.setRange(0, 100)
        self.slider_opacity.setValue(pct)
        self.slider_opacity.valueChanged.connect(self._on_opacity_changed)
        f_layout.addWidget(self.slider_opacity)

        self.layout.addWidget(self.fill_opt_container)
        self.fill_opt_container.setVisible(self.shape.filled)

    def _init_highlighter_controls(self):
        pct = int(self.shape.alpha * 100 / 255)
        self.lbl_hl = QLabel(tr("prop_hl_opacity", "Непрозрачность маркера: {pct}%", pct=pct))
        self.layout.addWidget(self.lbl_hl)
        self.slider_hl = QSlider(Qt.Orientation.Horizontal)
        self.slider_hl.setRange(10, 100)
        self.slider_hl.setValue(pct)
        self.slider_hl.valueChanged.connect(self._on_hl_opacity_changed)
        self.layout.addWidget(self.slider_hl)

    def _init_text_controls(self):
        self.edit_text = QLineEdit(self.shape.text)
        self.edit_text.setPlaceholderText(tr("prop_placeholder_text", "Текст надписи..."))
        self.edit_text.textChanged.connect(self._on_text_changed)
        self.layout.addWidget(self.edit_text)

        row_f = QHBoxLayout()
        self.combo_text_font = QComboBox()
        self.combo_text_font.addItems([
            "Segoe UI", "Arial", "Consolas", "Times New Roman",
            "Calibri", "Verdana", "Tahoma", "Impact", "Courier New"
        ])
        self.combo_text_font.setCurrentText(self.shape.font_family)
        self.combo_text_font.currentTextChanged.connect(self._on_text_font_changed)
        row_f.addWidget(self.combo_text_font, 1)

        self.btn_bold = QPushButton("B")
        self.btn_bold.setCheckable(True)
        self.btn_bold.setFixedSize(26, 24)
        self.btn_bold.setStyleSheet("font-weight: bold; font-size: 13px;")
        self.btn_bold.setChecked(self.shape.is_bold)
        self.btn_bold.setToolTip(tr("prop_bold_tip", "Жирный шрифт"))
        self.btn_bold.toggled.connect(self._on_text_bold_toggled)
        row_f.addWidget(self.btn_bold)

        self.btn_italic = QPushButton("I")
        self.btn_italic.setCheckable(True)
        self.btn_italic.setFixedSize(26, 24)
        self.btn_italic.setStyleSheet("font-style: italic; font-size: 13px; font-family: 'Times New Roman', serif;")
        self.btn_italic.setChecked(getattr(self.shape, "is_italic", False))
        self.btn_italic.setToolTip(tr("prop_text_italic", "Курсив"))
        self.btn_italic.toggled.connect(self._on_text_italic_toggled)
        row_f.addWidget(self.btn_italic)

        self.btn_underline = QPushButton("U")
        self.btn_underline.setCheckable(True)
        self.btn_underline.setFixedSize(26, 24)
        self.btn_underline.setStyleSheet("text-decoration: underline; font-size: 13px;")
        self.btn_underline.setChecked(self.shape.is_underline)
        self.btn_underline.setToolTip(tr("prop_underline_tip", "Подчёркнутый шрифт"))
        self.btn_underline.toggled.connect(self._on_text_underline_toggled)
        row_f.addWidget(self.btn_underline)
        self.layout.addLayout(row_f)

        self.lbl_font_size = QLabel(tr("prop_font_size", "Размер шрифта: {val} pt", val=self.shape.font_size))
        self.layout.addWidget(self.lbl_font_size)
        self.slider_font_size = QSlider(Qt.Orientation.Horizontal)
        self.slider_font_size.setRange(10, 64)
        self.slider_font_size.setValue(int(self.shape.font_size))
        self.slider_font_size.valueChanged.connect(self._on_text_size_changed)
        self.layout.addWidget(self.slider_font_size)

        # Настройка фона текста
        self.chk_text_bg = QCheckBox(tr("prop_text_bg", "Фон под текстом"))
        self.chk_text_bg.setChecked(getattr(self.shape, "has_bg", False))
        self.chk_text_bg.toggled.connect(self._on_text_bg_toggled)
        self.layout.addWidget(self.chk_text_bg)

        self.text_bg_container = QWidget()
        bg_lay = QVBoxLayout(self.text_bg_container)
        bg_lay.setContentsMargins(0, 0, 0, 0)
        bg_lay.setSpacing(4)

        row_bg_col = QHBoxLayout()
        row_bg_col.addWidget(QLabel(tr("prop_text_bg_col", "Цвет фона:")))
        self.btn_text_bg_col = QPushButton()
        self.btn_text_bg_col.setFixedSize(28, 22)
        cur_bg_col = getattr(self.shape, "bg_color", "#000000")
        self.btn_text_bg_col.setStyleSheet(f"background-color: {cur_bg_col}; border: 1px solid #666; border-radius: 4px;")
        self.btn_text_bg_col.clicked.connect(self._pick_text_bg_color)
        row_bg_col.addWidget(self.btn_text_bg_col)
        row_bg_col.addStretch()
        bg_lay.addLayout(row_bg_col)

        bg_pct = int(getattr(self.shape, "bg_alpha", 180) * 100 / 255)
        self.lbl_text_bg_alpha = QLabel(tr("prop_text_bg_alpha", "Непрозрачность фона: {pct}%", pct=bg_pct))
        bg_lay.addWidget(self.lbl_text_bg_alpha)
        self.slider_text_bg_alpha = QSlider(Qt.Orientation.Horizontal)
        self.slider_text_bg_alpha.setRange(10, 100)
        self.slider_text_bg_alpha.setValue(bg_pct)
        self.slider_text_bg_alpha.valueChanged.connect(self._on_text_bg_alpha_changed)
        bg_lay.addWidget(self.slider_text_bg_alpha)

        self.layout.addWidget(self.text_bg_container)
        self.text_bg_container.setVisible(getattr(self.shape, "has_bg", False))

    def _on_text_bg_toggled(self, checked: bool):
        self.shape.has_bg = checked
        self.text_bg_container.setVisible(checked)
        self.adjustSize()
        self.shape_modified.emit()

    def _pick_text_bg_color(self):
        c = QColorDialog.getColor(QColor(getattr(self.shape, "bg_color", "#000000")), self, tr("shape_edit_bg_picker_title", "Выбор цвета фона текста"))
        if c.isValid():
            self.shape.bg_color = c.name()
            self.btn_text_bg_col.setStyleSheet(f"background-color: {c.name()}; border: 1px solid #666; border-radius: 4px;")
            self.shape_modified.emit()

    def _on_text_bg_alpha_changed(self, val: int):
        self.lbl_text_bg_alpha.setText(tr("prop_text_bg_alpha", "Непрозрачность фона: {pct}%", pct=val))
        self.shape.bg_alpha = int(val * 255 / 100)
        self.shape_modified.emit()

    def _set_color(self, c: str):
        self.shape.color = c
        if isinstance(self.shape, TextShape):
            self._sync_to_parent_tool_config("color", c)
        if hasattr(self, "lbl_grain") and hasattr(self, "slider_grain"):
            is_mos = (c == "mosaic")
            self.lbl_grain.setVisible(is_mos)
            self.slider_grain.setVisible(is_mos)
        if hasattr(self, "lbl_blur") and hasattr(self, "slider_blur"):
            is_blr = (c == "blur")
            self.lbl_blur.setVisible(is_blr)
            self.slider_blur.setVisible(is_blr)
        self.adjustSize()
        self.shape_modified.emit()

    def _pick_custom_color(self):
        c = QColorDialog.getColor(QColor(self.shape.color), self, tr("shape_edit_stroke_picker_title", "Выбор цвета фигуры"))
        if c.isValid():
            self._set_color(c.name())

    def _on_size_changed(self, val: int):
        self.shape.stroke_width = val
        self.lbl_size.setText(tr("prop_stroke_width", "Толщина: {val} px", val=val))
        self.shape_modified.emit()

    def _on_arrow_style_changed(self, idx: int):
        s_key = self.combo_arrow.itemData(idx)
        if s_key:
            self.shape.arrow_style = s_key
            self.shape_modified.emit()

    def _set_arrow_filled(self, filled: bool):
        self.shape.filled = filled
        style_toggle_btn(self.btn_arrow_out, not filled, self.is_dark)
        style_toggle_btn(self.btn_arrow_fill, filled, self.is_dark)
        self.shape_modified.emit()

    def _on_line_style_changed(self, idx: int):
        s_key = self.combo_line.itemData(idx)
        if s_key:
            self.shape.line_style = s_key
            self.shape_modified.emit()

    def _on_rect_style_changed(self, idx: int):
        s_key = self.combo_rect.itemData(idx)
        if s_key:
            self.shape.is_rounded = (s_key == "rounded")
            self.shape_modified.emit()

    def _set_filled(self, filled: bool):
        self.shape.filled = filled
        style_toggle_btn(self.btn_out, not filled, self.is_dark)
        style_toggle_btn(self.btn_fill, filled, self.is_dark)
        self.fill_opt_container.setVisible(filled)
        self.adjustSize()
        self.shape_modified.emit()

    def _set_gradient(self, is_grad: bool):
        self.shape.is_gradient = is_grad
        style_toggle_btn(self.btn_solid, not is_grad, self.is_dark)
        style_toggle_btn(self.btn_grad, is_grad, self.is_dark)
        self.grad_colors_widget.setVisible(is_grad)
        self.adjustSize()
        self.shape_modified.emit()

    def _pick_grad_col(self, idx: int):
        cur = self.shape.gradient_color1 if idx == 1 else self.shape.gradient_color2
        c = QColorDialog.getColor(QColor(cur), self, tr("shape_edit_grad_picker_title", "Выбор цвета градиента {idx}", idx=idx))
        if c.isValid():
            if idx == 1:
                self.shape.gradient_color1 = c.name()
                self.btn_g1.setStyleSheet(f"background-color: {c.name()}; color: #fff; font-weight: bold; border-radius: 4px;")
            else:
                self.shape.gradient_color2 = c.name()
                self.btn_g2.setStyleSheet(f"background-color: {c.name()}; color: #fff; font-weight: bold; border-radius: 4px;")
            self.shape_modified.emit()

    def _on_opacity_changed(self, val: int):
        self.lbl_opacity.setText(tr("prop_fill_opacity", "Прозрачность заливки: {pct}%", pct=val))
        self.shape.fill_alpha = int(val * 255 / 100)
        self.shape_modified.emit()

    def _on_hl_opacity_changed(self, val: int):
        self.lbl_hl.setText(tr("prop_hl_opacity", "Непрозрачность маркера: {pct}%", pct=val))
        self.shape.alpha = int(val * 255 / 100)
        self.shape_modified.emit()

    def _sync_to_parent_tool_config(self, key: str, val):
        p = self.parent()
        if p and hasattr(p, "right_toolbar") and hasattr(p.right_toolbar, "tools_config"):
            tcfg = p.right_toolbar.tools_config.setdefault("text", {})
            tcfg[key] = val
            if key == "color":
                p.right_toolbar.current_color = val
                p.right_toolbar._update_color_swatch()
            if hasattr(p.right_toolbar, "properties_flyout") and p.right_toolbar.properties_flyout.isVisible():
                p.right_toolbar.properties_flyout.load_tool("text", tcfg)

    def _on_text_changed(self, txt: str):
        self.shape.text = txt
        self.shape.name = tr("shape_edit_text_prefix", "Текст: '{txt}'", txt=txt[:10]) if txt else tr("shape_edit_text_default", "Текст")
        self.shape_modified.emit()

    def _on_text_font_changed(self, fam: str):
        self.shape.font_family = fam
        self._sync_to_parent_tool_config("font_family", fam)
        self.shape_modified.emit()

    def _on_text_size_changed(self, val: int):
        self.shape.font_size = val
        self.lbl_font_size.setText(tr("prop_font_size", "Размер шрифта: {val} pt", val=val))
        self._sync_to_parent_tool_config("size", val)
        self.shape_modified.emit()

    def _on_text_bold_toggled(self, checked: bool):
        self.shape.is_bold = checked
        style_toggle_btn(self.btn_bold, checked, self.is_dark)
        self._sync_to_parent_tool_config("is_bold", checked)
        self.shape_modified.emit()

    def _on_text_underline_toggled(self, checked: bool):
        self.shape.is_underline = checked
        style_toggle_btn(self.btn_underline, checked, self.is_dark)
        self._sync_to_parent_tool_config("is_underline", checked)
        self.shape_modified.emit()

    def _on_text_italic_toggled(self, checked: bool):
        self.shape.is_italic = checked
        style_toggle_btn(self.btn_italic, checked, self.is_dark)
        self._sync_to_parent_tool_config("is_italic", checked)
        self.shape_modified.emit()

    def _pick_screen_color(self):
        p = self.parent()
        if p and hasattr(p, "start_eyedropper"):
            self.hide()
            p.start_eyedropper(callback=self._on_eyedropper_picked)
        else:
            self._pick_custom_color()

    def _on_eyedropper_picked(self, color):
        if color:
            hex_str = color.name() if hasattr(color, "name") else str(color)
            self._set_color(hex_str)
        self.show()

    def closeEvent(self, event):
        new_props = self._snapshot_props(self.shape)
        if new_props != self.old_props:
            self.editing_finished.emit(self.old_props, new_props)
        self.closed.emit()
        super().closeEvent(event)
