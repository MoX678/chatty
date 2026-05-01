"""Admin GUI for the Chatty server.

Pure UI — `ServerWindow` listens to `ChatServer` signals and renders
them: a status pill, host/port controls, a colored event log, and a
live list of connected users. No network logic of its own.
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
    QWidget,
)

import theme as T
from server import ChatServer, DEFAULT_HOST, DEFAULT_PORT


LEVEL_COLORS = {
    "info": "#A8B0C0",
    "warn": "#F5C26B",
    "err":  "#FF6B6B",
}

# Path to the client entry point we spawn from the toolbar.
CLIENT_MAIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "client", "main.py",
)


class ServerWindow(QMainWindow):
    """Admin dashboard. Owns one `ChatServer` instance."""

    def __init__(self) -> None:
        super().__init__()
        self.server = ChatServer()
        self.setWindowTitle("Chatty Server")
        self.setObjectName("ChatWindow")
        self.resize(900, 620)
        self.setMinimumSize(720, 520)

        self._build_ui()
        self._wire_signals()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(12)

        outer.addWidget(self._build_header())
        outer.addWidget(self._build_controls())

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self._build_log_pane(), 2)
        body.addWidget(self._build_users_pane(), 1)
        outer.addLayout(body, 1)

    def _build_header(self) -> QWidget:
        f = QFrame()
        f.setObjectName("chatHeader")
        T.apply_shadow(f, "sm")
        h = QHBoxLayout(f)
        h.setContentsMargins(18, 14, 18, 14)
        h.setSpacing(12)

        logo = QLabel()
        logo.setPixmap(T.make_pixmap("logo", size=26))
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("Chatty Server")
        title.setObjectName("chatTitle")
        sub = QLabel("Admin dashboard")
        sub.setObjectName("chatSub")
        title_col.addWidget(title)
        title_col.addWidget(sub)

        # status pill
        self.status_dot = QLabel()
        self.status_dot.setPixmap(T.make_pixmap(
            "dot", size=10,
            c1=T.palette()["muted_foreground"],
            c2=T.palette()["muted_foreground"],
        ))
        self.status_label = QLabel("STOPPED")
        self.status_label.setObjectName("sectionLabel")

        pill = QFrame()
        pill.setObjectName("systemEventPill")
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(12, 5, 14, 5)
        pl.setSpacing(8)
        pl.addWidget(self.status_dot)
        pl.addWidget(self.status_label)

        h.addWidget(logo)
        h.addLayout(title_col)
        h.addStretch(1)
        h.addWidget(pill)
        return f

    def _build_controls(self) -> QWidget:
        f = QFrame()
        f.setObjectName("chatHeader")
        T.apply_shadow(f, "sm")
        h = QHBoxLayout(f)
        h.setContentsMargins(18, 12, 18, 12)
        h.setSpacing(10)

        self.host_input = QLineEdit(DEFAULT_HOST)
        self.host_input.setObjectName("loginField")
        self.host_input.setPlaceholderText("host")
        self.host_input.setFixedWidth(160)

        self.port_input = QLineEdit(str(DEFAULT_PORT))
        self.port_input.setObjectName("loginField")
        self.port_input.setPlaceholderText("port")
        self.port_input.setFixedWidth(90)

        self.start_btn = QPushButton("  Start")
        self.start_btn.setObjectName("sendBtn")
        self.start_btn.setIcon(T.make_icon("send", size=14))
        self.start_btn.setIconSize(QSize(14, 14))
        self.start_btn.setMinimumHeight(36)
        self.start_btn.clicked.connect(self._on_start)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("iconBtn")
        self.stop_btn.setMinimumHeight(36)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)

        self.spawn_btn = QPushButton("  Spawn Client")
        self.spawn_btn.setObjectName("iconBtn")
        self.spawn_btn.setIcon(T.make_icon("user", size=14))
        self.spawn_btn.setIconSize(QSize(14, 14))
        self.spawn_btn.setMinimumHeight(36)
        self.spawn_btn.setToolTip("Launch a new client process")
        self.spawn_btn.clicked.connect(self._on_spawn)

        h.addWidget(QLabel("HOST"))
        h.addWidget(self.host_input)
        h.addSpacing(6)
        h.addWidget(QLabel("PORT"))
        h.addWidget(self.port_input)
        h.addStretch(1)
        h.addWidget(self.spawn_btn)
        h.addWidget(self.stop_btn)
        h.addWidget(self.start_btn)
        return f

    def _build_log_pane(self) -> QWidget:
        f = QFrame()
        f.setObjectName("chatHeader")
        T.apply_shadow(f, "sm")
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        head = QLabel("EVENT LOG")
        head.setObjectName("sectionLabel")
        v.addWidget(head)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setObjectName("chatScroll")
        self.log_view.setStyleSheet(
            f"background: {T.palette()['input']};"
            f"color: {T.palette()['foreground']};"
            f"border: 1px solid {T.palette()['border']};"
            f"border-radius: {T.RADIUS_MD}px; padding: 10px;"
            "font-family: ui-monospace, Consolas, Menlo, monospace;"
            "font-size: 12.5px;"
        )
        v.addWidget(self.log_view, 1)
        return f

    def _build_users_pane(self) -> QWidget:
        f = QFrame()
        f.setObjectName("chatHeader")
        T.apply_shadow(f, "sm")
        v = QVBoxLayout(f)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(8)

        head = QLabel("CONNECTED")
        head.setObjectName("sectionLabel")
        v.addWidget(head)

        self.users_list = QListWidget()
        self.users_list.setObjectName("navList")
        self.users_list.setIconSize(QSize(18, 18))
        v.addWidget(self.users_list, 1)
        return f

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    def _wire_signals(self) -> None:
        # All emitter calls happen on worker threads, so Qt auto-queues these.
        self.server.log_message.connect(self._on_log)
        self.server.users_changed.connect(self._on_users)
        self.server.state_changed.connect(self._on_state)

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        host = self.host_input.text().strip() or DEFAULT_HOST
        try:
            port = int(self.port_input.text().strip() or DEFAULT_PORT)
        except ValueError:
            QMessageBox.warning(self, "Invalid port", "Port must be a number.")
            return
        self.server.start(host, port)

    def _on_stop(self) -> None:
        self.server.stop()

    def _on_spawn(self) -> None:
        """Launch a new `client/main.py` as a detached subprocess.

        Uses the same Python interpreter currently running the server.
        Each call spawns an independent client window so you can sign in
        as several different users from one machine."""
        if not os.path.exists(CLIENT_MAIN):
            QMessageBox.warning(self, "Client not found", CLIENT_MAIN)
            return
        try:
            # Detach: don't pipe stdio, don't wait. On Windows we also
            # break the console group so closing the server doesn't
            # signal the spawned clients.
            kwargs = {
                "cwd": os.path.dirname(CLIENT_MAIN),
                "stdin":  subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "close_fds": True,
            }
            if sys.platform == "win32":
                kwargs["creationflags"] = (
                    subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                )
            else:
                kwargs["start_new_session"] = True
            subprocess.Popen([sys.executable, CLIENT_MAIN], **kwargs)
            self.server.log_message.emit("info", "spawned a new client")
        except OSError as e:
            QMessageBox.warning(self, "Spawn failed", str(e))

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------

    def _on_log(self, level: str, text: str) -> None:
        color = LEVEL_COLORS.get(level, "#E6E6EE")
        ts = datetime.now().strftime("%H:%M:%S")
        line = (
            f'<span style="color:#6B7280;">[{ts}]</span> '
            f'<span style="color:{color};font-weight:600;">{level.upper():4}</span> '
            f'<span style="color:#E6E6EE;">{self._escape(text)}</span>'
        )
        self.log_view.appendHtml(line)
        # auto-scroll
        c = self.log_view.textCursor()
        c.movePosition(QTextCursor.MoveOperation.End)
        self.log_view.setTextCursor(c)

    def _on_users(self, users: list) -> None:
        self.users_list.clear()
        for u in users:
            self.users_list.addItem(
                QListWidgetItem(T.make_icon("user", size=18), f"  {u}")
            )

    def _on_state(self, listening: bool, addr: str) -> None:
        p = T.palette()
        if listening:
            self.status_dot.setPixmap(T.make_pixmap(
                "dot", size=10,
                c1=p["online_grad_to"], c2=p["online"],
            ))
            self.status_label.setText(f"LISTENING · {addr}")
        else:
            self.status_dot.setPixmap(T.make_pixmap(
                "dot", size=10,
                c1=p["muted_foreground"], c2=p["muted_foreground"],
            ))
            self.status_label.setText("STOPPED")
        self.start_btn.setEnabled(not listening)
        self.stop_btn.setEnabled(listening)
        self.host_input.setEnabled(not listening)
        self.port_input.setEnabled(not listening)

    @staticmethod
    def _escape(text: str) -> str:
        return (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, ev) -> None:
        try:
            self.server.stop()
        except Exception:
            pass
        super().closeEvent(ev)


__all__ = ["ServerWindow"]
