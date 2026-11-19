"""The settings files - every login, the sync code, the tokens - are this
user's alone: 0600, the app's folder 0700 (core/settings_files.py)."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from dopeiptv.core import settings_files

ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")


def _mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


@pytest.fixture(autouse=True)
def _usual_umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def test_an_existing_file_and_its_folder_are_narrowed(tmp_path):
    folder = tmp_path / "dopeiptv"
    folder.mkdir(mode=0o755)
    f = folder / "dopeiptv.conf"
    f.write_text("[General]\npassword=secret\n")
    os.chmod(f, 0o644)
    assert settings_files.keep_private(str(f))
    assert _mode(f) == 0o600
    assert _mode(folder) == 0o700
    assert f.read_text() == "[General]\npassword=secret\n"


def test_a_missing_file_is_made_empty_at_0600_first(tmp_path):
    f = tmp_path / "dopeiptv" / "resume.ini"
    assert settings_files.keep_private(str(f))
    assert f.read_bytes() == b""
    assert _mode(f) == 0o600
    assert _mode(f.parent) == 0o700


def test_only_narrowed_never_widened(tmp_path):
    f = tmp_path / "x.ini"
    f.write_text("")
    os.chmod(f, 0o400)
    settings_files.keep_private(str(f), folder=False)
    assert _mode(f) == 0o400


def test_a_plist_is_never_made_and_its_folder_left_alone(tmp_path):
    folder = tmp_path / "Preferences"
    folder.mkdir(mode=0o755)
    f = folder / "com.dopeiptv.dopeiptv.plist"
    settings_files.keep_private(str(f), folder=False, create=False)
    assert not f.exists()
    assert _mode(folder) == 0o755


def test_qt_keeps_the_mode_when_it_writes_the_file_again(tmp_path):
    from PyQt6.QtCore import QSettings
    folder = tmp_path / "dopeiptv"
    path = str(folder / "dopeiptv.conf")
    s = QSettings(path, QSettings.Format.IniFormat)
    settings_files.protect({"main": s})
    assert _mode(path) == 0o600 and _mode(folder) == 0o700
    s.setValue("password", "secret")
    s.sync()
    assert s.status() == QSettings.Status.NoError
    assert _mode(path) == 0o600
    assert QSettings(path, QSettings.Format.IniFormat).value("password") == "secret"


def test_a_folder_not_the_app_s_own_is_left_alone(tmp_path):
    from PyQt6.QtCore import QSettings
    os.chmod(tmp_path, 0o755)
    path = str(tmp_path / "elsewhere.ini")
    settings_files.protect({"main": QSettings(path, QSettings.Format.IniFormat)})
    assert _mode(path) == 0o600
    assert _mode(tmp_path) == 0o755


def _env(tmp_path) -> dict:
    env = dict(os.environ, XDG_CONFIG_HOME=str(tmp_path / "config"),
               XDG_CACHE_HOME=str(tmp_path / "cache"), XDG_DATA_HOME=str(tmp_path / "data"),
               XDG_STATE_HOME=str(tmp_path / "state"), QT_QPA_PLATFORM="offscreen")
    env.pop("DOPEIPTV_LOG_FILE", None)
    return env


def _app_files(tmp_path) -> list[Path]:
    folder = tmp_path / "config" / "dopeiptv"
    return [folder / "dopeiptv.conf"] + [folder / f"{n}.ini" for n in ("resume", "follow", "cache")]


@pytest.mark.skipif(sys.platform == "darwin", reason="the main settings are a plist there")
def test_every_settings_file_of_an_existing_install_is_narrowed_at_start(tmp_path):
    folder = tmp_path / "config" / "dopeiptv"
    folder.mkdir(parents=True, mode=0o755)
    os.chmod(folder, 0o755)
    for f in _app_files(tmp_path):
        f.write_text("[General]\nk=v\n")
        os.chmod(f, 0o644)
    r = subprocess.run([sys.executable, "-c",
                        "from dopeiptv.core import settings_files; settings_files.protect()"],
                       cwd=str(ROOT), env=_env(tmp_path), capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 0, r.stderr
    assert _mode(folder) == 0o700
    for f in _app_files(tmp_path):
        assert _mode(f) == 0o600, f
        assert f.read_text() == "[General]\nk=v\n"


_SERVER = r"""
import os, signal, threading, time
def later():
    time.sleep(2.0)
    os.kill(os.getpid(), signal.SIGTERM)
threading.Thread(target=later, daemon=True).start()
from dopeiptv.launcher import main
import sys
sys.argv = ["dopeiptv", "--server", "--port", "0", "--no-discovery"]
sys.exit(main())
"""


@pytest.mark.skipif(sys.platform != "linux", reason="SIGTERM; the main settings file")
def test_server_mode_starts_with_its_settings_this_user_s_alone(tmp_path):
    r = subprocess.run([sys.executable, "-c", _SERVER], cwd=str(ROOT), env=_env(tmp_path),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    main = _app_files(tmp_path)[0]
    assert main.exists()
    assert _mode(main) == 0o600
    assert _mode(main.parent) == 0o700


def test_the_window_s_start_narrows_them_before_the_restore_writes_them():
    src = (ROOT / "dopeiptv" / "app.py").read_text(encoding="utf-8")
    body = src[src.index("def main() -> int:"):]
    assert body.index("settings_files.protect()") < body.index("_put_restore_in_place()")
    assert body.index("settings_files.protect()") < body.index("profiles.ProfileSettings(ORG, ORG)")
