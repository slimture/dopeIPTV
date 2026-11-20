"""Watch together: the code, the room id and the key; sealed messages and
their checks (strict frames, a member's own key and signature, old frames
handed again, what a hostile member can cause); the relay's clock; the room's bookkeeping; keeping in step
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


def test_a_code_is_ten_characters_nobody_can_misread():
    seen = set()
    for _ in range(200):
        code = together.new_code()
        assert len(code) == 11 and code[5] == "-"
        raw = code.replace("-", "")
        assert all(ch in together.ALPHABET for ch in raw)
        seen.add(code)
    assert len(seen) > 190
    for lookalike in "01OIL":
        assert lookalike not in together.ALPHABET


def test_a_code_as_typed_or_pasted():
    assert together.parse_code("k7pqm-2xdh9") == "K7PQM-2XDH9"
    assert together.parse_code(" K7PQM 2XDH9 ") == "K7PQM-2XDH9"
    assert together.parse_code("K7PQM2XDH9") == "K7PQM-2XDH9"
    for bad in ("", "K7PQM-2XDH", "K7PQM-2XDH9Z", "K0PQM-2XDH9", "K7PQM-2XDHI", None, "ÅÄÖ-1234",
                "K7PQ-M2XD"):
        assert together.parse_code(bad) is None, bad


def test_room_id_and_key_come_from_the_code_alone():
    room, key = together.derive("K7PQM-2XDH9")
    assert len(room) == 64 and all(c in "0123456789abcdef" for c in room)
    assert len(key) == 32 and key.hex() != room
    assert together.derive("k7pqm 2xdh9") == (room, key)        # as typed
    other_room, other_key = together.derive("K7PQM-2XDHA")
    assert other_room != room and other_key != key
    with pytest.raises(ValueError):
        together.derive("nonsense")


# -- sealed messages ----------------------------------------------------------------

ROOM, KEY = together.derive("ABCDE-FGHJK")


def test_a_message_sealed_opens_only_in_its_room():
    msg = {"k": "hi", "f": "ab" * 20, "n": 1, "t": 1, "name": "Anna"}
    blob = together.seal(KEY, ROOM, msg)
    assert together.open_frame(KEY, ROOM, blob) == msg
    other_room, other_key = together.derive("ABCDE-FGHJM")
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
          "p": True, "x": 12.5, "t": 1000, "v": 3, "b": "cd" * 20}
    st.update(kw)
    return st


def test_messages_are_checked_field_by_field():
    good = {"k": "st", "f": "ab" * 20, "n": 4, "t": 2000, "st": _state_wire()}
    msg = together.parse(good)
    assert msg is not None and msg.kind == "st" and msg.state is not None
    assert msg.state.media == together.Media("movie", "Dune (2021)", "Dune", 2021, 438631)
    assert msg.state.pos == 12.5 and msg.state.playing and msg.state.ver == 3
    for bad in ({**good, "k": "evil"}, {**good, "f": "XYZ"}, {**good, "n": 0}, {**good, "n": "4"},
                {**good, "t": None}, {**good, "st": _state_wire(x=-1)}, {**good, "st": _state_wire(p=1)},
                {**good, "st": _state_wire(m={"k": "song", "n": "x"})}, {**good, "st": "x"},
                {**good, "st": _state_wire(v=True)}, None, [1, 2]):
        assert together.parse(bad) is None, bad
    hi = together.parse({"k": "hi", "f": "ab" * 20, "n": 1, "t": 5,
                         "name": "  <b>Anna</b>\n\x07 Svensson with a very long surname indeed ",
                         "re": True, "s": "weird"})
    assert hi is not None and hi.answer and hi.status == ""
    assert "<" not in hi.name and ">" not in hi.name and "\n" not in hi.name
    assert len(hi.name) <= together.NAME_MAX and hi.name.startswith("‹b›Anna")


def test_a_state_says_where_the_room_is_and_which_is_newer():
    m = together.Media("movie", "Dune")
    st = together.PlayState(m, True, 100.0, 10_000, 2, "aa" * 20)
    assert st.position_at(15_000) == 105.0
    assert st.position_at(5_000) == 100.0            # never backwards
    paused = together.PlayState(m, False, 100.0, 10_000, 3, "aa" * 20)
    assert paused.position_at(99_000) == 100.0
    assert paused.newer_than(st) and not st.newer_than(paused) and st.newer_than(None)
    tie = together.PlayState(m, True, 0.0, 10_000, 2, "bb" * 20)
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
    """A message as open_message hands it on: its signature checked."""
    import dataclasses
    d = {"k": kind, "f": sender, "n": n, "t": t}
    d.update(kw)
    msg = together.parse(d)
    return dataclasses.replace(msg, signed=True) if msg is not None else None


def test_who_is_in_the_room():
    clock = _Clock()
    room = together.Room("ABCDE-FGHJK", "aa" * 20, "Me", clock)
    bob = "bb" * 20
    ev = room.receive(_msg("hi", bob, 1, clock.ms, name="Bob"), now=50.0)
    assert [k for k, _ in ev] == ["joined", "hello"] and room.names() == ["Bob"]
    assert room.receive(_msg("hi", bob, 1, clock.ms, name="Bob"), now=50.0) == []   # seen
    ev = room.receive(_msg("ka", bob, 2, clock.ms, name="Bobby", s="miss"), now=60.0)
    assert [k for k, _ in ev] == ["status"] and room.members[bob].name == "Bobby"
    ev = room.receive(_msg("hi", bob, 3, clock.ms, name="Bobby", re=True), now=61.0)
    assert ev == []                                  # an answer asks for none
    assert room.receive(_msg("hi", "aa" * 20, 9, clock.ms, name="Me"), now=61.0) == []  # mine
    assert room.expire(61.0 + room.GONE_AFTER - 1) == []
    gone = room.expire(61.0 + room.GONE_AFTER + 1)
    assert [m.name for m in gone] == ["Bobby"] and room.members == {}
    cleo = "cc" * 20
    room.receive(_msg("hi", cleo, 1, clock.ms, name="Cleo"), now=70.0)
    ev = room.receive(_msg("bye", cleo, 2, clock.ms), now=71.0)
    assert ev[0][0] == "left" and room.members == {}
    # From the room's history: someone long quiet is not "in".
    old = clock.ms - int((room.GONE_AFTER + 10) * 1000)
    assert room.receive(_msg("hi", "dd" * 20, 1, old, name="Dan"), now=80.0) == []


def test_the_newest_state_wins_and_publishing_moves_the_version_on():
    clock = _Clock()
    me = "aa" * 20
    room = together.Room("ABCDE-FGHJK", me, "Me", clock)
    film = together.Media("movie", "Dune", "Dune", 2021)
    sent = room.publish(film, True, 30.0)
    assert sent["k"] == "st" and room.state.ver == 1 and room.state.by == me
    older = _state_wire(v=1, t=clock.ms - 1, b="bb" * 20)
    assert room.receive(_msg("st", "bb" * 20, 1, clock.ms, st=older), now=1.0) == []
    newer = _state_wire(v=2, t=clock.ms + 10, b="bb" * 20, x=40.0, p=False)
    ev = room.receive(_msg("st", "bb" * 20, 2, clock.ms, st=newer), now=1.0)
    assert ev[-1][0] == "state" and room.state.pos == 40.0 and not room.state.playing
    room.publish(film, True, 41.0)
    assert room.state.ver == 3
    # A state "from the future" (a wrong clock) is taken as now.
    future = _state_wire(v=4, t=clock.ms + 3_600_000, b="bb" * 20)
    room.receive(_msg("st", "bb" * 20, 3, clock.ms, st=future), now=2.0)
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
    """A link for *me* - an Identity, so all it sends is signed, or an id
    (it then sends unsigned, which nobody takes)."""
    clock = together.Clock()
    ident = me if isinstance(me, together.Identity) else None
    me = ident.id if ident is not None else me
    link = together.Link(room, key, me, clock, on_message=got.append,
                         on_status=lambda k, n: status.append((k, n)),
                         relay_for=lambda r, m: FakeRelay(hub, m), identity=ident)
    link.start()
    # In the room once its first request is answered: what the relay held
    # before that is not delivered.
    assert _wait(lambda: link._polled.is_set() or status)
    return link, clock


def test_two_links_pass_sealed_messages_through_the_relay():
    hub = Hub()
    a_got, b_got, a_st, b_st = [], [], [], []
    anna, bob = together.new_identity(), together.new_identity()
    a, b = anna.id, bob.id
    link_a, clock_a = _link(hub, ROOM, KEY, anna, a_got, a_st)
    link_b, _ = _link(hub, ROOM, KEY, bob, b_got, b_st)
    try:
        room_a = together.Room("ABCDE-FGHJK", a, "Anna", clock_a)
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
    link_a, _ = _link(hub, ROOM, KEY, "a1" * 20, [], [])
    try:
        assert _wait(lambda: "a1" * 20 in hub.members)
        link_b, _ = _link(hub, ROOM, KEY, "b2" * 20, got, status)
        try:
            assert _wait(lambda: any(k == "full" for k, _n in status))
        finally:
            link_b.close()
    finally:
        link_a.close()


def test_what_another_key_sealed_is_not_delivered():
    hub = Hub()
    got = []
    link, _ = _link(hub, ROOM, KEY, "a1" * 20, got, [])
    try:
        other_room, other_key = together.derive("ZZZZZ-ZZZZZ")
        stray = together.seal(other_key, ROOM, {"k": "hi", "f": "c3" * 20, "n": 1, "t": 1})
        FakeRelay(hub, "c3" * 20).post(stray)
        FakeRelay(hub, "c3" * 20).post(b"\x01" + os.urandom(60))
        cleo = together.new_identity()
        unsigned = together.seal(KEY, ROOM, {"k": "hi", "f": cleo.id, "n": 1, "t": 1, "name": "Not"})
        FakeRelay(hub, cleo.id).post(unsigned)
        good = together.seal(KEY, ROOM, {"k": "hi", "f": cleo.id, "n": 2, "t": 1, "name": "Cleo"}, cleo)
        FakeRelay(hub, cleo.id).post(good)
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
    links = [w._together_link for w in wins]
    for w in wins:
        w.together_leave(quiet=True)
        w.pool.waitForDone(3000)
    # The links' threads end before the windows go: one still telling a
    # window that was gone of its news crashed the suite under load.
    for link in links:
        for t in getattr(link, "_threads", ()):
            t.join(10)
            assert not t.is_alive(), "a together link's thread did not end"


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
    win.view = {"in": True, "private": False, "code": "K7PQM-2XDH9", "me": "Anna", "conn": "ok",
                "joined": False, "note": "", "members": [("<b>Bob</b>", "miss")], "title": "Dune (2021)",
                "live": False}
    dlg.refresh()
    assert dlg.pages.currentIndex() == 1 and dlg.code_lbl.text() == "K7PQM-2XDH9"
    assert member_lines(win.view) == [tr("together_you", name="Anna"),
                                      tr("together_member_miss", name="<b>Bob</b>")]
    from PyQt6.QtCore import Qt
    assert dlg.members.textFormat() == Qt.TextFormat.PlainText
    dlg._leave()
    assert win.calls[-1] == ("leave",)
    strip = TogetherStrip(win)
    strip.refresh()
    assert not strip.isHidden() and strip.code.text() == "K7PQM-2XDH9" and "Anna" in strip.names.text()
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


def test_an_old_code_of_eight_joins_nothing():
    """1.6.7's codes of 8 (about 40 bits, fewer rounds) are no code: none
    is derived from one, and is_old_code tells it from a typo."""
    for old in ("K7PQ M2XD", "k7pq-m2xd", "K7PQM2XD"):
        assert together.parse_code(old) is None, old
        assert together.is_old_code(old), old
        with pytest.raises(ValueError):
            together.derive(old)
    assert together.parse_code("k7pqm2xdh9") == "K7PQM-2XDH9"
    for bad in ("K7PQM-2XDH", "K7PQM-2XDH9Z", "K7PQM-2XDH0", "K7PQ-M2X", "K0PQ-M2XD"):
        assert together.parse_code(bad) is None and not together.is_old_code(bad), bad
    assert not together.is_old_code("K7PQM-2XDH9") and not together.is_old_code("nonsense")


def test_the_relay_goes_over_https_only_and_follows_no_redirect():
    """A room's messages go to the relay named or nowhere: never to an
    address that is not https (this computer's own for the tests), never
    after a redirect."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    for url in ("http://dopeiptv.org/together/", "ftp://dopeiptv.org/together/"):
        relay = together.Relay("ab" * 32, "c3" * 20, endpoint=url)
        with pytest.raises(together.RelayError) as err:
            relay.post(b"x" * 40)
        assert err.value.kind == "net"
        assert relay._session is None                    # no connection was made

    elsewhere = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path.startswith("/elsewhere"):
                elsewhere.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"now": 1}')
                return
            self.send_response(307)
            self.send_header("Location", "/elsewhere" + self.path)
            self.send_header("Content-Length", "0")
            self.end_headers()

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        relay = together.Relay("ab" * 32, "c3" * 20,
                               endpoint=f"http://127.0.0.1:{httpd.server_address[1]}/together/")
        with pytest.raises(together.RelayError):
            relay.post(b"x" * 40)
        relay.close()
        assert not elsewhere
    finally:
        httpd.shutdown()
        httpd.server_close()


# -- hardening: strict frames, who said it, old frames, bounds ---------------------------


def _seal_plain(plain: bytes) -> bytes:
    """A frame with *plain* as its padded text, sealed as the room seals."""
    nonce = os.urandom(12)
    head = bytes([together.FORMAT])
    return head + nonce + together.aead.encrypt(
        KEY, nonce, plain, together._AAD + head + bytes.fromhex(ROOM))


def test_a_frame_is_exactly_as_seal_makes_it():
    text = json.dumps({"k": "hi", "f": "ab" * 20, "n": 1, "t": 1}).encode()
    head = len(text).to_bytes(2, "big")
    good = head + text + b"\0" * (192 - 2 - len(text))
    assert together.open_frame(KEY, ROOM, _seal_plain(good)) is not None
    # Something other than zeros after the text: refused.
    for at in (2 + len(text), 100, 191):
        dirty = bytearray(good)
        dirty[at] = 1
        assert together.open_frame(KEY, ROOM, _seal_plain(bytes(dirty))) is None, at
    # A size that is none of the padded ones: refused, shorter or longer.
    assert together.open_frame(KEY, ROOM, _seal_plain(head + text)) is None
    assert together.open_frame(KEY, ROOM, _seal_plain(good + b"\0")) is None
    assert together.open_frame(KEY, ROOM, _seal_plain(good[:-1])) is None
    # A length that reaches past the frame, and text that is no JSON object.
    assert together.open_frame(KEY, ROOM, _seal_plain(b"\xff\xff" + good[2:])) is None
    assert together.open_frame(KEY, ROOM, _seal_plain(b"\0\x02[]" + b"\0" * 188)) is None
    # Every size seal() makes passes.
    for n in (1, 150, 300, 700, 1400):
        assert together.open_frame(KEY, ROOM, together.seal(KEY, ROOM, {"x": "a" * n})) is not None


def test_a_member_has_a_key_of_its_own_and_its_id_is_the_keys_hash():
    me, other = together.new_identity(), together.new_identity()
    assert len(me.id) == together.ID_LEN == 40 and me.id != other.id
    assert me.id == together.member_of(me.public)
    # What the relay asks of a member id (donate/together.py).
    import re
    assert re.fullmatch(r"[0-9a-f]{16,64}", me.id)
    assert me.seed.hex() not in repr(me)
    msg = {"k": "hi", "f": me.id, "n": 1, "t": 5, "name": "Anna"}
    got = together.open_message(KEY, ROOM, together.seal(KEY, ROOM, msg, me))
    assert got is not None and got.signed and got.sender == me.id and got.name == "Anna"
    # Signing for an id that is not the key's is not done.
    with pytest.raises(ValueError):
        together.seal(KEY, ROOM, dict(msg, f=other.id), me)
    with pytest.raises(ValueError):
        together.Link(ROOM, KEY, other.id, together.Clock(), on_message=lambda m: None,
                      on_status=lambda k, n: None, identity=me)


def test_nobody_speaks_under_another_members_id():
    anna, mallory = together.new_identity(), together.new_identity()
    msg = {"k": "st", "f": anna.id, "n": 9, "t": 5, "st": _state_wire(b=anna.id)}
    # No signature at all (what any holder of the room's key could write).
    assert together.open_message(KEY, ROOM, together.seal(KEY, ROOM, msg)) is None
    # Mallory's key and signature under Anna's id.
    text = together.encode(dict(msg, f=mallory.id), ROOM, mallory)
    forged = text.replace(mallory.id.encode(), anna.id.encode())
    assert together.parse(json.loads(forged)).sender == anna.id
    assert together.open_message(KEY, ROOM, together._seal_text(KEY, ROOM, forged)) is None
    # Anna's own key shown, the signature Mallory's.
    theirs = json.loads(text)
    theirs["f"] = anna.id
    theirs["st"]["b"] = anna.id
    import base64
    theirs["pk"] = base64.b64encode(anna.public).decode()
    relabelled = json.dumps(theirs, separators=(",", ":")).encode()
    assert together.open_message(KEY, ROOM, together._seal_text(KEY, ROOM, relabelled)) is None
    # Anna's real message with one field changed, or carried to another room.
    real = together.encode(msg, ROOM, anna)
    assert together.open_message(KEY, ROOM, together._seal_text(KEY, ROOM, real)).signed
    changed = real.replace(b'"x":12.5', b'"x":99.5')
    assert changed != real
    assert together.open_message(KEY, ROOM, together._seal_text(KEY, ROOM, changed)) is None
    room2, key2 = together.derive("ABCDE-FGHJM")
    assert together.open_message(key2, room2, together._seal_text(key2, room2, real)) is None
    # A signature not at the very end, or cut short.
    moved = real[:-1] + b',"z":1}'
    assert together.open_message(KEY, ROOM, together._seal_text(KEY, ROOM, moved)) is None
    assert together.open_message(KEY, ROOM, together._seal_text(KEY, ROOM, real[:-12] + b'"}')) is None


def test_a_member_on_1_6_8_is_not_heard():
    """An unsigned message (1.6.8's: 16 hex, no key, no signature) is no
    message: whoever sent it could have written any id."""
    old = {"k": "st", "f": "ab" * 8, "n": 3, "t": 5, "st": _state_wire(b="ab" * 8)}
    assert together.open_message(KEY, ROOM, together.seal(KEY, ROOM, old)) is None
    assert together.parse(old) is None, "a 16-hex id is nobody's"
    # Ids of any other length than a key's hash are nobody's.
    for n in (4, 7, 8, 12, 24, 32):
        assert together.parse({"k": "ka", "f": "ab" * n, "n": 1, "t": 1}) is None, n
    assert together.parse({"k": "ka", "f": 1234567812345678, "n": 1, "t": 1}) is None


class _Lines:
    """What the app's logger says while it is held, at info and up."""

    def __enter__(self):
        import logging

        from dopeiptv.core.log import log
        self.records: list = []
        outer = self

        class Catch(logging.Handler):
            def emit(self, record):
                outer.records.append(record)
        self._log, self._handler, self._level = log, Catch(), log.level
        log.addHandler(self._handler)
        log.setLevel(logging.INFO)
        return self

    def __exit__(self, *exc):
        self._log.removeHandler(self._handler)
        self._log.setLevel(self._level)

    def saying(self, words: str) -> list:
        return [r for r in self.records if words in r.getMessage()]


def test_an_unsigned_message_never_speaks_for_anyone():
    """Unsigned under a key's id: no message, and the room takes none that
    came another way; the holder's own signed one is taken as theirs."""
    anna, me = together.new_identity(), together.new_identity()
    blob = together.seal(KEY, ROOM, {"k": "hi", "f": anna.id, "n": 1, "t": 5, "name": "Anna"})
    assert together.open_message(KEY, ROOM, blob) is None
    clock = _Clock()
    room = together.Room("ABCDE-FGHJK", me.id, "Me", clock)
    unsigned = together.parse({"k": "hi", "f": anna.id, "n": 1, "t": clock.ms, "name": "Anna"})
    assert unsigned is not None and not unsigned.signed
    with _Lines() as lines:
        assert room.receive(unsigned, now=10.0) == []
    assert room.members == {} and room.state is None and not lines.records
    signed = together.open_message(KEY, ROOM, together.seal(
        KEY, ROOM, {"k": "st", "f": anna.id, "n": 3, "t": clock.ms,
                    "st": _state_wire(b=anna.id, v=8, t=clock.ms)}, anna))
    assert signed is not None and signed.signed
    room.receive(signed, now=13.0)
    assert room.state.by == anna.id and room.state.ver == 8


def test_the_longest_names_still_fit_signed():
    me = together.new_identity()
    room = together.Room("ABCDE-FGHJK", me.id, "Ж" * together.NAME_MAX, _Clock())
    long = "Ж" * together.TEXT_MAX
    room.publish(together.Media("episode", long, long, 2021, 438631, 12, 345, long), True, 9999.999)
    assert len(together.seal(KEY, ROOM, room.hello(answer=True), me)) <= together.MAX_FRAME


class _Known(_Clock):
    """A clock that knows the relay's time: *ms* now, *joined* when the
    room was made."""

    def __init__(self, ms: int = 10_000_000, joined: int | None = None) -> None:
        super().__init__(ms)
        self.joined = ms if joined is None else joined

    known = True

    def now_ms(self, mono=None) -> int:
        return self.ms if mono is None else self.joined


def test_an_old_frame_handed_again_after_a_rejoin_is_ignored():
    bob = "bb" * 20
    clock = _Known()
    room = together.Room("ABCDE-FGHJK", "aa" * 20, "Me", clock)
    seek = _msg("st", bob, 7, clock.ms, st=_state_wire(v=5, b=bob, x=600.0, t=clock.ms))
    assert room.receive(seek, now=1.0)[-1][0] == "state"
    assert room.receive(seek, now=2.0) == []                  # the same again: its number is known
    # Rejoined an hour's film later: nothing is remembered of Bob, and the
    # old seek is handed again. It is from before this stay in the room.
    later = _Known(clock.ms + 240_000)
    again = together.Room("ABCDE-FGHJK", "a1" * 20, "Me", later)
    assert again.receive(seek, now=1.0) == [] and again.state is None and again.members == {}
    assert again._last_n == {}
    # What Bob says now is taken.
    now_msg = _msg("st", bob, 8, later.ms, st=_state_wire(v=6, b=bob, t=later.ms))
    assert again.receive(now_msg, now=2.0)[-1][0] == "state"


def test_a_message_out_of_its_time_is_ignored_and_a_normal_one_never():
    bob = "bb" * 20
    clock = _Known(joined=10_000_000 - 3_600_000)           # in the room for an hour
    room = together.Room("ABCDE-FGHJK", "aa" * 20, "Me", clock)
    n = iter(range(1, 100))

    def counted(t: int) -> bool:
        k = next(n)
        room.receive(_msg("ka", bob, k, t, name="Bob"), now=1.0)
        return room._last_n.get(bob, (0, 0.0))[0] == k

    assert counted(clock.ms)                                  # now
    assert counted(clock.ms - 26_000)                         # held a long poll's time
    assert counted(clock.ms - 90_000)                         # three slow tries at sending
    assert counted(clock.ms + 4_000)                          # clocks a few seconds apart
    assert not counted(clock.ms - together.STALE_MS - 1)
    assert not counted(clock.ms + together.AHEAD_MS + 1)
    # Just joined: only what is from before the join (and some slack) goes.
    fresh = _Known(joined=10_000_000 - 1_000)
    room = together.Room("ABCDE-FGHJK", "aa" * 20, "Me", fresh)
    assert counted(fresh.ms - 20_000)
    assert not counted(fresh.ms - 1_000 - together.JOIN_SLACK_MS - 1)
    # Until the relay's clock is known nothing can be said - nothing is refused.
    room = together.Room("ABCDE-FGHJK", "aa" * 20, "Me", _Clock())
    assert counted(5) and counted(10 ** 15)


def test_the_join_moment_follows_the_relays_clock_once_known():
    mono = [100.0]
    clock = together.Clock(mono=lambda: mono[0], wall=lambda: 5.0)     # a wall clock far off
    room = together.Room("ABCDE-FGHJK", "aa" * 20, "Me", clock)
    mono[0] = 102.0
    clock.sample(101.9, 2_000_000_000, 102.0)
    assert abs(clock.now_ms(room._born) - (2_000_000_000 - 1_950)) <= 2
    bob = "bb" * 20
    assert room.receive(_msg("hi", bob, 1, 2_000_000_000 - 500, name="Bob"), now=1.0)
    assert room.receive(_msg("hi", bob, 2, 2_000_000_000 - 120_000, name="Bob"), now=1.0) == []
    assert room._last_n[bob][0] == 1


def test_what_a_hostile_member_can_cause_is_bounded():
    clock = _Known()
    me = "aa" * 20
    room = together.Room("ABCDE-FGHJK", me, "Me", clock)
    # More "members" than a room has: not listed, not remembered without end.
    for i in range(500):
        room.receive(_msg("hi", f"{i:040x}", 1, clock.ms, name=f"M{i}"), now=1.0)
    assert len(room.members) == together.MEMBERS_MAX
    assert len(room._last_n) == together.SENDERS_MAX
    # Senders long silent make room again - not the ones in the room.
    late = 1.0 + (together.STALE_MS + together.AHEAD_MS) / 1000.0 + 1.0
    assert room.receive(_msg("ka", "ee" * 20, 1, clock.ms, name="Eve"), now=late) == []   # the room is full
    assert "ee" * 20 in room._last_n and len(room._last_n) == together.MEMBERS_MAX + 1
    assert set(room.members) <= set(room._last_n)

    room = together.Room("ABCDE-FGHJK", me, "Me", clock)
    bob, cleo = "bb" * 20, "cc" * 20
    # A hello is answered, a flood of them once in a while.
    asked = 0
    for i in range(1, 51):
        ev = room.receive(_msg("hi", bob, i, clock.ms, name="Bob"), now=10.0 + i * 0.01)
        asked += sum(1 for k, _ in ev if k == "hello")
    assert asked == 1
    ev = room.receive(_msg("hi", bob, 60, clock.ms, name="Bob"), now=10.0 + together.HELLO_EVERY + 1)
    assert [k for k, _ in ev] == ["hello"]
    # A choice told by someone other than who made it is not a choice.
    ev = room.receive(_msg("st", bob, 61, clock.ms, st=_state_wire(v=2, b=cleo, t=clock.ms)), now=20.0)
    assert ev == [] and room.state is None
    assert room.receive(_msg("st", bob, 62, clock.ms, st=_state_wire(v=2, b=bob, t=clock.ms)),
                        now=20.0)[-1][0] == "state"
    # A hello may carry what someone else chose (that is how a joiner learns it).
    ev = room.receive(_msg("hi", cleo, 1, clock.ms, name="Cleo", re=True,
                           st=_state_wire(v=3, b=bob, t=clock.ms)), now=20.0)
    assert ev[-1][0] == "state" and room.state.ver == 3
    # A version nobody could choose past is not taken; the next one is.
    top = _state_wire(v=1 << 53, b=bob, t=clock.ms)
    assert room.receive(_msg("st", bob, 63, clock.ms, st=top), now=21.0) == [] and room.state.ver == 3
    room.receive(_msg("st", bob, 64, clock.ms, st=_state_wire(v=4, b=bob, t=clock.ms)), now=21.0)
    assert room.state.ver == 4
    # Positions: a number, finite, in range - as JSON may spell others.
    for bad in ("NaN", "Infinity", "-Infinity", "-1", "1e400", "1e7", '"12"', "true", "null"):
        wire = json.loads('{"x": %s}' % bad)
        assert together.PlayState.from_wire(_state_wire(**wire)) is None, bad
    for huge in ("1e400", "NaN"):
        d = json.loads('{"k":"ka","f":"%s","n":%s,"t":5}' % (bob, huge))
        assert together.parse(d) is None
    # A name is one short line whatever was sent.
    got = together.parse({"k": "hi", "f": bob, "n": 1, "t": 1, "name": "x" * 5000 + "\n<b>"})
    assert len(got.name) == together.NAME_MAX


def test_a_paused_room_never_seeks_past_the_titles_end():
    fix = together.Corrector()
    got = fix.decide(100.0, False, 9_999_999.0, False, now=10.0, duration=7200.0)
    assert got.action == "seek" and got.value <= 7200.0
    # There, it stays: no seek again and again.
    assert fix.decide(got.value, False, 9_999_999.0, False, now=20.0, duration=7200.0) == together.NONE
    # Within the title nothing changed.
    fix = together.Corrector()
    assert fix.decide(103.0, False, 100.0, False, now=14.0, duration=7200.0) == together.Fix("seek", 100.0)


def test_the_app_makes_no_old_code_and_says_why_one_fails():
    assert all(len(together.new_code().replace("-", "")) == together.CODE_LEN for _ in range(50))
    _qapp()
    from dopeiptv.i18n import tr
    Win = _window_class()
    hub = Hub()
    w = Win(FakeClient(), hub)
    try:
        assert w.together_join("K7PQ-M2XD", "Anna") == tr("together_code_old")
        assert tr("together_code_old") != "together_code_old"
        assert w._together_room is None
        assert w.together_join("K7PQ-M2X", "Anna") == tr("together_code_bad")
        assert w.together_start("Anna") is None
        assert "old_code" not in w.together_view()
        assert len(w._together_room.me) == together.ID_LEN
        assert _spin(lambda: w._together_link is not None)
        assert w._together_link.identity.id == w._together_room.me
    finally:
        _close(w)


def test_what_the_relay_held_before_joining_is_not_delivered():
    """A rejoin: the relay still holds the room's old messages (or hands
    them again). The link's first answer is only where to go on from."""
    hub = Hub()
    bob = together.new_identity()
    old_room = together.Room("ABCDE-FGHJK", bob.id, "Bob", together.Clock())
    film = together.Media("movie", "Dune", "Dune", 2021)
    FakeRelay(hub, bob.id).post(together.seal(KEY, ROOM, old_room.hello(), bob))
    FakeRelay(hub, bob.id).post(together.seal(KEY, ROOM, old_room.publish(film, True, 600.0), bob))
    got, status = [], []
    me = together.new_identity()
    clock = together.Clock()
    link = together.Link(ROOM, KEY, me.id, clock, on_message=got.append,
                         on_status=lambda k, n: status.append((k, n)),
                         relay_for=lambda r, m: FakeRelay(hub, m), identity=me)
    link.start()
    try:
        link.send(together.Room("ABCDE-FGHJK", me.id, "Me", clock).hello())
        assert _wait(lambda: len(hub.msgs) == 3)             # the hello went out, after the first answer
        assert got == []
        # What comes after is delivered, signed.
        FakeRelay(hub, bob.id).post(together.seal(KEY, ROOM, old_room.hello(answer=True), bob))
        assert _wait(lambda: got)
        assert got[0].kind == "hi" and got[0].signed and got[0].state is not None
        # ...and what this link sent is signed with its key, stamped by the relay's clock.
        mine = together.open_message(KEY, ROOM, hub.msgs[2][2])
        assert mine.signed and mine.sender == me.id
        assert abs(mine.t - int(time.time() * 1000)) < 5_000
        # A forged message under Bob's id never reaches the room.
        forged = dict(old_room.publish(film, False, 5.0))
        FakeRelay(hub, "c3" * 20).post(together.seal(KEY, ROOM, forged))
        FakeRelay(hub, bob.id).post(together.seal(KEY, ROOM, old_room.keepalive(), bob))
        assert _wait(lambda: len(got) == 2)
        assert [m.kind for m in got] == ["hi", "ka"]
    finally:
        link.close()


def test_sending_does_not_wait_for_ever_for_a_relay_that_does_not_answer():
    hub = Hub()

    class Deaf(FakeRelay):
        def poll(self, after, wait):
            raise together.RelayError("net")

    bob = together.new_identity()
    FakeRelay(hub, bob.id).post(together.seal(KEY, ROOM, {"k": "ka", "f": bob.id, "n": 1, "t": 5,
                                                          "name": "Bob"}, bob))
    deaf = [True]
    got = []
    link = together.Link(ROOM, KEY, "a1" * 20, together.Clock(), on_message=got.append,
                         on_status=lambda k, n: None,
                         relay_for=lambda r, m: Deaf(hub, m) if deaf[0] else FakeRelay(hub, m))
    link.FIRST_POLL_WAIT = 0.3
    link.BACKOFF = (0.2,)
    link.start()
    try:
        link.send({"k": "ka", "f": "a1" * 20, "n": 1, "t": 5, "name": "Anna"})
        assert _wait(lambda: len(hub.msgs) == 2)
        # Sent before any answer: the backlog is then read (the room's
        # rules of time judge it) - an answer to the hello may be in it.
        link._poll_relay.__class__ = FakeRelay
        assert _wait(lambda: got)
        assert got[0].name == "Bob"
    finally:
        link.close()


def test_back_after_a_while_without_the_relay_the_window_says_hello_again():
    """What was said while the relay could not be reached may be too old
    to be taken when it comes: the others' answers to a hello say it anew."""
    _qapp()
    Win = _window_class()
    hub = Hub()
    w = Win(FakeClient(), hub)
    try:
        assert w.together_start("Anna") is None
        assert _spin(lambda: w._together_link is not None and len(hub.msgs) == 1)
        lid = w._together_lid
        w._together_on_status(lid, "net", 0)
        w._together_on_status(lid, "ok", 1)                  # a blip: nothing more is said
        w._together_on_status(lid, "net", 0)
        w._together_down_at -= 120.0
        w._together_on_status(lid, "net", 0)                 # still down: the count goes on
        w._together_on_status(lid, "ok", 1)
        assert _spin(lambda: len(hub.msgs) == 2)
        room_id, key = together.derive(w._together_room.code)
        again = together.open_message(key, room_id, hub.msgs[1][2])
        assert again.kind == "hi" and not again.answer and again.signed
        time.sleep(0.2)
        assert len(hub.msgs) == 2
    finally:
        _close(w)
