"""The command palette: Ctrl+K over the window, three groups as it is
typed in - the app's actions with their keys, every Settings row (Nerd's
only while Nerd is on), the active source's channels and the guide's
programmes - matched without case or accents, word starts first, fast on
tens of thousands of channels; Enter runs a row, Esc closes; its key is
the user's to change; nothing searched is kept while viewing privately."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from dopeiptv.core import palette_match as pm
from dopeiptv.core import shortcut_keys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- matching ---------------------------------------------------------------------

def test_case_accents_and_punctuation_do_not_count():
    assert pm.fold("Nätt-TV  HD!") == " natt tv hd "
    assert pm.fold("Café Ørsted Łódź") == " cafe orsted lodz "
    assert pm.words("  NATT,  tv ") == ["natt", "tv"]
    idx = pm.Index(["Nätt-TV", "Café du Monde", "Sport 1"])
    assert idx.search("natt", 5) == [0]
    assert idx.search("CAFE", 5) == [1]
    assert idx.search("", 5) == [] and idx.search("x", 0) == []


def test_every_word_has_to_be_there_and_word_starts_weigh_more():
    texts = ["Nightly news", "Midnight movie", "Night mode", "Sports at night", "Knight rider"]
    idx = pm.Index(texts)
    got = [texts[i] for i in idx.search("night", 10)]
    # At the very start first (the shorter of two first), then at a word's
    # start, then inside a word.
    assert got[:2] == ["Night mode", "Nightly news"], got
    assert got[2] == "Sports at night", got
    assert set(got[3:]) == {"Midnight movie", "Knight rider"}, got
    assert [texts[i] for i in idx.search("night mo", 10)] == ["Night mode", "Midnight movie"]
    assert idx.search("night zebra", 10) == []
    assert pm.score(["night"], pm.fold("Night")) > pm.score(["night"], pm.fold("Night mode"))


def test_a_query_over_twenty_thousand_channels_is_quick():
    names = [f"Channel {i} {'Sport' if i % 7 else 'Nyheter Ström'} HD" for i in range(20_000)]
    idx = pm.Index(names)
    assert len(idx) == 20_000
    start = time.perf_counter()
    for q in ("strom", "sport hd", "channel 1999", "zzz"):
        idx.search(q, 4)
    per_query = (time.perf_counter() - start) / 4
    assert per_query < 0.15, per_query
    assert names[idx.search("channel 1999", 1)[0]] == "Channel 1999 Sport HD"


def test_the_palette_is_a_rebindable_action():
    assert shortcut_keys.DEFAULTS["palette"] == "Ctrl+K"
    assert shortcut_keys.reserved("Ctrl+K", "linux") is None
    over, lost = shortcut_keys.assign({}, "palette", "Ctrl+Shift+P")
    assert over == {"palette": "Ctrl+Shift+P"} and lost == []
    assert shortcut_keys.effective(over)["palette"] == "Ctrl+Shift+P"


# -- the overlay, with stand-ins for what it finds and does -------------------------

@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _overlay():
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui.command_palette import CommandPalette, Entry
    host = QWidget()
    host.resize(1000, 700)
    ran = []
    rows = [Entry("actions", "Player", "Mute", keys="M", payload="mute"),
            Entry("settings", "Setting", "Night mode", where="Playback › Behaviour",
                  more="Evens out the loudness", payload="night"),
            Entry("settings", "Nerd", "Audio delay", where="Nerd › Player", nerd=True,
                  payload="delay"),
            Entry("channels", "Channel", "Nätt-TV", where="1 · Demo", payload="chan")]

    def search(q):
        terms = pm.words(q)
        return [e for e in rows if not terms or pm.score(terms, pm.fold(e.text))]

    pal = CommandPalette(host, search, lambda e, _pos: ran.append(e.payload), "Ctrl+K")
    host.show()
    return host, pal, ran


def test_the_overlay_opens_chooses_runs_and_closes(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    host, pal, ran = _overlay()
    closed = []
    pal.closed.connect(lambda: closed.append(1))
    pal.open()
    assert pal.isVisible()
    assert pal.geometry() == host.rect()
    # Three groups, each under its heading, the first row chosen.
    assert [e.group for e in pal.entries] == ["actions", "settings", "settings", "channels"]
    assert pal.current().payload == "mute"
    QTest.keyClick(pal.field, Qt.Key.Key_Down)
    assert pal.current().payload == "night"           # the heading is passed over
    QTest.keyClick(pal.field, Qt.Key.Key_Down)
    QTest.keyClick(pal.field, Qt.Key.Key_Down)
    assert pal.current().payload == "chan"
    QTest.keyClick(pal.field, Qt.Key.Key_Down)
    assert pal.current().payload == "chan"            # stays on the last
    QTest.keyClick(pal.field, Qt.Key.Key_Up)
    assert pal.current().payload == "delay"
    # Tab: more about the row.
    QTest.keyClick(pal.field, Qt.Key.Key_Up)
    QTest.keyClick(pal.field, Qt.Key.Key_Tab)
    assert pal.more.isVisibleTo(pal) and pal.more.text() == "Evens out the loudness"
    QTest.keyClick(pal.field, Qt.Key.Key_Tab)
    assert not pal.more.isVisibleTo(pal)
    # Typed: looked up again a moment later, accents left out.
    QTest.keyClicks(pal.field, "natt")
    assert pal.field.text() == "natt"
    QTest.qWait(200)
    assert [e.payload for e in pal.entries] == ["chan"]
    QTest.keyClick(pal.field, Qt.Key.Key_Return)
    assert ran == ["chan"]
    pal.field.setText("zebra")
    pal.refresh()
    assert pal.entries == [] and pal.none.isVisibleTo(pal) and "zebra" in pal.none.text()
    QTest.keyClick(pal.field, Qt.Key.Key_Escape)
    assert not pal.isVisible() and closed == [1]
    host.close()


def test_enter_right_after_typing_runs_what_was_typed(app):
    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    host, pal, ran = _overlay()
    pal.open()
    QTest.keyClicks(pal.field, "night")
    QTest.keyClick(pal.field, Qt.Key.Key_Return)      # before the pause is over
    assert ran == ["night"]
    host.close()


def test_a_big_guide_is_never_matched_on_the_window_s_thread(app, monkeypatch):
    """50,000 channels and a guide: a one-letter query took a few tenths of
    a second on the window's thread, every key typed, and the video drawn
    there stuttered. The actions come at once, the channels and programmes
    from a worker - found exactly as before, and only for the query still
    typed; Enter right after typing still runs what was typed."""
    import threading

    from PyQt6.QtCore import Qt
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui import mw_palette
    from dopeiptv.ui.command_palette import CommandPalette, Entry

    now = time.time()
    chans = [{"name": f"Kanal {i} {'Sport' if i % 7 else 'Nyheter Ström'} HD", "stream_id": i,
              "category_id": "1"} for i in range(50_000)]
    airings = [(chans[i], {"title": f"Programme {i} at night", "description": "",
                           "start_timestamp": now + 60, "stop_timestamp": now + 3600})
               for i in range(0, 50_000, 5)]
    cat = mw_palette.PaletteCatalogue(("k",), chans, {"1": "Sweden"}, [str(i + 1) for i in range(50_000)],
                                      airings)
    on_window: list[int] = []
    search = pm.Index.search
    ours = [False]                    # the test's own reference match, not the window's
    most = getattr(mw_palette, "MATCH_HERE", 5000)

    def watched(self, *a, **k):
        if (not ours[0] and threading.current_thread() is threading.main_thread()
                and len(self) > most):
            on_window.append(len(self))
        return search(self, *a, **k)
    monkeypatch.setattr(pm.Index, "search", watched)

    class Host(QWidget, mw_palette._PaletteMixin):
        pass

    host = Host()
    host.resize(1000, 700)
    played = []
    host.tune_from_guide = lambda ch: played.append(ch["name"])
    act = Entry("actions", "View", "Stats for nerds", payload=("call", lambda: None))
    host._pal_actions = [act]
    host._pal_action_index = pm.Index([act.text])
    host._pal_catalogue = cat
    pal = CommandPalette(host, host._palette_search, host._palette_run)
    host._palette = pal
    host.show()
    pal.open()

    def settled(secs=10.0):
        end = time.monotonic() + secs
        while pal._pending and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)
        app.processEvents()
        return not pal._pending

    def expected(q):
        ours[0] = True
        try:
            hits, titles = mw_palette._channel_hits(cat, q, pm.words(q))
        finally:
            ours[0] = False
        return ([chans[i]["name"] for i in hits]
                + [f"{airings[i][1]['title']}" for i in titles][:mw_palette.LIMITS["programmes"]])

    for q in ("s", "strom", "kanal 4999", "night 12"):
        pal.field.setText(q)
        pal.refresh()
        assert on_window == [], f"matched on the window's thread: {on_window}"
        assert not pal.none.isVisibleTo(pal), "nothing found is not said while it looks"
        assert settled(), q
        got = [e.text.split(" · ")[0] for e in pal.entries
               if e.group == "channels" and e.payload[0] != "search_all"]
        assert got == expected(q), (q, got)
        # Last, the Search page with the query, across every source (a
        # query of one letter would find nothing there).
        last = pal.entries[-1].payload
        assert (last == ("search_all", q)) == (len(q) > 1), (q, last)
    assert on_window == []

    # Typed on before the answer came: only the newest query's rows go in.
    pal.field.setText("sport")
    pal.refresh()
    pal.field.setText("nyheter")
    pal.refresh()
    assert settled()
    names = [e.text for e in pal.entries if e.group == "channels" and e.payload[0] == "channel"]
    assert names and all("Nyheter" in n for n in names), names

    # Enter right after typing: run once it is found, the channel typed.
    pal.field.clear()
    QTest.keyClicks(pal.field, "kanal 49999")
    QTest.keyClick(pal.field, Qt.Key.Key_Return)
    end = time.monotonic() + 10
    while not played and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    assert played == ["Kanal 49999 Sport HD"], played
    assert on_window == []
    host.close()


def test_a_click_beside_it_closes_it(app):
    from PyQt6.QtCore import QPoint, Qt
    from PyQt6.QtTest import QTest
    host, pal, _ran = _overlay()
    pal.open()
    QTest.mouseClick(pal, Qt.MouseButton.LeftButton, pos=QPoint(5, host.height() - 5))
    assert not pal.isVisible()
    host.close()


def test_its_keys_are_shown_as_the_system_writes_them(app):
    from dopeiptv.ui.command_palette import key_parts
    if sys.platform != "darwin":
        assert key_parts("Ctrl+Shift+N") == ["Ctrl", "Shift", "N"]
        assert key_parts("Ctrl++") == ["Ctrl", "+"]
    assert key_parts("") == []


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_a_menu_entry_is_named_by_the_menu_it_is_in(app, monkeypatch, platform):
    """An entry says which menu it is in. On macOS About, Settings, Quit
    and the supporter's entries are in the application menu, named like the
    app - not in Help or File, where they are on Linux and Windows."""
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QWidget

    from dopeiptv import APP_NAME
    from dopeiptv.i18n import tr
    from dopeiptv.ui import app_menu, mw_palette

    monkeypatch.setattr(sys, "platform", platform)

    class Host(QWidget, mw_palette._PaletteMixin):
        pass

    host = Host()
    host.shortcut_keys = lambda: dict(shortcut_keys.DEFAULTS)
    host._shortcut_callbacks = lambda: {sid: (lambda: None) for sid, *_ in shortcut_keys.ACTIONS}
    host._player_up = lambda: False
    host.can_cast_playing = lambda: True
    names = [n for _g, _where, group in app_menu.GROUPS for n in group]
    host._menu_actions = {n: QAction(f"Menu entry {n}", host) for n in names}
    kinds = {e.payload[1].text(): e.kind for e in host._palette_action_entries()
             if e.payload[0] == "action"}
    kinds = {text.removeprefix("Menu entry "): kind for text, kind in kinds.items()}
    assert set(kinds) == set(names)
    mac = platform == "darwin"
    for name in ("about", "supporter", "donate"):
        assert kinds[name] == (APP_NAME if mac else tr("menu_help")), name
    for name in ("settings", "quit"):
        assert kinds[name] == (APP_NAME if mac else tr("menu_file")), name
    # Everything else is where it is on every system.
    assert kinds["feedback"] == kinds["website"] == tr("menu_help")
    assert kinds["open_video"] == tr("menu_file")
    assert kinds["multiview"] == tr("menu_view") and kinds["stats"] == tr("nav_history")
    host.deleteLater()



def _catalogue_host(client, source="p1"):
    """A window stand-in with the palette's catalogue code and nothing else."""
    from types import SimpleNamespace

    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui import mw_palette

    class Host(QWidget, mw_palette._PaletteMixin):
        pass

    host = Host()
    host.client = client
    host.xmltv = None
    host.pool = QThreadPool()
    host.pool.setMaxThreadCount(4)
    host.parental = SimpleNamespace(session_unlocked=False)
    host._catalog_filter = lambda mode: (lambda it: True)
    host.playlist_store = SimpleNamespace(active_id=source, get=lambda i: {"name": "Demo"})
    host._palette = None
    host._chno_pid = lambda: source
    return host


def _until(app, cond, secs=5.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return cond()


def test_the_series_never_hold_up_the_channels_and_are_asked_for_once(app):
    """The palette finds series too. Their list - a provider's whole
    catalogue, a portal's two hundred pages - is asked for in a job of its
    own after the channels, at most once per source in a session, and never
    of a portal (slow_links): there only what the client holds already."""
    import threading

    gate = threading.Event()
    asked = []

    class Client:
        def live_streams(self, cat=None):
            return [{"name": "Sintel", "stream_id": 1, "category_id": "1"}]

        def live_categories(self):
            return [{"category_id": "1", "category_name": "Films"}]

        def series_list(self, cat=None):
            asked.append(cat)
            gate.wait(5)
            return [{"series_id": 7, "name": "Show Seven", "category_id": "2"}]

        def series_categories(self):
            return [{"category_id": "2", "category_name": "Drama"}]

    host = _catalogue_host(Client())
    try:
        host._palette_load_catalogue()
        # The channels are in while the series list is still on its way.
        assert _until(app, lambda: getattr(host, "_pal_catalogue", None) is not None, 2.0), \
            "the channels waited for the series"
        assert [c["name"] for c in host._pal_catalogue.chans] == ["Sintel"]
        gate.set()
        assert _until(app, lambda: [s["name"] for s in host._pal_catalogue.series] == ["Show Seven"])
        assert host._pal_catalogue.series_cats == {"2": "Drama"}
        assert [c["name"] for c in host._pal_catalogue.chans] == ["Sintel"]
        # Opened again (and again): never asked for again this session.
        for _ in range(3):
            host._palette_load_catalogue()
            assert _until(app, lambda: host._pal_catalogue is not None
                          and [s["name"] for s in host._pal_catalogue.series] == ["Show Seven"])
        host.pool.waitForDone(3000)
        app.processEvents()
        assert asked == [None], asked
    finally:
        gate.set()
        host.pool.waitForDone(3000)
        host.close()

    # A portal: its series list is never asked for by the palette - only
    # what it holds already is looked through.
    held: list = []

    class Portal(Client):
        slow_links = True

        def held_series(self):
            return (list(held[0]), [{"category_id": "2", "category_name": "Drama"}]) if held else None

    asked.clear()
    gate.set()
    host = _catalogue_host(Portal(), source="p2")
    try:
        host._palette_load_catalogue()
        assert _until(app, lambda: getattr(host, "_pal_catalogue", None) is not None)
        host.pool.waitForDone(3000)
        app.processEvents()
        assert host._pal_catalogue.series == [] and asked == [], asked
        # Its list loaded meanwhile (the Series section): found from then on.
        held.append([{"series_id": 9, "name": "Show Nine", "category_id": "2"}])
        host._palette_load_catalogue()
        assert _until(app, lambda: host._pal_catalogue is not None
                      and [s["name"] for s in host._pal_catalogue.series] == ["Show Nine"])
        host.pool.waitForDone(3000)
        assert asked == [], asked
    finally:
        host.pool.waitForDone(3000)
        host.close()

# -- the window: the real menu, Settings, channels; stand-ins where it would play --

_WINDOW = r"""
import json, os, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("nerd_enabled", "false")
from dopeiptv.core import nerd, privacy
nerd.install(s)
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell

w = MainWindow(DemoClient(), s, PlaylistStore(s))
now = time.time()


def airings(items, start, stop):
    # The guide in memory, as it answers: one programme on Sintel in an hour.
    ch = next(c for c in items if c.get("name") == "Sintel")
    return [(ch, {"title": "Nattsändning: Musikvideor", "description": "Videos all night",
                  "start_timestamp": now + 3600, "stop_timestamp": now + 7200})]


w.xmltv.airings = airings
w.show()
w.activateWindow()
QTest.qWaitForWindowActive(w, 3000)
w.listw.setFocus()
app.processEvents()


def wait(cond, secs=8.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def palette():
    pal = getattr(w, "_palette", None)
    return pal if pal is not None and pal.isVisible() else None


def ask(text):
    pal = palette()
    pal.field.setText(text)
    pal.refresh()
    return pal.entries


def choose(pal, pred):
    rows = [i for i in range(pal.list.count()) if pal._entry_at(i) is not None]
    row = next(i for i in rows if pred(pal._entry_at(i)))
    pal.list.setCurrentRow(row)
    QTest.keyClick(pal.field, Qt.Key.Key_Return)
    app.processEvents()


CTRL = Qt.KeyboardModifier.ControlModifier
# -- Ctrl+K opens it, Esc closes it, the focus goes back.
QTest.keyClick(w, Qt.Key.Key_K, CTRL)
assert wait(lambda: palette() is not None), "Ctrl+K opens the palette"
pal = palette()
assert pal.field.hasFocus()
assert pal.entries and all(e.group == "actions" for e in pal.entries)
QTest.keyClick(pal.field, Qt.Key.Key_Escape)
assert wait(lambda: palette() is None) and getattr(w, "_palette", None) is None
assert w.listw.hasFocus(), app.focusWidget()

# -- ACTIONS: a menu entry, and a rebindable one with its key; Enter runs it.
QTest.keyClick(w, Qt.Key.Key_K, CTRL)
assert wait(lambda: palette() is not None)
assert wait(lambda: getattr(w, "_settings_index", None) is not None), "Settings indexed"
assert wait(lambda: getattr(w, "_pal_catalogue", None) is not None), "channels folded"
found = ask(tr("menu_multiview"))
assert any(e.group == "actions" and e.payload[0] == "action"
           and e.payload[1] is w._menu_actions["multiview"] for e in found), found
side = next(e for e in ask(tr("sc_sidebar")) if e.group == "actions")
assert side.keys == "Ctrl+B", side
was = w.side_btn.isChecked()
choose(palette(), lambda e: e is side)
assert wait(lambda: w.side_btn.isChecked() != was), "Enter ran the action"
assert palette() is None
w.side_btn.setChecked(was)
# A menu entry runs through its QAction.
act = w._menu_actions["stats"]
act.triggered.disconnect()
fired = []
act.triggered.connect(lambda *_a: fired.append("stats"))
QTest.keyClick(w, Qt.Key.Key_K, CTRL)
assert wait(lambda: palette() is not None)
ask(tr("stats_menu"))
choose(palette(), lambda e: e.payload[0] == "action" and e.payload[1] is act)
assert wait(lambda: fired == ["stats"]), fired

# -- SETTINGS: a row opens Settings on its page, lit; Nerd's only while it is on.
rows = w._settings_index
night = next(r for r in rows if r["name"] == tr("night_mode"))
QTest.keyClick(w, Qt.Key.Key_K, CTRL)
assert wait(lambda: palette() is not None)
got = [e for e in ask(night["name"]) if e.group == "settings"]
assert got and got[0].payload[1] is night and got[0].where == night["where"], got
seen = {}


def in_settings(dlg):
    shell = dlg.findChild(SettingsShell)
    seen["page"] = shell.current_key()
    lit = shell.found_row()
    seen["row"] = lit.label.text() if lit is not None else None
    seen["prop"] = bool(lit is not None and lit.property("found"))
    return 0


QDialog.exec = in_settings
choose(palette(), lambda e: e is got[0])
assert wait(lambda: "page" in seen), seen
assert seen == {"page": night["page"], "row": night["name"], "prop": True}, seen
# Settings opened: its rows read again, the same.
assert [(r["page"], r["name"]) for r in w._settings_index] == [(r["page"], r["name"]) for r in rows]
nerd_row = next(r for r in rows if r["gated"])
assert nerd_row["nerd"] and nerd_row["where"].startswith(tr("nerd_page"))


def is_nerd_row(e):
    return e.group == "settings" and (e.payload[1]["page"], e.payload[1]["name"]) == (
        nerd_row["page"], nerd_row["name"])


QTest.keyClick(w, Qt.Key.Key_K, CTRL)
assert wait(lambda: palette() is not None)
assert not any(is_nerd_row(e) for e in ask(nerd_row["name"]))
s.setValue("nerd_enabled", "true")
hit = [e for e in ask(nerd_row["name"]) if is_nerd_row(e)]
assert hit and hit[0].nerd and hit[0].kind == tr("nerd_page"), hit
s.setValue("nerd_enabled", "false")

# -- CHANNELS AND PROGRAMMES: by name or number, accents left out; a channel plays.
played = []
w.tune_from_guide = lambda ch: played.append(ch.get("name"))
chans = [e for e in ask("SINTEL") if e.group == "channels"]
assert [(e.kind, e.text) for e in chans[:-1]] == [(tr("pal_kind_channel"), "Sintel")], chans
# The last row: the Search page with what was typed, every source's.
assert chans[-1].payload == ("search_all", "SINTEL"), chans[-1]
assert chans[-1].text == tr("pal_search_all", query="SINTEL")
assert [e.text for e in ask("3") if e.group == "channels" and e.payload[0] == "channel"][:1] == ["Sintel"]
prog = next(e for e in ask("nattsandning musik") if e.group == "channels")
assert prog.payload[0] == "programme" and prog.kind == tr("pal_kind_programme")
assert "Sintel" in prog.where and tr("pal_record_remind") in prog.where
menu = w._palette_programme_menu(*prog.payload[1])
words = [a.text() for a in menu.actions()]
assert words[:2] == [tr("epg_play_channel"), tr("rec_record_programme")], words
assert len(words) == 2 or words[2] in (tr("reminder_add"), tr("reminder_remove")), words
ask("sintel")
choose(palette(), lambda e: e.payload[0] == "channel")
assert wait(lambda: played == ["Sintel"]), played

# -- Private viewing: what was searched is not kept.
QTest.keyClick(w, Qt.Key.Key_K, CTRL)
assert wait(lambda: palette() is not None)
assert palette().field.text() == "sintel"      # kept in memory for the session
privacy.set_private(True)
ask("secret")
QTest.keyClick(palette().field, Qt.Key.Key_Escape)
assert w._palette_last == ""
QTest.keyClick(w, Qt.Key.Key_K, CTRL)
assert wait(lambda: palette() is not None) and palette().field.text() == ""
QTest.keyClick(palette().field, Qt.Key.Key_Escape)
privacy.set_private(False)

# -- Its key is the user's: another one opens it, Ctrl+K no longer.
s.setValue("keyboard_shortcuts", json.dumps({"palette": "Ctrl+Shift+P"}))
w.apply_shortcuts()
assert w._shortcuts["palette"].key() == QKeySequence("Ctrl+Shift+P")
w.listw.setFocus()
QTest.keyClick(w, Qt.Key.Key_K, CTRL)
app.processEvents()
assert palette() is None, "the old key does nothing"
QTest.keyClick(w, Qt.Key.Key_P, CTRL | Qt.KeyboardModifier.ShiftModifier)
assert wait(lambda: palette() is not None), "the user's key opens it"
w.hide()
print("PALETTE_WINDOW_OK", flush=True)
os._exit(0)
"""


def test_the_palette_in_the_window():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "PALETTE_WINDOW_OK" in out.stdout, out.stdout[-2000:] + out.stderr[-4000:]
