"""Programs started on Windows by their whole path (CORE-21): a bare
"ipconfig" or "powershell" is looked for in the app's folder and the
current one before System32, and shutil.which (Python 3.11) puts the
current folder first too - a folder the person opened a file from could
hold an ffmpeg.exe, an mpv.exe or a powershell.exe of its own. System
tools by their path under %SystemRoot%\\System32, ffmpeg and mpv only from
the PATH's own folders; the installer's PowerShell from $SYSDIR."""
from __future__ import annotations

import os
import sys

import pytest

from dopeiptv.core import nerd, player_exec

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_a_system_tool_is_named_by_its_path_under_system32(monkeypatch):
    monkeypatch.setenv("SystemRoot", "D:\\Win")
    assert player_exec.system_program("ipconfig.exe") == "D:\\Win\\System32\\ipconfig.exe"
    assert player_exec.system_program("WindowsPowerShell", "v1.0", "powershell.exe") == \
        "D:\\Win\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    monkeypatch.delenv("SystemRoot")
    monkeypatch.delenv("windir", raising=False)
    assert player_exec.system_program("ipconfig.exe") == "C:\\Windows\\System32\\ipconfig.exe"


def test_ipconfig_is_run_by_its_path(monkeypatch):
    calls = []
    monkeypatch.setenv("SystemRoot", "C:\\Windows")
    monkeypatch.setattr(nerd.sys, "platform", "win32")
    monkeypatch.setattr(nerd, "_tunnels_seen", (-1e9, []))
    monkeypatch.setattr(nerd, "_run", lambda cmd: calls.append(cmd) or "")
    nerd.active_tunnels()
    assert calls == [["C:\\Windows\\System32\\ipconfig.exe"]]


@pytest.mark.skipif(os.name != "posix", reason="the folders are made with POSIX paths")
def test_ffmpeg_and_mpv_never_come_from_the_current_folder(monkeypatch, tmp_path):
    here = tmp_path / "Downloads"
    on_path = tmp_path / "bin"
    for folder in (here, on_path):
        folder.mkdir()
    (here / "ffmpeg.exe").write_bytes(b"planted")
    (here / "mpv.exe").write_bytes(b"planted")
    (on_path / "mpv.bat").write_bytes(b"@echo a batch file")
    monkeypatch.chdir(here)
    monkeypatch.setattr(player_exec.sys, "platform", "win32")
    monkeypatch.setenv("PATH", os.pathsep.join(["", ".", "Downloads", str(on_path)]))
    assert player_exec.find_program("ffmpeg") is None
    assert player_exec.find_program("mpv") is None, "nor a batch file cmd.exe would read again"
    (on_path / "ffmpeg.exe").write_bytes(b"real")
    assert player_exec.find_program("ffmpeg") == str(on_path / "ffmpeg.exe")
    assert player_exec.find_program("ffmpeg.exe") == str(on_path / "ffmpeg.exe")
    from dopeiptv.core.recording import RecordingManager
    assert RecordingManager.ffmpeg() == str(on_path / "ffmpeg.exe")
    assert player_exec.find_player_executable("mpv") is None


def test_the_installer_starts_powershell_by_its_path():
    with open(os.path.join(_REPO_ROOT, "packaging", "windows", "dopeiptv.nsi"),
              encoding="utf-8") as fh:
        text = fh.read()
    runs = [line for line in text.splitlines() if "nsExec::" in line and "powershell" in line.lower()]
    assert runs, "the installer still closes a running copy"
    for line in runs:
        assert '`"$SYSDIR\\WindowsPowerShell\\v1.0\\powershell.exe" ' in line, line


@pytest.mark.skipif(sys.platform == "win32", reason="the code is read, not run")
def test_the_app_names_no_windows_tool_bare():
    """ipconfig and powershell by their paths wherever the app starts them."""
    import re
    bare = re.compile(r"""\[\s*["'](?:ipconfig|powershell)(?:\.exe)?["']""", re.IGNORECASE)
    found = []
    for folder, _dirs, files in os.walk(os.path.join(_REPO_ROOT, "dopeiptv")):
        for name in files:
            if name.endswith(".py"):
                path = os.path.join(folder, name)
                with open(path, encoding="utf-8") as fh:
                    if bare.search(fh.read()):
                        found.append(os.path.relpath(path, _REPO_ROOT))
    assert found == []
