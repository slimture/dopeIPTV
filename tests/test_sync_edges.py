"""Settings sync's edges (core/sync.py, ui/mw_sync.py): a history list that
is full on one computer, the same playlist under an id of its own on each,
and the copy kept here when the window closes right after a round."""
from __future__ import annotations

import json
import os

import pytest

from dopeiptv.core import sync
from dopeiptv.core.stores import HistoryStore
from tests import test_sync as base
from tests.test_sync import PROVIDER, Computer, _settle, _window


@pytest.fixture(autouse=True)
def _nothing_reaches_the_real_site(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(sync, "ENDPOINT", "http://127.0.0.1:9/never/")


@pytest.fixture
def site():
    s = base.Site()
    yield s
    s.close()


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _history(c, pid):
    return HistoryStore(c.settings, f"history_{pid}")


def test_a_full_history_list_takes_no_title_from_the_other_computer(tmp_path, site):
    """A's list also holds its own files, so it keeps fewer of the provider's
    titles than B has. Those that find no room on A fell off its end: they
    were not removed, and stay on B."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    for c in (a, b):
        c.playlists.add(dict(PROVIDER, id="p"))
    hb = _history(b, "p")
    for i in range(sync.MAX_HISTORY):
        hb.add(f"http://x/{i}", f"t{i}", None, i, "movie")
    ha = _history(a, "p")
    for i in range(40):
        ha.add(f"/films/{i}.mkv", f"f{i}", None, f"/films/{i}.mkv", "local")
    b.state.seen = set(sync.CATEGORIES)
    now = 2_000_000_000
    b.round(remote, key, now=now)
    a.state.fresh = a.state.joining = True
    for step, c in enumerate((a, b, a, b), 1):
        c.round(remote, key, now=now + step)
    assert len(_history(b, "p").entries) == sync.MAX_HISTORY
    assert len(_history(a, "p").entries) == sync.MAX_HISTORY
    assert sum(1 for e in _history(a, "p").entries if e["_kind"] == "local") == 40
    # Nothing changes any more: no round puts anything on the site, and a
    # round that brings nothing new says so (no list read again every time).
    puts = site.puts()
    _result, applied = a.round(remote, key, now=now + 10)
    assert not applied.any() and site.puts() == puts
    # A title removed on A itself still goes from B.
    newest = next(e for e in _history(a, "p").entries if e["_kind"] == "movie")
    _history(a, "p").remove(newest["_key"], "movie")
    a.round(remote, key, now=now + 20)
    b.round(remote, key, now=now + 30)
    assert str(newest["_key"]) not in {str(e["_key"]) for e in _history(b, "p").entries}


def test_a_cleared_history_is_cleared_on_the_other_computer_too(tmp_path, site):
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    for c in (a, b):
        c.playlists.add(dict(PROVIDER, id="p"))
    ha = _history(a, "p")
    for i in range(sync.MAX_HISTORY):
        ha.add(f"http://x/{i}", f"t{i}", None, i, "movie")
    a.state.seen = set(sync.CATEGORIES)
    now = 2_000_000_000
    a.round(remote, key, now=now)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=now + 1)
    assert len(_history(b, "p").entries) == sync.MAX_HISTORY
    _history(a, "p").clear()
    _history(a, "p").add("http://x/new", "new", None, "new", "movie")
    a.round(remote, key, now=now + 2)
    b.round(remote, key, now=now + 3)
    assert [e["_key"] for e in _history(b, "p").entries] == ["new"]


def test_a_playlist_under_another_id_follows_a_change_made_elsewhere(tmp_path, site):
    """Added by hand on both computers, so each has it under an id of its
    own: a new password typed on one reaches the other all the same."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    a.playlists.add(dict(PROVIDER, id="aaaa1111"))
    b.playlists.add(dict(PROVIDER, id="bbbb2222"))
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=200)
    a.round(remote, key, now=300)
    a.playlists.update("aaaa1111", password="new", name="Renamed")
    a.round(remote, key, now=400)
    b.round(remote, key, now=500)
    assert [(p["id"], p["password"], p["name"]) for p in b.playlists.items] == [
        ("bbbb2222", "new", "Renamed")]
    # And back: the later change wins on both, each keeping its own id.
    b.playlists.update("bbbb2222", password="newer")
    b.round(remote, key, now=600)
    a.round(remote, key, now=700)
    assert [(p["id"], p["password"]) for p in a.playlists.items] == [("aaaa1111", "newer")]
    # Then nothing moves: no round puts anything on the site.
    b.round(remote, key, now=800)
    a.round(remote, key, now=810)
    b.round(remote, key, now=820)
    puts = site.puts()
    a.round(remote, key, now=900)
    b.round(remote, key, now=1000)
    assert site.puts() == puts
    assert [(p["id"], p["password"]) for p in b.playlists.items] == [("bbbb2222", "newer")]


def test_the_order_follows_a_playlist_kept_under_another_id(tmp_path, site):
    """The playlists' order names their ids; one that has an id of its own
    here takes its place all the same - and neither computer takes the
    other's spelling of the same order for a change (they used to send the
    whole copy to the site again, turn and turn about, for ever)."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    other = dict(PROVIDER, name="Other", server="http://other.example")
    a.playlists.add(dict(PROVIDER, id="aaaa1111"))
    a.playlists.add(dict(other, id="cccc3333"))
    b.playlists.add(dict(PROVIDER, id="bbbb2222"))
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=200)
    assert [p["id"] for p in b.playlists.items] == ["bbbb2222", "cccc3333"]
    a.playlists.reorder(["cccc3333", "aaaa1111"])
    a.round(remote, key, now=300)
    b.round(remote, key, now=400)
    assert [p["id"] for p in b.playlists.items] == ["cccc3333", "bbbb2222"]
    a.round(remote, key, now=500)
    puts = site.puts()
    for now, c in ((600, b), (700, a), (800, b), (900, a)):
        c.round(remote, key, now=now)
    assert site.puts() == puts
    assert [p["id"] for p in a.playlists.items] == ["cccc3333", "aaaa1111"]


def test_what_a_round_changed_is_told_for_the_profile_in_use():
    """Sync writes the first profile's own under their plain names and each
    other profile's under its id. The window acts on what changed for the
    profile in use: its theme at once, another profile's not at all."""
    from dopeiptv.core.profiles import MAIN_ID
    from dopeiptv.ui.mw_sync import _LIVE_KEYS, seen_by
    changed = {"theme", "profiles/kid1/theme", "profiles/kid1/places_hidden",
               "profiles/kid2/accent", "cache_secs", "keyboard_shortcuts"}
    assert seen_by(changed, None) == changed                    # no profiles: as they are
    first = seen_by(changed, MAIN_ID)
    assert first == {"theme", "cache_secs", "keyboard_shortcuts"}
    kid = seen_by(changed, "kid1")
    assert kid == {"theme", "places_hidden", "cache_secs", "keyboard_shortcuts"}
    assert kid & _LIVE_KEYS == {"theme"}                        # put in effect at once
    # The first profile's theme changed, a third one is in use: nothing to show.
    assert not seen_by({"theme", "profiles/kid1/accent"}, "kid2") & _LIVE_KEYS


def test_closing_right_after_a_round_still_keeps_that_rounds_copy(tmp_path, site, qapp):
    """The round's copy is written on the pool; closing clears the pool.
    One still waiting there is written by the close itself - else the next
    start took what the round put here for changes made then."""
    w = _window(tmp_path, "w", site)
    assert w._sync_start_here() and _settle(w)
    w.settings.setValue("theme", "dawn")
    state = w._sync_state
    state.doc["c"].setdefault("settings", {})["theme"] = {"t": 5, "v": "dawn"}
    w._sync_state_file.unlink()
    # As _sync_done leaves it when the pool has not got to the write yet.
    w._sync_unwritten = True
    w._sync_on_close()
    data = json.loads(w._sync_state_file.read_text(encoding="utf-8"))
    assert data["doc"]["c"]["settings"]["theme"] == {"t": 5, "v": "dawn"}
    assert w._sync_unwritten is False
    w._sync_forget()
    w._sync_pool.waitForDone(2000)
