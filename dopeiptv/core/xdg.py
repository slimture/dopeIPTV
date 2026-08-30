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


def open_url(url: str) -> bool:
    """Open *url* in the user's browser. Returns whether a handler was
    started.

    On Linux the handler is spawned directly with a cleaned environment,
    because QDesktopServices.openUrl runs xdg-open as a CHILD of this
    process - so the browser inherits the bundle's library paths and
    fails to start, which from the user's side looks exactly like a link
    that does nothing. Qt's own call is kept as the fallback and is what
    macOS and Windows use, where there is no such problem.
    """
    if not url:
        return False
    if sys.platform.startswith("linux"):
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
