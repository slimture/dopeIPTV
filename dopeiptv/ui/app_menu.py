"""The app's menus, laid out the way each platform expects.

macOS: a real menu bar - the application menu (About, the supporter key
and donating, Preferences, Quit: Qt moves them there by their roles), then
File, View, History and Help. Linux and Windows: the one "dopeIPTV" menu,
the same groups one under the other with separators between them. The
actions are made by the window (they keep their shortcuts and handlers);
this only says where each goes."""

from __future__ import annotations

from PyQt6.QtGui import QAction, QKeySequence

from .. import APP_NAME
from ..core.xdg import open_url
from ..i18n import tr

#: The entries in their groups: (group, the menu it is on macOS, actions).
GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("about", "app", ("about",)),
    ("prefs", "app", ("settings",)),
    ("file", "file", ("open_video", "refresh", "reminders")),
    ("view", "view", ("multiview", "cast")),
    ("history", "history", ("private", "clear")),
    ("help", "help", ("feedback", "answers", "report_wait", "website")),
    ("support", "app", ("supporter", "donate")),
    ("quit", "app", ("quit",)),
)

#: macOS's menus in the bar, in order, and their titles.
MAC_MENUS: tuple[tuple[str, str], ...] = (
    ("file", "menu_file"), ("view", "menu_view"), ("history", "nav_history"), ("help", "menu_help"))

_ROLES = {"about": QAction.MenuRole.AboutRole, "settings": QAction.MenuRole.PreferencesRole,
          "quit": QAction.MenuRole.QuitRole}

WEBSITE = "https://dopeiptv.org/?ref=app-help"


def open_website() -> None:
    open_url(WEBSITE)


def fold_into_app_menu(menu) -> None:
    """macOS: every entry of *menu* without a standard role goes into the
    one real application menu - an entry left with none makes Qt keep a
    second menu named like the app beside it."""
    for act in menu.actions():
        if not act.isSeparator() and act.menuRole() in (QAction.MenuRole.TextHeuristicRole,
                                                        QAction.MenuRole.NoRole):
            act.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)


def lay_out(menubar, actions: dict[str, QAction], mac: bool) -> dict[str, object]:
    """*actions* (by the names in GROUPS; a missing one is left out) put in
    *menubar*'s menus; {"app"/"file"/"view"/"history"/"help": its QMenu} -
    on Linux and Windows all five are the one menu."""
    app_menu = menubar.addMenu(APP_NAME)
    menus: dict[str, object] = {"app": app_menu}
    for key, title in MAC_MENUS:
        menus[key] = menubar.addMenu(tr(title)) if mac else app_menu
    for _group, where, names in GROUPS:
        present = [n for n in names if n in actions]
        if not present:
            continue
        menu = menus[where] if mac else app_menu
        if not mac and app_menu.actions():
            app_menu.addSeparator()
        for name in present:
            act = actions[name]
            menu.addAction(act)
            if name in _ROLES:
                # A no-op on Linux and Windows.
                act.setMenuRole(_ROLES[name])
            elif not mac:
                continue
            elif where == "app":
                act.setMenuRole(QAction.MenuRole.ApplicationSpecificRole)
            else:
                # Never moved by its words ("Settings", "About", "Quit" in
                # some language) into the application menu.
                act.setMenuRole(QAction.MenuRole.NoRole)
    if mac:
        settings = actions.get("settings")
        if settings is not None and settings.shortcut().isEmpty():
            settings.setShortcut(QKeySequence(QKeySequence.StandardKey.Preferences))
        fold_into_app_menu(app_menu)
    return menus
