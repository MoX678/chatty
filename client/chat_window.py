"""Top-level windows for the chat client.

`LoginDialog`  — credential entry.
`ChatWindow`   — pure UI: sidebar, chat area, composer, tray.

The chat window owns no business logic; it asks `ChatController`
(in `controller.py`) for state and forwards user actions to it.
Custom widgets live in `widgets.py`; helpers in `utils.py`.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QSize, QTimer
from PyQt6.QtGui import QIcon, QImage, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea,
    QSystemTrayIcon, QVBoxLayout, QWidget,
)

import theme as T
from controller import ChatController
from utils import (
    DEFAULT_HOST, DEFAULT_PORT, GROUPS, IMAGE_EXTS,
    avatar_pixmap, round_pixmap,
)
from widgets import (
    DMRow, ImageRow, MessageInput, MessageRow, SystemRow,
)


# ---------------------------------------------------------------------------
# Login dialog
# ---------------------------------------------------------------------------

class LoginDialog(QDialog):
    """Simple login dialog. Returns username/password/host/port on accept."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("loginDialog")
        self.setWindowTitle("Sign in")
        self.setModal(True)
        self.setMinimumWidth(420)

        self.user = QLineEdit()
        self.user.setObjectName("loginField")
        self.user.setPlaceholderText("username")

        self.pw = QLineEdit()
        self.pw.setObjectName("loginField")
        self.pw.setPlaceholderText("password")
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)

        self.host = QLineEdit(DEFAULT_HOST)
        self.host.setObjectName("loginField")
        self.host.setPlaceholderText("host")

        self.port = QLineEdit(str(DEFAULT_PORT))
        self.port.setObjectName("loginField")
        self.port.setPlaceholderText("port")

        self.error_lbl = QLabel("")
        self.error_lbl.setObjectName("loginError")
        self.error_lbl.setVisible(False)

        sign_in = QPushButton("  Sign in")
        sign_in.setObjectName("sendBtn")
        sign_in.setIcon(T.make_icon("send", size=16))
        sign_in.setIconSize(QSize(16, 16))
        sign_in.setMinimumHeight(42)
        sign_in.clicked.connect(self._submit)
        self.pw.returnPressed.connect(self._submit)
        self.user.returnPressed.connect(self._submit)

        card = QFrame()
        card.setObjectName("loginCard")
        T.apply_shadow(card, "lg")
        cl = QVBoxLayout(card)
        cl.setContentsMargins(28, 28, 28, 28)
        cl.setSpacing(14)

        logo_row = QHBoxLayout()
        logo_lbl = QLabel()
        logo_lbl.setPixmap(T.make_pixmap("logo", size=34))
        title = QLabel("Welcome back")
        title.setObjectName("loginTitle")
        sub = QLabel("Sign in to your real-time workspace.")
        sub.setObjectName("loginSub")
        logo_row.addWidget(logo_lbl)
        logo_row.addSpacing(10)
        title_col = QVBoxLayout()
        title_col.addWidget(title)
        title_col.addWidget(sub)
        logo_row.addLayout(title_col)
        logo_row.addStretch(1)
        cl.addLayout(logo_row)
        cl.addSpacing(6)

        cl.addWidget(self._labeled("USERNAME", self.user))
        cl.addWidget(self._labeled("PASSWORD", self.pw))

        host_row = QHBoxLayout()
        host_row.setSpacing(10)
        host_row.addWidget(self._labeled("HOST", self.host), 2)
        host_row.addWidget(self._labeled("PORT", self.port), 1)
        cl.addLayout(host_row)

        cl.addWidget(self.error_lbl)
        cl.addWidget(sign_in)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 28, 28, 28)
        outer.addWidget(card)

        self.user.setFocus()

    def _labeled(self, label_text: str, widget: QWidget) -> QWidget:
        wrap = QWidget()
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        lbl = QLabel(label_text)
        lbl.setObjectName("sectionLabel")
        v.addWidget(lbl)
        v.addWidget(widget)
        return wrap

    def show_error(self, text: str) -> None:
        self.error_lbl.setText(text)
        self.error_lbl.setVisible(True)

    def _submit(self) -> None:
        if not self.user.text().strip() or not self.pw.text():
            self.show_error("Username and password are required.")
            return
        self.accept()

    def credentials(self) -> tuple[str, str, str, int]:
        try:
            port = int(self.port.text().strip() or DEFAULT_PORT)
        except ValueError:
            port = DEFAULT_PORT
        return (
            self.user.text().strip(),
            self.pw.text(),
            self.host.text().strip() or DEFAULT_HOST,
            port,
        )


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class ChatWindow(QMainWindow):
    """Pure UI shell. All state lives in `ChatController`."""

    def __init__(self, controller: ChatController):
        super().__init__()
        self.ctrl = controller
        self.username = controller.username

        self.setWindowTitle(f"Chat · {self.username}")
        self.setObjectName("ChatWindow")
        self.resize(1180, 760)
        self.setMinimumSize(960, 620)
        self.setAcceptDrops(True)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setContentsMargins(14, 14, 14, 14)
        h.setSpacing(14)

        self._build_sidebar(h)
        self._build_chat_area(h)
        self._build_tray()

        self._connect_controller()
        # Load disk history + start network listening (after signals wired).
        self.ctrl.bootstrap()

    # ------------------------------------------------------------------
    # Controller wiring
    # ------------------------------------------------------------------

    def _connect_controller(self) -> None:
        c = self.ctrl
        c.history_changed.connect(self._on_history_changed)
        c.entry_appended.connect(self._on_entry_appended)
        c.dm_list_changed.connect(self._refresh_dm_list)
        c.subtitle_changed.connect(self.subtitle_lbl.setText)
        c.notify_requested.connect(self._notify)
        c.pending_changed.connect(self._on_pending_changed)
        c.warning_requested.connect(self._show_warning)
        c.info_requested.connect(self._show_info)
        c.disconnected.connect(self._on_disconnected)

    # ------------------------------------------------------------------
    # Sidebar
    # ------------------------------------------------------------------

    def _build_sidebar(self, parent_layout: QHBoxLayout) -> None:
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(280)
        T.apply_shadow(side, "md")
        v = QVBoxLayout(side)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(14)

        # brand
        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(T.make_pixmap("logo", size=28))
        b = QLabel(" Chatty")
        b.setObjectName("brand")
        brand_row.addWidget(logo)
        brand_row.addWidget(b)
        brand_row.addStretch(1)
        v.addLayout(brand_row)

        # GROUPS
        gl = QLabel("GROUPS")
        gl.setObjectName("sectionLabel")
        v.addWidget(gl)

        self.groups_list = QListWidget()
        self.groups_list.setObjectName("navList")
        self.groups_list.setIconSize(QSize(18, 18))
        self.groups_list.setSpacing(0)
        self.groups_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        for g in GROUPS:
            it = QListWidgetItem(T.make_icon("hash", size=18), f"  {g.title()}")
            it.setData(Qt.ItemDataRole.UserRole, f"group_{g}")
            self.groups_list.addItem(it)
        self.groups_list.itemClicked.connect(self._on_nav_clicked)
        self.groups_list.setFixedHeight(56)
        v.addWidget(self.groups_list)

        # DMs
        pl = QLabel("DIRECT MESSAGES")
        pl.setObjectName("sectionLabel")
        v.addWidget(pl)

        self.people_list = QListWidget()
        self.people_list.setObjectName("navList")
        self.people_list.setIconSize(QSize(18, 18))
        self.people_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.people_list.itemClicked.connect(self._on_nav_clicked)
        v.addWidget(self.people_list, 1)

        # Bottom user badge → dropdown with Sign out.
        self.me_btn = QPushButton()
        self.me_btn.setObjectName("meBadge")
        self.me_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.me_btn.setMinimumHeight(56)

        mb = QHBoxLayout(self.me_btn)
        mb.setContentsMargins(10, 8, 10, 8)
        mb.setSpacing(10)
        avatar_lbl = QLabel(self.me_btn)
        avatar_lbl.setFixedSize(34, 34)
        avatar_lbl.setPixmap(avatar_pixmap(self.username, 34))
        avatar_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        me_col = QVBoxLayout()
        me_col.setSpacing(0)
        nm = QLabel(self.username, self.me_btn)
        nm.setObjectName("userBadgeName")
        nm.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        st_row = QHBoxLayout()
        st_row.setSpacing(6)
        st_row.setContentsMargins(0, 0, 0, 0)
        st_dot = QLabel(self.me_btn)
        st_dot.setPixmap(T.make_pixmap(
            "dot", size=10,
            c1=T.palette()["online_grad_to"], c2=T.palette()["online"],
        ))
        st_dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        st_lbl = QLabel("online", self.me_btn)
        st_lbl.setObjectName("userBadgeStatus")
        st_lbl.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        st_row.addWidget(st_dot)
        st_row.addWidget(st_lbl)
        st_row.addStretch(1)
        me_col.addWidget(nm)
        me_col.addLayout(st_row)

        chevron = QLabel("⌃", self.me_btn)
        chevron.setObjectName("userBadgeChevron")
        chevron.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        mb.addWidget(avatar_lbl)
        mb.addLayout(me_col, 1)
        mb.addWidget(chevron)

        self.me_btn.clicked.connect(self._show_user_menu)
        v.addWidget(self.me_btn)

        self.me_menu = QMenu(self)
        sign_out = self.me_menu.addAction(T.make_icon("logout", size=14), "Sign out")
        sign_out.triggered.connect(self.close)

        self.groups_list.setCurrentRow(0)
        parent_layout.addWidget(side)

    def _show_user_menu(self) -> None:
        global_top_left = self.me_btn.mapToGlobal(self.me_btn.rect().topLeft())
        size_hint = self.me_menu.sizeHint()
        self.me_menu.setMinimumWidth(self.me_btn.width())
        anchor = global_top_left
        anchor.setY(anchor.y() - size_hint.height() - 4)
        self.me_menu.exec(anchor)

    def _on_nav_clicked(self, item: QListWidgetItem) -> None:
        ctx = item.data(Qt.ItemDataRole.UserRole)
        if not ctx:
            return
        if ctx.startswith("group_"):
            self.people_list.clearSelection()
        else:
            self.groups_list.clearSelection()
        self.ctrl.set_context(ctx)

    def _refresh_dm_list(self) -> None:
        sel = self.people_list.currentItem()
        sel_ctx = sel.data(Qt.ItemDataRole.UserRole) if sel else None
        self.people_list.clear()

        for name, online, unread in self.ctrl.dm_rows():
            row = DMRow(name, online, unread)
            it  = QListWidgetItem()
            it.setData(Qt.ItemDataRole.UserRole, f"dm_{name}")
            it.setSizeHint(row.sizeHint())
            self.people_list.addItem(it)
            self.people_list.setItemWidget(it, row)
            if sel_ctx == it.data(Qt.ItemDataRole.UserRole):
                self.people_list.setCurrentItem(it)

    # ------------------------------------------------------------------
    # System tray + notifications
    # ------------------------------------------------------------------

    def _build_tray(self) -> None:
        tray_pix = T.make_pixmap("logo", size=64)
        app_icon = QIcon(tray_pix)
        self.setWindowIcon(app_icon)

        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = None
            return

        self.tray = QSystemTrayIcon(app_icon, self)
        self.tray.setToolTip(f" Chatty · {self.username}")

        tmenu = QMenu(self)
        open_act = tmenu.addAction("Open  Chatty")
        open_act.triggered.connect(self._show_from_tray)
        tmenu.addSeparator()
        quit_act = tmenu.addAction("Sign out")
        quit_act.triggered.connect(self.close)
        self.tray.setContextMenu(tmenu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _window_is_inactive(self) -> bool:
        return self.isMinimized() or not self.isActiveWindow() or not self.isVisible()

    def _notify(self, sender: str, body: str, ctx: str) -> None:
        # Audible cue regardless of focus (matches old behaviour).
        QApplication.beep()
        if not self._window_is_inactive():
            return
        QApplication.alert(self, 0)
        if self.tray is not None:
            preview = body if len(body) <= 140 else body[:137] + "…"
            self.tray.showMessage(
                sender, preview or "(image)",
                QSystemTrayIcon.MessageIcon.Information, 4000,
            )

    # ------------------------------------------------------------------
    # Chat area
    # ------------------------------------------------------------------

    def _build_chat_area(self, parent_layout: QHBoxLayout) -> None:
        wrap = QFrame()
        wrap.setObjectName("chatArea")
        v = QVBoxLayout(wrap)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(12)

        # header
        header = QFrame()
        header.setObjectName("chatHeader")
        T.apply_shadow(header, "sm")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 14, 14, 14)
        hl.setSpacing(12)

        self.header_icon = QLabel()
        self.header_icon.setPixmap(T.make_pixmap("hash", size=22))
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self.title_lbl = QLabel("General")
        self.title_lbl.setObjectName("chatTitle")
        self.subtitle_lbl = QLabel("Public group · all members")
        self.subtitle_lbl.setObjectName("chatSub")
        title_col.addWidget(self.title_lbl)
        title_col.addWidget(self.subtitle_lbl)
        hl.addWidget(self.header_icon)
        hl.addLayout(title_col)
        hl.addStretch(1)

        save_btn = QPushButton()
        save_btn.setObjectName("iconBtn")
        save_btn.setIcon(T.make_icon("save", size=18))
        save_btn.setIconSize(QSize(20, 20))
        save_btn.setFixedSize(40, 40)
        save_btn.setToolTip("Save chat as .txt")
        save_btn.clicked.connect(self._save_chat_dialog)
        hl.addWidget(save_btn)
        v.addWidget(header)

        # messages scroll
        self.scroll = QScrollArea()
        self.scroll.setObjectName("chatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self.msg_container = QWidget()
        self.msg_layout = QVBoxLayout(self.msg_container)
        self.msg_layout.setContentsMargins(8, 8, 8, 8)
        self.msg_layout.setSpacing(2)
        self.msg_layout.addStretch(1)
        self.scroll.setWidget(self.msg_container)
        v.addWidget(self.scroll, 1)

        # composer
        composer = QFrame()
        composer.setObjectName("composer")
        T.apply_shadow(composer, "sm")
        cv = QVBoxLayout(composer)
        cv.setContentsMargins(10, 8, 10, 8)
        cv.setSpacing(6)

        # staged-attachment chip
        self.chip_frame = QFrame()
        self.chip_frame.setObjectName("attachChip")
        ch = QHBoxLayout(self.chip_frame)
        ch.setContentsMargins(8, 6, 6, 6)
        ch.setSpacing(10)
        self.chip_thumb = QLabel()
        self.chip_thumb.setFixedSize(44, 44)
        self.chip_thumb.setObjectName("chipThumb")
        self.chip_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chip_text_col = QVBoxLayout()
        chip_text_col.setSpacing(0)
        self.chip_name = QLabel("")
        self.chip_name.setObjectName("chipName")
        self.chip_meta = QLabel("")
        self.chip_meta.setObjectName("chipMeta")
        chip_text_col.addWidget(self.chip_name)
        chip_text_col.addWidget(self.chip_meta)
        chip_close = QPushButton()
        chip_close.setObjectName("iconBtn")
        chip_close.setIcon(T.make_icon("x", size=14))
        chip_close.setIconSize(QSize(14, 14))
        chip_close.setFixedSize(28, 28)
        chip_close.setToolTip("Remove attachment")
        chip_close.clicked.connect(self.ctrl.clear_pending)
        ch.addWidget(self.chip_thumb)
        ch.addLayout(chip_text_col, 1)
        ch.addWidget(chip_close)
        self.chip_frame.setVisible(False)
        cv.addWidget(self.chip_frame)

        # input row
        row = QHBoxLayout()
        row.setContentsMargins(6, 0, 0, 0)
        row.setSpacing(10)

        self.input = MessageInput()
        self.input.setObjectName("messageInput")
        self.input.setPlaceholderText("Write a message…  (Ctrl+V to paste an image, or drop a file)")
        self.input.returnPressed.connect(self._send_clicked)
        self.input.imagePasted.connect(self._on_image_pasted)

        self.attach_btn = QPushButton()
        self.attach_btn.setObjectName("iconBtn")
        self.attach_btn.setIcon(T.make_icon("paperclip", size=18))
        self.attach_btn.setIconSize(QSize(18, 18))
        self.attach_btn.setFixedSize(40, 40)
        self.attach_btn.setToolTip("Attach an image")
        self.attach_btn.clicked.connect(self._pick_image)

        self.send_btn = QPushButton("Send  ")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setIcon(T.make_icon("send", size=16, c1="#FFFFFF", c2="#E9DEFF"))
        self.send_btn.setIconSize(QSize(16, 16))
        self.send_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.send_btn.setMinimumHeight(40)
        self.send_btn.clicked.connect(self._send_clicked)

        row.addWidget(self.attach_btn)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_btn)
        cv.addLayout(row)

        v.addWidget(composer)
        parent_layout.addWidget(wrap, 1)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    def _on_history_changed(self, ctx: str) -> None:
        """Controller switched contexts (or finished bootstrapping); rerender."""
        if ctx != self.ctrl.current_context:
            return
        # Update header.
        if ctx.startswith("group_"):
            name = ctx.split("_", 1)[1].title()
            self.title_lbl.setText(name)
            self.header_icon.setPixmap(T.make_pixmap("hash", size=22))
        else:
            other = ctx.split("_", 1)[1]
            self.title_lbl.setText(other)
            self.header_icon.setPixmap(T.make_pixmap("user", size=22))
        self.subtitle_lbl.setText(self.ctrl.subtitle_for(ctx))

        self._clear_messages()
        for entry in self.ctrl.histories.get(ctx, []):
            self._append_widget(self._row_for_entry(entry))
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _on_entry_appended(self, ctx: str, entry: dict) -> None:
        """Controller added a single entry; render it if it's the active ctx."""
        if ctx != self.ctrl.current_context:
            return
        self._append_widget(self._row_for_entry(entry))
        QTimer.singleShot(0, self._scroll_to_bottom)

    def _row_for_entry(self, e: dict) -> QWidget:
        if e["kind"] == "system":
            return SystemRow(e["text"], event_type=e.get("event", ""),
                             ts=e.get("ts", ""))
        if e["kind"] == "image":
            return ImageRow(
                sender=e["sender"],
                path=e["path"],
                caption=e.get("text", ""),
                mime=e.get("mime", ""),
                name=e.get("name", ""),
                is_self=(e["sender"] == self.username),
                ts=e["ts"],
            )
        return MessageRow(
            sender=e["sender"],
            message=e["text"],
            is_self=(e["sender"] == self.username),
            ts=e["ts"],
        )

    def _clear_messages(self) -> None:
        # Detach widgets immediately so deleteLater doesn't leave ghost rows.
        while self.msg_layout.count() > 1:
            item = self.msg_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _append_widget(self, w: QWidget) -> None:
        # Insert before the trailing stretch.
        self.msg_layout.insertWidget(self.msg_layout.count() - 1, w)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    # ------------------------------------------------------------------
    # User actions → controller
    # ------------------------------------------------------------------

    def _send_clicked(self) -> None:
        if self.ctrl.send_current(self.input.text()):
            self.input.clear()

    def _pick_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Attach an image", "",
            "Images (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;All files (*)",
        )
        if path:
            self.ctrl.stage_image_file(path)

    def _on_image_pasted(self, image: QImage, mime: str, name: str) -> None:
        self.ctrl.stage_qimage(image, mime, name)

    def _save_chat_dialog(self) -> None:
        ctx = self.ctrl.current_context
        path, _ = QFileDialog.getSaveFileName(
            self, "Save chat history", f"{ctx}.txt",
            "Text files (*.txt);;All files (*)",
        )
        if path:
            self.ctrl.export_chat(ctx, path, self.title_lbl.text())

    # ------------------------------------------------------------------
    # Pending-attachment chip
    # ------------------------------------------------------------------

    def _on_pending_changed(self, pending) -> None:
        if pending is None:
            self.chip_thumb.clear()
            self.chip_name.setText("")
            self.chip_meta.setText("")
            self.chip_frame.setVisible(False)
            self.input.setPlaceholderText(
                "Write a message…  (Ctrl+V to paste an image, or drop a file)")
            return

        preview = pending.get("preview")
        if preview is not None and not preview.isNull():
            thumb = preview.scaled(
                self.chip_thumb.width(), self.chip_thumb.height(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            pix = QPixmap.fromImage(thumb)
            self.chip_thumb.setPixmap(round_pixmap(pix, T.RADIUS_SM))
        else:
            self.chip_thumb.clear()

        self.chip_name.setText(pending["name"])
        size_kb = max(1, len(pending["data"]) // 1024)
        self.chip_meta.setText(f"{pending['mime']}  ·  {size_kb} KB  ·  ready to send")
        self.chip_frame.setVisible(True)
        self.input.setPlaceholderText(
            "Add a caption (optional) and press Enter to send…")
        self.input.setFocus()

    # ------------------------------------------------------------------
    # Drag-and-drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, ev) -> None:
        md = ev.mimeData()
        if md.hasImage() or self._has_image_urls(md):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev) -> None:
        md = ev.mimeData()
        if md.hasImage() or self._has_image_urls(md):
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dropEvent(self, ev) -> None:
        md = ev.mimeData()
        if md.hasUrls():
            for url in md.urls():
                if not url.isLocalFile():
                    continue
                p = url.toLocalFile()
                if os.path.splitext(p)[1].lower() in IMAGE_EXTS:
                    if self.ctrl.stage_image_file(p):
                        ev.acceptProposedAction()
                        return
        if md.hasImage():
            img = QImage(md.imageData())
            if not img.isNull():
                self.ctrl.stage_qimage(img, "image/png", "dropped.png")
                ev.acceptProposedAction()
                return
        ev.ignore()

    @staticmethod
    def _has_image_urls(md) -> bool:
        if not md.hasUrls():
            return False
        for url in md.urls():
            if (url.isLocalFile()
                    and os.path.splitext(url.toLocalFile())[1].lower() in IMAGE_EXTS):
                return True
        return False

    # ------------------------------------------------------------------
    # Dialog passthroughs (controller emits → window shows)
    # ------------------------------------------------------------------

    def _show_warning(self, title: str, body: str) -> None:
        QMessageBox.warning(self, title, body)

    def _show_info(self, title: str, body: str) -> None:
        QMessageBox.information(self, title, body)

    def _on_disconnected(self) -> None:
        QMessageBox.information(self, "Disconnected",
                                "Connection to server was closed.")
        self.close()

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, ev) -> None:
        try:
            if getattr(self, "tray", None) is not None:
                self.tray.hide()
        except Exception:
            pass
        self.ctrl.shutdown()
        super().closeEvent(ev)


__all__ = ["LoginDialog", "ChatWindow"]
