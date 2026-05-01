"""Custom Qt widgets used by the chat client.

Each class here is a self-contained `QWidget` subclass — they don't know
about the network or the main window, only how to render the data they
are given.  This keeps the larger `chat_window.py` focused on application
logic instead of pixel-pushing.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, QSize, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QDesktopServices, QImage, QKeySequence, QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout,
    QWidget,
)

import theme as T
from network import mime_for_path
from utils import (
    IMAGE_EXTS,
    avatar_pixmap, fmt_ts, round_pixmap,
)


# ---------------------------------------------------------------------------
# Chat bubbles
# ---------------------------------------------------------------------------

class MessageRow(QWidget):
    """A single chat row. Bubble alignment depends on `is_self`.
    Other-side rows get a circular avatar; both sides get tail-corner bubbles."""

    AVATAR  = 30
    BUBBLE_MAX_W = 520

    def __init__(self, sender: str, message: str, is_self: bool, ts: str, parent=None):
        super().__init__(parent)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 3, 4, 3)
        outer.setSpacing(8)

        bubble = QLabel(message)
        bubble.setWordWrap(True)
        bubble.setMaximumWidth(self.BUBBLE_MAX_W)
        bubble.setObjectName("bubbleSelf" if is_self else "bubbleOther")
        bubble.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        meta_text = fmt_ts(ts) if is_self else f"{sender} · {fmt_ts(ts)}"
        meta = QLabel(meta_text)
        meta.setObjectName("bubbleMeta")

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        if is_self:
            col.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
            col.addWidget(meta,   0, Qt.AlignmentFlag.AlignRight)
            outer.addStretch(1)
            outer.addLayout(col, 0)
        else:
            col.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
            col.addWidget(meta,   0, Qt.AlignmentFlag.AlignLeft)
            avatar_lbl = QLabel()
            avatar_lbl.setFixedSize(self.AVATAR, self.AVATAR)
            avatar_lbl.setPixmap(avatar_pixmap(sender, self.AVATAR))
            outer.addWidget(avatar_lbl, 0, Qt.AlignmentFlag.AlignTop)
            outer.addLayout(col, 0)
            outer.addStretch(1)


class SystemRow(QWidget):
    """Chapter-divider style row for system events (join / leave).

    Renders as a centered pill (small status dot + serif italic text +
    muted 12h timestamp) flanked by hairlines on both sides:
        ───────  ●  ahmed joined the workspace · 11:23 PM  ───────
    The dot color cues the event kind: green for join, muted for leave.
    Each event appends a fresh row — never replaces an existing one — so
    repeated joins/leaves form a chronological trail."""

    def __init__(self, text: str, event_type: str = "", ts: str = "",
                 parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(8, 14, 8, 14)
        h.setSpacing(10)

        kind = event_type or self._infer_kind(text)
        p = T.palette()

        # Left hairline.
        left = QFrame()
        left.setObjectName("systemDivider")
        left.setFixedHeight(1)
        left.setFrameShape(QFrame.Shape.NoFrame)

        # Centered pill: dot + label + timestamp.
        pill = QFrame()
        pill.setObjectName("systemEventPill")
        pl = QHBoxLayout(pill)
        pl.setContentsMargins(12, 5, 14, 5)
        pl.setSpacing(8)

        dot = QLabel()
        if kind == "join":
            dot_pix = T.make_pixmap(
                "dot", size=8,
                c1=p["online_grad_to"], c2=p["online"],
            )
        elif kind == "leave":
            dot_pix = T.make_pixmap(
                "dot", size=8,
                c1=p["muted_foreground"], c2=p["muted_foreground"],
            )
        else:
            dot_pix = T.make_pixmap("dot", size=8)
        dot.setPixmap(dot_pix)
        dot.setFixedSize(8, 8)

        lbl = QLabel(text)
        lbl.setObjectName("systemEvent")

        pl.addWidget(dot)
        pl.addWidget(lbl)

        # Optional 12h timestamp on the right side of the pill.
        when = fmt_ts(ts) if ts else ""
        if when:
            sep = QLabel("·")
            sep.setObjectName("bubbleMeta")
            stamp = QLabel(when)
            stamp.setObjectName("bubbleMeta")
            pl.addWidget(sep)
            pl.addWidget(stamp)

        # Right hairline (mirrors the left one).
        right = QFrame()
        right.setObjectName("systemDivider")
        right.setFixedHeight(1)
        right.setFrameShape(QFrame.Shape.NoFrame)

        h.addWidget(left, 1)
        h.addWidget(pill, 0)
        h.addWidget(right, 1)

    @staticmethod
    def _infer_kind(text: str) -> str:
        t = text.lower()
        if "joined" in t:
            return "join"
        if "left" in t or "disconnected" in t:
            return "leave"
        return ""


class DMRow(QWidget):
    """Sidebar row for a DM partner: small user icon + name + unread badge.

    The badge is a red pill on the right showing the unread DM count; it is
    hidden when the count is 0. Tooltip surfaces the partner's online state."""

    def __init__(self, name: str, online: bool, unread: int, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        h = QHBoxLayout(self)
        # Extra right margin keeps the unread badge from kissing the row's
        # rounded edge / selection highlight.
        h.setContentsMargins(12, 8, 14, 8)
        h.setSpacing(12)

        p = T.palette()
        if online:
            ic = T.make_icon("user", size=18,
                             c1=p["online_grad_to"], c2=p["online"])
        else:
            ic = T.make_icon("user", size=18)
        icon_lbl = QLabel()
        icon_lbl.setPixmap(ic.pixmap(QSize(18, 18)))
        icon_lbl.setFixedSize(18, 18)

        name_lbl = QLabel(name)
        name_lbl.setObjectName("dmRowName")

        self.badge = QLabel()
        self.badge.setObjectName("unreadBadge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setMinimumSize(20, 20)
        self._set_unread(unread)

        h.addWidget(icon_lbl)
        h.addWidget(name_lbl, 1)
        h.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignRight)

        tip = "online" if online else "offline"
        if unread:
            tip += f" · {unread} new"
        self.setToolTip(tip)

    def _set_unread(self, n: int) -> None:
        if n <= 0:
            self.badge.hide()
            self.badge.setText("")
        else:
            self.badge.show()
            self.badge.setText(str(n) if n < 100 else "99+")


class ImageRow(QWidget):
    """A chat row whose bubble is an image (with optional caption)."""

    MAX_W  = 320
    MAX_H  = 320
    AVATAR = 30

    def __init__(self, sender: str, path: str, caption: str, mime: str,
                 name: str, is_self: bool, ts: str, parent=None):
        super().__init__(parent)
        self._path = path

        outer = QHBoxLayout(self)
        outer.setContentsMargins(4, 3, 4, 3)
        outer.setSpacing(8)

        # bubble: a QFrame with rounded image + optional caption
        bubble = QFrame()
        bubble.setObjectName("imageBubbleSelf" if is_self else "imageBubbleOther")
        bv = QVBoxLayout(bubble)
        bv.setContentsMargins(6, 6, 6, 6)
        bv.setSpacing(6)

        img_label = QLabel()
        img_label.setCursor(Qt.CursorShape.PointingHandCursor)
        img_label.setToolTip(f"{name}\nClick to open")
        pix = QPixmap(path)
        if pix.isNull():
            img_label.setText(f"[broken image: {name}]")
            img_label.setObjectName("bubbleMeta")
        else:
            scaled = pix.scaled(
                self.MAX_W, self.MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            img_label.setPixmap(round_pixmap(scaled, T.RADIUS_MD))
            img_label.setFixedSize(scaled.size())
        img_label.mousePressEvent = self._open  # type: ignore[assignment]
        bv.addWidget(img_label, 0, Qt.AlignmentFlag.AlignCenter)

        if caption:
            cap = QLabel(caption)
            cap.setWordWrap(True)
            cap.setMaximumWidth(self.MAX_W)
            cap.setObjectName("imageCaption")
            bv.addWidget(cap)

        meta_text = fmt_ts(ts) if is_self else f"{sender} · {fmt_ts(ts)}"
        meta_text += f"  ·  {name}"
        meta = QLabel(meta_text)
        meta.setObjectName("bubbleMeta")

        col = QVBoxLayout()
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(2)
        if is_self:
            col.addWidget(bubble, 0, Qt.AlignmentFlag.AlignRight)
            col.addWidget(meta,   0, Qt.AlignmentFlag.AlignRight)
            outer.addStretch(1)
            outer.addLayout(col, 0)
        else:
            col.addWidget(bubble, 0, Qt.AlignmentFlag.AlignLeft)
            col.addWidget(meta,   0, Qt.AlignmentFlag.AlignLeft)
            avatar_lbl = QLabel()
            avatar_lbl.setFixedSize(self.AVATAR, self.AVATAR)
            avatar_lbl.setPixmap(avatar_pixmap(sender, self.AVATAR))
            outer.addWidget(avatar_lbl, 0, Qt.AlignmentFlag.AlignTop)
            outer.addLayout(col, 0)
            outer.addStretch(1)

    def _open(self, _ev) -> None:
        if self._path and os.path.exists(self._path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))


# ---------------------------------------------------------------------------
# Composer input
# ---------------------------------------------------------------------------

class MessageInput(QLineEdit):
    """QLineEdit that emits `imagePasted` when Ctrl+V holds an image.

    Falls back to the normal text paste otherwise.
    """
    imagePasted = pyqtSignal(QImage, str, str)  # (image, mime, suggested_name)

    def keyPressEvent(self, ev) -> None:
        if ev.matches(QKeySequence.StandardKey.Paste):
            cb = QApplication.clipboard()
            md = cb.mimeData()
            if md is not None:
                # 1. raw image on the clipboard (e.g. screenshot tool)
                if md.hasImage():
                    img = cb.image()
                    if not img.isNull():
                        self.imagePasted.emit(img, "image/png", "pasted.png")
                        ev.accept()
                        return
                # 2. file URL pointing to a local image
                if md.hasUrls():
                    for url in md.urls():
                        if url.isLocalFile():
                            p = url.toLocalFile()
                            ext = os.path.splitext(p)[1].lower()
                            if ext in IMAGE_EXTS:
                                img = QImage(p)
                                if not img.isNull():
                                    self.imagePasted.emit(
                                        img,
                                        mime_for_path(p),
                                        os.path.basename(p),
                                    )
                                    ev.accept()
                                    return
        super().keyPressEvent(ev)


__all__ = [
    "MessageRow", "SystemRow", "DMRow", "ImageRow", "MessageInput",
]
