"""Unit tests for dopeiptv.core.recording utilities."""

from dopeiptv.core.recording import format_size, safe_filename


def test_safe_filename_strips_unsafe():
    assert "/" not in safe_filename("a/b\\c:d")
    assert "\\" not in safe_filename("a/b\\c:d")


def test_safe_filename_empty():
    assert safe_filename("") == "recording"
    assert safe_filename(None) == "recording"


def test_safe_filename_truncates():
    long = "x" * 200
    assert len(safe_filename(long)) <= 120


def test_format_size_bytes():
    assert "0 B" == format_size(0)
    assert "512 B" == format_size(512)


def test_format_size_kb():
    assert "1 KB" == format_size(1024)


def test_format_size_mb():
    result = format_size(1024 * 1024)
    assert "MB" in result


def test_format_size_gb():
    result = format_size(1024 ** 3)
    assert "GB" in result


def test_info_sidecar_keeps_icon(tmp_path):
    """The channel logo written into a recording's sidecar survives and comes
    back through files(), so the Recordings list shows the same badge as live
    TV. Editing the title must not wipe the icon; clearing it removes it."""
    from PyQt6.QtCore import QSettings
    from dopeiptv.core.recording import RecordingManager

    vid = tmp_path / "Show.mp4"
    with open(vid, "wb") as f:
        f.write(b"\0" * 16)

    s = QSettings("dopeIPTV-test", "recicon")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)

    logo = "http://logos/chan.png"
    rm.write_info(str(vid), "", "", icon=logo)
    row = next(r for r in rm.files(None) if r["_path"] == str(vid))
    assert row["stream_icon"] == logo

    # Editing the title (icon=None) keeps the logo.
    rm.write_info(str(vid), "My Title", "", icon=None)
    row = next(r for r in rm.files(None) if r["_path"] == str(vid))
    assert row["name"] == "My Title" and row["stream_icon"] == logo

    # Explicitly clearing everything drops the sidecar.
    rm.write_info(str(vid), "", "", icon="")
    assert not (tmp_path / "Show.mp4.info.json").exists()


def test_total_recordings_cap(tmp_path):
    """The folder-wide size cap counts only recordings and trips at the
    limit so a new recording can be refused."""
    from PyQt6.QtCore import QSettings
    from dopeiptv.core.recording import RecordingManager

    for name, size in (("a.mp4", 1_000_000_000),
                       ("b.mp4", 2_000_000_000),
                       ("note.txt", 5_000_000_000)):
        p = tmp_path / name
        with open(p, "wb") as f:
            f.seek(size - 1)
            f.write(b"\0")

    s = QSettings("dopeIPTV-test", "reccap")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)

    # Only the two .mp4 files count (~3 GB), not the .txt.
    assert 2.9e9 < rm.folder_used_bytes() < 3.1e9
    assert rm.total_cap_bytes() == 0 and not rm.total_cap_exceeded()

    s.setValue("rec_total_value", "5")
    s.setValue("rec_total_unit", "GB")
    assert not rm.total_cap_exceeded()          # 3 GB < 5 GB

    s.setValue("rec_total_value", "2")
    assert rm.total_cap_exceeded()              # 3 GB >= 2 GB

    s.setValue("rec_total_value", "1")
    s.setValue("rec_total_unit", "TB")
    assert rm.total_cap_bytes() == 10**12 and not rm.total_cap_exceeded()


def test_ffmpeg_records_with_the_players_user_agent(tmp_path, monkeypatch):
    """ffmpeg sent its own "Lavf" User-Agent, which panels that accept the
    player refuse, so a recording failed at once; and one dropped connection
    ended it. A live URL gets the player's UA and reconnects, an archive
    window only the UA (a reconnect there restarts it), a local file none."""
    from PyQt6.QtCore import QSettings
    from dopeiptv.core import recording
    from dopeiptv.core.recording import RECORD_UA, RecordingManager

    s = QSettings("dopeIPTV-test", "recua")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    rm = RecordingManager(s)
    seen = []

    class FakeProc:
        def __init__(self, cmd, **_kw):
            seen.append(cmd)

        def poll(self):
            return None

    monkeypatch.setattr(recording.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(RecordingManager, "recorder",
                        staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    live = "http://h.tv:8080/live/u/p/1.ts"
    arch = "http://h.tv:8080/timeshift/u/p/60/2026-09-27:20-00/1.ts"
    for url in (live, arch):
        rm._spawn({"url": url, "title": "T", "stop": None})
    cmd_live, cmd_arch = seen
    i = cmd_live.index("-i")
    assert cmd_live[i + 1] == live
    before = cmd_live[:i]
    assert before[before.index("-user_agent") + 1] == RECORD_UA
    assert "-reconnect_streamed" in before
    j = cmd_arch.index("-i")
    assert "-user_agent" in cmd_arch[:j]
    assert "-reconnect" not in cmd_arch
    assert RecordingManager._input_options("/tmp/x.ts") == []

    seen.clear()
    monkeypatch.setattr(RecordingManager, "recorder",
                        staticmethod(lambda: ("mpv", "/usr/bin/mpv")))
    rm._spawn({"url": live, "title": "T", "stop": None})
    assert f"--user-agent={RECORD_UA}" in seen[0]


def test_a_rename_or_move_never_replaces_another_recording(tmp_path):
    from dopeiptv.core.recording import free_path

    a = tmp_path / "Show.ts"
    a.write_bytes(b"a")
    b = tmp_path / "Other.ts"
    b.write_bytes(b"b")
    assert free_path(str(a)) == str(tmp_path / "Show (2).ts")
    (tmp_path / "Show (2).ts").write_bytes(b"x")
    assert free_path(str(a)) == str(tmp_path / "Show (3).ts")
    assert free_path(str(a), current=str(a)) == str(a)    # renamed to itself
    assert free_path(str(tmp_path / "New.ts")) == str(tmp_path / "New.ts")


def test_console_tools_never_open_a_window_on_windows(monkeypatch):
    # A windowed Windows build has no console, so each ffmpeg got one of its
    # own - for a recording, open the whole time, and closing it killed the
    # recording. Everywhere else the flag must be 0 (Popen rejects others).
    import importlib
    import sys

    from dopeiptv.core import player_exec
    assert player_exec.NO_WINDOW == (0x08000000 if sys.platform == "win32"
                                     else 0)
    monkeypatch.setattr(sys, "platform", "win32")
    try:
        assert importlib.reload(player_exec).NO_WINDOW == 0x08000000
    finally:
        monkeypatch.undo()
        importlib.reload(player_exec)


def test_damaged_scheduled_jobs_are_skipped_not_crashed_on(tmp_path):
    """A hand-edited entry raised in tick() every five seconds."""
    import json
    import time

    from PyQt6.QtCore import QSettings
    from dopeiptv.core.recording import RecordingManager

    s = QSettings("dopeIPTV-test", "recjobs")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path))
    later = time.time() + 3600
    s.setValue("recording_jobs", json.dumps([
        "junk", 7, {"status": "scheduled"},
        {"status": "scheduled", "url": "http://x", "start": "soon"},
        {"status": "scheduled", "url": "http://x", "start": later,
         "stop": "later"},
        {"status": "scheduled", "url": "http://ok", "start": later,
         "stop": later + 60}]))
    rm = RecordingManager(s)
    assert [j["url"] for j in rm.jobs] == ["http://ok"]
    assert rm.jobs[0]["id"] and rm.jobs[0]["title"] == ""
    rm.tick()                       # and the scheduler runs over it
