"""A cast's archive and rewind buffer in pieces, read here into ONE converter
run (providers/cast_feed.py): the TV is handed one growing playlist, a
piece's end is not the stream's end, every piece starts at a keyframe, and
the buffer's loopback address stays on this computer. ffmpeg and the
readers are stand-ins (Python scripts); the command lines themselves were
checked against a real ffmpeg with generated transport streams."""
from __future__ import annotations

import sys
import time
import urllib.request

import pytest

from dopeiptv.providers import airplay_bridge as ab
from dopeiptv.providers import cast_bridge as cb
from dopeiptv.providers import cast_feed

# One piece: a line of its own on stdout, and how far it got on stderr, as
# ffmpeg's -progress says it.
_READER = r"""
import sys
sys.stdout.buffer.write(("PIECE %s\n" % sys.argv[1]).encode())
sys.stdout.flush()
sys.stderr.write("frame=1\nout_time_us=%d\nprogress=end\n" % int(float(sys.argv[2]) * 1e6))
"""

# The converter: a segment for every piece that arrives on stdin, one
# playlist for all of them, its end once stdin closes.
_CONVERTER = r"""
import os, sys
folder = sys.argv[1] if len(sys.argv) > 1 else "."
ext = sys.argv[2] if len(sys.argv) > 2 else "m4s"
segs = []
def write(end=False):
    body = "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n"
    body += "".join("#EXTINF:30,\n%s\n" % s for s in segs)
    if end:
        body += "#EXT-X-ENDLIST\n"
    tmp = os.path.join(folder, "index.m3u8.tmp")
    open(tmp, "w").write(body)
    os.replace(tmp, os.path.join(folder, "index.m3u8"))
for line in sys.stdin.buffer:
    name = "s%05d.%s" % (len(segs), ext)
    open(os.path.join(folder, name), "wb").write(line)
    segs.append(name)
    write()
write(end=True)
"""


@pytest.fixture
def readers(monkeypatch):
    calls = []

    def args(exe, url, start=0.0, ts_offset=0.0, net=None):
        calls.append({"url": url, "start": start, "ts_offset": ts_offset,
                      "net": net})
        return [sys.executable, "-c", _READER, url, "30"]
    monkeypatch.setattr(cast_feed, "reader_args", args)
    return calls


def _archive(n: int = 2, start: float = 1000.0):
    """n stretches of the archive, then no more."""
    asked: list[float] = []

    def url_at(moment):
        asked.append(moment)
        if len(asked) > n:
            raise EOFError("the end of the test")
        return (f"http://p/timeshift/u/pw/60/2026-11-15:20-0{len(asked)}/1.ts",
                12.0 if len(asked) == 1 else 0.0, moment)
    feed = cast_feed.Feed(url_at, start, kind="archive", clock=lambda: 10**7)
    return feed, asked


def _wait(cond, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def _get(url):
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.read()


def test_every_piece_starts_at_a_keyframe_and_carries_the_clock_on():
    args = cast_feed.reader_args("ffmpeg", "http://p/timeshift/1.ts", 12.5,
                                 600.25, ["-user_agent", "X"])
    i = args.index("-i")
    # Seeking before the input; network options before it too.
    assert args[args.index("-ss") + 1] == "12.500" and args.index("-ss") < i
    assert args.index("-user_agent") < i
    # Timestamps made where the panel gave none, and broken packets dropped
    # before they reach the converter.
    assert args[args.index("-fflags") + 1] == "+genpts+discardcorrupt"
    # A stream copy (which drops what precedes the first keyframe - no
    # -copyinkf), SPS/PPS in front of every keyframe, the clock carried on.
    assert "-copyinkf" not in args and args[args.index("-c") + 1] == "copy"
    assert args[args.index("-bsf:v") + 1] == "dump_extra=freq=keyframe"
    assert args[args.index("-output_ts_offset") + 1] == "600.250"
    assert args[-3:] == ["-f", "mpegts", "pipe:1"]
    assert args[args.index("-progress") + 1] == "pipe:2"
    first = cast_feed.reader_args("ffmpeg", "http://p/1.ts")
    assert "-ss" not in first and "-output_ts_offset" not in first
    assert cast_feed.progress_seconds("out_time_us=16692000") == 16.692
    assert cast_feed.progress_seconds("out_time=00:01:02.500000") == 62.5
    assert cast_feed.progress_seconds("progress=end") is None


def test_the_converter_reads_the_pieces_from_its_stdin():
    """Both converters read a transport stream on stdin and keep every
    segment of one EVENT playlist - the TV can go back within it."""
    p = ab.Plan(False, cast_feed.INPUT, live=True)
    a = ab.ffmpeg_args("ffmpeg", p, start=99.0, net=["-user_agent", "X"],
                       feed=True)
    assert a[a.index("-i") + 1] == "pipe:0" and "-nostdin" not in a
    assert a[a.index("-f") + 1] == "mpegts" and a.index("-f") < a.index("-i")
    assert "-ss" not in a and "-user_agent" not in a     # the readers' part
    assert a[a.index("-hls_playlist_type") + 1] == "event"
    assert a[a.index("-hls_list_size") + 1] == "0"
    assert "delete_segments" not in " ".join(a)
    c = cb.feed_args("ffmpeg", True, "/tmp/x")
    assert c[c.index("-i") + 1] == "pipe:0"
    assert c[c.index("-f") + 1] == "mpegts" and c.index("-f") < c.index("-i")
    assert c[c.index("-hls_playlist_type") + 1] == "event"
    assert c[-1].endswith(cb.FEED_PLAYLIST) and "-ss" not in c
    # Played from the moment asked for, not from the newest segment.
    body = b"#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXTINF:6,\ns0.m4s\n"
    served = ab.serve_playlist(body, from_start=True)
    assert served.splitlines()[1] == b"#EXT-X-START:TIME-OFFSET=0,PRECISE=YES"
    assert ab.serve_playlist(body) == body
    assert cast_feed.written(body + b"#EXTINF:5.5,\ns1.m4s\n") == 11.5


def test_archive_pieces_go_on_one_airplay_playlist_without_a_restart(
        monkeypatch, readers):
    seen = {}

    def args(exe, p, start=0.0, net=None, feed=False):
        seen.update(feed=feed)
        return [sys.executable, "-c", _CONVERTER]
    monkeypatch.setattr(ab, "ffmpeg_args", args)
    feed, asked = _archive(2)
    b = ab.AirPlayBridge()
    b.exe = sys.executable
    url = b.start(ab.Plan(False, cast_feed.INPUT, live=True), feed=feed)
    proc = b._proc
    try:
        assert seen["feed"] is True and b.feed is feed
        assert _wait(lambda: b"ENDLIST" in b._playlist()), b._playlist()
        # One converter for both: no restart where the first stretch ended.
        assert b._proc is proc and feed.pieces == 2
        body = _get(url)
        assert ab.segments_in(body) == 2 and b"#EXT-X-START" in body
        assert b.written() == 60.0
        base = url.rsplit("/", 1)[0]
        assert _get(base + "/s00001.m4s").startswith(b"PIECE http://p/timeshift")
        # The first from where it was asked; the next from where the first
        # ended, its clock carried on past the first's.
        assert readers[0]["start"] == 12.0 and readers[0]["ts_offset"] == 0.0
        assert asked[1] == 1000.0 + 30.0
        assert readers[1]["ts_offset"] == 30.0 + cast_feed.JOIN_GAP_S
    finally:
        b.stop()
    assert feed.closed                      # the bridge was done with it


def test_a_bridge_closes_a_feed_another_stream_replaced(monkeypatch, readers):
    monkeypatch.setattr(ab, "ffmpeg_args", lambda *a, **k: [
        sys.executable, "-c", "import time; time.sleep(30)"])
    old, _ = _archive(1)
    b = ab.AirPlayBridge()
    b.exe = sys.executable
    p = ab.Plan(False, cast_feed.INPUT, live=True)
    try:
        b.start(p, feed=old)
        b.start(p, feed=old)                # a second try: the same pieces
        assert not old.closed and b.feed is old
        new, _ = _archive(1)
        b.start(p, feed=new)
        assert old.closed and not new.closed and b.feed is new
    finally:
        b.stop()
    assert new.closed


def test_a_chromecast_gets_the_pieces_as_one_playlist(monkeypatch, readers):
    folders = []

    def args(exe, copy_video, folder, audio=0, quality="original"):
        folders.append(folder)
        return [sys.executable, "-c", _CONVERTER, folder, "ts"]
    monkeypatch.setattr(cb, "feed_args", args)
    feed, asked = _archive(3)
    b = cb.CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/timeshift/u/pw/60/x/1.ts", feed=feed,
                  subs=2, sub_codec="subrip", start_at=50.0)
    try:
        assert url.endswith("/" + cb.FEED_PLAYLIST) and b.hls
        assert b.subs is None and b.start_at == 0.0     # nothing rides along
        body = _get(url)                    # the receiver asks: ffmpeg starts
        assert len(folders) == 1 and len(b._procs) == 1
        assert _wait(lambda: feed.pieces == 3 and not feed.running())
        body = _get(url)
        assert body.count(b".ts") == 3 and b"#EXT-X-START" in body
        assert b.written() == 90.0 and len(b._procs) == 1
        assert [r["ts_offset"] for r in readers] == [
            0.0, 30.0 + cast_feed.JOIN_GAP_S, 60.0 + 2 * cast_feed.JOIN_GAP_S]
    finally:
        b.stop()
    assert feed.closed


def test_a_piece_that_gives_nothing_is_asked_again(monkeypatch):
    """The panel refusing one stretch (still counting the last connection)
    is a retry from the same moment, not the end of the cast."""
    calls = []

    def args(exe, url, start=0.0, ts_offset=0.0, net=None):
        calls.append(url)
        secs = "0" if len(calls) == 1 else "30"
        return [sys.executable, "-c", _READER, url, secs]
    monkeypatch.setattr(cast_feed, "reader_args", args)
    monkeypatch.setattr(cast_feed, "RETRIES", 2)
    feed, asked = _archive(3)
    sink = _Sink()
    feed.attach(sink, sys.executable)
    assert _wait(lambda: sink.closed, 20.0)
    assert feed.pieces == 2 and len(calls) == 3
    assert asked[:3] == [1000.0, 1000.0, 1030.0]   # the same moment again
    assert sink.data.count(b"PIECE") == 3
    feed.close()


class _Sink:
    def __init__(self):
        self.data, self.closed = b"", False

    def write(self, b):
        self.data += b

    def close(self):
        self.closed = True


def test_closing_a_feed_lets_the_rewind_buffer_go_once():
    gone = []
    feed = cast_feed.Feed(lambda m: ("http://127.0.0.1:9/t/s/1.ts", 0.0, m - 5),
                          100.0, lag=0.0, kind="rewind buffer",
                          on_close=lambda: gone.append(1))
    assert feed.first_wall == 95.0 and cast_feed.is_loopback(feed.first_url)
    feed.close()
    feed.close()
    assert gone == [1]
    with pytest.raises(RuntimeError):
        feed.attach(_Sink(), sys.executable)
