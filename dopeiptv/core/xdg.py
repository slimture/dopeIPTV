"""Talking to the rest of the desktop from inside a frozen bundle.

One problem, two symptoms. PyInstaller and AppImage prepend OUR bundled
libraries to the loader and plugin paths, and every child process inherits
them - so a system program we launch loads our Qt, our libmpv, our ffmpeg
instead of its own and dies before it draws anything. That is why external
mpv "did not work" from the AppImage, and it is why a link in the About
window did nothing: xdg-open reached the browser, and the browser fell
over on our libstdc++.

``system_env`` is the cure, and ``open_url`` is it applied to links.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .log import log

# Variables a bundle sets that a system binary must not inherit.
# PyInstaller stashes the pre-launch value of each as <VAR>_ORIG.
_BUNDLE_VARS = (
    "LD_LIBRARY_PATH", "LD_PRELOAD", "QT_PLUGIN_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH",
    "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_PATH", "GTK_PATH",
    "GDK_PIXBUF_MODULE_FILE", "FONTCONFIG_FILE", "FONTCONFIG_PATH",
    "PYTHONHOME", "PYTHONPATH",
)


def system_env() -> dict:
    """Environment for spawning a SYSTEM binary from inside a frozen
    bundle: the pre-launch value where PyInstaller stashed one, and the
    variable dropped entirely where it did not.

    Only when actually frozen. These variables belong to us in a
    PyInstaller build and nowhere else - under Flatpak the runtime sets
    LD_LIBRARY_PATH to /app/lib for its own reasons, and stripping it from
    a child would break the very thing we are trying to launch. Stripping
    unconditionally was a no-op on a plain source run and wrong in a
    sandbox, which is a bad trade for a guard that costs one attribute
    lookup."""
    env = dict(os.environ)
    if not getattr(sys, "frozen", False):
        return env
    for var in _BUNDLE_VARS:
        orig = env.get(var + "_ORIG")
        if orig:
            env[var] = orig
        else:
            env.pop(var, None)
    return env


def _qt_platform() -> str:
    try:
        from PyQt6.QtGui import QGuiApplication
        return QGuiApplication.platformName() if QGuiApplication.instance() else ""
    except Exception:   # noqa: BLE001 - no Qt, no Qt opener
        return ""


def _qt_open(url: str) -> bool:
    from PyQt6.QtCore import QUrl
    from PyQt6.QtGui import QDesktopServices
    return bool(QDesktopServices.openUrl(QUrl(url)))


def _swap_to_system_env() -> dict:
    """In a frozen bundle, put the system's values of our library-path
    variables into this process's environment; returns what to put back
    ({} when not frozen: nothing changes)."""
    if not getattr(sys, "frozen", False):
        return {}
    saved = {var: os.environ.get(var) for var in _BUNDLE_VARS}
    clean = system_env()
    for var in _BUNDLE_VARS:
        if var in clean:
            os.environ[var] = clean[var]
        else:
            os.environ.pop(var, None)
    return saved


def _restore_env(saved: dict) -> None:
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


# How long a link chosen from a menu waits for the window to have the focus
# back, and how often it looks (see _open_with_qt).
FOCUS_WAIT_MS = 500
_FOCUS_STEP_MS = 25


def _later(ms: int, fn) -> None:
    from PyQt6.QtCore import QTimer
    QTimer.singleShot(ms, fn)


def _focus_settled() -> bool:
    """Whether a window of ours other than a menu's popup has the focus."""
    try:
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QGuiApplication
        if QGuiApplication.instance() is None:
            return True
        w = QGuiApplication.focusWindow()
        return w is not None and w.type() != Qt.WindowType.Popup
    except Exception:   # noqa: BLE001 - no Qt to wait for
        return True


def _when_focused(fn, waited: int = 0) -> None:
    if _focus_settled() or waited >= FOCUS_WAIT_MS:
        fn()
        return
    _later(_FOCUS_STEP_MS, lambda: _when_focused(fn, waited + _FOCUS_STEP_MS))


def _open_with_qt(url: str) -> bool:
    """Qt's opener, on a desktop session (Wayland or X11). It asks the
    compositor for an activation token and hands it on (to the desktop
    portal or the handler it starts), and that token is what lets GNOME
    bring the browser to the front: without it the donate page opened
    BEHIND the app, and GNOME's launch spinner turned until it gave up.

    Qt asks for the token on behalf of the window that has the focus. A
    link chosen from a menu ("Support dopeIPTV…" in the app menu, the
    heart's right-click menu) had the menu's popup there - or, as it
    closed, no window at all - and the page opened behind the app all the
    same. On Wayland the link waits, a beat, for the window to have the
    focus back (FOCUS_WAIT_MS at most, then it opens regardless).

    In a frozen bundle a handler Qt starts would inherit our library
    paths, so for the moment of the launch - the token comes back a beat
    later - the process has the system's, and gets ours back after."""
    platform = _qt_platform()
    if platform not in ("wayland", "xcb"):
        return False
    if platform == "wayland" and not _focus_settled():
        def go() -> None:
            if not _open_with_qt_now(url):
                _open_with_handlers(url)
        _when_focused(go)
        return True
    return _open_with_qt_now(url)


def _open_with_qt_now(url: str) -> bool:
    saved = _swap_to_system_env()
    ok = False
    try:
        ok = _qt_open(url)
    except Exception as e:  # noqa: BLE001 - the handler below is the fallback
        log.info("open_url: Qt could not open the link (%s) - trying xdg-open", e)
    finally:
        if saved and ok:
            try:
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(3000, lambda: _restore_env(saved))
            except Exception:   # noqa: BLE001
                _restore_env(saved)
        elif saved:
            _restore_env(saved)
    return ok


def _open_with_handlers(url: str) -> bool:
    """The desktop's own openers, spawned with the system's library paths."""
    for exe in ("xdg-open", "gio", "gnome-open", "kde-open5", "kde-open"):
        path = shutil.which(exe)
        if not path:
            continue
        cmd = [path, "open", url] if exe == "gio" else [path, url]
        try:
            subprocess.Popen(
                cmd, env=system_env(), start_new_session=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError as e:
            log.info("open_url: %s failed (%s) - trying the next handler",
                     exe, e)
    return False


def open_url(url: str) -> bool:
    """Open *url* in the user's browser. Returns whether a handler was
    started.

    On a Linux desktop Qt's opener goes first (``_open_with_qt``): it
    passes the activation token that brings the browser to the front. If
    it cannot, the handler is spawned directly with a cleaned
    environment, because a child of this process inherits the bundle's
    library paths and the browser fails to start - which from the user's
    side looks exactly like a link that does nothing. macOS and Windows
    use Qt's call, where there is no such problem.
    """
    if not url:
        return False
    if sys.platform.startswith("linux") and _open_with_qt(url):
        return True
    if sys.platform.startswith("linux") and _open_with_handlers(url):
        return True
    try:
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        return bool(QDesktopServices.openUrl(QUrl(url)))
    except Exception as e:
        # Redacted: the Trakt authorisation URL carries a one-time OAuth
        # state token, and this line is written when something has gone
        # wrong - exactly when the log gets shared.
        from .log import redact_url
        log.warning("open_url: could not open %s: %s", redact_url(url), e)
        return False
