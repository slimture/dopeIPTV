"""TS analysis (core/ts_analysis.py): a simple TR 101 290 over a transport
stream made here (tests/ts_gen.py) - each indicator caused on purpose and
counted, the clean stream counting nothing, the tables read, the bitrate
per PID, sync found again, scrambled PIDs, the jitter from arrival times,
the bounds, the speed, and the reader over a real rewind-buffer store."""

from __future__ import annotations

import os
import random
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ts_gen import PACKET, Gen, crc32_mpeg_slow, packet, pmt, section, table_packet  # noqa: E402

from dopeiptv.core import ts_analysis as ta  # noqa: E402


def analyse(data: bytes, chunk: int = 64 * 1024, a: ta.Analyzer | None = None) -> ta.Analyzer:
    a = a or ta.Analyzer()
    for i in range(0, len(data), chunk):
        a.feed(data[i:i + chunk])
    return a


def ind(snap: ta.Snapshot, key: str) -> int:
    return next(i.count for i in snap.indicators if i.key == key)


def pid(snap: ta.Snapshot, number: int) -> ta.PidInfo:
    return next(p for p in snap.pids if p.pid == number)


def counted(snap: ta.Snapshot) -> dict[str, int]:
    return {i.key: i.count for i in snap.indicators if i.count}


# -- the clean stream ---------------------------------------------------------------


def test_crc_matches_the_bitwise_one():
    rnd = random.Random(7)
    for n in (0, 1, 5, 64, 1021):
        data = bytes(rnd.getrandbits(8) for _ in range(n))
        assert ta.crc32_mpeg(data) == crc32_mpeg_slow(data)
    sec = section(0x00, 1, b"\x00\x01\xe1\x00")
    assert ta.crc32_mpeg(sec) == 0


def test_a_clean_stream_counts_nothing_and_reads_its_tables():
    g = Gen(bitrate=8e6)
    snap = analyse(g.seconds(6.0)).snapshot()
    assert snap.state == "running"
    assert counted(snap) == {}
    assert ta.verdict(snap) == (ta.OK, [])
    assert snap.service() == "Test One" and snap.provider() == "dope"
    assert snap.tsid == 0x42
    prog = snap.programs[0]
    assert (prog.number, prog.pmt_pid, prog.pcr_pid, prog.ca) == (1, 0x1000, 0x100, False)
    assert (pid(snap, 0x100).codec, pid(snap, 0x100).kind) == ("h264", "video")
    assert (pid(snap, 0x101).codec, pid(snap, 0x101).lang) == ("aac", "swe")
    assert pid(snap, 0x102).kind == "subtitles" and pid(snap, 0x102).codec == "dvbsub"
    assert pid(snap, 0x1000).codec == "pmt" and pid(snap, 0x0).codec == "pat"
    assert pid(snap, ta.NULL_PID).kind == "null"
    # A subtitle PID with nothing to say is no PID_error.
    assert pid(snap, 0x102).packets == 0


def test_bitrate_per_pid_and_in_all():
    g = Gen(bitrate=8e6, table_ms=100.0)
    snap = analyse(g.seconds(8.0)).snapshot()
    assert snap.bps == pytest.approx(8e6, rel=0.01)
    # PAT and PMT: ten packets a second each; SDT one.
    assert pid(snap, 0x0).bps == pytest.approx(10 * PACKET * 8, rel=0.05)
    assert pid(snap, 0x1000).bps == pytest.approx(10 * PACKET * 8, rel=0.05)
    assert pid(snap, 0x11).bps == pytest.approx(PACKET * 8, rel=0.25)
    total = sum(p.bps for p in snap.pids)
    assert total == pytest.approx(snap.bps, rel=0.001)
    assert pid(snap, 0x100).bps > pid(snap, 0x101).bps > 0
    assert 0.1 < snap.null_share < 0.3
    # Every 20 ms, a table packet before it now and then.
    assert snap.pcr_max == pytest.approx(20.0, abs=1.0)
    assert snap.pcr_avg == pytest.approx(20.0, abs=0.2)


def test_the_window_slides():
    g = Gen(bitrate=4e6)
    a = analyse(g.seconds(10.0))
    before = a.snapshot().bps
    # The same stream at twice the rate from here on: within the 5 s window.
    g.set_bitrate(8e6)
    analyse(g.seconds(6.0), a=a)
    assert a.snapshot().bps == pytest.approx(2 * before, rel=0.05)


# -- priority 1 -----------------------------------------------------------------------


def test_a_lost_packet_is_a_continuity_error():
    pkts = Gen().take(4000)
    video = [k for k, p in enumerate(pkts) if ((p[1] & 0x1F) << 8 | p[2]) == 0x100]
    del pkts[video[1000]]
    snap = analyse(b"".join(pkts)).snapshot()
    assert ind(snap, "cc") == 1
    assert pid(snap, 0x100).cc_errors == 1
    assert pid(snap, 0x101).cc_errors == 0
    state, behind = ta.verdict(snap)
    assert state == ta.ERROR and behind[0].name == "Continuity_count_error"


def test_a_packet_sent_twice_is_allowed_once():
    pkts = Gen().take(3000)
    k = next(k for k, p in enumerate(pkts) if k > 500 and ((p[1] & 0x1F) << 8 | p[2]) == 0x101)
    twice = pkts[:k + 1] + [pkts[k]] + pkts[k + 1:]
    assert ind(analyse(b"".join(twice)).snapshot(), "cc") == 0
    thrice = pkts[:k + 1] + [pkts[k], pkts[k]] + pkts[k + 1:]
    assert ind(analyse(b"".join(thrice)).snapshot(), "cc") == 1


def test_the_discontinuity_indicator_starts_the_count_anew():
    g = Gen()
    data = g.seconds(1.0)
    # A jump in the counter, said with the indicator: no error.
    flagged = packet(0x101, (g.cc[0x101] + 5) & 15, b"\x11" * 170, disc=True)
    g.cc[0x101] = (g.cc[0x101] + 5) & 15
    snap = analyse(data + flagged + g.seconds(1.0)).snapshot()
    assert ind(snap, "cc") == 0
    # The same jump unsaid: one.
    g2 = Gen()
    data = g2.seconds(1.0)
    jumped = packet(0x101, (g2.cc[0x101] + 5) & 15, b"\x11" * 184)
    g2.cc[0x101] = (g2.cc[0x101] + 5) & 15
    snap = analyse(data + jumped + g2.seconds(1.0)).snapshot()
    assert ind(snap, "cc") == 1


def test_adaptation_only_packets_leave_the_counter_alone():
    g = Gen()
    data = g.seconds(1.0)
    last = g.cc[0x100]
    stuffing = packet(0x100, last, adaptation_only=True)
    snap = analyse(data + stuffing + g.seconds(1.0)).snapshot()
    assert ind(snap, "cc") == 0


def test_pat_and_pmt_too_far_apart():
    snap = analyse(Gen(table_ms=700.0).seconds(6.0)).snapshot()
    assert ind(snap, "pat") >= 7
    assert ind(snap, "pmt") >= 7
    clean = analyse(Gen(table_ms=400.0).seconds(6.0)).snapshot()
    assert ind(clean, "pat") == 0 and ind(clean, "pmt") == 0


def test_a_pat_that_stops_is_counted_once_while_it_stays_away():
    g = Gen()
    data = g.seconds(2.0)
    g.skip.add("pat")
    away = g.seconds(3.0)
    assert ind(analyse(data + away).snapshot(), "pat") == 1
    # Back again: the late one is the same error, not a second.
    g.skip.discard("pat")
    assert ind(analyse(data + away + g.seconds(2.0)).snapshot(), "pat") == 1


def test_a_scrambled_pat_and_the_wrong_table_on_pid_0():
    g = Gen()
    data = g.seconds(1.0)
    bad = table_packet(0, g._cc(0), section(0x42, 1, b"\x00\x01\xff"))
    scr = bytearray(table_packet(0, g._cc(0), g.pat))
    scr[3] |= 0x80
    snap = analyse(data + bad + bytes(scr) + g.seconds(1.0)).snapshot()
    assert ind(snap, "pat") == 2


def test_a_pid_the_pmt_names_that_never_comes():
    g = Gen()
    g.pmt = pmt(1, Gen.VIDEO, [(0x1B, Gen.VIDEO, b""), (0x0F, Gen.AUDIO, b""),
                               (0x04, 0x105, b"")])
    snap = analyse(g.seconds(8.0)).snapshot()
    assert ind(snap, "pid") == 1
    assert pid(snap, 0x105).packets == 0 and pid(snap, 0x105).codec == "mp2"


def test_sync_lost_and_found_again():
    pkts = Gen().take(5000)
    # One wrong sync byte: an error, the sync kept.
    one = list(pkts)
    one[2000] = b"\x00" + one[2000][1:]
    snap = analyse(b"".join(one)).snapshot()
    assert (ind(snap, "sync_byte"), ind(snap, "sync_loss")) == (1, 0)
    # Two in a row: the sync lost, found again five packets on.
    two = list(pkts)
    two[2000] = b"\x00" + two[2000][1:]
    two[2001] = b"\x00" + two[2001][1:]
    snap = analyse(b"".join(two)).snapshot()
    assert ind(snap, "sync_loss") == 1 and ind(snap, "sync_byte") == 2
    assert snap.packets >= 4990
    # Bytes lost in the middle of a packet: found again, the counters go on.
    cut = b"".join(pkts[:3000]) + b"".join(pkts[3000:])[77:]
    snap = analyse(cut, chunk=10_000).snapshot()
    assert ind(snap, "sync_loss") == 1
    assert snap.packets > 4900
    assert snap.state == "running"


def test_not_a_transport_stream():
    rnd = random.Random(3)
    noise = bytes(rnd.getrandbits(8) for _ in range(ta.NOT_TS_BYTES + 10_000))
    assert analyse(noise).snapshot().state == "not_ts"
    hls = b"#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:6.0,\nseg1.ts\n" * 20
    assert analyse(hls).snapshot().state == "hls"
    assert ta.verdict(analyse(hls).snapshot())[0] == ta.WAITING


# -- priority 2 ------------------------------------------------------------------------


def test_transport_error_indicator():
    g = Gen()
    data = g.seconds(1.0)
    damaged = packet(0x100, 0, b"\x00" * 184, tei=True)
    snap = analyse(data + damaged + damaged + g.seconds(1.0)).snapshot()
    assert ind(snap, "transport") == 2
    # A damaged packet is not a lost one: the counter goes on past it.
    assert ind(snap, "cc") == 0


def test_a_table_with_a_bad_checksum():
    g = Gen()
    data = g.seconds(1.0)
    broken = bytearray(g.pmt)
    broken[-6] ^= 0x01
    snap = analyse(data + table_packet(Gen.PMT_PID, g._cc(Gen.PMT_PID), bytes(broken))
                   + g.seconds(1.0)).snapshot()
    assert ind(snap, "crc") == 1


def test_pcr_too_far_apart():
    snap = analyse(Gen(pcr_ms=60.0).seconds(3.0)).snapshot()
    assert ind(snap, "pcr_rep") >= 40
    assert ind(snap, "pcr_disc") == 0
    assert snap.pcr_max == pytest.approx(60.0, abs=0.5)
    assert ta.verdict(snap)[0] == ta.WARN


def test_a_pcr_jump_with_and_without_the_indicator():
    g = Gen()
    data = g.seconds(1.0)
    g.pcr_start += 27_000_000 * 5                    # five seconds on
    jump = g.seconds(1.0)
    assert ind(analyse(data + jump).snapshot(), "pcr_disc") == 1
    g2 = Gen()
    data = g2.seconds(1.0)
    g2.pcr_start += 27_000_000 * 5
    first = packet(Gen.VIDEO, g2._cc(Gen.VIDEO), b"\x00" * 100, pcr=g2.pcr_at(g2.now()), disc=True)
    snap = analyse(data + first + g2.seconds(1.0)).snapshot()
    assert ind(snap, "pcr_disc") == 0 and ind(snap, "cc") == 0


def test_the_pcr_wraps_round_without_an_error():
    start = (1 << 33) * 300 - 27_000_000            # a second before the wrap
    snap = analyse(Gen(pcr_start=start).seconds(3.0)).snapshot()
    assert counted(snap) == {}
    assert snap.seconds == pytest.approx(3.0, abs=0.1)


def test_pts_too_far_apart():
    g = Gen()
    data = g.seconds(2.0)
    # No PES start on the picture or the sound for a second.
    g._next["vpes"] = g._next["apes"] = g.now() + 1.0
    snap = analyse(data + g.seconds(3.0)).snapshot()
    assert ind(snap, "pts") >= 2


def test_scrambled_pids_and_no_cat():
    g = Gen()
    out = []
    for p in g.take(12000):
        if ((p[1] & 0x1F) << 8 | p[2]) in (0x100, 0x101) and p[3] & 0x10:
            p = p[:3] + bytes([p[3] | 0x80]) + p[4:]
        out.append(p)
    snap = analyse(b"".join(out)).snapshot()
    assert pid(snap, 0x100).scrambled and pid(snap, 0x101).scrambled
    assert not pid(snap, 0x0).scrambled
    assert ind(snap, "cat") >= 1
    assert ind(snap, "pts") == 0                     # a scrambled PES is not read
    # With a CAT, scrambled is no CAT_error.
    g2 = Gen()
    cat = table_packet(1, 0, section(0x01, 0xFFFF, b""))
    out = [cat]
    for p in g2.take(12000):
        if ((p[1] & 0x1F) << 8 | p[2]) == 0x100 and p[3] & 0x10:
            p = p[:3] + bytes([p[3] | 0xC0]) + p[4:]
        out.append(p)
    snap = analyse(b"".join(out)).snapshot()
    assert ind(snap, "cat") == 0 and pid(snap, 0x100).scrambled


def test_jitter_from_arrival_times():
    clock = [1000.0]
    g = Gen(bitrate=4e6)
    a = ta.Analyzer(clock=lambda: clock[0])
    # Steady: each piece of the stream arrives as long after the one before
    # as it lasts.
    n = 266
    for _ in range(60):
        chunk = b"".join(g.take(n))
        clock[0] += n * g.dt
        a.feed(chunk, clock[0])
    snap = a.snapshot()
    assert snap.timed
    assert snap.jitter_max is not None and snap.jitter_max < 5.0
    assert ind(snap, "pcr_acc") == 0
    # A stall of a second, then the stream all at once.
    clock[0] += 1.0
    a.feed(b"".join(g.take(n)), clock[0])
    for _ in range(10):
        clock[0] += n * g.dt
        a.feed(b"".join(g.take(n)), clock[0])
    snap = a.snapshot()
    assert snap.jitter_max > 800.0
    assert ind(snap, "pcr_acc") >= 1
    assert any(v is not None and v > 800.0 for v in snap.jitter)
    # Read from what was kept (no arrival time): no jitter at all.
    b = analyse(Gen().seconds(3.0))
    assert not b.snapshot().timed and b.snapshot().jitter_max is None


def test_reset_counters_keeps_what_the_stream_is():
    pkts = Gen().take(3000)
    del pkts[1500]
    a = analyse(b"".join(pkts))
    assert counted(a.snapshot())
    a.reset_counters()
    snap = a.snapshot()
    assert counted(snap) == {} and all(p.cc_errors == 0 for p in snap.pids)
    assert snap.service() == "Test One"


def test_a_gap_is_not_an_error():
    g = Gen()
    a = analyse(g.seconds(2.0))
    g.seconds(3.0)                                   # never seen
    a.gap()
    analyse(g.seconds(2.0), a=a)
    assert counted(a.snapshot()) == {}


# -- the pieces ------------------------------------------------------------------------


def test_stream_types_named():
    assert ta.classify(0x06, [(0x6A, b"\x00")])[:2] == ("ac3", "audio")
    assert ta.classify(0x06, [(0x7A, b"\x00")])[:2] == ("eac3", "audio")
    assert ta.classify(0x06, [(0x56, b"deu\x09\x00")]) == ("teletext", "teletext", "deu")
    assert ta.classify(0x86, [])[:2] == ("scte35", "data")
    assert ta.classify(0x06, [(0x05, b"CUEI")])[:2] == ("scte35", "data")
    assert ta.classify(0x24, [])[:2] == ("hevc", "video")
    assert ta.classify(0x02, [])[:2] == ("mpeg2v", "video")
    assert ta.classify(0x03, [(0x0A, b"eng\x00")]) == ("mp2", "audio", "eng")
    assert ta.classify(0x06, [])[:2] == ("private", "data")
    assert ta.classify(0x99, [])[:2] == ("unknown", "data")
    for key in ("h264", "hevc", "mpeg2v", "aac", "ac3", "eac3", "mp2", "dvbsub", "teletext", "scte35"):
        assert key in ta.CODEC_NAMES


def test_dvb_text():
    assert ta.dvb_text(b"Das Erste") == "Das Erste"
    assert ta.dvb_text(b"\x15Sch\xc3\xb6n") == "Schön"
    assert ta.dvb_text(b"\x05\xc4\xe9") == "Äé"          # ISO 8859-9
    assert ta.dvb_text(b"\x86ZDF\x87 HD") == "ZDF HD"     # emphasis codes go
    assert ta.dvb_text(b"") == ""


def test_memory_stays_bounded():
    rnd = random.Random(11)
    noise = b"".join(packet(rnd.randrange(0x20, 0x1FFE), rnd.randrange(16), b"\x00" * 184)
                     for _ in range(20_000))
    a = analyse(Gen().seconds(1.0) + noise)
    snap = a.snapshot()
    assert len(snap.pids) <= ta.MAX_PIDS + 8
    assert len(a._jit_hist) <= ta.HISTORY and len(a._buckets) <= ta.WINDOW + 1


def test_fast_enough_for_tens_of_megabits():
    data = Gen(bitrate=30e6).seconds(8.0)           # 30 MB
    assert len(data) >= 29_000_000
    a = ta.Analyzer()
    t0 = time.perf_counter()
    for i in range(0, len(data), 256 * 1024):
        a.feed(data[i:i + 256 * 1024])
    took = time.perf_counter() - t0
    assert a.snapshot().packets == len(data) // PACKET
    assert took < 4.0, f"{len(data) / took / 1e6:.1f} MB/s"


# -- the reader over a rewind buffer ----------------------------------------------------------


def test_runner_reads_the_live_buffer_and_never_counts_across_a_new_run(tmp_path):
    from dopeiptv.core.dvr_store import SegmentStore
    store = SegmentStore(tmp_path / "s", keep_seconds=60, max_bytes=1 << 30, min_free=0)
    g = Gen(bitrate=4e6)
    store.append(g.seconds(0.5))
    r = ta.Runner(store)
    r.start()
    stop = threading.Event()

    def write() -> None:
        while not stop.is_set():
            store.append(g.seconds(0.05))
            time.sleep(0.05)
    w = threading.Thread(target=write, daemon=True)
    w.start()
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            snap = r.latest
            if snap is not None and snap.state == "running" and snap.bps:
                break
            time.sleep(0.05)
        assert snap is not None and snap.state == "running", snap
        assert snap.service() == "Test One"
        # A reconnect: what follows begins anew (another counter, another
        # clock) - nothing counted across it.
        stop.set()
        w.join(2)
        store.new_run()
        g2 = Gen(bitrate=4e6, pcr_start=123_456_789)
        for _ in range(20):
            store.append(g2.seconds(0.05))
            time.sleep(0.02)
        time.sleep(1.5)
        snap = r.latest
        assert snap is not None and counted(snap) == {}, counted(snap)
        assert r.idle() < 3.0
        r.reset()
    finally:
        stop.set()
        r.stop()
        r.join(3)
        store.close()
    assert not r.alive and r.ended


def test_runner_caps_its_work(tmp_path):
    from dopeiptv.core.dvr_store import SegmentStore
    store = SegmentStore(tmp_path / "s", keep_seconds=60, max_bytes=1 << 30, min_free=0)
    data = Gen(bitrate=8e6).seconds(3.0)
    store.append(data)
    cpu = [0.0]

    def busy() -> float:            # every look at the processor: a lot spent
        cpu[0] += 1.0
        return cpu[0]
    r = ta.Runner(store, cpu=busy)
    r.CHUNK = 64 * 1024
    r.start()
    time.sleep(1.5)
    r.stop()
    r.join(3)
    store.close()
    # It let the rest of the segment go instead of reading on.
    assert r.latest is not None
    assert r.latest.packets < len(data) // PACKET
