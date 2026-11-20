"""Settings sync's edges (core/sync.py, ui/mw_sync.py): a history list that
is full on one computer, the same playlist under an id of its own on each,
the copy kept here when the window closes right after a round, and a
playlist's connection settings, which sync never puts in place by itself."""
from __future__ import annotations

import base64
import json
import logging
import os

import pytest

from dopeiptv.core import backup, cert_pin, sync
from dopeiptv.core.stores import HistoryStore, PlaylistStore
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


# -- a playlist's connection settings: never taken unasked -----------------------------

PROXY = "http://10.8.0.1:3128"
#: What the red team's holder of the sync code put in the copy: every stream
#: through their proxy, its address rewritten, their certificate trusted.
EVIL = {"proxy": "http://mallory:hunter2@attacker.evil:8080",
        "rewrite": json.dumps([{"find": "iptv.example", "replace": "attacker.evil"}]),
        "cert_pin": "ab" * 32, "cert_pin_for": "attacker.evil:443"}


class _Said(logging.Handler):
    """The app's log lines while it listens."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture
def said():
    log = logging.getLogger("dopeiptv")
    handler, level = _Said(), log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    yield handler.lines
    log.removeHandler(handler)
    log.setLevel(level)


def _synced(tmp_path, site, options=None):
    """Two computers that sync one playlist, "p1": A's (with *options*),
    and B has joined."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    row = dict(PROVIDER, id="p1")
    if options is not None:
        row["options"] = dict(options)
    a.playlists.add(row)
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=200)
    return remote, key, slot, a, b


def _forge(site, slot, key, pid, drop=(), **fields):
    """Whoever holds the sync code puts a copy on the site in which playlist
    *pid* is as they want it: *fields* over what was there (*drop*: left
    out), timed later than anything a computer wrote."""
    version, blob = site.slots[slot]
    doc = sync.unseal(key, base64.b64decode(blob))
    ident = sync._eid("s", pid)
    was = doc["c"]["sources"][ident]
    v = {k: x for k, x in dict(was["v"], **fields).items() if k not in drop}
    doc["c"]["sources"][ident] = {"t": was["t"] + 10_000, "v": v}
    doc = sync._counted(doc, sync.counter(doc) + 1)
    site.slots[slot] = (version + 1, base64.b64encode(sync.seal(key, doc)).decode("ascii"))


def _copy_of(site, slot, key, pid):
    """Playlist *pid* as the copy on the site has it."""
    doc = sync.unseal(key, base64.b64decode(site.slots[slot][1]))
    return sync.live(doc, "sources")[sync._eid("s", pid)]


def _nothing_moves(site, remote, key, *computers, now=5000):
    """Rounds that put nothing on the site and change nothing here."""
    puts = site.puts()
    for i, c in enumerate(computers * 2):
        _result, applied = c.round(remote, key, now=now + i)
        assert not applied.any() and not applied.held
    assert site.puts() == puts


def test_whoever_holds_the_sync_code_cannot_send_a_playlists_streams_elsewhere(
        tmp_path, site, said):
    """The red team's finding: with playlists syncing, a copy in which an
    existing playlist had another proxy, rewrite rules and a trusted
    certificate was written over this computer's - every stream rerouted,
    and nothing said. Now those stay as they are here and what came waits;
    the rest of the playlist follows the copy as ever."""
    remote, key, slot, victim, _b = _synced(tmp_path, site)
    _forge(site, slot, key, "p1", options=dict(EVIL), password="theirs", name="Renamed")
    del said[:]
    _result, applied = victim.round(remote, key, now=300)
    p = victim.playlists.get("p1")
    assert not p.get("options"), "no proxy, no rewrite, no certificate trusted"
    assert (p["name"], p["password"]) == ("Renamed", "theirs")      # the rest still syncs
    assert applied.held == {"p1"} and applied.sources
    assert victim.state.held == {"p1": EVIL}
    # Which settings they are, never their values: a proxy's can hold a login.
    log = "\n".join(said)
    assert "proxy" in log and "rewrite" in log and "cert_pin" in log
    for secret in ("hunter2", "mallory", "attacker.evil", "ab" * 32):
        assert secret not in log
    # It reads back from the settings the same, and nothing goes on moving.
    assert not PlaylistStore(victim.settings).get("p1").get("options")
    _nothing_moves(site, remote, key, victim)
    assert victim.state.held == {"p1": EVIL}


def test_this_computers_own_proxy_stays_whatever_the_copy_says(tmp_path, site):
    """Changed in the copy, taken out of it, or its options gone altogether:
    this computer goes on with its own, and its snapshot keeps the copy's -
    so nothing goes back and forth while the person has not answered."""
    remote, key, slot, a, _b = _synced(tmp_path, site, {"proxy": PROXY, "user_agent": "Mine/1.0"})
    _forge(site, slot, key, "p1", options={"proxy": EVIL["proxy"], "user_agent": "Theirs/2.0"})
    a.round(remote, key, now=300)
    # The user agent sends nothing elsewhere: it follows the copy, as ever.
    assert a.playlists.get("p1")["options"] == {"proxy": PROXY, "user_agent": "Theirs/2.0"}
    assert a.state.held == {"p1": {"proxy": EVIL["proxy"]}}
    _nothing_moves(site, remote, key, a, now=310)
    _forge(site, slot, key, "p1", options={})                   # the proxy taken out
    _result, applied = a.round(remote, key, now=400)
    assert a.playlists.get("p1")["options"] == {"proxy": PROXY}
    assert a.state.held == {"p1": {"proxy": None}} and applied.held == {"p1"}
    _nothing_moves(site, remote, key, a, now=410)
    _forge(site, slot, key, "p1", drop=("options",))            # no options at all
    _result, applied = a.round(remote, key, now=500)
    assert a.playlists.get("p1")["options"] == {"proxy": PROXY}
    assert a.state.held["p1"]["proxy"] is None
    assert not applied.held, "the same proxy still waits: no news"
    _nothing_moves(site, remote, key, a, now=510)
    assert "options" not in _copy_of(site, slot, key, "p1")


def test_apply_puts_what_waited_in_place(tmp_path, site):
    remote, key, slot, a, _b = _synced(tmp_path, site)
    _forge(site, slot, key, "p1", options=dict(EVIL))
    a.round(remote, key, now=300)
    changes = sync.held_changes(a.playlists, a.state.held)
    assert [(c.pid, c.name, c.settings) for c in changes] == [
        ("p1", PROVIDER["name"], ["proxy", "rewrite", "cert_pin"])]
    assert sync.take_held(a.playlists, a.state.held, changes) == {"p1"}
    assert a.playlists.get("p1")["options"] == EVIL and a.state.held == {}
    assert PlaylistStore(a.settings).get("p1")["options"] == EVIL       # stored
    # It is what the copy has: nothing to send, nothing to ask again.
    _nothing_moves(site, remote, key, a)
    assert a.state.held == {} and a.playlists.get("p1")["options"] == EVIL


def test_keep_mine_forgets_them_and_this_computers_own_go_to_the_others(tmp_path, site):
    remote, key, slot, a, b = _synced(tmp_path, site)
    _forge(site, slot, key, "p1", options=dict(EVIL))
    for now, c in ((300, a), (310, b)):
        c.round(remote, key, now=now)
        assert c.state.held == {"p1": EVIL} and not c.playlists.get("p1").get("options")
    changes = sync.held_changes(a.playlists, a.state.held)
    assert sync.drop_held(a.state.held, changes) == {"p1"}
    assert a.state.held == {} and not a.playlists.get("p1").get("options")
    result, _applied = a.round(remote, key, now=400)
    assert result.uploaded, "this computer's own are the newer change"
    assert not _copy_of(site, slot, key, "p1").get("options")
    # The other computer had the same question. The copy no longer differs
    # from its own, so there is nothing left to ask it.
    _result, applied = b.round(remote, key, now=500)
    assert b.state.held == {} and not applied.held
    assert not b.playlists.get("p1").get("options")
    _nothing_moves(site, remote, key, a, b)


def test_a_playlist_new_from_sync_comes_without_its_connection_settings(tmp_path, site):
    """Joining: the other computer's playlist arrives without its proxy, its
    udpxy and its own DNS server, which wait - and nothing changes on the
    computer whose they are."""
    mine = {"proxy": PROXY, "udpxy": "http://192.168.1.1:4022", "user_agent": "Mine/1.0",
            "dns": "custom", "dns_url": "https://dns.example/dns-query"}
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    a.playlists.add(dict(PROVIDER, id="p1", options=dict(mine)))
    a.state.seen = set(sync.CATEGORIES)
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    _result, applied = b.round(remote, key, now=200)
    assert applied.held == {"p1"} and applied.sources
    assert b.playlists.get("p1")["options"] == {"user_agent": "Mine/1.0"}
    assert b.state.held == {"p1": {k: v for k, v in mine.items() if k != "user_agent"}}
    _nothing_moves(site, remote, key, a, b)
    assert a.playlists.get("p1")["options"] == mine and a.state.held == {}
    # Taken on B, it is the playlist A has.
    changes = sync.held_changes(b.playlists, b.state.held)
    assert changes[0].settings == ["proxy", "udpxy", "dns_url"]
    assert sync.take_held(b.playlists, b.state.held, changes) == {"p1"}
    assert b.playlists.get("p1")["options"] == mine
    _nothing_moves(site, remote, key, a, b, now=6000)


def test_a_computers_own_change_is_no_question_when_it_comes_back(tmp_path, site):
    remote, key, slot, a, b = _synced(tmp_path, site)
    a.playlists.update("p1", options={"proxy": PROXY})
    result, applied = a.round(remote, key, now=300)
    assert result.uploaded and not applied.held and a.state.held == {}     # sent as ever
    _result, applied = b.round(remote, key, now=400)
    assert applied.held == {"p1"} and b.state.held == {"p1": {"proxy": PROXY}}
    # B changes something else of the playlist: it goes with the copy's
    # proxy, which is A's own - A takes the name and is asked nothing.
    b.playlists.update("p1", name="Renamed on B")
    result, _applied = b.round(remote, key, now=500)
    assert result.uploaded and _copy_of(site, slot, key, "p1")["options"] == {"proxy": PROXY}
    _result, applied = a.round(remote, key, now=600)
    p = a.playlists.get("p1")
    assert (p["name"], p["options"]) == ("Renamed on B", {"proxy": PROXY})
    assert not applied.held and a.state.held == {}
    assert not b.playlists.get("p1").get("options"), "B still has not taken it"
    _nothing_moves(site, remote, key, b, a)


def test_options_that_send_nothing_elsewhere_sync_as_ever(tmp_path, site):
    remote, key, _slot, a, b = _synced(tmp_path, site)
    plain = {"user_agent": "Mine/1.0", "referer": "http://ref.example/", "epg_shift": "30",
             "catchup_mode": "shift", "dns": "quad9", "ip_family": "ipv4",
             "stream_port": "8002", "recordings": False}
    assert not set(plain) & set(backup.RISKY_SOURCE)
    a.playlists.update("p1", options=dict(plain))
    a.round(remote, key, now=300)
    _result, applied = b.round(remote, key, now=400)
    assert b.playlists.get("p1")["options"] == plain
    assert applied.sources and not applied.held and b.state.held == {}


def test_a_newer_value_takes_the_waiting_ones_place_and_none_waits_once_they_agree(
        tmp_path, site):
    remote, key, slot, a, _b = _synced(tmp_path, site, {"proxy": PROXY})
    _forge(site, slot, key, "p1", options={"proxy": "http://one.evil:1"})
    _result, applied = a.round(remote, key, now=300)
    assert applied.held == {"p1"} and a.state.held == {"p1": {"proxy": "http://one.evil:1"}}
    _forge(site, slot, key, "p1", options={"proxy": "http://two.evil:2"})
    _result, applied = a.round(remote, key, now=400)
    assert applied.held == {"p1"} and a.state.held == {"p1": {"proxy": "http://two.evil:2"}}
    # The copy has this computer's own again (space round it is no other
    # value): nothing waits.
    _forge(site, slot, key, "p1", options={"proxy": " " + PROXY})
    _result, applied = a.round(remote, key, now=500)
    assert not applied.held and a.state.held == {}
    assert a.playlists.get("p1")["options"]["proxy"].strip() == PROXY
    # Typed here by hand meanwhile: this computer has it itself now.
    _forge(site, slot, key, "p1", options={"proxy": "http://three.example:3"})
    a.round(remote, key, now=600)
    assert a.state.held == {"p1": {"proxy": "http://three.example:3"}}
    a.playlists.update("p1", options={"proxy": "http://three.example:3"})
    a.round(remote, key, now=700)
    assert a.state.held == {}
    # And one whose playlist is gone from this computer waits for nothing.
    held = {"gone": {"proxy": "http://x.example:1"}}
    assert sync.settle_held(a.playlists, held) and held == {}


@pytest.mark.parametrize("junk", [[["proxy", "http://attacker.evil:8080"]],
                                  "proxy=http://attacker.evil:8080", 7])
def test_options_that_are_no_set_of_options_are_never_stored(tmp_path, site, junk):
    """A list of pairs reads as options where they are used (dict(options,
    ...)): it came past every check of a set, a proxy in it and all."""
    for folder, own in (("with", {"proxy": PROXY}), ("without", None)):
        (tmp_path / folder).mkdir()
        remote, key, slot, a, _b = _synced(tmp_path / folder, site, own)
        _forge(site, slot, key, "p1", options=junk)
        a.round(remote, key, now=300)
        got = a.playlists.get("p1")
        assert dict(got.get("options") or {}) == (own or {})
        if own:
            assert got["options"] == own and a.state.held["p1"]["proxy"] is None
        else:
            assert "options" not in got and a.state.held == {}
            a.round(remote, key, now=400)           # nor is it what goes back
            assert "options" not in _copy_of(site, slot, key, "p1")


def test_what_waits_is_kept_with_this_computers_copy(tmp_path):
    path = tmp_path / "sync-state.json"
    st = sync.State(path, "ab" * 32)
    st.held = {"p1": {"proxy": EVIL["proxy"], "cert_pin": None, "cert_pin_for": None}}
    sync.write_private(path, st.dump())
    if os.name == "posix":
        assert os.stat(path).st_mode & 0o077 == 0        # a proxy's login is in it
    assert sync.State.load(path, "ab" * 32).held == st.held
    assert sync.State.load(path, "cd" * 32).held == {}           # another code's: none
    # Only what can wait is read back: never another option, never junk.
    data = json.loads(path.read_text(encoding="utf-8"))
    data["held"] = {"p1": {"proxy": "x", "user_agent": "y", "server": "z"},
                    "p2": "junk", "p3": {}, "p4": {"name": "n"}}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert sync.State.load(path, "ab" * 32).held == {"p1": {"proxy": "x"}}
    # A backup put back over everything: the playlists are the backup's, and
    # the copy's are looked at afresh.
    assert sync.rejoin(path) and sync.State.load(path, "ab" * 32).held == {}


def test_a_setting_waits_whole_never_half_of_it():
    """A DNS choice without its server fails every lookup, and a certificate
    without the address it is for is none: what makes one setting waits,
    and is taken, together."""
    taken, waits = sync.hold_back(
        {"options": {"dns": "custom", "dns_url": "https://d.example/q", "ip_family": "ipv4"}},
        {"options": {}})
    assert taken["options"] == {"ip_family": "ipv4"}
    assert waits == {"dns_url": "https://d.example/q", "dns": "custom"}
    # A preset DNS server is no address of anyone's own: as before.
    theirs = {"options": {"dns": "cloudflare"}}
    assert sync.hold_back(theirs, {"options": {"dns": "quad9"}}) == (theirs, {})
    # The address a trusted certificate is for, moved: the certificate waits.
    pin = cert_pin.pin_options("home.example", 8443, "cd" * 32)
    theirs = {"options": dict(pin, cert_pin_for="other.example:443")}
    taken, waits = sync.hold_back(theirs, {"options": dict(pin)})
    assert taken["options"] == pin and waits == theirs["options"]
    # A catch-up address with how it is used.
    taken, waits = sync.hold_back(
        {"options": {"catchup_template": "?utc={utc}", "catchup_mode": "append"}},
        {"options": {"catchup_mode": "shift"}})
    assert taken["options"] == {"catchup_mode": "shift"}
    assert waits == {"catchup_template": "?utc={utc}", "catchup_mode": "append"}
    # Space round a value is no other value.
    theirs = {"options": {"proxy": " " + PROXY + " "}}
    assert sync.hold_back(theirs, {"options": {"proxy": PROXY}}) == (theirs, {})
    # Every setting a restored backup shows first can wait - on its own too.
    assert set(backup.RISKY_SOURCE) <= sync.HELD_KEYS
    for name in backup.RISKY_SOURCE:
        taken, waits = sync.hold_back({"name": "N", "options": {name: "x"}}, {"name": "M"})
        assert name in waits and name not in taken["options"] and taken["name"] == "N", name


def test_what_waits_is_listed_as_it_is_here_and_as_the_copy_has_it(tmp_path):
    from PyQt6.QtCore import QSettings
    store = PlaylistStore(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    store.add(dict(PROVIDER, id="p1", options={"proxy": PROXY, "dns": "quad9"}))
    store.add(dict(PROVIDER, id="p2", name="", server="http://two.example"))
    rules = json.dumps([{"find": "http://", "replace": "https://"},
                        {"find": r"(\d+)\.ts$", "replace": r"\1.m3u8", "regex": True}])
    held = {"p1": {"proxy": EVIL["proxy"], "rewrite": rules, "dns": "custom",
                   "dns_url": "https://d.example/q", "cert_pin": "ab" * 32,
                   "cert_pin_for": "home.example:443"},
            "p2": {"headers": "X-Token: abc\nX-Other: 1", "backup_servers": "x" * 5000},
            "gone": {"proxy": "http://x.example:1"}}
    changes = sync.held_changes(store, held)
    assert [(c.pid, c.name) for c in changes] == [("p1", PROVIDER["name"]),
                                                  ("p2", "http://two.example")]
    rows = {name: (here, there) for name, here, there in changes[0].rows}
    assert rows == {
        "proxy": (PROXY, EVIL["proxy"]),
        "rewrite": ("", "http://  →  https://\n(\\d+)\\.ts$  →  \\1.m3u8  (.*)"),
        "dns_url": ("quad9", "https://d.example/q"),
        "cert_pin": ("", "home.example:443 · " + cert_pin.pretty("ab" * 32))}
    rows = {name: (here, there) for name, here, there in changes[1].rows}
    assert rows["headers"] == ("", "X-Token: abc\nX-Other: 1")
    here, there = rows["backup_servers"]
    assert here == "" and len(there) == sync.SHOWN_MAX and there.endswith("…")
    # Rules that do not read as rules - nested without end - are shown as
    # they are stored, cut; never an error.
    shown = sync.held_changes(store, {"p1": {"rewrite": "[" * 9000}})[0].rows
    assert [(name, here) for name, here, _there in shown] == [("rewrite", "")]
    assert shown[0][2].startswith("[[[") and len(shown[0][2]) == sync.SHOWN_MAX
