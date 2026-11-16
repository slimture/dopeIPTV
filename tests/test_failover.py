"""Failover: a live channel that does not play, found in another source -
by its guide id, else by its name as both sources would write it - and
played from there in a real mpv; once per channel, the active source's
options back for whatever plays next."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from dopeiptv.services import failover
from dopeiptv.core.nerd_check import OK, WARN

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.mark.parametrize("a, b", [
    ("TV4 HD", "tv4"), ("SE: TV4 FHD", "TV4"), ("|SE| TV4", "TV4 HD backup"),
    ("SVT-1 HD", "SVT1"), ("Kanal 5", "KANAL 5 HD"),
])
def test_the_same_name_as_two_sources_write_it(a, b):
    assert failover.name_key(a) == failover.name_key(b)


@pytest.mark.parametrize("a, b", [("TV4", "TV4 Sport"), ("SVT1", "SVT2"), ("Kanal 5", "Kanal 9")])
def test_different_channels_stay_different(a, b):
    assert failover.name_key(a) != failover.name_key(b)


def test_the_guide_id_wins_over_the_name():
    rows = [{"stream_id": 1, "name": "TV4 HD", "epg_channel_id": "tv4hd.se"},
            {"stream_id": 2, "name": "Fyran", "epg_channel_id": "tv4.se"},
            {"name": "TV4 header row"}]
    assert failover.same_channel(rows, {"name": "TV4", "epg_channel_id": "TV4.se"})["stream_id"] == 2
    assert failover.same_channel(rows, {"name": "SE: TV4 FHD"})["stream_id"] == 1
    assert failover.same_channel(rows, {"name": "Kanal 5"}) is None


def test_which_sources_are_tried():
    pls = [{"id": "a", "name": "A", "kind": "xtream"}, {"id": "b", "name": "B", "kind": "m3u"},
           {"id": "l", "name": "Files", "kind": "local"}, {"id": "c", "name": "Vu+", "kind": "enigma2"}]
    assert [p["id"] for p in failover.candidates(pls, "a")] == ["b", "c"]
    c = failover.check_sources(pls, "a")
    assert (c.state, c.params["n"], c.params["names"]) == (OK, 2, "B, Vu+")
    assert failover.check_sources(pls[:1], "a").state == WARN


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - Qt's name
        self[key] = value


class _Other:
    """Another source: the channel in its category "1", a second copy in "2"."""

    def live_streams(self, _cat):
        return [{"stream_id": 401, "name": "TV4 HD", "epg_channel_id": "tv4.se", "category_id": "1"},
                {"stream_id": 402, "name": "TV4", "category_id": "2"}]

    def live_categories(self):
        return [{"category_id": "1", "category_name": "Sverige"},
                {"category_id": "2", "category_name": "Reserv"}]

    def live_url(self, sid, ext):
        return f"http://other.example/live/{sid}.{ext}"


def _limits(cats=None, chans=None, locked=True):
    from dopeiptv.core.stores import CategoryOverrides, ChannelOverrides
    s = _Settings(category_overrides_b=json.dumps(cats or {}),
                  channel_overrides_b=json.dumps(chans or {}))
    return {"b": failover.Shown.of(CategoryOverrides(s, "category_overrides_b"),
                                   ChannelOverrides(s, "channel_overrides_b"), locked)}


def _found(shown, kids=False, item=None):
    pls = [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]
    got = failover.find_elsewhere(pls, "a", item or {"name": "TV4", "epg_channel_id": "tv4.se"},
                                  make_client=lambda _p: _Other(), kids=kids, shown=shown)
    return None if got is None else got[1]["stream_id"]


def test_the_other_sources_own_hide_and_lock_rules_hold_there_too():
    assert _found(None) == 401 and _found(_limits()) == 401
    # Its category hidden: the copy in a category it shows.
    assert _found(_limits({"live": {"1": {"hidden": True}}})) == 402
    assert _found(_limits({"live": {"1": {"hidden": True}, "2": {"hidden": True}}})) is None
    # Locked: left out while locked, there once unlocked.
    locked = {"live": {"1": {"locked": True}, "2": {"locked": True}}}
    assert _found(_limits(locked)) is None
    assert _found(_limits(locked, locked=False)) == 401
    # A channel hidden by hand.
    assert _found(_limits(chans={"live": {"401": {"hidden": True}}})) == 402
    assert _found(_limits(chans={"live": {"401": {"hidden": True}, "402": {"hidden": True}}})) is None
    # Another source's rules are not this one's.
    assert _found({"c": _limits(locked)["b"]}) == 401


def test_a_kids_profile_reads_the_other_sources_categories_by_the_names_given_them():
    renamed = {"live": {"1": {"name": "XXX Adults"}}}
    assert _found(_limits(renamed)) == 401                  # anyone else: a name, no more
    assert _found(_limits(renamed), kids=True) == 402       # a kid: not from "XXX Adults"
    both = {"live": {"1": {"name": "Adults"}, "2": {"name": "18+"}}}
    assert _found(_limits(both), kids=True) is None
    assert _found(None, kids=True) == 401                   # the provider's names say nothing


def test_found_in_another_source_and_it_plays():
    from tests.test_nerd import _libmpv, _plays
    from tests.test_sources_side_by_side import Xtream
    xt = Xtream()
    try:
        playlists = [
            {"id": "dead", "name": "Dead", "kind": "m3u", "server": "http://127.0.0.1:9/none.m3u"},
            {"id": "b", "name": "Anna's IPTV", "kind": "xtream", "server": xt.base,
             "username": "anna", "password": "pw-anna"},
        ]
        found = failover.find_elsewhere(playlists, "dead",
                                        {"name": "SE: TV4 FHD", "epg_channel_id": "tv4.se"})
        assert found is not None
        pl, row, url = found
        assert pl["id"] == "b" and row["stream_id"] == 401
        assert url == f"{xt.base}/live/anna/pw-anna/401.ts"
        player = _libmpv()
        try:
            assert _plays(player, url)
        finally:
            player.terminate()
        # Nobody has it: None, and a source that cannot be reached is passed by.
        assert failover.find_elsewhere(playlists, "dead", {"name": "Kanal 9"}) is None
        assert failover.find_elsewhere(playlists[:1], "x", {"name": "TV4"}) is None
    finally:
        xt.close()


_CHILD = r"""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication
from dopeiptv.core import nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from tests.test_sources_side_by_side import Xtream

app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
xt = Xtream()
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([
    {"id": "a", "name": "Main", "kind": "m3u", "server": "http://127.0.0.1:9/a.m3u",
     "options": {"user_agent": "kodi"}},
    {"id": "b", "name": "Backup", "kind": "xtream", "server": xt.base, "username": "anna",
     "password": "pw-anna", "options": {"user_agent": "vlc"}}]))
s.setValue("active_playlist", "a")
s.setValue("nerd_enabled", "true")
s.setValue("nerd_failover", "true")
nerd.install(s)
store = PlaylistStore(s)
w = MainWindow(DemoClient(), s, store)
nerd.set_source(store.active())
calls, toasts, fell_back = [], [], []
w._start_playback = lambda url, title, icon, key, kind, record=True, item=None, catchup=False: \
    calls.append((url, title, key, kind, record))
w._show_toast = lambda text, ms=0: toasts.append(text)
w._last_playback = {"kind": "live", "key": 7, "url": "http://127.0.0.1:9/dead.ts",
                    "item": {"name": "TV4 HD", "epg_channel_id": "tv4.se", "stream_id": 7}}
# The other source's own rules hold there too: its category hidden, or
# locked while it is locked, or the channel hidden by hand - no failover to it.
for rules in ({"category_overrides_b": {"live": {"1": {"hidden": True}}}},
              {"category_overrides_b": {"live": {"1": {"locked": True}}}},
              {"channel_overrides_b": {"live": {"401": {"hidden": True}}}}):
    for key, value in rules.items():
        s.setValue(key, json.dumps(value))
    fell_back.clear()
    w._failover_tried = None
    assert w._try_failover(lambda: fell_back.append(1))
    for _ in range(400):
        app.processEvents()
        w.pool.waitForDone(10)
        if calls or fell_back:
            break
    assert fell_back and not calls, (rules, calls)
    for key in rules:
        s.remove(key)
fell_back.clear()
w._failover_tried = None
assert w._try_failover(lambda: fell_back.append(1))
for _ in range(400):
    app.processEvents()
    w.pool.waitForDone(10)
    if calls or fell_back:
        break
assert calls, (calls, fell_back)
url, title, key, kind, record = calls[0]
assert url == xt.base + "/live/anna/pw-anna/401.ts" and kind == "live" and record is False
assert title == "TV4 HD · Backup", title
assert nerd.source().get("user_agent") == "vlc" and nerd.source().get("_kind") == "xtream"
assert toasts and "Backup" in toasts[-1]
# Once per channel.
assert not w._try_failover(lambda: None)
# The failover stream retried keeps its source; anything else gets the active one back.
w._failover_leave(url, key)
assert nerd.source().get("user_agent") == "vlc"
w._failover_leave("http://127.0.0.1:9/other.ts", 8)
assert nerd.source().get("user_agent") == "kodi" and nerd.source().get("_kind") == "m3u"
# A channel no source has: the usual diagnosis runs.
w._last_playback = {"kind": "live", "key": 9, "url": "http://x", "item": {"name": "Kanal 9", "stream_id": 9}}
calls.clear()
assert w._try_failover(lambda: fell_back.append(1))
for _ in range(400):
    app.processEvents()
    w.pool.waitForDone(10)
    if fell_back:
        break
assert fell_back and not calls
# Off: nothing.
s.setValue("nerd_failover", "false"); nerd.install(s)
w._last_playback["key"] = 10
assert not w._try_failover(lambda: None)
xt.close()
print("FAILOVER_OK")
"""


def test_the_window_fails_over_once_and_gives_the_source_back():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "FAILOVER_OK" in out.stdout, out.stdout + out.stderr[-3000:]
