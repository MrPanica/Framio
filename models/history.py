# -*- coding: utf-8 -*-
"""
Менеджер истории действий с поддержкой Undo/Redo и списком операций.
"""

from PyQt6.QtCore import QObject, pyqtSignal

class HistoryCommand:
    def __init__(self, description: str, do_func, undo_func):
        self.description = description
        self.do_func = do_func
        self.undo_func = undo_func

    def redo(self):
        self.do_func()

    def undo(self):
        self.undo_func()


class HistoryManager(QObject):
    history_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.undo_stack: list[HistoryCommand] = []
        self.redo_stack: list[HistoryCommand] = []

    def execute_command(self, cmd: HistoryCommand):
        cmd.redo()
        self.undo_stack.append(cmd)
        self.redo_stack.clear()
        self.history_changed.emit()

    def push_already_done(self, cmd: HistoryCommand):
        """Если действие уже было выполнено в UI, просто добавляем в стек."""
        self.undo_stack.append(cmd)
        self.redo_stack.clear()
        self.history_changed.emit()

    def undo(self):
        if not self.undo_stack:
            return False
        cmd = self.undo_stack.pop()
        cmd.undo()
        self.redo_stack.append(cmd)
        self.history_changed.emit()
        return True

    def redo(self):
        if not self.redo_stack:
            return False
        cmd = self.redo_stack.pop()
        cmd.redo()
        self.undo_stack.append(cmd)
        self.history_changed.emit()
        return True

    def can_undo(self) -> bool:
        return len(self.undo_stack) > 0

    def can_redo(self) -> bool:
        return len(self.redo_stack) > 0

    def get_log(self) -> list[tuple[str, bool]]:
        """Возвращает список кортежей (описание_действия, применено_ли)."""
        result = []
        for cmd in self.undo_stack:
            result.append((cmd.description, True))
        for cmd in reversed(self.redo_stack):
            result.append((cmd.description, False))
        return result

    def jump_to_step(self, target_undo_count: int):
        """Откатывает или накатывает историю до указанного количества выполненных шагов."""
        while len(self.undo_stack) > target_undo_count:
            if not self.undo():
                break
        while len(self.undo_stack) < target_undo_count:
            if not self.redo():
                break

    def clear(self):
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.history_changed.emit()
