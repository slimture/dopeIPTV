"""Theme engine: palettes, accent colors, and the global QSS builder."""

from __future__ import annotations

import re

ACCENTS = {
    "blue":   ("Blue",   "#4C8DFF", "#5E99FF"),
    "purple": ("Purple", "#8E6BFF", "#A184FF"),
    "teal":   ("Teal",   "#2AC3C3", "#4AD4D4"),
    "green":  ("Green",  "#2FBF71", "#4CD08A"),
    "orange": ("Orange", "#FF9F43", "#FFB160"),
    "pink":   ("Pink",   "#FF5C8A", "#FF7AA1"),
    "red":    ("Red",    "#FF5C5C", "#FF7A7A"),
    "gold":   ("Gold",   "#E8B339", "#F0C35A"),
    "indigo": ("Indigo", "#5B6CFF", "#7381FF"),
    "mint":   ("Mint",   "#34CFA0", "#55DBB2"),
}
# Accent choices that are not one colour: the theme's own (its "accent"
# entry, blue where it has none) and one the user picked ("accent_custom").
ACCENT_THEME, ACCENT_CUSTOM = "theme", "custom"

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
        "accent": ("#88C0D0", "#9DCDDB"),
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
        "accent": ("#BD93F9", "#CBA8FA"),
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
        "accent": ("#FE8019", "#FE9540"),
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
        "accent": ("#268BD2", "#3A99DA"),
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
        "accent": ("#CBA6F7", "#D6B9F9"),
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
        "light": True,
    },
    "tokyonight": {
        "name": "Tokyo Night",
        "bg": "#1A1B26", "side": "#16161E", "pane": "#1E2030",
        "hover": "#222436", "sel": "#292E42", "input": "#16161E",
        "btn": "#24283B", "btn_hover": "#2F3549",
        "border": "#15161E", "border_in": "#2F3549",
        "scroll": "#3B4261", "scroll_hover": "#414868",
        "text": "#C0CAF5", "text2": "#A9B1D6", "text3": "#9AA5CE",
        "muted": "#8089B3", "muted2": "#737AA2", "muted3": "#5E6590",
        "muted4": "#6B7199", "error": "#F7768E", "rec": "#F7768E",
        "accent": ("#7AA2F7", "#8FB2FA"),
    },
    "rosepine": {
        "name": "Rosé Pine",
        "bg": "#191724", "side": "#15131F", "pane": "#1F1D2E",
        "hover": "#221F31", "sel": "#2C2942", "input": "#15131F",
        "btn": "#26233A", "btn_hover": "#403D52",
        "border": "#13111B", "border_in": "#403D52",
        "scroll": "#403D52", "scroll_hover": "#524F67",
        "text": "#E0DEF4", "text2": "#CDCAE6", "text3": "#ABA7C6",
        "muted": "#908CAA", "muted2": "#7E7A98", "muted3": "#6E6A86",
        "muted4": "#857F9F", "error": "#EB6F92", "rec": "#EB6F92",
        "accent": ("#C4A7E7", "#D3BCF0"),
    },
    "onedark": {
        "name": "One Dark",
        "bg": "#282C34", "side": "#21252B", "pane": "#2C313A",
        "hover": "#2F343F", "sel": "#3E4451", "input": "#21252B",
        "btn": "#333842", "btn_hover": "#3E4451",
        "border": "#1B1F27", "border_in": "#3E4451",
        "scroll": "#4B5263", "scroll_hover": "#5C6370",
        "text": "#D7DAE0", "text2": "#ABB2BF", "text3": "#9DA5B4",
        "muted": "#8B929E", "muted2": "#7F848E", "muted3": "#636B78",
        "muted4": "#828997", "error": "#E06C75", "rec": "#E06C75",
        "accent": ("#61AFEF", "#7CBDF2"),
    },
    "everforest": {
        "name": "Everforest",
        "bg": "#2D353B", "side": "#232A2E", "pane": "#323C41",
        "hover": "#343F44", "sel": "#3D484D", "input": "#232A2E",
        "btn": "#343F44", "btn_hover": "#3D484D",
        "border": "#1E2326", "border_in": "#475258",
        "scroll": "#475258", "scroll_hover": "#56635F",
        "text": "#D3C6AA", "text2": "#C4B99D", "text3": "#9DA9A0",
        "muted": "#859289", "muted2": "#7A8478", "muted3": "#68736B",
        "muted4": "#859289", "error": "#E67E80", "rec": "#E67E80",
        "accent": ("#A7C080", "#B8CE94"),
    },
    "kanagawa": {
        "name": "Kanagawa",
        "bg": "#1F1F28", "side": "#16161D", "pane": "#22222C",
        "hover": "#2A2A37", "sel": "#2D4F67", "input": "#16161D",
        "btn": "#2A2A37", "btn_hover": "#363646",
        "border": "#16161D", "border_in": "#363646",
        "scroll": "#363646", "scroll_hover": "#54546D",
        "text": "#DCD7BA", "text2": "#C8C093", "text3": "#A6A28C",
        "muted": "#938F80", "muted2": "#807E72", "muted3": "#727169",
        "muted4": "#8A8878", "error": "#E46876", "rec": "#E82424",
        "accent": ("#7E9CD8", "#93ADE0"),
    },
    "contrast": {
        "name": "High contrast",
        "bg": "#000000", "side": "#000000", "pane": "#0A0A0A",
        "hover": "#1F1F1F", "sel": "#333333", "input": "#000000",
        "btn": "#1A1A1A", "btn_hover": "#2E2E2E",
        "border": "#8A8A8A", "border_in": "#BFBFBF",
        "scroll": "#8A8A8A", "scroll_hover": "#BFBFBF",
        "text": "#FFFFFF", "text2": "#FFFFFF", "text3": "#F0F0F0",
        "muted": "#E0E0E0", "muted2": "#D0D0D0", "muted3": "#C8C8C8",
        "muted4": "#D8D8D8", "error": "#FF6B6B", "rec": "#FF4D4D",
        "accent": ("#1A73E8", "#4A90F0"),
    },
    "latte": {
        "name": "Catppuccin (latte)",
        "bg": "#EFF1F5", "side": "#E6E9EF", "pane": "#F7F8FA",
        "hover": "#DCE0E8", "sel": "#CCD0DA", "input": "#FFFFFF",
        "btn": "#DCE0E8", "btn_hover": "#CCD0DA",
        "border": "#CCD0DA", "border_in": "#BCC0CC",
        "scroll": "#BCC0CC", "scroll_hover": "#ACB0BE",
        "text": "#4C4F69", "text2": "#5C5F77", "text3": "#6C6F85",
        "muted": "#6C6F85", "muted2": "#7C7F93", "muted3": "#8C8FA1",
        "muted4": "#7C7F93", "error": "#D20F39", "rec": "#D20F39",
        "accent": ("#8839EF", "#9A55F2"), "light": True,
    },
    "solarized_light": {
        "name": "Solarized (light)",
        "bg": "#FDF6E3", "side": "#EEE8D5", "pane": "#FFFBF0",
        "hover": "#EEE8D5", "sel": "#E4DCC4", "input": "#FFFDF6",
        "btn": "#EEE8D5", "btn_hover": "#E4DCC4",
        "border": "#E4DCC4", "border_in": "#D6CDB2",
        "scroll": "#D6CDB2", "scroll_hover": "#C4BB9F",
        "text": "#073642", "text2": "#586E75", "text3": "#657B83",
        "muted": "#657B83", "muted2": "#7B8E91", "muted3": "#839496",
        "muted4": "#7B8E91", "error": "#DC322F", "rec": "#DC322F",
        "accent": ("#268BD2", "#3A99DA"), "light": True,
    },
    # Themes that differ by colour, not by shade: each has a hue of its own
    # (sea, forest, wine, neon, an amber terminal, coffee, paper, sky).
    "ocean": {
        "name": "Ocean",
        "bg": "#062B36", "side": "#04212A", "pane": "#08323F",
        "hover": "#0B3A48", "sel": "#0F4A5C", "input": "#0A3644",
        "btn": "#0E4352", "btn_hover": "#145566",
        "border": "#0D3D4B", "border_in": "#13505F",
        "scroll": "#1A5A6B", "scroll_hover": "#24707F",
        "text": "#E3F6F8", "text2": "#BFE3E8", "text3": "#97CBD3",
        "muted": "#7FB3BC", "muted2": "#5F939C", "muted3": "#4A7A83",
        "muted4": "#6DA2AB", "error": "#FF7A7A", "rec": "#FF6B6B",
        "accent": ("#0F9F94", "#14B8A6"),
    },
    "forest": {
        "name": "Forest",
        "bg": "#0E2318", "side": "#0A1B12", "pane": "#12291D",
        "hover": "#163223", "sel": "#1D402D", "input": "#142E21",
        "btn": "#1A3A29", "btn_hover": "#234B36",
        "border": "#173526", "border_in": "#20462F",
        "scroll": "#2A543B", "scroll_hover": "#36694B",
        "text": "#E6F4EA", "text2": "#C4E2CD", "text3": "#9CC7A9",
        "muted": "#86B293", "muted2": "#679477", "muted3": "#527B61",
        "muted4": "#74A383", "error": "#FF7B72", "rec": "#FF6B6B",
        "accent": ("#1E9E50", "#2BB463"),
    },
    "bordeaux": {
        "name": "Bordeaux",
        "bg": "#2A0E17", "side": "#210A12", "pane": "#31111C",
        "hover": "#3A1522", "sel": "#4B1C2D", "input": "#36131F",
        "btn": "#441A29", "btn_hover": "#562236",
        "border": "#3D1725", "border_in": "#4F2032",
        "scroll": "#5E2A3D", "scroll_hover": "#743650",
        "text": "#F8E8EC", "text2": "#E7C7D0", "text3": "#D0A3B0",
        "muted": "#BB8C99", "muted2": "#9A6E7B", "muted3": "#805866",
        "muted4": "#AA7C89", "error": "#FF9A8F", "rec": "#FF6B6B",
        "accent": ("#C8784C", "#D88A5E"),
    },
    "synthwave": {
        "name": "Synthwave",
        "bg": "#1B0B33", "side": "#140826", "pane": "#210E3D",
        "hover": "#291248", "sel": "#36185E", "input": "#250F44",
        "btn": "#311553", "btn_hover": "#401C6B",
        "border": "#2D1350", "border_in": "#3C1B66",
        "scroll": "#4A2378", "scroll_hover": "#5C2E93",
        "text": "#F5EBFF", "text2": "#DCC8F5", "text3": "#BFA6E6",
        "muted": "#A68BD0", "muted2": "#866BB0", "muted3": "#6F5796",
        "muted4": "#977CC0", "error": "#FF6B9A", "rec": "#FF5C8A",
        "accent": ("#E03CAE", "#EC5BC0"),
    },
    "amber": {
        "name": "Amber terminal",
        "bg": "#0D0A05", "side": "#080603", "pane": "#130F07",
        "hover": "#1C160A", "sel": "#2B210E", "input": "#17120A",
        "btn": "#221A0C", "btn_hover": "#302410",
        "border": "#211A0C", "border_in": "#2E2412",
        "scroll": "#3A2D14", "scroll_hover": "#4D3C1A",
        "text": "#FFC24D", "text2": "#E8AE3E", "text3": "#CC9733",
        "muted": "#A87C2E", "muted2": "#86632A", "muted3": "#6B5023",
        "muted4": "#977030", "error": "#FF5F4A", "rec": "#FF5F4A",
        "accent": ("#CC7A00", "#E08E10"),
    },
    "espresso": {
        "name": "Espresso",
        "bg": "#1E1712", "side": "#17110D", "pane": "#251C16",
        "hover": "#2C221A", "sel": "#3A2C22", "input": "#291F18",
        "btn": "#33271E", "btn_hover": "#423328",
        "border": "#30251C", "border_in": "#3E3026",
        "scroll": "#4A3A2E", "scroll_hover": "#5E4A3B",
        "text": "#F3E9DF", "text2": "#DCCBBA", "text3": "#C2AC96",
        "muted": "#A88F78", "muted2": "#88715D", "muted3": "#705C4B",
        "muted4": "#98806A", "error": "#FF7B6B", "rec": "#FF6B5C",
        "accent": ("#B87A45", "#C88C58"),
    },
    "sand": {
        "name": "Sand",
        "bg": "#F3EAD7", "side": "#E9DDC4", "pane": "#F8F1E3",
        "hover": "#E6D8BC", "sel": "#DCCAA6", "input": "#FFFAF0",
        "btn": "#E6D8BC", "btn_hover": "#DCCAA6",
        "border": "#DCCDAE", "border_in": "#CDBB97",
        "scroll": "#CDBB97", "scroll_hover": "#B9A57F",
        "text": "#3B2F1E", "text2": "#55462F", "text3": "#6A5A40",
        "muted": "#6F5F45", "muted2": "#85755A", "muted3": "#9C8C70",
        "muted4": "#85755A", "error": "#B03024", "rec": "#C0392B",
        "accent": ("#A3561A", "#B5651D"), "light": True,
    },
    "sky": {
        "name": "Sky",
        "bg": "#E4EFFA", "side": "#D6E6F6", "pane": "#EEF5FC",
        "hover": "#CFE1F3", "sel": "#BBD4EE", "input": "#FFFFFF",
        "btn": "#D3E3F4", "btn_hover": "#C2D8EF",
        "border": "#C5D9EE", "border_in": "#AFC8E3",
        "scroll": "#AFC8E3", "scroll_hover": "#94B3D6",
        "text": "#102A43", "text2": "#243B53", "text3": "#334E68",
        "muted": "#4A5F78", "muted2": "#627D98", "muted3": "#7B93AB",
        "muted4": "#627D98", "error": "#C62828", "rec": "#D32F2F",
        "accent": ("#1E6FD9", "#3A85E3"), "light": True,
    },
}

#: Order of the theme picker: dark ones first, then the light ones.
THEME_ORDER = sorted(THEMES, key=lambda k: bool(THEMES[k].get("light")))

# Corners: what every border-radius in the stylesheet is scaled by.
CORNER_STYLES = {"sharp": 0.0, "rounded": 1.0, "round": 1.6}
_corners = 1.0

#: Interface sizes (percent). Applied as QT_SCALE_FACTOR when the app starts.
UI_SCALES = ("90", "100", "110", "125", "150")


def is_light(theme: str) -> bool:
    return bool(THEMES.get(theme, {}).get("light"))


def ui_scale_factor(value) -> float | None:
    """QT_SCALE_FACTOR for a stored interface size, or None to leave Qt's."""
    try:
        pct = int(str(value))
    except (TypeError, ValueError):
        return None
    if str(pct) not in UI_SCALES or pct == 100:
        return None
    return pct / 100


def system_is_dark() -> bool | None:
    """The desktop's light or dark mode, where Qt can tell (Qt 6.5+)."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return None
        scheme = QGuiApplication.styleHints().colorScheme()
    except Exception:
        return None
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    return None


def effective_theme(settings, system_dark: bool | None = None) -> str:
    """The theme to show: the chosen one, or - following the desktop - the
    light one while the desktop is light. Unknown counts as dark."""
    theme = settings.value("theme", "graphite") or "graphite"
    if settings.value("theme_follow_system", "false") == "true":
        if system_dark is None:
            system_dark = system_is_dark()
        if system_dark is False:
            theme = settings.value("theme_light", "light") or "light"
    return theme if theme in THEMES else "graphite"

P: dict[str, str] = {}
ACCENT: str = ACCENTS["blue"][1]


def _lighter(hex_color: str) -> str:
    return _mix("#FFFFFF", hex_color, 0.18)


def _valid_hex(value) -> str | None:
    v = str(value or "").strip()
    if len(v) == 7 and v.startswith("#"):
        try:
            int(v[1:], 16)
            return v.upper()
        except ValueError:
            return None
    return None


def resolve_accent(theme: str, accent: str, custom=None) -> tuple[str, str]:
    """The accent colour and its hover shade for a theme and accent choice."""
    if accent == ACCENT_CUSTOM:
        hex_ = _valid_hex(custom)
        if hex_:
            return hex_, _lighter(hex_)
        accent = ACCENT_THEME
    if accent == ACCENT_THEME:
        own = THEMES.get(theme, {}).get("accent")
        if own:
            return own[0], own[1]
        accent = "blue"
    acc = ACCENTS.get(accent, ACCENTS["blue"])
    return acc[1], acc[2]


def apply_theme(settings=None, theme: str | None = None,
                accent: str | None = None,
                system_dark: bool | None = None) -> None:
    """Activate a theme + accent into the global palette ``P``."""
    global ACCENT, _corners
    custom = None
    if settings is not None:
        theme = theme or effective_theme(settings, system_dark)
        accent = accent or settings.value("accent", "blue")
        custom = settings.value("accent_custom", "")
        _corners = CORNER_STYLES.get(
            settings.value("corner_style", "rounded"), 1.0)
    theme = theme if theme in THEMES else "graphite"
    base = THEMES[theme]
    P.clear()
    P.update({k: v for k, v in base.items() if isinstance(v, str)})
    P["accent"], P["accent_hi"] = resolve_accent(theme, accent or "blue",
                                                 custom)
    ACCENT = P["accent"]


def corner_radius(px: float) -> float:
    """A radius drawn by hand, scaled like the stylesheet's."""
    if _corners == 0:
        return min(px, 2.0)
    return px * _corners


_RADIUS = re.compile(r"border-radius:\s*(\d+)px")


def rounded(qss: str, factor: float | None = None) -> str:
    """Scale every border-radius in *qss* to the chosen corner style. Sharp
    keeps a hint (2 px) of the rounding; Qt shrinks a radius too large for
    its widget on its own."""
    f = _corners if factor is None else factor

    def scale(m) -> str:
        px = int(m.group(1))
        if f == 1.0 or px == 0:
            return m.group(0)
        new = min(px, 2) if f == 0 else round(px * f)
        return f"border-radius: {new}px"

    return _RADIUS.sub(scale, qss)


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
    from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPixmap, \
        QPolygonF
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(
        base, f"qss_arrow_{direction}_{size}_{color.lstrip('#')}.png")
    if not os.path.exists(path) and QGuiApplication.instance() is None:
        # No GUI app yet (build_style called from a bare test/headless
        # context): constructing a QPixmap would hard-abort the process.
        # Return the path unrendered - the QSS just gets a dead url there.
        return path.replace("\\", "/")
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
    from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen, \
        QPixmap, QPolygonF
    base = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation)
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"qss_check_{size}_{color.lstrip('#')}.png")
    if not os.path.exists(path) and QGuiApplication.instance() is None:
        # See _arrow_png: no QPixmap without a QGuiApplication.
        return path.replace("\\", "/")
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
    return rounded(f"""
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
/* Video fullscreen: flipped via a dynamic property + single-widget repolish.
   Calling setStyleSheet() on the pane instead forced a style recompute of its
   ENTIRE subtree on every fullscreen toggle - the pane has grown (cast strip,
   EPG panel, controls), so that recompute is what made entering/leaving
   fullscreen feel slower with every release. */
#DetailPane[videofs="true"] {{ background: #000000; border: none; }}
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
   heavy bordered cards. Opaque backgrounds (row + holder), not transparent:
   on macOS a transparent row repainted in place over a parent that isn't
   repainted leaves the old glyphs underneath, double-exposing the list when
   the rows are rebuilt (e.g. right-clicking another channel). */
#EpgHolder {{ background: {p['pane']}; }}
QFrame#EpgRow {{ background: {p['pane']}; border: none; border-radius: 8px; }}
QFrame#EpgRow:hover {{ background: {p['hover']}; }}
QLabel#EpgRowTime  {{ color: {ACCENT}; font-size: 12px; font-weight: 700;
                      background: transparent; }}
QLabel#EpgRowTitle {{ font-size: 13px; background: transparent; }}

QPushButton {{
    background: {p['btn']}; border: 1px solid {p['btn_hover']}; border-radius: 9px;
    padding: 9px 16px; font-size: 13px; font-weight: 600;
}}
QPushButton:hover  {{ background: {p['btn_hover']}; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; }}
QPushButton#Primary:hover {{ background: {p['accent_hi']}; }}
/* A button that cannot be pressed has to look it: without these a disabled
   button - the accent one above all - read exactly like a live one. */
QPushButton:disabled {{ color: {p['muted2']}; }}
QPushButton#Primary:disabled {{ background: {p['btn']}; color: {p['muted2']}; }}
/* The poster play/pause/stop overlay is just a white glyph (drawn in code with
   a faint dark outline so it reads over any artwork) - no disc or ring. Fully
   transparent button; a barely-there wash on hover is the only chrome. */
QPushButton#PlayGhost {{ background: transparent; border: none; padding: 0; }}
QPushButton#PlayGhost:hover {{ background: rgba(255,255,255,0.10); border-radius: 6px; }}
QPushButton#MiniBtn {{
    padding: 0; font-size: 13px; border-radius: 6px; text-align: center;
}}
/* A clearly visible hover cue on the player control row - the generic
   btn->btn_hover shift is too subtle on the dark bar to read as a highlight. */
QPushButton#MiniBtn:hover {{
    background: {p['sel']}; border-color: {ACCENT};
}}
/* LIVE in the DVR row while following live: lit, the way a TV shows it. */
QPushButton#MiniBtn[atLive="true"] {{
    background: {p['rec']}; border-color: {p['rec']}; color: white;
    font-weight: 600;
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
""")
