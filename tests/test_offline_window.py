"""Offline mode in a real window (offscreen): the window opening with no
network shows what is downloaded - Movies, Series, a series' page with the
episodes not downloaded dimmed, Mine, Search -, TV, the guide and Radio say
they need the network, the strip says it with the sidebar full or folded
to the icon rail and right to left; a downloaded film and episode play
their files with "Offline" by the title, History keeping the source's
address; nothing is looked up or connected to on the internet; the network
back, the source logs in and loads as at the start; the switch by hand
from File's menu and the command palette; a kids profile sees only what
its sidecars prove allowed. And the app started with no network: its
window at once, the source never asked."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_COMMON = r"""
import json, os, socket, sys, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
tmp = tempfile.mkdtemp()
asked = []
_real_lookup, _real_connect = socket.getaddrinfo, socket.create_connection


def _local(host):
    return str(host).strip("[]") in ("127.0.0.1", "::1", "localhost", "") or str(host).startswith("127.")


def lookup(host, *a, **k):
    if not _local(host):
        asked.append(("lookup", str(host)))
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
    return _real_lookup(host, *a, **k)


def connect(addr, *a, **k):
    if not _local(addr[0]):
        asked.append(("connect", str(addr[0])))
        raise OSError(101, "Network is unreachable")
    return _real_connect(addr, *a, **k)


socket.getaddrinfo, socket.create_connection = lookup, connect
from dopeiptv.core import downloads as dl


def download(kind, sid, title, **info):
    sub = os.path.join(tmp, "Downloads", "Movies" if kind == "movie" else os.path.join(
        "Series", str(info.get("series_title") or "x")))
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, f"{title}.mp4")
    with open(path, "wb") as fh:
        fh.write(b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 16)
    side = {"format": 1, "kind": kind, "source": info.pop("source", "a1b2c3d4"),
            "stream_id": sid, "key": sid, "title": title, "added": 100, **info}
    with open(path + dl.SIDECAR, "w", encoding="utf-8") as fh:
        json.dump(side, fh)
    return path


FILM = download("movie", "11", "Hemma", year="2021", category_id="3", category_name="Familj",
                plot="Ett hem.", length=5400)
download("movie", "12", "Vuxen", category_id="18", category_name="XXX Adult")
download("movie", "13", "Okänd", category_id="4")
EP = download("episode", "501", "Show S01E01 - Ett", series_id="77", series_title="Show",
              season=1, episode=1, episode_title="Ett", series_category_id="7",
              series_category_name="Drama", length=1500)
download("movie", "21", "Annans", source="b2b2b2b2", category_id="3", category_name="Familj")
"""

_WINDOW = _COMMON + r"""
os.environ["DOPEIPTV_NETWORK"] = "disconnected"
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from dopeiptv.core import offline
offline.SETTLE_S = 0.3
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers import client as client_mod
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui import mw_mitt

s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue(dl.DIR_KEY, os.path.join(tmp, "Downloads"))
s.setValue("check_updates", "false")
store = PlaylistStore(s)
store.add({"id": "a1b2c3d4", "name": "Pirat", "kind": "xtream",
           "server": "http://provider.example.com", "username": "kund", "password": "hemligt"})
store.add({"id": "b2b2b2b2", "name": "Annan", "kind": "xtream",
           "server": "http://other.example.com", "username": "u", "password": "p"})
store.set_active("a1b2c3d4")
asked_source = []
client = DemoClient()
for name in ("live_categories", "live_streams", "vod_categories", "vod_streams",
             "series_categories", "series_list", "series_info", "authenticate"):
    setattr(client, name, (lambda n: lambda *a, **k: asked_source.append(n) or [])(name))
client.vod_url = lambda sid, ext=None: f"http://provider.example.com/movie/kund/hemligt/{sid}.mp4"
client.episode_url = lambda eid, ext=None: f"http://provider.example.com/series/kund/hemligt/{eid}.mp4"
w = mw.MainWindow(client, s, store)
if w.player is None:
    print("SKIP: no embedded player here")
    sys.exit(0)
w.show()
out = {}


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


def until(cond, secs=20.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        pump(3)
    return bool(cond())


plays = []


def play(url, title, *a, **k):
    plays.append(url)
    w.player.current_url = url
    return True


w.player.play = play
w.wake.acquire = lambda *a, **k: None
toasts = []
real_toast = w._show_toast
w._show_toast = lambda text, ms=0: (toasts.append(text), real_toast(text, ms))[1]
until(lambda: w.downloads.scanned)
pump(40)
out["start"] = {"on": offline.on(), "mode": offline.mode(), "why": offline.why(),
                "client": type(w.client).__name__, "strip": w._off_strip.isVisible(),
                "strip_text": w._off_label.text(), "checked": w._menu_actions["offline"].isChecked()}
# -- the places -------------------------------------------------------------------
w._show_place("films")
until(lambda: w.list_model.rowCount() > 0)
pump(20)
w.cat_list.setCurrentRow(0)                 # All
until(lambda: w.list_model.rowCount() >= 3)
pump(10)
out["films"] = sorted(w.list_model.item_at(i).get("name") for i in range(w.list_model.rowCount()))
out["film_cats"] = [w.cat_list.item(i).text() for i in range(w.cat_list.count())]
w._show_place("series")
until(lambda: w.list_model.rowCount() > 0)
pump(10)
out["series"] = [w.list_model.item_at(i).get("name") for i in range(w.list_model.rowCount())]
w._show_place("tv")
pump(30)
out["tv"] = {"note": w._off_note.isVisible(), "text": w._off_note.text(),
             "rows": w.list_model.rowCount()}
w._show_place("guide")
pump(10)
out["guide_place"] = w._place
w._show_place("radio")
pump(30)
page = getattr(w, "_rap_page", None)
out["radio"] = page.note.text() if page is not None else ""
w._show_place("films")
pump(10)
# -- the strip: full sidebar, the icon rail, right to left ------------------------
mid = w._mid_stack.parentWidget()
out["strip_full"] = [w._off_strip.isVisible(), w._off_strip.width() == mid.width()]
w._set_sidebar_collapsed(True)
pump(30)
out["strip_rail"] = [w._sidebar_collapsed, w._off_strip.isVisible(),
                     w._off_strip.width() == mid.width()]
w._set_sidebar_collapsed(False)
pump(10)
app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
pump(20)
out["strip_rtl"] = [w._off_strip.isVisible(), w._off_strip.isRightToLeft(),
                    w._off_btn.geometry().x() < w._off_label.geometry().x()]
app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
pump(10)
w.resize(520, 600)
pump(20)
out["strip_narrow"] = [w._off_strip.isVisible(), w._off_label.wordWrap(),
                       w._off_strip.width() <= w.width()]
w.resize(1240, 780)
pump(10)
# -- a downloaded film plays offline ---------------------------------------------------
row = next(r for r in w.client.vod_streams() if r.get("stream_id") == "11")
w._dl_start(w.client.vod_url("11"), "Hemma", None, "11", "movie", record=True, item=row)
pump(20)
out["film_play"] = {"url": plays[-1] if plays else None, "status": w.count_lbl.text(),
                    "history": [(e.get("_url"), e.get("_kind")) for e in w.history.items()]}
w._dl_detail_show(row, "movie")
out["film_detail"] = w.d_dl_label.text()
# -- a series' page offline --------------------------------------------------------
dl.keep_episodes(w._off_episodes, "a1b2c3d4", "77",
                 [{"id": "501", "season": 1, "episode_num": 1, "title": "Ett"},
                  {"id": "502", "season": 1, "episode_num": 2, "title": "Två"}])
series = w.client.series_list()[0]
w._open_series_page(series)
until(lambda: bool(getattr(w, "_sp_seasons", None)))
pump(10)
states, nxt = w._sp_compute()
eps = [e for s_ in w._sp_seasons for e in s_.episodes]
from dopeiptv.ui.series_cards import EpisodeDelegate, episode_a11y
from dopeiptv.core import series_view as sv
out["series_page"] = [(str(e.get("id")), states[sv.episode_key(e)].missing,
                       EpisodeDelegate._meta(e, states[sv.episode_key(e)])) for e in eps]
out["next_up"] = str((nxt.episode or {}).get("id"))
missing = next(e for e in eps if e.get("_dl_missing"))
before = len(plays)
out["missing_play"] = [w._play_series_episode(series, w._sp_seasons, missing), len(plays) - before,
                       toasts[-1] if toasts else ""]
there = next(e for e in eps if not e.get("_dl_missing"))
w._play_series_episode(series, w._sp_seasons, there)
pump(20)
out["episode_play"] = {"url": plays[-1], "status": w.count_lbl.text()}
w._close_series_page()
pump(5)
# -- Search offline --------------------------------------------------------------------
got = []
w._open_search_page()
pump(10)
page = w._search_page
orig = page.show_results
page.show_results = lambda results, keep_place=True: (got.append(results), orig(results, keep_place))[1]
w._sa_query("Hemma")
until(lambda: got and got[-1] is not None, 20)
pump(10)
w._sa_query("Annans")
until(lambda: len(got) > 1 and got[-1] is not None, 20)
res1, res2 = got[0], got[-1]
out["search"] = {"hemma": {g: [h.title for h in hs] for g, hs in res1.hits.items() if hs},
                 "annans": {g: [h.title for h in hs] for g, hs in res2.hits.items() if hs},
                 "partial": page.partial.text() if hasattr(page, "partial") else ""}
w._close_search_page()
# -- Mine offline ----------------------------------------------------------------------
w.history.add("http://provider.example.com/live/kund/hemligt/5.ts", "Kanal", None, 5, "live")
w.history.add("http://provider.example.com/movie/kund/hemligt/99.mp4", "Inte nere", None, 99,
              "movie", extra={"stream_id": 99, "name": "Inte nere"})
snap = w._mitt_snapshot()
data = mw_mitt._build(snap)
out["mine"] = {"offline": snap.offline is not None,
               "history": sorted(e.title for e in data.history),
               "live": [e.title for e in data.live]}
# -- nothing out ----------------------------------------------------------------------
out["asked"] = list(asked)
out["asked_source"] = list(asked_source)
# -- the switch by hand: File's menu and the palette ---------------------------------
from dopeiptv.ui.mw_palette import Entry
texts = [e.text for e in w._palette_action_entries()]
out["palette"] = tr("offline_menu") in texts
# -- the network back by itself: the source as at the start ---------------------------
logins = []


class Back(DemoClient):
    def authenticate(self):
        logins.append(1)
        return {"user_info": {"exp_date": None}}


client_mod.make_client = lambda pl: Back()
w._off_said("online")
until(lambda: not offline.on(), 10)
pump(30)
until(lambda: logins, 10)
out["back"] = {"on": offline.on(), "client": type(w.client).__name__, "logins": len(logins),
               "strip": w._off_strip.isVisible(), "note": w._off_note.isVisible() if w._off_note else False,
               "checked": w._menu_actions["offline"].isChecked(),
               "said": [t for t in toasts if t == tr("offline_note_off")]}
act = w._menu_actions["offline"]
act.trigger()
pump(20)
out["by_hand"] = {"on": offline.on(), "mode": offline.mode(), "client": type(w.client).__name__,
                  "strip": w._off_strip.isVisible(), "checked": act.isChecked()}
w._off_said("disconnected")
w._off_said("online")
pump(60)
out["hand_stays"] = offline.mode()
act.trigger()
pump(20)
out["hand_off"] = [offline.on(), type(w.client).__name__, w._off_strip.isVisible()]
print("RESULT" + json.dumps(out), flush=True)
os._exit(0)
"""

_KIDS = _COMMON + r"""
os.environ["DOPEIPTV_NETWORK"] = "disconnected"
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue(dl.DIR_KEY, os.path.join(tmp, "Downloads"))
s.setValue("check_updates", "false")
store = PlaylistStore(s)
store.add({"id": "a1b2c3d4", "name": "Pirat", "kind": "xtream",
           "server": "http://provider.example.com", "username": "k", "password": "h"})
store.set_active("a1b2c3d4")
w = mw.MainWindow(DemoClient(), s, store)
w.show()
import time


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


end = time.monotonic() + 20
while not w.downloads.scanned and time.monotonic() < end:
    pump(3)
pump(20)
out = {"grown_up": sorted(i.title for i in w._dl_visible())}
w._profile_kids = lambda: True
w._dl_visible()                         # the categories read on the pool
end = time.monotonic() + 10
while w.pool.activeThreadCount() and time.monotonic() < end:
    pump(3)
pump(10)
out["kids"] = sorted(i.title for i in w._dl_visible())
keep = w._catalog_filter("vod")
out["kids_rows"] = sorted(r["name"] for r in w.client.vod_streams() if keep(r)
                          and w._kids_allows(r, "vod"))
out["asked"] = list(asked)
print("RESULT" + json.dumps(out), flush=True)
os._exit(0)
"""

_START = _COMMON + r"""
os.environ["DOPEIPTV_NETWORK"] = "disconnected"
sys.argv = ["dopeiptv"]
from PyQt6.QtCore import QSettings, QTimer
from dopeiptv import ORG
from dopeiptv.core import offline, profiles
from dopeiptv.core.stores import PlaylistStore
st = profiles.ProfileSettings(ORG, ORG)
st.setValue(dl.DIR_KEY, os.path.join(tmp, "Downloads"))
st.setValue("check_updates", "false")
store = PlaylistStore(st)
store.add({"id": "a1b2c3d4", "name": "Pirat", "kind": "xtream",
           "server": "http://provider.example.com", "username": "kund", "password": "hemligt"})
store.set_active("a1b2c3d4")
st.sync()
from dopeiptv.ui import main_window as mw
t0 = time.monotonic()
seen = {}
real_show = mw.MainWindow.show


def show(self):
    seen["shown_after"] = time.monotonic() - t0
    real_show(self)

    def report():
        seen["on"] = offline.on()
        seen["client"] = type(self.client).__name__
        seen["strip"] = self._off_strip.isVisible()
        seen["films"] = sorted(r["name"] for r in self.client.vod_streams())
        seen["asked"] = list(asked)
        print("RESULT" + json.dumps(seen), flush=True)
        os._exit(0)
    QTimer.singleShot(4000, report)


mw.MainWindow.show = show
from dopeiptv import app as app_mod
app_mod.main()
"""


_WELCOME = _COMMON + r"""
os.environ["DOPEIPTV_NETWORK"] = "disconnected"
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from dopeiptv.core import offline
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import OfflineClient
from dopeiptv.ui import main_window as mw
s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue(dl.DIR_KEY, os.path.join(tmp, "Downloads"))
s.setValue("check_updates", "false")
w = mw.MainWindow(OfflineClient(), s, PlaylistStore(s))
w.show()
w.show_welcome()


def pump(n=10):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


pump(40)
out = {"on": offline.on(), "client": type(w.client).__name__,
       "welcome": w._welcome is not None and w._welcome.isVisible(),
       "strip_under_welcome": w._off_strip.isVisible()}
failed = []
w._welcome.connect_failed = lambda text: failed.append(text)
t0 = time.monotonic()
w._wizard_connect("http://provider.example.com", "kund", "hemligt")
out["connect"] = {"said": failed, "secs": time.monotonic() - t0, "trying": w._wtry is not None,
                  "plain": tr("onb_err_no_network")}
w._welcome.hide()
pump(20)
out["strip_after_welcome"] = w._off_strip.isVisible()
w.start_demo()
pump(40)
out["demo"] = {"client": type(w.client).__name__, "live": w.client.live_streams(),
               "films": w.client.vod_streams(), "series": w.client.series_list(),
               "dl": [i.title for i in w._dl_visible() if i.source == ""]}
out["asked"] = list(asked)
print("RESULT" + json.dumps(out), flush=True)
os._exit(0)
"""


def _run(script: str, home) -> dict:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                "XDG_RUNTIME_DIR"):
        env[var] = str(home / var.lower())
        (home / var.lower()).mkdir(mode=0o700, exist_ok=True)
    out = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=300)
    if out.stdout.startswith("SKIP"):
        pytest.skip(out.stdout.strip())
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, out.stdout[-2000:] + out.stderr[-4000:]
    return json.loads(line[6:])


@pytest.fixture(scope="module")
def seen(tmp_path_factory):
    pytest.importorskip("PyQt6.QtWidgets")
    return _run(_WINDOW, tmp_path_factory.mktemp("offline-window"))


def test_the_window_opens_offline_with_what_is_downloaded(seen):
    start = seen["start"]
    assert start["on"] and start["mode"] == "itself" and start["why"] == "no_network"
    assert start["client"] == "DownloadedClient" and start["checked"]
    assert start["strip"] and start["strip_text"] == "Offline – only what is downloaded is shown"
    # Only the source in use's: another source's download is under Downloaded.
    assert seen["films"] == ["Hemma", "Okänd", "Vuxen"]
    assert "Familj" in seen["film_cats"] and "XXX Adult" in seen["film_cats"]
    assert "Downloaded" in seen["film_cats"]        # the one whose sidecar names no category
    assert seen["series"] == ["Show"]


def test_tv_the_guide_and_radio_say_they_need_the_network(seen):
    assert seen["tv"]["note"] and seen["tv"]["rows"] == 0
    assert seen["tv"]["text"].startswith("TV needs the network")
    assert seen["guide_place"] == "tv"
    assert seen["radio"] == "Radio needs the network."


def test_the_strip_on_the_full_sidebar_the_rail_right_to_left_and_narrow(seen):
    assert seen["strip_full"] == [True, True]
    assert seen["strip_rail"] == [True, True, True]
    visible, rtl, mirrored = seen["strip_rtl"]
    assert visible and rtl and mirrored
    assert seen["strip_narrow"] == [True, True, True]


def test_a_downloaded_film_plays_offline_marked_offline(seen):
    play = seen["film_play"]
    assert play["url"].endswith(os.path.join("Movies", "Hemma.mp4")) and os.path.isabs(play["url"])
    assert play["status"].endswith("· Offline") and "Hemma" in play["status"]
    # History keeps the source's address, never the file's path.
    assert ["http://provider.example.com/movie/kund/hemligt/11.mp4", "movie"] in play["history"]
    assert all(".mp4" not in str(u) or str(u).startswith("http") for u, _k in play["history"])
    assert seen["film_detail"].startswith("Offline · Downloaded · ")


def test_a_series_page_offline_dims_what_is_not_downloaded(seen):
    assert seen["series_page"] == [["501", False, "25 min"], ["502", True, "Not downloaded"]]
    assert seen["next_up"] == "501"
    ok, started, said = seen["missing_play"]
    assert ok is False and started == 0 and "not downloaded" in said
    play = seen["episode_play"]
    assert play["url"].endswith("Show S01E01 - Ett.mp4") and play["status"].endswith("· Offline")


def test_search_offline_searches_what_is_downloaded(seen):
    found = seen["search"]
    assert found["hemma"] == {"movies": ["Hemma"]}
    assert found["annans"] == {"movies": ["Annans"]}       # another source's download too
    assert found["partial"] == "Offline – searching what is downloaded"


def test_mine_offline_shows_only_what_plays(seen):
    mine = seen["mine"]
    # The film and the episode played here offline; not the channel, nor
    # the film that is not downloaded.
    assert mine["offline"] and mine["history"] == ["Hemma", "Show · S1 E1 · Ett"]
    assert mine["live"] == []


def test_nothing_was_looked_up_or_connected_to_offline(seen):
    assert seen["asked"] == []
    assert seen["asked_source"] == []                      # the provider never asked


def test_the_network_back_logs_the_source_in_as_at_the_start(seen):
    back = seen["back"]
    assert back["on"] is False and back["client"] == "Back" and back["logins"] == 1
    assert not back["strip"] and not back["note"] and not back["checked"]
    assert back["said"] == ["Back online"]


def test_the_switch_by_hand_from_the_menu_and_the_palette(seen):
    assert seen["palette"] is True
    hand = seen["by_hand"]
    assert hand == {"on": True, "mode": "hand", "client": "DownloadedClient", "strip": True,
                    "checked": True}
    assert seen["hand_stays"] == "hand"                 # the network's word changes nothing
    on, client, strip = seen["hand_off"]
    assert on is False and client == "Back" and strip is False


def test_a_kids_profile_offline_sees_only_what_its_sidecars_prove(tmp_path_factory):
    pytest.importorskip("PyQt6.QtWidgets")
    got = _run(_KIDS, tmp_path_factory.mktemp("offline-kids"))
    # A grown-up: every source's, as the Downloaded place shows them.
    assert got["grown_up"] == ["Annans", "Hemma", "Okänd", "Show S01E01 - Ett", "Vuxen"]
    # Familj and Drama allowed; XXX Adult not; a category the sidecar does
    # not name, not (it fails closed); another source's, not.
    assert got["kids"] == ["Hemma", "Show S01E01 - Ett"]
    assert got["kids_rows"] == ["Hemma"]
    assert got["asked"] == []


def test_the_app_started_with_no_network_opens_at_once_and_asks_nobody(tmp_path_factory):
    pytest.importorskip("PyQt6.QtWidgets")
    got = _run(_START, tmp_path_factory.mktemp("offline-start"))
    assert got["shown_after"] < 30
    assert got["on"] and got["client"] == "DownloadedClient" and got["strip"]
    assert got["films"] == ["Hemma", "Okänd", "Vuxen"]
    assert got["asked"] == []


def test_offline_with_no_source_the_guide_shows_and_its_connect_says_so(tmp_path_factory):
    pytest.importorskip("PyQt6.QtWidgets")
    got = _run(_WELCOME, tmp_path_factory.mktemp("offline-welcome"))
    assert got["on"] and got["client"] == "OfflineClient"      # explore mode as it is
    assert got["welcome"] and got["strip_under_welcome"] is False
    connect = got["connect"]
    assert connect["said"] == [connect["plain"]] and connect["secs"] < 2 and not connect["trying"]
    assert got["strip_after_welcome"] is True
    # The demo needs the network: none of it listed, nothing of it downloaded.
    demo = got["demo"]
    assert demo["client"] == "DownloadedClient"
    assert demo["live"] == [] and demo["films"] == [] and demo["series"] == [] and demo["dl"] == []
    assert got["asked"] == []
