"""The menu bar: File, View, History and Help on every platform; on macOS
About, Settings, Quit and Leave a tip in the application menu by
their roles, as it has always looked there."""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QMenuBar

from dopeiptv.i18n import tr
from dopeiptv.ui import app_menu

#: Every entry the window makes (main_window's _menu_actions).
NAMES = ("about", "settings", "open_video", "open_address", "refresh", "reminders", "calendar",
         "offline", "multiview", "cast", "together", "phone", "tv_mode", "teletext", "private", "clear",
         "stats", "feedback", "answers", "report_wait", "website", "donate", "quit")
TITLES = [tr("menu_file"), tr("menu_view"), tr("nav_history"), tr("menu_help")]
ROLE = QAction.MenuRole
MOVED = (ROLE.AboutRole, ROLE.PreferencesRole, ROLE.ApplicationSpecificRole, ROLE.QuitRole)
_APP = None


def _bar(drop=()):
    global _APP
    _APP = QApplication.instance() or QApplication([])
    bar = QMenuBar()
    return bar, {n: QAction(n, bar) for n in NAMES if n not in drop}


def _seq(menu, visible_only=False):
    return ["|" if a.isSeparator() else a.text() for a in menu.actions()
            if a.isVisible() or not visible_only]


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_four_menus_with_settings_and_quit_in_file_and_about_last_in_help(monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    bar, acts = _bar()
    menus = app_menu.lay_out(bar, acts)
    assert [a.text() for a in bar.actions()] == TITLES
    assert [menus[k].menuAction() for k in ("file", "view", "history", "help")] == bar.actions()
    assert _seq(menus["file"]) == ["open_video", "open_address", "refresh", "reminders", "calendar",
                                   "|", "offline", "|", "settings", "|", "quit"]
    assert _seq(menus["view"]) == ["tv_mode", "teletext", "multiview", "cast", "together", "phone"]
    assert _seq(menus["history"]) == ["stats", "private", "clear"]
    assert _seq(menus["help"]) == ["feedback", "answers", "report_wait", "website",
                                   "|", "donate", "|", "about"]
    # No macOS role: nothing is moved anywhere.
    assert all(a.menuRole() == ROLE.TextHeuristicRole for a in acts.values())
    # An error's report waits hidden; its group keeps the others.
    acts["report_wait"].setVisible(False)
    assert _seq(menus["help"], visible_only=True) == ["feedback", "answers", "website",
                                                      "|", "donate", "|", "about"]
    assert all(m.separatorsCollapsible() for m in menus.values())


def test_a_missing_entry_leaves_no_stray_separator_or_empty_menu(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    bar, acts = _bar(drop=("donate", "settings", "stats", "private", "clear"))
    menus = app_menu.lay_out(bar, acts)
    assert [a.text() for a in bar.actions()] == [tr("menu_file"), tr("menu_view"), tr("menu_help")]
    assert "history" not in menus
    assert _seq(menus["file"]) == ["open_video", "open_address", "refresh", "reminders", "calendar",
                                   "|", "offline", "|", "quit"]
    assert _seq(menus["help"]) == ["feedback", "answers", "report_wait", "website", "|", "about"]


def _app_menu(menus):
    """What Qt puts in macOS's application menu: About, Preferences, the
    application's own entries in their order, Quit."""
    acts = [a for m in menus.values() for a in m.actions() if a.menuRole() in MOVED]
    return [a.text() for r in MOVED for a in acts if a.menuRole() == r]


def test_on_a_mac_the_roles_keep_it_as_it_was(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    bar, acts = _bar()
    menus = app_menu.lay_out(bar, acts)
    # No menu named like the app beside the real application menu.
    assert [a.text() for a in bar.actions()] == TITLES
    assert acts["about"].menuRole() == ROLE.AboutRole
    assert acts["settings"].menuRole() == ROLE.PreferencesRole
    assert acts["quit"].menuRole() == ROLE.QuitRole
    assert acts["donate"].menuRole() == ROLE.ApplicationSpecificRole
    rest = [a for n, a in acts.items() if n not in ("about", "settings", "quit", "donate")]
    # Never moved by its words ("Settings", "About" in some language).
    assert all(a.menuRole() == ROLE.NoRole for a in rest)
    # Today's macOS layout: the application menu, then what stays in each.
    assert _app_menu(menus) == ["about", "settings", "donate", "quit"]

    def stays(menu):
        return [a.text() for a in menu.actions() if a.menuRole() not in MOVED]
    assert stays(menus["file"]) == ["open_video", "open_address", "refresh", "reminders", "calendar",
                                    "", "offline"]
    assert stays(menus["view"]) == ["tv_mode", "teletext", "multiview", "cast", "together", "phone"]
    assert stays(menus["history"]) == ["stats", "private", "clear"]
    assert stays(menus["help"]) == ["feedback", "answers", "report_wait", "website"]
    assert acts["settings"].shortcut() == QKeySequence(QKeySequence.StandardKey.Preferences)
