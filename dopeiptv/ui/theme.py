"""Theme engine: palettes, accent colors, and the global QSS builder."""

from __future__ import annotations

ACCENTS = {
    "blue":   ("Blue",   "#4C8DFF", "#5E99FF"),
    "purple": ("Purple", "#8E6BFF", "#A184FF"),
    "teal":   ("Teal",   "#2AC3C3", "#4AD4D4"),
    "green":  ("Green",  "#2FBF71", "#4CD08A"),
    "orange": ("Orange", "#FF9F43", "#FFB160"),
    "pink":   ("Pink",   "#FF5C8A", "#FF7AA1"),
    "red":    ("Red",    "#FF5C5C", "#FF7A7A"),
}

THEMES = {
    "graphite": {
        "name": "Graphite (default)",
        "bg": "#17171C", "side": "#101014", "pane": "#1B1B21",
        "hover": "#1D1D24", "sel": "#26262E", "input": "#222229",
        "btn": "#2A2A32", "btn_hover": "#34343E",
        "border": "#232329", "border_in": "#2C2C34",
        "scroll": "#33333C", "scroll_hover": "#45454F",
        "text": "#ECECF1", "text2": "#C9C9D2", "text3": "#A7A7B1",
        "muted": "#8B8B96", "muted2": "#6E6E79", "muted3": "#5A5A64",
        "muted4": "#7A7A85", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "midnight": {
        "name": "Midnight (blue)",
        "bg": "#0E1526", "side": "#0A101E", "pane": "#121A2E",
        "hover": "#182238", "sel": "#1E2A45", "input": "#16203A",
        "btn": "#1C2740", "btn_hover": "#28345A",
        "border": "#1C2740", "border_in": "#243052",
        "scroll": "#2A3654", "scroll_hover": "#3A4870",
        "text": "#E6EAF2", "text2": "#C3CBD9", "text3": "#9AA5B8",
        "muted": "#8E99AF", "muted2": "#6B7690", "muted3": "#57627B",
        "muted4": "#77829C", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "oled": {
        "name": "OLED (pure black)",
        "bg": "#000000", "side": "#000000", "pane": "#0A0A0C",
        "hover": "#16161A", "sel": "#202026", "input": "#121216",
        "btn": "#1A1A20", "btn_hover": "#26262E",
        "border": "#1C1C22", "border_in": "#26262C",
        "scroll": "#2E2E36", "scroll_hover": "#3E3E48",
        "text": "#F2F2F6", "text2": "#CFCFD8", "text3": "#A8A8B4",
        "muted": "#8B8B96", "muted2": "#6E6E79", "muted3": "#5A5A64",
        "muted4": "#7A7A85", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "nord": {
        "name": "Nord",
        "bg": "#2E3440", "side": "#272C36", "pane": "#333947",
        "hover": "#3B4252", "sel": "#434C5E", "input": "#3B4252",
        "btn": "#434C5E", "btn_hover": "#4C566A",
        "border": "#262B35", "border_in": "#4C566A",
        "scroll": "#4C566A", "scroll_hover": "#5E6A82",
        "text": "#ECEFF4", "text2": "#D8DEE9", "text3": "#B8C0D0",
        "muted": "#94A0B8", "muted2": "#7B879D", "muted3": "#6A7590",
        "muted4": "#8590A6", "error": "#FF6B6B", "rec": "#FF5C5C",
    },
    "dracula": {
        "name": "Dracula",
        "bg": "#282A36", "side": "#21222C", "pane": "#2B2E3B",
        "hover": "#343746", "sel": "#44475A", "input": "#21222C",
        "btn": "#343746", "btn_hover": "#44475A",
        "border": "#191A21", "border_in": "#44475A",
        "scroll": "#44475A", "scroll_hover": "#565973",
        "text": "#F8F8F2", "text2": "#E4E4DE", "text3": "#C9C9C4",
        "muted": "#A2A3B2", "muted2": "#7E7F92", "muted3": "#6C6D80",
        "muted4": "#8B8C9E", "error": "#FF5555", "rec": "#FF5555",
    },
    "gruvbox": {
        "name": "Gruvbox (dark)",
        "bg": "#282828", "side": "#1D2021", "pane": "#32302F",
        "hover": "#3C3836", "sel": "#504945", "input": "#1D2021",
        "btn": "#3C3836", "btn_hover": "#504945",
        "border": "#1D2021", "border_in": "#504945",
        "scroll": "#504945", "scroll_hover": "#665C54",
        "text": "#EBDBB2", "text2": "#D5C4A1", "text3": "#BDAE93",
        "muted": "#A89984", "muted2": "#928374", "muted3": "#7C6F64",
        "muted4": "#928374", "error": "#FB4934", "rec": "#FB4934",
    },
    "solarized": {
        "name": "Solarized (dark)",
        "bg": "#002B36", "side": "#00252E", "pane": "#073642",
        "hover": "#0A3F4C", "sel": "#0E4B5A", "input": "#00252E",
        "btn": "#073642", "btn_hover": "#0E4B5A",
        "border": "#00212B", "border_in": "#0E4B5A",
        "scroll": "#0E4B5A", "scroll_hover": "#14606F",
        "text": "#EEE8D5", "text2": "#93A1A1", "text3": "#839496",
        "muted": "#768D92", "muted2": "#657B83", "muted3": "#556B72",
        "muted4": "#768D92", "error": "#DC322F", "rec": "#DC322F",
    },
    "catppuccin": {
        "name": "Catppuccin (mocha)",
        "bg": "#1E1E2E", "side": "#181825", "pane": "#292C3C",
        "hover": "#313244", "sel": "#45475A", "input": "#181825",
        "btn": "#313244", "btn_hover": "#45475A",
        "border": "#11111B", "border_in": "#45475A",
        "scroll": "#45475A", "scroll_hover": "#585B70",
        "text": "#CDD6F4", "text2": "#BAC2DE", "text3": "#A6ADC8",
        "muted": "#9399B2", "muted2": "#7F849C", "muted3": "#6C7086",
        "muted4": "#9399B2", "error": "#F38BA8", "rec": "#F38BA8",
    },
    "light": {
        "name": "Light",
        "bg": "#F5F5F7", "side": "#ECECEF", "pane": "#FFFFFF",
        "hover": "#E4E4E9", "sel": "#D8D8DF", "input": "#FFFFFF",
        "btn": "#E8E8EC", "btn_hover": "#DCDCE2",
        "border": "#D9D9DE", "border_in": "#C9C9D2",
        "scroll": "#C5C5CE", "scroll_hover": "#ADADB8",
        "text": "#1B1B1F", "text2": "#3A3A42", "text3": "#55555F",
        "muted": "#6E6E79", "muted2": "#8B8B96", "muted3": "#8B8B96",
        "muted4": "#7A7A85", "error": "#D93025", "rec": "#D93025",
    },
}

P: dict[str, str] = {}
ACCENT: str = ACCENTS["blue"][1]


def apply_theme(settings=None, theme: str | None = None,
                accent: str | None = None) -> None:
    """Activate a theme + accent into the global palette ``P``."""
    global ACCENT
    if settings is not None:
        theme = theme or settings.value("theme", "graphite")
        accent = accent or settings.value("accent", "blue")
    base = THEMES.get(theme or "graphite", THEMES["graphite"])
    acc = ACCENTS.get(accent or "blue", ACCENTS["blue"])
    P.clear()
    P.update(base)
    P["accent"], P["accent_hi"] = acc[1], acc[2]
    ACCENT = acc[1]


apply_theme()


def _rgba(hex_color: str, alpha: float) -> str:
    """A CSS rgba() string from a #rrggbb colour - used for subtle, tinted
    borders/fills that let some background show through."""
    c = (hex_color or "#000000").lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _mix(fg: str, bg: str, ratio: float) -> str:
    """A SOLID #rrggbb that is ``ratio`` of ``fg`` blended over ``bg``. Unlike
    an rgba() fill, this stays fully opaque, so it reads the same over a
    transparent parent (e.g. the sidebar's scroll content) as it would over a
    solid one - no see-through washout."""
    f = (fg or "#000000").lstrip("#")
    b = (bg or "#000000").lstrip("#")
    fr, fg_, fb = int(f[0:2], 16), int(f[2:4], 16), int(f[4:6], 16)
    br, bg_, bb = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
    r = round(fr * ratio + br * (1 - ratio))
    g = round(fg_ * ratio + bg_ * (1 - ratio))
    bl = round(fb * ratio + bb * (1 - ratio))
    return f"#{r:02X}{g:02X}{bl:02X}"


def _arrow_png(direction: str, color: str, size: int = 10) -> str:
    """Path to a small antialiased arrow PNG for QSS ``image:`` rules. The
    QSS border-triangle trick renders as an empty box on several platform
    styles (macOS especially), so real images are the only arrows that show
    reliably everywhere. Rendered supersampled, cached per
    direction/colour/size and regenerated only when missing."""
    import os

    from PyQt6.QtCore import QPointF, QStandardPaths, Qt
    from PyQt6.QtGui import QColor, QPainter, QPixmap, QPolygonF
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(
        base, f"qss_arrow_{direction}_{size}_{color.lstrip('#')}.png")
    if not os.path.exists(path):
        ss = 4
        big = QPixmap(size * ss, size * ss)
        big.fill(Qt.GlobalColor.transparent)
        pr = QPainter(big)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(QColor(color))
        S = float(size * ss)
        if direction == "up":
            pts = [QPointF(S * 0.14, S * 0.70), QPointF(S * 0.86, S * 0.70),
                   QPointF(S * 0.50, S * 0.26)]
        else:
            pts = [QPointF(S * 0.14, S * 0.30), QPointF(S * 0.86, S * 0.30),
                   QPointF(S * 0.50, S * 0.74)]
        pr.drawPolygon(QPolygonF(pts))
        pr.end()
        big.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                   Qt.TransformationMode.SmoothTransformation).save(path)
    return path.replace("\\", "/")


def _check_png(color: str, size: int = 12) -> str:
    """Path to an antialiased check-mark PNG for the QCheckBox indicator
    (same rationale as _arrow_png: drawn QSS marks don't render reliably)."""
    import os

    from PyQt6.QtCore import QPointF, QStandardPaths, Qt
    from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"qss_check_{size}_{color.lstrip('#')}.png")
    if not os.path.exists(path):
        ss = 4
        big = QPixmap(size * ss, size * ss)
        big.fill(Qt.GlobalColor.transparent)
        pr = QPainter(big)
        pr.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        S = float(size * ss)
        pen = QPen(QColor(color))
        pen.setWidthF(S * 0.14)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pr.setPen(pen)
        pr.drawPolyline(QPolygonF([
            QPointF(S * 0.18, S * 0.55), QPointF(S * 0.42, S * 0.76),
            QPointF(S * 0.82, S * 0.26)]))
        pr.end()
        big.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                   Qt.TransformationMode.SmoothTransformation).save(path)
    return path.replace("\\", "/")


def build_style() -> str:
    """Generate the full application QSS from the active palette."""
    p = dict(P)
    arrow_up = _arrow_png("up", p['text2'])
    arrow_down = _arrow_png("down", p['text2'])
    arrow_up_sm = _arrow_png("up", p['text2'], 8)
    arrow_down_sm = _arrow_png("down", p['text2'], 8)
    check_png = _check_png("#FFFFFF")
    # Checkbox border mixed well toward the text colour, so the box is
    # clearly visible even on the near-black OLED palette (its border_in is
    # almost invisible against pure black).
    chk_border = _mix(p['text'], p['input'], 0.45)
    # Sidebar action buttons (Guide, Settings): a solid, theme-neutral raised
    # surface with a soft edge, both derived from the palette so they track the
    # theme instead of standing out in a clashing colour. Opaque so they show
    # clearly over the transparent scroll content. The accent is reserved for
    # hover, where a bit of colour is expected.
    side_action_bg = _mix(p['text'], p['side'], 0.11)
    side_action_edge = _mix(p['text'], p['side'], 0.26)
    side_action_hover = _mix(p['text'], p['side'], 0.20)
    return f"""
* {{
    font-family: "SF Pro Text", "Inter", "Cantarell", "Noto Sans", sans-serif;
    color: {p['text']};
}}
QMainWindow, QDialog {{ background: {p['bg']}; }}

/* Sidebar */
#Sidebar {{
    background: {p['side']};
    border-right: 1px solid {p['border']};
}}
#AppTitle {{ font-size: 15px; font-weight: 700; letter-spacing: 0.5px; }}
#AppSub   {{ color: {p['muted4']}; font-size: 11px; }}

QPushButton#NavBtn {{
    background: transparent; border: none; border-radius: 8px;
    padding: 4px 12px; text-align: left; font-size: 14px; color: {p['text2']};
}}
/* Browse (TV/Movies/Series) reads a notch or two above the Library rows - a
   small type hierarchy that structures the sidebar without extra chrome. */
QPushButton#NavBtn[primary="true"] {{ font-size: 16px; }}
QPushButton#NavBtn:hover  {{ background: {p['hover']}; }}
QPushButton#NavBtn:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#NavBtn[rail="true"] {{ text-align: center; padding: 4px 0; font-size: 19px; }}
/* Guide / Settings: the sidebar's bottom actions. Give them a visible filled
   pill with a border so they read as buttons (not plain text like the nav
   items), plus an obvious hover so it's clear they're clickable. */
QPushButton#SideAction {{
    background: {side_action_bg}; border: 1px solid {side_action_edge};
    border-radius: 8px; padding: 9px 14px; text-align: center;
    font-size: 13px; font-weight: 600; color: {p['text2']}; margin-top: 3px;
}}
QPushButton#SideAction:hover {{
    background: {side_action_hover}; border-color: {ACCENT}; color: {p['text']};
}}
QPushButton#SideAction:pressed {{ background: {side_action_edge}; }}
QPushButton#SideAction:pressed {{ background: {p['hover']}; }}
QPushButton#SideAction[rail="true"] {{
    text-align: center; padding: 8px 0; font-size: 14px; font-weight: 600;
}}

/* Playlist switcher: a compact pill "chip" sitting right under the logo, so
   the two read as one block (logo = the app, chip = the account/playlist it
   is showing). Same stack icon as its rail form ties the two states together. */
QPushButton#PlaylistChip {{
    background: transparent; border: 1px solid {side_action_edge};
    border-radius: 8px; padding: 5px 9px; text-align: center;
    font-size: 12px; font-weight: 600; color: {p['text2']};
}}
QPushButton#PlaylistChip:hover {{
    background: {side_action_hover}; border-color: {ACCENT}; color: {p['text']};
}}
QPushButton#PlaylistChip[rail="true"] {{
    /* On the rail it sits among the flat nav icons - same footprint, no
       pill chrome (the bordered pill read as a taller, different control). */
    background: transparent; border: none; border-radius: 8px;
    padding: 4px 0; margin-top: 0px;
}}
QPushButton#PlaylistChip[rail="true"]:hover {{ background: {p['hover']}; }}

#SectionLabel {{
    color: {p['muted2']}; font-size: 10px; font-weight: 700;
    letter-spacing: 1.2px; padding: 10px 14px 4px 14px;
}}

QListWidget {{
    background: transparent; border: none; outline: none; font-size: 13px;
    color: {p['text2']};
}}
QListWidget::item {{ border-radius: 8px; padding: 7px 10px; margin: 1px 6px; }}
/* The sidebar's category list is a long, scannable list, so its rows are a
   touch tighter than the default list item (less vertical padding/margin). */
QListWidget#CatList::item {{ padding: 4px 10px; margin: 1px 4px; }}
QListWidget::item:hover    {{ background: {p['hover']}; }}
QListWidget::item:selected {{ background: {p['sel']}; color: {p['text']}; }}

/* Middle column */
#MiddlePane {{ background: {p['bg']}; }}
QLineEdit#Search {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 9px;
    padding: 8px 12px; font-size: 13px;
}}
QLineEdit#Search:focus {{ border: 1px solid {ACCENT}; }}

QListView#Channels {{
    background: transparent; border: none; outline: none; font-size: 13px;
}}

#ChNum   {{ font-size: 11px; color: {p['muted3']}; }}

QProgressBar#LoadBar {{
    background: transparent; border: none; max-height: 3px;
}}
QProgressBar#LoadBar::chunk {{ background: {ACCENT}; }}

QProgressBar#EpgBar {{
    background: {p['btn']}; border: none; border-radius: 2px; max-height: 4px;
}}
QProgressBar#EpgBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QSlider::groove:horizontal {{
    background: {p['btn']}; height: 4px; border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 12px; height: 12px;
    margin: -4px 0; border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{ background: {p['accent_hi']}; }}

/* Detail panel */
#DetailPane {{ background: {p['pane']}; border-left: 1px solid {p['border']}; }}
#DetailTitle {{ font-size: 20px; font-weight: 700; }}
#DetailMeta  {{ color: {p['muted']}; font-size: 12px; }}
#NowTitle    {{ font-size: 15px; font-weight: 600; }}
#NowTime     {{ color: {ACCENT}; font-size: 11px; font-weight: 700; }}
#NowDesc     {{ color: {p['text3']}; font-size: 12px; }}

QFrame#Card {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 12px;
}}
/* "On now" card: an accent-tinted panel with a left accent rail, so the
   current programme reads as highlighted rather than a plain grey box. */
QFrame#NowCard {{
    background: {_rgba(ACCENT, 0.10)};
    border: 1px solid {_rgba(ACCENT, 0.30)};
    border-left: 2px solid {ACCENT};
    border-radius: 12px;
}}
/* Upcoming programmes: slim rows (time | title) with a hover cue, instead of
   heavy bordered cards. */
QFrame#EpgRow {{ background: transparent; border: none; border-radius: 8px; }}
QFrame#EpgRow:hover {{ background: {p['hover']}; }}
QLabel#EpgRowTime  {{ color: {ACCENT}; font-size: 12px; font-weight: 700; }}
QLabel#EpgRowTitle {{ font-size: 13px; }}

QPushButton {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']}; border-radius: 9px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover  {{ background: {p['btn_hover']}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; }}
QPushButton#Primary:hover {{ background: {p['accent_hi']}; }}
/* The poster play/pause/stop overlay is just a white glyph (drawn in code with
   a faint dark outline so it reads over any artwork) - no disc or ring. Fully
   transparent button; a barely-there wash on hover is the only chrome. */
QPushButton#PlayGhost {{ background: transparent; border: none; padding: 0; }}
QPushButton#PlayGhost:hover {{ background: rgba(255,255,255,0.10); border-radius: 6px; }}
QPushButton#MiniBtn {{
    padding: 0; font-size: 13px; border-radius: 6px; text-align: center;
}}

QToolButton#SectionToggle {{
    background: transparent; border: none; border-radius: 6px;
}}
QToolButton#SectionToggle:hover {{ background: {p['hover']}; }}
QToolButton#SectionToggle:checked {{ background: {p['hover']}; }}

QToolButton#ReopenStrip {{
    background: {p['hover']}; border: none;
    border-top-right-radius: 6px; border-bottom-right-radius: 6px;
}}
QToolButton#ReopenStrip:hover {{ background: {ACCENT}; }}

QSplitter::handle {{ background: transparent; width: 6px; }}
QSplitter::handle:hover {{ background: {p['border_in']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* Thin themed scrollbars in both orientations. Without an explicit
   horizontal style Qt falls back to the platform theme (a fat white bar on
   macOS / GTK+), which reads as ugly against the dark UI. */
QScrollBar:vertical {{
    background: transparent; width: 8px; margin: 2px;
    border: none;
}}
QScrollBar:horizontal {{
    background: transparent; height: 8px; margin: 2px;
    border: none;
}}
QScrollBar::handle:vertical {{
    background: {p['scroll']}; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:horizontal {{
    background: {p['scroll']}; border-radius: 4px; min-width: 30px;
}}
QScrollBar::handle:vertical:hover,
QScrollBar::handle:horizontal:hover {{ background: {p['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{
    width: 0; height: 0; background: none; border: none;
}}
QScrollBar::add-page, QScrollBar::sub-page {{
    background: transparent;
}}

QMenuBar {{
    background: {p['side']}; color: {p['text2']}; border-bottom: 1px solid {p['border']};
}}
QMenuBar::item {{
    background: transparent; padding: 4px 10px; margin: 0; border-radius: 6px;
    font-size: 12px;
}}
QMenuBar::item:selected {{ background: {p['hover']}; }}
QMenuBar::item:pressed {{ background: {ACCENT}; color: white; }}

QMenu {{
    background: {p['hover']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 5px; font-size: 12px;
}}
QMenu::item {{
    background: transparent; color: {p['text']}; border-radius: 6px;
    padding: 5px 20px 5px 10px; font-size: 12px;
}}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::item:disabled {{ color: {p['muted2']}; }}
QMenu::separator {{ height: 1px; background: {p['border_in']}; margin: 5px 8px; }}

QToolTip {{
    background: {p['hover']}; color: {p['text']}; border: 1px solid {p['border_in']};
    padding: 4px 6px;
}}

QTabWidget::pane {{
    border: 1px solid {p['border_in']}; border-radius: 8px; background: {p['pane']};
    top: -1px;
}}
QTabWidget::tab-bar {{ alignment: left; }}
QTabBar::tab {{
    background: transparent; color: {p['text2']}; padding: 7px 10px;
    border-radius: 7px; margin: 0px 1px; font-size: 12px;
}}
QTabBar::tab:selected {{ background: {p['btn']}; color: {p['text']}; }}
QTabBar::tab:hover:!selected {{ background: {p['hover']}; }}

QComboBox {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 5px 10px 5px 10px; font-size: 12px;
    combobox-popup: 0;
}}
QComboBox::drop-down {{
    subcontrol-origin: padding; subcontrol-position: center right;
    width: 18px; border: none; background: transparent;
}}
QComboBox::down-arrow {{
    image: url("{arrow_down}"); width: 10px; height: 10px;
    margin-right: 5px;
}}
QComboBox QAbstractItemView {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 6px;
    selection-background-color: {ACCENT}; selection-color: white;
    outline: none; font-size: 12px; padding: 3px;
}}
QComboBox QAbstractItemView::item {{ min-height: 22px; padding: 3px 8px; }}
QComboBox#InlineCombo {{ padding: 3px 8px; font-size: 11px; }}
QComboBox#InlineCombo::drop-down {{ width: 14px; }}
QComboBox#InlineCombo::down-arrow {{
    image: url("{arrow_down_sm}"); width: 8px; height: 8px; margin-right: 3px;
}}
/* Same visible box as the QToolButton form below, so the compact control
   strip's buttons all read as equal tiles. */
QPushButton#InlineToggle {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']};
    padding: 4px 8px; font-size: 11px; border-radius: 7px;
}}
QPushButton#InlineToggle:hover {{ background: {p['btn_hover']}; }}
QPushButton#InlineToggle:checked {{ background: {ACCENT}; border: none; color: white; }}
/* The compact ⊞/⇅ stand-ins for the Size/Sort combos are QToolButtons (they
   open a menu); match the pushbutton look and hide the extra menu arrow. */
QToolButton#InlineToggle {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']};
    padding: 4px 0; font-size: 12px; border-radius: 7px;
}}
QToolButton#InlineToggle:hover {{ background: {p['btn_hover']}; }}
QToolButton#InlineToggle::menu-indicator {{ image: none; }}
#MiddlePane QLabel {{ color: {p['muted2']}; font-size: 11px; }}
QDateTimeEdit {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 5px 10px; font-size: 12px;
}}
QDateTimeEdit::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border: none; border-left: 1px solid {p['border_in']};
    border-top-right-radius: 8px; background: {p['hover']};
}}
QDateTimeEdit::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border: none; border-left: 1px solid {p['border_in']};
    border-bottom-right-radius: 8px; background: {p['hover']};
}}
QDateTimeEdit::up-button:hover, QDateTimeEdit::down-button:hover {{
    background: {p['border_in']};
}}
QDateTimeEdit::up-arrow {{
    image: url("{arrow_up_sm}"); width: 8px; height: 8px;
}}
QDateTimeEdit::down-arrow {{
    image: url("{arrow_down_sm}"); width: 8px; height: 8px;
}}

QLineEdit {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 8px 10px;
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}

/* Check boxes: a clearly bordered indicator that fills with the accent when
   checked (drawn check-mark image - see _check_png). Styled explicitly so it
   stays visible on every palette; the style default drew a dark box that
   vanished on the pure-black OLED theme. */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{
    width: 18px; height: 18px; border: 1px solid {chk_border};
    border-radius: 4px; background: {p['input']};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT}; border-color: {ACCENT};
    image: url("{check_png}");
}}
QCheckBox::indicator:disabled {{
    border-color: {p['border_in']}; background: {p['pane']};
}}

QSpinBox, QDoubleSpinBox {{
    background: {p['input']}; color: {p['text']};
    border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 5px 8px; font-size: 12px;
}}
QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
/* Visible up/down stepper buttons: a filled column on the right edge with
   clear arrows, instead of the invisible transparent zones that made spin
   boxes read as plain little text boxes. */
QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border; subcontrol-position: top right;
    width: 20px; border: none; border-left: 1px solid {p['border_in']};
    border-top-right-radius: 8px; background: {p['hover']};
}}
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 20px; border: none; border-left: 1px solid {p['border_in']};
    border-bottom-right-radius: 8px; background: {p['hover']};
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
    background: {p['border_in']};
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url("{arrow_up}"); width: 10px; height: 10px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url("{arrow_down}"); width: 10px; height: 10px;
}}

QFileDialog QTreeView, QFileDialog QListView {{
    background: {p['input']}; border: 1px solid {p['border_in']};
    border-radius: 6px; color: {p['text']}; outline: none;
}}
QFileDialog QTreeView::item, QFileDialog QListView::item {{
    padding: 3px 6px; color: {p['text']};
}}
QFileDialog QTreeView::item:hover, QFileDialog QListView::item:hover {{
    background: {p['hover']};
}}
QFileDialog QTreeView::item:selected, QFileDialog QListView::item:selected {{
    background: {ACCENT}; color: white;
}}
QHeaderView::section {{
    background: {p['btn']}; color: {p['text2']}; border: none;
    padding: 4px 8px; font-size: 11px;
}}
QFileDialog QToolButton {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']};
    border-radius: 6px; padding: 4px 8px;
}}
QFileDialog QToolButton:hover {{ background: {p['btn_hover']}; }}
"""
