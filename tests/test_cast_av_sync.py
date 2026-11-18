"""Sound and picture stay together on an Apple TV after a seek.

A piece of the archive or the rewind buffer is joined in the middle of a
GOP: the copied picture starts at its next keyframe, the sound at once - up
to a GOP earlier. ffmpeg's MP4 muxer then started both tracks at zero and
wrote the difference as an empty edit in the init segment, which an HLS
player on an Apple TV does not apply: the picture ran ahead of the sound.

Two things keep them together, each checked here: the sound before the
first picture is left out of a seek's first piece (cast_feed.LeadTrim),
and every track's fragments carry their own start time (tfdt), with no
empty edit (airplay_bridge.TRACK_TIMES). The transport streams are made by
hand for the unit tests; the tests that run the bridge's real command
lines need ffmpeg and are skipped without it.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import time

import pytest

from dopeiptv.providers import airplay_bridge as ab
from dopeiptv.providers import cast_feed

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(not FFMPEG, reason="no ffmpeg here")

PMT_PID, VIDEO_PID, SOUND_PID = 0x1000, 0x100, 0x101


# -- a transport stream by hand ---------------------------------------------------

def _ts(pid: int, payload: bytes, start: bool = False) -> bytes:
    head = bytes([0x47, (0x40 if start else 0) | pid >> 8, pid & 0xFF])
    room = 184 - len(payload)
    assert room >= 0
    if room == 0:
        return head + b"\x10" + payload
    stuffing = b"" if room == 1 else b"\x00" + b"\xff" * (room - 2)
    return head + b"\x30" + bytes([room - 1]) + stuffing + payload


def _section(table: int, body: bytes) -> bytes:
    length = len(body) + 5 + 4
    return (b"\x00" + bytes([table, 0xB0 | length >> 8, length & 0xFF])
            + b"\x00\x01\xc1\x00\x00" + body + b"\x00\x00\x00\x00")


def _pat() -> bytes:
    return _ts(0, _section(0, struct.pack(">HH", 1, 0xE000 | PMT_PID)), True)


def _pmt(video: bool = True, sound: bool = True) -> bytes:
    body = struct.pack(">HH", 0xE000 | VIDEO_PID, 0xF000)
    if video:
        body += struct.pack(">BHH", 0x1B, 0xE000 | VIDEO_PID, 0xF000)
    if sound:
        body += struct.pack(">BHH", 0x0F, 0xE000 | SOUND_PID, 0xF000)
    return _ts(PMT_PID, _section(2, body), True)


def _pes(pid: int, pts: int, data: bytes, stream_id: int = 0xC0) -> bytes:
    stamp = bytes([0x21 | (pts >> 30 & 7) << 1, pts >> 22 & 0xFF,
                   (pts >> 15 & 0x7F) << 1 | 1, pts >> 7 & 0xFF,
                   (pts & 0x7F) << 1 | 1])
    payload = b"\x00\x00\x01" + bytes([stream_id]) + b"\x00\x00\x80\x80\x05" \
        + stamp + data
    out = _ts(pid, payload[:184], True)
    for i in range(184, len(payload), 184):
        out += _ts(pid, payload[i:i + 184])
    return out


def _sound(pts: int, mark: bytes = b"A") -> bytes:
    return _pes(SOUND_PID, pts, mark * 300)          # two packets each


def _picture(pts: int) -> bytes:
    return _pes(VIDEO_PID, pts, b"V" * 400, 0xE0)


def _through(trim: cast_feed.LeadTrim, data: bytes, step: int) -> bytes:
    out = b"".join(trim.feed(data[i:i + step]) for i in range(0, len(data), step))
    return out + trim.flush()


def _sound_stamps(ts: bytes) -> list[int]:
    out = []
    for i in range(0, len(ts), 188):
        pkt = ts[i:i + 188]
        if (pkt[1] & 0x1F) << 8 | pkt[2] == SOUND_PID:
            pts = cast_feed._pes_pts(pkt)
            if pts is not None:
                out.append(pts)
    return out


@pytest.mark.parametrize("step", [1, 97, 188, 500, 65536])
def test_the_sound_before_the_first_picture_is_left_out(step):
    """Whatever size the reader's output arrives in: sound from the first
    picture's moment on, whole PES packets, everything else untouched."""
    frame = 2160                                    # 24 ms at 90 kHz
    lead = [_sound(90000 + n * frame) for n in range(40)]       # 0.96 s early
    first = 90000 + 40 * frame + 500                # the keyframe, between two
    after = [_sound(90000 + n * frame, b"B") for n in range(40, 60)]
    # As a muxer interleaves them: the last early ones after the keyframe.
    data = (_pat() + _pmt() + b"".join(lead[:38]) + _picture(first)
            + b"".join(lead[38:]) + after[0] + _picture(first + 3600)
            + b"".join(after[1:]))
    trim = cast_feed.LeadTrim()
    out = _through(trim, data, step)
    assert len(out) % 188 == 0 and trim.done
    stamps = _sound_stamps(out)
    assert stamps == [90000 + n * frame for n in range(41, 60)]
    assert 0 <= stamps[0] - first < frame           # within one sound frame
    assert abs(trim.lead - (first - 90000) / 90000) < 1e-6
    # The tables and the picture are all there, in order.
    kept = out.replace(b"".join(after[1:]), b"")
    assert kept == _pat() + _pmt() + _picture(first) + _picture(first + 3600)


def test_a_stamp_that_wraps_is_still_earlier():
    wrap = 1 << 33
    data = (_pat() + _pmt() + _sound(wrap - 4000) + _sound(wrap - 1840)
            + _picture(500) + _sound(320) + _sound(2480))
    trim = cast_feed.LeadTrim()
    assert _sound_stamps(_through(trim, data, 188)) == [2480]


@pytest.mark.parametrize("data", [
    _pat() + _pmt(video=False) + b"".join(_sound(n * 2160) for n in range(50)),
    b"PIECE 1\n" * 100,                              # not a transport stream
    b"\x47" + b"\x00" * 50,                          # cut short
    b"",
], ids=["radio", "no stream", "short", "nothing"])
def test_what_is_not_a_picture_with_sound_passes_as_it_is(data):
    for step in (7, 188, 4096):
        assert _through(cast_feed.LeadTrim(), data, step) == data


def test_a_picture_that_never_comes_costs_no_sound():
    """A dead picture PID (radio in a TV multiplex): the sound is held a
    while, never lost."""
    frame = 2160
    n = int(cast_feed.LEAD_MAX_S * 90000 / frame) + 50
    data = _pat() + _pmt() + b"".join(_sound(k * frame) for k in range(n))
    trim = cast_feed.LeadTrim()
    out = b"".join(trim.feed(data[i:i + 4096]) for i in range(0, len(data), 4096))
    assert trim.done and out == data and trim.lead == 0.0
    # And one that ends before that: flushed at its end.
    short = _pat() + _pmt() + _sound(0) + _sound(frame)
    assert _through(cast_feed.LeadTrim(), short, 100) == short


def test_only_a_seeks_first_piece_is_trimmed(monkeypatch):
    """At a join the sound plays on from the piece before: only where the
    stream begins is the sound before the picture left out."""
    seen = []

    def read(self, sink, exe, url, at, ts_offset, net, stop, trim=None):
        seen.append(trim)
        return 30.0
    monkeypatch.setattr(cast_feed.Feed, "_read", read)
    asked = []

    def url_at(moment):
        asked.append(moment)
        if len(asked) > 3:
            raise EOFError
        return "http://p/timeshift/1.ts", 0.0, moment
    feed = cast_feed.Feed(url_at, 1000.0, clock=lambda: 10**7)

    class Sink:
        def close(self):
            pass
    feed.attach(Sink(), "ffmpeg")               # type: ignore[arg-type]
    end = time.monotonic() + 10
    while feed.running() and time.monotonic() < end:
        time.sleep(0.02)
    assert len(seen) == 3
    assert isinstance(seen[0], cast_feed.LeadTrim) and seen[1:] == [None, None]


def test_the_command_lines_keep_one_timeline():
    """A reader writes one sound frame per PES packet (the trim cuts to the
    frame); every bridge run - a seek's pieces, a channel, a film - has its
    fragments say when each track starts."""
    r = cast_feed.reader_args("ffmpeg", "http://p/1.ts", 5.0)
    assert r[r.index("-pes_payload_size") + 1] == "0"
    assert r.index("-pes_payload_size") > r.index("-i")
    for p, kw in ((ab.Plan(False, cast_feed.INPUT, live=True), {"feed": True}),
                  (ab.Plan(False, "http://p/live/1.ts", live=True), {}),
                  (ab.Plan(False, "/films/a.mkv"), {"start": 60.0})):
        a = ab.ffmpeg_args("ffmpeg", p, **kw)
        i = a.index("-hls_segment_options")
        assert "frag_discont" in a[i + 1] and a.index("-f", a.index("-i")) < i
        # Nothing that moves one track against the other.
        assert "-itsoffset" not in a and "-async" not in a


# -- the real command lines, with ffmpeg -----------------------------------------------

_BOXES = {b"moov", b"trak", b"mdia", b"edts", b"moof", b"traf"}


def _walk(data: bytes, start: int = 0, end: int | None = None):
    end = len(data) if end is None else end
    while start + 8 <= end:
        size, kind = struct.unpack(">I4s", data[start:start + 8])
        head = 8
        if size == 1:
            size, head = struct.unpack(">Q", data[start + 8:start + 16])[0], 16
        size = size or end - start
        yield kind, start + head, start + size
        if kind in _BOXES:
            yield from _walk(data, start + head, start + size)
        start += size


def _init(data: bytes) -> dict[int, dict]:
    """Each track of an init segment: its kind, timescale and edits."""
    tracks: dict[int, dict] = {}
    cur: dict = {}
    for kind, s, _e in _walk(data):
        if kind == b"tkhd":
            v = data[s]
            tid = struct.unpack(">I", data[s + (20 if v else 12):][:4])[0]
            cur = tracks[tid] = {"edits": []}
        elif kind == b"mdhd":
            v = data[s]
            cur["scale"] = struct.unpack(">I", data[s + (20 if v else 12):][:4])[0]
        elif kind == b"hdlr":
            cur["kind"] = data[s + 8:s + 12].decode()
        elif kind == b"elst":
            v, n = data[s], struct.unpack(">I", data[s + 4:s + 8])[0]
            at = s + 8
            for _ in range(n):
                if v:
                    dur, media = struct.unpack(">Qq", data[at:at + 16])
                    at += 20
                else:
                    dur, media = struct.unpack(">Ii", data[at:at + 8])
                    at += 12
                cur["edits"].append((dur, media))
    return tracks


def _fragment(data: bytes) -> dict[int, tuple[int, int]]:
    """Each track of a segment's first fragment: (tfdt, its first sample's
    composition offset)."""
    out: dict[int, tuple[int, int]] = {}
    tid = dts = 0
    for kind, s, _e in _walk(data):
        if kind == b"tfhd":
            tid = struct.unpack(">I", data[s + 4:s + 8])[0]
        elif kind == b"tfdt":
            dts = (struct.unpack(">Q", data[s + 4:s + 12])[0] if data[s]
                   else struct.unpack(">I", data[s + 4:s + 8])[0])
        elif kind == b"trun" and tid not in out:
            flags = struct.unpack(">I", data[s:s + 4])[0] & 0xFFFFFF
            at = s + 8 + (4 if flags & 1 else 0) + (4 if flags & 4 else 0)
            cto = 0
            if flags & 0x800:
                at += sum(4 for bit in (0x100, 0x200, 0x400) if flags & bit)
                cto = struct.unpack(">i", data[at:at + 4])[0]
            out[tid] = (dts, cto)
    return out


class _Measured:
    """What an HLS player that goes by tfdt alone makes of a bridge run."""

    def __init__(self, folder: str) -> None:
        with open(os.path.join(folder, "init.mp4"), "rb") as f:
            self.tracks = _init(f.read())
        with open(os.path.join(folder, ab.PLAYLIST), "rb") as f:
            lines = f.read().decode().splitlines()
        self.extinf = [float(ln[8:].split(",")[0]) for ln in lines
                       if ln.startswith("#EXTINF:")]
        self.names = [ln for ln in lines if ln and not ln.startswith("#")]
        self.frags = []
        for name in self.names:
            with open(os.path.join(folder, name), "rb") as f:
                self.frags.append(_fragment(f.read()))

    def track(self, kind: str) -> int:
        return next(t for t, info in self.tracks.items() if info["kind"] == kind)

    def empty_edits(self) -> list[tuple[int, int]]:
        return [e for info in self.tracks.values() for e in info["edits"]
                if e[1] == -1 and e[0] > 0]

    def start(self, kind: str, seg: int = 0) -> float:
        """When the track's first sample of segment *seg* is shown: tfdt
        and its composition offset, less the edit's media time (how the
        muxer takes a B-frame's delay off again) - empty edits ignored."""
        t = self.track(kind)
        info = self.tracks[t]
        skip = next((m for _d, m in info["edits"] if m > 0), 0)
        dts, cto = self.frags[seg][t]
        return (dts + cto - skip) / info["scale"]


def _source(tmp_path, sound: str, name: str = "src.ts", extra=()) -> str:
    """30 s of test picture with a keyframe every 2 s, and a tone."""
    assert FFMPEG
    out = str(tmp_path / name)
    res = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=1000",
         "-c:v", "libx264", "-preset", "ultrafast", "-g", "50",
         "-keyint_min", "50", "-sc_threshold", "0", "-c:a", sound,
         "-t", "30", *extra, "-f", "mpegts", out],
        capture_output=True, timeout=120)
    if res.returncode != 0:
        pytest.skip(f"this ffmpeg cannot make the test stream ({sound})")
    return out


def _run_feed(src: str, plan: ab.Plan, cuts: list[float], keep: str) -> None:
    """A seek's pieces through the real bridge: the readers and the
    converter as the app builds them."""
    left = list(cuts)

    def url_at(moment):
        if not left:
            raise EOFError("no more of it")
        return src, left.pop(0), moment
    feed = cast_feed.Feed(url_at, 1000.0, clock=lambda: 10**7)
    b = ab.AirPlayBridge()
    b.exe = FFMPEG
    b.start(plan, feed=feed)
    try:
        end = time.monotonic() + 90
        while b"#EXT-X-ENDLIST" not in b._playlist() and time.monotonic() < end:
            time.sleep(0.1)
        assert b"#EXT-X-ENDLIST" in b._playlist(), list(b._tail)
        shutil.copytree(b.folder, keep)
    finally:
        b.stop()


@needs_ffmpeg
@pytest.mark.parametrize("sound,plan_sound,frame", [
    ("aac", "copy", 1024 / 44100), ("ac3", "copy", 1536 / 44100),
    ("mp2", "aac", 1152 / 44100)])
def test_a_seek_between_keyframes_starts_sound_and_picture_together(
        tmp_path, sound, plan_sound, frame):
    """From 5.3 s into a stream with a keyframe every 2 s, through the
    readers and the converter as the bridge runs them: the first sound and
    the first picture are within one sound frame of each other, as the
    fragments' own times say - and nothing is left to an empty edit."""
    src = _source(tmp_path, sound)
    plan = ab.Plan(False, cast_feed.INPUT, live=True, audio=plan_sound,
                   adts=sound == "aac")
    out = str(tmp_path / "out")
    _run_feed(src, plan, [5.3, 17.7], out)
    m = _Measured(out)
    assert not m.empty_edits(), m.tracks
    assert abs(m.start("soun") - m.start("vide")) <= frame + 0.012, (
        m.start("soun"), m.start("vide"))
    # One timeline: each segment starts where the one before it ended, in
    # both tracks, across the join of the two pieces too.
    assert len(m.names) >= 8
    for kind in ("vide", "soun"):
        at = [m.start(kind, i) for i in range(len(m.names))]
        assert at == sorted(at)
        for i in range(1, len(at)):
            # The sound's fragment ends within a frame or two of the cut.
            assert abs((at[i] - at[i - 1]) - m.extinf[i - 1]) < 0.7, (kind, i, at)
    # Sound and picture still together after the join.
    for i in range(len(m.names)):
        assert abs(m.start("soun", i) - m.start("vide", i)) < 0.7


@needs_ffmpeg
def test_without_the_fix_the_same_seek_is_a_second_apart(tmp_path, monkeypatch):
    """The fault itself, so the test above cannot pass by accident: the old
    command lines, and no trim, leave the picture's start to an empty edit
    of about the 0.7 s to the next keyframe (and the muxer's own delay)."""
    src = _source(tmp_path, "aac")
    monkeypatch.setattr(ab, "TRACK_TIMES", [])
    monkeypatch.setattr(cast_feed, "FRAME_PES", [])

    class NoTrim:
        done = True
        lead = 0.0

        def feed(self, chunk):
            return chunk

        def flush(self):
            return b""
    monkeypatch.setattr(cast_feed, "LeadTrim", NoTrim)
    plan = ab.Plan(False, cast_feed.INPUT, live=True, adts=True)
    out = str(tmp_path / "out")
    _run_feed(src, plan, [5.3], out)
    m = _Measured(out)
    edits = m.empty_edits()
    assert edits and 0.5 < edits[0][0] / 1000 < 2.1      # the movie's timescale
    assert abs(m.start("soun") - m.start("vide")) < 0.05   # and tfdt says "together"


@needs_ffmpeg
def test_a_channel_joined_with_its_sound_ahead_keeps_the_difference(tmp_path):
    """A channel or a film read by ffmpeg itself (no pieces, nothing to
    trim): where the sound starts 0.7 s before the picture, the fragments
    say so themselves - the picture's first one starts 0.7 s in."""
    assert FFMPEG
    clean = _source(tmp_path, "aac")
    src = str(tmp_path / "late.ts")
    # The same stream with its picture 0.7 s later than its sound.
    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-itsoffset", "0.7", "-i", clean, "-i", clean,
         "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", "-t", "20",
         "-f", "mpegts", src], check=True, timeout=120)
    for live in (True, False):
        folder = tmp_path / ("live" if live else "film")
        folder.mkdir()
        plan = ab.Plan(False, src, live=live, adts=True)
        res = subprocess.run(ab.ffmpeg_args(FFMPEG, plan), cwd=folder,
                             capture_output=True, timeout=120)
        assert res.returncode == 0, res.stderr[-400:]
        m = _Measured(str(folder))
        assert not m.empty_edits(), m.tracks
        assert abs((m.start("vide") - m.start("soun")) - 0.7) < 0.035


@needs_ffmpeg
def test_the_quick_look_names_the_codecs_of_a_stream_joined_mid_gop(tmp_path):
    """A seek's first look (airplay_bridge.probe, names_only) at an archive
    that opens between two keyframes: no picture size yet, but the names
    the plan needs - from the stream's tables."""
    src = _source(tmp_path, "mp2")
    with open(src, "rb") as f:
        data = f.read()
    cut = (len(data) // 188 * 13 // 100) * 188        # no keyframe near
    mid = tmp_path / "timeshift.ts"
    mid.write_bytes(data[cut:])
    found = ab.probe(str(mid), [], FFMPEG, names_only=True)
    assert found["video"] == "h264" and found["audio"] == ["mp2"]
    plan = ab.refine(ab.Plan(False, str(mid), live=True), found)
    assert (plan.video, plan.audio) == ("copy", "aac")
