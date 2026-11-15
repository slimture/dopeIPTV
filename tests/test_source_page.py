"""A source's own page in Settings (ui/source_page.py): each kind's fields on
the tab they belong to, the Nerd rows (the Network tab whole) only while
Nerd is on, each row saying whether it is the source's own or the shared
one, a change stored a moment later just as the old Edit dialog stored it
on OK - and one that cannot be stored refused with what it says. In the
real Settings window (a child process, as test_settings_window): a search
finds a source by its name, its row opens its page in place of Sources,
Back returns, and the source menu opens Settings on it."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QSettings  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from dopeiptv.core import nerd  # noqa: E402
from dopeiptv.i18n import tr  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class Store:
    """The sources as the page uses them: get, and update (which merges)."""

    def __init__(self, *sources: dict) -> None:
        self.items = [dict(s) for s in sources]
        self.updates: list[dict] = []

    def get(self, pid):
        return next((p for p in self.items if p.get("id") == pid), None)

    def update(self, pid, **fields) -> None:
        self.updates.append(fields)
        self.get(pid).update(fields)


def source_page(playlist: dict, nerd_on: bool = True, **kw):
    """*playlist*'s page, as Settings opens it (with Nerd on or off)."""
    from dopeiptv.ui.source_page import SourcePage
    pl = dict(playlist)
    pl.setdefault("id", "s1")
    store = kw.pop("store", None) or Store(pl)
    return SourcePage(pl, store=store, nerd_on=nerd_on, **kw)


def tab_of(page, widget) -> str | None:
    """The tab *widget* is on (None: on none)."""
    w = widget
    while w is not None:
        for key, body in page.bodies.items():
            if w is body:
                return key
        w = w.parentWidget()
    return None


def shown(page, widget) -> bool:
    """Whether *widget*'s row shows on its tab (that tab open or not)."""
    return widget.isVisibleTo(page.bodies[tab_of(page, widget)])


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def settings(tmp_path, app, monkeypatch):
    from dopeiptv.ui.nerd_check_line import CheckLine
    monkeypatch.setattr(CheckLine, "show_problem", lambda self: None)
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    nerd.install(s)
    yield s
    nerd.install(None)


def test_each_kinds_fields_land_on_their_tab(settings):
    d = source_page({"name": "IPTV", "kind": "xtream", "server": "http://h:8080",
                     "username": "u", "password": "p"})
    p, net = d.nerd_panel, d.nerd_panel.net
    where = {
        "conn": [d.name, d.kind, d.server, d.user, d.pw],
        "net": [p.ua, p.referer, p.headers, p.proxy, net.dns, net.family, p.mirrors_box,
                net.cert_buttons],
        "streams": [p.udpxy, p.cu_mode, p.cu_template, p.rewrite],
        "guide": [d.epg_url, p.epg_shift],
        "adv": [d.refresh],
    }
    for tab, widgets in where.items():
        for w in widgets:
            assert tab_of(d, w) == tab, (tab, type(w).__name__, tab_of(d, w))
            assert shown(d, w), (tab, type(w).__name__)
    for w in (d.mac, p.port, p.profile, p.transcode, p.ms_quality, d.signin):
        assert not shown(d, w), type(w).__name__
    assert tab_of(d, p.port) == "streams" and tab_of(d, d.mac) == "conn"
    assert tab_of(d, p.kind_switches["enigma2"]["radio"]) == "adv"
    assert tab_of(d, p.kind_switches["enigma2"]["picons"]) == "guide"
    assert tab_of(d, p.kind_switches["enigma2"]["transcode_more"]) == "streams"
    assert tab_of(d, p.kind_switches["hdhomerun"]["cloud_guide"]) == "guide"
    # A box: its stream port and transcoding, its switches; no backup addresses.
    d.kind.setCurrentIndex(d.kind.findData("enigma2"))
    assert shown(d, p.port) and shown(d, p.e2_transcode) and not shown(d, p.mirrors_box)
    assert shown(d, p.kind_switches["enigma2"]["radio"]) and shown(d, d.finder.row)
    assert not shown(d, p.kind_switches["tvheadend"]["recordings"])
    assert not shown(d, p.cu_template)
    assert d.server_row.label.text() == tr("playlist_box_address")
    d.kind.setCurrentIndex(d.kind.findData("hdhomerun"))
    assert shown(d, p.transcode) and shown(d, p.kind_switches["hdhomerun"]["cloud_guide"])
    assert not shown(d, p.port)
    d.kind.setCurrentIndex(d.kind.findData("tvheadend"))
    assert shown(d, p.profile)
    d.kind.setCurrentIndex(d.kind.findData("jellyfin"))
    assert shown(d, p.ms_quality) and shown(d, d.signin)
    d.kind.setCurrentIndex(d.kind.findData("m3u"))
    assert not shown(d, d.user) and shown(d, p.mirrors_box) and shown(d, p.cu_template)
    assert d.server_row.label.text() == tr("playlist_m3u_url")
    if d.kind.findData("stalker") >= 0:
        d.kind.setCurrentIndex(d.kind.findData("stalker"))
        assert shown(d, d.mac) and not shown(d, d.user) and not shown(d, p.cu_template)


def test_nerds_rows_only_while_nerd_is_on(settings):
    pl = {"name": "Vu", "kind": "enigma2", "server": "http://10.0.0.2",
          "options": {"stream_port": "17999", "keep_me": "x"}}
    off = source_page(pl, nerd_on=False)
    assert off.nerd_panel is None and off.test_btn is None
    assert off.tabs["net"].isHidden() and not off.tabs["streams"].isHidden()
    off.show_tab("net")
    assert off.current_tab() == "conn"
    assert len(off._cards) == 4                  # one everyday card a tab, no Nerd ones
    # What the source had rides along unchanged.
    assert off.values()["options"] == {"stream_port": "17999", "keep_me": "x"}
    on = source_page(pl)
    assert on.nerd_panel is not None and on.nerd_panel.port.value() == 17999
    assert not on.tabs["net"].isHidden() and on.test_btn is not None
    on.nerd_panel.port.setValue(8001)
    assert on.values()["options"] == {"keep_me": "x"}


def test_each_row_says_own_or_shared(settings):
    d = source_page({"name": "IPTV", "kind": "xtream", "server": "http://h", "username": "u",
                     "password": "p", "options": {"dns": "cloudflare"}})
    marks = {id(row): (badge, row) for badge, row, _fn in d._origins}
    p = d.nerd_panel

    def mark(widget):
        w = widget
        while id(w) not in marks:
            w = w.parentWidget()
        badge, row = marks[id(w)]
        return badge.text(), row.hint_label.text() if row.hint_label.isVisibleTo(row) else ""
    shared = tr("src_follows", where=f"{tr('nerd_page')} › {tr('nerd_pg_net')}")
    assert mark(p.ua) == (tr("src_shared"), shared)
    assert mark(p.net.dns)[0] == tr("src_own")
    assert mark(p.net.cert_buttons)[0] == tr("src_system")
    p.ua.setCurrentIndex(p.ua.findData("kodi"))
    assert mark(p.ua) == (tr("src_own"), "")
    p.net.dns.setCurrentIndex(p.net.dns.findData(""))
    assert mark(p.net.dns) == (tr("src_shared"), shared)
    assert mark(p.mirrors_box)[0] == ""
    p.mirrors_box.setPlainText("http://b.example.com")
    assert mark(p.mirrors_box)[0] == tr("src_own")


def test_a_change_is_stored_as_the_old_dialog_stored_it(settings, tmp_path):
    from dopeiptv.core.stores import PlaylistStore
    store = PlaylistStore(QSettings(str(tmp_path / "pl.ini"), QSettings.Format.IniFormat))
    pl = store.add({"name": "IPTV", "kind": "xtream", "server": "http://h:8080", "username": "u",
                    "password": "p", "epg_url": "", "refresh": "never", "mac": "",
                    "options": {"keep_me": "x", "user_agent": "vlc"}})
    left = []
    d = source_page(dict(pl), store=store, left=lambda pid, ch: left.append((pid, ch)))
    assert d.commit() and store.get(pl["id"]) == pl          # untouched: nothing written
    d.name.setText("My TV")
    d.refresh.setCurrentIndex(d.refresh.findData("24h"))
    d.nerd_panel.ua.setCurrentIndex(d.nerd_panel.ua.findData("kodi"))
    d.nerd_panel.cu_template.setText("?utc={utc}")
    assert d._live.isActive()                                # stored a moment later
    d.leave()
    want = {"id": pl["id"], "name": "My TV", "kind": "xtream", "server": "http://h:8080",
            "username": "u", "password": "p", "epg_url": "", "refresh": "24h", "mac": "",
            "options": {"keep_me": "x", "user_agent": "kodi", "catchup_template": "?utc={utc}"}}
    assert store.get(pl["id"]) == want
    assert left == [(pl["id"], True)]
    assert PlaylistStore(store.settings).get(pl["id"]) == want     # on disk too
    # Every option back at its default: none kept (the store merges, so
    # leaving the key out kept the old ones).
    pl2 = store.add({"name": "Two", "kind": "m3u", "server": "http://h/x.m3u",
                     "options": {"user_agent": "kodi"}})
    d2 = source_page(dict(pl2), store=store)
    d2.nerd_panel.ua.setCurrentIndex(0)
    assert "options" not in d2.values()
    assert d2.commit() and store.get(pl2["id"])["options"] == {}


def test_what_cannot_be_stored_is_refused_and_said(settings, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox

    from dopeiptv.core.nerd_check import FAIL
    from dopeiptv.core.url_rewrite import Rule, parse
    said = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: said.append(a[2]))
    store = Store({"id": "s1", "name": "TV", "kind": "m3u", "server": "http://h/tv.m3u"})
    backs = []
    d = source_page(store.get("s1"), store=store, back=lambda: backs.append(1))
    row = d.nerd_panel.rewrite.add()
    row.find.setText("(unclosed")
    row.regex.setChecked(True)
    assert not d.commit() and not store.updates
    assert d.problem_label.isVisibleTo(d) and d.problem_label.text().startswith("✗")
    d.go_back()
    assert said and not backs and d.current_tab() == "streams"
    assert d.nerd_panel.rw_line.result.state == FAIL
    row.find.setText("(closed)")
    d.go_back()
    assert backs == [1]
    assert d.commit() and parse(store.get("s1")["options"]["rewrite"]) == [Rule("(closed)", "", True)]
    # A required field left empty: not stored, and leaving says so.
    d.server.setText("")
    said.clear()
    d.leave()
    assert store.get("s1")["server"] == "http://h/tv.m3u"
    assert said and tr("src_not_saved") in said[0]


_CHILD = r"""
import os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QPushButton
app = QApplication.instance() or QApplication([])
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", '[{"id": "a", "name": "Anna TV", "kind": "xtream", '
           '"server": "http://a.invalid", "username": "u", "password": "p"}, '
           '{"id": "b", "name": "Bo", "kind": "m3u", "server": "http://b.invalid/x.m3u"}]')
s.setValue("active_playlist", "a")
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import ReorderList, SettingsShell
from dopeiptv.ui.source_page import SourcePage
store = PlaylistStore(s)
w = MainWindow(DemoClient(), s, store)

def run(self):
    shell = self.findChild(SettingsShell)
    shell.search.setText("Anna TV")
    assert shell.search_hits() == ["playlists"], shell.search_hits()
    shell.search.setText("b.invalid")
    assert "playlists" in shell.search_hits(), shell.search_hits()
    shell.search.setText("")
    shell.show_page("playlists")
    lst = self.findChild(ReorderList)
    lst.open_requested.emit(lst.item(1))
    page = shell.sub
    assert isinstance(page, SourcePage) and shell.stack.currentWidget() is page
    assert page.name.text() == "Bo" and shell.current_key() == "playlists"
    assert page.crumb.text().startswith(tr("set_page_sources"))
    page.name.setText("Bo 2")
    page.crumb.click()                          # Back: stored, and gone
    assert shell.sub is None and shell.stack.currentWidget().key == "playlists"
    assert store.get("b")["name"] == "Bo 2"
    # Another page shown closes it too.
    lst.open_requested.emit(lst.item(0))
    assert shell.sub is not None
    shell.show_page("home")
    assert shell.sub is None
    return 0
QDialog.exec = run
w.open_settings()

# The source menu's entry opens Settings on the source in use.
menu = QMenu()
w._fill_playlist_menu(menu)
entry = [a for a in menu.actions() if a.text() == tr("src_settings_menu")]
assert entry, [a.text() for a in menu.actions()]
def on_page(self):
    shell = self.findChild(SettingsShell)
    assert isinstance(shell.sub, SourcePage) and shell.sub.name.text() == "Anna TV"
    return 0
QDialog.exec = on_page
entry[0].trigger()
print("SOURCE_PAGE_OK", flush=True)
"""


def test_in_the_settings_window(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(tmp_path))
    proc = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                          env=env, cwd=_REPO_ROOT, timeout=180)
    assert "SOURCE_PAGE_OK" in proc.stdout, f"stdout={proc.stdout!r}\nstderr={proc.stderr[-3000:]!r}"
