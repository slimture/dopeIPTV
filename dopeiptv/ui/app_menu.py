"""The app's menus: one layout on every platform.

File, View, History and Help in the menu bar, the same entries in the same
places on Linux, Windows and macOS - Settings and Quit in File, About last
in Help, where people look for them. On macOS Qt moves About, Settings and
Quit into the application menu by their roles, and the supporter key and
donating go there too (ApplicationSpecificRole), so it looks as it always
has; every other entry is kept from being moved there by its words. The
actions are made by the window (they keep their shortcuts and handlers);
this only says where each goes."""

from __future__ import annotations

import sys

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMenu

from ..core.xdg import open_url
from ..i18n import tr

#: The entries in their groups, a separator between two groups in one
#: menu: (group, its menu, actions).
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("file", "file", ("open_video", "open_address", "refresh", "reminders", "calendar")),
    ("prefs", "file", ("settings",)),
    ("quit", "file", ("quit",)),
    ("view", "view", ("tv_mode", "teletext", "multiview", "cast", "together", "phone")),
    ("history", "history", ("stats", "private", "clear")),
    ("help", "help", ("feedback", "answers", "report_wait", "website")),
    ("support", "help", ("supporter", "donate")),
    ("about", "help", ("about",)),
)

#: The menus in the bar, in order, and their titles.
MENUS: tuple[tuple[str, str], ...] = (
    ("file", "menu_file"), ("view", "menu_view"), ("history", "nav_history"), ("help", "menu_help"))

#: macOS: what goes into the application menu, by its role.
_MAC_ROLES = {"about": QAction.MenuRole.AboutRole, "settings": QAction.MenuRole.PreferencesRole,
              "quit": QAction.MenuRole.QuitRole,
              "supporter": QAction.MenuRole.ApplicationSpecificRole,
              "donate": QAction.MenuRole.ApplicationSpecificRole}

WEBSITE = "https://dopeiptv.org/?ref=app-help"


def open_website() -> None:
    open_url(WEBSITE)


def lay_out(menubar, actions: dict[str, QAction], mac: bool | None = None) -> dict[str, QMenu]:
    """*actions* (by the names in GROUPS; a missing one is left out) put in
    *menubar*'s menus; {"file"/"view"/"history"/"help": its QMenu}, a menu
    with nothing in it not made. *mac*: macOS's roles (default: on macOS)."""
    if mac is None:
        mac = sys.platform == "darwin"
    menus: dict[str, QMenu] = {}
    for key, title in MENUS:
        groups = [present for _group, where, names in GROUPS if where == key
                  if (present := [n for n in names if n in actions])]
        if not groups:
            continue
        menu = menus[key] = menubar.addMenu(tr(title))
        for present in groups:
            # On macOS a group that moves to the application menu leaves
            # no separator behind; a hidden entry's are Qt's to collapse.
            moved = mac and all(n in _MAC_ROLES for n in present)
            if menu.actions() and not moved:
                menu.addSeparator()
            for name in present:
                act = actions[name]
                menu.addAction(act)
                if mac:
                    # NoRole: never moved by its words ("Settings", "About",
                    # "Quit" in some language) into the application menu.
                    act.setMenuRole(_MAC_ROLES.get(name, QAction.MenuRole.NoRole))
    if mac:
        settings = actions.get("settings")
        if settings is not None and settings.shortcut().isEmpty():
            settings.setShortcut(QKeySequence(QKeySequence.StandardKey.Preferences))
    return menus
