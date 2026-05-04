"""Pure helpers for the chat client — no GUI dependencies."""
from __future__ import annotations

from datetime import datetime


# ---------------------------------------------------------------------------
# App-wide constants
# ---------------------------------------------------------------------------

GROUPS = ["general"]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5050

MAX_IMAGE_BYTES = 7 * 1024 * 1024   # 7 MB encoded ceiling
MAX_IMAGE_EDGE  = 1280              # auto-downscale longest edge

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def fmt_ts(raw: str) -> str:
    """Render a stored timestamp as a 12-hour clock string (e.g. '11:08 PM')."""
    if not raw:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            d = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    else:
        return raw
    h = d.hour % 12 or 12
    suffix = "AM" if d.hour < 12 else "PM"
    return f"{h}:{d.minute:02d} {suffix}"


__all__ = [
    "GROUPS", "DEFAULT_HOST", "DEFAULT_PORT",
    "MAX_IMAGE_BYTES", "MAX_IMAGE_EDGE", "IMAGE_EXTS",
    "now_ts", "fmt_ts",
]
