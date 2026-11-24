"""The app with only a keyboard and with a screen reader (ui/a11y.py).

Orca, VoiceOver, Narrator and NVDA read what Qt's QAccessible says of a
widget - its accessible name, else its own words (a button's text), else
the QLabel whose buddy it is - and a painted list's rows by their
model's AccessibleTextRole. PyQt6 does not wrap QAccessible, so these
tests hold the widgets to the rule ``a11y.spoken_name`` follows.

In process: the rule itself, the lists' words for a screen reader (the
channel list, a series' episodes), the keyboard's ring on a painted row
(only once the keyboard moved the focus), Enter on the category list as
the keyboard's click, the actor chip and the programme row taking the
focus. On the real window, in a child process: every control of the
window - in every place -, of Settings (every page, Nerd's too) and of
the main dialogs has words a screen reader can say; Tab goes through the
window to visible, enabled, named controls only and comes back to where
it started, never opening a window by resting somewhere (Local files'
"+ Add folder" row), never kept on a page (Search)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QKeyEvent, QPainter  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication, QGroupBox, QLabel, QLineEdit, QPushButton, QStyle,
    QStyleOptionViewItem, QToolButton, QVBoxLayout, QWidget,
)

from dopeiptv import i18n  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402
from dopeiptv.ui import a11y  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module", autouse=True)
def app():
    i18n.set_language("en")
    return QApplication.instance() or QApplication([])


# -- the rule ------------------------------------------------------------------------------

def test_what_counts_as_words_for_a_screen_reader():
    box = QWidget()
    lay = QVBoxLayout(box)
    worded, glyph, named = QPushButton("&Play"), QPushButton("✕"), QPushButton("⚙")
    named.setAccessibleName("Options")
    field, labelled = QLineEdit(), QLineEdit()
    field.setPlaceholderText("Filter…")             # a placeholder is no name
    label = QLabel("Server")
    label.setBuddy(labelled)
    group = QGroupBox("Proxy")
    in_group = QLineEdit(group)
    for w in (worded, glyph, named, field, label, labelled, group):
        lay.addWidget(w)
    assert a11y.spoken_name(worded) == "Play"
    assert a11y.spoken_name(glyph) == ""            # read as its Unicode name, or not at all
    assert a11y.spoken_name(named) == "Options"
    assert a11y.spoken_name(field) == ""
    assert a11y.spoken_name(labelled) == "Server"
    assert a11y.spoken_name(in_group) == "Proxy"
    missing = a11y.unnamed(box)
    assert glyph in missing and field in missing
    assert worded not in missing and labelled not in missing and in_group not in missing
    # Qt's own parts are not the app's controls: a line edit's clear button.
    field.setClearButtonEnabled(True)
    inner = field.findChildren(QToolButton)
    assert not any(b in a11y.unnamed(box) for b in inner)


def test_words_that_come_later_and_fields_named_by_their_row():
    box = QWidget()
    lay = QVBoxLayout(box)
    later = a11y.words_later(QPushButton(""))
    lay.addWidget(later)
    later.hide()
    assert later not in a11y.unnamed(box)            # hidden until it has its words
    later.show()
    assert later in a11y.unnamed(box)                # shown without them still counts
    later.setText("Update to 1.7")
    assert later not in a11y.unnamed(box)
    holder = QWidget()
    QVBoxLayout(holder).addWidget(QLineEdit())
    a11y.name_fields(holder, "HTTP proxy")
    assert a11y.spoken_name(holder.findChild(QLineEdit)) == "HTTP proxy"
    assert a11y.without_key("Collapse the sidebar to icons (Ctrl+B)") == "Collapse the sidebar to icons"
    assert a11y.without_key("隐藏列表（Ctrl+Shift+M）") == "隐藏列表"
    assert a11y.without_key("Volume") == "Volume"


# -- the lists' words ------------------------------------------------------------------------

class _Guide:
    def now_for(self, it):
        return ("Rapport", 40.0) if it.get("name") == "SVT1 HD" else None

    def is_loaded(self):
        return True


class _Window(QObject):
    """What the channel list's delegate asks of the window."""

    def __init__(self):
        super().__init__()
        from PyQt6.QtCore import QSettings
        self.settings = QSettings("dopeiptv-test", "a11y")
        self.settings.clear()
        self.xmltv = _Guide()
        self._playing_key = None
        self._playing_group = None
        self.renamed = {"SVT1 HD": "SVT1"}

    def channel_display_name(self, it):
        return self.renamed.get(it.get("name"), it.get("name") or "")

    def is_favorite_item(self, it, kind):
        return it.get("name") == "SVT1 HD"

    def watched_source(self, it, kind):
        return "local" if it.get("name") == "Dune" else None

    def is_item_on_watchlist(self, it, kind):
        return False

    def _chno_shown(self, it):
        return it.get("num")


def test_the_channel_list_says_what_each_row_shows():
    from dopeiptv.ui.channel_list import ChannelDelegate, ChannelListModel
    win = _Window()
    delegate = ChannelDelegate(win)
    delegate.set_grid(False)
    model = ChannelListModel()
    model.describe = delegate.words_for
    rows = [{"_header": "Sweden"},
            {"name": "SVT1 HD", "num": 1, "stream_id": 1},
            {"name": "Dune", "stream_id": 9, "_progress_pct": 42}]
    model.set_items(rows, "fav")
    role = Qt.ItemDataRole.AccessibleTextRole
    assert model.data(model.index(0), role) == "Sweden"
    said = model.data(model.index(1), role)
    # The renamed name the list paints, never the provider's; what is on; the star.
    assert said.startswith("1 SVT1,") and "SVT1 HD" not in said
    assert tr("epg_now_prefix", title="Rapport") in said and tr("sp_favorite") in said
    model.set_items(rows, "vod")
    said = model.data(model.index(2), role)
    assert said.startswith("Dune") and tr("pill_seen") in said
    assert tr("card_progress_a11y", n=42) in said
    # Without the delegate's words: the name, never nothing.
    plain = ChannelListModel()
    plain.set_items([{"name": "Kanal 5"}], "live")
    assert plain.data(plain.index(0), role) == "Kanal 5"
    # A describe that fails gives the name too (a paint-time fault never empties it).
    plain.describe = lambda it, kind: 1 / 0
    assert plain.data(plain.index(0), role) == "Kanal 5"


def test_an_episode_card_says_its_title_state_and_plot():
    from dopeiptv.core import series_view as sv
    from dopeiptv.ui.series_cards import EpisodeModel, EpisodeState, episode_a11y
    ep = {"id": "e5", "_sp_label": "E5 · The bridge", "info": {"duration_secs": 2700,
                                                                "plot": "Saga finds a body."}}
    plain = episode_a11y(ep, EpisodeState())
    assert plain.startswith("E5 · The bridge") and "Saga finds a body." in plain
    assert tr("sp_playing") in episode_a11y(ep, EpisodeState(), playing=True)
    assert tr("pill_on_tv") in episode_a11y(ep, EpisodeState(), playing=True, on_tv=True)
    assert tr("sp_up_next") in episode_a11y(ep, EpisodeState(), next_up=True)
    assert tr("pill_seen") in episode_a11y(ep, EpisodeState(watched="local"))
    half = EpisodeState(progress=sv.Progress(pos=1350.0, dur=2700.0))
    words = episode_a11y(ep, half)
    assert tr("card_progress_a11y", n=50) in words and tr("home_min_left", n=22) in words
    assert "45" in plain                    # its length, as the card writes it
    model = EpisodeModel()
    model.set_items([ep])
    role = Qt.ItemDataRole.AccessibleTextRole
    assert model.data(model.index(0), role) == "E5 · The bridge"
    model.describe = lambda e: episode_a11y(e, EpisodeState(watched="local"))
    assert tr("pill_seen") in model.data(model.index(0), role)


# -- the keyboard ----------------------------------------------------------------------------

def _ring_pixels(state: QStyle.StateFlag) -> int:
    img = QImage(120, 30, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    opt = QStyleOptionViewItem()
    opt.rect = QRect(0, 0, 120, 30)
    opt.state = state
    a11y.keyboard_ring(p, opt)
    p.end()
    return sum(1 for x in range(0, 120, 2) for y in range(30) if img.pixel(x, y))


def test_the_keyboards_row_is_ringed_only_once_the_keyboard_moved():
    s = QStyle.StateFlag
    assert _ring_pixels(s.State_HasFocus | s.State_KeyboardFocusChange) > 0
    assert _ring_pixels(s.State_HasFocus) == 0            # a click: the look as it was
    assert _ring_pixels(s.State_KeyboardFocusChange) == 0


def _key(w, key):
    QApplication.sendEvent(w, QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier))


def test_enter_on_a_category_is_the_keyboards_click():
    from PyQt6.QtWidgets import QListWidgetItem
    from dopeiptv.ui.channel_list import FolderDropList
    lst = FolderDropList()
    for name in ("Sweden", "+ Add folder…"):
        lst.addItem(QListWidgetItem(name))
    got = []
    lst.entered.connect(lambda it: got.append(it.text()))
    lst.setCurrentRow(1)
    assert got == []                       # coming to rest on a row does nothing
    _key(lst, Qt.Key.Key_Return)
    assert got == ["+ Add folder…"]


def test_a_clickable_chip_takes_enter_and_space():
    from dopeiptv.ui.widgets import _ClickableWidget
    chip = _ClickableWidget()
    hits = []
    chip.clicked.connect(lambda: hits.append(1))
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
        _key(chip, key)
    assert hits == [1, 1, 1]
    _key(chip, Qt.Key.Key_A)
    assert hits == [1, 1, 1]


def test_the_current_row_is_chosen_as_tab_comes_into_a_list():
    from PyQt6.QtCore import QStringListModel
    from PyQt6.QtWidgets import QListView
    win = QWidget()
    lay = QVBoxLayout(win)
    before = QLineEdit()
    view = a11y.show_current(QListView())
    view.setModel(QStringListModel(["SVT1", "SVT2"]))
    lay.addWidget(before)
    lay.addWidget(view)
    win.show()
    win.activateWindow()
    before.setFocus()
    QApplication.processEvents()
    _key(before, Qt.Key.Key_Tab)
    for _ in range(5):
        QApplication.processEvents()
    assert view.hasFocus() and view.currentIndex().isValid()
    assert view.selectionModel().isSelected(view.currentIndex())
    win.close()


# -- the real window ---------------------------------------------------------------------------

_CHILD = r"""
import faulthandler, os, sys, tempfile, time
faulthandler.enable()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QEvent, QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QToolButton
app = QApplication.instance() or QApplication([])

from dopeiptv import i18n
from dopeiptv.core import backup, places, profiles
from dopeiptv.core.profiles import ProfileSettings
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import a11y, main_window as mw, theme
from dopeiptv.ui.settings_ui import SettingsShell, Stepper

i18n.set_language("en")
mw.launch_player = lambda *a, **k: None
asked = []
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: asked.append("folder") or "")


class Client(DemoClient):
    def vod_categories(self):
        return [{"category_id": "m", "category_name": "Drama"}]

    def vod_streams(self, cat=None):
        return [{"stream_id": 1, "name": "Dune (2021)", "stream_type": "movie", "category_id": "m"},
                {"stream_id": 3, "name": "Oppenheimer", "stream_type": "movie", "category_id": "m"}]

    def series_categories(self):
        return [{"category_id": "s", "category_name": "Crime"}]

    def series(self, cat=None):
        return [{"series_id": 7, "name": "Bron", "category_id": "s"}]


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        w.pool.waitForDone(5)
        time.sleep(0.003)


def allowed(x):
    # Stepper's minus and plus never take the focus: the number between
    # them - named, its arrow keys step it - is the control.
    return isinstance(x, QToolButton) and isinstance(x.parentWidget(), Stepper)


def missing(root, where):
    bad = [a11y.describe(x) for x in a11y.unnamed(root) if not allowed(x)]
    assert not bad, where + ": no words for a screen reader:\n  " + "\n  ".join(bad)


path = os.path.join(tempfile.mkdtemp(), "s.ini")
s = ProfileSettings(path, QSettings.Format.IniFormat)
profiles.activate("")
s.setValue("check_updates", "false")
s.setValue("nerd_enabled", "true")
theme.apply_theme(s)
app.setStyleSheet(theme.build_style())
w = mw.MainWindow(Client(), s, PlaylistStore(s))
w.resize(1300, 820)
w.show()
pump(40)

# -- every control named, in every place -------------------------------------------------
for place in places.PLACES:
    w._show_place(place)
    pump(15)
    missing(w, "the window on " + place)

# -- Tab: visible, enabled, named stops only; back where it started ----------------------
w.activateWindow()
pump(3)


def tab_round(place, limit=200):
    w._show_place(place)
    pump(15)
    # The list, else the place's own entry in the sidebar: a stop that stays
    # while the round goes (the info column's programme rows are made again
    # as the guide comes in and the detail follows the place's pick).
    start = w.listw if w.listw.isVisible() else w.nav_btns[places.NAV_OF[place]]
    start.setFocus(Qt.FocusReason.TabFocusReason)
    pump(2)
    start = QApplication.focusWidget()
    assert start is not None, place
    stops = []
    for _ in range(limit):
        fw = QApplication.focusWidget()
        QApplication.sendEvent(fw, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                                             Qt.KeyboardModifier.NoModifier))
        app.processEvents()
        nw = QApplication.focusWidget()
        assert nw is not None, (place, "the focus went nowhere after", stops[-3:])
        assert nw.isVisible() and nw.isEnabled(), (place, a11y.describe(nw))
        assert a11y.spoken_name(nw), (place, "a stop without words", a11y.describe(nw))
        stops.append(a11y.spoken_name(nw))
        if nw is start:
            return stops
    raise AssertionError(f"{place}: Tab never came back: {stops[-12:]}")


for place in places.PLACES:
    stops = tab_round(place)
    assert len(stops) >= 5, (place, stops)
    assert "Settings" in stops and "TV" in stops, (place, stops)
# Resting on Local files' "+ Add folder" row asked for no folder.
assert asked == [], asked
# The Search page never keeps the keyboard: Tab from its results goes on.
w._show_place("search")
pump(10)
page = w._search_page
page.view.setFocus(Qt.FocusReason.TabFocusReason)
QApplication.sendEvent(page.view, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab,
                                            Qt.KeyboardModifier.NoModifier))
app.processEvents()
assert QApplication.focusWidget() not in (page.view, page.field), a11y.describe(QApplication.focusWidget())

# -- Settings, every page (Nerd's too) ------------------------------------------------------
def settings_run(self):
    self.findChild(SettingsShell).build_all()
    missing(self, "Settings")
    return 0


QDialog.exec = settings_run
w.open_settings()

# -- the main dialogs ----------------------------------------------------------------------
# Each opens without a window of its own flashing up first: a widget shown
# before it has a parent is one, and GNOME's pointer spins for seconds then.
from PyQt6.QtCore import QObject
from PyQt6.QtWidgets import QWidget
stray = []


class _StrayWatch(QObject):
    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.Show and isinstance(obj, QWidget) and obj.isWindow()
                and obj is not w and obj.windowType() not in (
                    Qt.WindowType.Popup, Qt.WindowType.ToolTip, Qt.WindowType.Dialog)
                and not isinstance(obj, QDialog)):
            stray.append(f"{type(obj).__name__} {getattr(obj, 'text', lambda: '')()!r}")
        return False


_stray_watch = _StrayWatch()
app.installEventFilter(_stray_watch)
from dopeiptv.ui.cast_dialog import CastDialog
from dopeiptv.ui.dialogs import FeedbackDialog, PlaylistDialog, ReportDialog, RestoreBackupDialog
from dopeiptv.ui.feedback_dialogs import AnswersDialog
from dopeiptv.ui.profiles_ui import ProfileDialog
from dopeiptv.ui.reminders import RemindersDialog
from dopeiptv.ui.source_page import SourcePage


class Store:
    def __init__(self, pl):
        self.pl = pl

    def get(self, pid):
        return self.pl

    def update(self, pid, **f):
        self.pl.update(f)


b = backup.parse({backup.MAGIC: 1, "stores": {"main": {"theme": "nord"}}})
for name, make in (
        ("cast", lambda: CastDialog(w, "http://p/live/u/pw/1.m3u8", "SVT1", probe=False,
                                    tracks={"audio": [], "subs": []})),
        ("source", lambda: PlaylistDialog(w)),
        ("source page", lambda: SourcePage({"id": "s1", "name": "Home", "kind": "xtream",
                                            "server": "http://a.invalid", "username": "u",
                                            "password": "p"}, store=Store({}), nerd_on=True,
                                           crumb="Sources")),
        ("profile", lambda: ProfileDialog(w, w.profiles)),
        ("feedback", lambda: FeedbackDialog(w, lambda opts: "")),
        ("report", lambda: ReportDialog(w, "error", "x")),
        ("answers", lambda: AnswersDialog(w, [])),
        ("restore", lambda: RestoreBackupDialog(w, b, backup.summary(b), 0, backup.risky(b))),
        ("reminders", lambda: RemindersDialog(w))):
    d = make()
    missing(d, name)
    d.deleteLater()


def window_run(self):
    missing(self, self.windowTitle())
    return 0


QDialog.exec = window_run
for opener in ("_show_scheduled_manager", "open_address", "open_queue", "open_equaliser",
               "show_about", "_open_content_manager"):
    getattr(w, opener)()
    pump(3)
assert not stray, "shown as windows of their own: " + ", ".join(stray)
print("A11Y_OK", flush=True)
os._exit(0)
"""


def _child_env(tmp_path) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    return env


def test_every_control_is_named_and_tab_goes_round_the_window(tmp_path):
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT,
                         env=_child_env(tmp_path), capture_output=True, text=True, timeout=240)
    assert "A11Y_OK" in out.stdout, (f"exit {out.returncode}\n" + out.stdout[-3000:]
                                     + out.stderr[-5000:])
