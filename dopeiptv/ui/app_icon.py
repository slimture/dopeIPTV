"""The app's own icon: the blue tile with its white play triangle - or
the one the person picks on Settings > Appearance (`LOOKS`): the heart
icon (the tile shaded from the blue into the heart's colour and the
triangle crowned with a heart), the tile in their own accent, darker,
lighter and brighter tiles, a round one and a retro TV.

Drawn here, at every size a desktop asks for, rather than shipped as a
file: a frozen build has nothing extra to bundle or to lose. It is the
window icon - the taskbar on Windows and X11, the Dock while the app runs
on macOS - and on Linux it is also written into the user's icon theme,
where a Wayland shell takes the icon of the window's entry from
(core/desktop_entry.py). The entry itself is never touched. On macOS the
one picked is also kept on the app itself as Finder's custom icon, so the
Dock and Finder show it while the app is closed (`keep_on_bundle`; nothing
in Contents changes). What a package ships - the .deb's and the AppImage's
theme icons, the Flatpak's exported ones, the .icns, the .ico - is the
plain icon, fixed when it was built.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
)

from ..core.log import log
from .theme import ACCENTS, HEART
from .widgets import heart_shape

# An app icon is drawn INSIDE its canvas, not edge to edge, and both
# desktops that care ask for the same thing:
#
#   Apple  - the rounded square is 824 pt on a 1024 pt canvas: 80.5% of
#            the canvas, ~9.8% margin a side.
#   GNOME  - "a margin that measures about 10% of the image canvas ...
#            it's best when the image occupies about 80%".
#
# Drawing full-bleed makes ours stand a quarter larger than every icon
# beside it, because the shell sizes by canvas and everyone else's canvas
# carries that margin. It was the macOS Dock first and the GNOME dash
# second, from the same cause and with the same fix.
#
# Windows is left full-bleed: its shell adds the padding itself, and no
# one has reported ours looking oversized there.
ICON_SAFE_INSET = 0.098

#: The icon's own colour: the default accent, whichever theme or accent the
#: user picked - the packaged icons are this blue too.
ICON_ACCENT = ACCENTS["blue"][1]

@dataclass(frozen=True)
class Look:
    """One of the icons to pick from: its tile's colour (two for a
    diagonal shade; "" the person's accent), the play triangle's colour,
    the tile's shape (a rounded square, a circle, a TV), a heart on the
    triangle's tip, a hairline round a tile that would vanish into a
    window of its own colour."""
    key: str
    tile: tuple[str, ...]
    mark: str = "#ffffff"
    shape: str = "square"
    heart: bool = False
    edge: str = ""


#: The blue tile and its white triangle: every package's own icon.
DEFAULT = "classic"
#: The icons on Settings > Appearance, in their order there. Their names
#: are `icon_<key>` in i18n.
LOOKS: dict[str, Look] = {lk.key: lk for lk in (
    Look("classic", (ICON_ACCENT,)),
    Look("heart", (ICON_ACCENT, HEART), heart=True),
    Look("accent", ("",)),
    Look("midnight", ("#262a35", "#0c0e13"), mark=ICON_ACCENT, edge="#3a4050"),
    Look("paper", ("#ffffff", "#e6eaf1"), mark=ICON_ACCENT, edge="#cfd5df"),
    Look("ocean", ("#36c6e8", "#2350d8")),
    Look("forest", ("#38c98a", "#0f7257")),
    Look("sunset", ("#ffa23d", "#e5437e")),
    Look("grape", ("#9b6bff", "#c24bd2")),
    Look("round", (ICON_ACCENT,), shape="circle"),
    Look("retro", ("#f6ead2", "#e3cfa8"), mark=ICON_ACCENT, shape="tv"),
)}
#: The TV's screen and antennas in "retro".
_SCREEN = "#1f232b"

#: Settings > Appearance's choice (a key of LOOKS), the household's; never
#: picked, the blue one.
SETTING = "app_icon"

_THEME_SIZES = (256, 128, 64, 48, 32)


def make_app_icon(inset: float = 0.0, sizes=(256, 128, 64, 48, 32),
                  look: str = "", accent: str = "") -> QIcon:
    """Draw the app icon (rounded accent tile with a play triangle).

    *inset* is the fraction of each side left as transparent margin; pass
    ICON_SAFE_INSET on macOS and Linux, 0 on Windows.

    *sizes* is every pixel size to render. QIcon.pixmap() never scales UP,
    so a size not drawn here comes back as the largest one that was: asking
    for 1024 got a 256 px image, and that is what the retina Dock icon was
    built from.

    *look* is one of LOOKS ("" or one not known: the blue one). The heart
    icon's heart is a little larger at panel sizes, so it still reads as
    a heart at 32 px. *accent* is the colour of "accent"'s tile (the
    theme's accent when not given)."""
    chosen = LOOKS.get(look) or LOOKS[DEFAULT]
    if not accent:
        from . import theme
        accent = theme.ACCENT
    icon = QIcon()
    for s in sizes:
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off = s * inset
        draw_look(p, chosen, QRectF(off, off, s - 2 * off, s - 2 * off), s, accent)
        p.end()
        icon.addPixmap(pm)
    return icon


def _triangle(r: QRectF) -> QPainterPath:
    """The play triangle in *r*, centred by its weight (centroid at the
    middle), not by its box: one centred by its box looks shifted left."""
    tri = QPainterPath()
    tri.moveTo(r.x() + r.width() * 0.38, r.y() + r.height() * 0.28)
    tri.lineTo(r.x() + r.width() * 0.38, r.y() + r.height() * 0.72)
    tri.lineTo(r.x() + r.width() * 0.74, r.y() + r.height() * 0.50)
    tri.closeSubpath()
    return tri


def draw_look(p: QPainter, look: Look, r: QRectF, px: int, accent: str) -> None:
    """One icon drawn into the square *r* (*px* the size it is drawn for:
    the heart grows a little at panel sizes)."""
    side = r.width()
    tile = QPainterPath()
    if look.shape == "circle":
        tile.addEllipse(r)
    else:
        tile.addRoundedRect(r, side * 0.22, side * 0.22)
    colours = [c or accent for c in look.tile]
    if len(colours) == 1:
        p.fillPath(tile, QColor(colours[0]))
    else:
        shade = QLinearGradient(r.topLeft(), r.bottomRight())
        shade.setColorAt(0.0, QColor(colours[0]))
        shade.setColorAt(1.0, QColor(colours[1]))
        p.fillPath(tile, shade)
    if look.edge:
        width = max(1.0, side / 64)
        pen = QPen(QColor(look.edge))
        pen.setWidthF(width)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        half = width / 2
        p.drawRoundedRect(r.adjusted(half, half, -half, -half),
                          side * 0.22 - half, side * 0.22 - half)
        p.setPen(Qt.PenStyle.NoPen)
    if look.shape == "tv":
        screen = QRectF(r.x() + side * 0.14, r.y() + side * 0.30,
                        side * 0.72, side * 0.56)
        box = QPainterPath()
        box.addRoundedRect(screen, side * 0.10, side * 0.10)
        p.fillPath(box, QColor(_SCREEN))
        pen = QPen(QColor(_SCREEN))
        pen.setWidthF(max(1.2, side * 0.045))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        foot = QPointF(r.x() + side * 0.5, screen.y())
        p.drawLine(foot, QPointF(r.x() + side * 0.36, r.y() + side * 0.13))
        p.drawLine(foot, QPointF(r.x() + side * 0.64, r.y() + side * 0.13))
        p.setPen(Qt.PenStyle.NoPen)
        p.fillPath(_triangle(screen), QColor(look.mark))
        return
    p.fillPath(_triangle(r), QColor(look.mark))
    if look.heart:
        w = side * (0.25 if px > 48 else 0.28)
        h = w * 0.84
        cx, cy = r.x() + side * 0.74, r.y() + side * 0.28
        p.fillPath(heart_shape(QRectF(cx - w / 2, cy - h / 2, w, h)),
                   QColor("white"))


def platform_icon(look: str = "", accent: str = "") -> QIcon:
    """The icon as this system wants it: Apple's and GNOME's margin on
    macOS and Linux, full bleed on Windows; big enough for a retina Dock.

    On macOS setWindowIcon OVERRIDES the bundle's .icns in the Dock the
    moment the app starts, so giving the .icns Apple's margin was not
    enough on its own - this icon replaced it a second later and the Dock
    icon stayed a quarter larger than its neighbours. Same margin here,
    and drawn large enough for a retina Dock (it only ever went to 256).

    Linux takes the margin too: GNOME asks for the same 80% of canvas
    that Apple does, and full-bleed made the dash icon stand out exactly
    the way the Dock one did. Windows keeps full-bleed - its shell adds
    the padding itself."""
    _mac = sys.platform == "darwin"
    _padded = _mac or sys.platform.startswith("linux")
    return make_app_icon(
        inset=ICON_SAFE_INSET if _padded else 0.0,
        sizes=((512, 256, 128, 64, 48, 32) if _mac
               else (256, 128, 64, 48, 32)),
        look=look, accent=accent)


#: 1.6.10's switch for the heart icon (Settings > General), read once.
OLD_SWITCH = "heart_icon"


def carry_over(settings) -> None:
    """1.6.10's heart switch, turned on by hand, becomes the picker's heart
    - a choice made is kept - and the switch goes. Nothing else from before
    picks an icon: a supporter key never does."""
    try:
        if (str(settings.value(OLD_SWITCH, "") or "") == "true"
                and not settings.contains(SETTING)):
            settings.setValue(SETTING, "heart")
        if settings.contains(OLD_SWITCH):
            settings.remove(OLD_SWITCH)
    except Exception as e:
        log.warning("app icon: 1.6.10's switch not carried over: %s", e)


def chosen(settings) -> str:
    """The icon to show, a key of LOOKS: the one picked, else - never
    picked, one not known, anything unreadable - the blue one."""
    try:
        picked = str(settings.value(SETTING, "") or "")
    except Exception:
        return DEFAULT
    return picked if picked in LOOKS else DEFAULT


#: Which icon the app shows now - its look and, for "accent", the colour
#: (None: none set by this module yet).
_shown: tuple[str, str] | None = None
#: Whether this run writes the icon into the user's icon theme - only the
#: real start does (app.main), so a window built anywhere else, a test's
#: for one, never touches the user's files.
_theme_icons = False


def show(look: str, theme_icons: bool | None = None) -> bool:
    """Make *look* (a key of LOOKS) the app's icon, now: the window icon
    (Qt passes it on to every open window and, on macOS, to the Dock), and
    on Linux the icon theme's copy. True when it changed.

    *theme_icons* is given once, by the start: whether this run keeps the
    icon theme's copy in step (Linux only)."""
    global _shown, _theme_icons
    if theme_icons is not None:
        _theme_icons = bool(theme_icons) and sys.platform.startswith("linux")
    if look not in LOOKS:
        look = DEFAULT
    from . import theme
    accent = theme.ACCENT if look == "accent" else ""
    if _shown == (look, accent):
        return False
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return False
    icon = platform_icon(look=look, accent=accent)
    app.setWindowIcon(icon)
    if _shown is not None:
        log.info("app icon: %s", look)
    _shown = (look, accent)
    if _theme_icons:
        install_icon(icon)
    return True


#: What this app last put on its bundle as the Mac app's own icon
#: ("look|accent|bundle"); none while the bundle has its own. A key with a
#: leading "_": this computer's, never in a backup or synced.
BUNDLE_KEY = "_bundle_icon"


def _bundle() -> Path | None:
    """The running dopeIPTV.app, when its icon may be changed: a built app,
    not moved aside by macOS nor run from the read-only .dmg, and this
    user's to change."""
    from ..core import macupdate
    app = macupdate.app_bundle()
    if app is None or "/AppTranslocation/" in str(app):
        return None
    return app if os.access(app, os.W_OK) else None


def keep_on_bundle(settings, look: str) -> str:
    """The icon picked kept on the Mac app itself, so the Dock, Finder and
    Launchpad show it while the app is closed too - setWindowIcon lasts only
    while it runs (core/platform_macos.set_bundle_icon). The blue one takes
    away only a custom icon this app put there: one the person gave the app
    in Finder's Get Info stays. An update brings a new bundle with the blue
    icon; the next start puts the picked one back. "set", "removed", or ""
    when nothing was done."""
    app = _bundle()
    if app is None:
        return ""
    from ..core import platform_macos
    if look not in LOOKS:
        look = DEFAULT
    from . import theme
    accent = theme.ACCENT if look == "accent" else ""
    try:
        had = str(settings.value(BUNDLE_KEY, "") or "")
    except Exception:
        had = ""
    custom = (app / platform_macos.CUSTOM_ICON).exists()
    if look == DEFAULT:
        if not had:
            return ""
        if custom and not platform_macos.set_bundle_icon(str(app), None):
            return ""
        settings.remove(BUNDLE_KEY)
        log.info("app icon: the Mac app's own icon again")
        return "removed"
    want = f"{look}|{accent}|{app}"
    if had == want and custom:
        return ""
    png = _png_bytes(make_app_icon(inset=ICON_SAFE_INSET, sizes=(1024,),
                                   look=look, accent=accent), 1024)
    if not png or not platform_macos.set_bundle_icon(str(app), png):
        return ""
    settings.setValue(BUNDLE_KEY, want)
    log.info("app icon: %s kept on the Mac app", look)
    return "set"


def install_icon(icon: QIcon) -> bool:
    """Save the icon into the XDG icon theme so .desktop files can find it.
    True when a file was written.

    Every rendered size, not just 256: a panel or a dash asks for 32 or 48,
    and with only the big one there it downscales, which is why the icon
    looked soft beside sharp system icons.

    An existing file is replaced when the icon has actually CHANGED, and
    left alone byte for byte when it has not. "Skip anything already
    there" was the obvious rule and the wrong one: the day the drawing
    changed - a margin added, a colour moved - every machine that had ever
    run the app kept the old picture forever, which is the one case where
    rewriting matters.

    A changed file is written beside the old one and moved over it, so a
    shell reading the icon while another replaces it
    (or back) never sees half a PNG; and the theme directory is touched,
    which is what tells GTK - and so GNOME's dash - to look again."""
    base = Path(os.environ.get("XDG_DATA_HOME")
                or Path.home() / ".local" / "share")
    theme = base / "icons" / "hicolor"
    newest = 0.0
    wrote = False
    for size in _THEME_SIZES:
        target = theme / f"{size}x{size}" / "apps" / "dopeiptv.png"
        try:
            fresh = _png_bytes(icon, size)
            if not fresh:
                continue
            if not target.exists() or target.read_bytes() != fresh:
                target.parent.mkdir(parents=True, exist_ok=True)
                _replace(target, fresh)
                wrote = True
            newest = max(newest, target.stat().st_mtime)
        except OSError:
            pass
    if newest:
        _drop_stale_icon_cache(theme, newest)
    if wrote:
        try:
            os.utime(theme, None)
        except OSError:
            pass
    return wrote


def _replace(target: Path, data: bytes) -> None:
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def _png_bytes(icon: QIcon, size: int) -> bytes:
    """The icon at *size*, encoded as PNG, without touching the disk - so
    it can be compared against what is already there."""
    from PyQt6.QtCore import QBuffer
    # No QByteArray argument: passing one hands the buffer a temporary that
    # Python frees immediately, and writing into it segfaults. Default
    # construction gives the buffer an internal array it owns.
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    ok = icon.pixmap(size, size).save(buf, "PNG")
    buf.close()
    return bytes(buf.data()) if ok else b""


def _drop_stale_icon_cache(theme: Path, icon_mtime: float) -> None:
    """Writing the PNG is not enough on its own.

    Where icon-theme.cache exists, GTK - and so GNOME's shell and dash -
    reads THAT instead of scanning the directory, and an icon that was not
    in the theme when the cache was built is invisible however correct the
    file on disk is. A cache four months older than the icons is exactly
    what months of testing leaves behind, and the symptom is an icon that
    never appears while everything else looks right.

    Keyed on the cache being OLDER than our icon, not on having just
    written one: by the time this bites, the icons have usually been on
    disk for a while and there is nothing new to write, so "did we write
    something this run" would skip precisely the case that needs fixing.

    The cache is derived data - removing it costs a directory scan and
    nothing else, and whatever built it will build it again with our icon
    in it this time. The directory's own timestamp is bumped as well,
    since that is the other half of the staleness check."""
    try:
        cache = theme / "icon-theme.cache"
        if not cache.exists() or cache.stat().st_mtime >= icon_mtime:
            return
        cache.unlink()
        os.utime(theme, None)
        log.info("icon theme: dropped %s - it predates our icons, so the "
                 "desktop could not see them", cache)
    except OSError:
        pass        # a cache we cannot remove is not worth failing over
