"""Pictures of the settings for the website's help, in every language.

The help on dopeiptv.org shows the settings page an answer talks about, in
the reader's language. This takes those pictures from the app itself,
offscreen, so they are the release's own: run it when the settings change
and copy the result into the website branch, ``public/help-shots/``::

    QT_QPA_PLATFORM=offscreen python tools/help_screenshots.py OUT [lang ...]

OUT gets ``<lang>/<help id>.webp`` for each language (all of them when none
is named). The help id is the answer's anchor on the help page (``set-home``
for ``/help/#set-home``) - the site renders a picture for the ids in
``site/i18n.json`` ``help_shots``. Nothing of the user's is read or written:
the settings are a throwaway store, and the playlist the demo one.
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Twice the pixels: the site shows them at about half, sharp on any screen.
os.environ.setdefault("QT_SCALE_FACTOR", "2")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtCore import QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

#: help id -> settings page key. Only answers a picture says more for.
SHOTS = {
    "set-language": "appearance",
    "set-playback": "playback",
    "set-playlists": "playlists",
    "set-home": "home",
    "set-parental": "parental",
    "set-testbuilds": "general",
}
#: The dialog's size in the pictures: its smallest, so the text stays
#: readable when the site scales it down to the answer's width.
SIZE = (860, 600)
QUALITY = 82


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    out, wanted = argv[0], argv[1:]

    from dopeiptv import i18n
    from dopeiptv.providers.client import DemoClient
    from dopeiptv.ui.qt_i18n import apply_qt_language
    from dopeiptv.ui.settings_ui import SettingsShell
    from dopeiptv.ui.theme import apply_theme, build_style

    app = QApplication.instance() or QApplication(sys.argv)
    store = tempfile.mkdtemp(prefix="dopeiptv-shots-")
    QSettings.setPath(QSettings.Format.IniFormat,
                      QSettings.Scope.UserScope, store)
    settings = QSettings(os.path.join(store, "shots.ini"),
                         QSettings.Format.IniFormat)
    apply_theme(settings)
    app.setStyleSheet(build_style())

    from dopeiptv.ui.main_window import MainWindow
    win = MainWindow(DemoClient(), settings)
    win.resize(1200, 820)

    langs = wanted or ["en", *sorted(c for c in i18n.LANGUAGES if c != "en")]
    current = {"lang": ""}

    def grab(dialog: QDialog) -> int:
        dialog.resize(*SIZE)
        dialog.show()
        app.processEvents()
        shell = dialog.findChild(SettingsShell)
        folder = os.path.join(out, current["lang"])
        os.makedirs(folder, exist_ok=True)
        for help_id, page in SHOTS.items():
            shell.show_page(page)
            # No caret blinking in the search box on every picture.
            focus = app.focusWidget()
            if focus is not None:
                focus.clearFocus()
            app.processEvents()
            path = os.path.join(folder, help_id + ".webp")
            if not dialog.grab().save(path, "webp", QUALITY):
                print(f"could not write {path}", file=sys.stderr)
                return 1
        return 0

    QDialog.exec = grab  # type: ignore[method-assign,assignment]
    for code in langs:
        if code not in i18n.LANGUAGES:
            print(f"skipping {code}: not a language of the app", file=sys.stderr)
            continue
        current["lang"] = code
        i18n.set_language(code)
        apply_qt_language(app)
        app.setLayoutDirection(
            Qt.LayoutDirection.RightToLeft if i18n.is_rtl(code)
            else Qt.LayoutDirection.LeftToRight)
        win.open_settings()
        print(code, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
