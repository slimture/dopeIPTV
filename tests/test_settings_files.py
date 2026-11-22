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


# -- a drive that keeps no permissions: the app starts, and says so once ------------

@pytest.fixture
def nothing_noted():
    settings_files._not_private.clear()
    yield
    settings_files._not_private.clear()


@pytest.fixture
def warnings():
    import logging

    from dopeiptv.core.log import log
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.WARNING:
                seen.append(record.getMessage())
    h = Catch()
    log.addHandler(h)
    yield seen
    log.removeHandler(h)


def _shared_file(tmp_path) -> Path:
    folder = tmp_path / "dopeiptv"
    folder.mkdir(mode=0o755)
    os.chmod(folder, 0o755)
    f = folder / "dopeiptv.conf"
    f.write_text("[General]\npassword=hunter2\n")
    os.chmod(f, 0o644)
    return f


def test_a_drive_that_refuses_the_mode_is_logged_by_name_and_noted(tmp_path, monkeypatch,
                                                                    nothing_noted, warnings):
    f = _shared_file(tmp_path)

    def refused(path, mode):
        raise PermissionError(1, "Operation not permitted", str(path))
    monkeypatch.setattr(settings_files.os, "chmod", refused)
    assert settings_files.keep_private(str(f)) is False         # never raises
    assert settings_files.not_private() == [str(f)]
    assert any(str(f) in w and "not permitted" in w for w in warnings), warnings
    assert not any("hunter2" in w for w in warnings)
    # Asked again (protect runs once, a second store may name it): one entry.
    settings_files.keep_private(str(f))
    assert settings_files.not_private() == [str(f)]
    assert f.read_text() == "[General]\npassword=hunter2\n"


def test_a_drive_that_takes_the_mode_and_keeps_its_own_is_noted_too(tmp_path, monkeypatch,
                                                                     nothing_noted, warnings):
    f = _shared_file(tmp_path)
    monkeypatch.setattr(settings_files.os, "chmod", lambda path, mode: None)
    assert settings_files.keep_private(str(f)) is False
    assert settings_files.not_private() == [str(f)]
    assert any(str(f) in w for w in warnings)


def test_a_file_kept_private_is_noted_nowhere(tmp_path, nothing_noted):
    f = _shared_file(tmp_path)
    assert settings_files.keep_private(str(f)) is True
    assert settings_files.not_private() == []


def test_the_window_asks_first_then_says_it_calmly_with_where_it_is(tmp_path, monkeypatch,
                                                                       nothing_noted):
    """A settings file others may read holds every login: the window asks -
    close (the default), or go on with that file - and only once the person
    said to go on does it say so calmly each start after."""
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dopeiptv.i18n import tr
    from dopeiptv.ui.mw_settings import _SettingsMixin
    from dopeiptv.ui.notices import NoticeHub

    class Settings(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):
            self[k] = v

    class Window(_SettingsMixin):
        def __init__(self, answer):
            self.notices = NoticeHub()
            self.settings = Settings()
            self.asked: list[str] = []
            self.closed = False
            self.answer = answer

        def _ask_settings_open(self, path):
            self.asked.append(path)
            return self.answer

        def close(self):
            self.closed = True

    w = Window("quit")
    w._tell_settings_not_private()
    assert w.notices.messages() == [] and not w.asked              # nothing to say
    f = _shared_file(tmp_path)
    monkeypatch.setattr(settings_files.os, "chmod", lambda path, mode: None)
    settings_files.keep_private(str(f))
    w._tell_settings_not_private()
    assert w.asked == [str(f)] and w.closed, "Close: the app closes"
    assert settings_files.ACCEPTED_KEY not in w.settings
    go = Window("go")
    go._tell_settings_not_private()
    assert go.asked == [str(f)] and not go.closed
    assert go.settings[settings_files.ACCEPTED_KEY] == settings_files.accepted_mark(str(f))
    assert str(f) not in str(go.settings), "the path itself is not kept"
    later = Window("quit")
    later.settings = go.settings
    later._tell_settings_not_private()
    later._tell_settings_not_private()
    assert later.asked == [] and not later.closed, "asked once, for that file"
    [msg] = later.notices.messages()
    assert msg.level == "warning" and not msg.decide
    assert msg.title == tr("settings_open_title") and str(f) in msg.body
    assert msg.action_label == tr("btn_open_folder")
    opened = []
    monkeypatch.setattr("dopeiptv.core.xdg.open_folder", opened.append)
    msg.action()
    assert opened == [str(f.parent)]


def test_a_link_to_nothing_is_not_private(tmp_path, nothing_noted):
    """What is written through a link that points nowhere lands where it
    points, which nothing made private: noted, never passed."""
    folder = tmp_path / "dopeiptv"
    folder.mkdir(mode=0o700)
    f = folder / "dopeiptv.conf"
    f.symlink_to(tmp_path / "elsewhere" / "settings.conf")
    assert settings_files.keep_private(str(f)) is False
    assert settings_files.not_private() == [str(f)]
    assert not (tmp_path / "elsewhere").exists()


def test_a_file_another_account_owns_is_not_private(tmp_path, monkeypatch, nothing_noted):
    f = _shared_file(tmp_path)
    real = settings_files.os.stat
    me = os.geteuid()

    class Theirs:
        def __init__(self, st):
            self._st = st

        def __getattr__(self, name):
            return me + 1 if name == "st_uid" else getattr(self._st, name)

    monkeypatch.setattr(settings_files.os, "stat",
                        lambda p, *a, **k: Theirs(real(p, *a, **k)) if str(p) == str(f)
                        else real(p, *a, **k))
    assert settings_files.keep_private(str(f)) is False
    assert settings_files.not_private() == [str(f)]


def test_every_file_others_may_read_is_asked_about_not_the_first_alone(tmp_path, monkeypatch,
                                                                        nothing_noted):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from dopeiptv.ui.mw_settings import _SettingsMixin
    from dopeiptv.ui.notices import NoticeHub

    class Settings(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):
            self[k] = v

    class Window(_SettingsMixin):
        def __init__(self, answers):
            self.notices = NoticeHub()
            self.settings = Settings()
            self.asked: list[str] = []
            self.closed = False
            self.answers = list(answers)

        def _ask_settings_open(self, path):
            self.asked.append(path)
            return self.answers.pop(0)

        def close(self):
            self.closed = True

    monkeypatch.setattr(settings_files.os, "chmod", lambda path, mode: None)
    one = _shared_file(tmp_path)
    other = tmp_path / "dopeiptv" / "resume.conf"
    other.write_text("")
    os.chmod(other, 0o644)
    settings_files.keep_private(str(one))
    settings_files.keep_private(str(other))
    w = Window(["go", "go"])
    w._tell_settings_not_private()
    assert w.asked == [str(one), str(other)] and not w.closed
    marks = settings_files.accepted_marks(w.settings[settings_files.ACCEPTED_KEY])
    assert marks == {settings_files.accepted_mark(str(one)), settings_files.accepted_mark(str(other))}
    # One said yes to before 1.6.12 (a single mark), the other new: asked.
    later = Window(["quit"])
    later.settings[settings_files.ACCEPTED_KEY] = settings_files.accepted_mark(str(one))
    later._tell_settings_not_private()
    assert later.asked == [str(other)] and later.closed


def test_an_unanswered_question_closes_the_window(tmp_path, monkeypatch, nothing_noted):
    """It fails closed: a question that could not be asked never lets the
    window go on with a file others may read."""
    from dopeiptv.ui.mw_settings import _SettingsMixin
    from dopeiptv.ui.notices import NoticeHub

    class Window(_SettingsMixin):
        def __init__(self):
            self.notices = NoticeHub()
            self.closed = False

        def _ask_settings_open(self, path):
            raise RuntimeError("no screen")

        def close(self):
            self.closed = True

    monkeypatch.setattr(settings_files.os, "chmod", lambda path, mode: None)
    settings_files.keep_private(str(_shared_file(tmp_path)))
    w = Window()
    w.settings = type("S", (dict,), {"value": lambda self, k, d=None: self.get(k, d),
                                     "setValue": lambda self, k, v: self.__setitem__(k, v)})()
    w._tell_settings_not_private()
    assert w.closed
