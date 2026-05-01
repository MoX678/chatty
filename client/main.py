"""Chatty client — entry point.

Responsibilities:
1. Build a `QApplication`, install fonts and the global stylesheet.
2. Show the login dialog and authenticate against the server.
3. Hand the live `NetworkClient` connection off to `ChatWindow`.

Everything else lives in dedicated modules:
    - `utils.py`         — pure helpers, constants, timestamp / pixmap utils.
    - `widgets.py`       — small reusable Qt widgets (bubbles, DM rows…).
    - `controller.py`    — `ChatController`: state, network glue, business logic.
    - `chat_window.py`   — `LoginDialog` + `ChatWindow` (pure UI shell).
    - `network.py`       — TCP client + per-user log management.
    - `theme.py`         — palette, QSS templates, icon factories.
"""
from __future__ import annotations

import os
import sys

from PyQt6.QtWidgets import (
    QApplication, QDialog, QLabel, QMessageBox, QVBoxLayout,
)

# allow running as `python client/main.py`
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import theme as T
from network import NetworkClient
from utils import set_app_font
from controller import ChatController
from chat_window import ChatWindow, LoginDialog


def _connect_and_authenticate(net: NetworkClient, username: str):
    """Spin up the network thread and block on the login result.

    Buffers any inbound state that arrives before `ChatWindow` is ready so
    nothing is lost in the gap between auth_success and window construction.
    Returns `(ok, reason, buffered_events)`."""
    buf_users:  list = []
    buf_sys:    list = []
    buf_dm:     list = []
    buf_group:  list = []

    def _buf_users(users):           buf_users[:] = list(users)
    def _buf_sys(event_type, username):           buf_sys.append((event_type, username))
    def _buf_dm(sender, target, message, attachment):      buf_dm.append((sender, target, message, attachment))
    def _buf_group(sender, target, message, attachment):   buf_group.append((sender, target, message, attachment))

    net.user_list_changed.connect(_buf_users)
    net.system_event.connect(_buf_sys)
    net.dm_received.connect(_buf_dm)
    net.group_received.connect(_buf_group)

    result = {"ok": False, "reason": ""}

    connecting = QDialog()
    connecting.setWindowTitle("Connecting…")
    connecting.setModal(True)
    connecting.setFixedSize(320, 110)
    cv = QVBoxLayout(connecting)
    cv.addWidget(QLabel(f"Signing in as {username}…"))
    cv.addStretch(1)

    def _on_ok():
        result["ok"] = True
        connecting.accept()

    def _on_fail(reason):
        result["ok"] = False
        result["reason"] = reason
        connecting.reject()

    def _on_err(msg):
        result["ok"] = False
        result["reason"] = msg
        connecting.reject()

    net.auth_success.connect(_on_ok)
    net.auth_failed.connect(_on_fail)
    net.connection_error.connect(_on_err)

    net.start()
    connecting.exec()

    # Detach buffer + auth-only slots before handing the connection to
    # the main window (which will register its own slots).
    try:
        net.auth_success.disconnect(_on_ok)
        net.auth_failed.disconnect(_on_fail)
        net.connection_error.disconnect(_on_err)
        net.user_list_changed.disconnect(_buf_users)
        net.system_event.disconnect(_buf_sys)
        net.dm_received.disconnect(_buf_dm)
        net.group_received.disconnect(_buf_group)
    except TypeError:
        # Some signals may already be disconnected if auth failed early.
        pass

    return result["ok"], result["reason"], (buf_users, buf_sys, buf_dm, buf_group)


def _replay_buffer(ctrl: ChatController, buffers) -> None:
    """Feed events that arrived during connect into the controller."""
    buf_users, buf_sys, buf_dm, buf_group = buffers
    if buf_users:
        ctrl.on_user_list(buf_users)
    for (event_type, username) in buf_sys:
        ctrl.on_system_event(event_type, username)
    for (sender, target, message, attachment) in buf_dm:
        ctrl.on_dm(sender, target, message, attachment)
    for (sender, target, message, attachment) in buf_group:
        ctrl.on_group(sender, target, message, attachment)



def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(" Chatty")
    set_app_font(app)
    app.setStyleSheet(T.build_qss(T.palette(dark=True)))

    while True:
        login = LoginDialog()
        if login.exec() != QDialog.DialogCode.Accepted:
            return 0
        username, password, host, port = login.credentials()

        net = NetworkClient(host, port, username, password)
        ok, reason, buffers = _connect_and_authenticate(net, username)

        if not ok:
            net.stop()
            net.wait(1500)
            QMessageBox.warning(
                None, "Sign in failed",
                f"Could not sign in: {reason or 'unknown'}",
            )
            continue

        ctrl = ChatController(net, username)
        win  = ChatWindow(ctrl)
        _replay_buffer(ctrl, buffers)
        win.show()
        return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
