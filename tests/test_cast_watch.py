"""The cast watchdog (core/cast_watch.py): every receiver's state read the
same way, a stall told from a pause, two tries for one stall and then a
failure, and a stall that ends - by itself or after a try - counted as
over."""

from __future__ import annotations

from dopeiptv.core import cast_watch as cw


def test_every_receiver_s_spelling_is_one_word():
    # Chromecast ("None"), DLNA, Kodi and AirPlay ("NONE", "ROUTE").
    assert cw.phase("PLAYING/None") == cw.phase("PLAYING/NONE") == cw.PLAYING
    assert cw.phase("BUFFERING/None") == cw.phase("buffering/none") == cw.BUFFERING
    assert cw.phase("PAUSED/NONE") == cw.PAUSED
    assert cw.phase("WAITING/ROUTE") == cw.WAITING
    assert cw.phase("IDLE/FINISHED") == cw.ENDED
    assert cw.phase("IDLE/ERROR") == cw.ERROR
    assert cw.phase("IDLE/None") == cw.phase("IDLE/INTERRUPTED") == cw.IDLE
    for nothing in ("", None, "UNKNOWN/None", "?/None"):
        assert cw.phase(nothing) == cw.STARTING


def _run(w, ph, start, end, step=1.0, **kw):
    said, t = [], start
    while t <= end:
        got = w.tick(t, ph, **kw)
        if got:
            said.append((t, got))
        t += step
    return said


def test_ten_seconds_stuck_is_sent_again_twice_then_it_fails():
    w = cw.Watchdog()
    assert w.tick(0.0, cw.PLAYING) == ""
    said = _run(w, cw.BUFFERING, 1.0, 11.0)
    assert said == [(1.0, cw.BEGAN), (11.0, cw.RECOVER)]
    assert w.tries == 1 and w.recovering()
    # Still stuck after the try (the receiver has gone idle meanwhile).
    said = _run(w, cw.IDLE, 12.0, 22.0)
    assert said == [(12.0, cw.BEGAN), (22.0, cw.RECOVER)] and w.tries == 2
    said = _run(w, cw.BUFFERING, 23.0, 33.0)
    assert said == [(23.0, cw.BEGAN), (33.0, cw.FAIL)]
    assert w.failed and not w.recovering()
    # Given up: nothing more by itself, until Try again starts over.
    assert w.tick(50.0, cw.BUFFERING) == ""
    w.reset()
    assert not w.failed and w.tries == 0
    assert w.tick(51.0, cw.BUFFERING) == cw.BEGAN


def test_a_pause_is_not_a_stall_nor_waiting_for_an_apple_tv():
    w = cw.Watchdog()
    w.tick(-1.0, cw.PLAYING)                          # it has played
    # The person paused it: a receiver that buffers meanwhile is not stuck.
    assert _run(w, cw.BUFFERING, 0.0, 60.0, held=True) == []
    assert _run(w, cw.PAUSED, 61.0, 120.0) == []
    assert _run(w, cw.WAITING, 121.0, 200.0) == []
    # And a stall's count starts over after a pause.
    _run(w, cw.BUFFERING, 201.0, 206.0)
    w.tick(207.0, cw.PAUSED)
    assert _run(w, cw.BUFFERING, 208.0, 217.0) == [(208.0, cw.BEGAN)]
    assert w.tries == 0


def test_nothing_is_judged_while_a_stream_is_on_its_way():
    w = cw.Watchdog()
    assert _run(w, cw.BUFFERING, 0.0, 40.0, busy=True) == []
    assert w.tries == 0


def test_a_stall_that_ends_by_itself_is_told_and_costs_no_try():
    w = cw.Watchdog()
    w.tick(-1.0, cw.PLAYING)                          # it has played
    _run(w, cw.BUFFERING, 0.0, 6.0)
    assert w.tick(7.0, cw.PLAYING) == cw.RESOLVED and w.last_stall == 7.0
    assert w.tries == 0
    # Buffering stays said a moment, so a short wait is seen.
    assert w.buffering_shown(7.5) and not w.buffering_shown(9.0)


def test_playing_again_after_a_try_ends_the_stall_and_its_tries():
    w = cw.Watchdog()
    w.tick(-1.0, cw.PLAYING)                          # it has played
    _run(w, cw.BUFFERING, 0.0, 10.0)
    assert w.tries == 1
    # Playing a moment is not yet over: a blink of play between stalls
    # does not hand out fresh tries.
    assert w.tick(11.0, cw.PLAYING) == ""
    assert w.tries == 1 and not w.recovering()
    assert _run(w, cw.PLAYING, 12.0, 16.0) == [(16.0, cw.RECOVERED)]
    assert w.tries == 0
    # The next stall has two tries of its own.
    said = _run(w, cw.BUFFERING, 17.0, 50.0)
    assert [s for _t, s in said if s in (cw.RECOVER, cw.FAIL)] == [cw.RECOVER, cw.RECOVER, cw.FAIL]


def test_an_error_on_the_receiver_counts_as_stuck():
    w = cw.Watchdog()
    w.tick(-1.0, cw.PLAYING)                          # it has played
    assert [s for _t, s in _run(w, cw.ERROR, 0.0, 10.0)] == [cw.BEGAN, cw.RECOVER]


def test_a_stream_that_has_not_played_yet_has_longer_to_start():
    """A cast just begun or a seek: an Apple TV loading a new stream often
    buffers longer than STALL_S - sent again then, it only started over."""
    w = cw.Watchdog()
    said = _run(w, cw.BUFFERING, 0.0, cw.START_STALL_S - 1)
    assert [s for _t, s in said] == [cw.BEGAN] and w.tries == 0
    assert w.tick(cw.START_STALL_S, cw.BUFFERING) == cw.RECOVER
    # Once it has played, ten seconds is a stall again.
    w = cw.Watchdog()
    w.tick(0.0, cw.PLAYING)
    assert [s for _t, s in _run(w, cw.BUFFERING, 1.0, 11.0)] == [cw.BEGAN, cw.RECOVER]
    # A new stream sent (a seek) starts over: its time to start, the tries kept.
    w.new_stream()
    assert not w.started and w.tries == 1
    assert [s for _t, s in _run(w, cw.BUFFERING, 12.0, 40.0)] == [cw.BEGAN]
    # And a stream seen on its way is a new one too.
    w = cw.Watchdog()
    w.tick(0.0, cw.PLAYING)
    w.tick(1.0, cw.BUFFERING, busy=True)
    assert not w.started


def test_an_ending_is_no_stall():
    # An archive's stretch or an episode that ended has paths of its own.
    w = cw.Watchdog()
    assert _run(w, cw.ENDED, 0.0, 60.0) == []
