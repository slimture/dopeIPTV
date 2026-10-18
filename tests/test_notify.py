"""Desktop notifications: the Notify call a notification daemon receives.

Runs against a private session bus (dbus-run-session) in a subprocess, with
a stand-in daemon in the same process answering org.freedesktop.Notifications.
"""
import os
import shutil
import subprocess
import sys
import textwrap

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = textwrap.dedent(r"""
    import os
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtCore import (QObject, QTimer, pyqtClassInfo, pyqtSlot)
    from PyQt6.QtDBus import QDBusAbstractAdaptor, QDBusConnection
    from PyQt6.QtWidgets import QApplication
    from dopeiptv.services import notify

    app = QApplication([])
    got = []

    @pyqtClassInfo("D-Bus Interface", "org.freedesktop.Notifications")
    class Daemon(QDBusAbstractAdaptor):
        @pyqtSlot(str, "uint", str, str, str, "QStringList", "QVariantMap",
                  int, result="uint")
        def Notify(self, app_name, replaces, icon, summary, body, actions,
                   hints, timeout):
            got.append((app_name, replaces, icon, summary, body, actions,
                        dict(hints), timeout))
            return 7

    owner = QObject()
    Daemon(owner)
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        raise SystemExit("could not join the session bus")
    assert bus.registerObject(notify.OBJECT_PATH, owner,
                              QDBusConnection.RegisterOption.ExportAdaptors)
    assert bus.registerService(notify.SERVICE)

    assert notify.notify("New episodes", "New episode of Show: S1 E3",
                         "file:///tmp/cover.jpg")

    def finish():
        print("GOT", got)
        app.quit()

    QTimer.singleShot(1500, finish)
    app.exec()
""")


@pytest.mark.skipif(not sys.platform.startswith("linux")
                    or not shutil.which("dbus-run-session"),
                    reason="needs dbus-run-session")
def test_the_daemon_gets_the_notification():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, "-c", _CHILD],
        capture_output=True, text=True, env=env, cwd=_REPO_ROOT, timeout=120)
    if "could not join the session bus" in proc.stderr:
        pytest.skip("no session bus could be started here")
    out = proc.stdout
    assert "GOT [('dopeIPTV', 0, 'dopeiptv', 'New episodes', " in out, (
        out + proc.stderr)
    assert "'New episode of Show: S1 E3', []" in out
    assert "'desktop-entry': 'dopeiptv'" in out
    assert "'image-path': 'file:///tmp/cover.jpg'" in out
    assert out.rstrip().endswith("10000)]"), out


def test_nothing_is_sent_off_linux(monkeypatch):
    pytest.importorskip("PyQt6")
    from dopeiptv.services import notify
    monkeypatch.setattr(notify.sys, "platform", "win32")
    assert notify.notify("x") is False
