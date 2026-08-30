"""The taskbar showed a blank placeholder for the running window.

Wayland ignores setWindowIcon: GNOME and KDE read a window's icon from the
.desktop file whose name matches its app_id, and nothing has ever
installed one for an AppImage. From the .deb the entry is there and the
icon is right.

Asked rather than done - the AppImage format's whole premise is a file
that changes nothing outside itself - so these pin the shape of what gets
written, and that it is only ever our own file that gets taken away.
"""
import sys

import pytest

from dopeiptv.core import desktop_entry


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "nowhere"))
    return tmp_path

def test_the_entry_points_at_the_appimage_not_the_mount(home, monkeypatch):
    """$APPIMAGE is the .AppImage file itself. The temporary mount it runs
    from is gone the moment the app exits, so an entry written against
    that path would launch nothing the second time."""
    appimage = home / "dopeIPTV-1.2.11-x86_64.AppImage"
    appimage.write_text("")
    monkeypatch.setenv("APPIMAGE", str(appimage))

    assert desktop_entry.can_offer() == sys.platform.startswith("linux")
    assert desktop_entry.install() is True
    body = desktop_entry.entry_path().read_text(encoding="utf-8")
    assert f"Exec={appimage} %U" in body
    # The icon name has to be the app_id, or Wayland never finds it.
    assert "Icon=dopeiptv" in body
    assert desktop_entry.entry_path().name == "dopeiptv.desktop"
    assert desktop_entry.is_installed()


def test_a_path_with_a_space_is_quoted(home, monkeypatch):
    appimage = home / "My Apps" / "dopeIPTV.AppImage"
    appimage.parent.mkdir()
    appimage.write_text("")
    monkeypatch.setenv("APPIMAGE", str(appimage))
    desktop_entry.install()
    body = desktop_entry.entry_path().read_text(encoding="utf-8")
    assert f'Exec="{appimage}" %U' in body


def test_nothing_is_offered_when_a_package_already_installed_one(
        home, monkeypatch):
    """A .deb ships its own entry. Offering to add a second would be
    noise, and the icon already works."""
    sysdir = home / "sys"
    (sysdir / "applications").mkdir(parents=True)
    (sysdir / "applications" / "dopeiptv.desktop").write_text("")
    monkeypatch.setenv("XDG_DATA_DIRS", str(sysdir))
    monkeypatch.setenv("APPIMAGE", str(home / "x.AppImage"))
    (home / "x.AppImage").write_text("")
    assert desktop_entry.system_entry_exists() is True
    assert desktop_entry.can_offer() is False


def test_nothing_is_offered_without_a_stable_command(home, monkeypatch):
    """A source checkout run as `python -m` has no path worth writing
    down - better no entry than one that breaks on the next rename."""
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(desktop_entry.shutil, "which", lambda _n: None)
    assert desktop_entry.exec_command() is None
    assert desktop_entry.can_offer() is False


def test_removal_only_ever_takes_away_our_own(home, monkeypatch):
    """The switch must never delete an entry a package manager owns, or
    put one there by hand."""
    monkeypatch.setenv("APPIMAGE", str(home / "x.AppImage"))
    (home / "x.AppImage").write_text("")
    desktop_entry.install()
    assert desktop_entry.remove() is True
    assert not desktop_entry.is_installed()

    # Somebody else's file at the same path: left exactly where it is.
    p = desktop_entry.entry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[Desktop Entry]\nName=hand written\n")
    assert desktop_entry.remove() is False
    assert p.exists()


def test_a_flatpak_is_left_alone(home, monkeypatch):
    """It ships its own entry and cannot see the host's applications
    directory anyway."""
    monkeypatch.setattr(desktop_entry, "_in_flatpak", lambda: True)
    monkeypatch.setenv("APPIMAGE", str(home / "x.AppImage"))
    (home / "x.AppImage").write_text("")
    assert desktop_entry.can_offer() is False
