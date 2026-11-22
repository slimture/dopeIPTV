"""The Windows update: the installer for this build's CPU, fetched and
checked, then run silently once the app has exited - only for a copy the
installer put there."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dopeiptv.core import selfupdate, winupdate


@pytest.fixture(autouse=True)
def running_1_4_4(monkeypatch):
    """These tests run as 1.4.4: an installer asks which version runs
    before it takes a release (selfupdate.refuse_older)."""
    monkeypatch.setattr(selfupdate, "VERSION", "1.4.4")


def assets(version):
    names = [f"dopeIPTV-{version}-windows-x64-setup.exe", f"dopeIPTV-{version}-windows-arm64-setup.exe",
             f"dopeIPTV-{version}-windows-x64.zip", f"dopeIPTV-{version}-x86_64.AppImage"]
    return [{"name": n, "url": f"https://dopeiptv.org/dl.php?f={n}", "size": 3,
             "digest": "sha256:" + "0" * 64} for n in names]


def test_the_installer_for_this_cpu_is_picked_not_the_zip():
    a = winupdate.pick_asset(assets("1.4.5"), "v1.4.5", "x64")
    assert a["name"] == "dopeIPTV-1.4.5-windows-x64-setup.exe"
    assert winupdate.pick_asset(assets("1.4.5"), "v1.4.5", "arm64")["name"].endswith("arm64-setup.exe")
    assert winupdate.pick_asset(assets("1.4.5"), "v1.4.6", "x64") is None


@pytest.mark.parametrize("plat,cpu", [("win-amd64", "x64"), ("win-arm64", "arm64"), ("win32", None)])
def test_the_cpu_is_the_builds_own(monkeypatch, plat, cpu):
    monkeypatch.setattr(winupdate.sysconfig, "get_platform", lambda: plat)
    assert winupdate.arch() == cpu


def test_a_copy_without_the_installers_uninstaller_does_not_update_itself(monkeypatch, tmp_path):
    exe = tmp_path / "dopeiptv.exe"
    exe.write_text("x")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(winupdate.sysconfig, "get_platform", lambda: "win-amd64")
    assert winupdate.installed_dir() is None and not winupdate.can_self_update()     # the zip
    (tmp_path / "uninstall.exe").write_text("x")
    assert winupdate.installed_dir() == tmp_path.resolve() and winupdate.can_self_update()


def test_off_windows_nothing_updates_this_way():
    if sys.platform != "win32":
        assert winupdate.installed_dir() is None and not winupdate.can_self_update()


def test_a_release_without_an_installer_says_so(monkeypatch):
    monkeypatch.setattr(winupdate, "can_self_update", lambda: True)
    monkeypatch.setattr(winupdate, "arch", lambda: "arm64")
    rel = {"tag": "v1.4.5", "assets": [a for a in assets("1.4.5") if "arm64-setup" not in a["name"]]}
    with pytest.raises(selfupdate.UpdateError) as e:
        winupdate.install(rel, "1.4.4")
    assert e.value.key == "upd_err_no_setup"


def test_the_installer_is_downloaded_into_the_temp_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(winupdate, "can_self_update", lambda: True)
    monkeypatch.setattr(winupdate, "arch", lambda: "x64")
    monkeypatch.setattr(winupdate, "update_folder", lambda: tmp_path / "dopeIPTV-update")
    got = {}
    monkeypatch.setattr(winupdate, "signed_asset", lambda rel, asset: asset)
    monkeypatch.setattr(winupdate, "download", lambda asset, dest, p, c: got.update(name=asset["name"], dest=dest))
    path = winupdate.install({"tag": "v1.4.5", "assets": assets("1.4.5")}, "1.4.4")
    assert path == tmp_path / "dopeIPTV-update" / "dopeIPTV-1.4.5-windows-x64-setup.exe" == got["dest"]


def test_the_installer_starts_at_once_and_waits_for_the_app_itself(monkeypatch):
    # Started by the app while its window is in front, so Windows lets the
    # installer's progress - and the new version it opens - come to the
    # front; it waits for this process to exit before it replaces anything.
    seen = {}

    class Popen:
        def __init__(self, argv, **kw):
            seen["argv"], seen["kw"] = argv, kw

    monkeypatch.setattr(winupdate.subprocess, "Popen", Popen)
    monkeypatch.setattr(winupdate, "verify_again", lambda path: seen.setdefault("checked", path))
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:/old")
    setup = Path("C:/Users/O'Brien/AppData/Local/Temp/dopeIPTV-update/x-setup.exe")
    winupdate.restart_into(setup, pid=4242)
    assert seen["checked"] == setup, "the installer checked again right before it runs"
    assert seen["argv"] == [str(setup), "/update", "/relaunch", "/waitpid=4242"]
    env = seen["kw"]["env"]
    assert "_PYI_APPLICATION_HOME_DIR" not in env and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"


def test_a_release_that_is_not_newer_is_never_fetched(monkeypatch):
    monkeypatch.setattr(winupdate, "can_self_update", lambda: True)
    monkeypatch.setattr(winupdate, "arch", lambda: "x64")
    monkeypatch.setattr(winupdate, "signed_asset", lambda rel, asset: pytest.fail("asked for a signature"))
    monkeypatch.setattr(winupdate, "download", lambda *a: pytest.fail("downloaded"))
    for old in ("v1.4.4", "v1.4.3", "v1.2.0", ""):
        with pytest.raises(selfupdate.UpdateError):
            winupdate.install({"tag": old, "assets": assets(old.lstrip("v") or "1.4.3")}, "1.4.4")


def test_the_installer_waits_in_the_users_own_folder_not_the_shared_temp(monkeypatch, tmp_path):
    # A PC where every account shares one temp folder: another account could
    # make dopeIPTV-update there first and swap the installer once checked.
    local = tmp_path / "Local"
    local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    assert winupdate.update_folder() == local / "dopeiptv" / "dopeIPTV" / "update"
    assert winupdate.update_folder() != winupdate.legacy_update_folder()
    # Without one (not Windows, or a broken environment) it is where it was.
    monkeypatch.delenv("LOCALAPPDATA")
    assert winupdate.update_folder() == winupdate.legacy_update_folder()
    monkeypatch.setenv("LOCALAPPDATA", "relative")
    assert winupdate.update_folder() == winupdate.legacy_update_folder()


def test_cleanup_removes_this_versions_and_the_older_versions_folder(monkeypatch, tmp_path):
    local, temp = tmp_path / "Local", tmp_path / "Temp"
    (local / "dopeiptv" / "dopeIPTV" / "update").mkdir(parents=True)
    (temp / "dopeIPTV-update").mkdir(parents=True)
    (local / "dopeiptv" / "dopeIPTV" / "update" / "setup.exe").write_bytes(b"x")
    (temp / "dopeIPTV-update" / "setup.exe").write_bytes(b"x")
    (local / "dopeiptv" / "dopeIPTV" / "cache").mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(winupdate.tempfile, "gettempdir", lambda: str(temp))
    winupdate.cleanup()
    assert not (local / "dopeiptv" / "dopeIPTV" / "update").exists()
    assert not (temp / "dopeIPTV-update").exists()
    assert (local / "dopeiptv" / "dopeIPTV" / "cache").is_dir()      # nothing else of the app's


def test_windows_refusing_the_installer_is_said_not_a_crash():
    """Error report a48a3a90ef4e: Smart App Control stopped the downloaded
    installer (WinError 4551) and the app reported a crash. Windows' own
    refusals are told apart from a missing file, and the update window says
    which - the start of the installer is never left uncaught."""
    from dopeiptv.i18n import set_language, tr
    from dopeiptv.ui import mw_updates

    def refused(code):
        e = OSError(f"[WinError {code}] blocked")
        e.winerror = code
        return e
    for code in (4551, 4556, 4559, 1260, 225):
        assert winupdate.blocked_by_policy(refused(code)), code
    for code in (2, 5, 193):
        assert not winupdate.blocked_by_policy(refused(code)), code
    assert not winupdate.blocked_by_policy(FileNotFoundError("gone"))

    set_language("en")
    assert mw_updates.start_refused(refused(4551)) == tr("update_blocked_windows")
    other = mw_updates.start_refused(refused(2))
    assert other.endswith(tr("update_nothing_changed")) and "WinError 2" in other
    src = Path(mw_updates.__file__).read_text(encoding="utf-8")
    at = src.index("updater.restart_into(new)")
    assert src.rfind("try:", 0, at) > src.rfind("def chosen", 0, at)
    assert "except OSError" in src[at:at + 200]


def test_an_installer_changed_since_its_check_never_runs(monkeypatch, tmp_path):
    from dopeiptv.core import selfupdate
    started = []
    monkeypatch.setattr(winupdate.subprocess, "Popen", lambda *a, **k: started.append(a))
    setup = tmp_path / "x-setup.exe"
    setup.write_bytes(b"checked bytes")
    import hashlib
    monkeypatch.setitem(selfupdate._VERIFIED, str(setup), hashlib.sha256(b"checked bytes").hexdigest())
    setup.write_bytes(b"something else")
    with pytest.raises(selfupdate.UpdateError):
        winupdate.restart_into(setup, pid=4242)
    assert started == [] and not setup.exists()
    # one never checked at all: the same
    other = tmp_path / "y-setup.exe"
    other.write_bytes(b"x")
    with pytest.raises(selfupdate.UpdateError):
        winupdate.restart_into(other, pid=4242)
    assert started == []
