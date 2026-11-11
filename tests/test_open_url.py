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

    assert xdg.open_url("https://dopeiptv.org") is True
    assert seen["cmd"] == ["/usr/bin/xdg-open", "https://dopeiptv.org"]
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
    assert xdg.open_url("https://dopeiptv.org") is True
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


def test_on_a_desktop_qt_opens_it_with_the_system_paths_for_the_moment(
        monkeypatch):
    """Qt's opener passes the activation token that brings the browser to
    the front on GNOME (the donate page opened behind the app without it).
    The handler it may start must still not inherit our library paths, and
    we get ours back after."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", "/opt/dopeiptv/lib")
    monkeypatch.setattr(xdg, "_qt_platform", lambda: "wayland")
    monkeypatch.setattr(xdg, "_focus_settled", lambda: True)   # the window has it
    seen = {}

    def fake_qt_open(url):
        seen["url"] = url
        seen["ld"] = xdg.os.environ.get("LD_LIBRARY_PATH")
        return False            # say it failed: the paths come back at once

    monkeypatch.setattr(xdg, "_qt_open", fake_qt_open)
    monkeypatch.setattr(xdg.shutil, "which", lambda n: None)
    xdg.open_url("https://dopeiptv.org/?ref=app-heart#donate")
    assert seen["url"] == "https://dopeiptv.org/?ref=app-heart#donate"
    assert seen["ld"] is None                       # stripped for the launch
    assert xdg.os.environ["LD_LIBRARY_PATH"] == "/opt/dopeiptv/lib"   # and back


def test_without_a_desktop_session_qt_is_not_asked(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(xdg, "_qt_platform", lambda: "offscreen")
    monkeypatch.setattr(xdg, "_qt_open", lambda url: pytest.fail("asked Qt"))
    monkeypatch.setattr(xdg.shutil, "which", lambda n: None)
    xdg.open_url("https://dopeiptv.org")


def _menu_case(monkeypatch, focus):
    """A link chosen from a menu: *focus* says, check by check, whether a
    real window (not the menu's popup) has the focus yet."""
    monkeypatch.setattr(xdg, "_qt_platform", lambda: "wayland")
    monkeypatch.setattr(xdg, "_focus_settled", lambda: next(focus))
    timers = []
    monkeypatch.setattr(xdg, "_later", lambda ms, fn: timers.append((ms, fn)))
    opened = []
    monkeypatch.setattr(xdg, "_qt_open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(xdg.shutil, "which", lambda n: None)
    return timers, opened


def test_a_link_from_a_menu_waits_for_the_window_to_have_the_focus(monkeypatch):
    """Qt asks GNOME for the token for the focused window. Chosen from a
    menu that was the popup, or no window, and the page opened behind the
    app; the link now waits until the window has the focus back."""
    timers, opened = _menu_case(monkeypatch, iter([False, False, False, True]))
    assert xdg.open_url("https://dopeiptv.org/?ref=app-menu#donate") is True
    assert opened == []                  # not yet: the popup has the focus
    while timers and not opened:
        timers.pop(0)[1]()
    assert opened == ["https://dopeiptv.org/?ref=app-menu#donate"]
    assert timers == []


def test_a_link_from_a_menu_still_opens_if_the_focus_never_comes(monkeypatch):
    timers, opened = _menu_case(monkeypatch, iter([False] * 1000))
    xdg.open_url("https://dopeiptv.org/")
    steps = 0
    while timers:
        timers.pop(0)[1]()
        steps += 1
    assert opened == ["https://dopeiptv.org/"]
    assert steps * xdg._FOCUS_STEP_MS >= xdg.FOCUS_WAIT_MS


def test_a_deferred_link_falls_back_to_the_desktops_opener(monkeypatch):
    timers, _opened = _menu_case(monkeypatch, iter([False, True]))
    monkeypatch.setattr(xdg, "_qt_open", lambda url: False)
    spawned = []
    monkeypatch.setattr(xdg.shutil, "which",
                        lambda n: "/usr/bin/xdg-open" if n == "xdg-open" else None)
    monkeypatch.setattr(xdg.subprocess, "Popen",
                        lambda cmd, **kw: spawned.append(cmd))
    xdg.open_url("https://dopeiptv.org/")
    while timers:
        timers.pop(0)[1]()
    assert spawned == [["/usr/bin/xdg-open", "https://dopeiptv.org/"]]

