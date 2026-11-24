"""The one gate in front of the app's servers for the home network
(core/home_gate.py): each check on its own, the order ``front`` asks them
in, a wrong token counted the way all three servers count it - and that
the phone remote, the remote API and server mode ask the gate rather than
a copy of their own, so they cannot drift apart again."""
from __future__ import annotations

import http.client
import logging
from pathlib import Path

import pytest

from dopeiptv.core import home_gate, remote_api
from dopeiptv.core.home_gate import RateLimiter
from dopeiptv.core.log import log
from dopeiptv.core.remote_api import RemoteServer

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "gate-token-0123456789abcdefghij"
SERVERS = ("dopeiptv/core/phone_remote.py", "dopeiptv/core/remote_api.py",
           "dopeiptv/services/home_server.py")


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def warnings():
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                seen.append(record.getMessage())
    h = Catch()
    log.addHandler(h)
    yield seen
    log.removeHandler(h)


# -- each check ------------------------------------------------------------------------------

@pytest.mark.parametrize("host", [None, "", "192.168.1.4:3673", "[fd00::5]:3674", "localhost:80",
                                  "nas", "tv.local", "box.lan", "media.home.arpa", "pc.fritz.box"])
def test_a_host_of_the_home_is_let_through(host):
    assert home_gate.host_ok(host)


@pytest.mark.parametrize("host", ["rebind.example.com", "attacker.example:3673", "evil.com."])
def test_a_public_name_a_rebinding_brought_here_is_not(host):
    assert not home_gate.host_ok(host)


def test_a_token_opens_only_as_itself_and_an_empty_one_nothing():
    assert home_gate.token_ok(TOKEN, TOKEN)
    assert home_gate.token_ok(TOKEN, "", "other", TOKEN)             # either of two doors
    assert not home_gate.token_ok(TOKEN + "x", TOKEN)
    assert not home_gate.token_ok(TOKEN[:-1], TOKEN)
    assert not home_gate.token_ok("", "")                            # no token: nothing opens
    assert not home_gate.token_ok("", "", "")
    assert not home_gate.token_ok("anything")
    assert not home_gate.token_ok("\udcff", TOKEN)                   # an odd byte, no exception


def test_the_token_a_request_brings_header_first():
    q = {"token": ["from-query"]}
    assert home_gate.given_token({"Authorization": "Bearer  b "}, q) == "b"
    assert home_gate.given_token({"authorization": "x"} | {"X-Dopeiptv-Token": " h "}, q) == "h"
    assert home_gate.given_token({}, q) == "from-query"
    assert home_gate.given_token({}, {}) == ""


def test_this_computer_however_it_is_written():
    for me in ("127.0.0.1", "127.8.9.1", "::1", "::ffff:127.0.0.1"):
        assert home_gate.this_computer(me), me
    for other in ("192.168.1.2", "::ffff:192.168.1.2", "fe80::1%eth0", "", "nas"):
        assert not home_gate.this_computer(other), other


def test_a_web_page_of_another_site_is_told_by_its_own_headers():
    assert home_gate.cross_site({"Sec-Fetch-Site": "cross-site"}, "192.168.1.4:3674")
    assert home_gate.cross_site({"Sec-Fetch-Site": "same-site"}, "192.168.1.4:3674")
    assert home_gate.cross_site({"Origin": "https://evil.example"}, "192.168.1.4:3674")
    assert not home_gate.cross_site({"Origin": "http://192.168.1.4:3674"}, "192.168.1.4:3674")
    assert not home_gate.cross_site({"Sec-Fetch-Site": "same-origin"}, "192.168.1.4:3674")
    assert not home_gate.cross_site({"Sec-Fetch-Site": "none"}, None)  # an address typed
    assert home_gate.cross_site({"Origin": "http://x"}, None)          # no Host: every Origin


# -- a wrong token ---------------------------------------------------------------------------

def test_a_wrong_token_is_counted_and_shuts_out_once(warnings):
    lim = RateLimiter(fails=3, clock=Clock())
    said = "test server: an address kept guessing"
    who = "192.168.1.30"
    assert not home_gate.wrong_token(lim, who, {}, here=False, said=said)
    assert not home_gate.wrong_token(lim, who, {}, here=False, said=said)
    assert home_gate.wrong_token(lim, who, {}, here=False, said=said)
    assert lim.blocked(who)
    assert warnings == [f"{said}; it is shut out for 10 minutes"]


def test_never_this_computers_nor_a_web_pages(warnings):
    lim = RateLimiter(fails=1, clock=Clock())
    who = "192.168.1.31"
    assert not home_gate.wrong_token(lim, who, {}, here=True, said="x")
    page = {"Host": "192.168.1.4:3673", "Origin": "https://evil.example"}
    assert not home_gate.wrong_token(lim, who, page, here=False, said="x")
    fetch = {"Host": "192.168.1.4:3673", "Sec-Fetch-Site": "cross-site"}
    assert not home_gate.wrong_token(lim, who, fetch, here=False, said="x")
    assert not lim.blocked(who) and warnings == []


# -- the front, in its order -------------------------------------------------------------------

def test_the_front_asks_who_then_the_host_then_the_budget():
    clock = Clock()
    lim = RateLimiter(rate=0.001, burst=2, clock=clock)
    home = "192.168.1.40"
    # The internet first, whatever Host it brings.
    assert home_gate.front("8.8.8.8", "", "rebind.example.com", lim) == home_gate.AWAY
    # A rebinding page's requests use none of the device's budget...
    for _ in range(10):
        assert home_gate.front(home, "", "rebind.example.com", lim) == home_gate.FOREIGN_HOST
    assert home_gate.front(home, "", "192.168.1.4", lim) is None
    assert home_gate.front(home, "", "192.168.1.4", lim) is None
    assert home_gate.front(home, "", "192.168.1.4", lim) == home_gate.TOO_OFTEN
    # ...and one shut out stays out, even within a bucket of its own.
    pictures = RateLimiter(rate=100.0, burst=100.0, clock=clock)
    for _ in range(10):
        lim.failed("192.168.1.41")
    assert home_gate.front("192.168.1.41", "", None, lim, pictures) == home_gate.TOO_OFTEN
    assert home_gate.front("192.168.1.42", "", None, lim, pictures) is None
    # The server's own judge of who is at home.
    assert home_gate.front(home, "", None, lim, pictures,
                           admits=lambda who, own: False) == home_gate.AWAY


def test_the_remote_api_turns_a_rebinding_away_before_it_uses_the_budget(monkeypatch):
    """Was: the budget first, then the Host - a web page rebound to this
    computer could use up a script's budget with requests it never got an
    answer to."""
    monkeypatch.setattr(remote_api.time, "sleep", lambda _s: None)
    srv = RemoteServer("127.0.0.1", 0, TOKEN, lambda action, params: {"ok": True})
    srv.limits = RateLimiter(rate=0.001, burst=3, clock=Clock())
    srv.start()

    def ask(host):
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=10)
        conn.putrequest("GET", "/", skip_host=True)
        conn.putheader("Host", host)
        conn.putheader("Authorization", f"Bearer {TOKEN}")
        conn.endheaders()
        r = conn.getresponse()
        r.read()
        conn.close()
        return r.status
    try:
        assert [ask("rebind.example.com") for _ in range(8)] == [403] * 8
        assert [ask(f"127.0.0.1:{srv.port}") for _ in range(4)] == [200, 200, 200, 429]
    finally:
        srv.stop()


# -- one gate, not three copies -------------------------------------------------------------

@pytest.mark.parametrize("path", SERVERS)
def test_each_server_asks_the_gate(path):
    src = (ROOT / path).read_text(encoding="utf-8")
    assert "home_gate" in src
    # Their own copies of the checks are gone: no second way to judge a
    # token, a Host, this computer or a web page.
    for own in ("def cross_site", "def this_computer", "def _loopback", "def _given_token",
                "def _token(", "def _tuner_token", "class RateLimiter",
                "home_net.home_host(", "home_net.is_home_client("):
        assert own not in src, (path, own)


def test_only_a_signature_is_compared_outside_the_gate():
    for path in SERVERS:
        src = (ROOT / path).read_text(encoding="utf-8")
        uses = src.count("compare_digest(")
        # The phone remote's two signatures (a request's, a picture's) are
        # HMACs it makes itself; every token is the gate's to compare.
        assert uses == (2 if path.endswith("phone_remote.py") else 0), (path, uses)
        if uses:
            for line in [x for x in src.splitlines() if "compare_digest(" in x]:
                at = src.index(line)
                assert "signature(" in src[at:at + 200], line


def test_the_names_tests_and_modules_reached_them_by_still_work():
    from dopeiptv.core import phone_remote
    from dopeiptv.services import home_server
    assert phone_remote.RateLimiter is home_gate.RateLimiter
    assert phone_remote.cross_site is home_gate.cross_site
    assert phone_remote.this_computer is home_gate.this_computer
    assert phone_remote.SERVER_RATE == home_gate.SERVER_RATE
    assert phone_remote.SERVER_BURST == home_gate.SERVER_BURST
    assert home_server.RateLimiter is home_gate.RateLimiter
    assert home_server._loopback is home_gate.this_computer
