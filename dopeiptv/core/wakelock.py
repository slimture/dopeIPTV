"""Screen/suspend inhibitor for playback sessions."""

from __future__ import annotations

import sys

from .. import APP_NAME


class WakeLock:
    """Keeps the screen and system awake while video plays.

    Linux: DBus inhibitors (org.freedesktop.ScreenSaver + PowerManagement).
    macOS: a caffeinate child process (via platform_macos).
    Acquire/release are idempotent.
    """

    DBUS_SERVICES = (
        ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver",
         "org.freedesktop.ScreenSaver"),
        ("org.freedesktop.PowerManagement.Inhibit",
         "/org/freedesktop/PowerManagement/Inhibit",
         "org.freedesktop.PowerManagement.Inhibit"),
    )

    def __init__(self) -> None:
        self._cookies: list[tuple] = []
        # A platform-native lock (macOS caffeinate / Windows
        # SetThreadExecutionState); when set it fully replaces the D-Bus path.
        self._native = None
        if sys.platform == "darwin":
            from .platform_macos import WakeLockMacOS
            self._native = WakeLockMacOS()
        elif sys.platform == "win32":
            from .platform_windows import WakeLockWindows
            self._native = WakeLockWindows()

    @property
    def held(self) -> bool:
        if self._native is not None:
            return self._native.held
        return bool(self._cookies)

    def acquire(self, reason: str = "Playing video") -> None:
        if self.held:
            return
        if self._native is not None:
            self._native.acquire(reason)
            return
        try:
            from PyQt6.QtDBus import QDBusConnection, QDBusInterface
        except Exception:
            return
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return
        for svc, path, iface_name in self.DBUS_SERVICES:
            iface = QDBusInterface(svc, path, iface_name, bus)
            if not iface.isValid():
                continue
            reply = iface.call("Inhibit", APP_NAME, reason)
            args = reply.arguments()
            if args and isinstance(args[0], int):
                self._cookies.append((iface, args[0]))

    def release(self) -> None:
        if self._native is not None:
            self._native.release()
            return
        for iface, cookie in self._cookies:
            try:
                iface.call("UnInhibit", cookie)
            except Exception:
                pass
        self._cookies = []
