"""The welcome's Bring step (ui/welcome.py, ui/mw_onboarding.py): a person who
uses dopeIPTV on another computer brings their settings over by sync code or
from a backup. Sync is faked at its seam (core/sync.Remote, kept in memory);
everything else - the code, the round trip, the merge, the window's stores -
runs as it does in the app. Nothing here reaches the network."""
from __future__ import annotations

import json
import logging
import tempfile
import time

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core import backup, build_check, sync
from dopeiptv.core.log import log
from dopeiptv.core.stores import FavoriteStore, PlaylistStore
from dopeiptv.i18n import tr

QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())

PROVIDER = {"name": "Home IPTV", "kind": "xtream", "server": "http://iptv.example:8080",
            "username": "anna", "password": "pw", "epg_url": "", "refresh": "never"}


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class Site:
    """dopeiptv.org's /sync/ in memory, behind core/sync.Remote's methods."""

    def __init__(self):
        self.slots: dict[str, tuple[int, bytes]] = {}
        self.calls = 0
        self.fail = ""
        site = self

        class FakeRemote:
            def __init__(self, slot, endpoint=None, timeout=20.0):
                self.slot = slot

            def _ask(self):
                site.calls += 1
                if site.fail:
                    raise sync.SyncError(site.fail)

            def get(self):
                self._ask()
                return site.slots.get(self.slot)

            def put(self, version_seen, blob):
                self._ask()
                version = site.slots.get(self.slot, (0, b""))[0]
                if version_seen != version:
                    return False, site.slots.get(self.slot)
                site.slots[self.slot] = (version + 1, blob)
                return True, version + 1

            def delete(self):
                self._ask()
                site.slots.pop(self.slot, None)
                return True

        self.Remote = FakeRemote


@pytest.fixture
def site(monkeypatch):
    s = Site()
    monkeypatch.setattr(sync, "Remote", s.Remote)
    return s


def _other_computer(tmp_path, site, with_source: bool) -> tuple[str, str]:
    """A computer that syncs already: its code, and its source's id ("" for
    none). With a source it syncs sources too (turned on there)."""
    st = QSettings(str(tmp_path / "other.ini"), QSettings.Format.IniFormat)
    rs = QSettings(str(tmp_path / "other-r.ini"), QSettings.Format.IniFormat)
    store = PlaylistStore(st)
    pid = ""
    if with_source:
        pid = store.add(dict(PROVIDER))["id"]
        FavoriteStore(st, f"favorites_{pid}").add("all", {"stream_id": 5, "name": "Five"})
    st.setValue("theme", "nord")
    cats = sync.CATEGORIES if with_source else sync.DEFAULT_CATS
    ctx = sync.Context(st, rs, store)
    code = sync.new_code()
    slot, key = sync.derive(sync.parse_code(code))
    sync.round_trip(site.Remote(slot), key, sync.empty_doc(), sync.collect(ctx, cats), cats)
    site.calls = 0
    return code, pid


def _window(tmp_path):
    from types import SimpleNamespace

    from PyQt6.QtWidgets import QMainWindow, QWidget

    from dopeiptv.providers.client import OfflineClient
    from dopeiptv.ui.mw_onboarding import _OnboardingMixin
    from dopeiptv.ui.mw_sync import _SyncMixin

    class Win(_OnboardingMixin, _SyncMixin, QMainWindow):
        def __init__(self):
            super().__init__()
            self.setCentralWidget(QWidget())
            self.resize(1100, 700)
            self.settings = QSettings(str(tmp_path / "here.ini"), QSettings.Format.IniFormat)
            self._resume_settings = QSettings(str(tmp_path / "here-r.ini"),
                                              QSettings.Format.IniFormat)
            self.playlist_store = PlaylistStore(self.settings)
            self._sync_state_file = tmp_path / "here-sync.json"
            self._welcome = None
            self._add_provider_btn = None
            self._player_fs = False
            self.client = OfflineClient()
            self.trakt = SimpleNamespace(is_connected=lambda: False)
            self.switched: list[str] = []
            self.picked = 0
            self.closed = 0
            self._init_sync()

        def switch_playlist(self, pid, then=None):
            self.switched.append(pid)

        def _maybe_pick_profile_at_start(self):
            self.picked += 1

        def _set_status(self, text):
            pass

        def start_demo(self):
            pass

        def _show_toast(self, text, ms=0):
            pass

        def _apply_theme_now(self):
            pass

        def close(self):
            self.closed += 1
            return True

    w = Win()
    w.show()
    w.show_welcome()
    return w, w._welcome


def _settle(w, secs=10.0):
    from PyQt6.QtWidgets import QApplication
    end = time.monotonic() + secs
    while time.monotonic() < end:
        QApplication.processEvents()
        if not w._wsync_pending and not w._sync_busy:
            w._sync_pool.waitForDone(2000)
            QApplication.processEvents()
            return True
        time.sleep(0.01)
    return False


def _ask(ov, code):
    ov._open_bring()
    ov._b_code.setText(code)
    ov._ask_sync()


def test_the_link_shows_and_opens_the_step(qapp, tmp_path, site):
    from dopeiptv.ui.welcome import PAGE_BRING, PAGE_WELCOME
    w, ov = _window(tmp_path)
    first = ov._stack.widget(PAGE_WELCOME)
    assert ov._w_bring.isVisibleTo(first) and ov._w_bring.plain() == tr("onb_bring_offer")
    assert ov._w_bring.wordWrap()            # a long translation wraps, never cut off
    ov._w_bring.clicked.emit()
    assert ov._stack.currentIndex() == PAGE_BRING
    step = ov._stack.widget(PAGE_BRING)
    for widget in (ov._b_code, ov._b_go, ov._b_restore, ov._b_back):
        assert widget.isVisibleTo(step) and widget.isEnabled()
    assert not ov._b_quit.isVisibleTo(step) and not ov._b_err.isVisibleTo(step)
    ov._b_back.click()
    assert ov._stack.currentIndex() == PAGE_WELCOME
    assert site.calls == 0 and not w._sync_on()


def test_a_good_code_with_sources_closes_the_welcome_and_loads_them(qapp, tmp_path, site):
    code, pid = _other_computer(tmp_path, site, with_source=True)
    seen: list[str] = []

    class Catch(logging.Handler):
        def emit(self, record):
            seen.append(record.getMessage())

    catch = Catch(logging.DEBUG)
    log.addHandler(catch)
    old_level = log.level
    log.setLevel(logging.DEBUG)
    try:
        w, ov = _window(tmp_path)
        _ask(ov, code.lower())
        assert ov._b_busy and ov._b_wait.isVisibleTo(ov._stack.currentWidget())
        assert not ov._b_go.isEnabled()
        assert _settle(w)
    finally:
        log.removeHandler(catch)
        log.setLevel(old_level)
    assert not ov.isVisible()                                 # the welcome went
    assert w.switched == [pid] and w.playlist_store.active_id == pid
    assert w.picked == 1                                      # "Who's watching?" as at start
    assert w.settings.value("theme") == "nord"
    favs = FavoriteStore(w.settings, f"favorites_{pid}").items()
    assert [f["stream_id"] for f in favs] == [5]
    # Sources follow the other computer, as with their switch on in Settings;
    # sync stays on with the code, which is never logged nor left in the field.
    assert "sources" in sync.chosen(w.settings) and w._sync_on()
    assert ov._b_code.text() == ""
    raw = code.replace("-", "")
    assert not any(code in m or raw in m.replace("-", "") for m in seen)


def test_a_good_code_without_sources_goes_to_connect_with_the_note(qapp, tmp_path, site):
    from dopeiptv.ui.welcome import PAGE_CONNECT
    code, _pid = _other_computer(tmp_path, site, with_source=False)
    w, ov = _window(tmp_path)
    _ask(ov, code)
    assert _settle(w)
    assert ov.isVisible() and ov._stack.currentIndex() == PAGE_CONNECT
    page = ov._stack.widget(PAGE_CONNECT)
    assert ov._c_note.isVisibleTo(page)
    assert ov._c_note.text() == tr("onb_sync_no_sources", what=tr("tab_playlists"),
                                   where=tr("sync_nav"))
    assert w.switched == [] and w.picked == 0
    assert w.settings.value("theme") == "nord" and w._sync_on()
    # Nothing to follow: this computer's switches stay as they were.
    assert sync.chosen(w.settings) == sync.DEFAULT_CATS
    # The connect form shows Xtream's own rows only.
    assert not ov._mac.isVisibleTo(page) and not ov._signin.isVisibleTo(page)
    ov.reset()
    assert not ov._c_note.isVisibleTo(page)


def test_a_bad_code_nothing_there_and_no_network_are_said(qapp, tmp_path, site):
    from dopeiptv.ui.welcome import PAGE_BRING
    w, ov = _window(tmp_path)
    step = ov._stack.widget(PAGE_BRING)
    _ask(ov, "not a code")
    assert ov._b_err.isVisibleTo(step) and ov._b_err.text() == tr("sync_code_bad")
    assert not ov._b_busy and ov._b_go.isEnabled()
    assert site.calls == 0 and not w._sync_on()
    ov._b_code.textEdited.emit("x")                           # typing again clears it
    assert not ov._b_err.isVisibleTo(step)

    _ask(ov, sync.new_code())                                 # a code with nothing under it
    assert _settle(w)
    assert ov._b_err.text() == tr("onb_sync_err_none") and not w._sync_on()
    assert ov._stack.currentIndex() == PAGE_BRING

    site.fail = "net"
    _ask(ov, sync.new_code())
    assert _settle(w)
    assert ov._b_err.text() == tr("onb_sync_err_net")
    assert not w._sync_on() and not w._sync_state_file.exists()   # nothing left joined
    assert not ov._b_busy and ov._b_code.isEnabled()


def test_back_while_fetching_stops_the_sync(qapp, tmp_path, site):
    from dopeiptv.ui.welcome import PAGE_WELCOME
    code, _pid = _other_computer(tmp_path, site, with_source=True)
    w, ov = _window(tmp_path)
    _ask(ov, code)
    ov._b_back.click()
    assert ov._stack.currentIndex() == PAGE_WELCOME and not ov._b_busy
    assert not w._sync_on()
    _settle(w)
    assert w.switched == [] and w.playlist_store.playlists() == []


def test_a_modified_build_is_refused(qapp, tmp_path, site):
    build_check.set_verdict(build_check.Verdict(build_check.MODIFIED, reason="files"))
    code, _pid = _other_computer(tmp_path, site, with_source=True)
    w, ov = _window(tmp_path)
    ov._open_bring()
    step = ov._stack.currentWidget()
    assert ov._b_err.isVisibleTo(step) and ov._b_err.text() == build_check.notice("sync")
    assert not ov._b_go.isEnabled() and not ov._b_code.isEnabled()
    assert ov._b_restore.isEnabled()                          # a backup is this computer's
    ov._b_code.setText(code)
    ov._ask_sync()
    w._wizard_sync(code)                                      # and asked anyway: refused
    assert ov._b_err.text() == build_check.notice("sync")
    assert site.calls == 0 and not w._sync_on()


def _backup_file(tmp_path) -> str:
    src = QSettings(str(tmp_path / "src.ini"), QSettings.Format.IniFormat)
    src.setValue("playlists", json.dumps([dict(PROVIDER, id="b1")]))
    src.setValue("theme", "nord")
    path = str(tmp_path / "backup.json")
    backup.write(path, backup.collect({"main": src}, "1.6.1"))
    return path


def test_the_backup_path_stages_and_offers_quit(qapp, tmp_path, site, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    from dopeiptv.ui import dialogs
    files = {"open": _backup_file(tmp_path)}
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (files["open"], "")))
    pending = tmp_path / "pending.json"
    monkeypatch.setattr(backup, "pending_path", lambda folder=None: str(pending))
    answer = {"choice": "replace"}
    shown = []

    def exec_(dlg):
        shown.append(dlg)
        (dlg.replace if answer["choice"] == "replace" else dlg.add).setChecked(True)
        return 1

    monkeypatch.setattr(dialogs.RestoreBackupDialog, "exec", exec_)
    w, ov = _window(tmp_path)
    ov._open_bring()
    step = ov._stack.currentWidget()

    # A file that is no backup: said, nothing staged.
    bad = tmp_path / "bad.json"
    bad.write_text('{"dopeiptv_backup": 1, "stores": {"main": {"the', encoding="utf-8")
    files["open"] = str(bad)
    ov._b_restore.click()
    assert ov._b_rerr.isVisibleTo(step) and ov._b_rerr.text() == tr("restore_invalid")
    assert not shown and not pending.exists()

    # Over everything: staged for the next start, Quit offered.
    files["open"] = str(tmp_path / "backup.json")
    ov._b_restore.click()
    assert shown and pending.exists()
    assert backup.read(str(pending)).stores["main"]["theme"] == "nord"
    assert ov._b_ready.isVisibleTo(step) and ov._b_ready.text() == tr("onb_restore_ready")
    assert ov._b_quit.isVisibleTo(step) and not ov._b_rerr.isVisibleTo(step)
    assert not ov._b_go.isEnabled()                       # a sync now would be replaced
    assert w.playlist_store.playlists() == []             # nothing changed until the start
    ov._b_quit.click()
    assert w.closed == 1 and w._resetting
    assert site.calls == 0


def test_a_backups_playlists_alone_load_as_after_connect(qapp, tmp_path, site, monkeypatch):
    from PyQt6.QtWidgets import QFileDialog

    from dopeiptv.ui import dialogs
    path = _backup_file(tmp_path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (path, "")))

    def exec_(dlg):
        dlg.add.setChecked(True)
        return 1

    monkeypatch.setattr(dialogs.RestoreBackupDialog, "exec", exec_)
    w, ov = _window(tmp_path)
    ov._open_bring()
    ov._b_restore.click()
    ids = [p["id"] for p in w.playlist_store.playlists()]
    assert len(ids) == 1 and w.switched == ids and not ov.isVisible()
