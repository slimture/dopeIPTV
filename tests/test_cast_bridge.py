"""The local bridge that makes a refused channel castable.

Some channels are ordinary H.264 video with Dolby Digital Plus audio: mpv
plays them without blinking, and a Chromecast that is not an Ultra or a
Google TV has no E-AC-3 decoder at all and answers IDLE/ERROR without saying
why. No address and no MIME type changes that - the only way such a channel
reaches the TV is to hand it something it can decode.

These checks run the real HTTP server with a stand-in for ffmpeg, so the
streaming path is exercised without needing a provider or a TV.
"""
import os
import sys
import time
import urllib.request

import pytest

from dopeiptv.core.log import log as _blog
from dopeiptv.providers.cast_bridge import CastBridge, ffmpeg_args, lan_address


def test_the_video_is_copied_when_only_the_audio_is_the_problem():
    """Re-encoding 1080p live would cook the machine, and it is not what the
    receiver choked on."""
    args = ffmpeg_args("ffmpeg", "http://p/9851.m3u8", copy_video=True)
    assert "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "aac"
    assert "libx264" not in args
    assert args[-1] == "pipe:1"


def test_video_is_re_encoded_only_when_it_has_to_be():
    args = ffmpeg_args("ffmpeg", "http://p/x.m3u8", copy_video=False)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert "-preset" in args


def test_hevc_forces_a_video_re_encode():
    b = CastBridge()
    b.exe = sys.executable
    try:
        b.start("http://p/x.m3u8", ["hevc", "eac3"])
        assert b.copy_video is False
        b.start("http://p/x.m3u8", ["h264", "eac3"])
        assert b.copy_video is True
        # An unknown codec is assumed fine: guessing the other way means
        # re-encoding video for no reason, which is the expensive mistake.
        b.start("http://p/x.m3u8", ["h264", "something-new"])
        assert b.copy_video is True
    finally:
        b.stop()


def test_the_chosen_audio_track_is_the_one_mapped():
    args = ffmpeg_args("ffmpeg", "http://p/x.mkv", copy_video=True, audio=2)
    assert "0:a:2" in args
    assert "0:v:0" in args


def test_a_bitmap_subtitle_is_overlaid_instead():
    """DVB and PGS subtitles are pictures, and the subtitles filter cannot
    draw them - they are composited over the video."""
    args = ffmpeg_args("ffmpeg", "http://p/x.ts", copy_video=True,
                       audio=0, subs=0, sub_codec="dvb_subtitle")
    assert "-filter_complex" in args
    assert args[args.index("-filter_complex") + 1] == \
        "[0:v:0][0:s:0]overlay[v]"
    assert "[v]" in args


def test_a_local_file_is_linked_under_a_name_nothing_can_misread(tmp_path):
    """A colon can be escaped through a filtergraph; an apostrophe cannot, not
    in every ffmpeg version - and "Ocean's Eleven 2026-07-28.ts" is an
    ordinary recording. Local files get a plain name instead of a quoting
    argument."""
    awkward = tmp_path / "Ocean's: Eleven.mkv"
    awkward.write_bytes(b"not really a film")
    b = CastBridge()
    b.exe = sys.executable
    try:
        b.start(str(awkward), ["h264"])
        assert b.source is not None
        assert "'" not in b.source and ":" not in b.source, b.source
        assert b.source.endswith(".mkv")
        assert os.path.realpath(b.source) == os.path.realpath(awkward)
    finally:
        tmp = b._tmp
        b.stop()
        assert tmp and not os.path.exists(tmp), "the link is cleaned up"


def test_a_url_is_never_linked():
    b = CastBridge()
    b.exe = sys.executable
    try:
        b.start("http://p/x.m3u8", ["h264"])
        assert b.source == "http://p/x.m3u8"
        assert b._tmp is None
    finally:
        b.stop()


def test_an_old_receiver_gets_a_picture_it_can_keep_up_with():
    """An FHD channel is two problems for an older Chromecast at once:
    nothing on the Cast platform deinterlaces, and 1080 lines are more than
    the decoder keeps up with. Both are answered on this side.

    The frame rate is NOT one of the problems, and touching it made the
    picture worse for nothing: HD channels play perfectly on a
    first-generation dongle and Swedish HD is 720p50, so fifty frames a
    second is plainly within reach.
    """
    args = ffmpeg_args("ffmpeg", "http://p/x.m3u8", copy_video=True,
                       quality="older")
    vf = args[args.index("-vf") + 1]
    assert vf == "yadif=deint=1,scale=-2:720", vf
    assert "-r" not in args, args
    assert args[args.index("-c:v") + 1] != "copy", "adapting means re-encoding"


def test_the_original_picture_is_never_touched():
    args = ffmpeg_args("ffmpeg", "http://p/x.m3u8", copy_video=True,
                       quality="original")
    assert "-vf" not in args and "-r" not in args
    assert args[args.index("-c:v") + 1] == "copy"


def test_the_address_is_one_the_chromecast_can_reach():
    addr = lan_address()
    assert addr and not addr.startswith("0."), addr


def test_the_stream_is_served_over_http():
    """End to end through the real server, with a stand-in for ffmpeg: the
    receiver connects, ffmpeg starts, and the bytes come out the other side."""
    b = CastBridge()
    # A "player" that writes a known payload and exits, so the response can
    # be compared byte for byte.
    b.exe = sys.executable
    url = b.start("http://p/x.m3u8", ["h264", "eac3"])
    try:
        import dopeiptv.providers.cast_bridge as cb
        # Big enough to be a stream the bridge will commit to. Anything
        # shorter is read as a run that died before it started, and is
        # deliberately never handed to the receiver.
        payload = b"MOOV" * 512_000

        def fake_args(exe, source, copy_video, *a, **k):
            # Written by the child, not passed on its command line: two
            # megabytes of argument is past what a process may be given.
            return [exe, "-c", "import sys;"
                    "sys.stdout.buffer.write(b'MOOV' * 512_000)"]

        real, cb.ffmpeg_args = cb.ffmpeg_args, fake_args
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                assert r.headers["Content-Type"] == "video/mp4"
                assert r.read() == payload
        finally:
            cb.ffmpeg_args = real
    finally:
        b.stop()


def test_a_wrong_address_gets_nothing():
    """The path carries a random token: nothing else on the LAN is served."""
    b = CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/x.m3u8", ["h264"])
    try:
        base = url.rsplit("/", 2)[0]
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/nope/stream.mp4", timeout=10)
        assert e.value.code == 404
    finally:
        b.stop()


def test_stopping_takes_the_server_down():
    b = CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/x.m3u8", ["h264"])
    b.stop()
    assert b.path is None and b.source is None
    with pytest.raises(OSError):        # URLError is an OSError
        urllib.request.urlopen(url, timeout=5)


def test_joining_a_broadcast_mid_stream_is_one_line_not_hundreds():
    """A transport stream can be joined at any byte, so everything before the
    next keyframe is undecodable and ffmpeg says so about every frame. It does
    not say it in one voice - "non-existing PPS 0", "no frame!" and its own
    "Last message repeated" take turns - so collapsing equal consecutive lines
    caught none of it and the log filled with hundreds of them.
    """
    import io
    import logging

    class Proc:
        stderr = io.BytesIO(
            b"[h264] non-existing PPS 0 referenced\n"
            b"Last message repeated 1 times\n"
            b"[h264] no frame!\n" * 1 +
            (b"[h264] non-existing PPS 0 referenced\n"
             b"Last message repeated 1 times\n"
             b"[h264] no frame!\n") * 60 +
            b"Output #0, mp4, to 'pipe:1':\n")

    lines = []

    class Grab(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    from dopeiptv.core.log import log
    h = Grab()
    level = log.level
    log.addHandler(h)
    log.setLevel(logging.INFO)
    try:
        CastBridge._drain_errors(Proc())
    finally:
        log.removeHandler(h)
        log.setLevel(level)

    # Said once in ffmpeg's own words, counted after that, and the line that
    # actually matters is still there.
    assert len(lines) == 3, lines
    assert "non-existing PPS 0 referenced" in lines[0]
    assert "joined mid-stream" in lines[0]
    assert "182 more" in lines[1], lines[1]
    assert "Output #0" in lines[2]


def test_a_failure_that_cannot_change_is_not_tried_twice_more():
    """The retry exists for a provider still counting a connection we closed a
    moment ago, which frees up within seconds. A filter this ffmpeg does not
    have never will - and the old code spent twenty seconds proving it three
    times over while the TV sat waiting."""
    import io
    import logging

    class Proc:
        stderr = io.BytesIO(
            b"[AVFilterGraph @ 0x1] No such filter: 'subtitles'\n"
            b"Error opening output file pipe:1.\n")

    b = CastBridge()
    b.exe = sys.executable
    b.start("http://p/x.m3u8", ["h264"])
    try:
        level = _blog.level
        _blog.setLevel(logging.CRITICAL)
        try:
            CastBridge._drain_errors(Proc(), b)
        finally:
            _blog.setLevel(level)
        assert "No such filter" in b.fatal
        assert b.spawn() is None, "no second attempt at an impossible run"
        # A fresh cast is allowed to fail on its own terms.
        b.start("http://p/y.m3u8", ["h264"])
        assert b.fatal == ""
    finally:
        b.stop()


def test_a_local_recording_takes_none_of_the_http_options():
    """ffmpeg exits outright on an HTTP option it was handed for a file:
    "Option user_agent not found", before it opens anything - which is every
    recording on disk failing to cast.

    Reconnecting at EOF is not among the options at all. These panels cut an
    archive stream short, and a reconnect on a stream nobody can seek starts
    it again from the beginning rather than continuing - the picture went
    back to where it had started, over and over, which is worse than
    stopping.
    """
    from dopeiptv.providers.cast_bridge import _input_options

    live = _input_options("http://p/live/u/pw/9851.ts")
    assert "-reconnect" in live and "-user_agent" in live
    assert "-reconnect_at_eof" not in live
    assert _input_options("/home/me/Recordings/x.mkv") == []

    # And none at all for an archive window. The panel closing the stream
    # at its write head is the END of the stretch - the next one is asked
    # for from that moment. A reconnect re-requests the same window, which
    # on a panel that ignores Range starts it over from the beginning:
    # television replayed at random, mid-programme.
    ts = _input_options(
        "http://p/timeshift/u/pw/300/2026-07-28:15-55/9851.ts?token=x")
    assert "-user_agent" in ts and "-reconnect" not in ts


def test_a_stream_that_dies_at_once_never_reaches_the_receiver():
    """These panels refuse a stream while they are still counting a connection
    that has just been dropped - and on the pause-and-resume path one always
    is, because the receiver only lets go of the live stream when the archive
    replaces it. ffmpeg then reads a fraction of a second and stops.

    Those bytes used to go straight to the TV, which played them and reported
    IDLE/FINISHED: a black screen from a cast asked a few seconds too early.
    Nothing is handed over until there is enough of it to be sure.
    """
    import dopeiptv.providers.cast_bridge as cb

    runs = []

    def fake_args(exe, source, copy_video, *a, **k):
        runs.append(source)
        # A short burst the first time, a real stream after that.
        size = 20_000 if len(runs) == 1 else 2_000_000
        return [exe, "-c",
                "import sys;sys.stdout.buffer.write(b'x' * %d)" % size]

    b = CastBridge()
    b.exe = sys.executable
    b.start("http://p/timeshift/x.ts", ["h264"])
    real_args, real_sleep = cb.ffmpeg_args, cb.time.sleep
    cb.ffmpeg_args = fake_args
    cb.time.sleep = lambda s: None            # no waiting in a test
    try:
        proc, spool = b.first_frames()
        assert proc is not None
        assert len(runs) == 2, "the short run was thrown away and retried"
        # The opening is on disk now: ffmpeg is drained at full speed into a
        # recording so it never waits for the television, and the receiver is
        # served from there.
        rdr = spool.reader()
        head = b"".join(iter(lambda: rdr.read(65536), b""))
        assert len(head) >= b.OPENING
        assert set(head) == {ord("x")}, "not a byte of the failed run"
        b.kill(proc)

        # A stream that never starts is refused rather than half-served.
        runs.clear()
        cb.ffmpeg_args = lambda *a, **k: [
            sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x')"]
        proc, spool = b.first_frames()
        assert proc is None and spool is None
    finally:
        cb.ffmpeg_args, cb.time.sleep = real_args, real_sleep
        b.stop()


def test_a_request_never_spawns_into_a_stream_that_replaced_it():
    """A request that is still retrying belongs to the run it began in.

    Changing a track or moving a film starts the bridge again, and the old
    request's retry then spawned ffmpeg into the NEW stream - two of them on
    one account, taking a connection each, cutting each other off, and
    neither arriving. The log showed it plainly: two "starting ffmpeg" in a
    row and three HTTP readers interleaved.
    """
    import dopeiptv.providers.cast_bridge as cb

    runs = []

    def fake_args(exe, source, copy_video, *a, **k):
        runs.append(source)
        return [exe, "-c", "import sys;sys.stdout.buffer.write(b'x' * 10)"]

    b = CastBridge()
    b.exe = sys.executable
    b.start("http://p/film.mkv", ["h264"])

    real_args, real_sleep = cb.ffmpeg_args, cb.time.sleep
    cb.ffmpeg_args = fake_args
    # The wait between attempts is where the other stream starts.
    cb.time.sleep = lambda s: b.start("http://p/other.mkv", ["h264"])
    try:
        proc, head = b.first_frames()
        assert proc is None and head == b""
        # One attempt for the run it belongs to, and nothing for the new one.
        assert runs == ["http://p/film.mkv"], runs
    finally:
        cb.ffmpeg_args, cb.time.sleep = real_args, real_sleep
        b.stop()


def test_a_slow_receiver_never_stalls_the_panel(tmp_path):
    """The failure this whole spool exists for, reproduced end to end.

    A television takes its stream at the speed it plays it. Reading ffmpeg
    directly passed that speed back up the chain: ffmpeg blocked writing,
    stopped reading the panel, and the panel - which has no patience for a
    connection nobody is reading - closed it a few seconds in:

        Stream ends prematurely at 29960221, should be 69423104
        Error during demuxing: Input/output error

    Here a stand-in for ffmpeg refuses to be throttled: it writes its whole
    stream promptly and reports failure if made to wait. The receiver reads
    slowly. Both must still get everything.
    """
    import dopeiptv.providers.cast_bridge as cb

    marker = tmp_path / "was-throttled"
    # Writes 1 MB in bursts and fails outright if any single write blocks for
    # long - which is what being read at playback speed looks like.
    child = (
        "import sys,time;"
        "w=sys.stdout.buffer;"
        "[ (t0:=time.monotonic(), w.write(b'z'*65536), w.flush(),"
        "   open(%r,'w').write('yes') if time.monotonic()-t0 > 1.5 else None)"
        "  for _ in range(16) ]" % str(marker))

    b = CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/timeshift/x.ts", ["h264"])
    real = cb.ffmpeg_args
    cb.ffmpeg_args = lambda *a, **k: [sys.executable, "-c", child]
    try:
        got = bytearray()
        with urllib.request.urlopen(url, timeout=30) as r:
            while True:                       # a receiver, reading slowly
                part = r.read(16384)
                if not part:
                    break
                got += part
                time.sleep(0.02)
        assert len(got) == 16 * 65536, len(got)
        assert set(got) == {ord("z")}
        assert not marker.exists(), "ffmpeg was made to wait for the TV"
    finally:
        cb.ffmpeg_args = real
        b.stop()


def test_a_reconnecting_receiver_does_not_start_a_second_ffmpeg():
    """A Chromecast opens, probes and reopens connections as a matter of
    course. Spawning ffmpeg for each one meant a fresh request to the panel
    and the stretch played from its beginning again - and on an account with
    one connection, the previous request still counted, so the new one was
    cut short too. One ffmpeg per run of the bridge; everyone reads the same
    spool."""
    import dopeiptv.providers.cast_bridge as cb

    runs = []

    def fake_args(exe, source, copy_video, *a, **k):
        runs.append(source)
        return [exe, "-c", "import sys,time;"
                "sys.stdout.buffer.write(b'y' * 200000);"
                "sys.stdout.flush();time.sleep(30)"]

    b = CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/timeshift/x.ts", ["h264"])
    real = cb.ffmpeg_args
    cb.ffmpeg_args = fake_args
    try:
        first = urllib.request.urlopen(url, timeout=30)
        assert first.read(4096)
        assert len(runs) == 1

        second = urllib.request.urlopen(url, timeout=30)
        assert second.read(4096)
        assert len(runs) == 1, "the second reader joined the running stream"

        # One reader leaving does not take the stream with it.
        first.close()
        time.sleep(0.5)
        assert second.read(4096)
    finally:
        cb.ffmpeg_args = real
        b.stop()


def test_a_paused_television_does_not_stop_the_recording():
    """This is what makes pausing a broadcast work at all.

    Every other way of holding a live cast asked the provider for the missing
    minutes afterwards, and the provider is exactly what cannot be relied on:
    it bursts, it cuts, it counts one connection. So do not ask it anything.
    The converter records into a spool as it goes; a pause is the television
    stopping reading, and play carries on at the very next frame - because
    the recording never stopped.
    """
    import dopeiptv.providers.cast_bridge as cb

    b = CastBridge()
    b.exe = sys.executable
    url = b.start("http://p/timeshift/x.ts", ["h264"])
    real = cb.ffmpeg_args
    cb.ffmpeg_args = lambda *a, **k: [
        sys.executable, "-c",
        "import sys,time\n"
        "for i in range(120):\n"
        "    sys.stdout.buffer.write(bytes([i % 251]) * 65536)\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.05)\n"]
    try:
        r = urllib.request.urlopen(url, timeout=30)
        spool = b._current[1]
        assert set(r.read(65536)) == {0}, "the first frame"
        was = spool.total
        # The television stops reading - a pause.
        time.sleep(1.5)
        # The recording carried on regardless, which is the whole point.
        assert spool.total > was + 65536 * 8, spool.total
        # And play picks up on the very next byte, not somewhere else.
        rest = r.read(65536)
        assert len(rest) == 65536
        assert set(rest) == {1}, "the frame straight after the paused one"
    finally:
        cb.ffmpeg_args = real
        b.stop()


def test_the_recording_costs_a_pause_not_an_evening(tmp_path):
    """What has to be kept is the stretch between the slowest reader and the
    write head - seconds of it while the television plays, and exactly the
    length of a pause while it does not. One growing file answered the wrong
    question: it grew for the whole sending, so a football match would have
    hit the limit around half time without anyone pausing at all.
    """
    from dopeiptv.providers.cast_bridge import _Spool

    sp = _Spool(str(tmp_path / "rec"), cap=10 * _Spool.PIECE)
    rdr = sp.reader()

    def keep() -> int:
        return sum(1 for n in os.listdir(tmp_path / "rec"))

    # An evening's watching: written and read, over and over.
    for _ in range(40):
        assert sp.write(b"x" * _Spool.PIECE)
        while rdr.read(1_000_000):
            pass
    assert sp.total == 40 * _Spool.PIECE, "everything was recorded"
    assert keep() <= 3, f"but only the live end is kept ({keep()} pieces)"

    # A pause: the television stops reading and the recording runs ahead.
    for _ in range(6):
        assert sp.write(b"y" * _Spool.PIECE)
    assert keep() >= 6, "a pause is what actually takes room"

    # Play again, and it comes back on the very next byte.
    assert set(rdr.read(1_000_000)) == {ord("y")}

    # A pause nobody ever ends stops at the cap rather than filling the disk.
    for _ in range(40):
        if not sp.write(b"z" * _Spool.PIECE):
            break
    else:
        raise AssertionError("the cap never stopped it")
    sp.close()


def test_a_pause_is_written_to_disk_and_never_left_behind(tmp_path,
                                                          monkeypatch):
    """Not the system temp folder: on many Linux systems /tmp is tmpfs, which
    is RAM, and a paused broadcast runs to gigabytes. Not the recordings
    folder either - nothing here is a recording anyone asked to keep.

    And nothing survives. It goes when the cast ends, and anything a crash
    left behind goes on the way in: four gigabytes of a fortnight-old pause
    is not a thing to discover.
    """
    import dopeiptv.providers.cast_bridge as cb

    cache = tmp_path / "cache" / "cast"
    monkeypatch.setattr(cb, "default_image_cache_dir", None, raising=False)
    monkeypatch.setattr(cb.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setitem(sys.modules, "dopeiptv.core.workers", None)

    # Something a previous run was killed in the middle of.
    stale = tmp_path / ".cache" / "dopeiptv" / "cast" / "cast-oldrun"
    stale.mkdir(parents=True)
    (stale / "piece00000").write_bytes(b"x" * 1000)

    folder = cb.cast_cache_dir()
    assert not stale.exists(), "a killed run's leftovers are swept on the way in"
    assert "cache" in folder.lower() or ".cache" in folder
    assert "recording" not in folder.lower()

    b = CastBridge()
    b.exe = sys.executable
    cb.ffmpeg_args_real = cb.ffmpeg_args
    cb.ffmpeg_args = lambda *a, **k: [
        sys.executable, "-c",
        "import sys,time;sys.stdout.buffer.write(b'q'*200000);"
        "sys.stdout.flush();time.sleep(20)"]
    try:
        url = b.start("http://p/timeshift/x.ts", ["h264"])
        with urllib.request.urlopen(url, timeout=30) as r:
            assert r.read(4096)
        run = b._tmp
        assert os.path.isdir(run)
        b.stop()
        assert not os.path.exists(run), "the recording goes with the cast"
    finally:
        cb.ffmpeg_args = cb.ffmpeg_args_real
        b.stop()
    _ = cache


def test_a_television_that_dropped_out_rejoins_the_recording(tmp_path):
    """Pause a minute, play, pause again - and nothing happened.

    A receiver that lets its connection go during a pause opens a new one on
    play, and every new reader started at the beginning of the recording.
    By then the beginning had been thrown away as watched, so it waited for a
    file that no longer existed, for ever. It has to pick up at the oldest
    piece still on disk - with the fragmented MP4's opening in front of it,
    and skipped forward to where a fragment actually starts.
    """
    from dopeiptv.providers.cast_bridge import _Spool

    sp = _Spool(str(tmp_path / "rec"), cap=100 * _Spool.PIECE)
    # An opening, then fragments - the shape ffmpeg's fragmented MP4 has.
    sp.write(b"\x00\x00\x00\x18ftypiso5" + b"MOOV" * 100)
    for i in range(6):
        sp.write(b"\x00\x00\x00\x10moof" + bytes([65 + i]) * (_Spool.PIECE - 8))
    assert sp.init.startswith(b"\x00\x00\x00\x18ftyp")
    assert b"moof" not in sp.init, "the opening stops at the first fragment"

    # Watch most of it, so the early pieces are thrown away.
    first = sp.reader()
    while first.read(1_000_000):
        pass
    sp.write(b"\x00\x00\x00\x10moof" + b"Z" * (_Spool.PIECE - 8))
    assert sp.first_kept() > 0, "the watched beginning is gone"

    # The television comes back. It must get the opening and then a fragment
    # boundary - not silence, and not the middle of a fragment.
    again = sp.reader()
    got = b""
    for _ in range(40):
        part = again.read(1_000_000)
        if not part:
            break
        got += part
    assert got.startswith(b"\x00\x00\x00\x18ftyp"), "the opening comes first"
    body = got[len(sp.init):]
    assert body[:8].endswith(b"moof"), "then a fragment, from its start"
    first.close()
    again.close()
    sp.close()


# ---------------------------------------------------------------------------
# The source spool: one connection to the provider, however many opens ffmpeg
# wants.
# ---------------------------------------------------------------------------

class _CountingProvider:
    """A provider that serves one file and counts how many times it is asked.

    The account under test allows a single connection. The point of the
    source spool is that this number stays at one no matter what the
    filtergraph does, so counting it is the whole test.
    """

    def __init__(self, payload: bytes, rate: int = 0):
        """*rate* trickles the body at that many bytes per tenth of a second.

        Zero delivers it instantly, which is what a test server does and a
        provider never does - and that difference hid a real failure: a wait
        for the far end of the file costs nothing when the far end arrives
        in the same millisecond as the near one.
        """
        import http.server
        import socketserver
        import threading as _th
        self.payload = payload
        self.rate = rate
        self.opens = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):                    # noqa: N802 (stdlib hook)
                rng = self.headers.get("Range") or ""
                outer.opens.append(rng or "-")
                at = 0
                if rng.startswith("bytes="):
                    at = int(rng.split("=", 1)[1].split("-", 1)[0] or 0)
                body = outer.payload[at:]
                self.send_response(206 if at else 200)
                self.send_header("Content-Type", "video/x-matroska")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                try:
                    if not outer.rate:
                        self.wfile.write(body)
                    else:
                        while body:
                            self.wfile.write(body[:outer.rate])
                            body = body[outer.rate:]
                            time.sleep(0.1)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.srv = socketserver.TCPServer(("127.0.0.1", 0), H)
        self.srv.daemon_threads = True
        _th.Thread(target=self.srv.serve_forever,
                   kwargs={"poll_interval": 0.05}, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.srv.server_address[1]}/film.mkv"

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()



# ---------------------------------------------------------------------------
# A text subtitle travels beside the picture, as HLS with a WebVTT rendition.
# ---------------------------------------------------------------------------

def test_a_text_subtitle_switches_the_delivery_to_hls():
    """It decides the whole shape of the cast, so it is decided in one place.

    A picture-based subtitle is still drawn into the frames with overlay -
    there is no WebVTT to make of a bitmap - and everything else is the
    single long response it always was.
    """
    from dopeiptv.providers.cast_bridge import hls_args
    for subs, codec, want in ((0, "subrip", True), (0, "ass", True),
                              (0, "dvb_subtitle", False),
                              (0, "hdmv_pgs_subtitle", False),
                              (None, "", False)):
        b = CastBridge()
        b.exe = "/bin/true"
        try:
            url = b.start("http://p/movie/u/pw/5.mkv", subs=subs,
                          sub_codec=codec)
            assert b.hls is want, (subs, codec)
            assert url.endswith("master.m3u8" if want else "stream.mp4")
            if want:
                assert b.hls_dir and os.path.isdir(b.hls_dir)
        finally:
            b.stop()

    args = hls_args("ffmpeg", "http://p/f.mkv", copy_video=True,
                    folder="/tmp/x", audio=1, subs=3)
    # The subtitle is mapped and converted, not drawn: no filter, and so no
    # second open of the source and no libass anywhere.
    assert "-map" in args and "0:s:3" in args
    assert args[args.index("-c:s") + 1] == "webvtt"
    assert not any("subtitles=" in a for a in args)
    # And the picture is left alone. A subtitle used to force a re-encode of
    # every frame; beside the picture it costs nothing.
    assert args[args.index("-c:v") + 1] == "copy"
    assert "-var_stream_map" in args


def test_a_broadcast_rolls_its_window_and_a_film_keeps_everything():
    """A film keeps every segment, which is what lets the television's own
    remote scrub it - and what makes a pause free, because the segments go
    on being written while the receiver sits still. A channel would fill the
    disk doing that, so it deletes behind itself."""
    from dopeiptv.providers.cast_bridge import hls_args
    film = hls_args("ffmpeg", "http://p/movie/u/pw/5.mkv", True, "/tmp/x",
                    subs=0, live=False)
    assert film[film.index("-hls_list_size") + 1] == "0"
    assert "event" in film
    chan = hls_args("ffmpeg", "http://p/live/u/pw/9851.ts", True, "/tmp/x",
                    subs=0, live=True)
    assert chan[chan.index("-hls_list_size") + 1] == "6"
    assert "delete_segments" in chan[chan.index("-hls_flags") + 1]
    # Half-written playlists are the other way this dies: a receiver that
    # asks at the wrong moment gets a parse error and nothing says why.
    for args in (film, chan):
        assert "temp_file" in args[args.index("-hls_flags") + 1]


def test_the_hls_files_are_served_with_the_types_the_receiver_needs():
    """A Cast receiver fetches the WebVTT rendition cross-origin and drops it
    silently without the CORS header, which looks exactly like a stream that
    has no subtitles at all. And a playlist served as video/mp4 plays
    nothing."""
    b = CastBridge()
    b.exe = "/bin/true"
    try:
        url = b.start("http://p/movie/u/pw/5.mkv", subs=0, sub_codec="subrip")
        folder = b.hls_dir
        open(os.path.join(folder, "master.m3u8"), "w").write("#EXTM3U\n")
        open(os.path.join(folder, "v0.ts"), "wb").write(b"\x47" * 188)
        open(os.path.join(folder, "s0.vtt"), "w").write("WEBVTT\n")
        base = url.rsplit("/", 1)[0]
        for name, ctype in (("master.m3u8", "application/vnd.apple.mpegurl"),
                            ("v0.ts", "video/mp2t"),
                            ("s0.vtt", "text/vtt")):
            with urllib.request.urlopen(f"{base}/{name}", timeout=10) as r:
                assert r.headers["Content-Type"] == ctype, name
                assert r.headers["Access-Control-Allow-Origin"] == "*", name
                assert r.read()
        # Only a plain name inside our own folder is ever answered.
        for bad in ("../secret", "sub/dir.ts", ".hidden"):
            try:
                urllib.request.urlopen(f"{base}/{bad}", timeout=5)
                raise AssertionError(f"{bad} was served")
            except urllib.error.HTTPError as e:
                assert e.code == 404, (bad, e.code)
    finally:
        b.stop()


@pytest.mark.filterwarnings("ignore")
def test_a_real_cast_with_a_subtitle_needs_one_connection_and_starts_at_once():
    """The claim, end to end, with a real ffmpeg.

    Burning a text subtitle in used to cost three opens of the source - the
    filter takes a filename, not a stream - and the panel hung up on the
    main one. It also read 100% of the file before the first frame, because
    the filter builds the whole track before drawing a line.

    Beside the picture there is nothing to preload and nothing to open
    twice: measured against a source fed at a thirtieth of real speed, the
    first WebVTT segment was written after 0.1 s and 3% of the file.
    """
    import shutil as _sh
    import subprocess

    exe = _sh.which("ffmpeg")
    if not exe:
        pytest.skip("no ffmpeg")

    srt = os.path.join(os.path.dirname(__file__), "_hls_s.srt")
    open(srt, "w").write("1\n00:00:00,500 --> 00:00:30,000\nHEJ\n")
    mkv = os.path.join(os.path.dirname(__file__), "_hls_film.mkv")
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=30",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=30",
         "-i", srt, "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-c:s", "copy", "-y", mkv], check=True)

    prov = _CountingProvider(open(mkv, "rb").read())
    b = CastBridge()
    try:
        url = b.start(prov.url, subs=0, sub_codec="subrip")
        assert b.hls is True
        with urllib.request.urlopen(url, timeout=60) as r:
            master = r.read().decode()
        assert "#EXTM3U" in master
        # The subtitle is announced as its own rendition - which is what the
        # receiver renders, rather than anything drawn into the picture.
        assert "TYPE=SUBTITLES" in master, master
        # A media playlist and a subtitle playlist, both fetchable.
        base = url.rsplit("/", 1)[0]
        names = [ln.strip() for ln in master.splitlines()
                 if ln.strip() and not ln.startswith("#")]
        assert names, master
        with urllib.request.urlopen(f"{base}/{names[0]}", timeout=30) as r:
            media = r.read().decode()
        assert "#EXTINF" in media, media
        assert len(prov.opens) == 1, (
            f"the provider was opened {len(prov.opens)} times: {prov.opens}")
    finally:
        b.stop()
        prov.close()
        for f in (srt, mkv):
            try:
                os.remove(f)
            except OSError:
                pass


def test_the_playlist_asks_for_the_subtitle_to_be_shown():
    """ffmpeg writes the rendition as DEFAULT=YES and stops there, and a
    receiver reads DEFAULT as "use this one IF subtitles are on at all" -
    to which its own answer is usually no. So a subtitle chosen in the
    dialog arrived listed and switched off, and the log said nothing,
    because from the sender's side everything had gone perfectly."""
    from dopeiptv.providers.cast_bridge import _autoselect_subtitles
    master = (b"#EXTM3U\n"
              b'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="s",'
              b'DEFAULT=YES,URI="stream_0_vtt.m3u8"\n'
              b'#EXT-X-STREAM-INF:BANDWIDTH=1,SUBTITLES="subs"\n'
              b"stream_0.m3u8\n")
    out = _autoselect_subtitles(master)
    assert b"AUTOSELECT=YES" in out
    assert b"FORCED=NO" in out
    # Nothing else is touched - a playlist is parsed strictly and an extra
    # word on the wrong line loses the stream, not just the subtitle.
    assert out.count(b"\n") == master.count(b"\n")
    assert b"stream_0.m3u8\n" in out
    assert b"AUTOSELECT" not in out.split(b"\n")[3]
    # And it is not doubled when it is served again.
    assert _autoselect_subtitles(out) == out


def test_the_picture_and_the_subtitle_share_a_clock():
    """They did not, and the subtitles ran a second and a third early.

    ffmpeg preloads a transport stream's clock by 1.4 seconds by default,
    and the WebVTT rendition carries no X-TIMESTAMP-MAP to say so - so the
    picture began at 1.421333 s while the subtitles began at 0.043 s.
    Measured on both, with ffprobe, before and after:

        before   video 1.421333   subtitle 0.043
        after    video 0.042333   subtitle 0.043
    """
    from dopeiptv.providers.cast_bridge import hls_args
    args = hls_args("ffmpeg", "http://p/f.mkv", True, "/tmp/x", subs=0)
    assert args[args.index("-muxpreload") + 1] == "0"
    assert args[args.index("-muxdelay") + 1] == "0"
    # Before the muxer, not after: they are output options for the format,
    # and after the output filename they are not options at all.
    assert args.index("-muxpreload") > args.index("-f")
    assert args.index("-muxpreload") < len(args) - 1


@pytest.mark.filterwarnings("ignore")
def test_the_subtitle_really_lands_on_the_picture(tmp_path):
    """Against a real ffmpeg, because a timestamp is not a thing that can be
    read off a command line - the offset above was invisible there and
    obvious the moment anything measured the two streams."""
    import shutil as _sh
    import subprocess
    from dopeiptv.providers.cast_bridge import hls_args

    exe = _sh.which("ffmpeg")
    probe = _sh.which("ffprobe")
    if not exe or not probe:
        pytest.skip("no ffmpeg")

    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:20,000\nHEJ\n")
    mkv = tmp_path / "f.mkv"
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=25:duration=20",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=20",
         "-i", str(srt), "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", "-c:s", "copy", "-y", str(mkv)], check=True)

    out = tmp_path / "hls"
    out.mkdir()
    subprocess.run(hls_args(exe, str(mkv), True, str(out), subs=0),
                   capture_output=True, timeout=180)
    seg = out / "v0.ts"
    assert seg.exists(), sorted(p.name for p in out.iterdir())
    first = subprocess.run(
        [probe, "-v", "error", "-select_streams", "v",
         "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(seg)],
        capture_output=True, text=True).stdout.splitlines()[0]
    video = float(first.rstrip(","))
    # The subtitle starts at zero in the source, so the picture must start
    # there too. It used to start 1.4 seconds later, which is exactly how
    # far ahead of the picture every line appeared.
    assert video < 0.5, f"the picture starts at {video} s, the subtitle at 0"


def test_every_subtitle_segment_says_which_clock_it_is_on():
    """A WebVTT segment in an HLS stream has to relate its own timeline to
    the transport stream's, and it says so with X-TIMESTAMP-MAP. ffmpeg
    writes none - its segments begin with a bare WEBVTT line - and a
    receiver handed cues it cannot place does not guess: it shows nothing,
    while reporting the track as present and switched on."""
    from dopeiptv.providers.cast_bridge import _timestamp_map
    out = _timestamp_map(b"WEBVTT\n\n00:00.043 --> 00:30.043\nHEJ\n")
    lines = out.split(b"\n")
    assert lines[0] == b"WEBVTT"
    assert lines[1] == b"X-TIMESTAMP-MAP=MPEGTS:0,LOCAL:00:00:00.000"
    assert b"00:00.043 --> 00:30.043" in out
    assert b"HEJ" in out
    # Not doubled when the same segment is fetched twice.
    assert _timestamp_map(out) == out
    # Windows line endings are kept as they were found.
    crlf = _timestamp_map(b"WEBVTT\r\n\r\n00:00.000 --> 00:01.000\r\nA\r\n")
    assert crlf.split(b"\r\n")[1].startswith(b"X-TIMESTAMP-MAP")
    # An empty segment is still a valid one; ffmpeg writes plenty of them.
    assert _timestamp_map(b"WEBVTT\n").startswith(
        b"WEBVTT\nX-TIMESTAMP-MAP=")
    # And nothing that is not a WebVTT file is touched.
    assert _timestamp_map(b"\x47\x40\x00") == b"\x47\x40\x00"
