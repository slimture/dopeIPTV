"""Pictures of the settings for the website's help, in every language.

The help on dopeiptv.org shows the settings page an answer talks about, in
the reader's language. This takes those pictures from the app itself,
offscreen, so they are the release's own: run it when the settings change
and copy the result into the website branch, ``public/help-shots/``::

    QT_QPA_PLATFORM=offscreen python tools/help_screenshots.py OUT [lang ...]

OUT gets ``<lang>/<help id>.webp`` for each language (all of them when none
is named). The help id is the answer's anchor on the help page (``set-room``
for ``/help/#set-room``) - the site renders a picture for the ids in
``site/i18n.json`` ``help_shots``. A picture can be of a page scrolled to
the row or section the answer names. Nothing of the user's is read or
written: the settings are a throwaway store, and the playlist the demo one.
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Twice the pixels: the site shows them at about half, sharp on any screen.
os.environ.setdefault("QT_SCALE_FACTOR", "2")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from PyQt6.QtCore import QEventLoop, QPoint, QSettings, Qt, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QWidget  # noqa: E402

#: help id -> (settings page key, what to scroll to: the string key of a
#: row's label or of a section's heading - found in every language - or ""
#: for the top of the page). Only answers a picture says more for.
SHOTS = {
    "set-language": ("appearance", ""),
    "set-playback": ("playback", ""),
    "set-playlists": ("playlists", ""),
    "set-room": ("appearance", "set_card_places_show"),
    "set-parental": ("parental", ""),
    "set-testbuilds": ("general", "setting_update_channel"),
    "set-profiles": ("profiles", ""),
    "set-sync": ("sync", ""),
    # Its section is there only in a build with a Simkl client id
    # (providers/simkl.py, PUBLIC_CLIENT_ID).
    "more-simkl": ("history", "tab_simkl"),
    "watch-night": ("playback", "night_mode"),
    "watch-web": ("playback", "ytdlp_card"),
    "more-castback": ("casting", "set_cast_keep_buffer"),
}
#: The same, taken with Nerd on: its pages are there only then.
NERD_SHOTS = {
    "fix-vlan": ("nerd_net", "nerd_card_lan"),
}
#: The dialog's size in the pictures: its smallest, so the text stays
#: readable when the site scales it down to the answer's width.
SIZE = (860, 600)
QUALITY = 82
#: Room left around what a picture is scrolled to.
MARGIN = 24


def _settle(app: QApplication, ms: int) -> None:
    """Let the window's timers run (Nerd's pages come out in the sidebar
    one after another)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    app.processEvents()


def _target(page, text: str) -> tuple[QWidget, QWidget] | None:
    """The section headed *text* on *page*, or the row labelled *text*:
    (what has to be in sight, where its section starts)."""
    for card in page.cards:
        head = card.property("_title")
        if isinstance(head, QLabel) and head.text() == text:
            return card, head
        for w, _words in card.rows:
            label = getattr(w, "label", None)
            if isinstance(label, QLabel) and label.text() == text:
                return w, head if isinstance(head, QLabel) else w
    return None


def _scroll_to(page, text: str) -> bool:
    """Scroll *page* to the row or section *text* (the top for none): the
    least that shows it whole, with its section's heading when both fit,
    so as much of the page above stays in the picture as there is room
    for. False when the page has no such row."""
    bar = page.verticalScrollBar()
    if not text:
        bar.setValue(0)
        return True
    found = _target(page, text)
    if found is None:
        return False
    widget, start = found
    top = widget.mapTo(page.body, QPoint(0, 0)).y()
    first = min(top, start.mapTo(page.body, QPoint(0, 0)).y())
    bottom = top + widget.height()
    height = page.viewport().height()
    if bottom - first + 2 * MARGIN <= height:
        value = bottom + MARGIN - height
    else:
        value = first - MARGIN
    bar.setValue(max(0, value))
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    out, wanted = argv[0], argv[1:]

    from dopeiptv import i18n
    from dopeiptv.core import ytdlp
    from dopeiptv.i18n import tr
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
    # The yt-dlp section as most people first see it, with none yet - not
    # this machine's own copy and where it is.
    ytdlp.find = lambda *_a, **_k: None

    from dopeiptv.ui.main_window import MainWindow
    win = MainWindow(DemoClient(), settings)
    win.resize(1200, 820)

    langs = wanted or ["en", *sorted(c for c in i18n.LANGUAGES if c != "en")]
    current: dict = {"lang": "", "shots": SHOTS, "failed": False}

    def fail(message: str) -> int:
        print(f"{current['lang']}: {message}", file=sys.stderr)
        current["failed"] = True
        return 1

    def grab(dialog: QDialog) -> int:
        dialog.resize(*SIZE)
        dialog.show()
        _settle(app, 400)
        shell = dialog.findChild(SettingsShell)
        folder = os.path.join(out, current["lang"])
        os.makedirs(folder, exist_ok=True)
        for help_id, (key, row) in current["shots"].items():
            shell.show_page(key)
            page = next((p for p in shell.pages if p.key == key), None)
            if page is None or shell.current_key() != key:
                return fail(f"{help_id}: no page {key!r} in Settings")
            app.processEvents()
            if not _scroll_to(page, tr(row) if row else ""):
                return fail(f"{help_id}: no row or section {row!r} on {key!r}")
            # No caret blinking in the search box on every picture.
            focus = app.focusWidget()
            if focus is not None:
                focus.clearFocus()
            app.processEvents()
            path = os.path.join(folder, help_id + ".webp")
            if not dialog.grab().save(path, "webp", QUALITY):
                return fail(f"could not write {path}")
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
        for nerd, shots in ((False, SHOTS), (True, NERD_SHOTS)):
            settings.setValue("nerd_enabled", "true" if nerd else "false")
            current["shots"] = shots
            win.open_settings()
        settings.setValue("nerd_enabled", "false")
        print(code, flush=True)
    return 1 if current["failed"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
