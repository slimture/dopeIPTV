"""Who records a scheduled recording (core/rec_claims.py): each job claimed
by one recorder - the window or ``dopeiptv --server`` - at a time, a
crashed one's claim taken over, a finished one never taken again, another
computer's honoured by its beat, the window's presence seen by the server,
a stop asked of the other; the folder this user's alone and every file in
it checked as from outside."""
from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dopeiptv.core import rec_claims as rc

ROOT = Path(__file__).resolve().parent.parent
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX locks and modes")


@pytest.fixture
def folder(tmp_path):
    return str(tmp_path / rc.FOLDER)


def test_one_recorder_holds_a_job_and_the_other_sees_it(folder):
    window, server = rc.Claims(folder, rc.WINDOW), rc.Claims(folder, rc.SERVER)
    assert window.take("j1", {"title": "News", "channel": "One"}) == (rc.TAKEN, None)
    got, c = server.take("j1", {"title": "News"})
    assert got == rc.OTHER and c.live and c.who == rc.WINDOW and c.state == rc.RECORDING
    assert c.info == {"title": "News", "channel": "One"} and c.pid == os.getpid()
    assert [o.job for o in server.others()] == ["j1"] and window.others() == []
    # Taken again by its holder: still its own.
    assert window.take("j1", {}) == (rc.TAKEN, None) and window.mine("j1")
    window.write("j1", rc.HELD, {"title": "News"})
    assert server.read("j1").state == rc.HELD


def test_a_finished_job_is_never_taken_again(folder):
    window, server = rc.Claims(folder, rc.WINDOW), rc.Claims(folder, rc.SERVER)
    window.take("j1", {"title": "News"})
    assert window.finish("j1", rc.DONE, {"title": "News", "path": "/r/News.ts"}) == rc.TAKEN
    assert not window.mine("j1")
    got, c = server.take("j1", {})
    assert got == rc.ENDED and c.state == rc.DONE and c.info["path"] == "/r/News.ts"
    assert not server.read("j1").live and server.others() == []
    # Said again by its own recorder (it failed after all): the claim says so.
    assert window.finish("j1", rc.FAILED, {"error": "no data captured"}) == rc.TAKEN
    assert server.read("j1").state == rc.FAILED


def test_a_cancel_of_a_job_nobody_holds_finishes_it_and_one_another_holds_asks_it(folder):
    window, server = rc.Claims(folder, rc.WINDOW), rc.Claims(folder, rc.SERVER)
    assert window.finish("j1", rc.CANCELLED, {"title": "Film"}) == rc.TAKEN
    assert server.take("j1", {})[0] == rc.ENDED
    server.take("j2", {})
    assert window.finish("j2", rc.CANCELLED, {}) == rc.OTHER
    window.ask_stop("j2")
    assert server.stop_asked("j2") and not server.stop_asked("j2"), "asked once, seen once"


def test_a_claim_let_go_is_free_for_the_other(folder):
    window, server = rc.Claims(folder, rc.WINDOW), rc.Claims(folder, rc.SERVER)
    server.take("j1", {})
    server.free("j1")
    assert window.read("j1") is None
    assert window.take("j1", {})[0] == rc.TAKEN


_HOLDER = r"""
import sys, time
sys.path.insert(0, sys.argv[1])
from dopeiptv.core import rec_claims as rc
c = rc.Claims(sys.argv[2], sys.argv[3])
print(c.take("j1", {"title": "News", "start": 1.0})[0], flush=True)
c.hold_presence()
time.sleep(120)
"""


@posix_only
def test_a_crashed_recorder_s_claim_is_taken_over(folder, caplog):
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(ROOT), folder, rc.SERVER],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == rc.TAKEN
        window = rc.Claims(folder, rc.WINDOW)
        c = window.read("j1")
        assert c.live and c.pid == proc.pid and c.who == rc.SERVER
        assert window.take("j1", {})[0] == rc.OTHER
        proc.send_signal(signal.SIGKILL)           # no goodbye: the lock dies with it
        proc.wait(10)
        assert not window.read("j1").live
        with caplog.at_level("INFO", logger="dopeiptv"):
            assert window.take("j1", {"title": "News"}) == (rc.TAKEN, None)
        assert "taken over" in caplog.text
        assert window.read("j1").who == rc.WINDOW
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def _plant(folder: str, job: str, **fields) -> Path:
    os.makedirs(folder, mode=0o700, exist_ok=True)
    data = {"v": 1, "job": job, "who": rc.SERVER, "state": rc.RECORDING, "pid": 1,
            "host": "elsewhere", "started": "x", "info": {"title": "News"}}
    data.update(fields)
    path = Path(folder) / (rc.key_of(job) + rc.SUFFIX)
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path


def test_another_computer_s_claim_counts_while_it_is_beaten(folder):
    """A network share may not carry the lock: the other computer's claim
    stands as long as its recorder touches it."""
    path = _plant(folder, "j1")
    window = rc.Claims(folder, rc.WINDOW)
    got, c = window.take("j1", {})
    assert got == rc.OTHER and c.host == "elsewhere" and c.live
    old = time.time() - rc.STALE_S - 5
    os.utime(path, (old, old))
    assert not window.read("j1").live
    assert window.take("j1", {})[0] == rc.TAKEN


def test_a_damaged_or_planted_claim_is_nobody_s(folder):
    window = rc.Claims(folder, rc.WINDOW)
    window.ensure()
    for i, junk in enumerate((b"", b"not json", b"[1, 2]", json.dumps({"v": 2}).encode(),
                              b"x" * (rc.MAX_FILE + 10))):
        job = f"junk{i}"
        (Path(folder) / (rc.key_of(job) + rc.SUFFIX)).write_bytes(junk)
        assert window.take(job, {}) == (rc.TAKEN, None), junk[:20]
    # What a claim carries is the job's words and numbers - never an address.
    raw = rc.parse("k", json.dumps({"v": 1, "job": "j", "state": "recording", "info": {
        "title": "T\x1b[31m", "url": "http://u:p@host/live/u/p/1.ts", "start": "1e999",
        "stop": 12.0, "own": "yes"}}).encode())
    assert raw.info == {"title": "T[31m", "stop": 12.0}


def test_a_job_s_id_never_names_a_file_outside_the_folder(folder):
    window = rc.Claims(folder, rc.WINDOW)
    window.take("../../escape", {})
    names = os.listdir(folder)
    assert names == [rc.key_of("../../escape") + rc.SUFFIX]
    assert all(len(n) == 32 + len(rc.SUFFIX) for n in names)


@posix_only
def test_a_link_in_place_of_a_claim_is_refused(folder, tmp_path):
    window = rc.Claims(folder, rc.WINDOW)
    window.ensure()
    target = tmp_path / "elsewhere.txt"
    target.write_text("keep")
    os.symlink(target, Path(folder) / (rc.key_of("j1") + rc.SUFFIX))
    with pytest.raises(OSError):
        window.take("j1", {"title": "News"})
    assert target.read_text() == "keep"


@posix_only
def test_the_folder_and_its_files_are_this_user_s_alone(folder):
    os.makedirs(folder, mode=0o755)
    os.chmod(folder, 0o755)
    window = rc.Claims(folder, rc.WINDOW)
    window.take("j1", {})
    window.hold_presence()
    window.ask_stop("j2")
    window.touch_jobs()
    assert stat.S_IMODE(os.stat(folder).st_mode) == 0o700
    for name in os.listdir(folder):
        assert stat.S_IMODE(os.stat(os.path.join(folder, name)).st_mode) == 0o600, name


def test_the_window_s_presence_is_seen_by_the_server(folder):
    window, server = rc.Claims(folder, rc.WINDOW), rc.Claims(folder, rc.SERVER)
    assert not server.present(rc.WINDOW)
    assert window.hold_presence()
    assert server.present(rc.WINDOW)
    assert not window.present(rc.WINDOW), "not itself"
    window.drop_presence()
    assert not server.present(rc.WINDOW)
    # A window on another computer that shares the folder: by its beat.
    _plant(folder, "unused")
    path = Path(folder) / (rc.WINDOW + rc.PRESENCE)
    path.write_text(json.dumps({"v": 1, "job": "", "who": rc.WINDOW, "state": rc.HELD,
                                "host": "elsewhere"}))
    assert server.present(rc.WINDOW)
    old = time.time() - rc.STALE_S - 5
    os.utime(path, (old, old))
    assert not server.present(rc.WINDOW)


@posix_only
def test_a_crashed_window_is_no_presence(folder):
    proc = subprocess.Popen([sys.executable, "-c", _HOLDER, str(ROOT), folder, rc.WINDOW],
                            stdout=subprocess.PIPE, text=True)
    try:
        assert proc.stdout.readline().strip() == rc.TAKEN
        server = rc.Claims(folder, rc.SERVER)
        assert wait_for(lambda: server.present(rc.WINDOW))
        proc.kill()
        proc.wait(10)
        assert not server.present(rc.WINDOW)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def wait_for(cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


def test_the_jobs_stamp_changes_with_each_write_and_the_lock_nests(folder):
    window = rc.Claims(folder, rc.WINDOW)
    assert window.jobs_stamp() == ""
    a = window.touch_jobs()
    b = window.touch_jobs()
    assert a != b and window.jobs_stamp() == b
    with window.jobs_lock():
        with window.jobs_lock():
            pass


def test_the_window_waits_a_moment_for_the_jobs_list_never_the_others_patience(folder, monkeypatch):
    """The other recorder keeps the jobs' list locked (suspended, stuck on a
    slow network disk): the window - whose thread draws it - waits a moment
    and is told to come back (False), a try again soon is one short look, and
    once the other has kept it JOBS_PATIENCE_S in a row it goes on without
    it, as before. The server waits its whole patience at once."""
    window, server = rc.Claims(folder, rc.WINDOW), rc.Claims(folder, rc.SERVER)
    assert rc.JOBS_WAIT_S[rc.WINDOW] <= 0.25 and rc.JOBS_WAIT_S[rc.SERVER] == rc.JOBS_PATIENCE_S
    monkeypatch.setattr(rc, "JOBS_PATIENCE_S", 1.0)
    with server.jobs_lock() as held:
        assert held
        t0 = time.monotonic()
        with window.jobs_lock() as go:
            assert go is False
        assert time.monotonic() - t0 < 0.5, "a moment, not the other's patience"
        t0 = time.monotonic()
        with window.jobs_lock() as go:
            assert go is False
            with window.jobs_lock() as inner:
                assert inner is False, "nested, the same answer"
        assert time.monotonic() - t0 < 0.2, "a try again soon: one short look"
        time.sleep(1.05)
        with window.jobs_lock() as go:
            assert go is True, "kept past its patience: on without it"
    with window.jobs_lock() as go:
        assert go is True and window._jobs_fd is not None, "free again: locked"
    assert window._jobs_missed is None
    monkeypatch.setitem(rc.JOBS_WAIT_S, rc.SERVER, 0.3)
    monkeypatch.setattr(rc, "JOBS_PATIENCE_S", 0.3)
    with window.jobs_lock():
        t0 = time.monotonic()
        with server.jobs_lock() as go:
            assert go is True and server._jobs_fd is None
        assert time.monotonic() - t0 >= 0.28, "the server waited its patience at once"


def test_finished_claims_are_swept_after_a_while_and_live_ones_never(folder):
    window, server = rc.Claims(folder, rc.WINDOW), rc.Claims(folder, rc.SERVER)
    window.take("done", {"stop": time.time()})
    window.finish("done", rc.DONE, {"stop": time.time()})
    server.take("live", {"stop": time.time()})
    window.ask_stop("live")
    later = time.time() + rc.KEEP_S + 3600
    assert window.sweep(later) >= 1
    assert window.read("done") is None
    assert server.mine("live") and window.read("live").live
