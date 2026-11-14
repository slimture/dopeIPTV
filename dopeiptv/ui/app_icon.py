"""The app's own icon: the accent tile with its play triangle - or, for a
supporter who keeps it on, the supporter's: the tile shaded from the accent
into the heart's colour and the triangle crowned with a heart, the same
mark as their sidebar logo (widgets._SidebarLogo.set_supporter).

Drawn here, at every size a desktop asks for, rather than shipped as a
file: a frozen build has nothing extra to bundle or to lose. It is the
window icon - the taskbar on Windows and X11, the Dock while the app runs
on macOS - and on Linux it is also written into the user's icon theme,
where a Wayland shell takes the icon of the window's entry from
(core/desktop_entry.py). The entry itself is never touched. What a
package ships - the .deb's and the AppImage's theme icons, the Flatpak's
exported ones, the .icns, the .ico - is the plain icon, fixed when it was
built.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPixmap,
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

#: The Settings switch (Settings > General > Support), on unless turned off.
SETTING = "supporter_icon"

_THEME_SIZES = (256, 128, 64, 48, 32)


def make_app_icon(inset: float = 0.0, sizes=(256, 128, 64, 48, 32),
                  supporter: bool = False) -> QIcon:
    """Draw the app icon (rounded accent tile with a play triangle).

    *inset* is the fraction of each side left as transparent margin; pass
    ICON_SAFE_INSET on macOS and Linux, 0 on Windows.

    *sizes* is every pixel size to render. QIcon.pixmap() never scales UP,
    so a size not drawn here comes back as the largest one that was: asking
    for 1024 got a 256 px image, and that is what the retina Dock icon was
    built from.

    *supporter* draws the supporter's icon: the same tile and triangle, the
    tile shaded diagonally from the accent into the heart's colour and a
    heart crowning the triangle's tip - a little larger at panel sizes, so
    it still reads as a heart at 32 px."""
    icon = QIcon()
    for s in sizes:
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off = s * inset
        side = s - 2 * off
        tile = QPainterPath()
        tile.addRoundedRect(off, off, side, side, side * 0.22, side * 0.22)
        if supporter:
            shade = QLinearGradient(QPointF(off, off),
                                    QPointF(off + side, off + side))
            shade.setColorAt(0.0, QColor(ICON_ACCENT))
            shade.setColorAt(1.0, QColor(HEART))
            p.fillPath(tile, shade)
        else:
            p.fillPath(tile, QColor(ICON_ACCENT))
        tri = QPainterPath()
        # Centred by its weight (centroid at the middle), not by its box:
        # a play triangle centred by its box looks shifted left.
        tri.moveTo(off + side * 0.38, off + side * 0.28)
        tri.lineTo(off + side * 0.38, off + side * 0.72)
        tri.lineTo(off + side * 0.74, off + side * 0.50)
        tri.closeSubpath()
        p.fillPath(tri, QColor("white"))
        if supporter:
            w = side * (0.25 if s > 48 else 0.28)
            h = w * 0.84
            cx, cy = off + side * 0.74, off + side * 0.28
            p.fillPath(heart_shape(QRectF(cx - w / 2, cy - h / 2, w, h)),
                       QColor("white"))
        p.end()
        icon.addPixmap(pm)
    return icon


def platform_icon(supporter: bool = False) -> QIcon:
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
        supporter=supporter)


def switched_on(settings) -> bool:
    """The Settings switch: on unless the supporter turned it off."""
    try:
        return str(settings.value(SETTING, "true")) != "false"
    except Exception:
        return True


def wanted(settings) -> bool:
    """Whether the supporter's icon is the one to show: a key that checks
    out (core/supporter.py) and the switch left on."""
    if not switched_on(settings):
        return False
    from ..core import supporter
    return supporter.is_supporter(settings)


#: Which icon the app shows now (None: none set by this module yet).
_shown: bool | None = None
#: Whether this run writes the icon into the user's icon theme - only the
#: real start does (app.main), so a window built anywhere else, a test's
#: for one, never touches the user's files.
_theme_icons = False


def show(supporter: bool, theme_icons: bool | None = None) -> bool:
    """Make the supporter's icon - or the plain one - the app's, now: the
    window icon (Qt passes it on to every open window and, on macOS, to the
    Dock), and on Linux the icon theme's copy. True when it changed.

    *theme_icons* is given once, by the start: whether this run keeps the
    icon theme's copy in step (Linux only)."""
    global _shown, _theme_icons
    if theme_icons is not None:
        _theme_icons = bool(theme_icons) and sys.platform.startswith("linux")
    supporter = bool(supporter)
    if _shown is supporter:
        return False
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return False
    icon = platform_icon(supporter)
    app.setWindowIcon(icon)
    if _shown is not None:
        log.info("app icon: %s", "the supporter's" if supporter else "the plain one")
    _shown = supporter
    if _theme_icons:
        install_icon(icon)
    return True


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
    shell reading the icon while the supporter's replaces the plain one
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
