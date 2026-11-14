#!/usr/bin/env python3
"""Tests for settings sync (sync.py, and its routes in donate.py).

Plain Python: ``python3 donate/test_sync.py`` runs them all (pytest finds
them too: ``python3 -m pytest donate/test_sync.py``). Everything in a
temporary directory; the service on 127.0.0.1 for the HTTP part.
"""

from __future__ import annotations

import base64
import http.client
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


# -- the routes in donate.py ----------------------------------------------------------

def _service():
    """donate.py's Handler on 127.0.0.1, with a signing key and statistics
    folder of its own."""
    import supporter
    root = tempfile.mkdtemp()
    seed_path = os.path.join(root, "supporter.key")
    seed = secrets.token_bytes(32)
    with open(seed_path, "w") as fh:
        fh.write(seed.hex() + "\n")
    conf = os.path.join(root, "donate.conf")
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    with open(conf, "w") as fh:
        fh.write(f"SUPPORTER_KEY_FILE={seed_path}\nSTATS_DIR={stats}\nLISTEN=127.0.0.1:0\n")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    sys.modules.pop("donate", None)
    import donate
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), donate.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    key = supporter.make(seed, 739000, secrets.token_bytes(6))
    return srv, key, stats, donate


def _call(srv, method, path, key=None, body=None, ip="198.51.100.7", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    h = {"X-Real-IP": ip}
    if key is not None:
        h[sync.HEADER] = key
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


def test_the_routes_check_the_key_and_keep_only_ciphertext():
    srv, key, stats, donate = _service()
    try:
        path = "/sync/" + SLOT
        assert _call(srv, "GET", path)[0] == 401                          # no key
        assert _call(srv, "GET", path, key="DS1-nonsense")[0] == 401
        import supporter
        other = supporter.make(secrets.token_bytes(32), 739000, b"\1" * 6)
        assert _call(srv, "GET", path, key=other)[0] == 401              # another site's key
        assert _call(srv, "GET", "/sync/xyz", key=key)[0] == 404
        assert _call(srv, "GET", path, key=key)[0] == 404
        blob = _blob(300)
        status, body = _call(srv, "PUT", path, key, {"version_seen": 0, "blob": blob})
        assert status == 200 and body["version"] == 1
        status, body = _call(srv, "GET", path, key=key)
        assert status == 200 and body["blob"] == blob and body["version"] == 1
        status, body = _call(srv, "PUT", path, key, {"version_seen": 0, "blob": _blob()})
        assert status == 409 and body["blob"] == blob
        assert _call(srv, "PUT", path, key, {"version_seen": 1})[0] == 400
        assert _call(srv, "PUT", path, key, None)[0] == 400
        status, _b = _call(srv, "PUT", path, key, None,
                           headers={"Content-Length": str(sync.MAX_BODY + 1)})
        assert status == 413
        assert _call(srv, "POST", path, key, {"version_seen": 1, "blob": _blob()})[0] == 404
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


def test_an_address_is_slowed_down():
    srv, key, _stats, donate = _service()
    try:
        ip = "203.0.113.9"
        codes = [_call(srv, "GET", "/sync/" + SLOT, key=key, ip=ip)[0]
                 for _ in range(donate.SYNC_PER_WINDOW + 1)]
        assert codes[:-1] == [404] * donate.SYNC_PER_WINDOW and codes[-1] == 429
        assert _call(srv, "GET", "/sync/" + SLOT, key=key, ip="203.0.113.10")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
