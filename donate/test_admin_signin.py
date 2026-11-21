#!/usr/bin/env python3
"""The admin page's sign-in and the service around it, against what a
security review found (donate.py, admin_auth.py):

- the key the session cookies are signed with is never short or empty: an
  empty key file used to be read as the key, and a cookie signed with an
  empty key is anyone's to make;
- an admin removed and invited again under the same name starts afresh -
  the cookie from before no longer fits (both counted from 1);
- signing out ends the session on the server: a copy of the cookie made
  before, and every renewal of it, names nobody afterwards; a cookie from
  before sessions had names still signs its browser in, as before;
- with no admin at all, a header naming one counts for nothing unless
  ADMIN_USERS names them too;
- a new password is hashed with OWASP's scrypt setting, an older hash still
  fits and is made again at the next sign-in;
- what cannot be read is answered (400, a word in JSON), a fault of the
  service's own is answered 500 and said in the log by kind and place - no
  traceback, none of a request's own words, no address;
- everything kept beside the statistics is the service's alone to read.

Plain Python: ``python3 donate/test_admin_signin.py`` runs them all (pytest
finds them too). Everything in a temporary directory.
"""

from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import stat
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

IP = "203.0.113.77"
PASSWORD = "a long and good password 42"


def _donate(extra: str = ""):
    root = tempfile.mkdtemp()
    stats = os.path.join(root, "stats")
    os.makedirs(stats)
    conf = os.path.join(root, "donate.conf")
    with open(conf, "w") as fh:
        fh.write(f"STATS_DIR={stats}\nLISTEN=127.0.0.1:0\n{extra}")
    os.environ["DONATE_CONFIG"] = conf
    os.environ["STATS_DIR"] = stats
    os.environ.pop("ADMIN_USERS", None)
    for name in ("donate", "admin_auth"):
        sys.modules.pop(name, None)
    import donate
    return donate, stats


def _admin(donate, user: str = "pontus"):
    """An admin made as the page makes one: an invite taken up with a password."""
    auth = donate._auth()
    token, _until = auth.invite("server", user, user.title())
    auth.invite_password(token, PASSWORD)
    return auth


def _serve(donate):
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", 0), donate.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _call(srv, method: str, path: str, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=20)
    h = {"X-Real-IP": IP}
    if isinstance(body, (dict, list)):
        body = json.dumps(body).encode()
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    try:
        return r.status, json.loads(raw) if raw else {}, r
    except ValueError:
        return r.status, raw, r


def test_the_session_key_is_never_short_or_empty():
    donate, stats = _donate()
    path = os.path.join(stats, "admin-session.key")
    open(path, "w").close()                      # left empty: a full disk the moment it was made
    key = donate._session_key()
    assert len(key) >= 64 and key == donate._session_key()
    with open(path) as fh:
        assert fh.read().strip().encode() == key
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    with open(path, "w") as fh:
        fh.write("short\n")
    assert len(donate._session_key()) >= 64
    os.unlink(path)                              # none at all: made, the same for whoever asks next
    assert len(donate._session_key()) >= 64 and donate._session_key() == donate._session_key()
    assert not [n for n in os.listdir(stats) if n.endswith(".tmp")]
    import admin_auth
    try:
        admin_auth.configure(stats, "https://dopeiptv.org", b"")
    except ValueError:
        pass
    else:
        raise AssertionError("an empty key was taken")


def test_an_admin_removed_and_invited_again_starts_afresh():
    donate, _stats = _donate()
    auth = _admin(donate)
    cookie = auth.session_cookie("pontus")
    assert auth.session_user(cookie) == "pontus"
    data = auth._load()
    del data["users"]["pontus"]
    auth._save(data)
    assert auth.session_user(cookie) == ""
    _admin(donate)                               # the same name, invited again
    assert auth.session_user(cookie) == ""       # was "pontus": both counted their sessions from 1
    fresh = auth.session_cookie("pontus")
    assert auth.session_user(fresh) == "pontus"
    # Signed out everywhere: nothing from before fits, and nothing is left to remember.
    auth.sign_out(fresh)
    auth.sign_out_everywhere("pontus")
    assert auth.session_user(fresh) == "" and "out" not in auth._load()["users"]["pontus"]


def test_signing_out_ends_the_session_on_the_server():
    donate, _stats = _donate()
    auth = _admin(donate)
    first = auth.session_cookie("pontus")
    copy = first                                 # what someone took from the browser
    renewed = auth.session_cookie("pontus", first)          # the browser's next visit: the same session
    assert renewed.split(".")[-2] == first.split(".")[-2]
    other = auth.session_cookie("pontus")                    # another device: a session of its own
    assert other.split(".")[-2] != first.split(".")[-2]
    assert auth.sign_out(renewed) is True
    for value in (renewed, copy, auth.session_cookie("pontus", copy) and copy):
        assert auth.session_user(value) == ""
    assert auth.session_user(other) == "pontus"              # the other device stays signed in
    # A cookie that names nobody ends nothing and writes nothing.
    assert auth.sign_out("pontus.1.2.3") is False and auth.sign_out("") is False
    # Renewing with a cookie that no longer holds starts a new session - it
    # never brings the ended one back.
    again = auth.session_cookie("pontus", copy)
    assert again.split(".")[-2] != copy.split(".")[-2]


def test_a_cookie_from_before_sessions_had_names_still_signs_in():
    donate, _stats = _donate()
    auth = _admin(donate)
    rec = auth._load()["users"]["pontus"]
    until = int(time.time()) + 1000
    old = f"pontus.{until}.{rec['epoch']}.{auth._sign('pontus', until, rec['epoch'])}"
    assert auth.session_user(old) == "pontus"                # the owner stays signed in
    named = auth.session_cookie("pontus", old)               # its next visit gives it a name
    assert len(named.split(".")) == 5 and auth.session_user(named) == "pontus"
    assert auth.sign_out(old) is True and auth.session_user(old) == ""
    assert auth.session_user(named) == "pontus"
    # Made up, tampered with or of another format: nobody.
    for bad in ("", "pontus", old[:-1] + ("0" if old[-1] != "0" else "1"), old + ".x", "pontus.².1.x",
                named.replace(".", ".9", 1), "x" * 5000):
        assert auth.session_user(bad) == "", bad
    # A name with a dot in it gets a session like any other.
    _admin(donate, "p.l")
    assert auth.session_user(auth.session_cookie("p.l")) == "p.l"


def test_with_no_admin_a_header_counts_for_nothing_unless_named():
    donate, _stats = _donate()
    assert not donate._auth().has_users()
    assert donate._is_admin("anyone") is False
    srv = _serve(donate)
    try:
        forged = {"X-Admin-User": "anyone", "Origin": donate.ORIGIN}
        assert _call(srv, "GET", "/admin/account", headers=forged)[0] == 401
        assert _call(srv, "POST", "/admin/account/invite", {"user": "mallory"}, forged)[0] == 401
    finally:
        srv.shutdown()
        srv.server_close()
    donate, _stats = _donate("ADMIN_USERS=owner\n")          # nginx's old prompt still in front: named
    assert donate._is_admin("owner") is True and donate._is_admin("anyone") is False


def test_a_password_is_hashed_as_asked_now_and_an_older_hash_is_made_again():
    donate, _stats = _donate()
    auth = _admin(donate)
    assert auth._load()["users"]["pontus"]["password"].startswith("scrypt$15$8$3$")
    data = auth._load()
    data["users"]["pontus"]["password"] = auth.hash_password(PASSWORD, (15, 8, 1))      # as before
    auth._save(data)
    try:
        auth.login_password("pontus", "not the password", IP)
    except auth.AuthError:
        pass
    else:
        raise AssertionError("a wrong password signed in")
    assert auth._load()["users"]["pontus"]["password"].startswith("scrypt$15$8$1$")
    assert auth.login_password("Pontus", PASSWORD, IP) == {"user": "pontus"}
    assert auth._load()["users"]["pontus"]["password"].startswith("scrypt$15$8$3$")
    assert auth.login_password("pontus", PASSWORD, IP) == {"user": "pontus"}
    assert auth.check_password(PASSWORD, "scrypt$40$8$1$AAAA$AAAA") is False            # nothing of ours


def test_failed_tries_are_forgotten_on_time_and_never_fill_the_memory():
    donate, _stats = _donate()
    auth = _admin(donate)
    auth._failed("a:" + IP, "u:" + "x" * 5000)
    assert max(len(k) for k in auth._fails) <= 100
    auth.forget(time.time() + auth.FAIL_WINDOW + 1)
    assert not auth._fails
    for _i in range(auth.MAX_CHALLENGES + 50):
        auth._challenge("login")
    assert len(auth._challenges) <= auth.MAX_CHALLENGES
    # What a passkey sends is refused when it cannot be read, never an error.
    for raw in (b"\xbf", b"\x81" * 3000 + b"\x00", b"\xa1\x80\x00"):
        try:
            auth._read_passkey(lambda raw=raw: auth.cbor(raw))
        except auth.AuthError:
            pass


def test_what_cannot_be_read_is_answered_and_a_fault_is_said_without_its_words():
    donate, _stats = _donate()
    auth = _admin(donate)
    srv = _serve(donate)
    said = io.StringIO()
    try:
        with contextlib.redirect_stderr(said):
            site = {"Origin": donate.ORIGIN}
            status, body, _r = _call(srv, "POST", "/pay/session", b'{"amount": 1e999, "currency": "eur"}', site)
            assert (status, body) == (400, {"error": "amount"})
            # A page cached from before November 2026 asks under the old name.
            status, body, _r = _call(srv, "POST", "/donate/session", b'{"amount": 1e999, "currency": "eur"}', site)
            assert (status, body) == (400, {"error": "amount"})
            deep = ("[" * 3000 + "]" * 3000).encode()
            assert _call(srv, "POST", "/feedback/report", deep, {"X-Dopeiptv-Report": "1"})[0] == 400
            assert _call(srv, "POST", "/admin/auth/password", deep, site)[0] == 400
            status, body, _r = _call(srv, "OPTIONS", "/pay/config")
            assert (status, body) == (501, {"error": "method"})
            # A fault of the service's own: answered, said once by kind and place.
            donate.Handler._feedback_replies = lambda self: 1 // 0
            for _ in range(3):
                status, body, _r = _call(srv, "POST", "/feedback/replies", {"t": "x" * 20, "secret": "PRIVATE-WORDS"}, site)
                assert (status, body) == (500, {"error": "internal"})
            # Refused sign-ins: why, never from where or what was typed.
            origin = "https://evil.example/<script>" + "A" * 3000
            assert _call(srv, "POST", "/admin/auth/" + "B" * 500, {}, {"Origin": origin})[0] == 403
            assert _call(srv, "POST", "/admin/auth/password", {"user": "typed-name", "password": "typed-pw"}, site)[0] == 400
        log = said.getvalue()
        assert log.count("ZeroDivisionError") == 1 and "a request could not be answered" in log
        assert "Traceback" not in log and "PRIVATE-WORDS" not in log
        assert "no admin by that name" in log
        for never in (IP, "typed-name", "typed-pw", "A" * 100, "B" * 30):
            assert never not in log, never
        assert max(len(line) for line in log.splitlines()) < 300
        # Signed in over the wire: the cookie renewed keeps its session, and
        # signing out ends it for every copy.
        status, _body, r = _call(srv, "POST", "/admin/auth/password", {"user": "pontus", "password": PASSWORD}, site)
        assert status == 200
        cookie = [v.split(";")[0] for k, v in r.getheaders() if k == "Set-Cookie" and v.startswith("iptv_admin=")][0]
        flags = [v for k, v in r.getheaders() if k == "Set-Cookie" and v.startswith("iptv_admin=")][0]
        for flag in ("Secure", "HttpOnly", "SameSite=Lax", "Path=/admin/"):
            assert flag in flags, flag
        assert _call(srv, "GET", "/admin/account", headers={"Cookie": cookie})[0] == 200
        status, _page, r = _call(srv, "GET", "/admin/", headers={"Cookie": cookie})
        renewed = [v.split(";")[0] for k, v in r.getheaders() if k == "Set-Cookie" and v.startswith("iptv_admin=")][0]
        assert status == 200 and renewed.split(".")[-2] == cookie.split(".")[-2]
        # A cookie that names nobody, put before the browser's own, does not hide it.
        assert _call(srv, "GET", "/admin/account", headers={"Cookie": f"iptv_admin=x.1.1.y; {cookie}"})[0] == 200
        assert _call(srv, "POST", "/admin/auth/signout", {}, {**site, "Cookie": renewed})[0] == 200
        for value in (cookie, renewed):
            assert _call(srv, "GET", "/admin/account", headers={"Cookie": value})[0] == 401
        assert auth.session_user(cookie.split("=", 1)[1]) == ""
    finally:
        srv.shutdown()
        srv.server_close()


def test_what_is_kept_beside_the_statistics_is_the_services_alone():
    donate, stats = _donate()
    os.makedirs(os.path.join(stats, "visits"))
    for name in ("feedback.jsonl", os.path.join("visits", "2026-11-19.json")):
        with open(os.path.join(stats, name), "w") as fh:
            fh.write("{}\n")
        os.chmod(os.path.join(stats, name), 0o644)
    os.chmod(stats, 0o750)
    os.chmod(os.path.join(stats, "visits"), 0o755)
    assert donate.keep_private(stats) == 4
    assert donate.keep_private(stats) == 0

    def mode(*path: str) -> int:
        return stat.S_IMODE(os.stat(os.path.join(stats, *path)).st_mode)
    assert mode() == 0o700 and mode("visits") == 0o700
    assert mode("feedback.jsonl") == 0o600 and mode("visits", "2026-11-19.json") == 0o600


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for name, fn in tests:
        fn()
        print("ok", name)
    print(f"{len(tests)} passed")
