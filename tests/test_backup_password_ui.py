"""A backup's password in the window (ui/backup_dialogs.py, Settings >
General > Backup; the format is tests/test_backup_lock.py's).

- the password asked for an encrypted backup, opened on a thread of its
  own: a wrong one says so and asks again, Cancel leaves all as it was;
- what opens goes on to the restore window as any backup does;
- work off the window's thread while a small window says so - the
  window's thread goes on meanwhile;
- the Settings card: a password and its repeat, the two must match, an
  encrypted file written and the fields emptied; empty, the file as ever.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import pytest

from dopeiptv.core import backup
from dopeiptv.i18n import tr

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASSWORD = "lång lösen-fras"
FEW = backup.MIN_ROUNDS


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _doc() -> dict:
    return {backup.MAGIC: 1, "app_version": "1.6.9", "created": "",
            "stores": {"main": {"theme": "nord", "playlists": json.dumps(
                [{"id": "a", "name": "A", "kind": "m3u", "server": "http://a.invalid/l.m3u"}])}}}


def _locked(tmp_path, inner: bytes | None = None) -> str:
    path = str(tmp_path / "locked.json")
    doc = backup.lock(_doc(), PASSWORD, FEW) if inner is None else backup.seal(inner, PASSWORD, FEW)
    backup.write(path, doc)
    return path


def _until(qapp, cond, secs: float = 20.0) -> bool:
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)
    return cond()


def _tries(qapp, *passwords):
    """An exec for UnlockBackupDialog: each password typed and sent in
    turn, the answer waited for; its result."""
    def exec_(dlg):
        for pw in passwords:
            if pw is None:
                dlg.reject()
                break
            dlg.field.setText(pw)
            assert dlg.go.isEnabled()
            dlg.go.click()
            assert dlg.field.text() == "" and not dlg.field.isEnabled()
            assert _until(qapp, lambda: not dlg.working())
            if dlg.result():
                break
        return dlg.result()
    return exec_


def test_a_wrong_password_says_so_and_asks_again(qapp, tmp_path):
    from dopeiptv.ui.backup_dialogs import UnlockBackupDialog
    dlg = UnlockBackupDialog(None, backup.read_any(_locked(tmp_path)))
    assert not dlg.go.isEnabled() and not dlg.wrong.isVisibleTo(dlg)
    dlg.field.setText("wrong")
    dlg.go.click()
    assert dlg.working() and dlg.busy.isVisibleTo(dlg) and not dlg.go.isEnabled()
    assert _until(qapp, lambda: not dlg.working())
    assert dlg.wrong.isVisibleTo(dlg) and dlg.wrong.text() == tr("restore_pw_wrong")
    assert dlg.field.isEnabled() and dlg.field.text() == "" and dlg.backup is None
    assert not dlg.result() and dlg.refused is None
    dlg.field.setText(PASSWORD)
    dlg.go.click()
    assert _until(qapp, lambda: not dlg.working())
    assert dlg.result() and dlg.backup.stores["main"]["theme"] == "nord"


def test_cancel_while_it_opens_leaves_everything_as_it_was(qapp, tmp_path):
    from dopeiptv.ui.backup_dialogs import UnlockBackupDialog
    dlg = UnlockBackupDialog(None, backup.read_any(_locked(tmp_path)))
    dlg.field.setText(PASSWORD)
    dlg.go.click()
    dlg.reject()                                   # at once: the key is still being made
    assert not dlg.result() and not dlg.working()
    for t in threading.enumerate():
        if t.name == "dopeiptv-restore":
            t.join(20)
    qapp.processEvents()
    assert dlg.backup is None and dlg.refused is None


def test_pick_backup_opens_an_encrypted_one_and_hands_on_what_it_holds(qapp, tmp_path,
                                                                        monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    from dopeiptv.ui import backup_dialogs
    path = _locked(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (path, "")))
    monkeypatch.setattr(backup_dialogs.UnlockBackupDialog, "exec",
                        _tries(qapp, "not it", PASSWORD))
    b, why = backup_dialogs.pick_backup(None)
    assert why == "" and b.full and b.stores["main"]["theme"] == "nord"
    assert [p["id"] for p in b.sources] == ["a"]
    # Cancelled: nothing, and nothing said.
    monkeypatch.setattr(backup_dialogs.UnlockBackupDialog, "exec", _tries(qapp, "nope", None))
    assert backup_dialogs.pick_backup(None) == (None, "")
    # A plain backup is never asked for a password.
    plain = str(tmp_path / "plain.json")
    backup.write(plain, _doc())
    path = plain
    monkeypatch.setattr(backup_dialogs.UnlockBackupDialog, "exec",
                        lambda _d: pytest.fail("a password asked of a plain backup"))
    b, why = backup_dialogs.pick_backup(None)
    assert why == "" and b.stores["main"]["theme"] == "nord"


def test_what_the_password_opens_must_be_a_backup(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    from dopeiptv.ui import backup_dialogs
    for inner, said in ((b'{"something": "else"}', tr("restore_invalid")),
                        (backup.encode({backup.MAGIC: backup.FORMAT + 1, "app_version": "9.1",
                                        "stores": {}}), tr("restore_newer", version="9.1"))):
        path = _locked(tmp_path, inner)
        monkeypatch.setattr(QFileDialog, "getOpenFileName",
                            staticmethod(lambda *a, p=path, **k: (p, "")))
        monkeypatch.setattr(backup_dialogs.UnlockBackupDialog, "exec", _tries(qapp, PASSWORD))
        assert backup_dialogs.pick_backup(None) == (None, said)
    # A damaged header: said before a password is asked.
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["rounds"] = 1
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    monkeypatch.setattr(backup_dialogs.UnlockBackupDialog, "exec",
                        lambda _d: pytest.fail("a password asked of a damaged file"))
    assert backup_dialogs.pick_backup(None) == (None, tr("restore_invalid"))


def test_work_off_the_windows_thread_lets_the_window_go_on(qapp):
    from PyQt6.QtCore import QTimer

    from dopeiptv.ui.backup_dialogs import run_working
    ticks, where = [], []
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()

    def work():
        where.append(threading.current_thread() is threading.main_thread())
        time.sleep(0.4)
        return 42

    assert run_working(None, "t", "working", work) == 42
    timer.stop()
    assert where == [False] and len(ticks) >= 5         # the window's thread went on

    def fails():
        raise OSError(28, "No space left on device")

    with pytest.raises(OSError):
        run_working(None, "t", "working", fails)


# -- the Settings card, in a window in a child process --------------------------------

_CARD = r"""
import json, os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QLineEdit, QMessageBox, QPushButton
app = QApplication.instance() or QApplication([])
base = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, base)
from dopeiptv.core import backup
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import dialogs
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell

PW = "mitt hemliga lösen"
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "a", "name": "A", "kind": "xtream",
           "server": "http://a.invalid", "username": "u", "password": "pl-secret"}]))
s.setValue("active_playlist", "a")
s.setValue("theme", "nord")
store = PlaylistStore(s)
w = MainWindow(DemoClient(), s, store)
out = os.path.join(base, "backup.json")
saved = []
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: saved.append(a[2]) or (out, ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (out, ""))
said = []
QMessageBox.information = staticmethod(
    lambda parent, title, text, *a, **k: said.append(text) or QMessageBox.StandardButton.Ok)
QMessageBox.warning = staticmethod(
    lambda parent, title, text, *a, **k: said.append("WARN " + text) or QMessageBox.StandardButton.Ok)
shown = []

def restore_exec(dlg):
    shown.append(dlg)
    dlg.add.setChecked(True)
    return 1

def unlock_exec(dlg):
    for pw in ("wrong", PW):
        dlg.field.setText(pw)
        dlg.go.click()
        end = time.monotonic() + 20
        while dlg.working() and time.monotonic() < end:
            app.processEvents()
        if dlg.result():
            break
        assert dlg.wrong.isVisibleTo(dlg)
    return dlg.result()

dialogs.RestoreBackupDialog.exec = restore_exec
dialogs.UnlockBackupDialog.exec = unlock_exec
asked_plain = []
type(w)._backup_without_password_ok = lambda self, parent: asked_plain.append(1) or True

def run(self):
    shell = self.findChild(SettingsShell)
    shell.build_all()
    general = next(p for p in shell.pages if p.key == "general")
    fields = {f.accessibleName(): f for f in general.findChildren(QLineEdit)}
    pw, again = fields[tr("set_backup_pw")], fields[tr("set_backup_pw_again")]
    assert pw.echoMode() == QLineEdit.EchoMode.Password == again.echoMode()
    assert pw.isEnabled() and not again.isEnabled()          # the repeat only for a password
    back = next(b for b in general.findChildren(QPushButton) if b.text() == tr("backup_btn"))
    restore = next(b for b in general.findChildren(QPushButton) if b.text() == tr("restore_btn"))
    # Two that differ: said, nothing written.
    pw.setText(PW)
    assert again.isEnabled()
    again.setText(PW + "!")
    back.click()
    assert said[-1] == "WARN " + tr("backup_pw_mismatch") and not os.path.exists(out)
    assert not saved                                         # not even asked where
    # Too short: said, nothing written.
    pw.setText("short")
    again.setText("short")
    back.click()
    assert said[-1] == "WARN " + tr("backup_pw_short", n=backup.PASSWORD_MIN), said[-1]
    assert not os.path.exists(out) and not saved
    pw.setText(PW)
    # The same: written encrypted, the fields emptied.
    again.setText(PW)
    back.click()
    raw = open(out, "rb").read()
    doc = json.loads(raw)
    assert doc["dopeiptv_backup"] == "encrypted" and doc["rounds"] == backup.ROUNDS
    assert b"pl-secret" not in raw and b"nord" not in raw and PW.encode() not in raw
    assert saved[-1].endswith(".json")                        # the same name as ever
    assert pw.text() == "" and again.text() == "" and not again.isEnabled()
    assert said[-1].startswith(tr("backup_done_locked", what="", path="").split("\n")[0]), said[-1]
    assert asked_plain == [], "a backup with a password is not asked about"
    # Restored: the password asked (a wrong one first), what it holds shown.
    restore.click()
    assert shown and tr("backup_count", what=tr("backup_kind_sources"), n=1) in shown[-1].holds.text()
    assert said[-1] == tr("pl_import_done", n=0), said[-1]
    # Without a password: the file without its logins.
    os.remove(out)
    back.click()
    assert asked_plain == [1], "made only once the person said so"
    raw = open(out, "rb").read()
    doc = json.loads(raw)
    assert doc["dopeiptv_backup"] == 1 and doc["logins"] == "left out"
    assert json.loads(doc["stores"]["main"]["playlists"])[0]["password"] == ""
    assert b"pl-secret" not in raw
    assert said[-1].startswith(tr("backup_done", what="", path="").split("\n")[0])
    return 0

QDialog.exec = run
w.open_settings()
print("CARD_OK")
"""


def test_the_settings_card_makes_and_restores_an_encrypted_backup():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _CARD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "CARD_OK" in out.stdout, out.stdout + out.stderr[-3000:]
    assert PASSWORD not in out.stderr and "mitt hemliga" not in out.stderr
