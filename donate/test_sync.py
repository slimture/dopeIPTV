#!/usr/bin/env python3
"""Tests for settings sync (sync.py, and its routes in donate.py): for
everyone, no key - and the limits that come with that: per slot, per
address, and for all slots together.

Plain Python: ``python3 donate/test_sync.py`` runs them all (pytest finds
them too: ``python3 -m pytest donate/test_sync.py``). Everything in a
temporary directory; the service on 127.0.0.1 for the HTTP part.
"""

from __future__ import annotations

import base64
import contextlib
import http.client
import io
import json
import os
import secrets
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import sync  # noqa: E402

SLOT = "ab" * 32


def _blob(n: int = 64) -> str:
    return base64.b64encode(secrets.token_bytes(n)).decode()


def test_put_get_versions_and_conflicts():
    d = tempfile.mkdtemp()
    assert sync.get(d, SLOT) is None
    status, body = sync.put(d, SLOT, 0, _blob())
    assert status == 200 and body["version"] == 1
    got = sync.get(d, SLOT)
    assert got["version"] == 1 and got["t"] > 0 and base64.b64decode(got["blob"])
    status, body = sync.put(d, SLOT, 0, _blob())          # stale: another computer was quicker
    assert status == 409 and body["version"] == 1 and body["blob"] == got["blob"]
    blob2 = _blob()
    assert sync.put(d, SLOT, 1, blob2) == (200, {"version": 2, "t": sync.get(d, SLOT)["t"]})
    assert sync.get(d, SLOT)["blob"] == blob2
    assert sync.delete(d, SLOT) and not sync.delete(d, SLOT)
    assert sync.get(d, SLOT) is None
    status, body = sync.put(d, SLOT, 2, _blob())           # deleted meanwhile: 409, no copy
    assert status == 409 and body["version"] == 0 and "blob" not in body


def test_what_is_refused():
    d = tempfile.mkdtemp()
    for bad in ("", "AB" * 32, "ab" * 31, "../" + "a" * 61, "g" * 64):
        try:
            sync.put(d, bad, 0, _blob())
        except ValueError:
            continue
        raise AssertionError(bad)
    assert sync.put(d, SLOT, -1, _blob())[0] == 400
    assert sync.put(d, SLOT, "0", _blob())[0] == 400
    assert sync.put(d, SLOT, True, _blob())[0] == 400
    assert sync.put(d, SLOT, 0, "not base64!")[0] == 400
    assert sync.put(d, SLOT, 0, _blob(10))[0] == 400            # shorter than any real one
    assert sync.put(d, SLOT, 0, 12345)[0] == 400
    big = base64.b64encode(b"\0" * (sync.MAX_BLOB + 1)).decode()
    assert sync.put(d, SLOT, 0, big)[0] == 413
    exact = base64.b64encode(b"\0" * sync.MAX_BLOB).decode()
    assert sync.put(d, SLOT, 0, exact)[0] == 200
    assert os.listdir(sync.folder(d))                 # the exact limit was kept


def test_written_whole_for_the_service_only():
    d = tempfile.mkdtemp()
    sync.put(d, SLOT, 0, _blob())
    path = os.path.join(sync.folder(d), SLOT + ".json")
    if os.name == "posix":
        assert os.stat(path).st_mode & 0o077 == 0
        assert os.stat(sync.folder(d)).st_mode & 0o077 == 0
    with open(path, encoding="ascii") as fh:
        assert set(json.load(fh)) == {"version", "t", "blob"}      # nothing else is kept
    assert [n for n in os.listdir(sync.folder(d)) if n.endswith(".tmp")] == []


def test_one_writer_wins_a_race():
    d = tempfile.mkdtemp()
    sync.put(d, SLOT, 0, _blob())
    results = []
    start = threading.Event()

    def writer():
        start.wait()
        results.append(sync.put(d, SLOT, 1, _blob())[0])

    threads = [threading.Thread(target=writer) for _ in range(12)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join()
    assert sorted(results) == [200] + [409] * 11
    assert sync.get(d, SLOT)["version"] == 2


def test_unused_slots_go_and_a_read_keeps_one():
    assert sync.KEEP_DAYS == 180                         # what the privacy policy says
    d = tempfile.mkdtemp()
    old, used, fresh = "01" * 32, "02" * 32, "03" * 32
    for slot in (old, used, fresh):
        sync.put(d, slot, 0, _blob())
    long_ago = time.time() - (sync.KEEP_DAYS + 1) * 86400
    for slot in (old, used):
        path = os.path.join(sync.folder(d), slot + ".json")
        os.utime(path, (long_ago, long_ago))
    stray = os.path.join(sync.folder(d), ".x.tmp")
    open(stray, "w").close()
    os.utime(stray, (long_ago, long_ago))
    assert sync.get(d, used) is not None                 # read: its time moves on
    assert sync.cleanup(d) == 1
    assert sync.get(d, old) is None and sync.get(d, used) and sync.get(d, fresh)
    assert not os.path.exists(stray)
    assert sync.cleanup(tempfile.mkdtemp()) == 0         # no sync folder yet
    assert sync.usage(d)["slots"] == 2                   # counted again after it


def _limit(monkeypatch_attrs: dict):
    """Set module attributes for a test; returns a function putting them back."""
    old = {(m, k): getattr(m, k) for (m, k) in monkeypatch_attrs}
    for (m, k), v in monkeypatch_attrs.items():
        setattr(m, k, v)
    return lambda: [setattr(m, k, v) for (m, k), v in old.items()]


def test_past_the_space_for_all_a_new_slot_waits_and_the_rest_are_written():
    d = tempfile.mkdtemp()
    back = _limit({(sync, "MAX_TOTAL"): 4000})
    try:
        one, two, three = "11" * 32, "12" * 32, "13" * 32
        assert sync.put(d, one, 0, _blob(1000))[0] == 200
        assert sync.put(d, two, 0, _blob(1000))[0] == 200
        used = sync.usage(d)
        assert used["slots"] == 2 and 2000 < used["bytes"] < 4000 and used["most"] == 4000
        status, body = sync.put(d, three, 0, _blob(2000))
        assert (status, body) == (507, {"error": "full"}) and sync.get(d, three) is None
        assert sync.put(d, one, 1, _blob(2000))[0] == 200          # one there: still written
        assert sync.usage(d)["slots"] == 2
        assert sync.usage(d, recount=True)["bytes"] == sync.usage(d)["bytes"]
        assert sync.delete(d, two)
        assert sync.put(d, three, 0, _blob(500))[0] == 200          # room again
        assert sync.usage(d) == sync.usage(d, recount=True)
    finally:
        back()


def test_slots_made_small_cannot_all_grow_past_the_space():
    """A new slot is refused once the space is full, but one there already
    is still written - so many made small could each grow to MAX_BLOB after.
    Growing stops at GROW_SHARE of the space; a slot rewritten no larger
    (a household that syncs) is taken as before."""
    d = tempfile.mkdtemp()
    back = _limit({(sync, "MAX_TOTAL"): 4000})
    try:
        slots = [f"{i:02x}" * 32 for i in range(1, 9)]
        for slot in slots:
            assert sync.put(d, slot, 0, _blob(100))[0] == 200
        grown = [sync.put(d, slot, 1, _blob(2000))[0] for slot in slots]
        assert grown[0] == 200 and grown[-1] == 507
        assert sync.usage(d, recount=True)["bytes"] <= 4000 * sync.GROW_SHARE
        kept = sync.get(d, slots[-1])
        assert kept["version"] == 1                                 # as it was
        assert sync.put(d, slots[-1], 1, _blob(100))[0] == 200      # no larger: written
        assert sync.put(d, slots[0], 2, _blob(1500))[0] == 200      # smaller: written
    finally:
        back()


def test_past_80_percent_it_says_so():
    d = tempfile.mkdtemp()
    sync.put(d, SLOT, 0, _blob(1000))
    size = sync.usage(d)["bytes"]
    back = _limit({(sync, "MAX_TOTAL"): int(size / 0.79)})
    try:
        assert not sync.usage(d)["warn"]
        sync.put(d, SLOT, 1, _blob(1100))
        used = sync.usage(d)
        assert used["warn"] and not used["full"] and 0.8 <= used["share"] < 1
        sync.put(d, SLOT, 2, _blob(1400))
        assert sync.usage(d)["full"]
    finally:
        back()


# -- the routes in donate.py ----------------------------------------------------------

def _service():
    """donate.py's Handler on 127.0.0.1, with a statistics folder of its
    own."""
    root = tempfile.mkdtemp()
    conf = os.path.join(root, "donate.conf")
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nLISTEN=127.0.0.1:0\n")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    sys.modules.pop("donate", None)
    import donate
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), donate.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # An older app's supporter key, in the header it sent: never read.
    key = "DS1-" + secrets.token_urlsafe(75)
    return srv, key, stats, donate


def _call(srv, method, path, key=None, body=None, ip="198.51.100.7", headers=None):
    """A request as the app makes it; *key*: an old app's supporter key,
    sent as it was in X-Dopeiptv-Supporter."""
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    h = {"X-Real-IP": ip}
    if key is not None:
        h["X-Dopeiptv-Supporter"] = key
    data = None if body is None else json.dumps(body).encode()
    if data is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    conn.request(method, path, body=data, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:
        return r.status, {}


def test_the_routes_need_no_key_and_keep_only_ciphertext():
    srv, key, stats, donate = _service()
    try:
        path = "/sync/" + SLOT
        assert _call(srv, "GET", path)[0] == 404                          # no key: fine
        assert _call(srv, "GET", path, key="DS1-nonsense")[0] == 404      # an old app's: not read
        assert _call(srv, "GET", "/sync/xyz")[0] == 404
        blob = _blob(300)
        status, body = _call(srv, "PUT", path, None, {"version_seen": 0, "blob": blob})
        assert status == 200 and body["version"] == 1
        status, body = _call(srv, "GET", path)
        assert status == 200 and body["blob"] == blob and body["version"] == 1
        status, body = _call(srv, "GET", path, key=key)                  # with a key: the same
        assert status == 200 and body["blob"] == blob
        status, body = _call(srv, "PUT", path, key, {"version_seen": 0, "blob": _blob()})
        assert status == 409 and body["blob"] == blob
        assert _call(srv, "PUT", path, None, {"version_seen": 1})[0] == 400
        assert _call(srv, "PUT", path, None, None)[0] == 400
        status, _b = _call(srv, "PUT", path, None, None,
                           headers={"Content-Length": str(sync.MAX_BODY + 1)})
        assert status == 413
        assert _call(srv, "POST", path, None, {"version_seen": 1, "blob": _blob()})[0] == 404
        # Nothing about the person on disk: no key, only the ciphertext.
        for name in os.listdir(stats):
            full = os.path.join(stats, name)
            for f in ([os.path.join(full, x) for x in os.listdir(full)]
                      if os.path.isdir(full) else [full]):
                with open(f, "rb") as fh:
                    text = fh.read()
                assert key.encode() not in text and key[4:20].encode() not in text, f
                assert b"198.51.100.7" not in text, f
        assert _call(srv, "DELETE", path, key=key) == (200, {"deleted": True})
        assert _call(srv, "GET", path, key=key)[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_bound_slot_opens_only_to_its_credential():
    """X-Sync-Auth: the first write with one binds the slot to its SHA-256
    (all that is kept); from then on a GET, PUT or DELETE without it is
    403 - before the copy or its version is said - and a slot an older
    app wrote without one goes on as before until a newer app writes."""
    import hashlib
    d = tempfile.mkdtemp()
    auth, other = "1f" * 32, "2e" * 32
    assert sync.put(d, SLOT, 0, _blob())[0] == 200                 # an older app's: unbound
    assert sync.get(d, SLOT)["version"] == 1
    assert sync.put(d, SLOT, 1, _blob(), auth)[0] == 200           # the newer app binds it
    with open(os.path.join(sync.folder(d), SLOT + ".json")) as fh:
        kept = json.load(fh)
    assert kept["auth"] == hashlib.sha256(auth.encode()).hexdigest() and auth not in json.dumps(kept)
    assert sync.get(d, SLOT, auth)["version"] == 2
    for who in ("", other):
        try:
            sync.get(d, SLOT, who)
        except sync.Refused:
            pass
        else:
            raise AssertionError("read without the credential")
        assert sync.put(d, SLOT, 2, _blob(), who) == (403, {"error": "refused"})
        assert sync.put(d, SLOT, 0, _blob(), who) == (403, {"error": "refused"}), \
            "a stale version is not answered with the copy either"
        try:
            sync.delete(d, SLOT, who)
        except sync.Refused:
            pass
        else:
            raise AssertionError("deleted without the credential")
    assert sync.put(d, SLOT, 2, _blob(), auth)[0] == 200           # the binding stays
    with open(os.path.join(sync.folder(d), SLOT + ".json")) as fh:
        assert json.load(fh)["auth"] == kept["auth"]
    assert sync.delete(d, SLOT, auth) and sync.get(d, SLOT, auth) is None
    # A new slot written with a credential is bound from its first version.
    assert sync.put(d, SLOT, 0, _blob(), auth)[0] == 200
    assert sync.put(d, SLOT, 1, _blob())[0] == 403

    # Over HTTP: the header, a malformed one refused outright.
    srv, _key, _stats, _donate = _service()
    try:
        path = "/sync/" + "cd" * 32
        h = {"X-Sync-Auth": auth}
        assert _call(srv, "PUT", path, None, {"version_seen": 0, "blob": _blob()}, headers=h)[0] == 200
        assert _call(srv, "GET", path, headers=h)[0] == 200
        assert _call(srv, "GET", path) == (403, {"error": "refused"})
        assert _call(srv, "GET", path, headers={"X-Sync-Auth": other})[0] == 403
        assert _call(srv, "GET", path, headers={"X-Sync-Auth": "nope"})[0] == 400
        assert _call(srv, "PUT", path, None, {"version_seen": 1, "blob": _blob()})[0] == 403
        assert _call(srv, "DELETE", path) == (403, {"error": "refused"})
        assert _call(srv, "DELETE", path, headers=h) == (200, {"deleted": True})
    finally:
        srv.shutdown()
        srv.server_close()


def test_an_address_is_slowed_down():
    srv, key, _stats, donate = _service()
    try:
        ip = "203.0.113.9"
        codes = [_call(srv, "GET", "/sync/" + SLOT, ip=ip)[0]
                 for _ in range(donate.SYNC_PER_WINDOW + 1)]
        assert codes[:-1] == [404] * donate.SYNC_PER_WINDOW and codes[-1] == 429
        assert _call(srv, "GET", "/sync/" + SLOT, ip="203.0.113.10")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_writes_and_new_slots_have_limits_of_their_own():
    srv, _key, _stats, donate = _service()
    try:
        ip, path = "203.0.113.20", "/sync/" + SLOT
        version, codes = 0, []
        for _ in range(donate.SYNC_WRITES_PER_WINDOW + 1):
            status, body = _call(srv, "PUT", path, None, {"version_seen": version, "blob": _blob()},
                                 ip=ip)
            codes.append(status)
            version = body.get("version", version)
        assert codes[:-1] == [200] * donate.SYNC_WRITES_PER_WINDOW and codes[-1] == 429
        assert _call(srv, "GET", path, ip=ip)[0] == 200                   # reading goes on
        assert _call(srv, "DELETE", path, ip=ip)[0] == 429                # a delete is a write
        # New slots: a household has one; a few more a day, then no more.
        donate.SYNC_NEW_PER_DAY = 3
        ip = "203.0.113.21"
        codes = [_call(srv, "PUT", "/sync/" + f"{n:02x}" * 32, None,
                       {"version_seen": 0, "blob": _blob()}, ip=ip)[0] for n in range(4)]
        assert codes == [200, 200, 200, 429]
        status, _b = _call(srv, "PUT", "/sync/" + "00" * 32, None,      # one of its own: fine
                           {"version_seen": 1, "blob": _blob()}, ip=ip)
        assert status == 200
        assert _call(srv, "PUT", "/sync/" + "aa" * 32, None,
                     {"version_seen": 0, "blob": _blob()}, ip="203.0.113.22")[0] == 200
    finally:
        srv.shutdown()
        srv.server_close()


def test_full_answers_507_and_the_owner_is_told_without_slots_or_addresses():
    srv, _key, stats, donate = _service()
    told = []
    back = _limit({(donate, "push_ready"): lambda: True,
                   (donate, "push"): lambda *a, **k: told.append(a) or (1, "")})
    try:
        ip = "198.51.100.33"
        assert _call(srv, "PUT", "/sync/" + SLOT, None, {"version_seen": 0, "blob": _blob(1000)},
                     ip=ip)[0] == 200
        assert not told
        size = sync.usage(stats)["bytes"]
        sync.MAX_TOTAL = int(size / 0.85)                    # past 80 %
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            assert _call(srv, "PUT", "/sync/" + SLOT, None,
                         {"version_seen": 1, "blob": _blob(1000)}, ip=ip)[0] == 200
            for _ in range(50):
                if told:
                    break
                time.sleep(0.02)
            assert len(told) == 1 and "filling up" in told[0][0]
            status, body = _call(srv, "PUT", "/sync/" + "cd" * 32, None,
                                 {"version_seen": 0, "blob": _blob(2000)}, ip=ip)
            assert (status, body) == (507, {"error": "full"})
            assert _call(srv, "PUT", "/sync/" + SLOT, None,
                         {"version_seen": 2, "blob": _blob(1000)}, ip=ip)[0] == 200
            time.sleep(0.1)
            assert len(told) == 1                            # once a day, not per write
            donate._SYNC_WARNED[0] = 0.0
            assert donate.sync_watch(stats)["warn"]
            time.sleep(0.1)
        log = err.getvalue() + " ".join(" ".join(map(str, a)) for a in told)
        assert "for sync in use" in log
        for secret in (SLOT, "cd" * 32, ip):
            assert secret not in log
        # The admin page gets the numbers (no slot in them).
        assert set(sync.usage(stats)) == {"bytes", "slots", "most", "share", "warn", "full"}
    finally:
        back()
        sync.MAX_TOTAL = 5 * 1024 ** 3
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
