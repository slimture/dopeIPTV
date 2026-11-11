"""The player's side of the app-kept buffer: when it is used, the session's
time, seeks inside mpv's own cache versus from the store, and an end of
file or a stall answered from the store - not by the app's reconnect."""
from __future__ import annotations

from types import SimpleNamespace

from dopeiptv.core.dvr_store import Segment
from dopeiptv.core.local_buffer import BufferWindow
from dopeiptv.media.dvr_player import DvrPlayerMixin


class Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)


class Mpv(dict):
    def __init__(self, playback_time=0.0, held=None) -> None:
        super().__init__()
        self.playback_time = playback_time
        self.demuxer_cache_state = {"seekable-ranges": held or []}
        self.commands: list = []
        self.played: list[str] = []

    def command(self, *a):
        self.commands.append(a)

    def play(self, url):
        self.played.append(url)


class Session:
    def __init__(self, state="streaming") -> None:
        self.state = state
        self.server = SimpleNamespace(last_opened_t0=1000.0)
        self.segs = [Segment(i, 0, 1000.0 + 2 * i, 1002.0 + 2 * i, 188, None)
                     for i in range(50)]

    def url_for(self, seg):
        return f"local:{seg.seq}"

    def span(self):
        return self.segs[0].t0, self.segs[-1].t1

    def segment_at(self, t):
        return [s for s in self.segs if s.t0 <= t][-1]

    def segment_after(self, t):
        return next((s for s in self.segs if s.t0 > t), None)

    def next_run_after(self, t, seq=None):
        cur = next((s for s in self.segs if s.seq == seq), None) \
            if seq is not None else None
        if cur is None:
            cur = [s for s in self.segs if s.t0 <= t][-1]
        return next((s for s in self.segs
                     if s.seq > cur.seq and s.run != cur.run), None)

    def live_segment(self):
        return self.segs[-2]


class Player(DvrPlayerMixin):
    def __init__(self, mpv, session=None, settings=None) -> None:
        self.video = SimpleNamespace(mpv=mpv)
        self._settings = settings or Settings()
        self._dvr = session
        self._dvr_origin = 1000.0
        self._user_paused = False
        self._eof_seen = True
        self._stall_since = 5.0
        self.mpv_sets: list = []

    def _set_mpv(self, k, v):
        self.mpv_sets.append((k, v))

    def playback_position(self):
        return self.video.mpv.playback_time


def test_used_only_when_asked_and_not_for_hls():
    p = Player(Mpv())
    assert not p._dvr_wanted("http://p.invalid/live/u/p/1.ts")
    p._settings["live_rewind_kept"] = "true"
    assert p._dvr_wanted("http://p.invalid/live/u/p/1.ts")
    assert p._dvr_wanted("https://p.invalid/live/u/p/1")
    assert not p._dvr_wanted("http://p.invalid/live/u/p/1.m3u8")
    assert not p._dvr_wanted("file:///tmp/x.ts")


def test_the_position_is_the_opened_segment_plus_mpvs():
    p = Player(Mpv(playback_time=7.0), Session())
    assert p._dvr_pos(7.0) == 7.0                 # opened at 1000, origin 1000
    p._dvr_open_wall = 1040.0
    assert p._dvr_pos(3.0) == 43.0
    assert p._local_position() == 47.0
    assert p._dvr_window() == BufferWindow(0.0, 100.0)


def test_a_seek_inside_mpvs_cache_is_mpvs_own():
    m = Mpv(playback_time=20.0, held=[{"start": 0.0, "end": 30.0}])
    p = Player(m, Session())
    p._dvr_seek(10.0, BufferWindow(0, 100))
    assert m.commands == [("seek", 10.0, "absolute+exact")]
    assert m.played == []


def test_further_back_opens_the_store_not_the_provider():
    m = Mpv(playback_time=20.0, held=[{"start": 15.0, "end": 30.0}])
    p = Player(m, Session())
    p._dvr_open_wall = 1060.0
    p._dvr_seek(9.0, BufferWindow(0, 100))
    assert m.played == ["local:4"]                # 1009 is in seq 4 (1008-1010)
    assert p._dvr_open_wall == 1008.0
    assert p._eof_seen is False and p._stall_since == 0.0
    assert ("pause", False) in p.mpv_sets


def test_an_end_of_file_goes_on_from_the_next_run():
    m = Mpv(playback_time=9.0)
    s = Session()
    for seg in s.segs[15:]:
        seg.run = 1                               # a reconnect at seq 15
    p = Player(m, s)
    p._dvr_open_wall = 1020.0
    assert p._dvr_on_eof() is True
    assert m.played == ["local:15"]               # the next run's first


def test_the_end_of_a_short_run_does_not_reopen_it():
    # A run of one segment, read to its end within half a second: playback
    # opened that same segment again and again.
    m = Mpv(playback_time=0.3)
    s = Session()
    for seg in s.segs[37:]:
        seg.run = 1
    for seg in s.segs[38:]:
        seg.run = 2                               # 37 alone is run 1
    p = Player(m, s)
    p._dvr_open_wall = s.segs[37].t0
    p._dvr_open_seq = 37
    p._dvr_reopened = 0.0
    assert p._dvr_on_eof() is True
    assert m.played == ["local:38"]


def test_when_its_own_connection_gave_up_the_app_takes_over():
    p = Player(Mpv(playback_time=9.0), Session(state="failed"))
    assert p._dvr_on_eof() is False
    assert p._dvr_on_stall() is False


def test_a_recording_in_the_player_keeps_its_stream():
    m = Mpv(playback_time=9.0, held=[{"start": 5.0, "end": 30.0}])
    m["stream-record"] = "/tmp/rec.ts"
    p = Player(m, Session())
    p._dvr_seek(90.0, BufferWindow(0, 100))
    assert m.played == []
    assert p._dvr_on_eof() is False


def test_a_stall_reopens_where_it_was():
    m = Mpv(playback_time=4.0)
    p = Player(m, Session())
    p._dvr_open_wall = 1030.0
    assert p._dvr_on_stall() is True
    assert m.played == ["local:17"]               # 1034 is in seq 17


def test_the_row_marks_a_gap_and_says_so_under_the_pointer():
    from dopeiptv.i18n import tr
    s = Session()
    s.runs = lambda: [(0, 1000.0, 1050.0), (1, 1100.0, 1198.0)]
    p = Player(Mpv(), s)
    assert p._dvr_gaps(0.0, 198.0) == [round(100 / 198, 3)]
    assert p._dvr_hover(0.0, 198.0, 0.3).endswith(tr("dvr_gap")), \
        "nothing was kept between the two runs"
    assert tr("dvr_gap") not in p._dvr_hover(0.0, 198.0, 0.9)


def test_a_recording_reads_the_buffer_from_what_is_on_screen():
    s = Session()
    asked = []

    class Rec:
        stopped = False

        def stop(self):
            Rec.stopped = True

    s.record = lambda path, at: asked.append((path, at)) or Rec()
    p = Player(Mpv(playback_time=30.0), s)
    assert p._dvr_record("/tmp/x.ts")
    assert asked == [("/tmp/x.ts", 1030.0)], "from the picture on screen"
    rec = p.take_buffer_recorder()
    assert isinstance(rec, Rec) and p.take_buffer_recorder() is None
    p._buffer_rec = rec
    p._stop_buffer_record()
    assert Rec.stopped


def test_leaving_a_channel_hands_it_back_to_the_pool():
    released = []
    p = Player(Mpv(), Session())
    p._dvr_pool = SimpleNamespace(release=released.append, sessions=[])
    s = p._dvr
    p._end_dvr()
    assert released == [s] and p._dvr is None
