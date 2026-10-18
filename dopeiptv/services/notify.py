"""Desktop notifications (org.freedesktop.Notifications), Linux only.

Sent and forgotten: no reply is waited for, so a desktop without a
notification daemon costs one message on the bus and nothing else.
"""
from __future__ import annotations

import sys
from typing import Any

from PyQt6.QtCore import QMetaType

from ..core.log import log

SERVICE = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
# The window's app_id and icon name (core/desktop_entry.py): the desktop
# files the notification under the app and draws its icon.
DESKTOP_ENTRY = "dopeiptv"
EXPIRE_MS = 10000


def _uint32(value: int) -> Any:
    from PyQt6.QtDBus import QDBusArgument
    arg = QDBusArgument()
    arg.add(int(value), QMetaType.Type.UInt.value)
    return arg


def _strings(values: list[str]) -> Any:
    from PyQt6.QtDBus import QDBusArgument
    arg = QDBusArgument()
    arg.add(list(values), QMetaType.Type.QStringList.value)
    return arg


def notify(summary: str, body: str = "", image: str = "") -> bool:
    """Show *summary* and *body* on the desktop, with *image* (a file:// URL
    or a path) beside them. Returns whether the message went out - not
    whether anything showed it."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        from PyQt6.QtDBus import QDBusConnection, QDBusMessage
    except Exception as e:
        log.debug("notify: QtDBus is not available (%s)", e)
        return False
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return False
    hints: dict[str, Any] = {"desktop-entry": DESKTOP_ENTRY}
    if image:
        hints["image-path"] = image
    msg = QDBusMessage.createMethodCall(SERVICE, OBJECT_PATH, SERVICE,
                                        "Notify")
    msg.setArguments(["dopeIPTV", _uint32(0), DESKTOP_ENTRY, summary, body,
                      _strings([]), hints, EXPIRE_MS])
    ok = bus.send(msg)
    if not ok:
        log.debug("notify: the message was not sent")
    return ok
