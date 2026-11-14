"""One app window per user: opening the app again (desktop icon, dock, menu)
while it is already running brings the open window to the front instead of
starting a second copy that cannot see the running campaign."""

from __future__ import annotations

import getpass
import os
import re

from PySide6.QtNetwork import QLocalServer, QLocalSocket

# Wayland only lets a window take focus with the token the launcher gave the new process.
_TOKENS = ("XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID")


def server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "crisp-automation-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)


def ask_running_copy_to_show(name: str | None = None, timeout_ms: int = 800) -> bool:
    """True when another copy is running and was asked to show its window."""
    sock = QLocalSocket()
    sock.connectToServer(name or server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    token = next((os.environ.get(k, "") for k in _TOKENS if os.environ.get(k)), "")
    sock.write(f"show {token}\n".encode())
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer:
    """Listens for 'show' from a second launch and calls on_show(token)."""

    def __init__(self, on_show, name: str | None = None):
        self.on_show = on_show
        self.name = name or server_name()
        self.server = QLocalServer()
        self.server.setSocketOptions(QLocalServer.UserAccessOption)
        if not self.server.listen(self.name):
            QLocalServer.removeServer(self.name)   # left behind by a crash
            self.server.listen(self.name)
        self.server.newConnection.connect(self._accept)

    def _accept(self):
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            sock.readyRead.connect(lambda s=sock: self._read(s))
            sock.disconnected.connect(sock.deleteLater)

    def _read(self, sock):
        line = bytes(sock.readAll()).decode(errors="ignore").strip()
        if line.startswith("show"):
            self.on_show(line[4:].strip())


def bring_to_front(window, token: str = "") -> None:
    """Restore and raise the window; on Wayland the launcher's token allows the focus change."""
    if token:
        os.environ["XDG_ACTIVATION_TOKEN" if os.environ.get("WAYLAND_DISPLAY") else "DESKTOP_STARTUP_ID"] = token
    if window.isMinimized():
        window.showNormal()
    window.show()
    window.raise_()
    window.activateWindow()
