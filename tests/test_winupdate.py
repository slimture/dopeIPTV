"""The Windows update: the installer for this build's CPU, fetched and
checked, then run silently once the app has exited - only for a copy the
installer put there."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dopeiptv.core import selfupdate, winupdate


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
    monkeypatch.setattr(winupdate, "download", lambda asset, dest, p, c: got.update(name=asset["name"], dest=dest))
    path = winupdate.install({"tag": "v1.4.5", "assets": assets("1.4.5")}, "1.4.4")
    assert path == tmp_path / "dopeIPTV-update" / "dopeIPTV-1.4.5-windows-x64-setup.exe" == got["dest"]


def test_the_installer_runs_silently_after_the_app_has_exited(monkeypatch):
    seen = {}

    class Popen:
        def __init__(self, argv, **kw):
            seen["argv"], seen["kw"] = argv, kw

    monkeypatch.setattr(winupdate.subprocess, "Popen", Popen)
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", "C:/old")
    winupdate.restart_into(Path("C:/Users/O'Brien/AppData/Local/Temp/dopeIPTV-update/x-setup.exe"), pid=4242)
    script = seen["argv"][-1]
    assert seen["argv"][0] == "powershell.exe"
    assert script.index("Wait-Process -Id 4242") < script.index("Start-Process")
    assert "'/S /relaunch'" in script
    assert "O''Brien" in script                       # quoted for PowerShell
    env = seen["kw"]["env"]
    assert "_PYI_APPLICATION_HOME_DIR" not in env and env["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
