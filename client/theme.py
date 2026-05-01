"""Design tokens, QSS, and a hollow-gradient SVG icon factory.

Color tokens are HEX/RGBA approximations of the oklch theme described in
the project spec. The `--primary` stays in the violet-indigo range
(oklch ~0.54 / 0.27 / 287deg).
"""
from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt, QSize
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QGraphicsDropShadowEffect, QWidget


# ----------------------------- tokens --------------------------------------

DARK = {
    "background":         "#0B0B12",
    "card":               "#14141D",
    "sidebar":            "#0E0E16",
    "foreground":         "#F4F4F8",
    "muted_foreground":   "#8F8FA3",
    "primary":            "#6029E0",   # oklch(0.5393 0.2713 286.7)
    "primary_grad_to":    "#9B6DFF",
    "primary_foreground": "#FFFFFF",
    "secondary":          "#1C1C28",
    "secondary_fg":       "#E6E6EE",
    "accent":             "#1F1A33",
    "border":             "#24242F",
    "sidebar_border":     "#1A1A26",
    "input":              "#161620",
    "ring":               "#6029E0",
    # Muted indigo so the user's own bubbles don't blast their eyes — the
    # vibrant primary purple is reserved for accents (Send button, links).
    "bubble_self":        "#332E66",
    "bubble_self_border": "#4A4486",
    "bubble_self_fg":     "#ECECF8",
    "bubble_other":       "#1C1C28",
    "bubble_other_fg":    "#F4F4F8",
    "online":             "#22C55E",
    "online_grad_to":     "#86EFAC",
}

LIGHT = {
    "background":         "#FAFAFA",
    "card":               "#FFFFFF",
    "sidebar":            "#F4F4F5",
    "foreground":         "#18181B",
    "muted_foreground":   "#71717A",
    "primary":            "#5B2EDB",
    "primary_grad_to":    "#9B6DFF",
    "primary_foreground": "#FFFFFF",
    "secondary":          "#F1F1F4",
    "secondary_fg":       "#18181B",
    "accent":             "#EDE9FE",
    "border":             "#E4E4E9",
    "sidebar_border":     "#E4E4E9",
    "input":              "#F4F4F5",
    "ring":               "#5B2EDB",
    "bubble_self":        "#EAE5FB",
    "bubble_self_border": "#D6CCF5",
    "bubble_self_fg":     "#251A66",
    "bubble_other":       "#F1F1F4",
    "bubble_other_fg":    "#18181B",
    "online":             "#16A34A",
    "online_grad_to":     "#4ADE80",
}

# Radii derived from --radius: 1.4rem (~22.4px).
RADIUS_LG = 22
RADIUS_MD = 14
RADIUS_SM = 10

# Typography stacks.
FONT_SANS = '"Plus Jakarta Sans","Segoe UI",system-ui,sans-serif'
FONT_SERIF = '"Lora","Georgia",serif'
FONT_MONO = '"IBM Plex Mono","Cascadia Mono","Consolas",monospace'

# --tracking-normal: -0.025em → roughly -0.4 px at 14pt body.
LETTER_SPACING_PX = -0.3


def palette(dark: bool = True) -> dict:
    return DARK if dark else LIGHT


# ----------------------------- QSS -----------------------------------------

def build_qss(p: dict) -> str:
    return f"""
    * {{
        font-family: {FONT_SANS};
        color: {p['foreground']};
        letter-spacing: {LETTER_SPACING_PX}px;
    }}
    QWidget#root {{
        background: {p['background']};
    }}
    /* ---- Sidebar ---- */
    QFrame#sidebar {{
        background: {p['sidebar']};
        border-right: 1px solid {p['sidebar_border']};
    }}
    QLabel#brand {{
        color: {p['foreground']};
        font-size: 18px;
        font-weight: 700;
        letter-spacing: -0.6px;
    }}
    QLabel#brandSub, QLabel#sectionLabel {{
        color: {p['muted_foreground']};
        font-family: {FONT_MONO};
        font-size: 10px;
        font-weight: 600;
        letter-spacing: 1.2px;
    }}
    QLabel#userBadgeName {{ font-weight: 600; font-size: 13px; color: {p['foreground']}; }}
    QLabel#userBadgeStatus {{ color: {p['muted_foreground']}; font-size: 11px; }}
    QLabel#userBadgeChevron {{
        color: {p['muted_foreground']};
        font-size: 14px;
        padding-right: 4px;
    }}

    /* Bottom user badge — clickable, opens a dropdown menu. */
    QPushButton#meBadge {{
        background: {p['secondary']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_MD}px;
        text-align: left;
        padding: 0px;
    }}
    QPushButton#meBadge:hover  {{ background: {p['card']}; border-color: {p['primary']}; }}
    QPushButton#meBadge:pressed {{ background: {p['secondary']}; }}

    /* Popup user menu */
    QMenu {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_MD}px;
        padding: 6px;
        color: {p['foreground']};
    }}
    QMenu::item {{
        padding: 8px 14px;
        border-radius: {RADIUS_SM}px;
        color: {p['foreground']};
    }}
    QMenu::item:selected {{
        background: {p['secondary']};
        color: {p['foreground']};
    }}
    QMenu::separator {{
        height: 1px;
        background: {p['border']};
        margin: 4px 6px;
    }}

    QListWidget#navList {{
        background: transparent;
        border: none;
        outline: none;
        padding: 4px;
    }}
    QListWidget#navList::item {{
        background: transparent;
        border-radius: {RADIUS_MD}px;
        padding: 2px 0px;
        margin: 2px 4px;
        color: {p['foreground']};
    }}
    QListWidget#navList::item:hover {{
        background: {p['secondary']};
    }}
    QListWidget#navList::item:selected {{
        background: {p['accent']};
        color: {p['foreground']};
        border: 1px solid {p['border']};
    }}
    QLabel#dmRowName {{
        color: {p['foreground']};
        font-size: 13px;
    }}
    /* Red unread-count pill anchored at the right edge of a DM row. */
    QLabel#unreadBadge {{
        background: #EF4444;
        color: #FFFFFF;
        font-family: {FONT_MONO};
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.2px;
        border-radius: 10px;
        padding: 1px 7px;
        min-width: 20px;
        min-height: 20px;
    }}

    /* ---- Chat area ---- */
    QFrame#chatArea {{
        background: {p['background']};
    }}
    QFrame#chatHeader {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_LG}px;
    }}
    QLabel#chatTitle {{
        font-size: 16px; font-weight: 700; letter-spacing: -0.4px;
    }}
    QLabel#chatSub {{
        color: {p['muted_foreground']}; font-size: 12px;
    }}

    QScrollArea#chatScroll, QScrollArea#chatScroll > QWidget > QWidget {{
        background: transparent;
        border: none;
    }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 4px 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p['border']}; border-radius: 5px; min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p['muted_foreground']}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

    /* ---- Composer ---- */
    QFrame#composer {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_LG}px;
    }}
    QLineEdit#messageInput {{
        background: transparent;
        border: none;
        font-size: 14px;
        padding: 6px 4px;
        color: {p['foreground']};
        selection-background-color: {p['primary']};
        selection-color: {p['primary_foreground']};
    }}
    QLineEdit#messageInput:focus {{ outline: none; border: none; }}

    /* ---- Buttons ---- */
    QPushButton#sendBtn {{
        background: {p['primary']};
        color: {p['primary_foreground']};
        border: none;
        border-radius: {RADIUS_MD}px;
        padding: 8px 14px;
        font-weight: 600;
    }}
    QPushButton#sendBtn:hover  {{ background: {p['primary_grad_to']}; }}
    QPushButton#sendBtn:pressed {{ background: {p['primary']}; }}
    QPushButton#sendBtn:disabled {{
        background: {p['secondary']}; color: {p['muted_foreground']};
    }}

    QPushButton#iconBtn {{
        background: transparent;
        border: 1px solid {p['border']};
        border-radius: {RADIUS_MD}px;
        padding: 6px;
    }}
    QPushButton#iconBtn:hover  {{ background: {p['secondary']}; }}

    /* ---- Bubbles ----
       Self bubbles use a single muted indigo (no bright gradient); the tail
       corner is gently rounded — softer than the previous "torn" look. */
    QLabel#bubbleSelf {{
        background: {p['bubble_self']};
        color: {p['bubble_self_fg']};
        padding: 10px 16px;
        font-size: 13.5px;
        line-height: 1.4;
        border: 1px solid {p['bubble_self_border']};
        border-top-left-radius:     18px;
        border-top-right-radius:    18px;
        border-bottom-left-radius:  18px;
        border-bottom-right-radius: 10px;
    }}
    QLabel#bubbleOther {{
        background: {p['card']};
        color: {p['bubble_other_fg']};
        padding: 10px 16px;
        font-size: 13.5px;
        line-height: 1.4;
        border: 1px solid {p['border']};
        border-top-left-radius:     18px;
        border-top-right-radius:    18px;
        border-bottom-left-radius:  10px;
        border-bottom-right-radius: 18px;
    }}
    QLabel#bubbleMeta {{
        color: {p['muted_foreground']};
        font-family: {FONT_MONO};
        font-size: 10px;
        letter-spacing: 0.6px;
        padding: 0px 6px;
    }}
    QFrame#imageBubbleSelf {{
        background: {p['bubble_self']};
        border: 1px solid {p['bubble_self_border']};
        border-top-left-radius:     18px;
        border-top-right-radius:    18px;
        border-bottom-left-radius:  18px;
        border-bottom-right-radius: 10px;
    }}
    QFrame#imageBubbleOther {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-top-left-radius:     18px;
        border-top-right-radius:    18px;
        border-bottom-left-radius:  10px;
        border-bottom-right-radius: 18px;
    }}
    QLabel#imageCaption {{
        color: {p['foreground']};
        font-size: 12.5px;
        padding: 2px 6px 4px 6px;
    }}
    QFrame#attachChip {{
        background: {p['secondary']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_MD}px;
    }}
    QLabel#chipThumb {{
        background: {p['input']};
        border-radius: {RADIUS_SM}px;
    }}
    QLabel#chipName {{
        color: {p['foreground']};
        font-size: 12.5px;
        font-weight: 600;
    }}
    QLabel#chipMeta {{
        color: {p['muted_foreground']};
        font-family: {FONT_MONO};
        font-size: 10px;
        letter-spacing: 0.4px;
    }}
    /* Chapter-divider system row: hairlines flank a pill (dot + label). */
    QFrame#systemDivider {{
        background: {p['border']};
        border: none;
        max-height: 1px;
        min-height: 1px;
    }}
    QFrame#systemEventPill {{
        background: {p['secondary']};
        border: 1px solid {p['border']};
        border-radius: 12px;
    }}
    QLabel#systemEvent {{
        color: {p['muted_foreground']};
        font-family: {FONT_SERIF};
        font-size: 12px;
        font-style: italic;
        background: transparent;
        border: none;
    }}

    /* ---- Login dialog ---- */
    QDialog#loginDialog {{
        background: {p['background']};
    }}
    QFrame#loginCard {{
        background: {p['card']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_LG}px;
    }}
    QLabel#loginTitle {{
        font-size: 22px; font-weight: 700; letter-spacing: -0.6px;
    }}
    QLabel#loginSub {{
        color: {p['muted_foreground']}; font-size: 12.5px;
    }}
    QLineEdit#loginField {{
        background: {p['input']};
        border: 1px solid {p['border']};
        border-radius: {RADIUS_MD}px;
        padding: 11px 14px;
        font-size: 13.5px;
        color: {p['foreground']};
    }}
    QLineEdit#loginField:focus {{ border: 1px solid {p['ring']}; }}
    QLabel#loginError {{ color: #F87171; font-size: 12px; }}

    QToolTip {{
        background: {p['card']};
        color: {p['foreground']};
        border: 1px solid {p['border']};
        padding: 6px 8px;
        border-radius: {RADIUS_SM}px;
    }}
    """


# ----------------------------- shadows -------------------------------------

def apply_shadow(widget: QWidget, level: str = "md") -> None:
    """Approximate Tailwind shadow-sm .. shadow-xl via QGraphicsDropShadowEffect."""
    table = {
        "sm": (10, 0,  2,  60),
        "md": (22, 0,  6,  90),
        "lg": (40, 0, 12, 110),
        "xl": (60, 0, 20, 130),
    }
    blur, dx, dy, alpha = table.get(level, table["md"])
    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(blur)
    eff.setOffset(dx, dy)
    eff.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(eff)


# ----------------------------- icons ---------------------------------------

# Hollow geometric SVG icons. Stroke-only, gradient-filled stroke, no solid fills.
# Each icon has two stops c1 -> c2 used for the linear gradient.

def _svg(body: str, size: int = 24, c1: str = "#9B6DFF", c2: str = "#6029E0") -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="24" y2="24" gradientUnits="userSpaceOnUse">
          <stop offset="0" stop-color="{c1}"/>
          <stop offset="1" stop-color="{c2}"/>
        </linearGradient>
      </defs>
      <g stroke="url(#g)" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" fill="none">
        {body}
      </g>
    </svg>"""


# Icon body fragments (hollow only).
_ICON_BODIES = {
    "send": '<path d="M3 12 L21 4 L13 21 L11 13 Z"/><path d="M11 13 L21 4"/>',
    "save": (
        '<path d="M5 3 H16 L21 8 V19 A2 2 0 0 1 19 21 H5 A2 2 0 0 1 3 19 V5 A2 2 0 0 1 5 3 Z"/>'
        '<path d="M7 3 V9 H15 V3"/>'
        '<rect x="7" y="13" width="10" height="6" rx="1"/>'
    ),
    "user":   '<circle cx="12" cy="8" r="4"/><path d="M4 21 C4 16 8 14 12 14 C16 14 20 16 20 21"/>',
    "users":  '<circle cx="9" cy="9" r="3.2"/><path d="M3 19 C3 15.5 6 14 9 14 C12 14 15 15.5 15 19"/><circle cx="17" cy="8" r="2.6"/><path d="M15.5 19 C16 16.5 18 15 21 15.5"/>',
    "hash":   '<path d="M5 9 H20"/><path d="M4 15 H19"/><path d="M10 4 L8 20"/><path d="M16 4 L14 20"/>',
    "dot":    '<circle cx="12" cy="12" r="4"/>',
    "logo":   '<rect x="3.5" y="4.5" width="17" height="13" rx="4"/><path d="M8 17 L7 21 L12.5 17"/><circle cx="9.5" cy="11" r="0.9"/><circle cx="14.5" cy="11" r="0.9"/>',
    "search": '<circle cx="11" cy="11" r="6"/><path d="M20 20 L16 16"/>',
    "logout": '<path d="M14 4 H6 A2 2 0 0 0 4 6 V18 A2 2 0 0 0 6 20 H14"/><path d="M10 12 H21"/><path d="M17 8 L21 12 L17 16"/>',
    "paperclip": (
        '<path d="M9 13 V7.5 A4.5 4.5 0 0 1 18 7.5 V16 A6 6 0 0 1 6 16 V8.5"/>'
        '<path d="M13 9 V16 A2.5 2.5 0 0 1 8 16 V9"/>'
    ),
    "x": '<path d="M6 6 L18 18"/><path d="M18 6 L6 18"/>',
}


def make_icon(name: str, size: int = 22, c1: str | None = None, c2: str | None = None,
              dark: bool = True) -> QIcon:
    p = palette(dark)
    c1 = c1 or p["primary_grad_to"]
    c2 = c2 or p["primary"]
    body = _ICON_BODIES.get(name, _ICON_BODIES["dot"])
    svg = _svg(body, size=size, c1=c1, c2=c2)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return QIcon(pix)


def make_pixmap(name: str, size: int = 22, c1: str | None = None, c2: str | None = None,
                dark: bool = True) -> QPixmap:
    p = palette(dark)
    c1 = c1 or p["primary_grad_to"]
    c2 = c2 or p["primary"]
    body = _ICON_BODIES.get(name, _ICON_BODIES["dot"])
    svg = _svg(body, size=size, c1=c1, c2=c2)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    return pix


__all__ = [
    "DARK", "LIGHT", "palette", "build_qss", "apply_shadow",
    "make_icon", "make_pixmap",
    "RADIUS_LG", "RADIUS_MD", "RADIUS_SM",
    "FONT_SANS", "FONT_SERIF", "FONT_MONO",
]
