from __future__ import annotations

from PyQt6.QtWidgets import QFileDialog

from dopeiptv.ui import widgets


def test_the_systems_own_dialog_on_macos_and_windows(monkeypatch):
    for plat in ("darwin", "win32"):
        monkeypatch.setattr(widgets.sys, "platform", plat)
        opts = widgets.file_dialog_options(QFileDialog.Option.ShowDirsOnly)
        assert not opts & QFileDialog.Option.DontUseNativeDialog
        assert opts & QFileDialog.Option.ShowDirsOnly


def test_qts_own_dialog_on_linux(monkeypatch):
    monkeypatch.setattr(widgets.sys, "platform", "linux")
    assert widgets.file_dialog_options() & QFileDialog.Option.DontUseNativeDialog
