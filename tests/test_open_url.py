"""Links in the About window did nothing on Linux.

QDesktopServices.openUrl runs xdg-open as a CHILD of this process, so from
a frozen bundle the browser inherited our LD_LIBRARY_PATH, loaded our
libraries instead of its own and fell over before drawing anything - which
from the user's side is a link that simply does not work. Exactly the
fault external mpv had, and exactly the same cure.
"""
import sys

import pytest

from dopeiptv.core import xdg


# ------------------------------------------------------------- open_url ---

def test_a_link_is_opened_with_the_bundles_library_paths_stripped(
        monkeypatch):
    """The whole bug in one assertion: the handler must not inherit
    LD_LIBRARY_PATH, or the browser loads our libstdc++ and dies."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/dopeiptv/lib")
    monkeypatch.setenv("QT_PLUGIN_PATH", "/opt/dopeiptv/plugins")
    monkeypatch.setattr(xdg.shutil, "which",
                        lambda n: "/usr/bin/xdg-open"
                        if n == "xdg-open" else None)
    seen = {}

    def fake_popen(cmd, **kw):
        seen["cmd"] = cmd
        seen["env"] = kw.get("env")
        seen["session"] = kw.get("start_new_session")
        return object()

    monkeypatch.setattr(xdg.subprocess, "Popen", fake_popen)

    assert xdg.open_url("https://iptv.dope.rs") is True
    assert seen["cmd"] == ["/usr/bin/xdg-open", "https://iptv.dope.rs"]
    assert "LD_LIBRARY_PATH" not in seen["env"]
    assert "QT_PLUGIN_PATH" not in seen["env"]
    # Not killed when this process exits.
    assert seen["session"] is True


def test_the_pre_bundle_value_is_restored_when_there_was_one(monkeypatch):
    """PyInstaller stashes what the variable held BEFORE the bundle set
    it. Dropping the variable is only right when there was nothing there
    to begin with - otherwise the child loses the user's own setting."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/dopeiptv/lib")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/usr/local/lib")
    assert xdg.system_env()["LD_LIBRARY_PATH"] == "/usr/local/lib"


def test_nothing_is_stripped_when_we_are_not_a_frozen_bundle(monkeypatch):
    """These variables are ours in a PyInstaller build and nobody else's.
    Under Flatpak the runtime sets LD_LIBRARY_PATH to /app/lib for its own
    reasons, and stripping that from a child would break the very thing we
    are trying to launch."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/app/lib")
    assert xdg.system_env()["LD_LIBRARY_PATH"] == "/app/lib"


def test_the_next_handler_is_tried_when_one_is_missing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(xdg.shutil, "which",
                        lambda n: "/usr/bin/gio" if n == "gio" else None)
    seen = {}
    monkeypatch.setattr(xdg.subprocess, "Popen",
                        lambda cmd, **kw: seen.setdefault("cmd", cmd))
    assert xdg.open_url("https://iptv.dope.rs") is True
    # gio takes a verb; xdg-open does not.
    assert seen["cmd"][:2] == ["/usr/bin/gio", "open"]


def test_an_empty_url_opens_nothing(monkeypatch):
    monkeypatch.setattr(xdg.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("spawned for no URL"))
    assert xdg.open_url("") is False


def test_players_still_uses_the_one_environment_helper():
    """It moved to core.xdg because opening a link needs exactly the same
    cure; external mpv must not quietly go back to inheriting the bundle."""
    from dopeiptv.media import players
    assert players._system_env is xdg.system_env
