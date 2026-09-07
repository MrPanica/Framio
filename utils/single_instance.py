# -*- coding: utf-8 -*-
"""
Менеджер предотвращения повторного запуска приложения (Single Instance).
Использует QLocalServer и QLocalSocket для межпроцессного взаимодействия (IPC).
Вторичные копии отправляют свои аргументы первичному процессу и мгновенно завершаются,
что исключает появление дублирующих иконок в системном трее и конфликты горячих клавиш.
"""

import sys
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

    def check_single_instance(self, payload: str = "") -> bool:
        """
        Проверяет статус запуска.
        Возвращает True, если текущий процесс является первичным (главным).
        Возвращает False, если процесс вторичный (сообщение отправлено первичному процессу).
        """
        socket = QLocalSocket(self)
        socket.connectToServer(self.server_name)

        if socket.waitForConnected(300):
            # Вторичный экземпляр: передаем команду / аргументы
            cmd = payload if payload else "activate"
            data = cmd.encode("utf-8")
            socket.write(data)
            socket.flush()
            socket.waitForBytesWritten(1000)
            self.client_socket = socket
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
            # Если не удалось запустить сервер, все же разрешаем запуск как первичного
            self.is_primary = True
            return True

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
