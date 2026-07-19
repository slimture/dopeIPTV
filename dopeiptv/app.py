"""Application entry point: icon generation, desktop integration, main()."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMessageBox, QProxyStyle, QPushButton, QStyle,
)


class _NoButtonIconsStyle(QProxyStyle):
    """Clean, text-only dialog buttons everywhere. Two platform-theme defaults
    are overridden: the icons Qt puts on OK / Cancel / Save buttons (a green
    tick, a red cross, ...), and the mnemonic underline drawn under a button
    letter (the 'O' in OK, the 'C' in Cancel). The underline override is scoped
    to push buttons, so menus keep their accelerator underlines; everything else
    defers to the real platform style, so nothing else changes on any OS."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons:
            return 0
        if (hint == QStyle.StyleHint.SH_UnderlineShortcut
                and isinstance(widget, QPushButton)):
            return 0
        return super().styleHint(hint, option, widget, returnData)

from . import APP_NAME, BUILD_VERSION, ORG
from .core.log import configure_logging, log
from .providers.client import OfflineClient, make_client
from .ui.dialogs import PlaylistDialog
from .ui.main_window import MainWindow
from .media.players import _libmpv, _libmpv_error, embedded_playback_reason

_SUPPRESSED_QT_WARNINGS = (
    b"Failed to register with host portal",
    b"Got leave event for surface",
)
_original_msg_handler = None


def _qt_message_filter(msg_type, context, message):
    msg_bytes = message.encode("utf-8", "replace") if isinstance(message, str) else message
    for pattern in _SUPPRESSED_QT_WARNINGS:
        if pattern in msg_bytes:
            return
    if _original_msg_handler is not None:
        _original_msg_handler(msg_type, context, message)


from .core.stores import PlaylistStore
from .ui.theme import ACCENT, apply_theme, build_style


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


def _install_crash_hooks() -> None:
    """Print genuinely-uncaught Python exceptions to stderr with an
    explicit flush. Newer PyQt6 turns unhandled slot exceptions into
    qFatal -> SIGABRT before Python's default excepthook manages to
    flush its buffered stderr traceback, so we lose the diagnostic
    just when we most need it. Routine network / DNS failures are
    filtered out - the app handles them and there's no signal in
    printing them."""
    import faulthandler
    import io
    import threading
    import traceback

    _QUIET_EXC_NAMES = {
        "requests.exceptions.ConnectionError",
        "requests.exceptions.ConnectTimeout",
        "requests.exceptions.ReadTimeout",
        "requests.exceptions.SSLError",
        "urllib3.exceptions.MaxRetryError",
        "urllib3.exceptions.NewConnectionError",
        "urllib3.exceptions.NameResolutionError",
        "urllib3.exceptions.ConnectTimeoutError",
        "socket.gaierror",
        "socket.timeout",
        "OSError",
        "ConnectionResetError",
    }

    def _is_quiet(exc_type) -> bool:
        try:
            name = f"{exc_type.__module__}.{exc_type.__name__}"
        except Exception:
            return False
        return name in _QUIET_EXC_NAMES

    def _hook(exc_type, exc_value, exc_tb):
        if _is_quiet(exc_type):
            return
        buf = io.StringIO()
        buf.write("\n[dopeIPTV] CRASH: uncaught exception\n")
        traceback.print_exception(exc_type, exc_value, exc_tb, file=buf)
        try:
            sys.stderr.write(buf.getvalue())
            sys.stderr.flush()
        except Exception:
            pass

    def _thread_hook(args):
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    def _unraisable_hook(args):
        # PyQt on newer Python routes exceptions from queued slot
        # delivery through sys.unraisablehook. Same filter applies.
        _hook(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    sys.unraisablehook = _unraisable_hook
    # C-level crashes (segfault before qFatal, mpv, GL) - dump a
    # native backtrace to stderr.
    #
    # NOT on Windows: libmpv/ffmpeg raise benign first-chance SEH exceptions
    # (code 0xe24c4a02) as part of normal playback, and faulthandler's
    # all-threads stack walk in response to each one itself access-violates,
    # turning harmless exceptions into a hard crash the moment you play a
    # stream. Linux/macOS never raise those SEH exceptions, so faulthandler is
    # kept there unchanged.
    if sys.platform != "win32":
        try:
            faulthandler.enable(file=sys.stderr, all_threads=True)
        except Exception:
            pass


def main() -> int:
    """Launch the application."""
    # PyInstaller's windowed (no-console) build - our Windows release - leaves
    # sys.stdout (and sometimes sys.stderr) as None. Any print()/flush() then
    # crashes with "'NoneType' object has no attribute 'write'/'flush'" - which
    # is exactly what bit the shutdown flush below, and would bite every stray
    # debug print too. Give them a throwaway sink so all output is harmless.
    # No-op where a real console exists (Linux/macOS, or the dev console build).
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")
    configure_logging()
    # One unconditional startup line so packaging smoke tests can prove
    # Python + our package imported cleanly before any GL/Qt init runs.
    log.info("%s starting", BUILD_VERSION)

    if "--self-check" in sys.argv:
        # GUI-less packaging check: did the *bundled* libmpv load? This runs
        # the frozen bundle without creating a QApplication, which aborts
        # under headless CI (no GPU) long before the app would otherwise
        # report whether libmpv is present. It lets CI prove the embedded
        # player is wired up on a machine with no system mpv - the exact case
        # that used to slip through because the build host had mpv installed.
        if _libmpv is None:
            reason = _libmpv_error
        else:
            reason = embedded_playback_reason()
        if reason:
            log.error("self-check: embedded playback DISABLED: %s", reason)
            return 1
        log.info("self-check: embedded playback OK (bundled libmpv loaded)")
        return 0

    _install_crash_hooks()
    global _original_msg_handler
    from PyQt6.QtCore import qInstallMessageHandler
    _original_msg_handler = qInstallMessageHandler(_qt_message_filter)

    if _libmpv is None:
        log.warning("Embedded playback disabled: %s", _libmpv_error)

    if _libmpv is not None:
        os.environ.setdefault("LC_NUMERIC", "C")

    if sys.platform == "darwin":
        from .core.platform_macos import fix_app_name
        fix_app_name(APP_NAME)
        if _libmpv is not None:
            from .core.platform_macos import setup_opengl
            setup_opengl()
    elif sys.platform == "win32" and _libmpv is not None:
        from .core.platform_windows import setup_opengl
        setup_opengl()

    # Opt-in X11/XWayland backend (Settings > "Run via X11"). Must be set
    # before QApplication. Guarded so it can never wedge startup: Linux only,
    # never overrides a user-set QT_QPA_PLATFORM, and `--no-x11` bypasses it
    # if XWayland is ever unavailable and the app won't open.
    if (sys.platform.startswith("linux")
            and "--no-x11" not in sys.argv
            and not os.environ.get("QT_QPA_PLATFORM")):
        try:
            if QSettings(ORG, ORG).value("force_x11", "false") == "true":
                os.environ["QT_QPA_PLATFORM"] = "xcb"
        except Exception:
            pass

    app = QApplication(sys.argv)
    # Base the proxy on Fusion, not the native platform style: the app is
    # fully QSS-themed anyway, and the native macOS style ignores QSS
    # subcontrol arrows (spin-box steppers, combo-box ▼), leaving bare boxes.
    # Fusion renders the stylesheet faithfully on every OS.
    from PyQt6.QtWidgets import QStyleFactory
    _base = QStyleFactory.create("Fusion")
    app.setStyle(_NoButtonIconsStyle(_base if _base is not None
                                     else app.style()))
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG)
    app.setApplicationDisplayName(APP_NAME)
    app.setDesktopFileName("dopeiptv")
    icon = make_app_icon()
    app.setWindowIcon(icon)
    install_icon(icon)
    settings = QSettings(ORG, ORG)
    from .i18n import set_language
    set_language(settings.value("language", "en"))
    apply_theme(settings)
    app.setStyleSheet(build_style())
    log.info("Qt platform: %s", app.platformName())
    if _libmpv is not None:
        reason = embedded_playback_reason()
        if reason:
            log.warning("Embedded playback disabled: %s", reason)
        else:
            log.info("Embedded playback: enabled")
    store = PlaylistStore(settings)

    client = None
    welcome = False
    offline = False
    while client is None:
        pl = store.active()
        if pl is None:
            # No provider configured yet (first run). Don't gate the app
            # behind a modal login - open the window in "explore" mode with a
            # do-nothing client and let the in-window welcome screen offer to
            # connect a provider or just look around.
            client = OfflineClient()
            welcome = True
            break

        candidate = make_client(pl)
        offline = False
        splash = QLabel(f"  Connecting to {pl.get('name', 'server')}…",
                        None, Qt.WindowType.SplashScreen)
        splash.setStyleSheet(
            "background:#17171C; color:#C9C9D2; font-size:14px;"
            "padding:18px 28px; border-radius:10px;")
        splash.adjustSize()
        splash.show()
        app.processEvents()

        import threading
        auth_err = [None]

        def _do_auth():
            # B023 false positive: the thread is join()ed within this same
            # iteration below, so the closure reads the current candidate /
            # auth_err, never a later loop value.
            try:
                candidate.authenticate()  # noqa: B023
            except Exception as exc:
                auth_err[0] = exc  # noqa: B023

        t = threading.Thread(target=_do_auth, daemon=True)
        t.start()
        # Bound the splash: a down/overloaded provider used to pin
        # "Connecting to ..." for the full network timeout before the window
        # appeared. After 8 s, open the window anyway - the auth thread keeps
        # running in the background, lists load when the server answers (or
        # come from the on-disk cache), and any real credential problem
        # surfaces in-window exactly like the "start anyway" path.
        import time as _time
        _t0 = _time.monotonic()
        while t.is_alive():
            app.processEvents()
            t.join(0.05)
            if _time.monotonic() - _t0 > 8:
                log.warning("auth still pending after 8 s - opening the "
                            "window without waiting")
                break

        if t.is_alive() or auth_err[0] is None:
            client = candidate
            settings.setValue("server", pl["server"])
            settings.setValue("username", pl["username"])
            settings.setValue("password", pl["password"])
            splash.close()
        else:
            e = auth_err[0]
            splash.close()
            box = QMessageBox(QMessageBox.Icon.Warning, "Connection failed",
                              f"{pl['name']}: {e}\n\n"
                              "You can start anyway - content will load once "
                              "the server is reachable again (retry by "
                              "switching category, or manage playlists in "
                              "Settings) - or fix the playlist details now.",
                              parent=None)
            start_btn = box.addButton("Start anyway",
                                      QMessageBox.ButtonRole.AcceptRole)
            edit_btn = box.addButton("Edit playlist…",
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
    if welcome:
        w.show_welcome()
    for _ in range(5):
        app.processEvents()

    rc = app.exec()
    # After the event loop returns, MainWindow.closeEvent has already torn down
    # mpv, recording, casting and flushed all persistence. Letting the Python
    # interpreter then garbage-collect the Qt object tree (notably the libmpv
    # QOpenGLWidget) can segfault on shutdown - the C++ objects get freed in an
    # order sip/PyQt don't guarantee, especially on newer Pythons. Everything
    # important is already released, so exit hard and skip that GC teardown.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(rc if isinstance(rc, int) else 0)
