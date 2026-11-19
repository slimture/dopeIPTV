"""A recording's file: its name is a provider's words and its folder a
setting, and neither leads anywhere but the recordings folder - nor stops
the recorder's tick.
"""

from __future__ import annotations

import json
import os
import time

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core import rec_extras
from dopeiptv.core.recording import RecordingManager, safe_filename


def _rm(tmp_path, name):
    s = QSettings("dopeIPTV-test", name)
    s.clear()
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    return RecordingManager(s), s


def test_a_title_with_a_nul_or_half_a_character_is_still_a_file_name():
    assert safe_filename("Ny\x00he\nter\tna\x7f") == "Nyheterna"
    assert safe_filename("A\ud800B") == "AB"                 # JSON can carry half a pair
    assert safe_filename("\x00\x01") == "recording"
    assert safe_filename("x" * 500).encode("utf-8").__len__() <= 200
    # The template's parts, the same.
    assert rec_extras.render_name("{channel}/{title}", "Ny\x00heter\ud800na", "SVT\x001") \
        == os.path.join("SVT1", "Nyheterna")


@pytest.mark.parametrize("name", ["CON", "nul", "Com1", "LPT9", "NUL.2026", "aux "])
def test_a_title_that_is_a_windows_device_gets_a_name_of_its_own(name):
    assert safe_filename(name) == "_" + name.strip()
    part = rec_extras.render_name("{title}", name, "")
    assert part.startswith("_")
    # ...and only those: a name that merely begins like one stays.
    assert safe_filename("CONAN") == "CONAN" and safe_filename("Null") == "Null"


def test_a_nul_in_a_title_never_raises_out_of_the_recorders_tick(tmp_path, monkeypatch):
    """os.path.exists raises ValueError for a NUL: the tick, every five
    seconds, then raised for good and no later recording ever started."""
    from dopeiptv.core import recording

    rm, _s = _rm(tmp_path, "recnul")
    started = []

    class FakeProc:
        def __init__(self, cmd, **_kw):
            started.append(cmd)

        def poll(self):
            return None

    monkeypatch.setattr(recording.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(RecordingManager, "recorder",
                        staticmethod(lambda: ("ffmpeg", "/usr/bin/ffmpeg")))
    now = time.time()
    rm.add_job("http://h.invalid/1.ts", "Bad\x00title\ud800", now - 1, now + 60)
    rm.add_job("http://h.invalid/2.ts", "Good", now - 1, now + 60, folder="a\x00b")
    rm.add_job("http://h.invalid/3.ts", "Fine", now - 1, now + 60)
    rm.tick()                                   # must not raise
    states = [j["status"] for j in rm.jobs]
    assert states[0] == "recording" and states[2] == "recording"
    assert "\x00" not in rm.jobs[0]["path"]
    assert states[1] in ("recording", "failed")  # a folder no file system takes
    assert len(started) >= 2


@pytest.mark.parametrize("folder", ["..", "../..", "../outside", "a/../../outside",
                                    "/etc", "/", "..\\..", "sub/../../.."])
def test_a_jobs_folder_never_leads_out_of_the_recordings_folder(tmp_path, folder):
    rm, _s = _rm(tmp_path, "recfolder")
    root = os.path.abspath(rm.directory())
    base = os.path.abspath(rm.subfolder(folder))
    assert os.path.commonpath([root, base]) == root
    path = os.path.abspath(rm.build_path("Title", folder))
    assert os.path.commonpath([root, path]) == root
    assert not (tmp_path / "outside").exists()


def test_a_folder_of_the_recordings_folder_is_still_used(tmp_path):
    rm, _s = _rm(tmp_path, "recfolder2")
    root = rm.directory()
    assert rm.subfolder("") == root and rm.subfolder(None) == root
    assert rm.subfolder("Series/Bron") == os.path.join(root, "Series", "Bron")
    path = rm.build_path("Title", "Series/Bron")
    assert os.path.dirname(path) == os.path.join(root, "Series", "Bron")
    assert os.path.isdir(os.path.dirname(path))


def test_a_job_from_the_settings_with_a_folder_outside_records_inside(tmp_path, monkeypatch):
    """A job is kept in the settings (and a backup carries them): its
    folder is no way to write where the person never chose."""
    from dopeiptv.core import recording

    s = QSettings("dopeIPTV-test", "recfolder3")
    s.clear()
    s.setValue("recordings_dir", str(tmp_path / "rec"))
    now = time.time()
    s.setValue("recording_jobs", json.dumps([
        {"id": "a", "status": "scheduled", "url": "http://h.invalid/1.ts", "title": "T",
         "start": now - 1, "stop": now + 60, "folder": "../../autostart"}]))
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
    rm.tick()
    out = os.path.abspath(seen[0][-1])
    root = os.path.abspath(str(tmp_path / "rec"))
    assert os.path.commonpath([root, out]) == root
    assert not (tmp_path / "autostart").exists()
