#!/usr/bin/env python3
"""Tests for refusing modified builds (official.py, and the routes in
donate.py), the build a report is kept with (feedback.py), and the admin
page's Copy (feedback.py plain(), admin/feedback.js).

Plain Python: ``python3 donate/test_official.py`` runs them all (pytest finds
them too: ``python3 -m pytest donate/test_official.py``). Everything in a
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
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import feedback  # noqa: E402
import official  # noqa: E402
import together  # noqa: E402

ROOM, SLOT, MEMBER = "cd" * 32, "ab" * 32, "a1" * 8
IP = "198.51.100.77"
REFUSAL = {"error": "unofficial_build",
           "message": "This is not an official version of dopeIPTV. This service is only in the official app, "
                      "from dopeiptv.org."}


class FakeClock:
    def __init__(self, t: float = 1_800_000_000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def test_what_the_header_says():
    assert official.REFUSAL == REFUSAL
    assert official.kind("official") == "official"
    assert official.kind(" Modified ") == "modified"
    assert official.kind("SOURCE") == "source"
    for other in (None, "", "forked", "official,modified"):
        assert official.kind(other) == "unknown"
    assert official.refused("modified") and official.refused("MODIFIED")
    for fine in (None, "", "official", "source", "nonsense"):
        assert not official.refused(fine)


def test_the_counts_per_day_and_service_and_kept_across_a_restart():
    d = tempfile.mkdtemp()
    clock = FakeClock()
    counts = official.Refused(clock=clock)
    assert counts.add(d, "sync") is True                 # the first today: a line in the log
    assert counts.add(d, "sync") is False
    counts.add(d, "together")
    clock.t += 86400 * 3
    assert counts.add(d, "feedback") is True             # a new day
    assert counts.totals(d, 1) == {"total": 1, "by": {"together": 0, "sync": 0, "feedback": 1}, "all": 4}
    assert counts.totals(d, 7) == {"total": 4, "by": {"together": 1, "sync": 2, "feedback": 1}, "all": 4}
    assert counts.totals(d, 0)["total"] == 4
    assert counts.take() == {"sync": 2, "together": 1, "feedback": 1}
    assert counts.take() == {}
    try:
        counts.add(d, "donate")
    except ValueError:
        pass
    else:
        raise AssertionError("an unknown service counted")
    counts.flush()
    again = official.Refused(clock=clock)              # the service started again
    assert again.totals(d, 7) == counts.totals(d, 7)
    with open(os.path.join(d, official.FILE), encoding="utf-8") as fh:
        kept = json.load(fh)
    assert set(kept) == {"days"} and all(set(row) <= set(official.SERVICES) for row in kept["days"].values())


def test_written_out_at_most_every_few_seconds_and_old_days_go():
    d = tempfile.mkdtemp()
    clock = FakeClock()
    counts = official.Refused(clock=clock)
    path = os.path.join(d, official.FILE)

    def on_disk() -> int:
        with open(path, encoding="utf-8") as fh:
            return sum(sum(row.values()) for row in json.load(fh)["days"].values())

    counts.add(d, "together")
    assert on_disk() == 1
    for _ in range(50):                                  # a build asking in a loop: no write each time
        counts.add(d, "together")
    assert on_disk() == 1 and counts.totals(d, 1)["total"] == 51
    clock.t += official.SAVE_EVERY
    counts.add(d, "together")
    assert on_disk() == 52
    clock.t += (official.KEEP_DAYS + 5) * 86400
    counts.add(d, "sync")
    counts.flush(d)
    with open(path, encoding="utf-8") as fh:
        assert len(json.load(fh)["days"]) == 1
    assert official.Refused(clock=clock).totals(d, 0)["all"] == 1
    # A file that is not what it should be is no reason not to start counting.
    bad = tempfile.mkdtemp()
    with open(os.path.join(bad, official.FILE), "w") as fh:
        fh.write("[1, 2")
    assert official.Refused().totals(bad, 0)["all"] == 0


# -- the routes in donate.py ----------------------------------------------------------

def _service():
    root = tempfile.mkdtemp()
    conf = os.path.join(root, "donate.conf")
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    with open(conf, "w") as fh:
        # ADMIN_USERS: the one nginx's old password prompt names, for the
        # tests that open the admin page before any admin exists (_admin_get).
        fh.write(f"STATS_DIR={stats}\nLISTEN=127.0.0.1:0\nADMIN_USERS=tester\n")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    sys.modules.pop("donate", None)
    import donate
    from http.server import ThreadingHTTPServer
    together.RELAY = together.Relay()
    official.REFUSED = official.Refused()
    feedback._seen.clear()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), donate.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, stats, donate


def _call(srv, method, path, build=None, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    h = {"X-Real-IP": IP, together.MEMBER_HEADER: MEMBER}
    if build is not None:
        h[official.HEADER] = build
    if isinstance(body, dict):
        body = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    try:
        return r.status, json.loads(raw or b"{}")
    except ValueError:
        return r.status, {}


def _report(text: str) -> dict:
    return {"kind": "error", "version": "1.5.0", "os": "Linux",
            "text": "Kind: error\nVersion: 1.5.0\n\nError:\nTraceback (most recent call last):\n"
                    f"  File \"x.py\", line 1\nValueError: {text}\n\nRecent log:\n10:00:00 INFO started"}


ROUTES = (
    ("together", "POST", "/together/" + ROOM, lambda: secrets.token_bytes(200)),
    ("together", "GET", f"/together/{ROOM}?after=0&wait=0", lambda: None),
    ("together", "DELETE", "/together/" + ROOM, lambda: None),
    ("sync", "GET", "/sync/" + SLOT, lambda: None),
    ("sync", "PUT", "/sync/" + SLOT,
     lambda: {"version_seen": 0, "blob": base64.b64encode(secrets.token_bytes(64)).decode()}),
    ("sync", "DELETE", "/sync/" + SLOT, lambda: None),
    ("feedback", "POST", "/feedback/report", lambda: _report(secrets.token_hex(4))),
)


def test_a_modified_build_is_refused_on_every_route_and_counted_without_anything_else():
    srv, stats, donate = _service()
    try:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            for service, method, path, body in ROUTES:
                for word in ("modified", "Modified"):
                    status, got = _call(srv, method, path, word, body(), headers={"X-Dopeiptv-Report": "1"})
                    assert (status, got) == (403, REFUSAL), (service, method, path, word, status, got)
        refused = official.REFUSED.totals(stats, 1)
        assert refused == {"total": 14, "by": {"together": 6, "sync": 6, "feedback": 2}, "all": 14}
        # Refused before anything was done: no room, no copy, no report.
        assert together.RELAY.counts()["rooms"] == 0
        assert not os.path.exists(os.path.join(stats, "sync"))
        assert not os.path.exists(os.path.join(stats, "feedback.jsonl"))
        # The log says it once today, and nothing that points anywhere.
        assert err.getvalue().count("a modified build refused") == 1
        official.REFUSED.flush(stats)
        with open(os.path.join(stats, official.FILE), encoding="utf-8") as fh:
            kept = fh.read()
        for secret in (IP, ROOM, SLOT, MEMBER):
            assert secret not in kept and secret not in err.getvalue()
    finally:
        srv.shutdown()
        srv.server_close()


def test_official_source_and_no_header_are_served():
    srv, stats, _donate = _service()
    try:
        for word in (None, "official", "source", "something-new"):
            assert _call(srv, "POST", "/together/" + ROOM, word, secrets.token_bytes(200))[0] == 200
            assert _call(srv, "GET", f"/together/{ROOM}?after=0&wait=0", word)[0] == 200
            assert _call(srv, "DELETE", "/together/" + ROOM, word)[0] == 200
            status, got = _call(srv, "GET", "/sync/" + SLOT, word)
            version = got.get("version", 0) if status == 200 else 0
            assert status in (200, 404)
            blob = base64.b64encode(secrets.token_bytes(64)).decode()
            assert _call(srv, "PUT", "/sync/" + SLOT, word, {"version_seen": version, "blob": blob})[0] == 200
            assert _call(srv, "GET", "/sync/" + SLOT, word)[0] == 200
            assert _call(srv, "DELETE", "/sync/" + SLOT, word)[0] == 200
            status, got = _call(srv, "POST", "/feedback/report", word, _report(str(word)),
                                headers={"X-Dopeiptv-Report": "1"})
            assert status == 200 and got.get("ok"), (word, status, got)
        assert official.REFUSED.totals(stats, 0)["all"] == 0
        # The other answers are what they were: a request not from the app
        # is still told so, whatever its build.
        assert _call(srv, "POST", "/feedback/report", "official", _report("x"))[0] == 400
    finally:
        srv.shutdown()
        srv.server_close()


def _admin_get(srv, path):
    """As a signed-in admin: with no admins set up yet, donate.py lets in
    whom nginx's old password named (X-Admin-User) - if ADMIN_USERS names
    them too (_service does)."""
    return _call(srv, "GET", path, headers={"X-Admin-User": "tester"})


def test_the_admin_page_shows_the_refusals():
    srv, stats, _donate = _service()
    stub = types.SimpleNamespace(collect_if_stale=lambda: None)     # no nginx logs here
    old = sys.modules.get("stats_collect")
    sys.modules["stats_collect"] = stub
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            _call(srv, "GET", "/sync/" + SLOT, "modified")
            _call(srv, "POST", "/feedback/report", "modified", _report("x"))
            status, data = _admin_get(srv, "/admin/data?days=30")
        assert status == 200
        assert data["refused"] == {"total": 2, "by": {"together": 0, "sync": 1, "feedback": 1}, "all": 2}
        with open(os.path.join(HERE, "admin", "index.html"), encoding="utf-8") as fh:
            page = fh.read()
        assert "Refused (modified builds)" in page and 'id="refused-by"' in page
        with open(os.path.join(HERE, "admin", "admin.js"), encoding="utf-8") as fh:
            js = fh.read()
        assert "function refusedBuilds()" in js and "data.refused" in js and "refusedBuilds();" in js
    finally:
        if old is None:
            sys.modules.pop("stats_collect", None)
        else:
            sys.modules["stats_collect"] = old
        srv.shutdown()
        srv.server_close()


# -- the build a report is kept with ----------------------------------------------------

def test_a_report_keeps_the_build_it_came_with():
    srv, stats, _donate = _service()
    try:
        sent = {}
        for word, kept in (("official", "official"), ("source", "source"), (None, "unknown"),
                           ("forked", "unknown")):
            status, got = _call(srv, "POST", "/feedback/report", word,
                                dict(_report(f"build {word}"), build="official"),   # the body's word is not read
                                headers={"X-Dopeiptv-Report": "1"})
            assert status == 200
            sent[got["id"]] = kept
        with open(os.path.join(stats, "feedback.jsonl"), encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        assert {r["id"]: r["build"] for r in rows} == sent
        status, summary = _admin_get(srv, "/admin/feedback?days=30")
        assert status == 200
        assert {i["id"]: i["build"] for i in summary["items"]} == sent
        assert dict(summary["by_build"]) == {"official": 1, "source": 1, "unknown": 2}
        builds = {k: sum(g["builds"][k] for g in summary["groups"]) for k in feedback.BUILDS}
        assert builds == {"official": 1, "source": 1, "unknown": 2}
        src = next(i for i, k in sent.items() if k == "source")
        status, one = _admin_get(srv, "/admin/feedback-item?id=" + src)
        assert status == 200 and one["build"] == "source" and "Build: source" in one["copy"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_report_from_before_the_header_is_unknown():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "feedback.jsonl"), "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "0123456789ab", "t": int(time.time()), "kind": "feedback",
                             "version": "1.4.0", "os": "Windows", "sig": "", "text": "Old words"}) + "\n")
    got = feedback.summary(d, 30)
    assert got["items"][0]["build"] == "unknown" and got["by_build"] == [("unknown", 1)]
    assert "Build: unknown" in feedback.item(d, "0123456789ab")["copy"]


def test_the_feedback_list_marks_source_and_filters_by_build():
    with open(os.path.join(HERE, "admin", "feedback.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert 'it.build === "source"' in js and "sourceChip(" in js           # the badge, on feedback
    assert "(g.builds || {}).source" in js                                 # and on a problem
    assert "i.build === build" in js and "(g.builds || {})[build]" in js   # the filter, on both lists
    assert '["source", "From source"]' in js
    with open(os.path.join(HERE, "admin", "admin.css"), encoding="utf-8") as fh:
        assert ".chip.b-source" in fh.read()


# -- Copy ----------------------------------------------------------------------------

def test_the_copied_text_has_every_field():
    row = {"id": "a1b2c3d4e5f6", "t": 1_800_000_000, "kind": "error", "version": "1.5.0", "os": "Linux",
           "build": "source", "sig": "ValueError: bad N",
           "text": "Kind: error\nPython: 3.12\n\nWhat happened:\nIt stopped when I zapped.\n\n"
                   "Error:\nTraceback (most recent call last):\n  File \"main.py\", line 9, in zap\n"
                   "ValueError: bad 3\n\nRecent log:\n21:04:11 INFO player: started\n21:04:12 ERROR boom"}
    text = feedback.plain(row)
    for want in ("dopeIPTV error report a1b2c3d4e5f6", "Sent: 2027-01-15 08:00 UTC", "Kind: error",
                 "Version: 1.5.0", "System: Linux", "Build: source", "From: the app",
                 "Problem: ValueError: bad N",
                 "What happened:\nIt stopped when I zapped.",                       # the message
                 "Traceback (most recent call last):\n  File \"main.py\", line 9, in zap\nValueError: bad 3",
                 "Recent log:\n21:04:11 INFO player: started\n21:04:12 ERROR boom"):
        assert want in text, want
    assert text.endswith("ERROR boom\n")
    fb = {"id": "f0f0f0f0f0f0", "t": 1_800_000_000, "kind": "feedback", "version": "?", "os": "?",
          "topic": "bug", "area": "playback", "often": "always", "from": "site", "text": "The guide is empty."}
    text = feedback.plain(fb)
    for want in ("dopeIPTV feedback report f0f0f0f0f0f0", "Build: unknown", "From: the website",
                 "Topic: not working", "Area: playback", "How often: every time", "\n\nThe guide is empty.\n"):
        assert want in text, want
    assert "Problem:" not in text and "Supporter:" not in text


def test_copy_all_shown_gives_the_reports_asked_for_in_their_order():
    d = tempfile.mkdtemp()
    ids = []
    for n in range(3):
        status, got = feedback.accept(d, _report(f"number {n}"), "official")
        ids.append(got["id"])
    got = feedback.copies(d, [ids[2], "not-an-id", ids[0], ids[2], "0" * 12])
    assert [g["id"] for g in got] == [ids[2], ids[0]]
    assert "ValueError: number 2" in got[0]["copy"] and "Build: official" in got[0]["copy"]
    assert feedback.copies(d, []) == [] and feedback.copies(d, ["../x"]) == []
    srv, stats, _donate = _service()
    try:
        status, one = _call(srv, "POST", "/feedback/report", "source", _report("over http"),
                            headers={"X-Dopeiptv-Report": "1"})
        status, got = _admin_get(srv, "/admin/feedback-copy?ids=" + one["id"] + ",zz")
        assert status == 200 and [g["id"] for g in got["items"]] == [one["id"]]
        assert "ValueError: over http" in got["items"][0]["copy"]
        assert _call(srv, "GET", "/admin/feedback-copy?ids=" + one["id"])[0] == 401   # not signed in
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_page_has_copy_buttons_that_work_on_phones():
    with open(os.path.join(HERE, "admin", "feedback.js"), encoding="utf-8") as fh:
        js = fh.read()
    assert 'copyButton("Copy", "fb-copy", groupCopy(g))' in js           # each problem
    assert 'copyButton("Copy", "fb-copy", itemCopy(it))' in js           # each feedback item
    assert '"Copy all shown"' in js and "/admin/feedback-copy?ids=" in js
    assert "navigator.clipboard.writeText" in js and 'document.execCommand("copy")' in js   # the fallback
    assert "setSelectionRange(0, text.length)" in js and "ClipboardItem" in js
    assert "Copied \\u2713" in js
    with open(os.path.join(HERE, "admin", "admin.css"), encoding="utf-8") as fh:
        css = fh.read()
    for rule in (".fb-copy {", ".fb-copy.done", ".copy-buffer {"):
        assert rule in css, rule
    # Colours from the page's tokens only, so both themes have them.
    block = css[css.index("/* Copying a report"):]
    assert "#" not in block.split("*/", 1)[1]


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")


def test_an_admin_is_who_the_signed_session_says_never_a_header_alone():
    # The security review's way in: anything that reaches the service's
    # socket could send X-Admin-User naming an admin - and make itself one
    # with an invite. Once there are admins, only the signed session counts.
    srv, _stats, donate = _service()
    import admin_auth
    admin_auth._CFG["path"] = ""        # this test's own admins, in its own folder
    try:
        auth = donate._auth()
        token, _until = auth.invite("owner", "alice", "Alice")
        auth.invite_password(token, "a long and good password 42")
        assert auth.has_users()
        forged = {"X-Admin-User": "alice", "Origin": donate.ORIGIN}
        assert _call(srv, "GET", "/admin/account", headers=forged)[0] == 401
        assert _call(srv, "POST", "/admin/account/invite", body={"user": "mallory"}, headers=forged)[0] == 401
        cookie = {"Cookie": f"{donate.SESSION_COOKIE}={donate.session_cookie('alice')}"}
        status, body = _call(srv, "GET", "/admin/account", headers=cookie)
        assert status == 200 and body.get("user", "alice") == "alice"
        assert _call(srv, "GET", "/admin/account", headers={"Cookie": f"{donate.SESSION_COOKIE}=alice.1.x"})[0] == 401
    finally:
        admin_auth._CFG["path"] = ""    # the next test's service sets up its own
        srv.shutdown()
        srv.server_close()
