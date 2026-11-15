"""Watch together: the code, the room id and the key; sealed messages and
their checks; the relay's clock; the room's bookkeeping; keeping in step
(Corrector, LocalWatch); the link over an in-memory relay; finding what
the room plays in this source; and the window's part, two windows in one
room."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from dopeiptv.core import privacy, together
from dopeiptv.services import together as matching

# -- the code, the room id and the key ---------------------------------------------


def test_a_code_is_eight_characters_nobody_can_misread():
    seen = set()
    for _ in range(200):
        code = together.new_code()
        assert len(code) == 9 and code[4] == "-"
        raw = code.replace("-", "")
        assert all(ch in together.ALPHABET for ch in raw)
        seen.add(code)
    assert len(seen) > 190
    for lookalike in "01OIL":
        assert lookalike not in together.ALPHABET


def test_a_code_as_typed_or_pasted():
    assert together.parse_code("k7pq-m2xd") == "K7PQ-M2XD"
    assert together.parse_code(" K7PQ M2XD ") == "K7PQ-M2XD"
    assert together.parse_code("K7PQM2XD") == "K7PQ-M2XD"
    for bad in ("", "K7PQ-M2X", "K7PQ-M2XDZ", "K0PQ-M2XD", "K7PQ-M2XI", None, "ÅÄÖ-1234"):
        assert together.parse_code(bad) is None, bad


def test_room_id_and_key_come_from_the_code_alone():
    room, key = together.derive("K7PQ-M2XD")
    assert len(room) == 64 and all(c in "0123456789abcdef" for c in room)
    assert len(key) == 32 and key.hex() != room
    assert together.derive("k7pq m2xd") == (room, key)        # as typed
    other_room, other_key = together.derive("K7PQ-M2XE")
    assert other_room != room and other_key != key
    with pytest.raises(ValueError):
        together.derive("nonsense")


# -- sealed messages ----------------------------------------------------------------

ROOM, KEY = together.derive("ABCD-EFGH")


def test_a_message_sealed_opens_only_in_its_room():
    msg = {"k": "hi", "f": "ab" * 8, "n": 1, "t": 1, "name": "Anna"}
    blob = together.seal(KEY, ROOM, msg)
    assert together.open_frame(KEY, ROOM, blob) == msg
    other_room, other_key = together.derive("ABCD-EFGJ")
    assert together.open_frame(other_key, ROOM, blob) is None          # another key
    assert together.open_frame(KEY, other_room, blob) is None          # another room
    broken = bytearray(blob)
    broken[20] ^= 1
    assert together.open_frame(KEY, ROOM, bytes(broken)) is None       # tampered
    assert together.open_frame(KEY, ROOM, b"\x02" + blob[1:]) is None  # a newer format
    assert together.open_frame(KEY, ROOM, b"short") is None
    assert together.open_frame(KEY, ROOM, "text") is None


def test_sizes_come_in_a_few_steps_and_have_a_ceiling():
    sizes = set()
    for n in (1, 50, 150, 300, 700, 1400):
        blob = together.seal(KEY, ROOM, {"x": "a" * n})
        assert len(blob) <= together.MAX_FRAME
        sizes.add(len(blob))
    assert sizes == {1 + 12 + p + 16 for p in together._PAD}
    with pytest.raises(ValueError):
        together.seal(KEY, ROOM, {"x": "a" * 2000})
    assert len(together.seal(KEY, ROOM, {"x": "a"})) == len(together.seal(KEY, ROOM, {"x": "ab"}))


def _state_wire(**kw):
    st = {"m": {"k": "movie", "n": "Dune (2021)", "q": "Dune", "y": 2021, "tm": 438631},
          "p": True, "x": 12.5, "t": 1000, "v": 3, "b": "cd" * 8}
    st.update(kw)
    return st


def test_messages_are_checked_field_by_field():
    good = {"k": "st", "f": "ab" * 8, "n": 4, "t": 2000, "st": _state_wire()}
    msg = together.parse(good)
    assert msg is not None and msg.kind == "st" and msg.state is not None
    assert msg.state.media == together.Media("movie", "Dune (2021)", "Dune", 2021, 438631)
    assert msg.state.pos == 12.5 and msg.state.playing and msg.state.ver == 3
    for bad in ({**good, "k": "evil"}, {**good, "f": "XYZ"}, {**good, "n": 0}, {**good, "n": "4"},
                {**good, "t": None}, {**good, "st": _state_wire(x=-1)}, {**good, "st": _state_wire(p=1)},
                {**good, "st": _state_wire(m={"k": "song", "n": "x"})}, {**good, "st": "x"},
                {**good, "st": _state_wire(v=True)}, None, [1, 2]):
        assert together.parse(bad) is None, bad
    hi = together.parse({"k": "hi", "f": "ab" * 8, "n": 1, "t": 5,
                         "name": "  <b>Anna</b>\n\x07 Svensson with a very long surname indeed ",
                         "re": True, "s": "weird"})
    assert hi is not None and hi.answer and hi.status == ""
    assert "<" not in hi.name and ">" not in hi.name and "\n" not in hi.name
    assert len(hi.name) <= together.NAME_MAX and hi.name.startswith("‹b›Anna")


def test_a_state_says_where_the_room_is_and_which_is_newer():
    m = together.Media("movie", "Dune")
    st = together.PlayState(m, True, 100.0, 10_000, 2, "aa" * 8)
    assert st.position_at(15_000) == 105.0
    assert st.position_at(5_000) == 100.0            # never backwards
    paused = together.PlayState(m, False, 100.0, 10_000, 3, "aa" * 8)
    assert paused.position_at(99_000) == 100.0
    assert paused.newer_than(st) and not st.newer_than(paused) and st.newer_than(None)
    tie = together.PlayState(m, True, 0.0, 10_000, 2, "bb" * 8)
    assert tie.newer_than(st) and not st.newer_than(tie)       # the same everywhere
    assert together.PlayState.from_wire(st.wire()) == st
    live = together.Media("live", "TV4 HD", guide="tv4.se")
    assert together.Media.from_wire(live.wire()) == live and not live.synced and m.synced


# -- the relay's clock ----------------------------------------------------------------


def test_the_clock_trusts_the_shortest_round_trip():
    mono = [100.0]
    clock = together.Clock(mono=lambda: mono[0], wall=lambda: 5000.0)
    assert not clock.known and clock.now_ms() == 5_000_000      # the wall clock, until told
    # The relay is 2 s ahead of this computer's wall clock.
    clock.sample(100.0, 5_002_400, 100.8)       # 0.8 s round trip: a noisy answer
    clock.sample(101.0, 5_003_050, 101.1)       # 0.1 s round trip
    clock.sample(102.0, 5_009_000, 109.0)       # too long a trip to count
    assert clock.known
    assert abs(clock.offset() - (5003.05 - 101.05)) < 1e-6
    mono[0] = 200.0
    assert abs(clock.now_ms() - (200.0 + 5003.05 - 101.05) * 1000) < 1
    assert abs(clock.uncertainty() - 0.05) < 1e-9
    clock.sample(103.0, 0, 103.1)                # no time given: ignored
    assert abs(clock.offset() - (5003.05 - 101.05)) < 1e-6


# -- the room -------------------------------------------------------------------------


class _Clock(together.Clock):
    def __init__(self, ms: int = 1_000_000) -> None:
        super().__init__()
        self.ms = ms

    def now_ms(self, mono=None) -> int:
        return self.ms


def _msg(kind, sender, n, t, **kw):
    d = {"k": kind, "f": sender, "n": n, "t": t}
    d.update(kw)
    return together.parse(d)


def test_who_is_in_the_room():
    clock = _Clock()
    room = together.Room("ABCD-EFGH", "aa" * 8, "Me", clock)
    bob = "bb" * 8
    ev = room.receive(_msg("hi", bob, 1, clock.ms, name="Bob"), now=50.0)
    assert [k for k, _ in ev] == ["joined", "hello"] and room.names() == ["Bob"]
    assert room.receive(_msg("hi", bob, 1, clock.ms, name="Bob"), now=50.0) == []   # seen
    ev = room.receive(_msg("ka", bob, 2, clock.ms, name="Bobby", s="miss"), now=60.0)
    assert [k for k, _ in ev] == ["status"] and room.members[bob].name == "Bobby"
    ev = room.receive(_msg("hi", bob, 3, clock.ms, name="Bobby", re=True), now=61.0)
    assert ev == []                                  # an answer asks for none
    assert room.receive(_msg("hi", "aa" * 8, 9, clock.ms, name="Me"), now=61.0) == []  # mine
    assert room.expire(61.0 + room.GONE_AFTER - 1) == []
    gone = room.expire(61.0 + room.GONE_AFTER + 1)
    assert [m.name for m in gone] == ["Bobby"] and room.members == {}
    cleo = "cc" * 8
    room.receive(_msg("hi", cleo, 1, clock.ms, name="Cleo"), now=70.0)
    ev = room.receive(_msg("bye", cleo, 2, clock.ms), now=71.0)
    assert ev[0][0] == "left" and room.members == {}
    # From the room's history: someone long quiet is not "in".
    old = clock.ms - int((room.GONE_AFTER + 10) * 1000)
    assert room.receive(_msg("hi", "dd" * 8, 1, old, name="Dan"), now=80.0) == []


def test_the_newest_state_wins_and_publishing_moves_the_version_on():
    clock = _Clock()
    me = "aa" * 8
    room = together.Room("ABCD-EFGH", me, "Me", clock)
    film = together.Media("movie", "Dune", "Dune", 2021)
    sent = room.publish(film, True, 30.0)
    assert sent["k"] == "st" and room.state.ver == 1 and room.state.by == me
    older = _state_wire(v=1, t=clock.ms - 1, b="bb" * 8)
    assert room.receive(_msg("st", "bb" * 8, 1, clock.ms, st=older), now=1.0) == []
    newer = _state_wire(v=2, t=clock.ms + 10, b="bb" * 8, x=40.0, p=False)
    ev = room.receive(_msg("st", "bb" * 8, 2, clock.ms, st=newer), now=1.0)
    assert ev[-1][0] == "state" and room.state.pos == 40.0 and not room.state.playing
    room.publish(film, True, 41.0)
    assert room.state.ver == 3
    # A state "from the future" (a wrong clock) is taken as now.
    future = _state_wire(v=4, t=clock.ms + 3_600_000, b="bb" * 8)
    room.receive(_msg("st", "bb" * 8, 3, clock.ms, st=future), now=2.0)
    assert room.state.t == clock.ms
    # A hello carries what the room plays, for whoever joins.
    hello = together.parse(room.hello())
    assert hello.state == room.state and hello.name == "Me"


# -- keeping in step ------------------------------------------------------------------


def test_in_step_nothing_happens_and_play_pause_are_mirrored():
    fix = together.Corrector()
    assert fix.decide(100.4, True, 100.0, True, now=10.0) == together.NONE
    assert fix.decide(99.2, True, 100.0, True, now=11.0) == together.NONE
    assert fix.decide(100.0, True, 100.0, False, now=12.0).action == "pause"
    assert fix.decide(100.0, False, 100.0, False, now=12.5) == together.NONE   # a moment
    # Paused, the frames are lined up exactly.
    assert fix.decide(103.0, False, 100.0, False, now=14.0) == together.Fix("seek", 100.0)
    assert fix.decide(100.0, False, 100.0, True, now=20.0).action == "play"


def test_a_little_ahead_holds_far_off_or_behind_seeks():
    fix = together.Corrector()
    # One reading is not enough.
    assert fix.decide(103.0, True, 100.0, True, now=10.0) == together.NONE
    got = fix.decide(104.0, True, 101.0, True, now=11.0)
    assert got.action == "hold" and abs(got.value - 3.0) < 1e-9
    # Not again within the grace.
    assert fix.decide(110.0, True, 101.0, True, now=12.0) == together.NONE
    fix = together.Corrector()
    fix.decide(90.0, True, 100.0, True, now=10.0)
    got = fix.decide(91.0, True, 101.0, True, now=11.0)
    assert got.action == "seek" and got.value == 101.0 + fix.LEAD_START
    fix = together.Corrector()
    fix.decide(120.0, True, 100.0, True, now=10.0)
    assert fix.decide(121.0, True, 101.0, True, now=11.0).action == "seek"   # too far to wait
    # The last seconds of a title: nothing to catch up to.
    fix = together.Corrector()
    fix.decide(80.0, True, 7198.0, True, now=1.0, duration=7200.0)
    assert fix.decide(81.0, True, 7199.0, True, now=2.0, duration=7200.0) == together.NONE


def test_the_lead_learns_how_long_a_seek_takes():
    fix = together.Corrector()
    fix.decide(50.0, True, 60.0, True, now=10.0)
    first = fix.decide(51.0, True, 61.0, True, now=11.0)
    assert first.action == "seek"
    # The seek took 3 s: landed 1.5 s behind (lead 1.5).
    after = fix.decide(64.5, True, 66.0, True, now=17.0)
    assert after == together.NONE                     # within IN_SYNC... and learnt:
    assert fix.lead > together.Corrector.LEAD_START
    assert together.Corrector.LEAD_MIN <= fix.lead <= together.Corrector.LEAD_MAX


def test_a_seek_here_is_told_from_playback_and_from_our_own():
    w = together.LocalWatch()
    assert not w.tick(10.0, True, now=0.0)
    assert not w.tick(11.0, True, now=1.0)            # playing on
    assert not w.tick(11.2, True, now=2.0)            # stalling is not a seek
    assert w.tick(60.0, True, now=3.0)                # forward
    assert w.tick(20.0, True, now=4.0)                # back
    assert not w.tick(20.5, False, now=5.0)
    assert w.tick(30.0, False, now=6.0)               # paused, moved
    w.expect_seek(100.0, now=7.0)
    assert not w.tick(99.5, True, now=7.5)            # ours
    assert w.tick(200.0, True, now=8.0)               # the next one is not


# -- the link, over an in-memory relay --------------------------------------------------


class Hub:
    """The relay's rules (donate/together.py), in memory."""

    def __init__(self, most: int = together.MAX_MEMBERS) -> None:
        self.cond = threading.Condition()
        self.msgs: list[tuple[int, str, bytes]] = []
        self.members: set[str] = set()
        self.most = most
        self.left: list[str] = []

    def seat(self, member: str) -> None:
        if member not in self.members and len(self.members) >= self.most:
            raise together.RelayError("full")
        self.members.add(member)


class FakeRelay:
    def __init__(self, hub: Hub, member: str) -> None:
        self.hub, self.member, self.closed = hub, member, False

    def post(self, blob: bytes) -> int:
        with self.hub.cond:
            self.hub.seat(self.member)
            self.hub.msgs.append((len(self.hub.msgs) + 1, self.member, bytes(blob)))
            self.hub.cond.notify_all()
        return int(time.time() * 1000)

    def poll(self, after: int, wait: float) -> together.Polled:
        end = time.monotonic() + min(wait, 5.0)
        with self.hub.cond:
            self.hub.seat(self.member)
            while True:
                got = [(s, b) for s, m, b in self.hub.msgs if s > after and m != self.member]
                if got or self.closed or time.monotonic() >= end:
                    break
                self.hub.cond.wait(0.05)
            return together.Polled(seq=len(self.hub.msgs), msgs=got,
                                   now=int(time.time() * 1000), members=len(self.hub.members))

    def leave(self) -> None:
        with self.hub.cond:
            self.hub.members.discard(self.member)
            self.hub.left.append(self.member)

    def close(self) -> None:
        self.closed = True
        with self.hub.cond:
            self.hub.cond.notify_all()


def _wait(cond, timeout: float = 8.0, step=None) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if step is not None:
            step()
        if cond():
            return True
        time.sleep(0.01)
    return False


def _link(hub, room, key, me, got, status):
    clock = together.Clock()
    link = together.Link(room, key, me, clock, on_message=got.append,
                         on_status=lambda k, n: status.append((k, n)),
                         relay_for=lambda r, m: FakeRelay(hub, m))
    link.start()
    return link, clock


def test_two_links_pass_sealed_messages_through_the_relay():
    hub = Hub()
    a_got, b_got, a_st, b_st = [], [], [], []
    a, b = "a1" * 8, "b2" * 8
    link_a, clock_a = _link(hub, ROOM, KEY, a, a_got, a_st)
    link_b, _ = _link(hub, ROOM, KEY, b, b_got, b_st)
    try:
        room_a = together.Room("ABCD-EFGH", a, "Anna", clock_a)
        link_a.send(room_a.hello())
        assert _wait(lambda: b_got)
        assert b_got[0].kind == "hi" and b_got[0].name == "Anna" and b_got[0].sender == a
        assert _wait(lambda: ("ok", 2) in b_st)
        assert _wait(lambda: clock_a.known)       # its requests timed the relay's clock
        # The relay only ever held ciphertext.
        assert all(b"Anna" not in blob for _s, _m, blob in hub.msgs)
        link_b.send({"k": "ka", "f": b, "n": 1, "t": 5, "name": "Bob"})
        assert _wait(lambda: a_got)
        assert a_got[0].name == "Bob" and not any(m.sender == a for m in a_got)
        link_a.close(room_a.bye(), wait=3.0)
        assert _wait(lambda: any(m.kind == "bye" for m in b_got))
        assert _wait(lambda: a in hub.left)
        assert link_a.closed
        link_a.send({"k": "ka"})                  # after close: dropped, no error
    finally:
        link_a.close()
        link_b.close()


def test_a_full_room_says_so():
    hub = Hub(most=1)
    got, status = [], []
    link_a, _ = _link(hub, ROOM, KEY, "a1" * 8, [], [])
    try:
        assert _wait(lambda: "a1" * 8 in hub.members)
        link_b, _ = _link(hub, ROOM, KEY, "b2" * 8, got, status)
        try:
            assert _wait(lambda: any(k == "full" for k, _n in status))
        finally:
            link_b.close()
    finally:
        link_a.close()


def test_what_another_key_sealed_is_not_delivered():
    hub = Hub()
    got = []
    link, _ = _link(hub, ROOM, KEY, "a1" * 8, got, [])
    try:
        other_room, other_key = together.derive("ZZZZ-ZZZZ")
        stray = together.seal(other_key, ROOM, {"k": "hi", "f": "c3" * 8, "n": 1, "t": 1})
        FakeRelay(hub, "c3" * 8).post(stray)
        FakeRelay(hub, "c3" * 8).post(b"\x01" + os.urandom(60))
        good = together.seal(KEY, ROOM, {"k": "hi", "f": "c3" * 8, "n": 2, "t": 1, "name": "Cleo"})
        FakeRelay(hub, "c3" * 8).post(good)
        assert _wait(lambda: got)
        time.sleep(0.2)
        assert [m.name for m in got] == ["Cleo"]
    finally:
        link.close()


# -- what plays, found in this source ----------------------------------------------------


def test_what_plays_is_described_as_any_source_would_know_it():
    film = matching.describe("movie", {"name": "EN| Dune: Part Two (2024) [4K]", "stream_id": 3,
                                       "tmdb": "693134"})
    assert film.kind == "movie" and film.title == "Dune: Part Two" and film.year == 2024
    assert film.tmdb == 693134 and film.name == "Dune: Part Two (2024)"
    ep = matching.describe("episode", {"season": "2", "episode_num": 5, "id": "77"},
                           {"name": "Severance (2022)", "series_id": 9})
    assert (ep.kind, ep.title, ep.year, ep.season, ep.episode) == ("episode", "Severance", 2022, 2, 5)
    assert ep.name == "Severance S02E05"
    live = matching.describe("live", {"name": "SE: TV4 HD", "epg_channel_id": "tv4.se"})
    assert live == together.Media("live", "SE: TV4 HD", guide="tv4.se")
    assert matching.describe("local", {"name": "holiday.mkv"}) is None
    assert matching.describe("movie", None) is None
    assert matching.describe("episode", {"name": "x"}, {"name": "Show"}) is None   # no numbers
    # A TMDB id known here already (the poster cache) is used.
    film2 = matching.describe("movie", {"name": "Arrival (2016)"}, tmdb_of=lambda t, k: 329865)
    assert film2.tmdb == 329865


def test_the_same_title_by_id_else_by_title_and_year():
    a = together.Media("movie", "x", "Dune: Part Two", 2024, 693134)
    assert matching.same(a, together.Media("movie", "y", "Dune Part Two", 2024))
    assert matching.same(a, together.Media("movie", "y", "dune - part two", 2023))   # a year apart
    assert not matching.same(a, together.Media("movie", "y", "Dune Part Two", 2021))
    assert not matching.same(a, together.Media("movie", "y", "Dune Part Two", 2024, 1))
    assert matching.same(a, together.Media("movie", "y", "Something else", None, 693134))
    assert not matching.same(a, together.Media("episode", "y", "Dune: Part Two", 2024, 693134, 1, 1))
    assert not matching.same(a, None)
    e1 = together.Media("episode", "x", "The Office", 2005, None, 2, 3)
    assert matching.same(e1, together.Media("episode", "y", "Office", 2005, None, 2, 3))
    assert not matching.same(e1, together.Media("episode", "y", "The Office", 2005, None, 2, 4))
    l1 = together.Media("live", "SE: TV4 HD", guide="tv4.se")
    assert matching.same(l1, together.Media("live", "TV4 FHD"))
    assert matching.same(l1, together.Media("live", "TV Four", guide="TV4.se"))
    assert not matching.same(l1, together.Media("live", "SVT1", guide="svt1.se"))


VOD = [
    {"name": "EN| Dune (1984)", "stream_id": 1},
    {"name": "Dune (2021) [MULTI]", "stream_id": 2},
    {"name": "Dune 2021 4K", "stream_id": 3},
    {"name": "Arrival", "stream_id": 4, "tmdb": "329865"},
    {"name": "Dune: Part Two", "stream_id": 5},
]


def test_a_film_found_by_id_else_the_closest_year():
    want = together.Media("movie", "Dune (2021)", "Dune", 2021)
    assert matching.best_title(VOD, want, "vod")["stream_id"] == 2          # first of the year
    assert matching.best_title(VOD, together.Media("movie", "x", "Dune", 1985), "vod")["stream_id"] == 1
    assert matching.best_title(VOD, together.Media("movie", "x", "Dune", 1999), "vod") is None
    assert matching.best_title(VOD, together.Media("movie", "x", "Dune", None), "vod")["stream_id"] == 1
    # By id, whatever the title says.
    assert matching.best_title(VOD, together.Media("movie", "x", "L'arrivée", 2016, 329865),
                               "vod")["stream_id"] == 4
    # A row whose id here says another film is not that film.
    assert matching.best_title(VOD, together.Media("movie", "x", "Dune", 2021, 438631), "vod",
                               tmdb_of=lambda t, k: 1) is None
    assert matching.best_title(VOD, together.Media("movie", "x", "Dune: Part Two", None),
                               "vod")["stream_id"] == 5
    assert matching.best_title([], want, "vod") is None


INFO = {"episodes": {"1": [{"id": "101", "episode_num": 1, "title": "Pilot"},
                           {"id": "102", "episode_num": "2", "title": "Two", "container_extension": "mkv"}],
                     "2": [{"id": "201", "episode_num": 1, "season": 2, "title": "Back"}]}}


class FakeClient:
    def __init__(self, live=(), vod=(), series=(), info=None) -> None:
        self._live, self._vod, self._series, self._info = list(live), list(vod), list(series), info or {}
        self.asked: list[str] = []

    def live_streams(self, cat=None):
        self.asked.append("live")
        return self._live

    def vod_streams(self, cat=None):
        self.asked.append("vod")
        return self._vod

    def series_list(self, cat=None):
        self.asked.append("series")
        return self._series

    def series_info(self, sid):
        self.asked.append(f"info:{sid}")
        return json.loads(json.dumps(self._info))

    def live_url(self, sid, fmt="ts"):
        return f"http://p/live/{sid}.{fmt}"

    def vod_url(self, sid, ext=None):
        return f"http://p/movie/{sid}.{ext or 'mp4'}"

    def episode_url(self, eid, ext=None):
        return f"http://p/series/{eid}.{ext or 'mp4'}"


def test_located_in_this_source():
    client = FakeClient(live=[{"name": "TV4 FHD", "stream_id": 7, "epg_channel_id": "tv4.se"}],
                        vod=VOD, series=[{"name": "The Office (US) (2005)", "series_id": 9},
                                         {"name": "The Office (2001)", "series_id": 8}],
                        info=INFO)
    got = matching.locate(client, together.Media("movie", "Dune (2021)", "Dune", 2021))
    assert got.kind == "movie" and got.row["stream_id"] == 2 and got.url == "http://p/movie/2.mp4"
    got = matching.locate(client, together.Media("live", "SE: TV4 HD", guide="tv4.se"))
    assert got.kind == "live" and got.row["stream_id"] == 7
    got = matching.locate(client, together.Media("episode", "x", "The Office", 2001, None, 1, 2))
    assert got.kind == "episode" and got.series["series_id"] == 8
    assert got.url == "http://p/series/102.mkv" and got.row["id"] == "102"
    assert [e["id"] for e in got.queue] == ["101", "102", "201"]
    assert got.queue.index(got.row) == 1 and got.row["name"].startswith("S1 * E2")
    assert matching.locate(client, together.Media("episode", "x", "The Office", 2001, None, 5, 1)) is None
    assert matching.locate(client, together.Media("movie", "x", "Solaris", 1972)) is None
    assert matching.locate(client, together.Media("live", "Nope TV")) is None

    class Broken(FakeClient):
        def vod_streams(self, cat=None):
            raise OSError("down")
    assert matching.locate(Broken(), together.Media("movie", "x", "Dune", 2021)) is None
    assert matching.locate(object(), together.Media("movie", "x", "Dune", 2021)) is None


# -- the window's part ------------------------------------------------------------------

_APP: list = []


def _qapp():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    if not _APP:
        _APP.append(QApplication.instance() or QApplication([]))     # kept: not collected
    return _APP[0]


def _window_class():
    from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

    from dopeiptv.ui.mw_together import _TogetherMixin

    class FakePlayer(QObject):
        paused_changed = pyqtSignal(bool)
        stopped = pyqtSignal()

        def __init__(self) -> None:
            super().__init__()
            self.extra_options: list = []
            self.current_url = None
            self.pos = 0.0
            self._paused = False
            self.seeks: list[float] = []
            self.video = SimpleNamespace(mpv=None)

        def playback_position(self) -> float:
            return self.pos

        def playback_duration(self) -> float:
            return 7200.0

        def toggle_pause(self) -> None:
            self._paused = not self._paused
            self.paused_changed.emit(self._paused)

        def _do_seek(self, s: float) -> None:
            self.seeks.append(s)
            self.pos = s

    class Win(_TogetherMixin, QObject):
        def __init__(self, client, hub) -> None:
            QObject.__init__(self)
            self.pool = QThreadPool()
            self.client = client
            self.player = FakePlayer()
            self.series_ctx = None
            self._last_playback = None
            self._playing_catchup = False
            self.tmdb = None
            self.toasts: list[str] = []
            self.started: list[tuple] = []
            self._together_relay_for = lambda room, me: FakeRelay(hub, me)
            self._init_together()

        def _show_toast(self, text, ms=0) -> None:
            self.toasts.append(text)

        @staticmethod
        def _item_key(it):
            return it.get("stream_id") or it.get("series_id") or it.get("id") if it else None

        def _start_playback(self, url, title, icon, key, kind, record=True, item=None,
                            catchup=False, start_at=0.0) -> None:
            at = self._together_take_start()
            self.started.append((kind, url, at))
            self.player.current_url = url
            self.player.pos = at or 0.0
            self.player._paused = False
            self._last_playback = {"kind": kind, "item": item,
                                   "series_ctx": self.series_ctx if kind == "episode" else None}
            self._together_started(key, title, item, kind, catchup)

        def play_live_channel(self, row) -> None:
            self._start_playback(self.client.live_url(row["stream_id"]), row["name"], None,
                                 row["stream_id"], "live", item=row)

    return Win


def _spin(cond, timeout: float = 8.0) -> bool:
    from PyQt6.QtCore import QCoreApplication
    return _wait(cond, timeout, step=QCoreApplication.processEvents)


def _pair():
    _qapp()
    Win = _window_class()
    hub = Hub()
    a = Win(FakeClient(vod=[{"name": "EN| Dune (2021) [4K]", "stream_id": 11, "tmdb": "438631"}],
                       live=[{"name": "SE: TV4 HD", "stream_id": 1, "epg_channel_id": "tv4.se"}]), hub)
    b = Win(FakeClient(vod=[{"name": "Dune 2021", "stream_id": 99}],
                       live=[{"name": "TV4 FHD", "stream_id": 42, "epg_channel_id": "TV4.se"}]), hub)
    return a, b, hub


def _close(*wins) -> None:
    for w in wins:
        w.together_leave(quiet=True)
        w.pool.waitForDone(3000)


def test_two_windows_watch_a_film_together():
    a, b, hub = _pair()
    try:
        a._start_playback("http://a/movie/11.mkv", "Dune", None, 11, "movie",
                          item=a.client._vod[0])
        a.player.pos = 600.0
        assert a.together_start("Anna") is None
        assert _spin(lambda: a._together_link is not None)
        code = a.together_view()["code"]
        assert together.parse_code(code) == code
        a._together_tick()                       # what plays here goes to the room
        assert _spin(lambda: a._together_room.state is not None and len(hub.msgs) >= 2)
        assert a._together_room.state.media.tmdb == 438631
        # Bob joins with the code; his source has the film under another name.
        assert b.together_join(code.lower(), "Bob") is None
        assert _spin(lambda: b.started)
        kind, url, at = b.started[0]
        assert kind == "movie" and url == "http://p/movie/99.mp4"
        assert 600.0 + together.Corrector.LEAD_START <= at < 600.0 + together.Corrector.LEAD_START + 5
        assert _spin(lambda: "Bob" in a._together_room.names() and "Anna" in b._together_room.names())
        view = b.together_view()
        assert view["title"] == "Dune (2021)" and not view["live"] and view["members"] == [("Anna", "ok")]
        # Anna pauses: Bob's player pauses too, at her frame.
        b._together_started_at -= 10
        a.player.toggle_pause()
        assert _spin(lambda: a._together_room.state.playing is False)
        assert _spin(lambda: b.player._paused)
        b._together_fix.reset()
        b._together_tick()
        assert b.player.seeks and abs(b.player.seeks[-1] - 600.0) < 0.01
        # Bob leaves: Anna is told; nothing stops on either side.
        b.together_leave()
        assert b.player.current_url and a.player.current_url
        assert _spin(lambda: "Bob" not in a._together_room.names())
        assert any("Bob" in t for t in a.toasts)
        assert b.together_view() == {"in": False, "private": False}
    finally:
        _close(a, b)


def test_a_live_channel_is_tuned_on_the_others_own_source():
    a, b, _hub = _pair()
    from dopeiptv.i18n import tr
    from dopeiptv.ui.together_ui import note_text
    try:
        assert a.together_start("Anna") is None
        assert b.together_join("", "Bob") == tr("together_code_bad")
        assert _spin(lambda: a._together_link is not None)
        assert b.together_join(a.together_view()["code"], "Bob") is None
        assert _spin(lambda: b._together_link is not None)
        a.play_live_channel(a.client._live[0])
        assert _spin(lambda: b.started)
        assert b.started[0][:2] == ("live", "http://p/live/42.ts")
        assert _spin(lambda: b.together_view().get("live"))
        assert tr("together_live_note") in note_text(b.together_view())
    finally:
        _close(a, b)


def test_a_title_this_source_lacks_is_said_plainly():
    a, b, _hub = _pair()
    b.client._vod = []
    try:
        a._start_playback("http://a/movie/11.mkv", "Dune", None, 11, "movie", item=a.client._vod[0])
        a.player.pos = 30.0
        a.together_start("Anna")
        assert _spin(lambda: a._together_link is not None)
        a._together_tick()
        b.together_join(a.together_view()["code"], "Bob")
        from dopeiptv.i18n import tr
        assert _spin(lambda: b.together_view().get("note") == tr("together_not_here", title="Dune (2021)"))
        assert not b.started and any(tr("together_not_here", title="Dune (2021)") == t for t in b.toasts)
        assert _spin(lambda: ("Bob", "miss") in a.together_view()["members"])
    finally:
        _close(a, b)


def test_never_while_private_viewing_is_on():
    a, b, _hub = _pair()
    from dopeiptv.i18n import tr
    try:
        privacy.set_private(True)
        assert a.together_start("Anna") == tr("together_private")
        assert a.together_view() == {"in": False, "private": True}
        privacy.set_private(False)
        assert a.together_start("  ") == tr("together_name_needed")
        assert a.together_start("Anna") is None
        assert _spin(lambda: a._together_link is not None)
        privacy.set_private(True)
        a._together_private_on()                      # what mw_privacy calls
        assert not a.together_view()["in"] and tr("together_private_left") in a.toasts
    finally:
        privacy.set_private(False)
        _close(a, b)


def test_the_dialog_and_the_strip_draw_what_the_window_says():
    _qapp()
    from dopeiptv.i18n import tr
    from dopeiptv.ui.together_ui import TogetherDialog, TogetherStrip, member_lines

    class Win:
        _together_name = ""

        def __init__(self) -> None:
            self.view = {"in": False, "private": False}
            self.calls: list = []

        def together_view(self):
            return self.view

        def together_start(self, name):
            self.calls.append(("start", name))
            return None

        def together_join(self, code, name):
            self.calls.append(("join", code, name))
            return tr("together_code_bad")

        def together_leave(self):
            self.calls.append(("leave",))

        def open_together(self):
            self.calls.append(("open",))

    win = Win()
    dlg = TogetherDialog(win)
    assert dlg.pages.currentIndex() == 0 and not dlg.start_btn.isEnabled()
    dlg.name.setText("Anna")
    assert dlg.start_btn.isEnabled() and not dlg.join_btn.isEnabled()
    dlg.code.setText("nope")
    dlg._join()
    assert win.calls[-1] == ("join", "nope", "Anna") and dlg.error.text() == tr("together_code_bad")
    win.view = {"in": False, "private": True}
    dlg.refresh()
    assert not dlg.start_btn.isEnabled() and not dlg.private_lbl.isHidden()
    win.view = {"in": True, "private": False, "code": "K7PQ-M2XD", "me": "Anna", "conn": "ok",
                "joined": False, "note": "", "members": [("<b>Bob</b>", "miss")], "title": "Dune (2021)",
                "live": False}
    dlg.refresh()
    assert dlg.pages.currentIndex() == 1 and dlg.code_lbl.text() == "K7PQ-M2XD"
    assert member_lines(win.view) == [tr("together_you", name="Anna"),
                                      tr("together_member_miss", name="<b>Bob</b>")]
    from PyQt6.QtCore import Qt
    assert dlg.members.textFormat() == Qt.TextFormat.PlainText
    dlg._leave()
    assert win.calls[-1] == ("leave",)
    strip = TogetherStrip(win)
    strip.refresh()
    assert not strip.isHidden() and strip.code.text() == "K7PQ-M2XD" and "Anna" in strip.names.text()
    win.view = {"in": False, "private": False}
    strip.refresh()
    assert strip.isHidden()
    dlg.deleteLater()
    strip.deleteLater()


def test_watch_together_sits_in_the_view_menu():
    _qapp()
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QMenuBar

    from dopeiptv.ui import app_menu
    bar = QMenuBar()
    acts = {n: QAction(n, bar) for n in ("multiview", "cast", "together", "about")}
    menus = app_menu.lay_out(bar, acts, mac=True)
    assert [a.text() for a in menus["view"].actions()] == ["multiview", "cast", "together"]
