"""Downloads in a real window (offscreen), against a provider of our own on
this computer: a film downloaded from its menu shows Downloaded on its
Movies card, in the Downloaded place and under the poster; it plays its
file through the window's one playback path as that same film - its kind
and key, History keeping the source's address and never the file's path,
the VPN guard letting a file of this computer's play; an episode's card on
a series page says it too; a download counts as a connection and asks over
a full limit, and gives way to the player; a kids profile cannot download
what its lists do not show; private viewing refuses one and holds the one
running; a locked category's download is hidden while the lock is shut."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import json, os, sys, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from PyQt6.QtCore import QSettings
tmp = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tmp)
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv.core import downloads as dl
from dopeiptv.core import nerd, privacy
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw
from dopeiptv.ui.media_cards import Card

FILM = b"\x00\x00\x00\x20ftypisom" + bytes(range(256)) * 2400
EPISODE = b"\x00\x00\x00\x20ftypisom" + bytes(reversed(range(256))) * 1600
slow = threading.Event()
slow.set()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        data = EPISODE if "/series/" in self.path else FILM
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(data) - start))
        self.end_headers()
        body = data[start:]
        try:
            self.wfile.write(body[:64 * 1024])
            self.wfile.flush()
            slow.wait(30)
            self.wfile.write(body[64 * 1024:])
        except OSError:
            pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

s = QSettings(os.path.join(tmp, "s.ini"), QSettings.Format.IniFormat)
s.setValue(dl.DIR_KEY, os.path.join(tmp, "Downloads"))
# Nerd's VPN guard on and no VPN up: no stream plays - a file of this
# computer's does.
s.setValue("nerd_enabled", "true")
s.setValue("nerd_vpn_guard", "true")
s.setValue("nerd_vpn_iface", "dlnovpn9")
store = PlaylistStore(s)
store.add({"id": "a1b2c3d4", "name": "Pirat", "kind": "xtream", "server": "http://192.0.2.1",
           "username": "kund", "password": "hemligt"})
client = DemoClient()
client.vod_url = lambda sid, ext=None: f"http://192.0.2.1/movie/kund/hemligt/{sid}.{ext or 'mp4'}"
client.episode_url = lambda eid, ext=None: f"http://192.0.2.1/series/kund/hemligt/{eid}.{ext or 'mkv'}"
client.vod_categories = lambda: [{"category_id": "3", "category_name": "Familj"},
                                 {"category_id": "18", "category_name": "XXX Adult"}]
client.series_categories = lambda: [{"category_id": "7", "category_name": "Drama"}]
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


pump(20)
w._dl_born -= 1000                      # the app's own start is over
w.downloads.resolve = lambda job: (
    f"{base}/{'movie' if job.kind == 'movie' else 'series'}/kund/hemligt/{job.stream_id}.mp4",
    None)
asked = []
answers = []
w._choice_dialog = lambda title, body, options, dont_ask_setting=None: (
    asked.append(body), answers.pop(0) if answers else None)[1]
plays = []


def play(url, title, *a, **k):
    plays.append({"url": url, "title": title, "buffer_key": k.get("buffer_key")})
    w.player.current_url = url
    return True


w.player.play = play
w.wake.acquire = lambda *a, **k: None

film = {"name": "Hemma", "stream_id": 9001, "container_extension": "mp4", "category_id": "3",
        "year": "2021"}
# -- a film downloaded from its menu ------------------------------------------------
from PyQt6.QtWidgets import QMenu
m = QMenu()
w._dl_item_menu(m, film, "vod")
out["menu"] = [a.text() for a in m.actions() if a.text()]
[a for a in m.actions() if a.text() == "Download"][0].trigger()
out["queued"] = [j.title for j in w.downloads.jobs()]
until(lambda: w.downloads.find("a1b2c3d4", "movie", "9001") is not None)
item = w.downloads.find("a1b2c3d4", "movie", "9001")
out["file"] = os.path.relpath(item.path, tmp) if item else None
until(lambda: not w._dl_notice)
out["pill"] = list(w._dl_pills("movie", film))
card = Card(key="9001", title="Hemma", data=film)
w._pf_dress(card)
out["movies_card_pills"] = list(card.pills)
m = QMenu()
w._dl_item_menu(m, film, "vod")
out["menu_after"] = [a.text() for a in m.actions() if a.text()]
# -- under the poster -------------------------------------------------------------
w._dl_detail_show(film, "movie")
out["detail"] = [w.d_dl.isVisibleTo(w), w.d_dl_label.text(), w.d_dl_btn.text()]
# -- the place ---------------------------------------------------------------------
w._show_place("dl")
pump(10)
page = w._dl_page
cards = page.view.items()
for c in cards:
    page.view.dress(c)
out["place"] = {"shown": w._dl_showing(), "place": w._place,
                "lit": [k for k, b in w.nav_btns.items() if b.isChecked()],
                "cards": [(c.title, list(c.pills), bool(c.line)) for c in cards],
                "count": page.header.line_text() or page.header.count if hasattr(page.header, "count") else "",
                "note": page.note.text()}
# -- it plays its file, as that same film -------------------------------------------
started = []
real = w._start_playback


def spy(*a, **k):
    started.append({"url": a[0], "key": a[3], "kind": a[4], "record": k.get("record")})
    return real(*a, **k)


w._start_playback = spy
w._film_play(film)
pump(10)
lp = dict(w._last_playback or {})
out["play"] = {"started": started, "plays": plays[-1:], "key": w._playing_key,
               "group": w._playing_group, "last_url": lp.get("url"),
               "history": [(e.get("_url"), e.get("_key"), e.get("_kind")) for e in w.history.items()],
               "vpn_stream": w._vpn_guard_blocks("http://provider.example/live/kund/hemligt/1.ts"),
               "vpn_file": w._vpn_guard_blocks(item.path)}
w._start_playback = real
w.player.current_url = None
w._last_playback = None
w._playing_key = None
# -- an episode on its series page -----------------------------------------------
series = {"series_id": 77, "name": "Huset", "category_id": "7", "cover": ""}
ep = {"id": "501", "title": "Piloten", "season": 1, "episode_num": 2, "container_extension": "mkv",
      "info": {"duration_secs": 2700}}
client.series_info = lambda sid: {"episodes": {"1": [dict(ep)]}}
w._dl_series_all(series)
until(lambda: w.downloads.find("a1b2c3d4", "episode", "501") is not None)
eitem = w.downloads.find("a1b2c3d4", "episode", "501")
out["episode_file"] = os.path.relpath(eitem.path, tmp) if eitem else None
w._open_series_page(series)
until(lambda: bool(getattr(w, "_sp_seasons", None)))
states, _nxt = w._sp_compute()
out["episode_state"] = [bool(st.downloaded) for st in states.values()]
w._show_place("dl")
pump(5)
out["place_after"] = sorted(c.title for c in w._dl_page.view.items())
# -- a download counts as a connection, and asks over a full limit -----------------
client.max_connections = 1
slow.clear()
w.player.current_url = "http://192.0.2.1/live/kund/hemligt/1.ts"
w._last_playback = {"url": w.player.current_url, "kind": "live", "key": 1}
answers[:] = [1]                                   # When the connection is free
other = {"name": "Borta", "stream_id": 9002, "container_extension": "mp4", "category_id": "3"}
made = w._dl_queue_many("movie", [other])
w._dl_tick()
pump(20)
job = w.downloads.job(made[0].id)
out["asked"] = len(asked)
out["held"] = [job.state, w.downloads.holds.get(job.id), w.downloads.current() is None]
answers[:] = [0]                                   # Download now anyway
w.downloads.cancel(job.id)
pump(5)
made = w._dl_queue_many("movie", [other])
until(lambda: w.downloads.current() is not None)
out["anyway"] = [len(asked), [u.kind for u in w._conn_uses()]]
w.downloads.cancel(made[0].id)
until(lambda: w.downloads.current() is None)
# ...and gives way to the player when one more is not known to fit.
client.max_connections = None
w.player.current_url = None
w._last_playback = None
third = {"name": "Tredje", "stream_id": 9003, "container_extension": "mp4", "category_id": "3"}
made = w._dl_queue_many("movie", [third])
until(lambda: w.downloads.current() is not None)
before = len(plays)
ok = w._guard_stream_switch("http://192.0.2.1/live/kund/hemligt/1.ts", "Ett")
w.player.current_url = "http://192.0.2.1/live/kund/hemligt/1.ts"
w._last_playback = {"url": w.player.current_url, "kind": "live", "key": 1}
until(lambda: w.downloads.current() is None)
w._dl_tick()
pump(20)
out["gives_way"] = [ok, w.downloads.job(made[0].id).state, w.downloads.holds.get(made[0].id)]
w.player.current_url = None
w._last_playback = None
w._dl_tick()
until(lambda: w.downloads.current() is not None)
out["goes_on"] = w.downloads.current() is not None
# -- private viewing: refused, and the running one waits ----------------------------
w.set_private_viewing(True)
until(lambda: w.downloads.current() is None)
w._dl_tick()
out["private"] = [w.downloads.job(made[0].id).state, w.downloads.holds.get(made[0].id),
                  w._dl_queue_many("movie", [{"name": "X", "stream_id": 9100, "category_id": "3"}])]
w.set_private_viewing(False)
w.downloads.cancel(made[0].id)
slow.set()
until(lambda: w.downloads.current() is None)
# -- a kids profile: only what its lists show ---------------------------------------
w._profile_kids = lambda: True
adult = {"name": "Vuxen", "stream_id": 9200, "category_id": "18"}
w._dl_allowed("movie", adult)                       # the categories asked for, on the pool
w._dl_allowed("movie", film)
until(lambda: w.pool.activeThreadCount() == 0)
pump(10)
out["kids"] = {"adult": w._dl_allowed("movie", adult), "family": w._dl_allowed("movie", film),
               "queued_adult": len(w._dl_queue_many("movie", [adult])),
               "visible": sorted(i.title for i in w._dl_visible())}
m = QMenu()
w._dl_item_menu(m, adult, "vod")
out["kids"]["menu"] = [a.text() for a in m.actions() if a.text()]
w._profile_kids = lambda: False
# -- a locked category's download hidden while the lock is shut ---------------------
w.overrides.data.setdefault("vod", {})["3"] = {"locked": True}
out["locked"] = sorted(i.title for i in w._dl_visible())
w.parental.session_unlocked = True
out["unlocked"] = sorted(i.title for i in w._dl_visible())
# -- the sidecar names no address and no login ---------------------------------------
text = open(item.path + dl.SIDECAR, encoding="utf-8").read() + open(
    eitem.path + dl.SIDECAR, encoding="utf-8").read()
out["sidecar_clean"] = not any(w_ in text for w_ in ("kund", "hemligt", "127.0.0.1", "192.0.2", "://"))
w._dl_shutdown()
out["queue_clean"] = "hemligt" not in str(s.value(dl.QUEUE_KEY, ""))
print("RESULT" + json.dumps(out), flush=True)
os._exit(0)
"""


@pytest.fixture(scope="module")
def seen(tmp_path_factory):
    pytest.importorskip("PyQt6.QtWidgets")
    home = tmp_path_factory.mktemp("downloads-window")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(home / var.lower())
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=300)
    if out.stdout.startswith("SKIP"):
        pytest.skip(out.stdout.strip())
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, out.stdout[-2000:] + out.stderr[-4000:]
    return json.loads(line[6:])


def test_a_film_downloaded_from_its_menu_is_marked_everywhere(seen):
    assert "Download" in seen["menu"] and seen["queued"] == ["Hemma"]
    assert seen["file"] == os.path.join("Downloads", "Movies", "Hemma (2021).mp4")
    assert seen["pill"] == ["dl"] and "dl" in seen["movies_card_pills"]
    assert "Delete download" in seen["menu_after"] and "Download" not in seen["menu_after"]
    shown, words, button = seen["detail"]
    assert shown and words.startswith("Downloaded · ") and button == "Delete download"


def test_the_downloaded_place(seen):
    place = seen["place"]
    assert place["shown"] and place["place"] == "dl" and place["lit"] == ["dl"]
    assert place["cards"] == [["Hemma", ["dl"], True]] and not place["note"]
    assert sorted(seen["place_after"]) == ["Hemma", "Huset"]      # the film, the series


def test_a_downloaded_film_plays_its_file_as_that_same_film(seen):
    play = seen["play"]
    path = play["started"][-1]["url"]
    assert path.endswith(os.path.join("Movies", "Hemma (2021).mp4")) and os.path.isabs(path)
    assert play["started"][-1]["kind"] == "movie" and str(play["started"][-1]["key"]) == "9001"
    assert play["started"][-1]["record"] is False           # History is added as the film's
    assert play["plays"][0]["url"] == path and play["plays"][0]["buffer_key"] == "movie:9001"
    assert str(play["key"]) == "9001" and play["group"] == "vod" and play["last_url"] == path
    assert play["history"] == [["http://192.0.2.1/movie/kund/hemligt/9001.mp4", 9001, "movie"]]
    assert all(path not in str(h) for h in play["history"])
    assert play["vpn_stream"] is True and play["vpn_file"] is False


def test_an_episode_into_its_series_and_marked_on_its_page(seen):
    assert seen["episode_file"] == os.path.join("Downloads", "Series", "Huset",
                                                "Huset S01E02 - Piloten.mp4")
    assert seen["episode_state"] == [True]


def test_a_download_counts_as_a_connection_and_asks_over_a_full_limit(seen):
    assert seen["asked"] == 1
    state, hold, idle = seen["held"]
    assert state == "waiting" and hold == "connection" and idle   # nothing opened
    asked, kinds = seen["anyway"]
    assert asked == 2 and kinds.count("download") == 1 and "player" in kinds


def test_a_download_gives_way_to_the_player_and_goes_on_after(seen):
    ok, state, hold = seen["gives_way"]
    assert ok is True and state == "waiting" and hold == "connection"
    assert seen["goes_on"] is True


def test_private_viewing_refuses_and_holds(seen):
    state, hold, made = seen["private"]
    assert state == "waiting" and hold == "private" and made == []


def test_a_kids_profile_downloads_only_what_its_lists_show(seen):
    kids = seen["kids"]
    assert kids["adult"] is False and kids["family"] is True and kids["queued_adult"] == 0
    assert "Download" not in kids["menu"] and "Delete download" not in kids["menu"]
    assert kids["visible"] == ["Hemma", "Huset · S1 E2 · Piloten"]


def test_a_locked_categorys_download_is_hidden_while_the_lock_is_shut(seen):
    assert seen["locked"] == ["Huset · S1 E2 · Piloten"]
    assert seen["unlocked"] == ["Hemma", "Huset · S1 E2 · Piloten"]


def test_nothing_on_the_disk_or_in_the_queue_names_an_address_or_login(seen):
    assert seen["sidecar_clean"] and seen["queue_clean"]
