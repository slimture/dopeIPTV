"""Offline mode's state, its rules and the guard every request takes
(core/offline.py): off, on by hand or by itself; the system's word settled
(a flapping network toggles nothing); the login's hint; nothing to the
internet in any spelling - an IPv4 address inside an IPv6 one read as
itself -, a public name never even looked up; this computer and the home
network still reached. And what offline mode shows: a source's lists as
its downloads (providers/downloaded.py), a series' known episodes kept
bounded and plain (core/downloads.py), Mine's rule (core/mitt.py)."""
from __future__ import annotations

import errno
import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import dest_guard, offline


@pytest.fixture(autouse=True)
def _clean():
    offline.reset()
    dest_guard.forget_names()
    yield
    offline.reset()
    dest_guard.forget_names()


@pytest.fixture
def no_lookup(monkeypatch):
    """Every name lookup recorded - and none answered."""
    asked: list = []

    def look(host, *a, **k):
        asked.append(host)
        raise socket.gaierror(socket.EAI_AGAIN, "not here")
    monkeypatch.setattr(socket, "getaddrinfo", look)
    return asked


# -- the state ------------------------------------------------------------------------

def test_off_by_default_and_the_mode_says_why_and_since():
    assert not offline.on() and offline.mode() == offline.OFF and offline.since() == 0
    assert offline.set_mode(offline.HAND, offline.WHY_HAND, now=100.0)
    assert offline.on() and offline.why() == offline.WHY_HAND and offline.since() == 100.0
    assert not offline.set_mode(offline.HAND, offline.WHY_HAND)
    assert offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK, now=200.0)
    assert offline.since() == 100.0             # still offline since it began
    assert offline.set_mode(offline.OFF)
    assert not offline.on() and offline.why() == "" and offline.since() == 0
    with pytest.raises(ValueError):
        offline.set_mode("sometimes")


def test_the_environment_word():
    assert offline.env_word({}) is None
    assert offline.env_word({offline.ENV: "watch"}) is None
    assert offline.env_word({offline.ENV: "0"}) == ""
    assert offline.env_word({offline.ENV: "off"}) == ""
    assert offline.env_word({offline.ENV: "Disconnected"}) == offline.DISCONNECTED
    assert offline.env_word({offline.ENV: "local"}) == offline.LOCAL
    assert offline.env_word({offline.ENV: "nonsense"}) is None


def test_what_the_systems_word_means():
    assert offline.offline_word(offline.DISCONNECTED) is True
    assert offline.offline_word(offline.LOCAL) is True
    assert offline.offline_word(offline.SITE) is True
    assert offline.offline_word(offline.ONLINE) is False
    assert offline.offline_word(offline.UNKNOWN) is None
    # A source at home still answers on the local network.
    assert offline.offline_word(offline.LOCAL, home_source=True) is None
    assert offline.offline_word(offline.DISCONNECTED, home_source=True) is True


# -- the system's word, settled ------------------------------------------------------

def _run(w: offline.Watch, now: float, mode: str = offline.OFF, why: str = ""):
    return w.tick(now, mode, why)


def test_offline_only_once_the_word_held_and_back_likewise():
    w = offline.Watch()
    w.said(offline.DISCONNECTED, 0.0)
    assert _run(w, offline.SETTLE_S - 0.1) is None
    assert _run(w, offline.SETTLE_S) == (offline.ITSELF, offline.WHY_NO_NETWORK)
    w.said(offline.ONLINE, 10.0)
    assert _run(w, 12.0, offline.ITSELF, offline.WHY_NO_NETWORK) is None
    assert _run(w, 10.0 + offline.SETTLE_S, offline.ITSELF, offline.WHY_NO_NETWORK) == (
        offline.OFF, "")


def test_a_flapping_network_toggles_nothing():
    w = offline.Watch()
    t = 0.0
    for _ in range(20):
        w.said(offline.DISCONNECTED, t)
        assert _run(w, t + 1.0) is None
        w.said(offline.ONLINE, t + 1.5)
        assert _run(w, t + 2.5) is None
        t += 3.0


def test_only_the_local_network_is_offline_for_a_source_on_the_internet():
    w = offline.Watch(home_source=False)
    w.said(offline.LOCAL, 0.0)
    assert _run(w, 10.0) == (offline.ITSELF, offline.WHY_LOCAL)
    home = offline.Watch(home_source=True)
    home.said(offline.SITE, 0.0)
    assert _run(home, 10.0) is None


def test_on_by_hand_stays_until_turned_off():
    w = offline.Watch()
    assert w.by_hand(True, 0.0) == (offline.HAND, offline.WHY_HAND)
    w.said(offline.ONLINE, 0.0)
    assert _run(w, 100.0, offline.HAND, offline.WHY_HAND) is None
    assert w.by_hand(False, 100.0) == (offline.OFF, "")


def test_turned_off_by_hand_while_offline_stays_off_until_the_word_changes():
    w = offline.Watch()
    w.said(offline.DISCONNECTED, 0.0)
    assert _run(w, 10.0) == (offline.ITSELF, offline.WHY_NO_NETWORK)
    assert w.by_hand(False, 10.0) == (offline.OFF, "")
    assert _run(w, 20.0) is None                # not by itself again at once
    w.said(offline.ONLINE, 30.0)
    assert _run(w, 40.0) is None
    w.said(offline.DISCONNECTED, 50.0)
    assert _run(w, 60.0) == (offline.ITSELF, offline.WHY_NO_NETWORK)


def test_the_logins_hint_holds_until_the_systems_word_changes():
    w = offline.Watch()
    w.said(offline.ONLINE, 0.0)
    assert w.unreached(offline.OFF) == (offline.ITSELF, offline.WHY_UNREACHED)
    # The system still says online: no back and forth with the login.
    assert _run(w, 100.0, offline.ITSELF, offline.WHY_UNREACHED) is None
    w.said(offline.DISCONNECTED, 110.0)
    w.said(offline.ONLINE, 120.0)
    assert _run(w, 130.0, offline.ITSELF, offline.WHY_UNREACHED) == (offline.OFF, "")
    # On by hand already: the hint changes nothing.
    assert w.unreached(offline.HAND) is None


def test_no_detection_at_all_keeps_offline_by_hand_alone():
    w = offline.Watch()                         # the word stays unknown
    assert _run(w, 1000.0) is None
    assert w.unreached(offline.OFF) == (offline.ITSELF, offline.WHY_UNREACHED)
    assert _run(w, 2000.0, offline.ITSELF, offline.WHY_UNREACHED) is None


# -- for want of a network ------------------------------------------------------------

def test_no_network_is_told_from_a_server_that_said_no():
    assert offline.no_network(socket.gaierror(socket.EAI_AGAIN, "Temporary failure"))
    assert offline.no_network(OSError(errno.ENETUNREACH, "Network is unreachable"))
    assert not offline.no_network(socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
    assert not offline.no_network(ConnectionRefusedError(errno.ECONNREFUSED, "refused"))
    assert not offline.no_network(TimeoutError("timed out"))
    assert not offline.no_network(RuntimeError("Invalid username or password"))
    assert not offline.no_network(None)
    # However deep requests and urllib3 wrapped it.
    inner = socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
    try:
        try:
            raise inner
        except socket.gaierror as e:
            raise ConnectionError("Max retries exceeded") from e
    except ConnectionError as outer:
        wrapped = outer
    assert offline.no_network(wrapped)
    loop = RuntimeError("loop")
    loop.__cause__ = loop
    assert not offline.no_network(loop)


# -- nothing to the internet ------------------------------------------------------------

PUBLIC = ["8.8.8.8", "1.1.1.1", "134744072", "0x08.0x08.0x08.0x08", "010.010.010.010",
          "8.8.2056", "::ffff:8.8.8.8", "[::ffff:8.8.8.8]", "::ffff:808:808",
          "2001:4860:4860::8888", "[2001:4860:4860::8888]", "::8.8.8.8"]
HOME = ["192.168.1.10", "10.0.0.2", "172.16.5.4", "::ffff:192.168.1.10", "[::ffff:c0a8:10a]",
        "fd00::1", "169.254.10.1", "nas", "box.local", "fritz.box", "tuner.lan",
        "server.home.arpa"]
HERE = ["127.0.0.1", "localhost", "::1", "[::1]", "::ffff:127.0.0.1", "0x7f.1"]


def test_off_nothing_is_refused():
    for host in PUBLIC:
        assert offline.host_refusal(host) == ""
    assert offline.refusal("https://example.com/x") == ""
    assert offline.peer_refusal("8.8.8.8") == ""


@pytest.mark.parametrize("host", PUBLIC)
def test_an_internet_address_is_refused_in_every_spelling(host, no_lookup):
    offline.set_mode(offline.HAND, offline.WHY_HAND)
    assert offline.host_refusal(host) == offline.INTERNET
    assert offline.peer_refusal(host.strip("[]")) == offline.INTERNET
    assert offline.refusal(f"http://{host if ':' not in host or host.startswith('[') else '[' + host + ']'}:80/x") \
        == offline.INTERNET
    assert no_lookup == []


@pytest.mark.parametrize("host", HOME + HERE)
def test_this_computer_and_the_home_network_are_reached(host, no_lookup):
    offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK)
    assert offline.host_refusal(host) == ""
    assert no_lookup == []


def test_a_public_name_is_refused_without_being_looked_up(no_lookup):
    offline.set_mode(offline.HAND, offline.WHY_HAND)
    for url in ("https://api.themoviedb.org/3/search", "https://dopeiptv.org/api/sync",
                "http://provider.example.net:8080/player_api.php", "https://www.youtube.com/"):
        assert offline.refusal(url) == offline.INTERNET
    assert no_lookup == []
    # What cannot be read fails closed.
    assert offline.refusal("http://[::1") == offline.INTERNET
    assert offline.refusal("") == offline.INTERNET
    assert offline.host_refusal("") == offline.INTERNET


def test_a_name_the_person_gave_is_judged_by_where_it_resolves(monkeypatch):
    answers = {"tv.example.org": ["192.168.1.20"], "far.example.org": ["192.168.1.20", "8.8.8.8"],
               "gone.example.org": []}

    def look(host, port, *a, **k):
        got = answers.get(host)
        if not got:
            raise socket.gaierror(socket.EAI_NONAME, "no")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a_, port)) for a_ in got]
    monkeypatch.setattr(socket, "getaddrinfo", look)
    for name in answers:
        dest_guard.note_own(f"http://{name}")
    offline.set_mode(offline.ITSELF, offline.WHY_LOCAL)
    assert offline.refusal("http://tv.example.org:8096/") == ""          # a server at home
    assert offline.refusal("http://far.example.org/") == offline.INTERNET
    assert offline.refusal("http://gone.example.org/") == offline.INTERNET


def test_a_stream_is_judged_by_its_host_and_a_file_is_this_computers():
    offline.set_mode(offline.HAND, offline.WHY_HAND)
    assert offline.stream_refusal("/home/me/Videos/film.mkv") == ""
    assert offline.stream_refusal("file:///home/me/film.mkv") == ""
    assert offline.stream_refusal("udp://@:1234") == ""
    assert offline.stream_refusal("udp://@239.1.1.1:1234") == ""
    assert offline.stream_refusal("rtsp://192.168.1.5/stream") == ""
    assert offline.stream_refusal("http://8.8.8.8/live/1.ts") == offline.INTERNET
    assert offline.stream_refusal("rtmp://cdn.example.com/live") == offline.INTERNET
    offline.set_mode(offline.OFF)
    assert offline.stream_refusal("http://8.8.8.8/live/1.ts") == ""


def test_a_refusal_is_said_once_per_host(caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="dopeiptv")
    for _ in range(5):
        offline.note_refused("api.example.com")
    said = [r for r in caplog.records if "api.example.com" in r.getMessage()]
    assert len(said) == 1 and said[0].levelno == logging.DEBUG


# -- the one adapter every request takes ------------------------------------------------

class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


@pytest.fixture
def local_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_the_adapter_refuses_the_internet_and_lets_this_computer_through(local_server, monkeypatch):
    from dopeiptv.core._lazy_requests import requests
    connected: list = []
    real = socket.create_connection

    def connect(addr, *a, **k):
        connected.append(addr)
        return real(addr, *a, **k)
    monkeypatch.setattr(socket, "create_connection", connect)
    offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK)
    with pytest.raises(requests.OfflineRefused):
        requests.get("http://8.8.8.8/x", timeout=2)
    with pytest.raises(requests.ConnectionError):        # what callers already handle
        requests.Session().get("https://api.themoviedb.org/3/x", timeout=2)
    assert connected == []
    assert requests.get(local_server + "/", timeout=5).text == "ok"
    # Through a proxy on the internet: not even to a server at home.
    s = requests.Session()
    with pytest.raises(requests.OfflineRefused):
        s.get("http://192.168.1.9/x", proxies={"http": "http://8.8.8.8:3128"}, timeout=2)
    offline.set_mode(offline.OFF)
    assert requests.get(local_server + "/", timeout=5).text == "ok"


def test_a_routes_adapter_takes_the_same_guard():
    from dopeiptv.core import net_adapter, net_route
    from dopeiptv.core._lazy_requests import requests
    route = net_route.Route(family="4")
    s = requests.Session()
    assert net_adapter.mount(s, route)
    offline.set_mode(offline.HAND, offline.WHY_HAND)
    with pytest.raises(requests.OfflineRefused):
        s.get("https://iptv.example.com/x", timeout=2)


def test_a_connection_that_landed_on_the_internet_goes_nowhere():
    from urllib3.exceptions import NewConnectionError

    class Sock:
        closed = False

        def getpeername(self):
            return ("8.8.8.8", 443)

        def close(self):
            self.closed = True

    class Conn:
        host = "nas.local"
        proxy = None
        _tunnel_host = None

    offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK)
    sock = Sock()
    with pytest.raises(NewConnectionError):
        dest_guard.check_peer(Conn(), sock)
    assert sock.closed
    # A proxy's connection too, off as well as on: judged by its peer.
    proxied = Conn()
    proxied.proxy = "http://8.8.8.8:3128"
    with pytest.raises(NewConnectionError):
        dest_guard.check_peer(proxied, Sock())
    offline.set_mode(offline.OFF)
    ok = Sock()
    assert dest_guard.check_peer(proxied, ok) is ok


# -- what else goes out -------------------------------------------------------------------

def test_a_webhook_on_the_internet_is_not_posted(monkeypatch):
    from dopeiptv.core import events
    opened: list = []
    monkeypatch.setattr(events, "opener_for", lambda url: opened.append(url))
    offline.set_mode(offline.HAND, offline.WHY_HAND)
    code, err = events.post("https://hooks.example.com/abc", {"event": "x"}, None)
    assert code == 0 and err.startswith("offline mode") and opened == []


def test_mqtt_to_a_broker_on_the_internet_is_refused(monkeypatch):
    from dopeiptv.core import mqtt
    tried: list = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: tried.append(a))
    offline.set_mode(offline.HAND, offline.WHY_HAND)
    client = mqtt.Client("broker.example.com", 1883, client_id="t")
    with pytest.raises(OSError):
        client.connect()
    assert tried == []


def test_the_bridge_opens_nothing_on_the_internet(monkeypatch):
    from dopeiptv.core import net_bridge
    tried: list = []
    monkeypatch.setattr(net_bridge.net_route, "open_connection",
                        lambda *a, **k: tried.append(a))
    bridge = net_bridge.Bridge(net_bridge.net_route.Route(), net_bridge.Policy(internet=True))
    offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK)
    with pytest.raises(net_bridge.Guarded):
        bridge._open("cdn.example.com", 443)
    with pytest.raises(net_bridge.Guarded):
        bridge._open("8.8.8.8", 80)
    assert tried == []


def test_a_recording_that_comes_due_offline_says_no_network():
    from dopeiptv.core import recording
    rm = recording.RecordingManager.__new__(recording.RecordingManager)
    job = {"id": "j", "title": "Nyheter", "url": "http://8.8.4.4/live/1.ts",
           "status": "scheduled"}
    offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK)
    rm._spawn(job)
    assert job["status"] == "failed" and job["error"] == recording.NO_NETWORK


def test_server_mode_opens_no_channel_on_the_internet():
    from dopeiptv.core import restream
    r = restream.Restreamer.__new__(restream.Restreamer)
    offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK)
    with pytest.raises(restream.Busy) as got:
        r.watch("a1", "7", "http://8.8.4.4/live/7.ts", "192.168.1.30")
    assert got.value.reason == restream.NO_DATA


def test_tmdb_is_asked_nothing_offline():
    from dopeiptv.providers.metadata import PosterResolver

    class Client:
        asked = 0

        def fetch_details(self, *a):
            Client.asked += 1
            return {}

    class S:
        def value(self, k, d=None):
            return d

        def setValue(self, k, v):
            pass
    r = PosterResolver.__new__(PosterResolver)
    r._cache, r._failed, r._waiting, r._pending = {}, set(), {}, set()
    r.client = Client()
    offline.set_mode(offline.HAND, offline.WHY_HAND)
    assert r.get_full("Hemma", "vod", lambda d: None) == {}
    assert r.is_resolved("Hemma", "vod")
    assert Client.asked == 0 and r._pending == set()


def test_the_image_loader_does_not_fetch_and_marks_nothing_dead(tmp_path, monkeypatch):
    from dopeiptv.core import workers
    asked: list = []
    monkeypatch.setattr(workers, "_image_get", lambda url: asked.append(url))
    loader = workers.LogoLoader.__new__(workers.LogoLoader)
    loader.disk_dir = tmp_path
    loader.dead, loader.dead_hosts, loader._host_strikes = {}, {}, {}
    offline.set_mode(offline.ITSELF, offline.WHY_NO_NETWORK)
    assert loader.fetch_file("https://image.tmdb.org/t/p/w342/x.jpg") is None
    assert asked == [] and loader.dead == {}


# -- what offline mode shows ---------------------------------------------------------------

def _download(folder, kind, sid, title, **info):
    sub = os.path.join(folder, "Movies" if kind == "movie" else "Series")
    os.makedirs(sub, exist_ok=True)
    path = os.path.join(sub, f"{title}.mp4")
    with open(path, "wb") as fh:
        fh.write(b"\x00\x00\x00\x20ftypisom" + b"x" * 512)
    side = {"format": 1, "kind": kind, "source": info.pop("source", "a1"), "stream_id": sid,
            "key": sid, "title": title, "added": info.pop("added", 1), **info}
    with open(path + ".dl.json", "w", encoding="utf-8") as fh:
        json.dump(side, fh)
    return path


class _Settings(dict):
    def value(self, k, d=None):
        return self.get(k, d)

    def setValue(self, k, v):
        self[k] = v


def _downloads(tmp_path):
    from dopeiptv.core import downloads as dl
    s = _Settings({dl.DIR_KEY: str(tmp_path)})
    d = dl.Downloads(s)
    d.scan()
    return d


def test_a_sources_lists_are_its_downloads(tmp_path):
    from dopeiptv.providers.downloaded import CATEGORY, DownloadedClient
    _download(str(tmp_path), "movie", "11", "Hemma", category_id="3", category_name="Familj",
              year="2021")
    _download(str(tmp_path), "movie", "12", "Borta", category_id="18")      # no name kept
    _download(str(tmp_path), "movie", "13", "Annan", source="b2", category_id="3",
              category_name="Familj")
    _download(str(tmp_path), "episode", "501", "Show S01E01", series_id="77",
              series_title="Show", season=1, episode=1, episode_title="Ett",
              series_category_id="7", series_category_name="Drama")
    d = _downloads(tmp_path)
    known = {"77": [{"id": "501", "season": 1, "episode": 1, "title": "Ett"},
                    {"id": "502", "season": 1, "episode": 2, "title": "Två"}]}
    c = DownloadedClient(d, "a1", "Downloaded", known=lambda sid: known.get(sid, []))
    assert c.live_categories() == [] and c.live_streams() == [] and c.xmltv() == b""
    assert c.vod_categories() == [{"category_id": "3", "category_name": "Familj"},
                                  {"category_id": CATEGORY, "category_name": "Downloaded"}]
    assert [r["name"] for r in c.vod_streams()] == ["Borta", "Hemma"]      # never another source's
    assert [r["name"] for r in c.vod_streams("3")] == ["Hemma"]
    nameless = c.vod_streams(CATEGORY)
    assert [r["name"] for r in nameless] == ["Borta"]
    assert nameless[0]["category_id"] == "18"      # its own category kept: the lock still knows it
    assert c.vod_url("11").endswith("Hemma.mp4") and c.vod_url("13") == "" and c.vod_url("99") == ""
    assert c.series_categories() == [{"category_id": "7", "category_name": "Drama"}]
    assert [r["name"] for r in c.series_list()] == ["Show"]
    info = c.series_info("77")
    eps = info["episodes"]["1"]
    assert [(e["id"], bool(e.get("_dl_missing"))) for e in eps] == [("501", False), ("502", True)]
    assert c.episode_url("501").endswith(".mp4") and c.episode_url("502") == ""
    assert c.vod_info("11")["info"]["releasedate"] == "2021"
    assert DownloadedClient(d, "", "Downloaded").vod_streams() == []        # the demo: nothing


def test_a_series_known_episodes_are_kept_plain_and_bounded(tmp_path, monkeypatch):
    from dopeiptv.core import downloads as dl
    from dopeiptv.core import privacy
    rows = [{"id": 501, "season": "1", "episode_num": "2", "title": "Ett https://x.example/y",
             "info": {"movie_image": "http://login:pw@x/"}, "container_extension": "mkv",
             "direct_source": "http://u:p@host/"},
            {"id": True}, {"title": "no id"}, "junk", {"id": "x" * 500, "season": "a"}]
    assert dl.keep_episodes(str(tmp_path), "a1", "77", rows)
    got = dl.known_episodes(str(tmp_path), "a1", "77")
    assert got[0] == {"id": "501", "season": 1, "episode": 2, "title": "Ett"}
    assert got[1] == {"id": "x" * 200}
    raw = open(dl.episodes_file(str(tmp_path), "a1", "77"), encoding="utf-8").read()
    assert "pw" not in raw and "http" not in raw and "u:p" not in raw
    assert dl.known_episodes(str(tmp_path), "a1", "78") == []
    assert dl.known_episodes(str(tmp_path), "b2", "77") == []
    # Named by a hash: an id from outside never names a path.
    assert os.path.dirname(dl.episodes_file(str(tmp_path), "../..", "../x")) == str(tmp_path)
    # Too large, not JSON, nested, another series' file: nothing.
    path = dl.episodes_file(str(tmp_path), "a1", "79")
    for body in (b"x" * (dl.EPISODES_FILE_MAX + 1), b"{nope", b"[" * 100000,
                 json.dumps({"source": "a1", "series_id": "80", "episodes": [{"id": "1"}]}).encode()):
        with open(path, "wb") as fh:
            fh.write(body)
        assert dl.known_episodes(str(tmp_path), "a1", "79") == []
    # Private viewing keeps nothing on disk.
    monkeypatch.setattr(privacy, "private", lambda: True)
    assert not dl.keep_episodes(str(tmp_path), "a1", "90", rows)
    assert dl.known_episodes(str(tmp_path), "a1", "90") == []


def test_mine_keeps_only_what_plays_offline():
    from dopeiptv.core import mitt
    files = frozenset({("a1", "movie", "11"), ("a1", "episode", "501")})
    shows = frozenset({("a1", "77")})
    E = mitt.Entry
    assert mitt.offline_keeps(E(mitt.FILM, "11", "Hemma", {}, "a1"), files, shows)
    assert not mitt.offline_keeps(E(mitt.FILM, "11", "Hemma", {}, "b2"), files, shows)
    assert not mitt.offline_keeps(E(mitt.FILM, "12", "Borta", {}, "a1"), files, shows)
    assert mitt.offline_keeps(E(mitt.EPISODE, "501", "Ett", {"id": "501"}, "a1"), files, shows)
    assert mitt.offline_keeps(E(mitt.SERIES, "77", "Show", {}, "a1"), files, shows)
    assert not mitt.offline_keeps(E(mitt.SERIES, "78", "Annan", {}, "a1"), files, shows)
    assert mitt.offline_keeps(E(mitt.LOCAL, "/home/me/a.mkv", "a", {}), files, shows)
    assert not mitt.offline_keeps(E(mitt.LOCAL, "smb://nas/a.mkv", "a", {}), files, shows)
    for kind in (mitt.CHANNEL, mitt.RADIO, mitt.WEB):
        assert not mitt.offline_keeps(E(kind, "1", "x", {}, "a1"), files, shows)
