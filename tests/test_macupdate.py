"""The Mac update: the .dmg for this build's CPU, its app accepted only
when it is intact, Solecraft AB's and notarised, put beside this one, and
swapped in once the app has quit."""
from __future__ import annotations

import plistlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dopeiptv.core import macupdate, selfupdate


def assets(version):
    names = [f"dopeIPTV-{version}-macOS-arm64.dmg", f"dopeIPTV-{version}-macOS-x86_64.dmg",
             f"dopeIPTV-{version}-windows-x64-setup.exe"]
    return [{"name": n, "url": f"https://dopeiptv.org/dl.php?f={n}", "size": 3,
             "digest": "sha256:" + "0" * 64} for n in names]


def test_the_dmg_for_this_cpu_is_picked():
    assert macupdate.pick_asset(assets("1.4.5"), "v1.4.5", "arm64")["name"] == "dopeIPTV-1.4.5-macOS-arm64.dmg"
    assert macupdate.pick_asset(assets("1.4.5"), "v1.4.5", "x86_64")["name"].endswith("x86_64.dmg")
    assert macupdate.pick_asset(assets("1.4.5"), "v1.4.6", "arm64") is None


@pytest.mark.parametrize("plat,cpu", [("macosx-11.0-arm64", "arm64"), ("macosx-10.15-x86_64", "x86_64"),
                                      ("linux-x86_64", "x86_64"), ("win-amd64", None)])
def test_the_cpu_is_the_builds_own(monkeypatch, plat, cpu):
    monkeypatch.setattr(macupdate.sysconfig, "get_platform", lambda: plat)
    assert macupdate.arch() == cpu


@pytest.mark.parametrize("machine,cpu", [("arm64", "arm64"), ("x86_64", "x86_64"), ("ppc", None)])
def test_a_universal2_python_takes_the_cpu_this_process_runs_as(monkeypatch, machine, cpu):
    # The builds' Python is universal2 (python.org's installer, as
    # actions/setup-python ships it): sysconfig names no CPU, so no copy
    # could update itself - About's button opened a web page instead.
    monkeypatch.setattr(macupdate.sysconfig, "get_platform", lambda: "macosx-10.13-universal2")
    monkeypatch.setattr(macupdate.platform, "machine", lambda: machine)
    assert macupdate.arch() == cpu


@pytest.fixture
def mac_app(monkeypatch, tmp_path):
    app = tmp_path / "Applications" / "dopeIPTV.app"
    exe = app / "Contents" / "MacOS" / "dopeiptv"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(macupdate.sysconfig, "get_platform", lambda: "macosx-11.0-arm64")
    macupdate._team_id.cache_clear()
    return app.resolve()


def test_only_our_signed_app_in_a_writable_folder_updates_itself(monkeypatch, mac_app):
    assert macupdate.app_bundle() == mac_app
    monkeypatch.setattr(macupdate, "_team_id", lambda app: macupdate.TEAM_ID)
    assert macupdate.can_self_update()
    monkeypatch.setattr(macupdate, "_team_id", lambda app: "")          # ad hoc: a fork's build
    assert not macupdate.can_self_update()


def test_a_translocated_copy_is_offered_the_download_instead(monkeypatch, tmp_path):
    exe = tmp_path / "AppTranslocation" / "X" / "d" / "dopeIPTV.app" / "Contents" / "MacOS" / "dopeiptv"
    exe.parent.mkdir(parents=True)
    exe.write_text("x")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(macupdate, "_team_id", lambda app: macupdate.TEAM_ID)
    assert not macupdate.can_self_update()


class FakeTools:
    """codesign, spctl, hdiutil and ditto as a Mac would answer them."""

    def __init__(self, mount: Path, team: str = macupdate.TEAM_ID, gatekeeper: str = "accepted\nsource=Notarized Developer ID"):
        self.mount, self.team, self.gatekeeper, self.calls = mount, team, gatekeeper, []

    def run(self, argv, **kw):
        self.calls.append(argv[0] if argv[0] != "codesign" else " ".join(argv[:2]))
        tool = argv[0]
        if tool == "hdiutil" and argv[1] == "attach":
            (self.mount / "dopeIPTV.app" / "Contents").mkdir(parents=True, exist_ok=True)
            out = plistlib.dumps({"system-entities": [{"dev-entry": "/dev/disk9"},
                                                      {"mount-point": str(self.mount)}]}).decode()
            return SimpleNamespace(returncode=0, stdout=out, stderr="")
        if tool == "codesign" and argv[1] == "-dv":
            return SimpleNamespace(returncode=0, stdout="", stderr=f"Identifier=x\nTeamIdentifier={self.team}\n")
        if tool == "spctl":
            return SimpleNamespace(returncode=0 if "accepted" in self.gatekeeper else 3, stdout="",
                                   stderr=self.gatekeeper)
        if tool == "ditto":
            Path(argv[2]).mkdir(parents=True)
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def install_with(monkeypatch, mac_app, tools):
    monkeypatch.setattr(macupdate, "can_self_update", lambda: True)
    monkeypatch.setattr(macupdate, "download", lambda asset, dest, p, c: dest.write_text("dmg"))
    monkeypatch.setattr(macupdate.subprocess, "run", tools.run)
    macupdate._team_id.cache_clear()
    return macupdate.install({"tag": "v1.4.5", "assets": assets("1.4.5")}, "1.4.4")


def test_a_notarised_app_of_ours_is_put_beside_this_one(monkeypatch, mac_app, tmp_path):
    tools = FakeTools(tmp_path / "mnt")
    staged = install_with(monkeypatch, mac_app, tools)
    assert staged == mac_app.parent / ".dopeIPTV-update.app" and staged.is_dir()
    assert tools.calls.index("spctl") < tools.calls.index("ditto")      # checked before it is copied
    assert "hdiutil" in tools.calls[-3:] or tools.calls.count("hdiutil") == 2   # mounted, then detached


@pytest.mark.parametrize("team,gatekeeper,step", [
    ("ABCDE12345", "accepted\nsource=Notarized Developer ID", "Team ID"),   # someone else's
    (macupdate.TEAM_ID, "rejected\nsource=Unnotarized Developer ID", "Gatekeeper"),
])
def test_an_app_that_is_not_ours_or_not_notarised_is_refused(monkeypatch, mac_app, tmp_path, team, gatekeeper, step):
    tools = FakeTools(tmp_path / "mnt", team=team, gatekeeper=gatekeeper)
    with pytest.raises(selfupdate.UpdateError) as e:
        install_with(monkeypatch, mac_app, tools)
    assert e.value.key == "upd_err_mac_check" and e.value.kwargs["step"] == step
    assert not (mac_app.parent / ".dopeIPTV-update.app").exists()
    assert "ditto" not in tools.calls


def test_the_swap_waits_for_the_app_to_quit(monkeypatch, mac_app):
    seen = {}

    class Popen:
        def __init__(self, argv, **kw):
            seen["argv"] = argv

    monkeypatch.setattr(macupdate.subprocess, "Popen", Popen)
    staged = mac_app.parent / ".dopeIPTV-update.app"
    macupdate.restart_into(staged, pid=777)
    argv = seen["argv"]
    assert argv[:2] == ["/bin/sh", "-c"] and argv[3] == "777"
    assert argv[4:] == [str(mac_app), str(staged), str(mac_app.parent / ".dopeIPTV-previous.app"), "1"]
    script = argv[2]
    assert script.index('kill -0 "$0"') < script.index('mv "$app" "$aside"')
    macupdate.on_exit(staged, pid=777)
    assert seen["argv"][-1] == "0"                      # swapped at quit, not opened


def test_a_test_build_is_held_to_our_signature_not_to_notarisation(monkeypatch, mac_app, tmp_path):
    # Test builds are signed but never sent to Apple (release.yml).
    tools = FakeTools(tmp_path / "mnt", gatekeeper="rejected\nsource=Unnotarized Developer ID")
    monkeypatch.setattr(macupdate, "can_self_update", lambda: True)
    monkeypatch.setattr(macupdate, "download", lambda asset, dest, p, c: dest.write_text("dmg"))
    monkeypatch.setattr(macupdate.subprocess, "run", tools.run)
    macupdate._team_id.cache_clear()
    staged = macupdate.install({"tag": "v1.4.5.80", "assets": assets("1.4.5.80")}, "1.4.5.79")
    assert staged.is_dir() and "spctl" not in tools.calls
    # ...but someone else's signature is refused all the same.
    tools2 = FakeTools(tmp_path / "mnt2", team="ABCDE12345")
    monkeypatch.setattr(macupdate.subprocess, "run", tools2.run)
    macupdate._team_id.cache_clear()
    with pytest.raises(selfupdate.UpdateError):
        macupdate.install({"tag": "v1.4.5.80", "assets": assets("1.4.5.80")}, "1.4.5.79")
