"""Application entry point: icon generation, desktop integration, main()."""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QSettings, Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMessageBox, QProxyStyle, QPushButton, QStyle,
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
from .ui.main_window import MainWindow
from .media.players import _libmpv, _libmpv_error, embedded_playback_reason

_SUPPRESSED_QT_WARNINGS = (
    b"Failed to register with host portal",
    b"Got leave event for surface",
    # Wayland, on every focus change while no text field has focus: Qt's
    # text-input-v3 bookkeeping, dozens a minute and nothing the app can act on.
    b"QWaylandTextInputv3::disableSurface",
    # macOS, on drawing the Hindi name in the language list: the system
    # font has no OpenType tables for Devanagari and Qt shapes it without.
    b"OpenType support missing for",
    # Channel logos and posters from providers are often PNGs with a chunk
    # out of place or twice ("eXIf: duplicate", "iCCP: out of place"). They
    # load and show fine; libpng only says so, a line per image.
    b"libpng warning:",
)
_original_msg_handler = None


def _qt_message_filter(msg_type, context, message):
    msg_bytes = message.encode("utf-8", "replace") if isinstance(message, str) else message
    for pattern in _SUPPRESSED_QT_WARNINGS:
        if pattern in msg_bytes:
            return
    if _original_msg_handler is not None:
        _original_msg_handler(msg_type, context, message)
        return
    # PyQt answers None for Qt's own default handler, so there was nothing to
    # hand the message on to and every Qt warning was dropped - including
    # the one line a fatal error prints before Qt aborts, which is how a
    # start-up crash came to say nothing at all ("Fatal Python error:
    # Aborted", and no reason). They go to the log now.
    from PyQt6.QtCore import QtMsgType
    text = (message if isinstance(message, str)
            else bytes(message).decode("utf-8", "replace"))
    if msg_type == QtMsgType.QtDebugMsg:
        log.debug("qt: %s", text)
    elif msg_type == QtMsgType.QtInfoMsg:
        log.info("qt: %s", text)
    elif msg_type == QtMsgType.QtWarningMsg:
        log.warning("qt: %s", text)
    else:
        log.error("qt: %s", text)


from .core.stores import PlaylistStore
from .ui.theme import apply_theme, build_style


# The icon is drawn in ui/app_icon.py (the plain one and the supporter's);
# the names stay importable from here for packaging/gen_icon*.py.
from .ui.app_icon import (  # noqa: F401
    ICON_SAFE_INSET, install_icon, make_app_icon,
)


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
        traceback.print_exception(exc_type, exc_value, exc_tb, file=buf)
        # Through the logger, so the traceback also lands in the log file
        # (DOPEIPTV_LOG_FILE) that gets attached to bug reports, redacted
        # like every other line; its stderr handler flushes each record.
        try:
            from .core import report
            report.notify_error(buf.getvalue())
        except Exception:
            pass
        try:
            log.error("CRASH: uncaught exception\n%s", buf.getvalue().rstrip())
            return
        except Exception:
            pass
        try:
            sys.stderr.write("\n[dopeIPTV] CRASH: uncaught exception\n"
                             + buf.getvalue())
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
        # From a terminal, to it; otherwise to a file in the log folder,
        # which the next start reads and offers to send (Help > reports).
        target = sys.stderr
        try:
            from .core import report
            from .core.log import log_dir
            report.set_pending_crash(report.take_last_crash(log_dir()))
            if not (sys.stderr and sys.stderr.isatty()):
                target = report.open_crash_file(log_dir())
        except Exception:
            target = sys.stderr
        try:
            faulthandler.enable(file=target, all_threads=True)
        except Exception:
            pass


def _watch_link_events(app, window) -> None:
    """macOS hands a dopeiptv:// link to a running app as an event, not on
    the command line."""
    from PyQt6.QtCore import QEvent, QObject

    from .core.commands import parse_link

    class _Links(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 - Qt's name
            if event.type() == QEvent.Type.FileOpen:
                url = event.url().toString() if event.url().isValid() else event.file()
                cmd = parse_link(url) if url.lower().startswith("dopeiptv://") else None
                if cmd is not None:
                    window.handle_command(cmd)
                    return True
            return False
    window._link_filter = _Links(window)
    app.installEventFilter(window._link_filter)


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
        if sys.platform == "win32" and getattr(sys, "frozen", False):
            # libmpv hard-links vulkan-1.dll, which Windows does not ship:
            # graphics drivers do. The build machine has one, so the load
            # above succeeds there whether or not the bundle carries its own
            # copy - which is how 1.2.11 shipped without one and had no
            # in-app video on any PC whose driver predates Vulkan (#19).
            # The bundle dir is first in the DLL search order, so if the
            # loaded copy is not the bundled one, the bundle has none.
            from .core.platform_windows import loaded_module_path
            bundle = getattr(sys, "_MEIPASS", "") or ""
            loaded = loaded_module_path("vulkan-1.dll")

            def _same(a: str, b: str) -> bool:
                return (os.path.normcase(os.path.realpath(a))
                        == os.path.normcase(os.path.realpath(b)))
            if loaded is None:
                # A static import is loaded with the DLL, so not loaded
                # means this libmpv build does not link it at all.
                log.info("self-check: libmpv does not link vulkan-1.dll")
            elif not _same(os.path.dirname(loaded), bundle):
                log.error("self-check: vulkan-1.dll must come from the "
                          "bundle (%s) but was loaded from %s - libmpv "
                          "will not load on a PC without a Vulkan driver",
                          bundle, loaded)
                return 1
            else:
                log.info("self-check: vulkan-1.dll loaded from the bundle "
                         "(%s)", loaded)
        if sys.platform == "darwin" and getattr(sys, "frozen", False):
            # The app updates itself with the .dmg for its own CPU; a build
            # that cannot tell which (a universal2 Python's "universal2"
            # once) offers every Mac the download page instead.
            from .core import macupdate
            if macupdate.arch() is None:
                log.error("self-check: this build cannot tell its CPU, so "
                          "it cannot update itself")
                return 1
            log.info("self-check: updates itself with the %s .dmg",
                     macupdate.arch())
        # Every shipped language must actually load from THIS bundle. The
        # locale JSONs live outside the code archive in frozen builds, so a
        # packaging slip silently collapses the picker to English-only (the
        # 1.0.0 regression) - fail the build loudly instead.
        from .i18n import LANGUAGES, _NATIVE_NAMES
        missing = [c for c in _NATIVE_NAMES if c not in LANGUAGES]
        if missing:
            log.error(
                "self-check: %d language(s) did not load from the bundle: %s",
                len(missing), ", ".join(missing))
            return 1
        log.info("self-check: all %d languages loaded", len(LANGUAGES))
        # The programme guide is parsed by this same executable started as a
        # process of its own (providers/epg.py). A bundle that cannot do that
        # still parses in the app, slowly - the regression this catches.
        from .providers.epg import XmltvGuide
        probe = XmltvGuide(None)
        if not probe._parse_elsewhere(
                b'<tv><channel id="c1"><display-name>One</display-name>'
                b'</channel></tv>') or probe._by_name.get("one") != "c1":
            log.error("self-check: the guide parse process did not run "
                      "from this bundle")
            return 1
        log.info("self-check: the guide parse process works")
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

    # A backup restored over everything in Settings waits for this moment:
    # put in place before anything reads the settings it replaces (the size
    # of the interface below included). Never in the way of starting.
    restored = None
    try:
        from .core import backup
        restored = backup.apply_pending()
    except Exception as e:
        log.warning("restore: not put in place: %s", e)

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

    # Settings -> Appearance -> Size of text and controls: Qt scales the
    # whole interface, so text, icons and rows stay in proportion. Read
    # before QApplication, like the X11 choice above; a QT_SCALE_FACTOR the
    # user set in the environment wins.
    if not os.environ.get("QT_SCALE_FACTOR"):
        try:
            from .ui.theme import ui_scale_factor
            factor = ui_scale_factor(QSettings(ORG, ORG).value("ui_scale", "100"))
            if factor:
                os.environ["QT_SCALE_FACTOR"] = str(factor)
                log.info("interface size %d%%", round(factor * 100))
        except Exception:
            pass

    app = QApplication(sys.argv)
    # A command (--play, --channel, a dopeiptv:// link) goes to the copy
    # already running, if there is one; this start then ends here.
    from .core.commands import parse_command
    command = parse_command(sys.argv[1:])
    if command is not None:
        from .core.instance import send_to_running
        if send_to_running(command):
            return 0
    # Cyclic garbage collection on the GUI thread only: collected on a pool
    # thread, a cycle holding Qt objects segfaults (see core/gcguard.py).
    from .core.gcguard import GcGuard
    GcGuard(app)
    # Every way between Qt and Python - a signal, an event, each list row a
    # delegate paints, many a widget call, every video frame - takes the
    # interpreter lock, and while pool threads were busy in Python (lists,
    # guide, artwork) each one waited out their 5 ms turn: building Home's
    # 18 cards took 9 s instead of 16 ms next to two busy threads, and the
    # window and the player crawled whenever anything loaded. 0.2 ms brings
    # that to 0.5 s. Windows rounds a wait under 1 ms down to none, which
    # would have busy threads trade the lock back and forth all the time.
    sys.setswitchinterval(0.001 if sys.platform == "win32" else 0.0002)
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
    # The window's app_id: "dopeiptv", or the name of the entry this run was
    # started from when that is a copy of ours (see launched_app_id).
    try:
        from .core.desktop_entry import launched_app_id
        app_id = launched_app_id() or "dopeiptv"
    except Exception:
        app_id = "dopeiptv"
    app.setDesktopFileName(app_id)
    if app_id != "dopeiptv":
        log.info("window app_id %s (the entry it was started from)", app_id)
    # Roomier global pixmap cache: the list/grid delegates memoise their
    # smooth-scaled logos and posters here (see ChannelDelegate._scaled_logo),
    # and the default 10 MB evicts them constantly with thousands of channels.
    # 64 MB keeps a large visible working set scaled exactly once.
    from PyQt6.QtGui import QPixmapCache
    QPixmapCache.setCacheLimit(64 * 1024)   # KB
    # The app's icon - the supporter's for a supporter who keeps it on -
    # drawn as this system wants it (ui/app_icon.platform_icon: Apple's and
    # GNOME's margin, a retina Dock's size) and set for every window; on
    # Linux also written into the XDG icon theme, where Wayland takes it
    # from. The window keeps it in step with the key and the switch.
    settings = QSettings(ORG, ORG)
    from .ui import app_icon
    try:
        supporter_icon = app_icon.wanted(settings)
    except Exception as e:          # a thank-you is never worth a failed start
        log.warning("supporter icon: %s", e)
        supporter_icon = False
    app_icon.show(supporter_icon, theme_icons=sys.platform.startswith("linux"))
    from .i18n import is_rtl, set_language
    set_language(settings.value("language", "en"))
    from .ui.qt_i18n import apply_qt_language
    apply_qt_language(app)          # Qt's own buttons and dialogs too
    # Right-to-left languages (Arabic, Persian) mirror the whole UI.
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if is_rtl()
        else Qt.LayoutDirection.LeftToRight)
    apply_theme(settings)
    app.setStyleSheet(build_style())
    log.info("Qt platform: %s", app.platformName())
    if _libmpv is not None:
        # X11 aborts outright on a GL widget without GLX; see probe_opengl.
        from .media.players import probe_opengl
        probe_opengl(app.platformName())
    if _libmpv is not None:
        reason = embedded_playback_reason()
        if reason:
            log.warning("Embedded playback disabled: %s", reason)
        else:
            log.info("Embedded playback: enabled")
    store = PlaylistStore(settings)

    # Open the window IMMEDIATELY - no splash, no blocking authenticate.
    # The client is built from the stored playlist and credentials are
    # verified in the background after the window is up: content loads as
    # soon as the server answers (or from the on-disk list/EPG caches), and
    # a genuine credential rejection surfaces as an in-window warning that
    # points at Settings -> Playlists.
    pl = store.active()
    welcome = pl is None
    if welcome:
        # No provider configured yet (first run): "explore" mode with a
        # do-nothing client; the welcome screen offers to connect one.
        client = OfflineClient()
    else:
        client = make_client(pl)
        settings.setValue("server", pl["server"])
        settings.setValue("username", pl.get("username", ""))
        settings.setValue("password", pl.get("password", ""))

    w = MainWindow(client, settings, store)
    w.show()
    if restored is not None:
        from .i18n import tr
        w._show_toast(tr("restore_done_start") if restored
                      else tr("restore_failed_start"), 8000)
    w._start_command_server()
    w._remote_apply()
    w._events_apply(started=True)
    _watch_link_events(app, w)
    if command is not None:
        # After the window and its source are up: a channel needs its list.
        QTimer.singleShot(1500, lambda: w.handle_command(command))
    if welcome:
        w.show_welcome()
    if "--updated" in sys.argv:
        # Opened by the installer at the end of an update (Windows,
        # packaging/windows/dopeiptv.nsi): it asks for the front itself.
        QTimer.singleShot(0, w._come_forward)
    for _ in range(5):
        app.processEvents()

    if not welcome:
        import threading
        auth_err: list = [None]

        def _do_auth():
            try:
                client.authenticate()
            except Exception as exc:
                auth_err[0] = exc

        _t = threading.Thread(target=_do_auth, daemon=True)
        _t.start()

        def _check_auth() -> None:
            if _t.is_alive():
                QTimer.singleShot(500, _check_auth)
                return
            e = auth_err[0]
            # Only a real credential rejection deserves a dialog; network
            # failures already surface through the list-load error paths and
            # recover by themselves (cooldown + caches).
            if isinstance(e, RuntimeError) and "username or password" in str(e):
                from .i18n import tr
                where = f"{tr('settings_title')} → {tr('tab_playlists')}"
                QMessageBox.warning(
                    w, tr("msg_connection_failed"),
                    f"{pl['name']}: {e}\n\n"
                    + tr("msg_check_playlist_credentials", where=where))

        QTimer.singleShot(500, _check_auth)

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
