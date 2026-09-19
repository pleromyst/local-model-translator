"""Single-instance coordination through Qt local IPC."""

from __future__ import annotations

import hashlib
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal, Slot
from PySide6.QtNetwork import QLocalServer, QLocalSocket


DEFAULT_SERVER_NAME = "LocalLens.SingleInstance.v1"
_ACTIVATE_MESSAGE = b"activate\n"
_ACKNOWLEDGED_MESSAGE = b"ok\n"


class SingleInstance(QObject):
    """Own one local server or notify the already-running LocalLens process."""

    activation_requested = Signal()

    def __init__(
        self,
        server_name: str = DEFAULT_SERVER_NAME,
        *,
        connection_timeout_ms: int = 1500,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._server_name = server_name
        self._connection_timeout_ms = connection_timeout_ms
        lock_suffix = hashlib.sha256(server_name.encode("utf-8")).hexdigest()[:20]
        lock_path = Path(tempfile.gettempdir()) / f"locallens-{lock_suffix}.lock"
        self._lock = QLockFile(str(lock_path))
        self._server = QLocalServer(self)
        self._server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self._server.newConnection.connect(self._accept_connections)
        self._is_primary = False

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    def acquire(self) -> bool:
        """Become primary, or wake the primary instance and return ``False``."""

        if self._is_primary:
            return True
        if self._lock.tryLock(0):
            QLocalServer.removeServer(self._server_name)
            if not self._server.listen(self._server_name):
                self._lock.unlock()
                return False
            self._is_primary = True
            return True

        if self.notify_existing_instance():
            return False

        # Recover only when Qt verifies that the lock belongs to a dead process.
        if self._lock.removeStaleLockFile() and self._lock.tryLock(0):
            QLocalServer.removeServer(self._server_name)
            if not self._server.listen(self._server_name):
                self._lock.unlock()
                return False
            self._is_primary = True
            return True
        return False

    def notify_existing_instance(self) -> bool:
        deadline = time.monotonic() + self._connection_timeout_ms / 1000
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self._server_name)
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            if socket.waitForConnected(min(200, remaining_ms)):
                socket.write(_ACTIVATE_MESSAGE)
                socket.flush()
                socket.waitForBytesWritten(remaining_ms)
                acknowledged = socket.waitForReadyRead(remaining_ms) and (
                    _ACKNOWLEDGED_MESSAGE.strip()
                    in bytes(socket.readAll()).splitlines()
                )
                socket.disconnectFromServer()
                if socket.state() != QLocalSocket.LocalSocketState.UnconnectedState:
                    socket.waitForDisconnected(remaining_ms)
                return acknowledged
            socket.abort()
            time.sleep(0.025)
        return False

    @Slot()
    def _accept_connections(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                continue
            socket.readyRead.connect(
                lambda socket=socket: self._read_message(socket)
            )
            socket.disconnected.connect(socket.deleteLater)
            self._read_message(socket)

    def _read_message(self, socket: QLocalSocket) -> None:
        if _ACTIVATE_MESSAGE.strip() in bytes(socket.readAll()).splitlines():
            self.activation_requested.emit()
            socket.write(_ACKNOWLEDGED_MESSAGE)
            socket.flush()

    def close(self) -> None:
        if not self._is_primary:
            return
        self._server.close()
        QLocalServer.removeServer(self._server_name)
        self._lock.unlock()
        self._is_primary = False
