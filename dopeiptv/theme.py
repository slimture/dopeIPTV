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


def build_style() -> str:
    """Generate the full application QSS from the active palette."""
    p = dict(P)
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
    padding: 8px 12px; text-align: left; font-size: 13px; color: {p['text2']};
}}
QPushButton#NavBtn:hover  {{ background: {p['hover']}; }}
QPushButton#NavBtn:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}

#SectionLabel {{
    color: {p['muted2']}; font-size: 10px; font-weight: 700;
    letter-spacing: 1.2px; padding: 10px 14px 4px 14px;
}}

QListWidget {{
    background: transparent; border: none; outline: none; font-size: 13px;
    color: {p['text2']};
}}
QListWidget::item {{ border-radius: 8px; padding: 7px 10px; margin: 1px 6px; }}
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
#NowTitle    {{ font-size: 14px; font-weight: 600; }}
#NowTime     {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
#NowDesc     {{ color: {p['text3']}; font-size: 12px; }}

QFrame#Card {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 12px;
}}
QLabel#EpgRowTime  {{ color: {ACCENT}; font-size: 11px; font-weight: 600; }}
QLabel#EpgRowTitle {{ font-size: 12px; }}

QPushButton {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']}; border-radius: 9px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover  {{ background: {p['btn_hover']}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; }}
QPushButton#Primary:hover {{ background: {p['accent_hi']}; }}
QPushButton#MiniBtn {{
    padding: 2px 4px; font-size: 10px; border-radius: 5px;
    min-width: 20px; min-height: 20px; max-height: 20px; text-align: center;
}}

QSplitter::handle {{ background: transparent; width: 6px; }}
QSplitter::handle:hover {{ background: {p['border_in']}; }}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p['scroll']}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {p['scroll_hover']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

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
QTabBar::tab {{
    background: transparent; color: {p['text2']}; padding: 7px 18px;
    border-radius: 7px; margin: 2px; font-size: 12px; min-width: 60px;
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
    image: none; border: none; background: transparent;
    width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p['muted2']};
    margin-right: 6px;
}}
QComboBox::down-arrow:hover {{ border-top-color: {p['text']}; }}
QComboBox QAbstractItemView {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 6px;
    selection-background-color: {ACCENT}; selection-color: white;
    outline: none; font-size: 12px; padding: 3px;
}}
QComboBox QAbstractItemView::item {{ min-height: 22px; padding: 3px 8px; }}
QComboBox#InlineCombo {{ padding: 3px 8px; font-size: 11px; }}
QComboBox#InlineCombo::drop-down {{ width: 14px; }}
QComboBox#InlineCombo::down-arrow {{
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-top: 4px solid {p['muted2']}; margin-right: 4px;
}}
QPushButton#InlineToggle {{
    padding: 4px 12px; font-size: 11px; border-radius: 7px;
}}
QPushButton#InlineToggle:checked {{ background: {ACCENT}; border: none; color: white; }}
#MiddlePane QLabel {{ color: {p['muted2']}; font-size: 11px; }}
QDateTimeEdit {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 5px 10px; font-size: 12px;
}}
QDateTimeEdit::up-button, QDateTimeEdit::down-button {{
    subcontrol-origin: border; width: 16px; border: none;
    background: transparent;
}}
QDateTimeEdit::up-arrow {{
    image: none; width: 0; height: 0;
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-bottom: 4px solid {p['muted2']};
}}
QDateTimeEdit::down-arrow {{
    image: none; width: 0; height: 0;
    border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-top: 4px solid {p['muted2']};
}}
QDateTimeEdit::up-arrow:hover {{ border-bottom-color: {p['text']}; }}
QDateTimeEdit::down-arrow:hover {{ border-top-color: {p['text']}; }}

QLineEdit {{
    background: {p['input']}; border: 1px solid {p['border_in']}; border-radius: 8px;
    padding: 8px 10px;
}}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}

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
