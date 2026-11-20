"""A source's address is the provider's word, and mpv takes far more than
streams there. av://lavfi:...metadata=mode=print:file=PATH made libmpv write
lines of the sender's choice to a file of the sender's choice - a channel in
a playlist, clicked once, could rewrite ~/.bashrc. mpv refuses such an
address from a playlist it reads itself; the app reads the playlist and
hands mpv one address at a time, so the app looks first
(dopeiptv/core/stream_address.py)."""
from __future__ import annotations

import logging

import pytest

from dopeiptv.core import stream_address
from dopeiptv.core.log import log
from dopeiptv.core.stream_address import played, refusal, scheme_of
from dopeiptv.providers import client as client_mod
from dopeiptv.providers.client import M3UClient

WRITES_A_FILE = ("av://lavfi:color=c=black:s=16x16:d=1,metadata=mode=add:key=X:value=$(id),"
                 "metadata=mode=print:file=.bashrc")

#: What a provider's stream comes over: played as it always was.
STREAMS = (
    "http://tv.example:8080/live/u/p/1.ts", "https://cdn.example/hls/a.m3u8?token=1",
    "HTTP://TV.EXAMPLE/x.ts", "rtsp://cam.example/1", "rtsps://cam.example/1",
    "rtmp://live.example/app playpath=x live=1", "rtmps://live.example/app/x",
    "rtp://@239.1.1.1:5000", "udp://@239.0.0.1:1234", "udp://239.0.0.1:1234",
    "mms://radio.example/a", "mmsh://radio.example/a", "srt://relay.example:9000?streamid=x",
    "rist://relay.example:1968", "ftp://nas.example/film.mkv", "sftp://nas.example/film.mkv",
    "smb://nas/films/a.mkv", "tcp://10.0.0.5:1234",
    "http://192.168.1.1:4022/udp/239.0.0.1:1234", "http://127.0.0.1:6878/ace/getstream?id=abc",
    "http://tv.example/x.ts|User-Agent=VLC&Referer=http://tv.example/",
)

#: No stream, whoever lists it.
NEVER = (
    WRITES_A_FILE, "av://lavfi:testsrc", "AV://LAVFI:testsrc", "avdevice://lavfi:mandelbrot",
    "av://  lavfi:testsrc", "edl://%5%a.mkv", "memory://#EXTM3U", "hex://23454454",
    "lavf://concat:a|b", "ffmpeg://subfile,,start,0,end,9,,:/etc/passwd",
    "gopher://127.0.0.1:6379/_FLUSHALL%0D%0A", "gophers://10.0.0.1:70/x", "unix:///run/docker.sock",
    "fd://3", "fdclose://3", "slice://0-100@av://lavfi:testsrc", "md5://x", "null://", "data://x",
    "--script=/tmp/x.lua", "-vf=lavfi", "http://tv.example/a\nb", "http://tv.example/a\x00b",
)


def test_the_networks_own_protocols_play_from_any_source():
    for address in STREAMS:
        assert refusal(address, remote=True) == "", address
        assert refusal(address, remote=False) == "", address
        assert played(address, True) == address


def test_what_is_no_stream_is_refused_from_every_source():
    for address in NEVER:
        assert refusal(address, remote=True), address
        assert refusal(address, remote=False), address
        assert played(address, True) == "" and played(address, False) == ""


def test_a_playlist_file_of_ones_own_lists_discs_tuners_devices_and_files():
    local = ("dvb://SVT1", "cdda://", "dvd://1", "bd://", "bluray://", "tv://",
             "av://v4l2:/dev/video0", "avdevice://dshow:video=Capture", "mf://*.jpg",
             "archive:///home/u/a.zip|/a.mkv", "/home/u/Videos/a.mkv", "Videos/a.mkv",
             "C:\\Videos\\a.mkv", "\\\\nas\\films\\a.mkv", "//nas/films/a.mkv",
             "file:///home/u/a.mkv", "file://nas/films/a.mkv", "acestream://abc")
    for address in local:
        assert refusal(address, remote=False) == "", address


def test_a_list_from_the_network_names_streams_and_nothing_of_this_computer():
    """Not a file (on Windows \\\\host\\share logs in there with the person's
    own account), not a disc, a tuner card or a capture device - and no
    protocol mpv does not play over the network."""
    for address in ("dvb://SVT1", "cdda://", "dvd://1", "bd://", "tv://", "av://v4l2:/dev/video0",
                    "avdevice://dshow:video=Capture", "mf://*.jpg", "archive:///a.zip|/a.mkv",
                    "acestream://abc", "plugin://video", "pipe://ffmpeg -i x", "screen://",
                    "/home/u/Videos/a.mkv", "Videos/a.mkv", "C:\\Videos\\a.mkv",
                    "file:///home/u/a.mkv", "file:///C:/Videos/a.mkv", "file://localhost/C:/a.mkv",
                    "\\\\evil.example\\share\\a.ts", "//evil.example/share/a.ts",
                    "file://evil.example/share/a.ts", "file:////evil.example/share/a.ts",
                    "\\\\.\\pipe\\x", "/dev/video0"):
        assert refusal(address, remote=True), address
        assert played(address, True) == ""


def test_yt_dlp_runs_on_a_web_stream_the_person_opened_never_on_a_source_s():
    """ytdl:// hands the page after it to yt-dlp: the app's own word for an
    address opened by hand (Open address, a saved web stream), never one a
    provider, a box or a playlist at a web address may give."""
    from dopeiptv.core import ytdlp
    for address in ("ytdl://www.youtube.com/watch?v=x", "YTDL://evil.example/x",
                    "ytdl://http://127.0.0.1:8080/admin"):
        assert refusal(address, remote=True), address
        assert played(address, True) == ""
    own = ytdlp.mpv_address("https://www.youtube.com/watch?v=x")
    assert own.startswith("ytdl://")
    # The player's last look and a playlist file of one's own: as before.
    assert refusal(own, remote=False) == "" and played(own, False) == own
    c = _Fake("ytdl://www.youtube.com/watch?v=x")
    client_mod._nerd_lists(c, {"_kind": "xtream", "_server": "http://tv.example"}, "p1")
    assert c.live_url(1) == "" and c.vod_url(1) == "" and c.archive_link(1, 1.0) is None


def test_the_protocol_is_read_as_mpv_reads_it():
    assert scheme_of("HtTp://x") == "http" and scheme_of("av://lavfi:x") == "av"
    assert scheme_of("/a/b://c") == "" and scheme_of("://x") == "" and scheme_of("1a://x") == ""
    assert scheme_of("a b://x") == "" and scheme_of("C:\\a") == "" and scheme_of("") == ""
    assert scheme_of("rtmp+x-1.2://h") == "rtmp+x-1.2"


@pytest.fixture
def warnings():
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                seen.append(record.getMessage())
    h = Catch()
    log.addHandler(h)
    stream_address._said.clear()
    yield seen
    log.removeHandler(h)
    stream_address._said.clear()


def test_a_refusal_is_logged_once_with_its_reason_and_never_the_address(warnings):
    hostile = "av://lavfi:color,metadata=mode=print:file=/home/anna/.bashrc?password=hunter2"
    for _ in range(5):
        assert played(hostile, True, "m3u") == ""
    assert len(warnings) == 1, warnings
    assert "av://lavfi" in warnings[0] and "m3u" in warnings[0]
    assert "hunter2" not in warnings[0] and "anna" not in warnings[0]


PLAYLIST = f"""#EXTM3U
#EXTINF:-1 tvg-id="one",One
http://tv.example/1.ts
#EXTINF:-1 tvg-id="two",Two
{WRITES_A_FILE}
#EXTINF:-1 tvg-id="three",Three
dvb://SVT1
#EXTINF:-1 tvg-id="four",Four
http://tv.example/4.ts
"""


def test_a_playlists_channel_keeps_its_place_and_has_no_address(warnings):
    c = M3UClient("http://tv.example/list.m3u")
    c._parse(PLAYLIST)
    c._loaded = True
    rows = c.live_streams()
    assert [r["name"] for r in rows] == ["One", "Two", "Three", "Four"]
    assert [r["num"] for r in rows] == [1, 2, 3, 4], "the places after it did not move"
    assert rows[1]["_url"] == "" and c.live_url(rows[1]["stream_id"]) == ""
    assert c.live_url(rows[0]["stream_id"]) == "http://tv.example/1.ts"
    assert c.live_url(rows[3]["stream_id"]) == "http://tv.example/4.ts"
    assert any("no stream" in w for w in warnings)


def _wrapped(server: str, monkeypatch) -> M3UClient:
    """An M3U source as make_client hands it out, its list read from PLAYLIST."""
    monkeypatch.setattr(M3UClient, "_fetch", lambda self: PLAYLIST)
    return client_mod.make_client({"kind": "m3u", "server": server})


def test_every_address_a_source_hands_out_is_looked_at(monkeypatch, warnings):
    remote = _wrapped("http://tv.example/list.m3u", monkeypatch)
    ids = [r["stream_id"] for r in remote.live_streams()]
    assert [remote.live_url(i) for i in ids] == ["http://tv.example/1.ts", "", "", "http://tv.example/4.ts"]
    # The same list as a file of the person's own: its tuner card plays.
    local = _wrapped("/home/u/list.m3u", monkeypatch)
    ids = [r["stream_id"] for r in local.live_streams()]
    assert [local.live_url(i) for i in ids] == ["http://tv.example/1.ts", "", "dvb://SVT1",
                                                "http://tv.example/4.ts"]


class _Fake:
    """A client with every call make_client wraps."""
    server = "http://tv.example"

    def __init__(self, address: str) -> None:
        self.address = address

    def live_streams(self, category_id=None):
        return []

    def live_url(self, stream_id, fmt="ts"):
        return self.address

    def vod_url(self, stream_id, ext=None):
        return self.address

    def episode_url(self, episode_id, ext=None):
        return self.address

    def copy_url(self, stream_id):
        return self.address

    def timeshift_urls(self, stream_id, start_dt, duration_min):
        return [self.address, "http://tv.example/archive.ts"]

    def archive_link(self, stream_id, start, stop=None):
        return {"url": self.address, "start": 1.0, "stop": 2.0}


@pytest.mark.parametrize("kind", ["xtream", "stalker", "enigma2", "jellyfin", "m3u"])
def test_no_way_out_of_a_client_carries_what_is_no_stream(kind, warnings):
    for hostile in (WRITES_A_FILE, "edl://x", "gopher://127.0.0.1:6379/_x"):
        c = _Fake(hostile)
        client_mod._nerd_lists(c, {"_kind": kind, "_server": "http://tv.example"}, "p1")
        assert c.live_url(1) == "" and c.vod_url(1) == "" and c.episode_url(1) == ""
        assert c.copy_url(1) == ""
        assert c.timeshift_urls(1, None, 60) == ["http://tv.example/archive.ts"]
        assert c.archive_link(1, 1.0) is None
    ok = _Fake("http://tv.example/1.ts")
    client_mod._nerd_lists(ok, {"_kind": kind, "_server": "http://tv.example"}, "p1")
    assert ok.live_url(1) == ok.vod_url(1) == ok.episode_url(1) == ok.copy_url(1) == "http://tv.example/1.ts"
    assert ok.archive_link(1, 1.0) == {"url": "http://tv.example/1.ts", "start": 1.0, "stop": 2.0}
    assert ok.timeshift_urls(1, None, 60) == ["http://tv.example/1.ts", "http://tv.example/archive.ts"]
    # No address stays no address (a box that gave none), not an error.
    none = _Fake("")
    client_mod._nerd_lists(none, {"_kind": kind, "_server": "http://tv.example"}, "p1")
    assert none.live_url(1) == "" and none.archive_link(1, 1.0) is None


def test_an_address_the_persons_own_rewrite_rules_made_is_theirs(warnings):
    """Looked at last: a rule may make a stream's address of what was none
    (an engine on this computer, a copy on a disk) - and never of what is
    no stream at all."""
    from dopeiptv.core import nerd, url_rewrite
    from tests.test_nerd import S
    nerd.install(S(nerd_enabled="true"))
    try:
        rules = url_rewrite.dump([url_rewrite.Rule("acestream://", "http://127.0.0.1:6878/ace/getstream?id="),
                                  url_rewrite.Rule("http://tv.example/films/", "/mnt/films/"),
                                  url_rewrite.Rule("graph://", "av://lavfi:")])
        source = {"_kind": "xtream", "_server": "http://tv.example", url_rewrite.KEY: rules}
        for raw, out in (("acestream://abc", "http://127.0.0.1:6878/ace/getstream?id=abc"),
                         ("http://tv.example/films/a.mkv", "/mnt/films/a.mkv"),
                         ("http://tv.example/1.ts", "http://tv.example/1.ts"),
                         ("graph://testsrc", ""),          # no stream, whoever made it
                         ("dvb://SVT1", "")):              # the source's own word, untouched
            c = _Fake(raw)
            client_mod._nerd_lists(c, source, "p1")
            assert c.live_url(1) == out, raw
            assert c.vod_url(1) == out, raw
    finally:
        nerd.install(None)


def test_a_portals_answer_that_is_no_stream_is_no_address():
    from dopeiptv.providers.stalker import archive_address
    assert archive_address({"cmd": "auto http://cdn.example/a.mpg position:0"}) == "http://cdn.example/a.mpg"
    assert archive_address({"cmd": "auto " + WRITES_A_FILE}) == ""
    assert archive_address({"cmd": "ffmpeg edl://x"}) == ""
    assert archive_address("gopher://127.0.0.1:25/_HELO") == ""


def test_the_players_take_a_last_look_before_mpv():
    """Whatever way an address reaches a player, what is no stream is never
    handed to mpv: the first thing play() does, before it touches anything."""
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.media.embedded import EmbeddedPlayer
    from dopeiptv.ui.multiview_cell import _MultiviewCell

    class Untouched:
        def __getattr__(self, name):
            raise AssertionError(f"play() went on to {name}")

        def __setattr__(self, name, value):
            raise AssertionError(f"play() went on to set {name}")

    for hostile in (WRITES_A_FILE, "edl://x", "memory://x", "fdclose://3"):
        assert EmbeddedPlayer.play(Untouched(), hostile, "t") is False
        assert _MultiviewCell.play(Untouched(), hostile, "t") is False


# -- a source on the internet hands out media streams, never this computer -----------

#: What a source on the internet may not send the player to, and why: a
#: file share (Windows signs in there with the person's account), a raw
#: socket, the printer's page on this computer, the cloud's metadata.
FROM_THE_INTERNET = ("smb://evil.example/share/a.ts", "tcp://evil.example:25", "tls://evil.example:443",
                     "ftp://evil.example/a.ts", "sftp://evil.example/a.ts", "davs://evil.example/a",
                     "ipfs://Qm", "httpproxy://evil.example:3128/10.0.0.1:22", "ytdl://evil.example/x",
                     "http://127.0.0.1:631/", "http://169.254.169.254/latest/meta-data/",
                     "http://[::1]/")
#: Every spelling of this computer and of a link-local host is the same host.
SPELLINGS = ("http://127.1/", "http://2130706433/", "http://0x7f000001/", "http://0177.0.0.1/",
             "http://127.0.0.1./", "http://localhost:8080/", "http://LOCALHOST./", "http://a.localhost/",
             "http://[::ffff:127.0.0.1]/", "http://[::ffff:7f00:1]/", "http://[0:0:0:0:0:0:0:1]/",
             "http://[::127.0.0.1]/", "http://0.0.0.0:631/", "http://0/", "http://[::]/",
             "http://[fe80::1%25eth0]/", "http://169.254.1.1/", "http://metadata.google.internal/",
             "http://[fd00:ec2::254]/", "http://100.100.100.200/", "http://127.0.0.1%2e/",
             "http://tv.example@127.0.0.1/", "http://tv.example#@127.0.0.1/x",
             "http://tv.example\\@127.0.0.1/", "http://１２７.０.０.１/", "https://[::1]:8443/x.m3u8",
             "rtmp://127.0.0.1/app playpath=x", "rtsp://169.254.10.10/cam", "udp://@127.0.0.1:1234")
#: From the internet as before: a server's stream, a multicast group, and a
#: group through a relay at home (an ISP's multicast proxy, udpxy on the
#: router).
STILL_FROM_THE_INTERNET = (
    "http://tv.example:8080/live/u/p/1.ts", "https://cdn.example/hls/a.m3u8?token=1",
    "rtsp://cam.example/1", "rtmp://live.example/app playpath=x live=1", "udp://@239.0.0.1:1234",
    "rtp://@239.1.1.1:5000", "srt://relay.example:9000?streamid=x", "rist://relay.example:1968",
    "mms://radio.example/a", "http://192.168.1.1:4022/udp/239.0.0.1:1234",
    "http://10.0.0.1:4022/rtp/@232.1.1.1:5000",
    "http://tv.example/x.ts|Referer=http://127.0.0.1/", "http://127.0.0.1.tv.example/x.ts")
#: A device of the home network: never from a source on the internet unless
#: the person let that source in (its option home_addresses).
HOME_FROM_THE_INTERNET = (
    "http://10.0.0.5:8080/x.ts", "http://[fd00::5]:8096/x", "http://100.64.1.2/x.ts",
    "http://192.168.1.1/", "http://router/admin", "http://nas.local:5000/x.ts",
    "http://kodi.lan:8080/jsonrpc", "http://192.168.1.1:4022/status",
    "http://192.168.1.1:4022/udp/192.168.1.5:80")


def test_a_source_on_the_internet_hands_out_media_streams_and_never_this_computer():
    for address in FROM_THE_INTERNET + SPELLINGS:
        assert refusal(address, internet=True), address
        assert played(address, True, internet=True) == ""
    for address in STILL_FROM_THE_INTERNET:
        assert refusal(address, internet=True) == "", address
    for address in HOME_FROM_THE_INTERNET:
        assert refusal(address, internet=True) == stream_address.HOME + " from the internet", address
        assert refusal(address, internet=True, home_ok=True) == "", address
        assert refusal(address, internet=False) == "", "from the home network's own source"
    # What is no stream whoever lists it stays so.
    for address in NEVER:
        assert refusal(address, internet=True), address
    # A source of the home network as before: its NAS's share, udpxy here.
    for address in FROM_THE_INTERNET + SPELLINGS:
        assert refusal(address, remote=True) == "" or stream_address.scheme_of(address) not in \
            stream_address.NETWORK, address


def test_an_acestream_list_on_the_internet_plays_through_the_engine_here_and_nothing_else():
    # The engine's streams, as AceStream playlists write them, play as before.
    for address in ("http://127.0.0.1:6878/ace/getstream?id=0123abcd",
                    "http://localhost:6878/ace/getstream?infohash=ab12&pid=1",
                    "http://127.0.0.1:6878/ace/manifest.m3u8?id=0123abcd",
                    "http://[::1]:6878/ace/r/0123abcd/x.ts"):
        assert refusal(address, internet=True) == "", address
        assert played(address, True, internet=True) == address
    # Anything else on this computer stays refused: another port, another
    # path of the engine, https, a login, a host part any reader could read
    # another way, the engine's port at a link-local or spelt-out host.
    for address in ("http://127.0.0.1:6879/ace/getstream?id=1", "http://127.0.0.1:6878/webui/api/service",
                    "http://127.0.0.1:6878/server/api?method=x", "http://127.0.0.1:6878/",
                    "https://127.0.0.1:6878/ace/getstream?id=1", "http://u:p@127.0.0.1:6878/ace/getstream",
                    "http://tv.example@127.0.0.1:6878/ace/getstream", "http://127.1:6878/ace/getstream",
                    "http://127.0.0.1:6878\\@127.0.0.1:631/ace/getstream", "http://127.0.0.1:631/ace/getstream",
                    "http://169.254.1.1:6878/ace/getstream", "http://0.0.0.0:6878/ace/getstream",
                    "rtmp://127.0.0.1:6878/ace/getstream"):
        assert refusal(address, internet=True), address


def test_where_a_source_is_read_from_its_own_address_never_looked_up():
    for server in ("http://tv.example/get.php?username=u", "tv.example:8080", "https://abc.plex.direct:32400",
                   "http://8.8.8.8", "http://[2001:db8::1]/", "http://[bad"):
        assert stream_address.on_internet(server), server
    for server in ("http://192.168.1.10/list.m3u", "10.0.0.1:8096", "http://127.0.0.1:34400/lineup.m3u",
                   "https://[fd00::5]:8096", "nas", "http://vuplus.local", "http://box.lan:8001",
                   "http://fritz.box/dvb/m3u/tvsd.m3u", "http://100.101.102.103:9981", ""):
        assert not stream_address.on_internet(server), server


PLAYLIST_ODD = """#EXTM3U
#EXTINF:-1 tvg-logo="http://127.0.0.1:631/printers",Share
smb://evil.example/share/a.ts
#EXTINF:-1 tvg-logo="http://192.168.1.1/picon/2.png",Socket
tcp://evil.example:25
#EXTINF:-1 tvg-logo="http://[::1]/x.png",Printer
http://127.0.0.1:631/
#EXTINF:-1 tvg-logo="http://169.254.169.254/x.png",Cloud
http://169.254.169.254/latest/meta-data/
#EXTINF:-1 tvg-logo="http://logos.example/5.png",Loopback
http://[::1]/
#EXTINF:-1 tvg-logo="http://localhost/6.png",Proxy
http://192.168.1.1:4022/udp/239.0.0.1:1234
"""


def _odd(server: str, monkeypatch) -> M3UClient:
    monkeypatch.setattr(M3UClient, "_fetch", lambda self: PLAYLIST_ODD)
    return client_mod.make_client({"kind": "m3u", "server": server})


def test_an_internet_playlist_plays_no_share_socket_or_service_of_this_computer(monkeypatch, warnings):
    internet = _odd("http://tv.example/list.m3u", monkeypatch)
    rows = internet.live_streams()
    assert [internet.live_url(r["stream_id"]) for r in rows] == [
        "", "", "", "", "", "http://192.168.1.1:4022/udp/239.0.0.1:1234"]
    # Said once each, with its reason and never the address.
    assert any("smb://" in w for w in warnings) and any("this computer" in w for w in warnings)
    assert any("link-local" in w for w in warnings)
    assert not any("evil.example" in w or "631" in w for w in warnings)
    # The same list from a box of the home network or as a file of one's own: as before.
    for server in ("http://192.168.1.10/list.m3u", "http://nas.local/list.m3u", "/home/u/list.m3u"):
        own = _odd(server, monkeypatch)
        assert [own.live_url(r["stream_id"]) for r in own.live_streams()] == [
            "smb://evil.example/share/a.ts", "tcp://evil.example:25", "http://127.0.0.1:631/",
            "http://169.254.169.254/latest/meta-data/", "http://[::1]/",
            "http://192.168.1.1:4022/udp/239.0.0.1:1234"], server


def test_a_backup_address_on_the_internet_makes_the_source_the_internets(monkeypatch):
    from dopeiptv.core import mirrors, nerd
    from tests.test_nerd import S
    nerd.install(S(nerd_enabled="true"))
    try:
        assert not client_mod._on_internet("m3u", "http://192.168.1.10/list.m3u", {})
        assert client_mod._on_internet("m3u", "http://192.168.1.10/list.m3u",
                                       {mirrors.KEY: "http://tv.example/list.m3u"})
        assert not client_mod._on_internet("m3u", "http://192.168.1.10/list.m3u",
                                           {mirrors.KEY: "http://192.168.1.11/list.m3u"})
    finally:
        nerd.install(None)


def test_the_persons_udpxy_on_this_computer_still_plays_an_internet_sources_multicast(warnings):
    from dopeiptv.core import nerd
    from tests.test_nerd import S
    nerd.install(S(nerd_enabled="true", nerd_udpxy="127.0.0.1:4022"))
    try:
        source = {"_kind": "m3u", "_server": "http://tv.example/list.m3u"}
        c = _Fake("udp://@239.0.0.1:1234")
        client_mod._nerd_lists(c, source, "p1")
        assert c.live_url(1) == "http://127.0.0.1:4022/udp/239.0.0.1:1234"
        # The source's own word is judged first: no udpxy makes this computer of it.
        c = _Fake("udp://@127.0.0.1:1234")
        client_mod._nerd_lists(c, source, "p1")
        assert c.live_url(1) == ""
    finally:
        nerd.install(None)


def test_an_internet_sources_archive_and_catchup_addresses_are_judged_the_same(warnings):
    for kind in ("stalker", "xtream", "m3u"):
        c = _Fake("http://127.0.0.1:631/")
        client_mod._nerd_lists(c, {"_kind": kind, "_server": "http://tv.example"}, "p1")
        assert c.archive_link(1, 1.0) is None
        assert c.timeshift_urls(1, None, 60) == ["http://tv.example/archive.ts"]
        assert c.played_from_source("http://[::1]/x") == ""
        assert c.played_from_source("http://tv.example/x") == "http://tv.example/x"
        home = _Fake("http://127.0.0.1:631/")
        client_mod._nerd_lists(home, {"_kind": kind, "_server": "http://192.168.1.2"}, "p1")
        assert home.archive_link(1, 1.0) is not None
        assert home.played_from_source("http://[::1]/x") == "http://[::1]/x"


def test_a_channels_own_catchup_address_from_the_internet_is_the_sources_word(warnings):
    """Nerd's catch-up with no template of the person's: the channel's own
    catchup-source is the provider's word (ui/mw_timeshift.py)."""
    from datetime import datetime, timezone

    from dopeiptv.core import nerd
    from dopeiptv.ui.mw_timeshift import _TimeshiftMixin
    from tests.test_nerd import S
    nerd.install(S(nerd_enabled="true"))

    class Window:
        settings = S()

        def __init__(self, server: str) -> None:
            self.client = _Fake("http://tv.example/live/1.ts")
            client_mod._nerd_lists(self.client, {"_kind": "m3u", "_server": server}, "p1")

    start = datetime(2026, 11, 1, 20, tzinfo=timezone.utc).timestamp()
    try:
        for template, channel, server, out in (
                ("", "http://127.0.0.1:631/x?s={utc}", "http://tv.example/l.m3u", None),
                ("", "http://tv.example/arc/{utc}.ts", "http://tv.example/l.m3u",
                 "http://tv.example/arc/%d.ts" % start),
                ("", "http://127.0.0.1:631/x?s={utc}", "http://192.168.1.2/l.m3u",
                 "http://127.0.0.1:631/x?s=%d" % start),
                # The person's own template is theirs.
                ("http://127.0.0.1:8000/{utc}.ts", "", "http://tv.example/l.m3u",
                 "http://127.0.0.1:8000/%d.ts" % start)):
            nerd.set_source({"kind": "m3u", "options": {"catchup_mode": "default",
                                                         "catchup_template": template}})
            it = {"stream_id": 1, "catchup_source": channel}
            assert _TimeshiftMixin._nerd_catchup_url(Window(server), it, start, start + 1800) == out, \
                (template, channel, server)
    finally:
        nerd.set_source(None)
        nerd.install(None)


# -- a source on the internet's pictures: the same host rule --------------------------

def test_an_internet_sources_pictures_never_name_this_computer(monkeypatch):
    """The app fetches a row's logo and poster itself: from a source on the
    internet, one on this computer or a link-local host would have it send
    requests there. A private address of the home network stays (a picon on
    the box), and a home network's or a playlist file's pictures as before."""
    internet = _odd("http://tv.example/list.m3u", monkeypatch)
    assert [r["stream_icon"] for r in internet.live_streams()] == [
        "", "", "", "", "http://logos.example/5.png", ""]
    monkeypatch.setattr(M3UClient, "_fetch", lambda self: PLAYLIST_ODD)
    let_in = client_mod.make_client({"kind": "m3u", "server": "http://tv.example/list.m3u",
                                     "options": {"home_addresses": True}})
    assert [r["stream_icon"] for r in let_in.live_streams()] == [
        "", "http://192.168.1.1/picon/2.png", "", "", "http://logos.example/5.png", ""]
    for server in ("http://192.168.1.10/list.m3u", "/home/u/list.m3u"):
        own = _odd(server, monkeypatch)
        assert [r["stream_icon"] for r in own.live_streams()] == [
            "http://127.0.0.1:631/printers", "http://192.168.1.1/picon/2.png", "http://[::1]/x.png",
            "http://169.254.169.254/x.png", "http://logos.example/5.png", "http://localhost/6.png"], server
    for spelt in SPELLINGS:
        if spelt.startswith("http"):
            assert stream_address.picture_refusal(spelt), spelt
    for fine in ("http://logos.example/a.png", "https://image.tmdb.org/t/p/w500/x.jpg",
                 "http://192.168.1.1/picon/1.png", "/home/u/picons/1.png", ""):
        assert stream_address.picture_refusal(fine) == "", fine
    assert stream_address.picture_refusal("http://192.168.1.1/picon/1.png", home=True) \
        == stream_address.HOME


class _Pictured(_Fake):
    """A client whose lists and pages carry pictures."""

    def __init__(self, picture: str) -> None:
        super().__init__("http://tv.example/1.ts")
        self.picture = picture

    def vod_streams(self, category_id=None):
        return [{"stream_id": 1, "stream_icon": self.picture, "cover": self.picture}]

    def series_list(self, category_id=None):
        return [{"series_id": 1, "cover": self.picture, "backdrop_path": [self.picture, "http://b.example/1"]}]

    def series_info(self, series_id):
        return {"info": {"cover": self.picture, "backdrop_path": [self.picture]},
                "seasons": [{"cover": self.picture, "cover_big": self.picture}],
                "episodes": {"1": [{"id": 5, "info": {"movie_image": self.picture}}]}}

    def vod_info(self, vod_id):
        return {"info": {"movie_image": self.picture, "cover_big": self.picture,
                         "backdrop_path": self.picture}}


def test_every_list_and_page_of_an_internet_source_is_looked_at_for_pictures():
    c = _Pictured("http://127.0.0.1:631/x.png")
    client_mod._nerd_lists(c, {"_kind": "xtream", "_server": "http://tv.example"}, "p1")
    assert c.vod_streams() == [{"stream_id": 1, "stream_icon": "", "cover": ""}]
    assert c.series_list() == [{"series_id": 1, "cover": "", "backdrop_path": ["http://b.example/1"]}]
    assert c.series_info(1) == {"info": {"cover": "", "backdrop_path": []},
                                "seasons": [{"cover": "", "cover_big": ""}],
                                "episodes": {"1": [{"id": 5, "info": {"movie_image": ""}}]}}
    assert c.vod_info(1) == {"info": {"movie_image": "", "cover_big": "", "backdrop_path": ""}}
    # The home network's own, on this computer too: as before.
    for picture, opts in (("http://127.0.0.1:8096/Items/1/Images/Primary",
                           {"_server": "http://127.0.0.1:8096"}),
                          ("http://192.168.1.5/picon.png",
                           {"_server": "http://tv.example", "home_addresses": True})):
        c = _Pictured(picture)
        client_mod._nerd_lists(c, dict(opts, _kind="jellyfin"), "p1")
        assert c.vod_streams()[0]["stream_icon"] == picture
        assert c.series_info(1)["episodes"]["1"][0]["info"]["movie_image"] == picture
    # A device at home named by a source on the internet the person did not let in: out.
    c = _Pictured("http://192.168.1.5/picon.png")
    client_mod._nerd_lists(c, {"_kind": "jellyfin", "_server": "http://tv.example"}, "p1")
    assert c.vod_streams()[0]["stream_icon"] == ""


def test_an_internet_playlists_own_guide_address_never_names_this_computer():
    head = '#EXTM3U url-tvg="{}"\n#EXTINF:-1,One\nhttp://tv.example/1.ts\n'
    for server, guide, kept in (
            ("http://tv.example/list.m3u", "https://guide.example/epg.xml.gz", True),
            ("http://tv.example/list.m3u", "http://127.0.0.1:631/printers", False),
            ("http://tv.example/list.m3u", "http://169.254.169.254/latest/meta-data/", False),
            ("http://tv.example/list.m3u", "file:///etc/passwd", False),
            ("http://tv.example/list.m3u", "http://192.168.1.10/epg.xml", False),
            ("http://192.168.1.10/list.m3u", "http://127.0.0.1:34400/xmltv.xml", True),
            ("/home/u/list.m3u", "/home/u/guide.xml", True)):
        c = M3UClient(server)
        c._parse(head.format(guide))
        assert c.epg_url == (guide if kept else ""), (server, guide)
    c = M3UClient("http://tv.example/list.m3u")
    c.home_ok = True                    # the person let this list in
    c._parse(head.format("http://192.168.1.10/epg.xml"))
    assert c.epg_url == "http://192.168.1.10/epg.xml"
