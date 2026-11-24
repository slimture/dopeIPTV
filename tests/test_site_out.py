"""Nothing that goes to dopeiptv.org carries a login of the person's
(core/site_out.py), checked in the one adapter every request goes out
through - whoever makes the request."""

from __future__ import annotations

import ast
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core import build_check, report, site_out, sync, together
from dopeiptv.core._lazy_requests import requests
from dopeiptv.core.stores import PlaylistStore

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "dopeiptv"

PASSWORD = "Hunter2Secret"
USER = "anna.svensson"
SHORT = "q7x"          # a short password: matched as a word of its own


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    site_out.forget_for_tests()
    monkeypatch.setattr(site_out, "HOSTS", {site_out.SERVICE_HOST, "127.0.0.1"})
    build_check.set_verdict(build_check.Verdict(build_check.OFFICIAL, "test"))
    yield
    site_out.forget_for_tests()
    build_check.set_verdict(None)


def _playlist(**more):
    p = {"id": "a1b2c3d4", "name": "Mine", "server": "http://provider.example:8080",
         "username": USER, "password": PASSWORD}
    p.update(more)
    return p


class _Site:
    """A stand-in for dopeiptv.org on this computer: what reached it."""

    def __init__(self):
        self.got: list[tuple[str, str, bytes]] = []
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                site.got.append((self.command, self.path, self.rfile.read(n) if n else b""))
                body = json.dumps({"version": 1, "now": 1, "ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_PUT = do_DELETE = _any

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def site():
    s = _Site()
    yield s
    s.close()


# -- what a login is ------------------------------------------------------------------

def test_every_login_a_source_holds_is_found():
    found = site_out.logins_of([_playlist(
        token="tok-123456", mac="00:1A:79:AB:CD:EF",
        epg_url="https://guide.example/xmltv.php?username=epguser&password=epgpass",
        options={"proxy": "http://puser:ppass@proxy.example:3128",
                 "backup_servers": "https://bu:bp@backup.example",
                 "headers": "Referer: https://ok.example\nAuthorization: Bearer abcdef123"})])
    for v in (USER, PASSWORD, "tok-123456", "00:1A:79:AB:CD:EF", "00:1a:79:ab:cd:ef",
              "epguser", "epgpass", "puser", "ppass", "bu", "bp", "Bearer abcdef123",
              "abcdef123", base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()):
        assert v in found, v
    assert "https://ok.example" not in found          # a header that opens nothing


def test_the_persons_other_logins_are_found_in_every_profile(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("profiles/kid/trakt_access_token", "trakt-token-xyz")
    s.setValue("os_password", "subs-password")
    s.setValue("kodi_devices", json.dumps([{"host": "k", "password": "kodi-pass"}]))
    s.setValue("nerd_proxy", "socks5://nu:np@proxy.example:1080")
    s.setValue("theme", "nord")
    got = site_out.account_logins(s)
    assert {"trakt-token-xyz", "subs-password", "kodi-pass", "nu", "np"} <= got
    assert "nord" not in got


# -- what is refused, what is not -----------------------------------------------------

@pytest.mark.parametrize("where", ["url", "header", "body", "json", "percent", "jsonesc", "basic"])
def test_a_login_readable_to_the_site_is_refused(where):
    site_out.learn([_playlist()])
    url, headers, body = "https://dopeiptv.org/feedback/report", {}, None
    if where == "url":
        url += "?q=" + PASSWORD
    elif where == "header":
        headers = {"X-Thing": PASSWORD}
    elif where == "body":
        body = f"text {PASSWORD} more".encode()
    elif where == "json":
        body = json.dumps({"text": f"log: login {USER} failed"}).encode()
    elif where == "percent":
        # quote() of a value with characters that need it
        site_out.learn([_playlist(password="p@ss word/1")])
        body = b"x=p%40ss%20word%2F1"
    elif where == "jsonesc":
        site_out.learn([_playlist(password="lösen\"ord")])
        body = json.dumps({"text": "lösen\"ord"}, ensure_ascii=True).encode()
    elif where == "basic":
        headers = {"X-Auth": "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()}
    assert site_out.refusal(url, headers, body), where


def test_nothing_is_refused_that_holds_no_login():
    site_out.learn([_playlist(password=SHORT)])
    assert site_out.refusal("https://dopeiptv.org/releases.json",
                            {"User-Agent": "dopeIPTV/1.6.12 (Linux; x86_64)"}) is None
    body = json.dumps({"kind": "error", "text": "the guide did not load", "os": "Linux"}).encode()
    assert site_out.refusal("https://dopeiptv.org/feedback/report", {}, body) is None
    # a short login inside another word, or a random id, is no login
    assert site_out.refusal("https://dopeiptv.org/sync/aq7xb0", {"X-Member": "ffq7x90"}) is None
    # nor a JSON document's keys
    assert site_out.refusal("https://dopeiptv.org/x", {}, json.dumps({SHORT: 1}).encode()) is None
    # and anything not for dopeiptv.org is not this check's
    assert site_out.refusal("https://provider.example/get.php?password=" + SHORT) is None


def test_a_short_login_standing_alone_is_refused():
    site_out.learn([_playlist(password=SHORT)])
    body = json.dumps({"text": f"password {SHORT} was wrong"}).encode()
    assert site_out.refusal("https://dopeiptv.org/feedback/report", {}, body)


def test_what_is_sealed_is_not_read_and_the_rest_still_is():
    site_out.learn([_playlist()])
    blob = base64.b64encode(b"random " + PASSWORD.encode() + b" bytes").decode()
    site_out.sealed(blob)
    body = json.dumps({"version_seen": 1, "blob": blob}).encode()
    assert site_out.refusal("https://dopeiptv.org/sync/slot", {}, body) is None
    body = json.dumps({"version_seen": 1, "blob": blob, "note": PASSWORD}).encode()
    assert site_out.refusal("https://dopeiptv.org/sync/slot", {}, body)


def test_a_source_added_since_is_known_before_anything_is_sent(tmp_path):
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    PlaylistStore(s)
    assert site_out.refusal("https://dopeiptv.org/x?t=" + PASSWORD) is None
    # written by another way than the store (a sync, a restore)
    s.setValue("playlists", json.dumps([_playlist()]))
    assert site_out.refusal("https://dopeiptv.org/x?t=" + PASSWORD)


def test_settings_that_cannot_be_read_send_nothing_to_the_site():
    """A login added since may be in settings that could not be read: the
    check fails closed rather than going on with what it learnt before
    (an outside review of 1.7.0). Nothing else is held back."""
    class Unreadable:
        def value(self, *_a, **_k):
            raise OSError("the settings file went away")

    keep = Unreadable()
    site_out.learn([_playlist()], settings=keep)
    assert site_out.refusal("https://dopeiptv.org/api/report", {}, b"{}") == site_out.UNCHECKED
    with pytest.raises(site_out.LoginRefused, match="could not be read"):
        site_out.check("https://dopeiptv.org/api/report", {}, b"{}")
    assert site_out.refusal("https://api.themoviedb.org/3/search/movie") is None


def test_the_codes_that_open_a_copy_or_a_room_never_go():
    site_out.never("ABCD-EFGH-JKLM")
    assert site_out.refusal("https://dopeiptv.org/x", {}, b"code ABCD-EFGH-JKLM")


# -- through the adapter every request takes ------------------------------------------

def test_the_adapter_sends_nothing_that_holds_a_login(site):
    site_out.learn([_playlist()])
    with pytest.raises(requests.LoginHeld):
        requests.post(site.url + "/feedback/report", data=f"x {PASSWORD}".encode(), timeout=5)
    with pytest.raises(requests.RequestException):
        requests.get(site.url + "/releases.json", params={"u": USER}, timeout=5)
    assert site.got == []
    r = requests.post(site.url + "/feedback/report", data=b"nothing of theirs", timeout=5)
    assert r.status_code == 200 and len(site.got) == 1


def test_a_report_with_a_login_the_scrub_missed_is_not_sent(site, monkeypatch):
    site_out.learn([_playlist()])
    # The scrub masks it (the log knows every source's login from the
    # start now); were it to miss one, the adapter still sends nothing.
    assert PASSWORD not in report.scrub(f"my password is {PASSWORD}", [])
    monkeypatch.setattr(report, "scrub", lambda text, personal=(): text)
    status = report.send_status("feedback", f"my password is {PASSWORD}", personal=[],
                                endpoint=site.url + "/feedback/report", timeout=5)
    assert status == 0 and site.got == []
    status = report.send_status("feedback", "the guide is late", personal=[],
                                endpoint=site.url + "/feedback/report", timeout=5)
    assert status == 200 and len(site.got) == 1


def test_a_synced_copy_goes_even_when_its_ciphertext_holds_a_short_word(site):
    site_out.learn([_playlist(password=SHORT)])
    remote = sync.Remote("ab" * 16, site.url + "/sync/", timeout=5)
    blob = b"\x00 " + SHORT.encode() + b" \x00" * 40      # by chance, as ciphertext may
    ok, _version = remote.put(0, blob)
    assert ok and site.got and site.got[-1][0] == "PUT"


def test_a_rooms_sealed_message_goes(site):
    site_out.learn([_playlist()])
    relay = together.Relay("cd" * 16, "ef" * 20, endpoint=site.url + "/together/")
    relay.post(b"sealed " + PASSWORD.encode())
    assert site.got and site.got[-1][0] == "POST"


# -- the rule, read from the code base -------------------------------------------------

def test_the_adapter_asks_before_every_request():
    src = (PKG / "core" / "_lazy_requests.py").read_text()
    send = src[src.index("def send(self, request"):]
    send = send[:send.index("super().send(")]
    assert "site_out.check(request.url, request.headers, request.body)" in send


def test_only_the_sealing_modules_mark_anything_sealed():
    callers = set()
    for path in PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sealed"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "site_out"):
                callers.add(path.relative_to(PKG).as_posix())
    assert callers == {"core/sync.py", "core/together.py"}


def test_what_talks_to_dopeiptv_org_uses_no_other_way_out():
    """The modules that address dopeiptv.org make their requests through
    core/_lazy_requests.py alone - never urllib, http.client or a socket of
    their own, which the adapter would not see."""
    names = ("core/updates.py", "core/report.py", "core/sync.py", "core/together.py",
             "core/selfupdate.py", "core/winupdate.py", "core/macupdate.py")
    banned = ("urllib.request", "http.client", "urlopen", "socket.create_connection",
              "import socket", "import urllib3", "from urllib3")
    for name in names:
        src = (PKG / name).read_text(encoding="utf-8")
        for word in banned:
            assert word not in src, f"{name}: {word}"
