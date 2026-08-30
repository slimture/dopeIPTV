"""The taskbar showed a blank placeholder for the running window.

Wayland ignores setWindowIcon: GNOME and KDE read a window's icon from the
.desktop file whose name matches its app_id, and nothing has ever
installed one for an AppImage. From the .deb the entry is there and the
icon is right.

Asked rather than done - the AppImage format's whole premise is a file
that changes nothing outside itself - so these pin the shape of what gets
written, and that it is only ever our own file that gets taken away.
"""
import os
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


def test_the_written_entry_is_a_valid_desktop_file(home, monkeypatch):
    """A malformed .desktop is not a degraded entry - GNOME ignores the
    file completely, and the symptom is an icon that never appears with
    nothing anywhere saying why."""
    import shutil
    import subprocess

    exe = shutil.which("desktop-file-validate")
    if not exe:
        pytest.skip("desktop-file-utils not installed")
    monkeypatch.setenv("APPIMAGE", str(home / "x.AppImage"))
    (home / "x.AppImage").write_text("")
    assert desktop_entry.install() is True
    r = subprocess.run([exe, str(desktop_entry.entry_path())],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_describe_names_the_cause_whichever_it_is(home, monkeypatch):
    """"No icon" has four causes that look identical from outside. This
    line is what stops the next one being a guessing game."""
    monkeypatch.setenv("APPIMAGE", str(home / "x.AppImage"))
    (home / "x.AppImage").write_text("")

    before = desktop_entry.describe()
    assert "installed=False" in before and "can_offer=True" in before
    assert "icons=NONE" in before          # the icon half, reported too

    desktop_entry.install()
    after = desktop_entry.describe()
    assert "installed=True" in after and "can_offer=False" in after
    assert str(desktop_entry.entry_path()) in after


# ------------------------------------------------------- icon theme cache ---

class _StubIcon:
    """install_icon only ever asks an icon for pixmap(w, h).save(path).
    Every file these tests care about is already on disk, so it is never
    called - and the tests stay free of a QApplication, which cannot be
    built in this process without a platform plugin."""

    def pixmap(self, *_a):
        raise AssertionError("an existing icon file was overwritten")


def _install_icon():
    """Run the real install_icon against the patched XDG_DATA_HOME."""
    from dopeiptv.app import install_icon
    install_icon(_StubIcon())


def test_a_stale_icon_cache_is_dropped_so_the_icon_can_be_seen(
        tmp_path, monkeypatch):
    """The icon files were right, the .desktop was right, and the desktop
    still showed nothing - because GTK reads icon-theme.cache instead of
    the directory, and this one was built four months before the icons.

    Keyed on the cache being OLDER than the icons, not on having just
    written one: by the time this bites, the icons are already on disk and
    there is nothing new to write."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    theme = tmp_path / "icons" / "hicolor"
    for size in (256, 128, 64, 48, 32):
        d = theme / f"{size}x{size}" / "apps"
        d.mkdir(parents=True)
        (d / "dopeiptv.png").write_bytes(b"")
    cache = theme / "icon-theme.cache"
    cache.write_bytes(b"")
    os.utime(cache, (0, 0))          # long predates the icons

    _install_icon()
    assert not cache.exists(), "the stale cache hid the icons and survived"


def test_a_current_icon_cache_is_left_alone(tmp_path, monkeypatch):
    """It is somebody else's derived data. Dropping one that already knows
    about us buys nothing and costs every other icon a directory scan."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    theme = tmp_path / "icons" / "hicolor"
    for size in (256, 128, 64, 48, 32):
        d = theme / f"{size}x{size}" / "apps"
        d.mkdir(parents=True)
        p = d / "dopeiptv.png"
        p.write_bytes(b"")
        os.utime(p, (1000, 1000))
    cache = theme / "icon-theme.cache"
    cache.write_bytes(b"")
    os.utime(cache, (2000, 2000))    # newer than the icons

    _install_icon()
    assert cache.exists(), "a cache that postdates the icons was removed"
