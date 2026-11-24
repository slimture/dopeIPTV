"""A question asked and answered leaves nothing behind: its window goes.

Kept, each one stayed a child of the main window for the rest of the
session - a QMessageBox or a QDialog for every Yes/No, every "stop the
recording?", every PIN typed (with the PIN still in its field) - and every
one was restyled at each theme change (docs/PERFORMANCE.md). Each helper
reads what it needs before the window goes.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def _flush_deletes():
    from PyQt6.QtCore import QCoreApplication, QEvent
    from PyQt6.QtWidgets import QApplication
    for _ in range(3):
        QApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)


def test_a_yes_or_no_leaves_no_window(monkeypatch):
    from PyQt6.QtWidgets import QMessageBox, QWidget

    from dopeiptv.ui.widgets import confirm
    answers = iter([QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No] * 3)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: next(answers))
    parent = QWidget()
    got = [confirm(parent, "Delete?", "It goes.") for _ in range(6)]
    assert got == [True, False] * 3
    _flush_deletes()
    assert parent.findChildren(QMessageBox) == []
    parent.deleteLater()


def test_a_choice_leaves_no_window(monkeypatch):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QCheckBox, QDialog, QPushButton, QWidget

    from dopeiptv.ui.mw_recording import _RecordingMixin

    def pick_second(self):
        self.findChildren(QPushButton)[1].click()
        for box in self.findChildren(QCheckBox):
            box.setChecked(True)
        return 1
    monkeypatch.setattr(QDialog, "exec", pick_second)
    host = QWidget()
    host.settings = QSettings("dopeiptv-test", "dialogs-freed")
    host.settings.clear()
    for _ in range(4):
        got = _RecordingMixin._choice_dialog(
            host, "Recording", "Stop it?", [("Keep", "primary"), ("Stop", "danger")],
            dont_ask_setting="perf_test_dont_ask")
        assert got == 1
    assert host.settings.value("perf_test_dont_ask") == "true"   # read before it went
    _flush_deletes()
    assert host.findChildren(QDialog) == []
    host.settings.clear()
    host.deleteLater()


def test_a_pin_asked_leaves_no_window_and_no_pin(monkeypatch):
    from PyQt6.QtWidgets import QDialog, QLineEdit, QWidget

    from dopeiptv.ui import profiles_ui
    fields: list = []

    def type_pin(self):
        field = self.findChild(QLineEdit)
        field.setText(" 4711 ")
        fields.append(field)
        return 1
    monkeypatch.setattr(QDialog, "exec", type_pin)
    parent = QWidget()
    assert profiles_ui.ask_pin(parent, "PIN", "Your PIN") == "4711"
    assert fields[0].text() == ""           # not kept in a hidden field
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    assert profiles_ui.ask_pin(parent, "PIN", "Your PIN") is None
    _flush_deletes()
    assert parent.findChildren(QDialog) == []
    parent.deleteLater()


def test_the_reminders_window_counts_down_only_while_open(monkeypatch):
    """Its 1 s countdown started with the window and was never stopped:
    opened once, it ticked in a closed window for the rest of the session,
    and one more each time it was opened."""
    from types import SimpleNamespace

    from PyQt6.QtWidgets import QDialog, QWidget

    from dopeiptv.ui.mw_reminders import _RemindersMixin
    from dopeiptv.ui.reminders import RemindersDialog
    seen = []

    def run(self):
        self.show()
        seen.append(self._timer.isActive())     # open: it counts down
        self.hide()
        seen.append(self._timer.isActive())     # closed: it does not
        return 0
    monkeypatch.setattr(QDialog, "exec", run)
    host = QWidget()
    host.reminders = SimpleNamespace(all=lambda: [], remove=lambda *a: None)
    for _ in range(3):
        _RemindersMixin._open_reminders(host)
    assert seen == [True, False] * 3
    kept = RemindersDialog(host)                # one made and never shown
    assert not kept._timer.isActive()
    kept.deleteLater()
    _flush_deletes()
    assert host.findChildren(RemindersDialog) == []
    host.deleteLater()
