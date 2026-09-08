# -*- coding: utf-8 -*-
"""
Менеджер предотвращения повторного запуска приложения (Single Instance).
Использует QLocalServer и QLocalSocket для межпроцессного взаимодействия (IPC).
Вторичные копии отправляют свои аргументы первичному процессу и мгновенно завершаются,
что исключает появление дублирующих иконок в системном трее и конфликты горячих клавиш.
"""

import ctypes
import sys
import time
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket


SERVER_NAME = "Framio_SingleInstance_IPC"


class SingleInstanceManager(QObject):
    """
    Проверяет, запущен ли уже экземпляр Framio.
    Если да — передает параметры первичному процессу и сигнализирует о необходимости завершения.
    Если нет — регистрирует локальный IPC-сервер и принимает сообщения от вторичных процессов.
    """
    message_received = pyqtSignal(str)

    def __init__(self, server_name: str = SERVER_NAME, parent: QObject = None):
        super().__init__(parent)
        self.server_name = server_name
        self.server: QLocalServer | None = None
        self.is_primary: bool = False
        self._mutex_handle = None

    def _acquire_process_mutex(self):
        """Атомарно резервирует имя процесса на Windows.

        Возвращает ``True`` для primary, ``False`` для вторичного запуска и
        ``None`` только если системный mutex недоступен (например, не-Windows).
        """
        if sys.platform != "win32":
            return None
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.restype = ctypes.c_void_p
            handle = kernel32.CreateMutexW(None, True, f"Local\\{self.server_name}_Mutex")
            if not handle:
                return None
            if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
                kernel32.CloseHandle(handle)
                return False
            self._mutex_handle = handle
            self._kernel32 = kernel32
            return True
        except Exception:
            return None

    def _send_command(self, payload: str) -> bool:
        """Передаёт команду primary, включая короткое окно его запуска."""
        cmd = (payload or "activate").encode("utf-8")
        for _ in range(20):
            socket = QLocalSocket(self)
            socket.connectToServer(self.server_name)
            if socket.waitForConnected(100):
                socket.write(cmd)
                socket.flush()
                socket.waitForBytesWritten(500)
                self.client_socket = socket
                return True
            socket.abort()
            time.sleep(0.05)
        return False

    def check_single_instance(self, payload: str = "") -> bool:
        """
        Проверяет статус запуска.
        Возвращает True, если текущий процесс является первичным (главным).
        Возвращает False, если процесс вторичный (сообщение отправлено первичному процессу).
        """
        mutex_state = self._acquire_process_mutex()
        if mutex_state is False:
            # Mutex уже принадлежит первому процессу. Даже если его IPC-сервер
            # ещё не успел подняться, второй запуск не становится primary.
            self._send_command(payload)
            self.is_primary = False
            return False

        if mutex_state is None and self._send_command(payload):
            self.is_primary = False
            return False

        # Первичный экземпляр: очищаем старые пайпы и поднимаем сервер
        QLocalServer.removeServer(self.server_name)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_new_connection)

        if self.server.listen(self.server_name):
            self.is_primary = True
            return True
        else:
            # Не запускаем второй экземпляр без IPC-сервера: иначе следующий
            # запуск не сможет активировать текущую сессию.
            self.cleanup()
            self.is_primary = False
            return False

    def _on_new_connection(self):
        """Принимает входящее IPC-соединение от вторичного процесса."""
        if not self.server:
            return
        client_socket = self.server.nextPendingConnection()
        if not client_socket:
            return

        if not hasattr(self, "_client_sockets"):
            self._client_sockets = []
        self._client_sockets.append(client_socket)

        def handle_ready_read():
            data = client_socket.readAll().data()
            try:
                msg = data.decode("utf-8").strip()
            except Exception:
                msg = ""
            if msg:
                self.message_received.emit(msg)
            if client_socket in getattr(self, "_client_sockets", []):
                self._client_sockets.remove(client_socket)
            client_socket.disconnectFromServer()

        client_socket.readyRead.connect(handle_ready_read)
        if client_socket.bytesAvailable() > 0:
            handle_ready_read()

    def cleanup(self):
        """Останавливает сервер при выходе из приложения."""
        if self.server:
            self.server.close()
            self.server = None
        QLocalServer.removeServer(self.server_name)
        if self._mutex_handle:
            try:
                self._kernel32.CloseHandle(self._mutex_handle)
            except Exception:
                pass
            self._mutex_handle = None
