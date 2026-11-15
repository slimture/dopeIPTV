"""Recordings saved as MP4: the remux command (stream copy, faststart), what
ffmpeg says of a file, when the MP4 is plausibly the whole recording, and
the files - the .ts deleted only once the MP4 passed, kept (and no half MP4
left) otherwise, its info moving along - with a fake ffmpeg. Then the
recorder: off by default, the command after a recording getting the final
file, the jobs list showing it, a remux the closing cut short done at the
next start. With a real ffmpeg here, a real remux too."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from dopeiptv.core import rec_mp4

PY = sys.executable

TS_PROBE = """Input #0, mpegts, from 'a.ts':
  Duration: 01:02:03.50, start: 1.400000, bitrate: 4210 kb/s
  Program 1
  Stream #0:0[0x100]: Video: h264 (High) ([27][0][0][0] / 0x001B), yuv420p, 1280x720, 25 fps
  Stream #0:1[0x101](swe): Audio: aac (LC) ([15][0][0][0] / 0x000F), 48000 Hz, stereo, fltp
  Stream #0:2[0x102](swe): Subtitle: dvb_teletext ([6][0][0][0] / 0x0006)
At least one output file must be specified
"""
MP4_PROBE = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'a.mp4.part':
  Duration: 01:02:03.46, start: 0.000000, bitrate: 4100 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p, 1280x720 (default)
  Stream #0:1[0x2](swe): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo (default)
"""


def test_only_a_ts_is_remuxed_and_its_mp4_is_named_beside_it(tmp_path):
    assert rec_mp4.wanted("/r/Show 2026-11-15 21.00.ts") and rec_mp4.wanted("/r/X.TS")
    assert not rec_mp4.wanted("/r/x.mp4") and not rec_mp4.wanted("/r/x.mkv") and not rec_mp4.wanted("")
    src = str(tmp_path / "SVT1 HD" / "Nyheterna 2026-11-15.ts")
    assert rec_mp4.target(src) == str(tmp_path / "SVT1 HD" / "Nyheterna 2026-11-15.mp4")
    assert rec_mp4.part_path(src) == str(tmp_path / "SVT1 HD" / "Nyheterna 2026-11-15.mp4.part")
    taken = {str(tmp_path / "SVT1 HD" / "Nyheterna 2026-11-15.mp4"),
             str(tmp_path / "SVT1 HD" / "Nyheterna 2026-11-15 (2).mp4")}
    assert rec_mp4.target(src, taken.__contains__) == str(
        tmp_path / "SVT1 HD" / "Nyheterna 2026-11-15 (3).mp4")


def test_the_command_copies_picture_and_sound_and_puts_the_index_first():
    cmd = rec_mp4.command("ffmpeg", "in.ts", "out.mp4.part")
    joined = " ".join(cmd)
    assert cmd[0] == "ffmpeg" and cmd[-1] == "out.mp4.part"
    assert cmd[cmd.index("-i") + 1] == "in.ts"
    assert "-c copy" in joined and "-movflags +faststart" in joined and "-f mp4" in joined
    assert "-map 0:v? -map 0:a?" in joined          # no teletext or data streams
    assert "-nostdin" in cmd and "-y" in cmd
    for encode in ("libx264", "-crf", "-b:v", "-c:v", "-c:a", "-tag:v"):
        assert encode not in cmd
    hevc = rec_mp4.command("ffmpeg", "in.ts", "out.mp4.part", hevc=True)
    assert hevc[hevc.index("-tag:v") + 1] == "hvc1" and "-c" in hevc


def test_what_ffmpeg_says_of_a_file_is_read():
    p = rec_mp4.parse_probe(TS_PROBE)
    assert p.duration == pytest.approx(3723.5) and p.kinds == {"video", "audio", "subtitle"}
    assert not p.hevc
    assert rec_mp4.parse_probe("Duration: N/A, bitrate: N/A\n  Stream #0:0: Video: hevc (Main)").hevc
    assert rec_mp4.parse_probe("Duration: N/A, start").duration is None
    assert rec_mp4.parse_probe("") == rec_mp4.Probe()


def test_the_mp4_must_be_plausibly_all_of_the_recording():
    src, out = rec_mp4.parse_probe(TS_PROBE), rec_mp4.parse_probe(MP4_PROBE)
    gb = 2_000_000_000
    assert rec_mp4.plausible(gb, src, int(gb * 0.95), out) == ""
    # The sound left behind, a third of the length, a sliver of the size.
    no_audio = rec_mp4.Probe(out.duration, frozenset({"video"}))
    assert "audio" in rec_mp4.plausible(gb, src, gb, no_audio)
    short = rec_mp4.Probe(1200.0, out.kinds)
    assert "lasts" in rec_mp4.plausible(gb, src, gb, short)
    assert rec_mp4.plausible(gb, src, gb // 10, out)
    assert rec_mp4.plausible(gb, src, gb, rec_mp4.Probe(None, out.kinds))
    # A few seconds short of ffmpeg's estimate of the .ts is whole.
    assert rec_mp4.plausible(gb, src, gb, rec_mp4.Probe(3700.0, out.kinds)) == ""
    # ffmpeg could not tell the .ts's length: the other checks decide.
    no_len = rec_mp4.Probe(None, src.kinds)
    assert rec_mp4.plausible(gb, no_len, gb, out) == ""
    assert rec_mp4.plausible(gb, rec_mp4.Probe(), gb, out)        # nothing readable in it


class FakeRun:
    """ffmpeg as remux() calls it: a probe answers with *probes*, a remux
    writes *write* bytes (None: nothing) and ends with *code*."""

    def __init__(self, out_probe=MP4_PROBE, code=0, ratio=0.95, between=None):
        self.calls: list[list[str]] = []
        self.out_probe, self.code, self.ratio, self.between = out_probe, code, ratio, between

    def __call__(self, argv, timeout):
        self.calls.append(argv)
        assert timeout > 0
        if argv[-2] != "mp4":
            return 1, (TS_PROBE if argv[-1].endswith(".ts") else self.out_probe)
        src, out = argv[argv.index("-i") + 1], argv[-1]
        with open(out, "wb") as fh:
            fh.write(b"\0" * int(os.path.getsize(src) * self.ratio))
        if self.between:
            self.between()
        return self.code, "" if self.code == 0 else "Could not find tag for codec aac_latm"


def _ts(tmp_path, name="Show.ts", size=100_000, info=True):
    p = tmp_path / name
    p.write_bytes(b"\x47" * size)
    if info:
        (tmp_path / (name + ".info.json")).write_text('{"title": "Show", "icon": "x"}')
    return str(p)


def _room(_folder):
    return 10 ** 12


def test_a_good_remux_replaces_the_ts_and_takes_its_info_along(tmp_path):
    src = _ts(tmp_path)
    run = FakeRun()
    res = rec_mp4.remux(src, "ffmpeg", run, free=_room)
    assert res == rec_mp4.Result(True, str(tmp_path / "Show.mp4"))
    assert sorted(os.listdir(tmp_path)) == ["Show.mp4", "Show.mp4.info.json"]
    assert json.loads((tmp_path / "Show.mp4.info.json").read_text())["title"] == "Show"
    assert [c[-1] for c in run.calls] == [src, src[:-3] + ".mp4.part", src[:-3] + ".mp4.part"]


@pytest.mark.parametrize("run, why", [
    (FakeRun(code=1), "ffmpeg ended with 1"),
    (FakeRun(out_probe=MP4_PROBE.replace("01:02:03.46", "00:10:00.00")), "lasts"),
    (FakeRun(ratio=0.05), "bytes"),
    (FakeRun(out_probe="  Stream #0:0: Video: h264\n  Duration: 01:02:03.00"), "audio"),
])
def test_anything_short_of_a_whole_mp4_keeps_the_ts_and_leaves_no_half_mp4(tmp_path, run, why):
    src = _ts(tmp_path)
    res = rec_mp4.remux(src, "ffmpeg", run, free=_room)
    assert not res.ok and res.path == src and why in res.why, res
    assert sorted(os.listdir(tmp_path)) == ["Show.ts", "Show.ts.info.json"]


def test_too_little_room_keeps_the_ts_without_asking_ffmpeg(tmp_path):
    src = _ts(tmp_path)
    run = FakeRun()
    res = rec_mp4.remux(src, "ffmpeg", run, free=lambda _f: 100_000 + 1000)
    assert not res.ok and "room" in res.why and run.calls == []
    assert os.path.exists(src)


def test_a_recording_deleted_while_it_was_copied_leaves_nothing(tmp_path):
    src = _ts(tmp_path, info=False)
    res = rec_mp4.remux(src, "ffmpeg", FakeRun(between=lambda: os.remove(src)), free=_room)
    assert not res.ok and os.listdir(tmp_path) == []
    gone = rec_mp4.remux(str(tmp_path / "nothing.ts"), "ffmpeg", FakeRun(), free=_room)
    assert not gone.ok and "not there" in gone.why


def test_an_mp4_of_that_name_already_there_is_never_replaced(tmp_path):
    src = _ts(tmp_path, info=False)
    (tmp_path / "Show.mp4").write_bytes(b"older")
    res = rec_mp4.remux(src, "ffmpeg", FakeRun(), free=_room)
    assert res.ok and res.path == str(tmp_path / "Show (2).mp4")
    assert (tmp_path / "Show.mp4").read_bytes() == b"older"


# -- with a fake ffmpeg program ---------------------------------------------------

FAKE_FFMPEG = r'''#!{py}
import os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_FFMPEG_LOG"], "a") as log:
    log.write(" ".join(args) + "\n")
mode = os.environ.get("FAKE_FFMPEG", "ok")
src = args[args.index("-i") + 1]
if args[-2] != "mp4":
    part = src.endswith(".part")
    dur = "00:00:20.00" if mode == "short" and part else "00:01:00.00"
    sys.stderr.write(f"Input #0, mpegts, from '{{src}}':\n  Duration: {{dur}}, start: 1.4\n")
    sys.stderr.write("  Stream #0:0[0x100]: Video: h264 (High), yuv420p\n")
    sys.stderr.write("  Stream #0:1[0x101]: Audio: aac (LC), 48000 Hz\n")
    sys.stderr.write("At least one output file must be specified\n")
    sys.exit(1)
with open(args[-1], "wb") as fh:
    fh.write(b"\0" * int(os.path.getsize(src) * 0.9))
if mode == "fail":
    sys.stderr.write("Could not find tag for codec aac_latm in stream #1\n")
    sys.exit(1)
'''


@pytest.fixture
def fake_ffmpeg(tmp_path, monkeypatch):
    path = tmp_path / "bin" / "ffmpeg"
    path.parent.mkdir()
    path.write_text(FAKE_FFMPEG.format(py=PY))
    path.chmod(0o755)
    monkeypatch.setenv("FAKE_FFMPEG_LOG", str(tmp_path / "ffmpeg.log"))
    monkeypatch.setattr(rec_mp4, "SPARE_BYTES", 0)
    return str(path)


@pytest.mark.skipif(sys.platform == "win32", reason="a script as a program")
def test_the_fake_program_runs_through_the_real_runner(tmp_path, fake_ffmpeg, monkeypatch):
    rec = tmp_path / "rec"
    rec.mkdir()
    src = _ts(rec)
    res = rec_mp4.remux(src, fake_ffmpeg)
    assert res.ok and sorted(os.listdir(rec)) == ["Show.mp4", "Show.mp4.info.json"]
    log = (tmp_path / "ffmpeg.log").read_text().splitlines()
    assert len(log) == 3 and "-c copy" in log[1] and "+faststart" in log[1]
    monkeypatch.setenv("FAKE_FFMPEG", "fail")
    src = _ts(rec, "Other.ts")
    res = rec_mp4.remux(src, fake_ffmpeg)
    assert not res.ok and "aac_latm" in res.why and os.path.exists(src)
    assert not os.path.exists(rec_mp4.part_path(src))


# -- the recorder -------------------------------------------------------------------

@pytest.fixture
def manager(tmp_path, fake_ffmpeg):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.core import nerd
    from dopeiptv.core.recording import RecordingManager
    app = QApplication.instance() or QApplication([])
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    s.setValue("nerd_enabled", "true")
    code = "import sys,pathlib; pathlib.Path(sys.argv[1]+'.done').write_text(sys.argv[2]+'|'+sys.argv[3])"
    s.setValue("nerd_rec_post", f'"{PY}" -c "{code}" "{{path}}" "{{title}}" "{{channel}}"')
    nerd.install(s)
    (tmp_path / "rec").mkdir()

    def make():
        rm = RecordingManager(s)
        rm.ffmpeg = lambda: fake_ffmpeg
        return rm
    yield s, make, app
    nerd.install(None)


def _wait(app, cond, secs=20.0):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.02)
    assert cond()


def _marked(path):
    """The command after the recording has written its marker for *path*."""
    try:
        return os.path.getsize(path + ".done") > 0
    except OSError:
        return False


def _job(path, **kw):
    return dict({"id": "j1", "title": "Nyheterna", "channel": "SVT1 HD", "status": "done",
                 "path": path, "start": 0, "stop": None}, **kw)


@pytest.mark.skipif(sys.platform == "win32", reason="a script as a program")
def test_off_by_default_the_ts_stays_and_the_command_gets_it(tmp_path, manager):
    s, make, app = manager
    rm = make()
    assert not rec_mp4.enabled(s)
    src = _ts(tmp_path / "rec", info=False)
    j = _job(src)
    rm.jobs = [j]
    rm._after(j)
    _wait(app, lambda: _marked(src))
    assert j["path"] == src and "remux" not in j and os.path.exists(src)
    assert not (tmp_path / "ffmpeg.log").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="a script as a program")
def test_on_the_recording_ends_as_an_mp4_and_the_command_gets_that(tmp_path, manager):
    s, make, app = manager
    s.setValue(rec_mp4.SETTING, "true")
    rm = make()
    src = _ts(tmp_path / "rec")
    j = _job(src)
    rm.jobs = [j]
    told = []
    rm.jobs_changed.connect(lambda: told.append(j.get("remux")))
    rm._after(j)
    assert j["remux"] == "running" and rec_mp4.Journal(s).get(src)
    from dopeiptv.ui.mw_recording import _RecordingMixin
    from dopeiptv.i18n import tr
    assert tr("rec_mp4_saving") in _RecordingMixin._job_item(None, j)["name"]
    _wait(app, lambda: j.get("remux") == "done")
    mp4 = str(tmp_path / "rec" / "Show.mp4")
    assert j["path"] == mp4 and not os.path.exists(src) and told[-1] == "done"
    _wait(app, lambda: _marked(mp4))
    assert open(mp4 + ".done").read() == "Nyheterna|SVT1 HD"
    assert rec_mp4.Journal(s).items() == []
    listed = rm.files()
    assert [f["_path"] for f in listed] == [mp4] and listed[0]["name"] == "Show"
    assert _RecordingMixin._job_item(None, j)["_path"] == mp4
    rm._after(j)                                 # once
    time.sleep(0.2)
    app.processEvents()
    assert len((tmp_path / "ffmpeg.log").read_text().splitlines()) == 3


@pytest.mark.skipif(sys.platform == "win32", reason="a script as a program")
def test_a_failed_remux_keeps_the_ts_says_so_and_the_command_gets_the_ts(tmp_path, manager, monkeypatch):
    s, make, app = manager
    s.setValue(rec_mp4.SETTING, "true")
    monkeypatch.setenv("FAKE_FFMPEG", "short")
    rm = make()
    src = _ts(tmp_path / "rec", info=False)
    j = _job(src)
    rm.jobs = [j]
    rm._after(j)
    _wait(app, lambda: j.get("remux") == "failed")
    assert j["path"] == src and "lasts" in j["remux_error"] and os.path.exists(src)
    assert sorted(os.listdir(tmp_path / "rec"))[0] == "Show.ts"
    assert not os.path.exists(rec_mp4.part_path(src))
    _wait(app, lambda: _marked(src))
    from dopeiptv.i18n import tr
    from dopeiptv.ui.mw_recording import _RecordingMixin
    assert tr("rec_mp4_kept_ts") in _RecordingMixin._job_item(None, j)["name"]


@pytest.mark.skipif(sys.platform == "win32", reason="a script as a program")
def test_one_due_as_the_app_closes_is_done_at_the_next_start(tmp_path, manager):
    s, make, app = manager
    s.setValue(rec_mp4.SETTING, "true")
    rm = make()
    src = _ts(tmp_path / "rec")
    j = _job(src, status="recording", proc=None)
    rm.jobs = [j]
    rm._ended_on_quit = [j]
    rm._closing = True                       # what shutdown() sets first
    rm.after_shutdown()
    assert os.path.exists(src) and not (tmp_path / "ffmpeg.log").exists()
    assert rec_mp4.Journal(s).get(src)["title"] == "Nyheterna"
    # A half MP4 of it left by a remux the closing ended goes first.
    with open(rec_mp4.part_path(src), "wb") as fh:
        fh.write(b"half")
    again = make()
    again._resume_remuxes()
    mp4 = str(tmp_path / "rec" / "Show.mp4")
    _wait(app, lambda: _marked(mp4))
    assert not os.path.exists(src) and rec_mp4.Journal(s).items() == []
    assert open(mp4 + ".done").read() == "Nyheterna|SVT1 HD"
    # One whose .ts went meanwhile is forgotten.
    rec_mp4.Journal(s).add(str(tmp_path / "rec" / "gone.ts"), "Gone")
    make()._resume_remuxes()
    assert rec_mp4.Journal(s).items() == []


def test_the_journal_keeps_one_entry_per_recording():
    class S(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):  # noqa: N802
            self[k] = v
    s = S()
    j = rec_mp4.Journal(s)
    j.add("/r/a.ts", "A", "One")
    j.add("/r/a.ts", "A again", "One")
    j.add("/r/b.ts")
    assert [e["ts"] for e in j.items()] == ["/r/a.ts", "/r/b.ts"]
    assert j.get("/r/a.ts")["title"] == "A again"
    j.done("/r/a.ts")
    j.done("/r/b.ts")
    assert j.items() == [] and s[rec_mp4.JOURNAL] == ""
    s[rec_mp4.JOURNAL] = "not json"
    assert j.items() == []


def test_the_journal_is_this_computers_own():
    from dopeiptv.core import backup, sync
    assert not sync.syncable(rec_mp4.JOURNAL) and backup.left_out(rec_mp4.JOURNAL)
    assert not sync.syncable(rec_mp4.SETTING)


# -- a real ffmpeg -------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="no ffmpeg here")
def test_a_real_remux_is_whole_and_starts_with_its_index(tmp_path, monkeypatch):
    monkeypatch.setattr(rec_mp4, "SPARE_BYTES", 0)
    ff = shutil.which("ffmpeg")
    src = str(tmp_path / "Real.ts")
    made = subprocess.run(
        [ff, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
         "-t", "6", "-c:v", "mpeg2video", "-c:a", "mp2", "-f", "mpegts", src],
        capture_output=True, timeout=120)
    if made.returncode != 0:
        pytest.skip("this ffmpeg cannot make a test stream")
    res = rec_mp4.remux(src, ff)
    assert res.ok, res
    data = open(res.path, "rb").read()
    assert 0 <= data.find(b"moov") < data.find(b"mdat")      # faststart
    _code, said = rec_mp4._run(rec_mp4.probe_command(ff, res.path), 60)
    p = rec_mp4.parse_probe(said)
    assert p.kinds >= {"video", "audio"} and p.duration == pytest.approx(6.0, abs=0.5)
    assert not os.path.exists(src)
