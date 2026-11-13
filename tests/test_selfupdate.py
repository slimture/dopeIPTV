"""Updating a running AppImage in place.

"Download the update" left two files and a dock icon still starting the
old one - the menu entry names the exact file. The app now fetches the
new AppImage itself, and nothing on disk changes until the download has
matched GitHub's checksum and the new file has passed its own start-up
check. These run the real code against fake AppImages (shell scripts that
pass or fail --self-check) and a fake download.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
from pathlib import Path

import pytest

from dopeiptv.core import desktop_entry, ed25519, selfupdate, updates
from dopeiptv.core.selfupdate import UpdateError
from dopeiptv.i18n import tr

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="AppImages are Linux-only")

# A release signing key for these tests only (the app carries the real one).
TEST_SEED = bytes(range(32))
TEST_KEY = base64.b64encode(ed25519.public_key(TEST_SEED)).decode()


def signed(release: dict) -> dict:
    """*release* with a checksum list of its assets, signed with TEST_SEED."""
    sums = "".join(f"{a['digest'].split(':', 1)[1]}  {a['name']}\n"
                   for a in release["assets"] if str(a.get("digest", "")).startswith("sha256:"))
    sig = base64.b64encode(ed25519.sign(TEST_SEED, sums.encode())).decode()
    return dict(release, sums=sums, sums_sig=sig)


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
    monkeypatch.setattr(selfupdate, "UPDATE_KEYS", (TEST_KEY,))
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
    return signed({"tag": f"v{version}", "assets": [
        {"name": f"dopeIPTV-{version}-aarch64.AppImage", "url": url + "-arm",
         "size": 1, "digest": ""},
        {"name": name, "url": url, "size": len(body), "digest": sha}]})


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


def test_a_failed_update_says_why(monkeypatch):
    """Closing Qt's progress dialog emitted canceled, so every failure was
    taken for the user's own cancel and nothing was shown at all. The
    update dialog says why, and a cancel stays quiet."""
    import os as _os
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication, QWidget

    from dopeiptv.ui import mw_updates
    from dopeiptv.ui.dialogs import UpdateDialog
    from dopeiptv.ui.mw_updates import _UpdatesMixin

    app = QApplication.instance() or QApplication([])   # noqa: F841 - kept alive

    class _Win(_UpdatesMixin, QWidget):
        pool = None

    monkeypatch.setattr(mw_updates, "run_async",
                        lambda _pool, _work, _done, fail: fail(
                            UpdateError("checksum mismatch")))
    w = _Win()
    w._self_update()
    dlg = w.findChildren(UpdateDialog)[-1]
    assert dlg.mode() == "done" and "checksum mismatch" in dlg._body.text()
    assert dlg._title.text() == tr("update_failed_title")
    assert not w._self_updating

    def cancelled(_pool, _work, _done, fail):
        w.findChildren(UpdateDialog)[-1]._first.click()     # Cancel
        fail(UpdateError("cancelled"))

    monkeypatch.setattr(mw_updates, "run_async", cancelled)
    w._self_update()
    assert w.findChildren(UpdateDialog)[-1].mode() == "cancelled"


def test_the_update_dialog_offers_the_restart_and_acts_on_it(monkeypatch, tmp_path):
    """Ready: the version without its v, Restart now (Install now where the
    installer runs) and Later; Restart now restarts into the new version,
    Later leaves the Mac's in place for when the app quits."""
    import os as _os
    import types
    _os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QApplication, QWidget

    from dopeiptv.ui import mw_updates
    from dopeiptv.ui.dialogs import UpdateDialog
    from dopeiptv.ui.mw_updates import _UpdatesMixin

    app = QApplication.instance() or QApplication([])   # noqa: F841 - kept alive
    calls: list = []

    class _Win(_UpdatesMixin, QWidget):
        pool = None
        settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)

        def close(self):
            calls.append("closed")

    for key, button in (("update_ready_restart", "update_restart_now"),
                        ("update_ready_install", "update_install_now")):
        fake = types.SimpleNamespace(
            DONE_KEY=key, VERIFY_KEY=None,
            restart_into=lambda new, pid=None: calls.append(("restart", new)),
            on_exit=lambda new, pid=None: calls.append(("on_exit", new)))
        monkeypatch.setattr(mw_updates, "_updater", lambda fake=fake: fake)
        monkeypatch.setattr(mw_updates, "run_async",
                            lambda _p, _w, done, _f: done(("v1.4.5.83", None, tmp_path / "new")))
        calls.clear()
        w = _Win()
        w._self_update()
        dlg = w.findChildren(UpdateDialog)[-1]
        assert dlg.mode() == "ready"
        assert dlg._title.text() == tr("update_ready_title", version="1.4.5.83")
        assert dlg._first.text() == tr(button) and dlg._second.text() == tr("update_later")
        dlg._first.click()
        assert calls == [("restart", tmp_path / "new"), "closed"]

    calls.clear()
    w = _Win()
    w._self_update()
    w.findChildren(UpdateDialog)[-1]._second.click()            # Later
    assert calls == [("on_exit", tmp_path / "new")]


def test_the_restart_keeps_the_entry_it_was_launched_from(monkeypatch, tmp_path):
    """The new version names its window after the entry the old one was
    started from (desktop_entry.launched_app_id), so the restart must hand
    that on - or the window comes up beside the pinned icon, not in it."""
    seen = {}

    class Popen:
        def __init__(self, argv, env=None, **kw):
            seen["env"] = env

    monkeypatch.setattr(selfupdate.subprocess, "Popen", Popen)
    monkeypatch.setenv("GIO_LAUNCHED_DESKTOP_FILE", "/x/applications/a.desktop")
    monkeypatch.setenv("APPIMAGE", "/old/dopeIPTV.AppImage")
    selfupdate.restart_into(tmp_path / "dopeIPTV.AppImage", pid=1)
    assert seen["env"]["GIO_LAUNCHED_DESKTOP_FILE"] == "/x/applications/a.desktop"
    assert "APPIMAGE" not in seen["env"]


def _asset(name="dopeIPTV-1.4.6-x86_64.AppImage", digest="sha256:" + "ab" * 32):
    return {"name": name, "url": f"https://dopeiptv.org/dl.php?f={name}", "size": 3, "digest": digest}


class _Got:
    def __init__(self, body: bytes):
        self.content = body

    def raise_for_status(self):
        pass


def _github_has(monkeypatch, rel: dict | None) -> list:
    """GitHub's copy of the release's signed list - *rel*'s, or none."""
    asked: list = []

    def get(url, **_kw):
        asked.append(url)
        if rel is None:
            raise OSError("no network")
        return _Got(rel["sums"].encode() if url.endswith("/SHA256SUMS") else rel["sums_sig"].encode())

    monkeypatch.setattr(selfupdate.requests, "get", get)
    return asked


def test_only_a_checksum_list_signed_with_the_apps_key_is_trusted(monkeypatch):
    monkeypatch.setattr(selfupdate, "UPDATE_KEYS", (TEST_KEY,))
    _github_has(monkeypatch, None)
    real = "cd" * 32
    rel = signed({"tag": "v1.4.6", "assets": [_asset(digest="sha256:" + real)]})
    # What the site's own JSON says is replaced by the signed list's.
    got = selfupdate.signed_asset(rel, _asset(digest="sha256:" + "00" * 32))
    assert got["digest"] == "sha256:" + real
    # A list changed after signing - a site taken over - is refused.
    forged = dict(rel, sums=rel["sums"].replace(real, "ee" * 32))
    with pytest.raises(UpdateError, match="not signed"):
        selfupdate.signed_asset(forged, _asset())
    # Signed with another key: refused.
    other = base64.b64encode(ed25519.sign(bytes(32), rel["sums"].encode())).decode()
    with pytest.raises(UpdateError, match="not signed"):
        selfupdate.signed_asset(dict(rel, sums_sig=other), _asset())
    # A file the signed list does not name: refused.
    with pytest.raises(UpdateError, match="not signed"):
        selfupdate.signed_asset(rel, _asset(name="dopeIPTV-1.4.6-evil.AppImage"))


def test_without_the_list_on_the_site_it_comes_from_github(monkeypatch):
    monkeypatch.setattr(selfupdate, "UPDATE_KEYS", (TEST_KEY,))
    rel = signed({"tag": "v1.4.5.90", "assets": [_asset(name="dopeIPTV-1.4.5.90-x86_64.AppImage")]})
    asked = _github_has(monkeypatch, rel)
    got = selfupdate.signed_asset({"tag": "v1.4.5.90"}, _asset(name="dopeIPTV-1.4.5.90-x86_64.AppImage"))
    assert got["digest"].startswith("sha256:")
    assert asked == ["https://github.com/slimture/dopeIPTV/releases/download/nightly-1.4.5.90/SHA256SUMS",
                     "https://github.com/slimture/dopeIPTV/releases/download/nightly-1.4.5.90/SHA256SUMS.sig"]

    _github_has(monkeypatch, None)
    with pytest.raises(UpdateError, match="not signed"):
        selfupdate.signed_asset({"tag": "v1.4.6"}, _asset())


def test_a_stale_list_on_the_site_falls_back_to_githubs(monkeypatch):
    # The site still has the list from before a file was rebuilt (or a
    # garbled one): GitHub's signed list decides, and the site's is not
    # fetched again.
    monkeypatch.setattr(selfupdate, "UPDATE_KEYS", (TEST_KEY,))
    name = "dopeIPTV-1.4.6-x86_64.AppImage"
    old = signed({"tag": "v1.4.6", "assets": [_asset(name="dopeIPTV-1.4.5-x86_64.AppImage")]})
    new = signed({"tag": "v1.4.6", "assets": [_asset(name=name, digest="sha256:" + "cd" * 32)]})
    asked = _github_has(monkeypatch, new)
    assert selfupdate.signed_asset(old, _asset(name=name))["digest"] == "sha256:" + "cd" * 32
    assert len(asked) == 2
    garbled = dict(new, sums_sig="not base64!")
    assert selfupdate.signed_asset(garbled, _asset(name=name))["digest"] == "sha256:" + "cd" * 32
    # A good list on the site needs nothing from GitHub.
    asked.clear()
    selfupdate.signed_asset(new, _asset(name=name))
    assert asked == []


def test_nothing_is_fetched_without_https_or_a_checksum(monkeypatch, tmp_path):
    def get(*_a, **_k):
        raise AssertionError("nothing may be fetched")

    monkeypatch.setattr(selfupdate.requests, "get", get)
    with pytest.raises(UpdateError, match="https"):
        selfupdate.download(dict(_asset(), url="http://dopeiptv.org/x"), tmp_path / "x")
    with pytest.raises(UpdateError, match="checksum"):
        selfupdate.download(_asset(digest=""), tmp_path / "x")


def test_the_release_keys_are_ed25519_public_keys():
    # A release cannot go out without one: tools/sign_sums.py refuses a
    # secret whose public half is not here.
    from dopeiptv.core.update_keys import UPDATE_KEYS
    for k in UPDATE_KEYS:
        assert len(base64.b64decode(k, validate=True)) == 32


def test_a_build_without_a_key_goes_by_the_checksum(monkeypatch):
    monkeypatch.setattr(selfupdate, "UPDATE_KEYS", ())
    asset = _asset()
    assert selfupdate.signed_asset({"tag": "v1.4.6"}, asset) is asset
