"""Updating a running AppImage in place.

"Download the update" left two files and a dock icon still starting the
old one - the menu entry names the exact file. The app now fetches the
new AppImage itself, and nothing on disk changes until the download has
matched GitHub's checksum and the new file has passed its own start-up
check. These run the real code against fake AppImages (shell scripts that
pass or fail --self-check) and a fake download.
"""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest

from dopeiptv.core import desktop_entry, selfupdate, updates
from dopeiptv.core.selfupdate import UpdateError

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="AppImages are Linux-only")

GOOD = b"#!/bin/sh\n[ \"$1\" = --self-check ] && exit 0\nexit 0\n"
BROKEN = b"#!/bin/sh\nexit 1\n"


class _Resp:
    def __init__(self, body: bytes) -> None:
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self) -> None:
        pass

    def iter_content(self, chunk_size: int):
        for i in range(0, len(self.body), chunk_size):
            yield self.body[i:i + chunk_size]


@pytest.fixture
def env(tmp_path, monkeypatch):
    apps = tmp_path / "Downloads"
    apps.mkdir()
    current = apps / "dopeIPTV-1.2.14-x86_64.AppImage"
    current.write_bytes(GOOD)
    current.chmod(0o755)
    monkeypatch.setenv("APPIMAGE", str(current))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(selfupdate.platform, "machine", lambda: "x86_64")
    served: dict = {}

    class _Requests:
        @staticmethod
        def get(url, **_kw):
            return _Resp(served[url])

    monkeypatch.setattr(selfupdate, "requests", _Requests)
    return current, served


def _release(served: dict, body: bytes, version: str = "1.2.15",
             digest: str | None = None) -> dict:
    name = f"dopeIPTV-{version}-x86_64.AppImage"
    url = f"https://example.invalid/{name}"
    served[url] = body
    sha = digest or "sha256:" + hashlib.sha256(body).hexdigest()
    return {"tag": f"v{version}", "assets": [
        {"name": f"dopeIPTV-{version}-aarch64.AppImage", "url": url + "-arm",
         "size": 1, "digest": ""},
        {"name": name, "url": url, "size": len(body), "digest": sha}]}


def _leftovers(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


def test_installs_beside_the_old_one_and_moves_the_menu_entry(env):
    current, served = env
    assert selfupdate.can_self_update()
    assert desktop_entry.install()
    new = selfupdate.install(_release(served, GOOD), "1.2.14")
    assert new == current.with_name("dopeIPTV-1.2.15-x86_64.AppImage")
    assert new.read_bytes() == GOOD and os.access(new, os.X_OK)
    assert current.exists(), "the old file goes only once the new one ran"
    assert _leftovers(current.parent) == [current.name, new.name]
    assert f"Exec={new}\n" in desktop_entry.entry_path().read_text()

    # The new version, started, removes the old one.
    assert selfupdate.take_pending_cleanup(str(current), new)
    assert not current.exists()


def test_a_corrupt_download_changes_nothing(env):
    current, served = env
    rel = _release(served, GOOD, digest="sha256:" + "0" * 64)
    with pytest.raises(UpdateError, match="checksum"):
        selfupdate.install(rel, "1.2.14")
    assert _leftovers(current.parent) == [current.name]


def test_a_new_version_that_does_not_start_changes_nothing(env):
    current, served = env
    assert desktop_entry.install()
    before = desktop_entry.entry_path().read_text()
    with pytest.raises(UpdateError, match="start-up check"):
        selfupdate.install(_release(served, BROKEN), "1.2.14")
    assert _leftovers(current.parent) == [current.name]
    assert desktop_entry.entry_path().read_text() == before


def test_a_renamed_appimage_is_replaced_under_its_own_name(env, monkeypatch):
    current, served = env
    mine = current.with_name("dopeIPTV.AppImage")
    current.rename(mine)
    monkeypatch.setenv("APPIMAGE", str(mine))
    body = GOOD + b"# 1.2.15\n"
    assert selfupdate.install(_release(served, body), "1.2.14") == mine
    assert mine.read_bytes() == body
    assert _leftovers(mine.parent) == [mine.name]


def test_no_asset_for_this_machine(env, monkeypatch):
    _current, served = env
    monkeypatch.setattr(selfupdate.platform, "machine", lambda: "riscv64")
    assert not selfupdate.can_self_update()
    with pytest.raises(UpdateError):
        selfupdate.install(_release(served, GOOD), "1.2.14")


def test_not_an_appimage(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert selfupdate.appimage_path() is None
    assert not selfupdate.can_self_update()


def _entry(text: str) -> None:
    p = desktop_entry.entry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def test_menu_entry_follows_a_replaced_appimage_only(env, tmp_path):
    current, _served = env
    gone = tmp_path / "Downloads" / "dopeIPTV-1.2.11-x86_64.AppImage"
    _entry(f"[Desktop Entry]\nExec={gone}\nX-dopeIPTV-Generated=true\n")
    assert selfupdate.repoint_desktop_entry(current)
    assert f"Exec={current}\n" in desktop_entry.entry_path().read_text()

    # Pointing at another file that still exists is the user's choice.
    other = tmp_path / "Downloads" / "dopeIPTV-old.AppImage"
    other.write_bytes(GOOD)
    _entry(f"[Desktop Entry]\nExec={other}\nX-dopeIPTV-Generated=true\n")
    assert not selfupdate.repoint_desktop_entry(current)

    # Someone else's entry is never touched.
    _entry(f"[Desktop Entry]\nExec={gone}\n")
    assert not selfupdate.repoint_desktop_entry(current)


def test_menu_entry_quotes_a_path_with_spaces(env, tmp_path, monkeypatch):
    _current, _served = env
    spaced = tmp_path / "My Apps" / "dopeIPTV-1.2.15-x86_64.AppImage"
    spaced.parent.mkdir()
    spaced.write_bytes(GOOD)
    _entry("[Desktop Entry]\nExec=/gone/dopeIPTV.AppImage\n"
           "X-dopeIPTV-Generated=true\n")
    assert selfupdate.repoint_desktop_entry(spaced)
    assert f'Exec="{spaced}"\n' in desktop_entry.entry_path().read_text()


def test_cleanup_only_removes_an_old_appimage(tmp_path):
    running = tmp_path / "dopeIPTV-1.2.15-x86_64.AppImage"
    running.write_bytes(GOOD)
    stranger = tmp_path / "notes.txt"
    stranger.write_text("keep")
    assert not selfupdate.take_pending_cleanup(str(stranger), running)
    assert not selfupdate.take_pending_cleanup(str(running), running)
    assert stranger.exists() and running.exists()


def test_release_lookup_returns_the_assets(monkeypatch):
    class _R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"tag_name": "v1.2.15", "assets": [
                {"name": "dopeIPTV-1.2.15-x86_64.AppImage",
                 "browser_download_url": "https://x/a", "size": 5,
                 "digest": "sha256:" + "a" * 64}, "junk"]}

    class _Requests:
        @staticmethod
        def get(*_a, **_k):
            return _R()

    monkeypatch.setattr(updates, "requests", _Requests)
    rel = updates.fetch_latest_release()
    assert rel["tag"] == "v1.2.15"
    assert rel["assets"] == [{"name": "dopeIPTV-1.2.15-x86_64.AppImage",
                              "url": "https://x/a", "size": 5,
                              "digest": "sha256:" + "a" * 64}]


NO_PLAYER = (b"#!/bin/sh\necho '[dopeIPTV] 1.2.15 starting'\n"
             b"echo 'self-check: embedded playback DISABLED' >&2\nexit 1\n")


def test_a_machine_without_the_player_can_still_update(env):
    """Where the built-in player cannot load (no 3D graphics), every version
    fails --self-check, the running one included - that must not block
    updates for good, as long as the new one does start."""
    current, served = env
    current.write_bytes(b"#!/bin/sh\nexit 1\n")
    new = selfupdate.install(_release(served, NO_PLAYER), "1.2.14")
    assert new.read_bytes() == NO_PLAYER


def test_a_new_version_worse_than_the_running_one_is_refused(env):
    current, served = env              # the running one passes its check
    with pytest.raises(UpdateError, match="start-up check"):
        selfupdate.install(_release(served, NO_PLAYER), "1.2.14")
    assert _leftovers(current.parent) == [current.name]


def test_the_new_version_cleans_up_after_the_update(env, monkeypatch):
    """Started for the first time, the new version removes the old file the
    updater left behind, and the button offers to update in place."""
    from types import SimpleNamespace

    from dopeiptv.i18n import tr
    from dopeiptv.ui.mw_updates import _UpdatesMixin

    current, _served = env
    new = current.with_name("dopeIPTV-1.2.15-x86_64.AppImage")
    new.write_bytes(GOOD)
    monkeypatch.setenv("APPIMAGE", str(new))
    store = {"selfupdate_old_path": str(current)}
    w = SimpleNamespace(settings=SimpleNamespace(
        value=lambda k, d=None: store.get(k, d),
        setValue=store.__setitem__, remove=lambda k: store.pop(k, None)))

    assert _UpdatesMixin._update_action_label(w) == tr("update_now")
    _UpdatesMixin._finish_self_update(w)
    assert not current.exists() and new.exists()
    assert "selfupdate_old_path" not in store

    monkeypatch.delenv("APPIMAGE")
    assert _UpdatesMixin._update_action_label(w) == tr("about_download")
