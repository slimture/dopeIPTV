"""Server mode's Settings card: what the tuner is open to without its token,
in plain words for each choice, and the choice stored as made - picking
"Every device on this network" there is told already, so the window's card
about a tuner kept open from before 1.6.9 does not say it again."""
from __future__ import annotations

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QComboBox, QDialog

from dopeiptv.core import nerd
from dopeiptv.services import home_server as hs


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)
    yield s
    nerd.install(None)


def _pages(settings):
    from dopeiptv.ui import nerd_settings
    from dopeiptv.ui.settings_ui import SettingsPage
    blank = lambda k, sz, c: QPixmap(sz, sz)  # noqa: E731
    page = SettingsPage("nerd", "Nerd", "")
    pages = {}
    for key in nerd_settings.PARTS:
        sp = SettingsPage(f"nerd_{key}", key.title(), "")
        sp.icon_kind, sp.icon_base = "nerd", blank
        pages[key] = sp
    commit = nerd_settings.build(page, settings, QDialog(), pages=pages, open_page=lambda k: None)
    return commit, pages


def _open_box(pages):
    return next(c for c in pages["serve"].findChildren(QComboBox) if c.findData(hs.OPEN_HOME) >= 0)


def test_the_mode_chosen_is_stored_even_never_stored_before(app, settings):
    commit, pages = _pages(settings)
    stored: dict = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert hs.K_OPEN not in stored and hs.K_OPEN_TOLD not in stored      # untouched: nothing
    box = _open_box(pages)
    box.setCurrentIndex(box.findData(hs.OPEN_DEVICES))
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored[hs.K_OPEN] == hs.OPEN_DEVICES and hs.K_OPEN_TOLD not in stored
    box.setCurrentIndex(box.findData(hs.OPEN_HOME))
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored[hs.K_OPEN] == hs.OPEN_HOME and stored[hs.K_OPEN_TOLD] == "true"


def test_a_tuner_kept_open_and_only_looked_at_is_not_told_here(app, settings):
    settings.setValue(hs.K_OPEN, hs.OPEN_HOME)          # kept from before 1.6.9
    commit, pages = _pages(settings)
    stored: dict = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert hs.K_OPEN_TOLD not in stored and hs.open_untold(settings)
