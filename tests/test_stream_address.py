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
