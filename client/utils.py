"""Pure helpers for the chat client.

Functions in this module are stateless and have no UI-specific dependencies
beyond the basic PyQt6 painting / font primitives.  Anything that builds a
window or a custom widget lives in `widgets.py` or `chat_window.py`.
"""
from __future__ import annotations

from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QColor, QFont, QFontDatabase, QPainter, QPainterPath, QPixmap,
)
from PyQt6.QtWidgets import QApplication

import theme as T


# ---------------------------------------------------------------------------
# App-wide constants
# ---------------------------------------------------------------------------

# List of public groups the client knows about (kept here so any module
# can reference it without importing the whole UI).
GROUPS = ["general"]

# Default network endpoint pre-filled in the login dialog.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050

# Image staging caps.
MAX_IMAGE_BYTES = 7 * 1024 * 1024   # 7 MB encoded ceiling
MAX_IMAGE_EDGE  = 1280              # auto-downscale longest edge

# Image extensions we recognise for drag-and-drop / clipboard paste.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Distinct accent palette for deterministic per-user avatar colors.
AVATAR_COLORS = [
    "#7C3AED", "#EC4899", "#06B6D4", "#10B981",
    "#F59E0B", "#EF4444", "#3B82F6", "#A855F7",
    "#14B8A6", "#F97316",
]


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_ts(raw: str) -> str:
    """Render a stored timestamp as a 12-hour clock string (e.g. '11:08 PM').
    Accepts both legacy short forms and the canonical ISO-ish form."""
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            d = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    else:
        return raw  # unrecognized — show as-is
    h = d.hour % 12 or 12
    suffix = "AM" if d.hour < 12 else "PM"
    return f"{h}:{d.minute:02d} {suffix}"


# ---------------------------------------------------------------------------
# Pixmap factories
# ---------------------------------------------------------------------------

def avatar_pixmap(name: str, size: int = 30) -> QPixmap:
    """Solid-color disc with the speaker's first letter in white."""
    initial = (name.strip()[:1] or "?").upper()
    h = sum(ord(c) for c in name) if name else 0
    bg = QColor(AVATAR_COLORS[h % len(AVATAR_COLORS)])
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setBrush(bg)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(0, 0, size, size)
    p.setPen(QColor("#FFFFFF"))
    f = p.font()
    f.setBold(True)
    f.setPointSize(max(7, int(size * 0.42)))
    p.setFont(f)
    p.drawText(pix.rect(), int(Qt.AlignmentFlag.AlignCenter), initial)
    p.end()
    return pix


def round_pixmap(pix: QPixmap, radius: int) -> QPixmap:
    """Return a copy of `pix` clipped to a rounded rectangle."""
    if pix.isNull():
        return pix
    out = QPixmap(pix.size())
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pix.width(), pix.height(), radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pix)
    p.end()
    return out


# ---------------------------------------------------------------------------
# App-wide font setup
# ---------------------------------------------------------------------------

def set_app_font(app: QApplication) -> None:
    """Install the preferred font family with the project's letter spacing."""
    families = set(QFontDatabase.families())
    for preferred in ("Plus Jakarta Sans", "Segoe UI", "Inter"):
        if preferred in families:
            f = QFont(preferred, 10)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, T.LETTER_SPACING_PX)
            app.setFont(f)
            return
    f = QFont()
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, T.LETTER_SPACING_PX)
    app.setFont(f)


__all__ = [
    "GROUPS", "DEFAULT_HOST", "DEFAULT_PORT",
    "MAX_IMAGE_BYTES", "MAX_IMAGE_EDGE", "IMAGE_EXTS",
    "AVATAR_COLORS",
    "now_ts", "fmt_ts",
    "avatar_pixmap", "round_pixmap",
    "set_app_font",
]
