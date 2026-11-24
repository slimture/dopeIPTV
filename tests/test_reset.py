"""Settings -> Reset all leaves nothing of what the person had or watched:
every settings file (the follow file too), a restore waiting for the next
start, and the app's folders - the cache, the logs, its own yt-dlp - emptied
on the way out and again at the next start (core/reset.py). Only the app's
own folders are touched, a link inside is never followed, and the
recordings stay wherever they are."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from dopeiptv.core import reset


def _fill(folder: Path) -> None:
    (folder / "images").mkdir(parents=True)
    (folder / "images" / "logo.png").write_bytes(b"png")
    (folder / "meta").mkdir()
    (folder / "meta" / "local_library.json").write_text("{}")
    (folder / "tmdb_trending.json").write_text("[]")


def test_only_a_folder_of_the_apps_own_is_emptied(tmp_path):
    home = Path.home()
    assert not reset.owned(home)
    assert not reset.owned("/")
    assert not reset.owned(home / ".cache")
    assert not reset.owned(home.parent)
    assert reset.owned(home / ".cache" / "dopeIPTV" / "dopeIPTV")
    assert reset.owned(home / ".local" / "state" / "dopeiptv" / "logs")
    assert reset.owned(tmp_path / "dopeIPTV" / "dopeIPTV")
    assert not reset.owned(tmp_path / "somewhere" / "else" / "cache")

    stranger = tmp_path / "somewhere" / "else" / "cache"
    _fill(stranger)
    reset.wipe([stranger])
    assert (stranger / "images" / "logo.png").exists()


def test_the_cache_is_emptied_links_never_followed_recordings_kept(tmp_path):
    cache = tmp_path / "cache" / "dopeIPTV" / "dopeIPTV"
    _fill(cache)
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "photo.jpg").write_bytes(b"jpg")
    try:
        os.symlink(outside, cache / "linked-dir")
        os.symlink(outside / "photo.jpg", cache / "linked-file")
    except (OSError, NotImplementedError):
        pytest.skip("no symbolic links here")
    recordings = cache / "Recordings"
    recordings.mkdir()
    (recordings / "match.ts").write_bytes(b"ts")

    assert reset.wipe([cache], keep=[recordings]) == 0
    assert sorted(p.name for p in cache.iterdir()) == ["Recordings"]
    assert (recordings / "match.ts").exists()
    assert (outside / "photo.jpg").read_bytes() == b"jpg"


def test_the_next_start_empties_what_was_left_and_keeps_what_was_kept(tmp_path):
    settings = tmp_path / "config"
    cache = tmp_path / "cache" / "dopeIPTV" / "dopeIPTV"
    _fill(cache)
    kept = cache / "My recordings"
    kept.mkdir()
    (kept / "film.mp4").write_bytes(b"mp4")

    assert not reset.finish(settings, [cache])          # no reset waiting
    assert (cache / "images" / "logo.png").exists()

    reset.request(settings, [kept])
    marker = settings / reset.MARKER
    assert json.loads(marker.read_text())["keep"] == [str(kept)]
    assert reset.finish(settings, [cache])
    assert not marker.exists()
    assert sorted(p.name for p in cache.iterdir()) == ["My recordings"]
    assert (kept / "film.mp4").exists()


def test_the_folders_a_reset_empties(tmp_path):
    from dopeiptv.core import ytdlp
    from dopeiptv.core.log import log_dir
    folders = reset.app_folders()
    assert Path(log_dir()) in folders
    assert ytdlp.managed_dir() in folders


def test_reset_all_clears_every_file_and_a_staged_restore(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
    QApplication.instance() or QApplication([])
    from dopeiptv.core import backup
    from dopeiptv.ui import mw_settings

    def ini(name: str) -> QSettings:
        s = QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat)
        s.setValue(f"{name}_key", "something of theirs")
        s.setValue("profiles/kid/favorites", "theirs too")
        s.sync()
        return s

    pending = tmp_path / backup.PENDING_NAME
    pending.write_text("{}")
    monkeypatch.setattr(backup, "pending_path", lambda folder=None: str(pending))
    monkeypatch.setattr(mw_settings, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(mw_settings.message_box, "information", lambda *a, **k: None)

    class Rec:
        def directory(self) -> str:
            return str(tmp_path / "Videos" / "dopeIPTV")

    class Window:
        closed = False

        def close(self) -> None:
            self.closed = True

    class Dialog:
        def reject(self) -> None:
            pass

    w = Window()
    w.settings, w._resume_settings = ini("main"), ini("resume")
    w._follow_settings, w._cache_settings = ini("follow"), ini("cache")
    w.rec = Rec()
    mw_settings._SettingsMixin._reset_all_settings(w, Dialog())

    for st in (w.settings, w._resume_settings, w._follow_settings, w._cache_settings):
        assert QSettings(st.fileName(), QSettings.Format.IniFormat).allKeys() == [], st.fileName()
    assert not pending.exists()
    assert json.loads((tmp_path / reset.MARKER).read_text())["keep"] == [Rec().directory()]
    assert w._reset_wipe == [Rec().directory()]
    assert w._resetting and w.closed
