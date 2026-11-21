"""Where the app's own requests may land (core/dest_guard.py): a name never
reaches this computer or a link-local host unless it says so - checked on
the connection, so DNS rebinding is caught - and a redirect never goes from
the internet into the home network, or to this computer or a link-local
host, whoever follows it (requests itself, or the cast's resolver by hand)."""
from __future__ import annotations

import http.server
import socket
import threading

import pytest

from dopeiptv.core import dest_guard as g
from dopeiptv.core._lazy_requests import requests


@pytest.mark.parametrize("origin,target,why", [
    ("http://cdn.example.com/a", "http://127.0.0.1:8080/x", g.THIS_COMPUTER),
    ("http://cdn.example.com/a", "http://localhost/x", g.THIS_COMPUTER),
    ("http://cdn.example.com/a", "http://[::ffff:127.0.0.1]/x", g.THIS_COMPUTER),
    ("http://cdn.example.com/a", "http://2130706433/x", g.THIS_COMPUTER),
    ("http://cdn.example.com/a", "http://169.254.169.254/latest", g.LINK_LOCAL),
    ("http://cdn.example.com/a", "http://metadata.google.internal/", g.LINK_LOCAL),
    ("http://cdn.example.com/a", "http://192.168.1.1/", g.HOME),
    ("http://cdn.example.com/a", "http://10.0.0.1/", g.HOME),
    ("http://cdn.example.com/a", "http://router.local/", g.HOME),
    ("http://cdn.example.com/a", "http://fritz.box/", g.HOME),
    ("http://cdn.example.com/a", "file:///etc/passwd", "file://"),
    ("http://cdn.example.com/a", "gopher://x/", "gopher://"),
    ("http://cdn.example.com/a", "https://cdn2.example.net/b", ""),
    ("http://cdn.example.com/a", "http://cdn.example.com/b", ""),
    ("http://192.168.1.5/a", "http://192.168.1.6/b", ""),
    ("http://192.168.1.5/a", "https://cdn.example.com/b", ""),
    ("http://192.168.1.5/a", "http://127.0.0.1/b", g.THIS_COMPUTER),
    ("http://127.0.0.1:6878/ace/x", "http://127.0.0.1:6878/ace/y", ""),
])
def test_a_redirect_is_judged_by_where_it_goes(origin, target, why):
    assert g.redirect_refusal(origin, target, resolve=False) == why


def test_a_redirect_to_a_name_is_judged_by_what_it_resolves_to(monkeypatch):
    answers = {"rebind.example.com": "127.0.0.1", "inside.example.com": "192.168.0.10",
               "meta.example.com": "169.254.169.254", "fine.example.com": "93.184.216.34"}

    def fake(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answers[host], port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake)
    o = "http://cdn.example.com/a"
    assert g.redirect_refusal(o, "http://rebind.example.com/") == g.THIS_COMPUTER
    assert g.redirect_refusal(o, "http://inside.example.com/") == g.HOME
    assert g.redirect_refusal(o, "http://meta.example.com/") == g.LINK_LOCAL
    assert g.redirect_refusal(o, "http://fine.example.com/") == ""
    # The same host again, answered at home by its own DNS: a server at home
    # by its public name.
    assert g.redirect_refusal("http://inside.example.com/a", "https://inside.example.com/b") == ""


@pytest.mark.parametrize("host,peer,why", [
    ("evil.example.com", "127.0.0.1", g.THIS_COMPUTER),
    ("evil.example.com", "::1", g.THIS_COMPUTER),
    ("evil.example.com", "::ffff:127.0.0.1", g.THIS_COMPUTER),
    ("evil.example.com", "169.254.169.254", g.LINK_LOCAL),
    ("evil.example.com", "169.254.10.10", g.LINK_LOCAL),
    ("localhost", "127.0.0.1", ""),
    ("127.0.0.1", "127.0.0.1", ""),
    ("vuplus.local", "169.254.3.4", ""),
    ("vuplus.local", "169.254.169.254", g.LINK_LOCAL),
    ("evil.example.com", "192.168.1.1", ""),
    ("evil.example.com", "93.184.216.34", ""),
])
def test_a_connection_is_judged_by_where_it_landed(host, peer, why):
    assert g.peer_refusal(host, peer) == why


class _Seen(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self):
        _Seen.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


@pytest.fixture()
def local_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Seen)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _Seen.hits = []
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def test_dns_rebinding_to_this_computer_is_refused_at_the_connection(local_server, monkeypatch):
    real = socket.getaddrinfo

    def rebind(host, port, *a, **kw):
        if host == "rebind.example.com":
            return real("127.0.0.1", port, *a, **kw)
        return real(host, port, *a, **kw)
    monkeypatch.setattr(socket, "getaddrinfo", rebind)
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(requests.ConnectionError):
        requests.get(f"http://rebind.example.com:{local_server}/secret", timeout=5)
    assert _Seen.hits == [], "not a byte reached the service on this computer"
    # The same service by its own name answers as ever.
    assert requests.get(f"http://127.0.0.1:{local_server}/fine", timeout=5).text == "ok"
    assert _Seen.hits == ["/fine"]


def _redirect(url: str, location: str):
    import requests as real
    r = real.Response()
    r.status_code = 302
    r.url = url
    r.headers["Location"] = location
    return r


def test_requests_never_follows_a_redirect_from_the_internet_inward():
    s = requests.Session()
    with pytest.raises(requests.RedirectRefused):
        s.get_redirect_target(_redirect("http://cdn.example.com/a", "http://127.0.0.1:631/"))
    with pytest.raises(requests.RedirectRefused):
        s.get_redirect_target(_redirect("http://cdn.example.com/a", "http://192.168.1.1/admin"))
    assert s.get_redirect_target(_redirect("http://cdn.example.com/a", "/b")) == "/b"
    assert issubclass(requests.RedirectRefused, requests.RequestException)


def test_the_casts_own_redirects_are_judged_hop_by_hop(monkeypatch):
    """The cast resolves the provider's redirects by hand: one into this
    computer is not followed, and the receiver gets what was asked for."""
    import requests as real

    from dopeiptv.providers import chromecast

    asked: list[str] = []

    class Hop:
        def __init__(self, loc):
            self.headers = {"Location": loc} if loc else {"Content-Type": "video/mp2t"}

        def iter_content(self, n):
            return iter([b""])

        def close(self):
            pass

    hops = {"http://iptv.example.com/live/1": "http://127.0.0.1:8080/admin"}

    def fake_get(url, **kw):
        asked.append(url)
        return Hop(hops.get(url))
    monkeypatch.setattr(real, "get", fake_get)
    url, served = chromecast._resolve_redirects("http://iptv.example.com/live/1")
    assert url == "http://iptv.example.com/live/1" and served is None
    assert asked == ["http://iptv.example.com/live/1"], "the refused hop is never asked"
    hops["http://iptv.example.com/live/1"] = "http://cdn.example.net/x.ts"
    monkeypatch.setattr(g, "_resolved", lambda host, port: ["93.184.216.34"])
    asked.clear()
    url, served = chromecast._resolve_redirects("http://iptv.example.com/live/1")
    assert url == "http://cdn.example.net/x.ts" and served == "video/mp2t"


# -- a public name an internet source named never leads home ---------------------

def test_a_picture_host_of_an_internet_source_never_reaches_the_home_network():
    """The review's first point: a provider's picture name that its DNS
    answers with a router's or a NAS's address. Noted as the source's
    (note_outside), the connection is refused there; anywhere public it
    goes on, and a name nobody noted is as before."""
    g.forget_names()
    try:
        g.note_outside("https://img.provider.example/logo/1.png")
        assert g.peer_refusal("img.provider.example", "192.168.1.1") == g.HOME
        assert g.peer_refusal("img.provider.example", "10.0.0.2") == g.HOME
        assert g.peer_refusal("IMG.provider.example.", "fd00::5") == g.HOME
        assert g.peer_refusal("img.provider.example", "93.184.216.34") == ""
        assert g.peer_refusal("cdn.elsewhere.example", "192.168.1.1") == ""
    finally:
        g.forget_names()


def test_the_persons_own_names_and_plex_stay_reachable_at_home():
    """A server at home by its public name (split DNS), a source's backup
    address, and Plex's own names answering a home address."""
    g.forget_names()
    try:
        for host in ("media.myhome.example", "1-2-3-4.abc123.plex.direct"):
            g.note_outside(f"https://{host}:32400/photo/x.jpg")
        g.note_own("https://media.myhome.example:8920")
        assert g.peer_refusal("media.myhome.example", "192.168.1.5") == ""
        assert g.peer_refusal("1-2-3-4.abc123.plex.direct", "192.168.1.5") == ""
        # Home names and addresses as written are never noted.
        g.note_outside("http://192.168.1.9/picon.png")
        g.note_outside("http://vuplus.local/picon.png")
        assert g.peer_refusal("192.168.1.9", "192.168.1.9") == ""
        assert g.peer_refusal("vuplus.local", "192.168.1.9") == ""
    finally:
        g.forget_names()


def test_the_noted_names_are_bounded():
    g.forget_names()
    try:
        for i in range(g.OUTSIDE_MAX + 50):
            g.note_outside(f"https://h{i}.example/p.png")
        assert len(g._OUTSIDE) == g.OUTSIDE_MAX
        assert g.peer_refusal("h0.example", "192.168.1.1") == ""         # the oldest let go
        assert g.peer_refusal(f"h{g.OUTSIDE_MAX + 49}.example", "192.168.1.1") == g.HOME
    finally:
        g.forget_names()


def test_an_internet_sources_lists_note_their_pictures_and_its_server_is_its_own():
    """make_client: a source on the internet's rows note their pictures;
    the source's own server is the person's."""
    from dopeiptv.providers import client as c
    g.forget_names()
    try:
        rows = [{"stream_icon": "https://img.provider.example/a.png"},
                {"stream_icon": "https://iptv.provider.example/b.png"}]
        c._note_own_names({"kind": "xtream", "server": "https://iptv.provider.example"})
        c._pictures_kept(rows, home=True)
        assert g.peer_refusal("img.provider.example", "192.168.1.1") == g.HOME
        assert g.peer_refusal("iptv.provider.example", "192.168.1.1") == ""
        # A source the person let into the home network (home=False): nothing noted.
        g.forget_names()
        c._pictures_kept([{"stream_icon": "https://img2.provider.example/a.png"}], home=False)
        assert g.peer_refusal("img2.provider.example", "192.168.1.1") == ""
    finally:
        g.forget_names()
