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


def test_a_text_subtitle_is_burned_in_through_the_subtitles_filter():
    """The receiver renders no subtitle carried inside a stream - it only
    shows ones handed to it as a separate WebVTT file, which cannot be made
    from a live channel. Burning them into the picture always works, at the
    cost of re-encoding the video."""
    args = ffmpeg_args("ffmpeg", "http://p/x.mkv", copy_video=True,
                       audio=0, subs=1, sub_codec="subrip")
    vf = args[args.index("-vf") + 1]
    # Doubled backslashes: a filtergraph is parsed twice and the colon has to
    # survive both. Quoting instead is what broke it - newer ffmpeg takes a
    # backslash inside quotes literally and the filename kept them.
    # yadif comes first: no Chromecast deinterlaces, and burning subtitles in
    # re-encodes the video anyway. deint=1 leaves progressive frames alone.
    assert vf == ("yadif=deint=1,"
                  "subtitles=filename=http\\\\://p/x.mkv:si=1"), vf
    # copy_video is overridden: a picture that changes cannot be copied.
    assert args[args.index("-c:v") + 1] == "libx264"


def test_a_bitmap_subtitle_is_overlaid_instead():
    """DVB and PGS subtitles are pictures, and the subtitles filter cannot
    draw them - they are composited over the video."""
    args = ffmpeg_args("ffmpeg", "http://p/x.ts", copy_video=True,
                       audio=0, subs=0, sub_codec="dvb_subtitle")
    assert "-filter_complex" in args
    assert args[args.index("-filter_complex") + 1] == \
        "[0:v:0][0:s:0]overlay[v]"
    assert "[v]" in args


def test_a_url_with_a_port_survives_the_filtergraph():
    args = ffmpeg_args("ffmpeg", "http://h:8080/a.mkv", copy_video=False,
                       subs=0, sub_codec="ass")
    vf = args[args.index("-vf") + 1]
    assert vf == ("yadif=deint=1,"
                  "subtitles=filename=http\\\\://h\\\\:8080/a.mkv:si=0"), vf


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


def test_a_subtitle_choice_is_only_offered_when_it_can_be_honoured():
    """A Chromecast renders no subtitle carried inside a stream, so the only
    way to show one is to draw it into the picture - and a text subtitle needs
    ffmpeg's subtitles filter, which plenty of builds ship without. The
    capability is read from ffmpeg itself, once."""
    import dopeiptv.providers.cast_bridge as cb

    listed = []

    class Result:
        stdout = "Unknown filter 'subtitles'.\n"
        stderr = ""

    def fake_run(args, **kw):
        listed.append(args)
        return Result()

    cb._can_burn = None
    old = cb.subprocess.run
    cb.subprocess.run = fake_run
    try:
        # Asked about the one filter, so a build that lays its filter
        # table out differently is not misread as having no libass.
        assert cb.can_burn_subtitles("ffmpeg") is False
        assert "filter=subtitles" in listed[0]
        Result.stdout = ("Filter subtitles\n  Render text subtitles.\n"
                         "    Inputs: video\n")
        assert cb.can_burn_subtitles("ffmpeg") is True
    finally:
        cb.subprocess.run = old
        cb._can_burn = None


def test_the_ffmpeg_that_can_send_subtitles_is_the_one_chosen():
    """Machines carry more than one ffmpeg - a slim one on PATH and a full one
    from Homebrew, or a bundled one inside the app - and whether a build has
    libass decides whether a subtitle can be sent at all. Picking the first on
    PATH meant the choice was made by accident."""
    import dopeiptv.providers.cast_bridge as cb

    asked = []

    def fake_run(args, **kw):
        asked.append(args[0])

        class R:
            # Only the Homebrew one was built with libass.
            stdout = ("Filter subtitles\n  Render text subtitles.\n"
                      if args[0] == "/opt/homebrew/bin/ffmpeg"
                      else "Unknown filter 'subtitles'.\n")
            stderr = ""
        return R()

    old_run, old_which, old_access = cb.subprocess.run, cb.shutil.which, \
        cb.os.access
    cb._ffmpeg, cb._can_burn = False, None
    cb.subprocess.run = fake_run
    cb.shutil.which = lambda n: "/usr/bin/" + n
    cb.os.access = lambda p, m: p in ("/usr/bin/ffmpeg",
                                      "/opt/homebrew/bin/ffmpeg")
    try:
        assert cb.ffmpeg_path() == "/opt/homebrew/bin/ffmpeg"
        assert cb.can_burn_subtitles() is True
        assert asked == ["/usr/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg"]
        # Asked once - spawn() calls this for every run.
        asked.clear()
        cb.ffmpeg_path()
        assert asked == []

        # With only the slim one present it is still used: converting a
        # channel the receiver cannot decode matters more than subtitles.
        cb._ffmpeg, cb._can_burn = False, None
        cb.os.access = lambda p, m: p == "/usr/bin/ffmpeg"
        assert cb.ffmpeg_path() == "/usr/bin/ffmpeg"
        assert cb.can_burn_subtitles() is False

        # And none at all is not a crash.
        cb._ffmpeg, cb._can_burn = False, None
        cb.os.access = lambda p, m: False
        cb.shutil.which = lambda n: None
        assert cb.ffmpeg_path() is None
        assert cb.can_burn_subtitles() is False
    finally:
        cb.subprocess.run, cb.shutil.which = old_run, old_which
        cb.os.access = old_access
        cb._ffmpeg, cb._can_burn = False, None


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


def test_a_burned_subtitle_survives_a_film_resumed_part_way_in():
    """The subtitles filter reads the file from its own copy, and that copy
    knows nothing of a seek made before -i: the video arrives with timestamps
    starting at zero, the filter looks for a line to show at zero seconds,
    and nothing is drawn at all. Not a line out of step - none.

    The fix is to hand the filter the timeline it expects and take it back
    afterwards, which keeps both the cheap seek and a stream that starts at
    zero.
    """
    args = ffmpeg_args("ffmpeg", "/rec/film.mkv", copy_video=False,
                       subs=2, sub_codec="subrip", start=1608.0)
    vf = args[args.index("-vf") + 1]
    assert "setpts=PTS+1608.000/TB,subtitles=" in vf, vf
    assert vf.endswith("setpts=PTS-1608.000/TB"), vf
    # Not by keeping the original timestamps: that renders the subtitle but
    # hands the receiver a stream that begins twenty-six minutes in.
    assert "-copyts" not in args and "-start_at_zero" not in args

    # From the beginning there is no shift to make.
    plain = ffmpeg_args("ffmpeg", "/rec/film.mkv", copy_video=False,
                        subs=2, sub_codec="subrip")
    assert "setpts" not in plain[plain.index("-vf") + 1]

    # A picture-based subtitle is drawn from the same input and moves with
    # the seek on its own.
    bitmap = ffmpeg_args("ffmpeg", "/rec/film.mkv", copy_video=False,
                         subs=2, sub_codec="dvb_subtitle", start=1608.0)
    assert not any("setpts" in a for a in bitmap)


@pytest.mark.filterwarnings("ignore")
def test_ffmpeg_really_draws_the_subtitle_after_a_seek(tmp_path):
    """Against a real ffmpeg, because this is not a thing that can be read
    off the command line.

    Every check above says what the arguments look like. The bug they exist
    for was invisible there: the command was well formed, ffmpeg ran happily,
    exited zero, and drew no subtitle whatsoever. Only a frame counts.
    """
    import shutil
    import subprocess

    exe = shutil.which("ffmpeg")
    if not exe:
        pytest.skip("no ffmpeg")
    if b"Unknown filter" in subprocess.run(
            [exe, "-hide_banner", "-h", "filter=subtitles"],
            capture_output=True).stderr:
        pytest.skip("this ffmpeg has no libass")

    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:05,000 --> 00:00:08,000\nWWWWWWWWWWWW\n")
    src = tmp_path / "src.mkv"
    subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=black:size=160x90:rate=10:duration=12",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=12",
         "-i", str(srt), "-c:v", "libx264", "-c:a", "aac", "-c:s", "copy",
         "-y", str(src)], check=True)   # longer than the subtitle, on purpose

    W, H, FPS = 160, 90, 10

    def frames(start: float) -> list[int]:
        """Lit pixels per frame of the bridge's own command, run for real.
        The subtitle is the only thing in the picture that is not black."""
        out = tmp_path / f"out{start}.mp4"
        args = ffmpeg_args(exe, str(src), copy_video=False, subs=0,
                           sub_codec="subrip", start=start)
        # Exactly what the bridge would run, written where it can be read
        # back instead of piped to a receiver.
        assert args[-1] == "pipe:1"
        r = subprocess.run(args[:-1] + [str(out)], capture_output=True)
        assert out.exists(), r.stderr.decode()[-500:]
        raw = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", str(out),
             "-f", "rawvideo", "-pix_fmt", "gray", "-"],
            capture_output=True).stdout
        size = W * H
        return [sum(1 for b in raw[i:i + size] if b > 60)
                for i in range(0, len(raw) - size + 1, size)]

    # Started five seconds in, the line is on screen at once and gone three
    # seconds later - it runs to 0:08 and the film began at 0:05.
    part_way = frames(5.0)
    assert len(part_way) > FPS * 5, len(part_way)
    assert part_way[FPS // 2] > 100, "the subtitle is drawn after a seek"
    assert part_way[FPS * 4] == 0, "and only while it should be"

    # And from the beginning, where nothing needs shifting.
    whole = frames(0.0)
    assert whole[FPS * 6] > 100
    assert whole[FPS * 1] == 0


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
