"""Settings sync (core/sync.py, core/chacha20poly1305.py, ui/mw_sync.py): the
cipher against RFC 8439's own vectors, the code and what comes of it, what
syncs and what never does, how two computers' changes merge, and "computers"
syncing through a stand-in for dopeiptv.org on 127.0.0.1 - no real network."""
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core import chacha20poly1305 as aead
from dopeiptv.core import shortcut_keys, supporter, sync
from dopeiptv.core.stores import FavoriteStore, HistoryStore, PlaylistStore
from dopeiptv.i18n import tr

QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())

SEED, PUB = supporter.new_seed()
SUNSCREEN = (b"Ladies and Gentlemen of the class of '99: If I could offer you only one "
             b"tip for the future, sunscreen would be it.")
ALL = sync.CATEGORIES


@pytest.fixture(autouse=True)
def _no_proxy_and_a_test_key(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(supporter, "PUBLIC_KEY_HEX", PUB.hex())
    # Nothing in here may reach the real site.
    monkeypatch.setattr(sync, "ENDPOINT", "http://127.0.0.1:9/never/")


# -- the cipher -----------------------------------------------------------------------

def test_rfc_8439_chacha20_block_and_encryption():
    key = bytes(range(32))
    # 2.3.2: the block function.
    assert aead.block(key, bytes.fromhex("000000090000004a00000000"), 1).hex() == (
        "10f1e7e4d13b5915500fdd1fa32071c4c7d1f4c733c068030422aa9ac3d46c4e"
        "d2826446079faa0914c2d705d98b02a2b5129cd1de164eb9cbd083e8a2503c4e")
    # 2.4.2: encryption, counter 1.
    assert aead.chacha20(key, bytes.fromhex("000000000000004a00000000"), 1, SUNSCREEN).hex() == (
        "6e2e359a2568f98041ba0728dd0d6981e97e7aec1d4360c20a27afccfd9fae0b"
        "f91b65c5524733ab8f593dabcd62b3571639d624e65152ab8f530c359f0861d8"
        "07ca0dbf500d6a6156a38e088a22b65e52bc514d16ccf806818ce91ab7793736"
        "5af90bbf74a35be6b40b8eedf2785e42874d")


def test_rfc_8439_poly1305_and_its_key():
    # 2.5.2
    key = bytes.fromhex("85d6be7857556d337f4452fe42d506a80103808afb0db2fd4abff6af4149f51b")
    assert aead.poly1305(key, b"Cryptographic Forum Research Group").hex() == \
        "a8061dc1305136c6c22b8baf0c0127a9"
    # 2.6.2: the one-time key is the first half of block 0.
    otk = aead.block(bytes(range(0x80, 0xA0)), bytes.fromhex("000000000001020304050607"), 0)[:32]
    assert otk.hex() == "8ad5a08b905f81cc815040274ab29471a833b637e3fd0da508dbb8e2fdd1a646"


def test_rfc_8439_aead():
    # 2.8.2
    key = bytes(range(0x80, 0xA0))
    nonce = bytes.fromhex("070000004041424344454647")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    out = aead.encrypt(key, nonce, SUNSCREEN, aad)
    assert out[:-16].hex() == (
        "d31a8d34648e60db7b86afbc53ef7ec2a4aded51296e08fea9e2b5a736ee62d6"
        "3dbea45e8ca9671282fafb69da92728b1a71de0a9e060b2905d6a5b67ecd3b36"
        "92ddbd7f2d778b8c9803aee328091b58fab324e4fad675945585808b4831d7bc"
        "3ff4def08e4b7a9de576d26586cec64b6116")
    assert out[-16:].hex() == "1ae10b594f09e26a7e902ecbd0600691"
    assert aead.decrypt(key, nonce, out, aad) == SUNSCREEN
    for bad in (out[:5] + bytes([out[5] ^ 1]) + out[6:], out[:-1] + bytes([out[-1] ^ 1])):
        with pytest.raises(ValueError):
            aead.decrypt(key, nonce, bad, aad)
    with pytest.raises(ValueError):
        aead.decrypt(key, nonce, out, aad + b"x")


def test_many_blocks_at_once_match_one_at_a_time():
    """The lanes: every length across the batch edges is the stream block by
    block, as the RFC defines it."""
    key, nonce = os.urandom(32), os.urandom(12)
    data = os.urandom(64 * 300 + 17)
    stream = b"".join(aead.block(key, nonce, 1 + i) for i in range(301))
    want = bytes(a ^ b for a, b in zip(data, stream, strict=False))
    for n in (0, 1, 63, 64, 65, 64 * 255, 64 * 256, 64 * 256 + 1, len(data)):
        assert aead.chacha20(key, nonce, 1, data[:n]) == want[:n], n


def test_half_a_megabyte_is_quick():
    key, nonce, data = os.urandom(32), os.urandom(12), os.urandom(500_000)
    t0 = time.perf_counter()
    out = aead.encrypt(key, nonce, data)
    took = time.perf_counter() - t0
    assert aead.decrypt(key, nonce, out) == data
    assert took < 2.0, f"500 KB took {took:.2f} s"


def test_hkdf_rfc_5869_case_1():
    okm = sync.hkdf(b"\x0b" * 22, bytes(range(13)), bytes(range(0xF0, 0xFA)), 42)
    assert okm.hex() == ("3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
                         "34007208d5b887185865")


# -- the code, the slot, the key ------------------------------------------------------

def test_a_code_reads_back_however_it_is_typed():
    code = sync.new_code()
    groups = code.split("-")
    assert len(groups) == 13 and all(len(g) == 4 for g in groups)
    raw = sync.parse_code(code)
    assert raw is not None and len(raw) == 32
    assert sync.parse_code(code.lower().replace("-", " ")) == raw
    assert sync.parse_code("  " + code.replace("-", "") + "\n") == raw
    assert sync.parse_code(code.replace("O", "0").replace("I", "1")) == raw
    for junk in ("", "ABCD", code[:-1], code + "A", code.replace(code[5], "9", 1)):
        assert sync.parse_code(junk) is None or junk == code, junk
    assert sync.masked(code).startswith(groups[0]) and groups[1] not in sync.masked(code)


def test_slot_and_key_come_from_the_code_alone_and_apart():
    raw = sync.parse_code(sync.new_code())
    slot, key = sync.derive(raw)
    assert sync.derive(raw) == (slot, key)
    assert len(slot) == 64 and all(c in "0123456789abcdef" for c in slot)
    assert len(key) == 32 and key.hex() != slot and key.hex()[:16] not in slot
    other_slot, other_key = sync.derive(sync.parse_code(sync.new_code()))
    assert other_slot != slot and other_key != key


def test_a_sealed_snapshot_opens_with_its_key_only():
    _slot, key = sync.derive(os.urandom(32))
    doc = {"v": 1, "c": {"settings": {"theme": {"t": 5, "v": "nord"}}}}
    blob = sync.seal(key, doc)
    assert b"nord" not in blob and blob[0] == sync.FORMAT
    assert sync.unseal(key, blob) == doc
    assert sync.seal(key, doc) != blob                       # a nonce of its own each time
    with pytest.raises(sync.SyncError) as e:
        sync.unseal(os.urandom(32), blob)
    assert e.value.kind == "read"
    with pytest.raises(sync.SyncError) as e:
        sync.unseal(key, blob[:-1] + bytes([blob[-1] ^ 1]))
    assert e.value.kind == "read"
    with pytest.raises(sync.SyncError) as e:
        sync.unseal(key, bytes([sync.FORMAT + 1]) + blob[1:])
    assert e.value.kind == "newer"


# -- a "computer" -------------------------------------------------------------------------

class Computer:
    """Settings, resume positions and playlists of their own, and the sync's
    copy - the core flow the window runs, without a window."""

    def __init__(self, tmp, name, slot, platform="linux"):
        self.settings = QSettings(str(tmp / f"{name}.ini"), QSettings.Format.IniFormat)
        self.resume = QSettings(str(tmp / f"{name}-resume.ini"), QSettings.Format.IniFormat)
        self.playlists = PlaylistStore(self.settings)
        self.ctx = sync.Context(self.settings, self.resume, self.playlists, platform=platform)
        self.state = sync.State(tmp / f"{name}-state.json", slot)

    def round(self, remote, key, cats=ALL, now=None):
        snap = sync.collect(self.ctx, cats)
        result = sync.round_trip(remote, key, self.state.doc, snap, cats, now=now,
                                 fresh=self.state.fresh, joining=self.state.joining,
                                 seen=self.state.seen)
        here = sync.collect(self.ctx, cats)
        applied, keep = sync.apply(self.ctx, result, snap, here, cats)
        self.state.doc, self.state.version = keep, result.version
        self.state.fresh = self.state.joining = False
        self.state.seen |= sync.seen_after(cats, applied)
        return result, applied

    def favs(self, pid, kind="favorites", id_key="stream_id"):
        return FavoriteStore(self.settings, f"{kind}_{pid}", id_key)


PROVIDER = {"name": "Home IPTV", "kind": "xtream", "server": "http://iptv.example:8080",
            "username": "anna", "password": "pw", "epg_url": "", "refresh": "never"}


class Site:
    """dopeiptv.org's /sync/ as donate/sync.py answers it: a version per
    slot, 409 with the copy there for a stale version, 507 for a new slot
    while it is *full*, *refuse* for every request turned down - and the
    headers of each request kept, to see that nothing about the person
    goes along."""

    def __init__(self):
        self.slots: dict[str, tuple[int, str]] = {}
        self.calls: list[tuple[str, str]] = []
        self.headers: list[dict[str, str]] = []
        self.full = False
        self.refuse = 0
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, body=None):
                data = json.dumps(body or {}).encode()
                self.send_response(status)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _slot(self):
                site.calls.append((self.command, self.path))
                site.headers.append({k.lower(): v for k, v in self.headers.items()})
                if site.refuse:
                    self._send(site.refuse, {"error": "no"})
                    return None
                return self.path.rsplit("/", 1)[-1]

            def do_GET(self):
                slot = self._slot()
                if slot is None:
                    return
                if slot not in site.slots:
                    return self._send(404, {"error": "none"})
                version, blob = site.slots[slot]
                self._send(200, {"version": version, "t": 1, "blob": blob})

            def do_PUT(self):
                slot = self._slot()
                if slot is None:
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                version, blob = site.slots.get(slot, (0, ""))
                if body["version_seen"] != version:
                    return self._send(409, {"version": version, "t": 1, "blob": blob})
                if site.full and slot not in site.slots:
                    return self._send(507, {"error": "full"})
                site.slots[slot] = (version + 1, body["blob"])
                self._send(200, {"version": version + 1, "t": 1})

            def do_DELETE(self):
                slot = self._slot()
                if slot is not None:
                    site.slots.pop(slot, None)
                    self._send(200, {"deleted": True})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/sync/"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def puts(self):
        return sum(1 for m, _p in self.calls if m == "PUT")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def site():
    s = Site()
    yield s
    s.close()


def _key():
    return supporter.make(SEED)


# -- what syncs, what never does --------------------------------------------------------

def test_collect_and_apply_take_the_allowed_settings_and_never_the_rest(tmp_path):
    a = Computer(tmp_path, "a", "s")
    allowed = {"theme": "nord", "accent": "green", "language": "sv", "sub_lang": "swe",
               "start_place": "mitt", "popular_series": "false", "nav_color_live": "#112233",
               "keyboard_shortcuts": json.dumps({"mute": "J"}), "epg_upcoming_count": 5}
    # The old editor's keys (moved into keyboard_shortcuts at the first
    # start since 1.5.0): one left in a file is another system's.
    never = {"shortcut/linux/play_pause": "Space", "shortcut/darwin/mute": "J",
             "window_geometry": "geo", "supporter_key": _key(), "sync_code": sync.new_code(),
             "sync_cats": "settings", "recordings_dir": "/home/anna/rec", "local_dirs": "[]",
             "nerd_proxy": "socks5://x", "nerd_mpv_config_dir": "/x", "parental_pin_hash": "h",
             "trakt_access_token": "t", "simkl_access_token": "t",
             "kodi_devices": "[]", "discord_presence": "true",
             "tmdb_api_key": "k", "os_password": "p",
             "hwdec_mode": "auto-safe", "splitter_state": "s", "playlists": "[]",
             "favorites_abc": "{}", "feedback_tickets": "[]", "volume": "80",
             "live_rewind_space_gb": "8", "mv_geometry": "g", "made_up_key": "x"}
    for k, v in {**allowed, **never}.items():
        a.settings.setValue(k, v)
    got = sync.collect(a.ctx, ["settings"]).values["settings"]
    assert got == {sync.entry_of(k, "linux"): str(v) for k, v in allowed.items()}
    assert "keyboard_shortcuts@linux" in got
    for k in never:
        assert not sync.syncable(k), k

    b = Computer(tmp_path, "b", "s")
    b.settings.setValue("window_geometry", "mine")
    b.settings.setValue("theme", "graphite")
    snap = sync.collect(b.ctx, ["settings"])
    doc = sync.empty_doc()
    sync.stamp(doc, sync.collect(a.ctx, ["settings"]), ["settings"], 100)
    result = sync.RoundResult(doc, doc, 1, False, 0)
    applied, _keep = sync.apply(b.ctx, result, snap, snap, ["settings"])
    for k, v in allowed.items():
        assert b.settings.value(k) == str(v), k
    for k in never:
        assert b.settings.value(k) in (None, "mine"), k
    assert b.settings.value("window_geometry") == "mine"
    assert applied.settings == set(allowed)


def test_favorites_history_and_resume_are_collected_per_playlist(tmp_path):
    a = Computer(tmp_path, "a", "s")
    pid = a.playlists.add(dict(PROVIDER))["id"]
    fav = a.favs(pid)
    fav.add("all", {"stream_id": 1, "name": "One"})
    fav.add("Sports", {"stream_id": 2, "name": "Two"})
    fav.add("Sports", {"stream_id": 3, "name": "Three"})
    fav.set_group_color("Sports", color="#ff0000")
    hist = HistoryStore(a.settings, f"history_{pid}")
    hist.add("http://x/1.ts", "One", None, "1", "live")
    hist.add("/home/anna/film.mkv", "Film", None, "/home/anna/film.mkv", "local")
    a.resume.setValue(f"resume_positions_{pid}", json.dumps({
        "vod:9": {"pos": 600, "dur": 6000, "ts": 50}, "local:/x.mkv": {"pos": 1, "dur": 2, "ts": 1}}))
    snap = sync.collect(a.ctx, ALL)
    favs = snap.values["favorites"]
    assert len([k for k in favs if '"i"' in k]) == 3
    assert any('"c"' in k for k in favs) and any('"o"' in k for k in favs)
    history = snap.values["history"]
    assert len(history) == 2                      # the channel and the film's position
    assert all("/home/anna" not in k for k in history)          # this computer's own files stay
    sources = snap.values["sources"]
    assert sources[json.dumps(["s", pid])]["password"] == "pw"
    assert sources[json.dumps(["o"])] == [pid]


# -- merging --------------------------------------------------------------------------------

def test_merge_is_last_writer_wins_per_entry_and_any_order():
    a = {"v": 1, "c": {"settings": {"theme": {"t": 10, "v": "nord"},
                                    "accent": {"t": 30, "v": "red"}}}}
    b = {"v": 1, "c": {"settings": {"theme": {"t": 20, "v": "dawn"},
                                    "accent": {"t": 5, "v": "blue"},
                                    "sub_lang": {"t": 7, "d": 1}}}}
    m = sync.merge(a, b)
    assert sync.live(m, "settings") == {"theme": "dawn", "accent": "red"}
    assert m["c"]["settings"]["sub_lang"] == {"t": 7, "d": 1}
    assert sync.merge(b, a) == m
    tie_a = {"v": 1, "c": {"x": {"k": {"t": 1, "v": "a"}}}}
    tie_b = {"v": 1, "c": {"x": {"k": {"t": 1, "v": "b"}}}}
    assert sync.merge(tie_a, tie_b) == sync.merge(tie_b, tie_a)


def test_stamp_times_what_changed_and_tombstones_what_went(tmp_path):
    a = Computer(tmp_path, "a", "s")
    a.settings.setValue("theme", "nord")
    a.settings.setValue("accent", "red")
    doc = sync.empty_doc()
    assert sync.stamp(doc, sync.collect(a.ctx, ["settings"]), ["settings"], 100)
    assert doc["c"]["settings"]["theme"] == {"t": 100, "v": "nord"}
    assert not sync.stamp(doc, sync.collect(a.ctx, ["settings"]), ["settings"], 200)
    a.settings.setValue("theme", "dawn")
    a.settings.remove("accent")
    assert sync.stamp(doc, sync.collect(a.ctx, ["settings"]), ["settings"], 300)
    assert doc["c"]["settings"]["theme"] == {"t": 300, "v": "dawn"}
    assert doc["c"]["settings"]["accent"] == {"t": 300, "d": 1}
    # Joining: this computer's own get the time 0 and lose where others differ.
    fresh = sync.empty_doc()
    sync.stamp(fresh, sync.collect(a.ctx, ["settings"]), ["settings"], 400, fresh=True)
    assert fresh["c"]["settings"]["theme"]["t"] == 0


def test_history_keeps_the_newest_of_each_title_and_prunes_old_tombstones():
    fp = "f" * 20
    old = {"t": 100, "v": {"_key": "1", "_kind": "live", "_watched_at": "x", "name": "old"}}
    new = {"t": 200, "v": {"_key": "1", "_kind": "live", "_watched_at": "y", "name": "new"}}
    ident = json.dumps([fp, "h", "live", "1"])
    m = sync.merge({"v": 1, "c": {"history": {ident: old}}}, {"v": 1, "c": {"history": {ident: new}}})
    assert m["c"]["history"][ident] is new
    gone = {"v": 1, "c": {"settings": {"theme": {"t": 1, "d": 1}, "accent": {"t": 10**10, "d": 1}}}}
    sync.prune(gone, int(time.time()))
    assert list(gone["c"]["settings"]) == ["accent"]
    many = {"v": 1, "c": {"history": {json.dumps([fp, "h", "live", str(i)]):
                                      {"t": i, "v": {"_key": str(i)}} for i in range(400)}}}
    sync.prune(many, 1000)
    assert len(many["c"]["history"]) == sync.MAX_HISTORY
    assert json.dumps([fp, "h", "live", "399"]) in many["c"]["history"]


# -- two computers through the site ------------------------------------------------------------

def test_first_upload_join_and_both_ways(tmp_path, site):
    raw = sync.parse_code(sync.new_code())
    slot, key = sync.derive(raw)
    remote = sync.Remote(slot, site.url, timeout=5)
    a = Computer(tmp_path, "a", slot)
    pid = a.playlists.add(dict(PROVIDER))["id"]
    a.favs(pid).add("all", {"stream_id": 1, "name": "One"})
    a.favs(pid).add("Sports", {"stream_id": 2, "name": "Two"})
    a.settings.setValue("theme", "nord")
    a.settings.setValue("window_geometry", "a-geometry")
    result, _applied = a.round(remote, key, now=1000)
    assert result.uploaded and site.slots[slot][0] == 1
    stored = base64.b64decode(site.slots[slot][1])
    for secret in (b"nord", b"anna", b"iptv.example", b"One"):
        assert secret not in stored                  # the site keeps ciphertext only
    # Nothing about the person goes along: the app's name, and the ciphertext.
    assert site.headers and all("supporter" not in " ".join(h) for h in site.headers)
    assert {h["user-agent"].split("/")[0] for h in site.headers} == {"dopeIPTV"}

    # Nothing changed: a round only looks.
    puts = site.puts()
    result, applied = a.round(remote, key, now=1100)
    assert not result.uploaded and site.puts() == puts and not applied.any()

    # B joins: what A had arrives, B's own is added, A's wins where both differ.
    b = Computer(tmp_path, "b", slot)
    b.state.fresh = b.state.joining = True
    b.settings.setValue("theme", "graphite")
    b.settings.setValue("accent", "green")
    b.settings.setValue("window_geometry", "b-geometry")
    result, applied = b.round(remote, key, now=2000)
    assert b.settings.value("theme") == "nord" and b.settings.value("accent") == "green"
    assert b.settings.value("window_geometry") == "b-geometry"
    assert [p["server"] for p in b.playlists.items] == [PROVIDER["server"]]
    bpid = b.playlists.items[0]["id"]
    assert bpid == pid and b.playlists.items[0]["password"] == "pw"
    assert {x["stream_id"] for x in b.favs(bpid).items()} == {1, 2}
    assert "Sports" in b.favs(bpid).groups and applied.sources

    # A gets B's accent; a favorite removed on B goes on A too.
    b.favs(bpid).remove(2)
    b.round(remote, key, now=3000)
    a.round(remote, key, now=4000)
    assert a.settings.value("accent") == "green"
    assert {x["stream_id"] for x in a.favs(pid).items()} == {1}
    assert a.settings.value("window_geometry") == "a-geometry"


def test_an_older_backup_restored_takes_nothing_from_the_others(tmp_path, site):
    """A backup put in place over everything (core/backup.apply_pending, then
    sync.rejoin at start): the restored computer joins again as a new one -
    the others' newer values come back to it and nothing the backup lacks
    is removed from them. Without it the old values were stamped as new
    and won everywhere, and what they lacked went on every computer."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    pid = a.playlists.add(dict(PROVIDER))["id"]
    a.favs(pid).add("all", {"stream_id": 1, "name": "One"})
    a.settings.setValue("theme", "nord")
    a.round(remote, key, now=1000)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=2000)
    a.favs(pid).add("all", {"stream_id": 2, "name": "Two"})
    a.settings.setValue("theme", "dracula")
    a.round(remote, key, now=3000)
    sync.write_private(b.state.path, b.state.dump())

    # B's backup from before: the old theme, no favorite Two.
    b.settings.setValue("theme", "graphite")
    b.favs(pid).remove(1)
    b.favs(pid).add("all", {"stream_id": 1, "name": "One"})
    assert sync.rejoin(b.state.path)
    b.state = sync.State.load(b.state.path, slot)
    assert b.state.fresh and not b.state.joining and b.state.doc == sync.empty_doc()
    b.round(remote, key, now=4000)
    assert b.settings.value("theme") == "dracula"
    assert {x["stream_id"] for x in b.favs(pid).items()} == {1, 2}
    a.round(remote, key, now=5000)
    assert a.settings.value("theme") == "dracula"
    assert {x["stream_id"] for x in a.favs(pid).items()} == {1, 2}

    # Once synced the mark is gone: the next round is an ordinary one.
    sync.write_private(b.state.path, b.state.dump())
    assert not sync.State.load(b.state.path, slot).fresh


def test_rejoin_marks_any_slot_and_a_computer_that_never_synced(tmp_path):
    path = tmp_path / "sync-state.json"
    assert not sync.rejoin(path)                        # never synced here
    assert sync.State.load(path, "any-slot").fresh      # a backup brought a code
    st = sync.State(path, "slot-a")
    st.seen = {"favorites"}
    st.doc["c"]["settings"] = {"theme": {"t": 5, "v": "nord"}}
    st.version = 9
    sync.write_private(path, st.dump())
    assert sync.rejoin(path)
    got = sync.State.load(path, "slot-a")
    assert got.fresh and got.version == 0 and got.doc == sync.empty_doc()
    assert got.seen == {"favorites"}
    assert sync.State.load(path, "slot-b").fresh        # another code from the backup
    st = sync.State(path, "slot-b")
    sync.write_private(path, st.dump())
    assert not sync.State.load(path, "slot-c").fresh    # no restore: a stranger's state


def test_the_same_playlist_under_another_id_is_one_playlist(tmp_path, site):
    """Added by hand on both computers before syncing: favorites follow it by
    its address and login, and nothing is listed twice."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    apid = a.playlists.add(dict(PROVIDER, id="aaaa1111"))["id"]
    bpid = b.playlists.add(dict(PROVIDER, id="bbbb2222", name="Mine"))["id"]
    a.favs(apid).add("all", {"stream_id": 7, "name": "Seven"})
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    b.round(remote, key, now=200)
    assert [p["id"] for p in b.playlists.items] == [bpid]
    assert [x["stream_id"] for x in b.favs(bpid).items()] == [7]
    a.round(remote, key, now=300)
    assert [p["id"] for p in a.playlists.items] == [apid]
    # Removed on B (not the one in use there): it goes on A as well.
    b.playlists.add(dict(PROVIDER, id="cccc3333", server="http://other.example"))
    b.playlists.set_active("cccc3333")
    b.playlists.remove(bpid)
    b.round(remote, key, now=400)
    a.playlists.add(dict(PROVIDER, id="dddd4444", server="http://third.example"))
    a.playlists.set_active("dddd4444")
    a.round(remote, key, now=500)
    assert apid not in [p["id"] for p in a.playlists.items]
    assert "cccc3333" in [p["id"] for p in a.playlists.items]


def test_radio_stations_and_web_streams_sync_whatever_the_source(tmp_path, site):
    """Favourites of no source (mw_radio, mw_webstreams): one list each, on
    every computer, without a playlist in common - added and removed alike."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    FavoriteStore(a.settings, "radio_favorites", "stationuuid").add(
        "all", {"stationuuid": "u-jazz", "name": "Jazz", "url": "http://x/jazz"})
    FavoriteStore(a.settings, "web_favorites", "url").add(
        "Games", {"url": "https://www.twitch.tv/x", "name": "X", "_live": True})
    snap = sync.collect(a.ctx, ["favorites"])
    assert any(k.startswith('["*", "radio", "i"') for k in snap.values["favorites"])
    a.round(remote, key, now=100)
    b.state.fresh = b.state.joining = True
    _result, applied = b.round(remote, key, now=200)
    assert sync.GLOBAL_FP in applied.favorites
    radio = FavoriteStore(b.settings, "radio_favorites", "stationuuid")
    assert [x["name"] for x in radio.items()] == ["Jazz"]
    web = FavoriteStore(b.settings, "web_favorites", "url")
    assert web.groups == {"Games": [{"url": "https://www.twitch.tv/x", "name": "X", "_live": True}]}
    radio.remove("u-jazz")
    b.round(remote, key, now=300)
    a.round(remote, key, now=400)
    assert FavoriteStore(a.settings, "radio_favorites", "stationuuid").items() == []
    assert FavoriteStore(a.settings, "web_favorites", "url").is_favorite("https://www.twitch.tv/x")


def test_the_playlist_in_use_is_never_removed_by_a_sync(tmp_path, site):
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    pid = a.playlists.add(dict(PROVIDER))["id"]
    a.round(remote, key, now=100)
    b.state.fresh = True
    b.round(remote, key, now=200)
    assert b.playlists.active_id == pid
    a.playlists.add(dict(PROVIDER, id="other", server="http://two.example"))
    a.playlists.set_active("other")
    a.playlists.remove(pid)
    a.round(remote, key, now=300)
    b.round(remote, key, now=400)
    assert pid in [p["id"] for p in b.playlists.items]       # in use on B: kept
    assert "other" in [p["id"] for p in b.playlists.items]


def test_a_category_turned_on_later_takes_what_the_others_have(tmp_path, site):
    """Playlists turned on on B long after A synced them: A's newer password
    wins over B's old copy, and B's own playlist is added for A."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    pid = a.playlists.add(dict(PROVIDER))["id"]
    a.round(remote, key, now=100)
    b.playlists.add(dict(PROVIDER, id=pid, password="old"))
    b.playlists.add(dict(PROVIDER, id="bb", server="http://b-only.example"))
    b.state.fresh = True
    b.round(remote, key, ["settings"], now=200)
    a.playlists.update(pid, password="new")
    a.round(remote, key, now=300)
    b.round(remote, key, now=400)
    assert b.playlists.get(pid)["password"] == "new"
    a.round(remote, key, now=500)
    assert a.playlists.get("bb") is not None and a.playlists.get(pid)["password"] == "new"


# -- keyboard shortcuts, per system ---------------------------------------------------------

def _keys(computer):
    return shortcut_keys.parse(computer.settings.value(shortcut_keys.SETTING, "") or "")


def _site_doc(site, slot, key):
    return sync.unseal(key, base64.b64decode(site.slots[slot][1]))


def test_shortcuts_sync_between_computers_of_one_system_only(tmp_path, site):
    """⌘ is not Ctrl, and each system keeps keys of its own: a Mac's keys
    reach the other Mac, never the Linux computer, and each keeps its own."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    mac = Computer(tmp_path, "mac", slot, "darwin")
    mac2 = Computer(tmp_path, "mac2", slot, "darwin")
    lin = Computer(tmp_path, "lin", slot, "linux")
    mac.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"palette": "Ctrl+Shift+P"}))
    lin.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"mute": "J"}))
    mac.round(remote, key, now=100)
    lin.state.fresh = lin.state.joining = True
    _result, applied = lin.round(remote, key, now=200)
    assert _keys(lin) == {"mute": "J"} and shortcut_keys.SETTING not in applied.settings
    mac2.state.fresh = mac2.state.joining = True
    _result, applied = mac2.round(remote, key, now=300)
    assert _keys(mac2) == {"palette": "Ctrl+Shift+P"} and shortcut_keys.SETTING in applied.settings
    mac.round(remote, key, now=400)
    lin.round(remote, key, now=500)
    assert _keys(mac) == {"palette": "Ctrl+Shift+P"} and _keys(lin) == {"mute": "J"}
    there = sync.live(_site_doc(site, slot, key), "settings")
    assert json.loads(there["keyboard_shortcuts@macos"]) == {"palette": "Ctrl+Shift+P"}
    assert json.loads(there["keyboard_shortcuts@linux"]) == {"mute": "J"}
    assert shortcut_keys.SETTING not in there                   # never a system's name left out

    # Two Macs share: a change on the second reaches the first; Linux keeps its own.
    mac2.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "K"}))
    mac2.round(remote, key, now=600)
    lin.round(remote, key, now=700)
    mac.round(remote, key, now=800)
    assert _keys(mac) == {"pause": "K"} and _keys(lin) == {"mute": "J"}
    # And Linux's change stays with Linux.
    lin.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"mute": "U"}))
    lin.round(remote, key, now=900)
    for c in (mac, mac2):
        c.round(remote, key, now=1000)
        assert _keys(c) == {"pause": "K"}


def test_a_reset_or_a_removal_on_one_mac_reaches_the_other_mac_only(tmp_path, site):
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    mac = Computer(tmp_path, "mac", slot, "darwin")
    mac2 = Computer(tmp_path, "mac2", slot, "darwin")
    lin = Computer(tmp_path, "lin", slot, "linux")
    mac.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "K"}))
    lin.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "L"}))
    mac.round(remote, key, now=100)
    for c, t in ((mac2, 200), (lin, 300)):
        c.state.fresh = c.state.joining = True
        c.round(remote, key, now=t)
    assert _keys(mac2) == {"pause": "K"}

    # Reset all (Settings stores nothing over the app's own: "").
    mac.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({}))
    mac.round(remote, key, now=400)
    mac2.round(remote, key, now=500)
    lin.round(remote, key, now=600)
    assert shortcut_keys.from_settings(mac2.settings) == shortcut_keys.DEFAULTS
    assert _keys(lin) == {"pause": "L"}

    # Given keys again on the second Mac, then the setting taken out on the first.
    mac2.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"stats": "F9"}))
    mac2.round(remote, key, now=700)
    mac.round(remote, key, now=800)
    assert _keys(mac) == {"stats": "F9"}
    mac.settings.remove(shortcut_keys.SETTING)
    mac.round(remote, key, now=900)
    mac2.round(remote, key, now=1000)
    lin.round(remote, key, now=1100)
    assert mac2.settings.value(shortcut_keys.SETTING) is None
    assert _keys(lin) == {"pause": "L"}
    entries = _site_doc(site, slot, key)["c"]["settings"]
    assert entries["keyboard_shortcuts@macos"].get("d") == 1
    assert json.loads(entries["keyboard_shortcuts@linux"]["v"]) == {"pause": "L"}


def test_shortcuts_of_no_system_and_the_old_editors_keys_are_left_as_they_are(tmp_path, site):
    """No version sent shortcuts without a system (1.5.0 and 1.6.0 sent
    none at all) and which system such an entry was for cannot be told: on
    any computer it is neither applied nor removed - and so are the old
    editor's per-platform keys, which no longer sync."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    old = {"keyboard_shortcuts": {"t": 50, "v": json.dumps({"mute": "Ctrl+M"})},
           "shortcut/darwin/mute": {"t": 50, "v": "J"},
           "theme": {"t": 50, "v": "nord"}}
    site.slots[slot] = (1, base64.b64encode(
        sync.seal(key, {"v": sync.FORMAT, "c": {"settings": old}})).decode("ascii"))
    mac = Computer(tmp_path, "mac", slot, "darwin")
    lin = Computer(tmp_path, "lin", slot, "linux")
    lin.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "K"}))
    for c, t in ((mac, 100), (lin, 200)):
        c.state.fresh = c.state.joining = True
        c.round(remote, key, now=t)
        assert c.settings.value("theme") == "nord"
        assert c.settings.value("shortcut/darwin/mute") is None
    assert mac.settings.value(shortcut_keys.SETTING) is None
    assert _keys(lin) == {"pause": "K"}
    for c, t in ((mac, 300), (lin, 400)):
        c.round(remote, key, now=t)
    entries = _site_doc(site, slot, key)["c"]["settings"]
    assert entries["keyboard_shortcuts"] == old["keyboard_shortcuts"]
    assert entries["shortcut/darwin/mute"] == old["shortcut/darwin/mute"]


def test_the_first_round_after_the_update_takes_this_systems_keys_and_overrides_nothing(
        tmp_path, site):
    """A Mac that synced on 1.6.0 holds the other Mac's keys in its copy but
    never sent its own. Its first round on 1.6.1 is a newcomer's for them:
    its own lose to what is there, and none of its own is no removal."""
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    mac = Computer(tmp_path, "mac", slot, "darwin")
    mac.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"palette": "Ctrl+Shift+P"}))
    mac.round(remote, key, now=100)
    assert sync.SEEN_SHORTCUTS in mac.state.seen

    def updated(name, keys):
        # Synced on 1.6.0: every category seen, the copy holding the other's keys.
        c = Computer(tmp_path, name, slot, "darwin")
        c.state.seen = set(ALL)
        c.state.doc = _site_doc(site, slot, key)
        if keys:
            c.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump(keys))
        return c

    plain = updated("plain", {})
    plain.round(remote, key, now=200)
    mac.round(remote, key, now=300)
    assert _keys(plain) == _keys(mac) == {"palette": "Ctrl+Shift+P"}
    assert sync.SEEN_SHORTCUTS in plain.state.seen

    own = updated("own", {"pause": "K"})
    own.round(remote, key, now=400)
    mac.round(remote, key, now=500)
    assert _keys(own) == _keys(mac) == {"palette": "Ctrl+Shift+P"}
    # From then on its changes are anyone's.
    own.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "K"}))
    own.round(remote, key, now=600)
    mac.round(remote, key, now=700)
    assert _keys(mac) == {"pause": "K"}

    # The first of its system to update: its keys are the start.
    lin = Computer(tmp_path, "lin", slot, "linux")
    lin.state.seen = set(ALL)
    lin.state.doc = _site_doc(site, slot, key)
    lin.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"mute": "J"}))
    lin.round(remote, key, now=800)
    there = _site_doc(site, slot, key)["c"]["settings"]["keyboard_shortcuts@linux"]
    assert there["t"] == 800 and json.loads(there["v"]) == {"mute": "J"}


def test_which_system_an_entry_is():
    assert [sync.system_of(p) for p in ("darwin", "linux", "win32", "cygwin", "freebsd14")] \
        == ["macos", "linux", "windows", "windows", "freebsd"]
    assert sync.entry_of("keyboard_shortcuts", "darwin") == "keyboard_shortcuts@macos"
    assert sync.entry_of("theme", "darwin") == "theme"
    assert sync.setting_of("keyboard_shortcuts@macos", "darwin") == "keyboard_shortcuts"
    for ident in ("keyboard_shortcuts@linux", "keyboard_shortcuts@windows",
                  "keyboard_shortcuts", "keyboard_shortcuts@", "shortcut/darwin/mute",
                  "window_geometry"):
        assert sync.setting_of(ident, "darwin") is None, ident
    assert sync.setting_of("theme", "darwin") == "theme"
    # An entry 1.6.0 does not know: it syncs only what syncable() allows.
    for p in ("darwin", "linux", "win32"):
        assert not sync.syncable(sync.entry_of(shortcut_keys.SETTING, p))


class Racing(sync.Remote):
    """A Remote that lets another computer sync between its GET and PUT."""

    between = None

    def get(self):
        got = super().get()
        run, self.between = self.between, None
        if run is not None:
            run()
        return got


def test_two_computers_at_once_conflict_and_both_changes_stay(tmp_path, site):
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    pid = a.playlists.add(dict(PROVIDER))["id"]
    a.round(remote, key, now=100)
    b.state.fresh = True
    b.round(remote, key, now=200)
    a.favs(pid).add("all", {"stream_id": 10, "name": "From A"})
    a.settings.setValue("theme", "nord")
    b.favs(pid).add("all", {"stream_id": 20, "name": "From B"})
    b.settings.setValue("accent", "red")
    racing = Racing(slot, site.url, timeout=5)
    racing.between = lambda: a.round(remote, key, now=300)
    version_before = site.slots[slot][0]
    result, _applied = b.round(racing, key, now=310)
    conflicts = [c for c in site.calls if c[0] == "PUT"]
    assert result.uploaded and site.slots[slot][0] == version_before + 2
    assert len(conflicts) >= 3                  # A's, B's refused one, B's again
    a.round(remote, key, now=400)
    for c in (a, b):
        assert {x["stream_id"] for x in c.favs(pid).items()} == {10, 20}
        assert c.settings.value("theme") == "nord" and c.settings.value("accent") == "red"


def test_joining_a_code_with_nothing_under_it_a_full_site_and_a_refusal(tmp_path, site):
    slot, key = sync.derive(os.urandom(32))
    b = Computer(tmp_path, "b", slot)
    b.state.fresh = b.state.joining = True
    with pytest.raises(sync.SyncError) as e:
        b.round(sync.Remote(slot, site.url, timeout=5), key)
    assert e.value.kind == "none" and not site.slots
    # Full: a new copy waits, one kept there already is still written.
    site.full = True
    with pytest.raises(sync.SyncError) as e:
        Computer(tmp_path, "c", slot).round(sync.Remote(slot, site.url, timeout=5), key)
    assert e.value.kind == "full" and not site.slots
    site.full = False
    a = Computer(tmp_path, "a", slot)
    a.settings.setValue("theme", "nord")
    a.round(sync.Remote(slot, site.url, timeout=5), key, ["settings"], now=100)
    site.full = True
    a.settings.setValue("theme", "dawn")
    result, _applied = a.round(sync.Remote(slot, site.url, timeout=5), key, ["settings"],
                               now=200)
    assert result.uploaded and site.slots[slot][0] == 2
    site.full = False
    for status in (401, 403):
        site.refuse = status
        with pytest.raises(sync.SyncError) as e:
            Computer(tmp_path, f"r{status}", slot).round(
                sync.Remote(slot, site.url, timeout=5), key)
        assert e.value.kind == "refused"
    site.refuse = 0
    with pytest.raises(sync.SyncError) as e:
        Computer(tmp_path, "d", slot).round(
            sync.Remote(slot, "http://127.0.0.1:9/sync/", timeout=2), key)
    assert e.value.kind == "net"


def test_too_much_is_refused_before_it_is_sent(tmp_path, site, monkeypatch):
    monkeypatch.setattr(sync, "MAX_BLOB", 2000)
    slot, key = sync.derive(os.urandom(32))
    a = Computer(tmp_path, "a", slot)
    pid = a.playlists.add(dict(PROVIDER))["id"]
    for i in range(200):
        a.favs(pid).add("all", {"stream_id": i, "name": os.urandom(16).hex()})
    with pytest.raises(sync.SyncError) as e:
        a.round(sync.Remote(slot, site.url, timeout=5), key, ["favorites"])
    assert e.value.kind == "big" and e.value.size > 2000 and site.puts() == 0


def test_a_change_made_while_a_round_ran_is_left_for_the_next(tmp_path, site):
    slot, key = sync.derive(os.urandom(32))
    remote = sync.Remote(slot, site.url, timeout=5)
    a, b = Computer(tmp_path, "a", slot), Computer(tmp_path, "b", slot)
    a.settings.setValue("theme", "nord")
    a.round(remote, key, ["settings"], now=100)
    b.state.fresh = True
    snap = sync.collect(b.ctx, ["settings"])
    result = sync.round_trip(remote, key, b.state.doc, snap, ["settings"], now=200, fresh=True)
    b.settings.setValue("accent", "red")              # meanwhile, on B
    applied, keep = sync.apply(b.ctx, result, snap, sync.collect(b.ctx, ["settings"]), ["settings"])
    assert applied.skipped == {"settings"} and b.settings.value("theme") is None
    assert b.settings.value("accent") == "red"
    b.state.doc, b.state.fresh = keep, False
    b.round(remote, key, ["settings"], now=300)
    assert b.settings.value("theme") == "nord" and b.settings.value("accent") == "red"


def test_the_copy_here_is_written_for_this_user_only(tmp_path):
    st = sync.State(tmp_path / "sync-state.json", "ab" * 32)
    st.doc = {"v": 1, "c": {"settings": {"theme": {"t": 1, "v": "nord"}}}}
    st.version, st.fresh = 3, True
    sync.write_private(st.path, st.dump())
    if os.name == "posix":
        assert os.stat(st.path).st_mode & 0o077 == 0
    back = sync.State.load(st.path, "ab" * 32)
    assert (back.doc, back.version, back.fresh) == (st.doc, 3, True)
    assert sync.State.load(st.path, "cd" * 32).doc == sync.empty_doc()   # another code
    sync.forget(st.path)
    assert not st.path.exists()


# -- the window's side --------------------------------------------------------------------------

@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _window(tmp_path, name, site):
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.ui.mw_sync import _SyncMixin

    class Win(_SyncMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.settings = QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat)
            self._resume_settings = QSettings(str(tmp_path / f"{name}-r.ini"),
                                              QSettings.Format.IniFormat)
            self.playlist_store = PlaylistStore(self.settings)
            self._sync_endpoint = site.url
            self._sync_state_file = tmp_path / f"{name}-sync.json"
            self.toasts = []
            self.themed = 0
            self.keys_applied = 0
            self._init_sync()

        def _show_toast(self, text, ms=0):
            self.toasts.append(text)

        def _apply_theme_now(self):
            self.themed += 1

        def apply_shortcuts(self):
            self.keys_applied += 1

    return Win()


def _settle(w, secs=10.0):
    from PyQt6.QtWidgets import QApplication
    end = time.monotonic() + secs
    while time.monotonic() < end:
        QApplication.processEvents()
        if not w._sync_busy:
            w._sync_pool.waitForDone(2000)
            QApplication.processEvents()
            return True
        time.sleep(0.01)
    return False


def test_off_nothing_runs_and_anyone_can_start(tmp_path, site, qapp):
    """Sync is for everyone: no supporter key, and none sent when there is."""
    w = _window(tmp_path, "w", site)
    assert not w._sync_on() and not w._sync_timer.isActive()
    assert not w.sync_now(manual=True)
    w._sync_tick()
    assert site.calls == []
    assert w.settings.value("supporter_key") is None
    code = w._sync_start_here()                                   # no key at all
    assert sync.parse_code(code) and w._sync_on() and _settle(w)
    assert w._sync_status[0] == "ok" and len(site.slots) == 1
    w.settings.setValue("supporter_key", _key())                  # a supporter's
    w.settings.setValue("theme", "nord")
    assert w.sync_now(manual=True) and _settle(w) and w._sync_status[0] == "ok"
    for h in site.headers:
        assert "supporter" not in " ".join(h) and _key() not in " ".join(h.values())
    w._sync_forget()
    w._sync_pool.waitForDone(2000)


def _shown_rows(card):
    out = []
    for row, _text in card.rows:
        if not row.isHidden():
            label = getattr(row, "label", None)
            out.append(label.text() if label is not None else "widget")
    return out


def test_the_settings_page_offers_sync_to_everyone_and_says_what_is_sent(
        tmp_path, site, qapp):
    from PyQt6.QtWidgets import QDialog, QLabel

    from dopeiptv.ui.mw_sync import _SyncCard
    from dopeiptv.ui.settings_ui import SettingsPage

    w = _window(tmp_path, "w", site)
    dlg = QDialog()
    page = SettingsPage("sync", tr("sync_title"), nav_title=tr("sync_nav"))
    w._build_sync_card(page, dlg)
    card, privacy = page.cards
    assert _shown_rows(card) == [tr("sync_title")]                   # start or join, no key
    # What dopeiptv.org gets, said on the page, on or off.
    assert privacy.property("_title").text() == tr("sync_privacy_title")
    assert tr("sync_privacy") in [lbl.text() for lbl in privacy.findChildren(QLabel)]
    for word in ("encrypted", "random number", "cannot read", "No account", "180 days"):
        assert word in tr("sync_privacy")
    assert "supporter" not in (tr("sync_privacy") + tr("set_page_sync_sub")
                               + tr("sync_off_hint")).lower()
    assert page.nav_title == "Sync" and page.title == "Sync between computers"
    assert page.apply_filter("sync")
    dlg2 = QDialog()
    card2 = SettingsPage("sync", "Sync").card("Sync")
    ui = _SyncCard(w, card2, dlg2)
    ui.start()
    assert _settle(w)
    shown = _shown_rows(card2)
    assert tr("sync_code") in shown and tr("sync_stop") in shown
    assert w.settings.value(sync.K_CODE) in ui.code_row.hint_label.text()   # shown once made
    w._sync_status = ("full", 0)
    assert w._sync_status_text()[1] == tr("sync_err_full")
    w._sync_forget()
    dlg.deleteLater()
    dlg2.deleteLater()


def test_two_windows_start_join_and_sync_both_ways(tmp_path, site, qapp):
    a = _window(tmp_path, "a", site)
    b = _window(tmp_path, "b", site)
    for w in (a, b):
        sync.set_chosen(w.settings, ALL)          # playlists too (off by default)
    pid = a.playlist_store.add(dict(PROVIDER))["id"]
    FavoriteStore(a.settings, f"favorites_{pid}").add("all", {"stream_id": 5, "name": "Five"})
    a.settings.setValue("theme", "nord")
    a.settings.setValue("language", "sv")
    code = a._sync_start_here()
    assert code and a._sync_on() and _settle(a)
    assert a._sync_status[0] == "ok" and len(site.slots) == 1
    assert json.loads(a._sync_state_file.read_text(encoding="utf-8"))["version"] == 1

    assert not b._sync_join("not a code")
    assert b._sync_join(code.lower()) and _settle(b)
    assert b._sync_status[0] == "ok"
    assert b.settings.value("theme") == "nord" and b.themed == 1
    assert b.toasts[-1] == tr("sync_applied_restart")      # the language waits for a start
    assert b.playlist_store.items[0]["id"] == pid
    assert [x["stream_id"] for x in FavoriteStore(b.settings, f"favorites_{pid}").items()] == [5]

    # A change on B reaches A; the window's own stores are read again.
    b.playlist_store.set_active(pid)
    FavoriteStore(b.settings, f"favorites_{pid}").add("Kids", {"stream_id": 6, "name": "Six"})
    assert b.sync_now(manual=True) and _settle(b)
    a.playlist_store.set_active(pid)
    assert a.sync_now(manual=True) and _settle(a)
    assert {x["stream_id"] for x in a.favs.items()} == {5, 6}

    # Stop on B, deleting the copy there: gone for good, A starts it again.
    b._sync_stop(True)
    b._sync_pool.waitForDone(5000)
    assert not b._sync_on() and not site.slots and not b._sync_state_file.exists()
    assert a.sync_now(manual=True) and _settle(a) and len(site.slots) == 1


def test_a_typo_in_a_code_does_not_join(tmp_path, site, qapp):
    w = _window(tmp_path, "w", site)
    assert w._sync_join(sync.new_code()) and _settle(w)
    assert w._sync_status[0] == "none" and not w._sync_on() and not site.slots


def test_no_round_in_a_streams_first_seconds_and_a_change_is_noticed(tmp_path, site, qapp):
    w = _window(tmp_path, "w", site)
    assert w._sync_start_here() and _settle(w)
    calls = len(site.calls)
    w._sync_mark_play()
    assert not w.sync_now() and len(site.calls) == calls          # waits
    w._sync_play_started -= 60
    w._sync_tick()
    assert not w._sync_busy                                        # nothing changed, not due
    w.settings.setValue("theme", "nord")
    w._sync_tick()                                                  # seen, not yet quiet
    assert not w._sync_busy
    w._sync_seen_at -= 60
    w._sync_tick()
    assert w._sync_busy and _settle(w) and len(site.calls) > calls
    w._sync_forget()


def test_closing_keeps_when_a_change_was_made(tmp_path, site, qapp):
    """A change made just before closing gets its time then - not at the
    next start, where it would beat a later change on another computer."""
    w = _window(tmp_path, "w", site)
    assert w._sync_start_here() and _settle(w)
    w.settings.setValue("theme", "dawn")
    before = int(time.time())
    w._sync_on_close()
    data = json.loads(w._sync_state_file.read_text(encoding="utf-8"))
    entry = data["doc"]["c"]["settings"]["theme"]
    assert entry["v"] == "dawn" and entry["t"] >= before
    calls = len(site.calls)
    w._sync_on_close()                                   # nothing new: nothing written, no network
    assert len(site.calls) == calls
    w._sync_forget()
    w._sync_pool.waitForDone(2000)


def test_shortcuts_from_the_other_computer_apply_at_once(tmp_path, site, qapp):
    a = _window(tmp_path, "a", site)
    b = _window(tmp_path, "b", site)
    a.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"pause": "K"}))
    code = a._sync_start_here()
    assert _settle(a)
    assert b._sync_join(code) and _settle(b)
    assert _keys(b) == {"pause": "K"} and b.keys_applied == 1
    assert b.toasts[-1] == tr("sync_applied")                    # no restart for keys
    assert sync.SEEN_SHORTCUTS in json.loads(b._sync_state_file.read_text(encoding="utf-8"))["seen"]
    for w in (a, b):
        w._sync_forget()
        w._sync_pool.waitForDone(2000)


def test_closing_before_the_first_round_after_the_update_removes_no_ones_keys(
        tmp_path, site, qapp):
    """Updated from 1.6.0 and closed before a round: no keys of its own is
    no removal of the other computer's, and its own wait to lose to them."""
    w = _window(tmp_path, "w", site)
    assert w._sync_start_here() and _settle(w)
    state = w._sync_state
    mine = sync.entry_of(shortcut_keys.SETTING)
    other = {"t": 100, "v": shortcut_keys.dump({"pause": "K"})}
    state.seen.discard(sync.SEEN_SHORTCUTS)
    state.doc["c"].setdefault("settings", {})[mine] = other
    w._sync_on_close()
    assert state.doc["c"]["settings"][mine] == other
    w.settings.setValue(shortcut_keys.SETTING, shortcut_keys.dump({"mute": "J"}))
    w._sync_on_close()
    assert state.doc["c"]["settings"][mine]["t"] == 0
    w._sync_forget()
    w._sync_pool.waitForDone(2000)
