# -*- coding: utf-8 -*-
"""
Окно просмотра истории действий (Undo/Redo лог).
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget
)
from models.history import HistoryManager

class HistoryDialog(QFrame):
    def __init__(self, history_manager: HistoryManager, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.history_manager = history_manager
        self.setFixedWidth(240)
        self.setMaximumHeight(320)

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
                border-radius: 4px;
                padding: 4px;
                text-align: left;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #3e4658;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("История действий")
        title.setStyleSheet("font-weight: bold; color: #98c379;")
        header.addWidget(title)

        btn_clear = QPushButton("Сброс")
        btn_clear.setFixedWidth(50)
        btn_clear.clicked.connect(self.history_manager.clear)
        header.addWidget(btn_clear)
        main_layout.addLayout(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background: transparent; border: none;")

        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(2)
        self.list_layout.addStretch()

        self.scroll.setWidget(self.list_container)
        main_layout.addWidget(self.scroll)

        self.history_manager.history_changed.connect(self.refresh_list)
        self.refresh_list()

    def refresh_list(self):
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        undo_stack = self.history_manager.undo_stack
        redo_stack = self.history_manager.redo_stack

        if not undo_stack and not redo_stack:
            lbl_empty = QLabel("История пуста")
            lbl_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl_empty.setStyleSheet("color: #7f848e; font-style: italic; padding: 10px;")
            self.list_layout.insertWidget(0, lbl_empty)
            return

        # Проходим по применённым действиям
        for i, cmd in enumerate(undo_stack):
            btn = QPushButton(f"✓  {cmd.description}")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #242832;
                    color: #dcdfe4;
                    border: 1px solid #363c4a;
                    border-radius: 4px;
                    padding: 4px 6px;
                }
                QPushButton:hover {
                    background-color: #313644;
                }
            """)
            btn.clicked.connect(lambda checked, idx=i+1: self._jump(idx))
            self.list_layout.insertWidget(self.list_layout.count() - 1, btn)

        # Проходим по отмененным действиям
        for j, cmd in enumerate(redo_stack):
            btn = QPushButton(f"↩  {cmd.description} (отменено)")
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #1a1d24;
                    color: #636d83;
                    border: 1px dashed #363c4a;
                    border-radius: 4px;
                    padding: 4px 6px;
                }
                QPushButton:hover {
                    background-color: #242832;
                    color: #98c379;
                }
            """)
            target_step = len(undo_stack) + (j + 1)
            btn.clicked.connect(lambda checked, idx=target_step: self._jump(idx))
            self.list_layout.insertWidget(self.list_layout.count() - 1, btn)

    def _jump(self, count):
        self.history_manager.jump_to_step(count)
        self.refresh_list()
