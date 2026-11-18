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


OTHER = dict(PROVIDER, name="Other IPTV", server="http://other.example:2095",
             username="bo")


def _stations(c, pid):
    return {x["stream_id"] for x in c.favs(pid).items()}


def test_two_computers_with_a_default_playlist_of_another_provider_each(tmp_path, site):
    """A version before playlists made one called "default" from its
    settings, on each computer - of another provider on each. Joining, B's
    lost to A's under the same id: B's playlist became A's provider, and
    B's favourites went with it. Now each keeps its own and gets the
    other's beside it, the favourites with their own provider."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    a.playlists.add(dict(PROVIDER, id="default"))
    b.playlists.add(dict(OTHER, id="default"))
    a.favs("default").add("all", {"stream_id": 1, "name": "A's"})
    b.favs("default").add("all", {"stream_id": 7, "name": "B's"})
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)

    b.state.fresh = b.state.joining = True
    _, applied = b.round(remote, key, now=200)
    assert applied.sources and applied.again
    mine = b.playlists.get("default")
    assert (mine["server"], mine["username"]) == (OTHER["server"], "bo")
    assert b.playlists.active_id == "default"
    assert _stations(b, "default") == {7}
    theirs = [p for p in b.playlists.items if p["id"] != "default"]
    assert [p["server"] for p in theirs] == [PROVIDER["server"]]
    assert _stations(b, theirs[0]["id"]) == {1}

    # B's own goes to the copy next round; A gets it beside its own.
    b.round(remote, key, now=300)
    a.round(remote, key, now=400)
    assert a.playlists.get("default")["server"] == PROVIDER["server"]
    got = [p for p in a.playlists.items if p["id"] != "default"]
    assert [p["server"] for p in got] == [OTHER["server"]]
    assert _stations(a, "default") == {1} and _stations(a, got[0]["id"]) == {7}

    # A change goes to the right playlist both ways.
    a.playlists.update("default", password="a-new")
    b.playlists.update("default", password="b-new")
    a.round(remote, key, now=500)
    b.round(remote, key, now=600)
    a.round(remote, key, now=700)
    assert b.playlists.get("default")["password"] == "b-new"
    assert b.playlists.get(theirs[0]["id"])["password"] == "a-new"
    assert a.playlists.get("default")["password"] == "a-new"
    assert a.playlists.get(got[0]["id"])["password"] == "b-new"

    # Then nothing moves.
    b.round(remote, key, now=800)
    a.round(remote, key, now=900)
    puts = site.puts()
    for now, c in ((1000, b), (1100, a), (1200, b)):
        c.round(remote, key, now=now)
    assert site.puts() == puts
    assert len(a.playlists.items) == len(b.playlists.items) == 2


def test_the_same_default_playlist_on_both_is_one(tmp_path, site):
    """The same provider under "default" on both: one playlist, as before -
    the group's version where they differ."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    a.playlists.add(dict(PROVIDER, id="default", name="Mine"))
    b.playlists.add(dict(PROVIDER, id="default", password="old"))
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    _, applied = b.round(remote, key, now=200)
    assert not applied.again
    assert [(p["id"], p["name"], p["password"]) for p in b.playlists.items] == [
        ("default", "Mine", "pw")]
    assert b.settings.value(sync.K_SOURCE_IDS) is None


def test_a_new_address_typed_on_one_is_no_other_playlist(tmp_path, site):
    """A computer that synced before takes a playlist's new address made
    on another as it always did: only one new to the copy keeps its own."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    a.playlists.add(dict(PROVIDER, id="default"))
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=200)
    a.playlists.update("default", server="http://moved.example:8080")
    a.round(remote, key, now=300)
    _, applied = b.round(remote, key, now=400)
    assert not applied.again
    assert [(p["id"], p["server"]) for p in b.playlists.items] == [
        ("default", "http://moved.example:8080")]


def test_a_playlist_kept_under_another_id_removed_elsewhere(tmp_path, site):
    """Removed on the other computer: gone here too, and its id with it."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    a.playlists.add(dict(PROVIDER, id="default"))
    b.playlists.add(dict(OTHER, id="default"))
    b.playlists.add(dict(PROVIDER, name="Third", server="http://third.example", id="cccc3333"))
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=200)
    b.round(remote, key, now=300)
    a.round(remote, key, now=400)
    other_on_a = next(p["id"] for p in a.playlists.items if p["server"] == OTHER["server"])
    a.playlists.remove(other_on_a)
    a.round(remote, key, now=500)
    b.playlists.set_active("cccc3333")
    b.round(remote, key, now=600)
    assert sorted(p["server"] for p in b.playlists.items) == [
        PROVIDER["server"], "http://third.example"]
    assert b.playlists.get("default") is None
    # Only A's playlist, the copy's "default", is still kept under another id.
    kept = json.loads(b.settings.value(sync.K_SOURCE_IDS) or "{}")
    assert list(kept.values()) == ["default"]
    assert b.playlists.get(next(iter(kept)))["server"] == PROVIDER["server"]


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
