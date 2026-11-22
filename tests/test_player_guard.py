"""The in-app player and every program the app starts on a stream go out
through the app's guard (core/net_bridge.py), and a certificate the player
must check is checked or the stream is not opened.

- The player (libmpv in the app): an http(s) stream of a source on the
  internet goes through the bridge, Nerd on or off, so what mpv fetches
  itself - an HLS playlist's segments, its keys, a redirect - never lands on
  this computer, a link-local host or the home network. Put back to none
  for the next stream that needs none; a source at home, this computer's own
  servers and a web page through yt-dlp as before.
- A cast's converter and its probe, the archive's pieces, AirPlay's
  converter and server mode's HLS copy read an alias: their command line
  names nothing of the address, its login, the proxy or a header that may
  hold a token - the bridge adds the source's headers where it reads the
  request.
- A core that will not check a certificate it must: CertificateUnchecked,
  the stream not opened, said on the video - never another player.

The real tests play an HLS stream whose list names a service on this
computer with the real mpv, an ICY radio station through the bridge, and
convert and copy one with the real ffmpeg, reading its /proc/<pid>/cmdline.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from dopeiptv.core import nerd, net_bridge
from dopeiptv.core.net_route import Route
from tests.test_net_advanced import S
from tests.test_stream_guard import Server, _ask, _get

_REPO_ROOT = Path(__file__).resolve().parent.parent
INTERNET_SRC = {"_kind": "m3u", "_server": "http://provider.example/list.m3u"}
HOME_SRC = {"_kind": "m3u", "_server": "http://192.168.1.20/list.m3u"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY",
              "no_proxy", "NO_PROXY"):
        monkeypatch.delenv(k, raising=False)
    kept = type(net_bridge._bridges)()
    monkeypatch.setattr(net_bridge, "_bridges", kept)
    nerd.install(None)
    yield kept
    nerd.install(None)
    nerd.set_source(None)
    nerd.set_cast_source(None)
    for b in list(kept.values()):
        b.close()


def _only_bridge(kept) -> net_bridge.Bridge:
    live = [b for b in kept.values() if not b.closed]
    assert len(live) == 1, live
    return live[0]


def _is_bridge(px: str) -> bool:
    u = urlsplit(px)
    return u.hostname == "127.0.0.1" and u.username == net_bridge.USER and len(u.password or "") >= 24


# -- the proxy a stream goes out through ------------------------------------------

def test_the_environments_proxy_as_ffmpeg_takes_it(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://px.example:3128")
    assert net_bridge.system_proxy("https://cdn.example/1.m3u8") == "http://px.example:3128"
    for skip in ("cdn.example", ".example", "*.example", "a.b, cdn.example", "*"):
        monkeypatch.setenv("no_proxy", skip)
        assert net_bridge.system_proxy("https://cdn.example/1.m3u8") == "", skip
    monkeypatch.setenv("no_proxy", "other.example,dn.example")
    assert net_bridge.system_proxy("https://cdn.example/1.m3u8") == "http://px.example:3128"
    # Never for this computer's own servers; never one FFmpeg would not take.
    assert net_bridge.system_proxy("http://127.0.0.1:5000/buffer/x") == ""
    monkeypatch.setenv("http_proxy", "socks5://px.example:1080")
    assert net_bridge.system_proxy("https://cdn.example/1.m3u8") == ""
    monkeypatch.delenv("http_proxy")
    monkeypatch.setenv("HTTPS_PROXY", "http://px.example:3128")          # FFmpeg reads only http_proxy
    assert net_bridge.system_proxy("https://cdn.example/1.m3u8") == ""
    # Handed one: an http:// one, else none at all (FFmpeg connects itself).
    assert net_bridge.effective_proxy("http://me:pw@px:3128", "http://a.example/") == "http://me:pw@px:3128"
    assert net_bridge.effective_proxy("socks5://px:1080", "http://a.example/") == ""
    assert net_bridge.effective_proxy("https://px:3128", "http://a.example/") == ""


def test_the_proxy_streams_take_is_nerds_while_it_takes_streams(monkeypatch):
    url = "http://provider.example/live/1.ts"
    assert net_bridge.stream_proxy(INTERNET_SRC, url) == ""
    n = nerd.Nerd(S(nerd_enabled="true", nerd_proxy="http://10.0.0.1:3128"))
    assert net_bridge.stream_proxy(INTERNET_SRC, url, n) == "http://10.0.0.1:3128"
    alone = nerd.Nerd(S(nerd_enabled="true", nerd_proxy="http://10.0.0.1:3128",
                        nerd_proxy_player="false"))
    monkeypatch.setenv("http_proxy", "http://system.example:8080")
    assert net_bridge.stream_proxy(INTERNET_SRC, url, alone) == "http://system.example:8080"
    socks = nerd.Nerd(S(nerd_enabled="true", nerd_proxy="socks5://10.0.0.1:1080"))
    assert net_bridge.stream_proxy(INTERNET_SRC, url, socks) == ""    # never the system's instead


# -- a source's own headers -------------------------------------------------------

def test_only_headers_that_open_nothing_may_be_a_programs_arguments():
    safe, held = net_bridge.split_headers({
        "Referer": "http://portal.example/", "Origin": "http://portal.example",
        "Authorization": "Bearer s3cret", "Cookie": "sid=1", "X-Token": "t0k",
        "referer": "http://portal.example/?token=abc"})
    assert safe == {"Referer": "http://portal.example/", "Origin": "http://portal.example"}
    assert held == ["Authorization", "Cookie", "X-Token", "referer"]
    lines = net_bridge.header_lines({"Host": "evil.example", "Connection": "keep-alive",
                                     "X-A": "1\r\nX-B: 2", "X-Ok": "yes", "Proxy-Authorization": "x"})
    assert lines == (("X-Ok", "yes"),)
    fields = net_bridge.with_headers(b"User-Agent: Lavf\r\nx-ok: no\r\nAccept: */*", lines)
    assert fields.split(b"\r\n") == [b"User-Agent: Lavf", b"Accept: */*", b"X-Ok: yes"]


def test_the_bridge_adds_the_sources_headers_where_it_reads_the_request():
    srv = Server({"/live/u/p/1.ts": (200, {}, b"TS"), "/x": (200, {}, b"plain")})
    seen: list[dict] = []
    orig = srv.httpd.RequestHandlerClass.do_GET

    def note(handler):
        seen.append({k.lower(): v for k, v in handler.headers.items()})
        orig(handler)
    srv.httpd.RequestHandlerClass.do_GET = note
    b = net_bridge.Bridge(Route(), headers=(("Authorization", "Bearer s3cret"), ("X-Token", "t0k")))
    try:
        assert _get(b, b.alias(f"http://127.0.0.1:{srv.port}/live/u/p/1.ts")).endswith(b"TS")
        assert seen[-1]["authorization"] == "Bearer s3cret" and seen[-1]["x-token"] == "t0k"
        got = _ask(b, f"GET http://127.0.0.1:{srv.port}/x HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                      "X-Token: from-the-client\r\n\r\n")
        assert got.endswith(b"plain") and seen[-1]["x-token"] == "t0k"
        # A login in the address wins over a header of the same name.
        assert _get(b, b.alias(f"http://u:pw@127.0.0.1:{srv.port}/live/u/p/1.ts")).endswith(b"TS")
        assert seen[-1]["authorization"].startswith("Basic ")
    finally:
        b.close()
        srv.close()


# -- what a program the app starts is handed --------------------------------------

def test_a_program_is_handed_an_alias_and_its_environment(_clean, caplog):
    way = net_bridge.hand_over(INTERNET_SRC, "/home/me/film.mkv")
    assert way is not None and way.address == "/home/me/film.mkv" and not way.bridged
    assert way.env is None
    with caplog.at_level(logging.INFO, logger="dopeiptv"):
        url = "https://provider.example/live/alice/s3cret/1.m3u8?token=t0k"
        way = net_bridge.hand_over(INTERNET_SRC, url, {"X-Token": "t0k", "Referer": "http://p/"})
        again = net_bridge.hand_over(INTERNET_SRC, url, {"X-Token": "t0k", "Referer": "http://p/"})
    assert way is not None and way.bridged and ".stream.invalid/~/~/~/~.m3u8" in way.address
    for word in ("alice", "s3cret", "t0k", "provider"):
        assert word not in way.address
    bridge = _only_bridge(_clean)
    assert way.env["http_proxy"] == bridge.address and way.env["no_proxy"] == ""
    assert bridge.headers == (("X-Token", "t0k"), ("Referer", "http://p/"))
    said = [r.getMessage() for r in caplog.records if "go to the stream's own server" in r.getMessage()]
    assert len(said) == 1 and "X-Token" in said[0] and "t0k" not in said[0]   # once, by name
    assert bridge.aliases == 2 and again is not None
    way.close()
    again.close()
    assert bridge.aliases == 0
    assert "t0k" not in repr(way) and "s3cret" not in repr(way)


def test_a_program_goes_out_the_way_it_went_before(_clean, monkeypatch):
    """A program's http_proxy is the bridge now: the bridge goes on through
    what it would have taken itself - Nerd's proxy where streams take it,
    else the environment's."""
    from dopeiptv.core.recording import RecordingManager
    url = "http://provider.example/live/1.m3u8"
    monkeypatch.setenv("http_proxy", "http://system.example:8080")
    assert RecordingManager._proxy_for(INTERNET_SRC, url) == "http://system.example:8080"
    way = net_bridge.hand_over(INTERNET_SRC, url)
    assert way is not None and way.bridge._up == net_bridge.Upstream("system.example", 8080, None)
    nerd.install(S(nerd_enabled="true", nerd_proxy="http://10.0.0.1:3128"))
    assert RecordingManager._proxy_for(INTERNET_SRC, url) == "http://10.0.0.1:3128"
    way = net_bridge.hand_over(INTERNET_SRC, url)
    assert way is not None and way.bridge._up.host == "10.0.0.1"


def test_another_network_address_goes_to_a_converter_in_a_private_list(tmp_path, monkeypatch):
    """An rtsp, rtmp or srt address may hold a token where carries_login
    cannot see it - in its path, under a word of its own: a cast's
    converter and ffprobe get a private list that names it (read as the
    concat list it is), never the address; a multicast group and a file as
    before; the list gone with the run."""
    from dopeiptv.core import player_exec
    from dopeiptv.providers import cast_bridge
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    os.chmod(tmp_path, 0o700)
    for url in ("rtsp://cam.example/live?x-opaque=s3cret",
                "rtmp://h.example/live/9f86d081884c7d659a2feaa0c55ad015",
                "srt://h.example:9000?passphrase=s3cret", "udp://@239.0.0.1:1234?pass=s3cret"):
        way = net_bridge.hand_over(INTERNET_SRC, url)
        assert way is not None and way.listed and not way.bridged and way.env is None, url
        assert url in Path(way.address).read_text(encoding="utf-8")
        assert stat.S_IMODE(os.stat(way.address).st_mode) == 0o600
        args = cast_bridge.ffmpeg_args("ffmpeg", url, True, read=way.address)
        assert not any(url in a or "s3cret" in a for a in args), args
        assert args[args.index("-f") + 1] == "concat" and args[args.index("-i") + 1] == way.address
        assert "-user_agent" not in args and "-reconnect" not in args
        probe = cast_bridge.probe_args("ffprobe", url, way.address)
        assert not any("s3cret" in a for a in probe) and "-user_agent" not in probe
        way.close()
        assert not os.path.exists(way.address)
    for url in ("udp://@239.0.0.1:1234", "/films/a.mkv"):
        way = net_bridge.hand_over(INTERNET_SRC, url)
        assert way is not None and way.address == url and not way.listed
    monkeypatch.setattr(player_exec, "private_file", lambda *_a, **_k: "")
    assert net_bridge.hand_over(INTERNET_SRC, "rtsp://cam.example/live") is None


def test_no_bridge_no_program(monkeypatch):
    monkeypatch.setattr(net_bridge, "guard_for", lambda *_a, **_k: None)
    assert net_bridge.hand_over(INTERNET_SRC, "http://provider.example/1.ts") is None
    from dopeiptv.i18n import LocalizedError
    from dopeiptv.providers import airplay_bridge as ab
    from dopeiptv.providers import cast_bridge as cb
    from dopeiptv.providers import cast_feed
    b = cb.CastBridge()
    b.exe = sys.executable
    with pytest.raises(LocalizedError) as e:
        b.start("http://provider.example/live/u/p/1.ts", ["h264"])
    assert e.value.key == "cast_err_no_bridge" and b._server is None
    assert cb.probe_tracks("http://provider.example/live/u/p/1.ts", exe="ffprobe")["audio"] == []
    a = ab.AirPlayBridge()
    a.exe = sys.executable
    with pytest.raises(LocalizedError) as e:
        a.start(ab.Plan(False, "http://provider.example/live/u/p/1.ts", live=True))
    assert e.value.key == "cast_err_no_bridge" and a._proc is None
    assert ab.probe("http://provider.example/1.ts", exe="ffmpeg")["video"] == ""
    feed = cast_feed.Feed(lambda m: ("http://provider.example/t/1.ts", 0.0, m), 1000.0)
    assert feed._read(None, "ffmpeg", "http://provider.example/t/1.ts", 0.0, 0.0, [],
                      threading.Event()) is None              # the stream ends, nothing read
    from dopeiptv.core import restream
    from dopeiptv.core.dvr_ingest import NotAStream
    with pytest.raises(NotAStream):
        restream.ffmpeg_opener("ffmpeg", INTERNET_SRC)("http://provider.example/x/index.m3u8")


def test_a_bridge_a_stream_reads_is_not_the_one_let_go(_clean, monkeypatch):
    monkeypatch.setattr(net_bridge, "MAX_BRIDGES", 2)
    a = net_bridge.bridge_for(Route(), net_bridge.Policy(entry=("a.example", 80)))
    b = net_bridge.bridge_for(Route(), net_bridge.Policy(entry=("b.example", 80)))
    a.busy = 1                              # a stream reads it right now
    c = net_bridge.bridge_for(Route(), net_bridge.Policy(entry=("c.example", 80)))
    assert not a.closed and b.closed
    c.busy = 1
    d = net_bridge.bridge_for(Route(), net_bridge.Policy(entry=("d.example", 80)))
    assert not a.closed and not c.closed and not d.closed       # room made: none let go
    # One that has served nothing for a while goes first.
    d.active_at -= net_bridge.IDLE_S + 1
    e = net_bridge.bridge_for(Route(), net_bridge.Policy(entry=("e.example", 80)))
    assert d.closed and not e.closed
    a.busy = c.busy = 0


def test_the_client_gone_the_servers_connection_goes_at_once():
    """A stalled server is not held open after the player has gone: the
    account's one connection is let go with it."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    ended = threading.Event()

    def stall():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\n\r\n")
        conn.settimeout(10)
        try:
            if conn.recv(1) == b"":
                ended.set()
        except OSError:
            ended.set()
        conn.close()
    threading.Thread(target=stall, daemon=True).start()
    b = net_bridge.Bridge(Route())
    try:
        word = __import__("base64").b64encode(f"{net_bridge.USER}:{b.secret}".encode()).decode()
        with socket.create_connection(("127.0.0.1", b.port), timeout=10) as c:
            c.sendall(f"GET http://127.0.0.1:{port}/live.ts HTTP/1.1\r\nHost: x\r\n"
                      f"Proxy-Authorization: Basic {word}\r\n\r\n".encode())
            assert c.recv(4096).startswith(b"HTTP/1.1 200 OK")
        assert ended.wait(5), "the server's connection was held open"
    finally:
        b.close()
        srv.close()


# -- the in-app player ------------------------------------------------------------

class Core(dict):
    """A stand-in for an mpv core: options as items."""

    def __init__(self, **kw):
        super().__init__({"tls-verify": False, "tls-ca-file": "", **kw})


def test_the_player_through_the_guard_nerd_or_not_and_back_to_none(_clean):
    core = Core()
    nerd.apply_stream(core, HOME_SRC, False, url="http://192.168.1.20/live/1.ts")
    assert "http-proxy" not in core and "user-agent" not in core     # untouched, as before
    nerd.apply_stream(core, INTERNET_SRC, False, url="http://provider.example/live/1.ts")
    assert _is_bridge(core["http-proxy"])
    bridge = _only_bridge(_clean)
    assert bridge.policy.internet and bridge.policy.entry == ("provider.example", 80)
    assert not bridge.route.active and "user-agent" not in core
    for url, src, opened in (("http://192.168.1.20/live/1.ts", HOME_SRC, ""),      # at home
                             ("http://provider.example/live/1.ts", INTERNET_SRC,
                              "http://127.0.0.1:41234/tok/live.ts"),             # the buffer's
                             ("/home/me/film.mkv", None, ""),                    # a file
                             ("http://127.0.0.1:6878/ace/getstream?id=x", INTERNET_SRC, ""),
                             ("rtsp://provider.example/x", INTERNET_SRC, "")):
        nerd.apply_stream(core, INTERNET_SRC, False, url="http://provider.example/live/2.ts")
        assert _is_bridge(core["http-proxy"])
        nerd.apply_stream(core, src, False, url=url, opened=opened)
        assert core["http-proxy"] == "", url
    # A web page through yt-dlp (the person's own, which yt-dlp opens
    # itself) as before, and an address from outside judged by where it is.
    nerd.apply_stream(core, None, False, url="https://www.example/watch?v=x", guard=False)
    assert core["http-proxy"] == ""
    nerd.apply_stream(core, None, False, url="https://outside.example/a.m3u8")
    assert _is_bridge(core["http-proxy"])


def test_nerds_proxy_chains_through_the_guard_and_a_home_source_keeps_it(_clean):
    nerd.install(S(nerd_enabled="true", nerd_proxy="http://me:pw@10.0.0.1:3128"))
    core = Core()
    assert nerd.apply_stream(core, INTERNET_SRC, False, url="https://provider.example/1.m3u8")
    bridge = net_bridge._bridges[next(iter(net_bridge._bridges))]
    assert core["http-proxy"] == bridge.address and bridge._up.host == "10.0.0.1"
    nerd.apply_stream(core, HOME_SRC, True, url="http://192.168.1.20/1.ts")
    assert core["http-proxy"] == "http://me:pw@10.0.0.1:3128"        # as it always was
    nerd.install(S(nerd_enabled="true", nerd_proxy="socks5://10.0.0.1:1080"))
    nerd.apply_stream(core, INTERNET_SRC, True, url="https://provider.example/2.m3u8")
    socks = [b for b in net_bridge._bridges.values() if b.policy.entry == ("provider.example", 443)
             and b._up is None]
    assert socks and core["http-proxy"] == socks[0].address           # FFmpeg went direct past it


def test_low_latency_and_the_guard_together():
    """Nerd's low latency for a live channel is the demuxer's; the guard the
    connection's - both on the one stream, and both back for the next."""
    from dopeiptv.core import live_latency
    nerd.install(S(nerd_enabled="true", nerd_low_latency="true"))
    core = Core()
    nerd.apply_stream(core, INTERNET_SRC, False, url="http://provider.example/live/1.ts")
    assert live_latency.apply(core, True)
    assert _is_bridge(core["http-proxy"]) and "live_start_index=-1" in core["demuxer-lavf-o"]
    assert core["cache-secs"] == "2"
    nerd.apply_stream(core, HOME_SRC, True, url="http://192.168.1.20/vod/film.mkv")
    assert live_latency.apply(core, False) == [] and core["http-proxy"] == ""


def test_no_bridge_the_player_plays_as_before_and_says_so_once(monkeypatch, caplog):
    monkeypatch.setattr(net_bridge, "Bridge", lambda *_a, **_k: (_ for _ in ()).throw(OSError("no port")))
    monkeypatch.setattr(net_bridge, "_player_said", set())
    core = Core()
    with caplog.at_level(logging.WARNING, logger="dopeiptv"):
        for _ in range(3):
            nerd.apply_stream(core, INTERNET_SRC, False, url="http://provider.example/1.ts")
    assert "http-proxy" not in core
    said = [r for r in caplog.records if "connects itself, unguarded" in r.getMessage()]
    assert len(said) == 1


def test_a_source_knows_where_its_server_is():
    nerd.set_source({"kind": "xtream", "server": "http://provider.example:8080", "options": {"a": "1"}})
    assert nerd.source() == {"a": "1", "_kind": "xtream", "_server": "http://provider.example:8080"}
    assert net_bridge.policy_for(nerd.source(), "http://192.168.1.5/live/1.ts").internet
    nerd.set_cast_source({"kind": "m3u", "server": "http://192.168.1.20/l.m3u", "options": {}})
    assert not net_bridge.policy_for(nerd.cast_source(), "http://cdn.example/1.ts").internet


# -- the certificate's check fails closed -----------------------------------------

class Refusing(Core):
    """A core that will not take what it is told: *refuse* raises, *ignore*
    is said to take and does not."""

    def __init__(self, refuse=(), ignore=(), **kw):
        super().__init__(**kw)
        self.refuse, self.ignore = set(refuse), set(ignore)

    def __setitem__(self, key, value):
        if key in self.refuse:
            raise RuntimeError(f"option {key} refused")
        if key in self.ignore:
            return
        super().__setitem__(key, value)


@pytest.mark.parametrize("core", [Refusing(refuse={"tls-verify"}), Refusing(refuse={"tls-ca-file"}),
                                  Refusing(ignore={"tls-verify"})])
def test_a_certificate_to_check_on_a_core_that_will_not_is_never_played(core, monkeypatch):
    monkeypatch.setattr(nerd, "ca_file", lambda: "/etc/ssl/certs/ca-certificates.crt")
    with pytest.raises(nerd.CertificateUnchecked):
        nerd.apply_stream(core, INTERNET_SRC, False, url="https://provider.example/live/u/p/1.ts")


def test_where_nothing_is_to_be_checked_such_a_core_plays():
    core = Refusing(refuse={"tls-verify", "tls-ca-file"})
    for src, url in ((INTERNET_SRC, "http://provider.example/1.ts"),           # no TLS
                     (HOME_SRC, "https://192.168.1.20/1.ts"),                  # at home
                     ({**INTERNET_SRC, nerd.TLS_UNCHECKED: "true"}, "https://provider.example/1.ts")):
        nerd.apply_stream(core, src, False, url=url)
    # The person's own tls-verify in Nerd's mpv options is theirs.
    nerd.install(S(nerd_enabled="true", nerd_mpv_options="tls-verify=no"))
    nerd.apply_stream(core, INTERNET_SRC, False, url="https://provider.example/1.ts")


def test_a_core_whose_setting_cannot_be_read_is_still_told_to_check():
    class Unread(Core):
        def __getitem__(self, key):
            if key.startswith("tls-"):
                raise RuntimeError("no such property")
            return super().__getitem__(key)
    core = Unread()
    nerd.apply_stream(core, INTERNET_SRC, False, url="https://provider.example/1.ts")
    assert dict.__getitem__(core, "tls-verify") is True


def test_the_recorders_reasons_in_the_persons_words():
    from dopeiptv.core import recording
    from dopeiptv.i18n import tr
    from dopeiptv.ui.mw_recording import rec_reason
    for reason, key in ((recording.NO_BRIDGE, "rec_reason_no_bridge"),
                        (recording.NO_PRIVATE_FILE, "rec_reason_no_private_file"),
                        (recording.NOT_A_STREAM, "rec_reason_not_a_stream")):
        assert rec_reason(reason) == tr(key) != key


# -- the real thing ---------------------------------------------------------------

@pytest.fixture
def hls_av(tmp_path):
    """A provider serving an HLS list with picture and sound at
    /live/alice/s3cr3tpass/ (a ?token= on the list), one segment by an
    absolute path, and - before the last - one on another server of this
    computer that the provider must not reach; and that other server."""
    ff = shutil.which("ffmpeg")
    if ff is None:
        pytest.skip("needs ffmpeg")
    work = tmp_path / "make"
    work.mkdir()
    sample = work / "sample.ts"
    for video, audio in (("libx264", "aac"), ("mpeg2video", "mp2")):
        made = subprocess.run(
            [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
             "testsrc=duration=4:size=160x120:rate=25", "-f", "lavfi", "-i", "sine=duration=4",
             "-c:v", video, "-g", "25", "-c:a", audio, "-f", "mpegts", str(sample)],
            capture_output=True, timeout=120, check=False)
        if made.returncode == 0:
            break
    else:
        pytest.skip("this ffmpeg cannot make the sample")
    subprocess.run([ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", str(sample),
                    "-c", "copy", "-f", "hls", "-hls_time", "1", "-hls_list_size", "0",
                    "-hls_segment_filename", str(work / "seg%d.ts"), str(work / "index.m3u8")],
                   capture_output=True, timeout=120, check=True)
    inside = Server({"/steal.ts": (200, {}, (work / "seg0.ts").read_bytes())})
    lines = (work / "index.m3u8").read_text(encoding="utf-8").splitlines()
    segs = [line for line in lines if line.endswith(".ts")]
    assert len(segs) >= 3
    routes = {f"/live/alice/s3cr3tpass/{s}": (200, {"Content-Type": "video/mp2t"},
                                              (work / s).read_bytes()) for s in segs}
    routes["/hls/" + segs[1]] = routes.pop(f"/live/alice/s3cr3tpass/{segs[1]}")
    text = "\n".join(lines).replace(f"\n{segs[1]}\n", f"\n/hls/{segs[1]}\n")
    text = text.replace(f"\n{segs[-1]}\n", f"\nhttp://127.0.0.1:{inside.port}/steal.ts\n"
                                            f"#EXTINF:1.000000,\n{segs[-1]}\n")
    routes["/live/alice/s3cr3tpass/index.m3u8"] = (
        200, {"Content-Type": "application/vnd.apple.mpegurl"}, text.encode() + b"\n")
    provider = Server(routes)
    try:
        yield provider, inside, len(segs)
    finally:
        provider.close()
        inside.close()


def _argv_and_env(proc) -> tuple[bytes, bytes]:
    try:
        with open(f"/proc/{proc.pid}/cmdline", "rb") as fh:
            argv = fh.read()
        with open(f"/proc/{proc.pid}/environ", "rb") as fh:
            env = fh.read()
    except FileNotFoundError:
        argv, env = b"\0".join(str(a).encode() for a in proc.args), b""
    return argv, env


def _nothing_of_it(argv: bytes, provider, inside, bridge) -> None:
    for word in (b"alice", b"s3cr3tpass", b"t0k3n", b"provider", str(provider.port).encode(),
                 str(inside.port).encode(), bridge.secret.encode(), b"127.0.0.1"):
        assert word not in argv, word
    assert b".stream.invalid/~/~/~/~.m3u8" in argv


def _resolve_here(monkeypatch, *names: str) -> None:
    """*names* resolve to this computer - for the bridge, which looks names
    up in this process; mpv, which would look them up itself, cannot - and
    a peer on this computer counts as the internet (net_bridge.place_of)."""
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: real("127.0.0.1" if host in names else host, *a, **k))
    monkeypatch.setattr(net_bridge, "place_of", lambda _ip: "")


def _segments_asked(provider) -> list[str]:
    return [s["path"] for s in provider.seen if s["path"].endswith(".ts")]


def test_a_casts_converter_with_the_real_ffmpeg_reads_an_alias(hls_av, _clean, monkeypatch):
    """The provider by a name only the bridge resolves: ffmpeg could not
    have reached it any other way."""
    from dopeiptv.providers import cast_bridge as cb
    provider, inside, n = hls_av
    _resolve_here(monkeypatch, "provider.example")
    nerd.set_cast_source({"kind": "m3u", "server": "http://provider.example/list.m3u", "options": {}})
    b = cb.CastBridge()
    b.exe = shutil.which("ffmpeg")
    url = f"http://provider.example:{provider.port}/live/alice/s3cr3tpass/index.m3u8?token=t0k3n"
    b.start(url, ["h264", "aac"], receiver="127.0.0.1")
    try:
        bridge = _only_bridge(_clean)
        assert bridge.policy.internet and bridge.aliases == 1
        proc = b.spawn()
        assert proc is not None
        argv, environ = _argv_and_env(proc)
        _nothing_of_it(argv, provider, inside, bridge)
        assert not environ or bridge.secret.encode() in environ     # its environment alone
        out = proc.stdout.read()
        assert proc.wait(60) == 0
        assert len(out) > 5_000 and out[4:8] == b"ftyp"
        assert inside.seen == [] and bridge.guarded >= 1
        asked = _segments_asked(provider)
        assert len(asked) == n and any(p.startswith("/hls/") for p in asked)
        assert provider.seen[0]["path"] == "/live/alice/s3cr3tpass/index.m3u8?token=t0k3n"
    finally:
        b.stop()
    assert bridge.aliases == 0                      # forgotten with the run


def test_ffprobe_lists_the_tracks_through_an_alias(hls_av, _clean, monkeypatch):
    from dopeiptv.providers import cast_bridge as cb
    if shutil.which("ffprobe") is None:
        pytest.skip("needs ffprobe")
    provider, inside, _n = hls_av
    _resolve_here(monkeypatch, "provider.example")
    nerd.set_cast_source({"kind": "m3u", "server": "http://provider.example/list.m3u", "options": {}})
    url = f"http://provider.example:{provider.port}/live/alice/s3cr3tpass/index.m3u8?token=t0k3n"
    found = cb.probe_tracks(url, exe=shutil.which("ffprobe"))
    assert len(found["audio"]) == 1 and found["height"] == 120
    assert _only_bridge(_clean).aliases == 0


def test_server_modes_hls_copy_with_the_real_ffmpeg_reads_an_alias(hls_av, _clean):
    from dopeiptv.core import restream
    provider, inside, n = hls_av
    url = f"http://127.0.0.1:{provider.port}/live/alice/s3cr3tpass/index.m3u8?token=t0k3n"
    piped = restream.ffmpeg_opener(shutil.which("ffmpeg"), dict(INTERNET_SRC))(url)
    bridge = _only_bridge(_clean)
    try:
        argv, environ = _argv_and_env(piped.proc)
        _nothing_of_it(argv, provider, inside, bridge)
        data = b"".join(piped.iter_content(65536))
        assert len(data) > 10_000 and data[0] == 0x47 and data[188] == 0x47      # MPEG-TS
        assert inside.seen == [] and len(_segments_asked(provider)) == n
    finally:
        piped.close()
    assert bridge.aliases == 0


def test_a_casts_archive_piece_and_the_buffers_token_never_in_a_readers_arguments(hls_av, _clean):
    """The rewind buffer's own server on this computer, a token in its path:
    a reader reads it as an alias too."""
    from dopeiptv.providers import cast_feed
    provider, inside, _n = hls_av
    seen_argv: list[bytes] = []
    real = subprocess.Popen

    def spy(args, **kw):
        proc = real(args, **kw)
        seen_argv.append(_argv_and_env(proc)[0])
        return proc
    url = f"http://127.0.0.1:{provider.port}/live/alice/s3cr3tpass/seg0.ts"
    feed = cast_feed.Feed(lambda m: (url, 0.0, m), 1000.0, kind="rewind buffer")
    r, w = os.pipe()
    sink = os.fdopen(w, "wb")
    got: list[bytes] = []
    reader = threading.Thread(target=lambda: got.append(os.fdopen(r, "rb").read()), daemon=True)
    reader.start()
    old = cast_feed.subprocess.Popen
    cast_feed.subprocess.Popen = spy
    try:
        secs = feed._read(sink, shutil.which("ffmpeg"), url, 0.0, 0.0, [], threading.Event())
    finally:
        cast_feed.subprocess.Popen = old
        sink.close()
    reader.join(10)
    assert secs is not None and secs > 0.3 and got and got[0][:1] == b"\x47"
    assert seen_argv and b"s3cr3tpass" not in seen_argv[0] and str(provider.port).encode() \
        not in seen_argv[0]
    assert not _only_bridge(_clean).policy.internet         # this computer's own: no source's


def _libmpv():
    try:
        import locale

        import mpv
        locale.setlocale(locale.LC_NUMERIC, "C")
        return mpv.MPV(vo="null", ao="null", terminal=False, idle=True)
    except Exception as e:     # no libmpv here: CI installs it, a dev box may not
        pytest.skip(f"libmpv not available: {e}")


def test_real_mpv_plays_an_internet_hls_whose_list_names_this_computer(hls_av, _clean, monkeypatch):
    """The in-app player, Nerd off: the list's own segments play through the
    bridge (its host is a name only the bridge resolves), the one on another
    server of this computer is refused there and never asked for."""
    provider, inside, n = hls_av
    _resolve_here(monkeypatch, "provider.example")
    m = _libmpv()
    try:
        ends: list[str] = []
        m.event_callback("end-file")(lambda evt: ends.append(str(evt)))
        url = f"http://provider.example:{provider.port}/live/alice/s3cr3tpass/index.m3u8?token=t0k3n"
        assert not nerd.apply_stream(m, dict(INTERNET_SRC), False, url=url)
        assert _is_bridge(m["http-proxy"])
        m.play(url)
        end = time.monotonic() + 40
        while time.monotonic() < end and not ends:
            time.sleep(0.1)
        bridge = _only_bridge(_clean)
        asked = _segments_asked(provider)
        assert ends and len(asked) == n, (ends, asked, bridge.failures)
        assert inside.seen == [] and bridge.guarded >= 1
        assert all(s["host"] == f"provider.example:{provider.port}" for s in provider.seen)
    finally:
        m.terminate()


def test_real_mpv_plays_an_icy_radio_station_through_the_bridge(tmp_path, _clean, monkeypatch):
    """A Shoutcast server answers "ICY 200 OK" and puts its titles between
    the sound every icy-metaint bytes: through the bridge it plays, and the
    title reaches the player."""
    ff = shutil.which("ffmpeg")
    if ff is None:
        pytest.skip("needs ffmpeg")
    mp3 = tmp_path / "tone.mp3"
    made = subprocess.run([ff, "-y", "-loglevel", "error", "-nostdin", "-f", "lavfi", "-i",
                           "sine=frequency=440:duration=8", "-c:a", "libmp3lame", "-b:a", "128k",
                           str(mp3)], capture_output=True, timeout=60, check=False)
    if made.returncode:
        pytest.skip("this ffmpeg makes no mp3")
    data = mp3.read_bytes()
    metaint = 8192
    heads: list[str] = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    port = srv.getsockname()[1]

    def serve(conn):
        head = b""
        while b"\r\n\r\n" not in head:
            got = conn.recv(4096)
            if not got:
                return
            head += got
        heads.append(head.decode("latin-1"))
        icy = b"icy-metadata: 1" in head.lower()
        conn.sendall(b"ICY 200 OK\r\nicy-name: Test radio\r\ncontent-type: audio/mpeg\r\n"
                     + (f"icy-metaint: {metaint}\r\n".encode() if icy else b"") + b"\r\n")
        title = b"StreamTitle='Artist - Song';"
        block = title + b"\0" * (-len(title) % 16)
        try:
            for i in range(0, len(data), metaint):
                conn.sendall(data[i:i + metaint])
                if icy:
                    conn.sendall(bytes([len(block) // 16]) + block)
                time.sleep(0.05)
        except OSError:
            pass
        conn.close()

    def accept():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(conn,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    _resolve_here(monkeypatch, "radio.example")
    m = _libmpv()
    try:
        url = f"http://radio.example:{port}/stream"
        nerd.apply_stream(m, None, False, url=url)        # a station is no source's
        assert _is_bridge(m["http-proxy"])
        m.play(url)
        end = time.monotonic() + 30
        title = pos = None
        while time.monotonic() < end:
            try:
                pos = m.time_pos
                title = (m.metadata or {}).get("icy-title")
            except Exception:
                pass
            if title and pos and pos > 1.0:
                break
            time.sleep(0.2)
        assert title == "Artist - Song" and pos and pos > 1.0, (title, pos, heads)
        assert heads and "icy-metadata: 1" in heads[0].lower()
        assert _only_bridge(_clean).served >= 1
    finally:
        m.terminate()
        srv.close()


_TLS_CHILD = r"""
import json, os, sys, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import main_window as mw

external = []
mw.launch_player = lambda *a, **k: external.append(a[1] if len(a) > 1 else "")
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("live_rewind_minutes", 0)
store = PlaylistStore(s)
store.add({"id": "p1", "name": "P", "kind": "m3u", "server": "https://provider.example/list.m3u",
           "options": {}})
store.set_active("p1")
w = mw.MainWindow(DemoClient(), s, store)
if w.player is None:
    print("SKIP: no embedded player here")
    sys.exit(0)
player = w.player
player._dvr_start = lambda url, key=None: None
w.wake.acquire = w.wake.release = lambda *a, **k: None
try:
    import locale
    import mpv
    locale.setlocale(locale.LC_NUMERIC, "C")
    core = mpv.MPV(vo="null", ao="null", terminal=False, idle=True)
except Exception as e:
    print("SKIP: no libmpv core here (%s)" % e)
    sys.exit(0)


class Refusing:
    # A core that will not check certificates: tls-verify refused.
    def __init__(self, inner):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "loaded", [])

    def __getitem__(self, key):
        return self._inner[key]

    def __setitem__(self, key, value):
        if key == "tls-verify" and value:
            raise RuntimeError("tls-verify refused")
        self._inner[key] = value

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def __setattr__(self, name, value):
        setattr(self._inner, name, value)

    def play(self, url):
        self.loaded.append(url)
        self._inner.play(url)


m = player.video.mpv = Refusing(core)
url = "https://provider.example/live/u/p/1.ts"
w._start_playback(url, "TV4", None, 7, "live", item={"name": "TV4", "stream_id": 7})
out = {"refusal": player.refusal, "error": w.stream_error.text(), "shown": not w.stream_error.isHidden(),
       "external": list(external), "loaded": list(m.loaded), "current": player.current_url,
       "want": tr("player_tls_unverifiable")}
# A stream on plain http from the same core: nothing to check, it opens.
w._start_playback("http://provider.example/live/u/p/2.ts", "TV5", None, 8, "live",
                  item={"name": "TV5", "stream_id": 8})
out["then"] = {"refusal": player.refusal, "loaded": m.loaded, "external": external}
print("RESULT" + json.dumps(out), flush=True)
os._exit(0)
"""


def test_the_window_says_why_and_never_hands_it_to_another_player(tmp_path):
    """A core that will not check the certificate of an internet https
    stream: the stream is not opened, the video says why, and it is not
    handed to an external mpv instead (which play()'s other failures are)."""
    pytest.importorskip("PyQt6.QtWidgets")
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env["QT_QPA_PLATFORM"] = "offscreen"
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    out = subprocess.run([sys.executable, "-c", _TLS_CHILD], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    if out.stdout.startswith("SKIP"):
        pytest.skip(out.stdout.strip())
    line = next((ln for ln in out.stdout.splitlines() if ln.startswith("RESULT")), None)
    assert line, out.stdout + out.stderr[-3000:]
    got = json.loads(line[6:])
    assert got["refusal"] == got["want"] and got["want"] in got["error"] and got["shown"], got
    assert got["external"] == [] and got["loaded"] == [] and got["current"] is None, got
    assert got["then"]["refusal"] == "" and got["then"]["loaded"] == [
        "http://provider.example/live/u/p/2.ts"] and got["then"]["external"] == [], got
