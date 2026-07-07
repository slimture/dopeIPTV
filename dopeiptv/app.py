"""Application entry point: icon generation, desktop integration, main()."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QSurfaceFormat
from PyQt6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, ORG
from .client import XtreamClient
from .dialogs import LoginDialog, PlaylistDialog
from .main_window import MainWindow
from .players import _libmpv, _libmpv_error, embedded_playback_reason
from .stores import PlaylistStore
from .theme import ACCENT, apply_theme, build_style


def make_app_icon() -> QIcon:
    """Draw the app icon (rounded accent tile with a play triangle)."""
    icon = QIcon()
    for s in (256, 128, 64, 48, 32):
        pm = QPixmap(s, s)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        tile = QPainterPath()
        tile.addRoundedRect(0, 0, s, s, s * 0.22, s * 0.22)
        p.fillPath(tile, QColor(ACCENT))
        tri = QPainterPath()
        tri.moveTo(s * 0.40, s * 0.28)
        tri.lineTo(s * 0.40, s * 0.72)
        tri.lineTo(s * 0.76, s * 0.50)
        tri.closeSubpath()
        p.fillPath(tri, QColor("white"))
        p.end()
        icon.addPixmap(pm)
    return icon


def install_icon(icon: QIcon) -> None:
    """Save the icon into the XDG icon theme so .desktop files can find it."""
    base = Path(os.environ.get(
        "XDG_DATA_HOME", Path.home() / ".local" / "share"))
    target = base / "icons" / "hicolor" / "256x256" / "apps" / "dopeiptv.png"
    if target.exists():
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        icon.pixmap(256, 256).save(str(target), "PNG")
    except OSError:
        pass


def _setup_opengl() -> None:
    """Configure OpenGL surface format for embedded mpv on macOS only."""
    if sys.platform != "darwin":
        return

    fmt = QSurfaceFormat()
    fmt.setVersion(4, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    fmt.setDepthBufferSize(0)
    fmt.setStencilBufferSize(0)
    QSurfaceFormat.setDefaultFormat(fmt)

def main() -> int:
    """Launch the application."""
    if _libmpv is None:
        print(f"[dopeIPTV] Embedded playback disabled: {_libmpv_error}",
              file=sys.stderr)

if _libmpv is not None and sys.platform == "darwin":
    QApplication.setAttribute(
        Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    _setup_opengl()

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("dopeiptv")
    icon = make_app_icon()
    app.setWindowIcon(icon)
    install_icon(icon)
    settings = QSettings(ORG, ORG)
    apply_theme(settings)
    app.setStyleSheet(build_style())
    print(f"[dopeIPTV] Qt platform: {app.platformName()}", file=sys.stderr)
    if _libmpv is not None:
        reason = embedded_playback_reason()
        if reason:
            print(f"[dopeIPTV] Embedded playback disabled: {reason}",
                  file=sys.stderr)
        else:
            print("[dopeIPTV] Embedded playback: enabled", file=sys.stderr)
    store = PlaylistStore(settings)

    client = None
    while client is None:
        pl = store.active()
        if pl is None:
            dlg = LoginDialog(settings)
            if not dlg.exec():
                return 0
            server, user, pw = dlg.values()
            name = server.split("//")[-1].split("/")[0] or "My playlist"
            pl = store.add({"name": name, "server": server, "username": user,
                            "password": pw, "epg_url": "", "refresh": "never"})
            store.set_active(pl["id"])

        candidate = XtreamClient(pl["server"], pl["username"], pl["password"])
        offline = False
        try:
            candidate.authenticate()
            client = candidate
            settings.setValue("server", pl["server"])
            settings.setValue("username", pl["username"])
            settings.setValue("password", pl["password"])
        except Exception as e:
            box = QMessageBox(QMessageBox.Icon.Warning, "Connection failed",
                              f"{pl['name']}: {e}\n\n"
                              "You can start anyway - content will load once "
                              "the server is reachable again (retry by "
                              "switching category, or manage playlists in "
                              "Settings) - or fix the playlist details now.",
                              parent=None)
            start_btn = box.addButton("Start anyway",
                                      QMessageBox.ButtonRole.AcceptRole)
            edit_btn = box.addButton("Edit playlist...",
                                     QMessageBox.ButtonRole.ActionRole)
            quit_btn = box.addButton("Quit",
                                     QMessageBox.ButtonRole.RejectRole)
            box.setDefaultButton(start_btn)
            box.exec()
            clicked = box.clickedButton()
            if clicked is quit_btn:
                return 0
            if clicked is edit_btn:
                dlg = PlaylistDialog(None, pl)
                if dlg.exec():
                    store.update(pl["id"], **dlg.values())
                continue
            client = candidate
            offline = True

    w = MainWindow(client, settings, store)
    if offline:
        w.setWindowTitle(w.windowTitle() + "  (offline)")
    w.show()
    return app.exec()
