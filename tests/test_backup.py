"""Backup and restore (core/backup.py): everything a person would miss on a
new computer in one file, and back - over everything, or the playlists only.

- export then restore into fresh settings gives the same data;
- what stays out (window layout, caches, update state, feedback codes, this
  computer's graphics) is never written, and never taken from a file;
- a restore replaces what a backup covers and keeps the rest;
- folders this computer does not have are left out, its own kept;
- an export from before (a list of playlists) still reads, as playlists;
- a broken or newer file is refused before anything changes;
- the Settings card, in a window in a child process.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile

import pytest
from PyQt6.QtCore import QByteArray, QSettings

from dopeiptv.core import backup
from dopeiptv.core.stores import PlaylistStore

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def test_radio_stations_and_web_streams_are_favorites_the_yt_dlp_check_is_not_kept():
    assert backup._kind_of("radio_favorites") == "favorites"
    assert backup._kind_of("web_favorites") == "favorites"
    assert backup._kind_of("web_favorites_colors") == ""
    assert backup.covered("main", "radio_favorites") and backup.covered("main", "ytdlp_managed")
    assert backup.left_out("ytdlp_checked")


SOURCES = [
    {"id": "a", "name": "A", "kind": "xtream", "server": "http://a.invalid",
     "username": "user", "password": "secret", "epg_url": "", "refresh": "never"},
    {"id": "b", "name": "B", "kind": "m3u", "server": "http://b.invalid/l.m3u"},
]

KEPT_OUT = {
    "window_geometry": QByteArray(b"\x01\xd9\xd0\xcb"), "splitter_state": "x",
    "sidebar_collapsed": "true", "docked_video_height": "300",
    "guide_channel_col": "220",
    "mv_geometry": "g", "popout_geometry": "g", "update_check_ts": "123",
    "update_channel": "nightly", "update_latest_tag": "v9", "feedback_tickets": "[1]",
    "feedback_last": "1", "desktop_entry": "true", "desktop_entry_asked": "true",
    "selfupdate_old_path": "/tmp/old.AppImage", "local_tmdb_posters_v2": "{}",
    "music_art_v1": "{}", "home_pcache_a": "{}", "ts_broken/a": "1", "ts_depth/a": "60",
    "nerd_health_data": "{}", "hwdec_mode": "auto-safe", "force_x11": "true",
    "cast_devices": "TV", "subtitle_files": "{}", "tmdb_poster_cache_v3": "{}",
    "channel_stream_info": "{}",
    # The PIN's wrong guesses and its lock (an older file would give them back).
    "pin_tries": '{"tries": 4, "until": 1700000000}',
}


def _ini(path) -> QSettings:
    return QSettings(str(path), QSettings.Format.IniFormat)


def _stores(tmp_path, tag: str) -> dict:
    return {name: _ini(tmp_path / f"{tag}-{name}.ini") for name in backup.STORES}


def _fill(st: dict) -> None:
    m = st["main"]
    m.setValue("playlists", json.dumps(SOURCES))
    m.setValue("active_playlist", "a")
    m.setValue("favorites_a", json.dumps({"Favorites": [{"stream_id": 1, "name": "One"}],
                                          "Sport": [{"stream_id": 2, "name": "Two"},
                                                    {"stream_id": 1, "name": "One"}]}))
    m.setValue("favorites_a_locked", json.dumps(["Sport"]))
    m.setValue("movie_favorites_a", json.dumps({"Favorites": [{"stream_id": 30}]}))
    m.setValue("series_favorites_a", json.dumps({"Favorites": [{"series_id": 40}]}))
    m.setValue("history_a", json.dumps([{"_key": i, "_kind": "live"} for i in range(3)]))
    m.setValue("epg_reminders_a", json.dumps([{"stream_id": 1, "start": 9}]))
    m.setValue("recording_jobs", json.dumps([{"id": "j", "url": "http://x", "start": 1.0,
                                              "stop": None, "status": "scheduled"}]))
    m.setValue("category_overrides_a", json.dumps({"live": {"5": {"hidden": True}}}))
    m.setValue("channel_overrides_a", json.dumps({"live": {"k1": {"name": "X"},
                                                           "k2": {"hidden": True}}}))
    m.setValue("nerd_chno_a", json.dumps({"1": 101}))
    m.setValue("nerd_adelay/0abc1234", "0.2")
    m.setValue("nerd_epg_map_a", json.dumps({"1": "one.tv"}))
    for key, value in (("theme", "nord"), ("language", "sv"), ("nerd_enabled", "true"),
                       ("supporter_key", "DS1-abc"), ("trakt_access_token", "tok"),
                       ("simkl_access_token", "stok"),
                       ("os_password", "pw"), ("nerd_remote_token", "rt"),
                       ("check_updates", "false"), ("volume", 70),
                       ("library_collapsed", True), ("sub_langs", ["eng", "swe"])):
        m.setValue(key, value)
    for key, value in KEPT_OUT.items():
        m.setValue(key, value)
    st["resume"].setValue("resume_positions_a", json.dumps({"vod:1": {"pos": 600, "dur": 5000}}))
    st["resume"].setValue("_migrated", "1")
    st["follow"].setValue("followed_series_a", json.dumps({"9": {"snap": {}}}))
    st["cache"].setValue("trakt_watched_cache", json.dumps({"local_items": [{"name": "M"}]}))
    st["cache"].setValue("trakt_watchlist_cache", json.dumps({"movies": [{"t": 1}],
                                                              "shows": [{"t": 2}]}))
    st["cache"].setValue("tmdb_poster_cache_v3", "{}")
    st["cache"].setValue("_migrated", "1")
    for s in st.values():
        s.sync()


def _on_disk(st: dict) -> dict:
    """What a fresh reader of each file sees (values as the next start reads them)."""
    out = {}
    for name, s in st.items():
        s.sync()
        fresh = _ini(s.fileName())
        out[name] = {k: fresh.value(k) for k in fresh.allKeys()}
    return out


def _roundtrip(tmp_path, exists=lambda _p: True) -> tuple[dict, dict, backup.Backup]:
    src = _stores(tmp_path, "src")
    _fill(src)
    path = str(tmp_path / "b.json")
    backup.write(path, backup.collect(src, "1.5.0"))
    b = backup.read(path)
    dst = _stores(tmp_path, "dst")
    backup.restore(b, dst, exists)
    return src, dst, b


def test_export_then_restore_gives_the_same_data(tmp_path):
    src, dst, b = _roundtrip(tmp_path)
    assert b.full and b.app_version == "1.5.0" and b.created
    before, after = _on_disk(src), _on_disk(dst)
    for name in backup.STORES:
        mine = {k: v for k, v in before[name].items() if backup.covered(name, k)}
        assert mine and after[name] == mine, name
    # Logins come along, as the card says - Simkl's as Trakt's.
    assert after["main"]["supporter_key"] == "DS1-abc"
    assert after["main"]["trakt_access_token"] == "tok"
    assert after["main"]["simkl_access_token"] == "stok"
    assert json.loads(after["main"]["playlists"])[0]["password"] == "secret"
    # Group keys and lists keep their shape.
    assert after["main"]["nerd_adelay/0abc1234"] == "0.2"
    assert after["main"]["sub_langs"] == ["eng", "swe"]
    assert b.sources == SOURCES


def test_what_stays_out_is_not_in_the_file(tmp_path):
    src = _stores(tmp_path, "src")
    _fill(src)
    doc = backup.collect(src, "1.5.0")
    main = doc["stores"]["main"]
    for key in KEPT_OUT:
        assert key not in main, key
    assert set(doc["stores"]["resume"]) == {"resume_positions_a"}
    assert set(doc["stores"]["follow"]) == {"followed_series_a"}
    assert set(doc["stores"]["cache"]) == {"trakt_watched_cache", "trakt_watchlist_cache"}
    assert "tmdb_api_key" not in main and not backup.left_out("tmdb_api_key")
    assert doc[backup.MAGIC] == backup.FORMAT and doc["app_version"] == "1.5.0"


def test_a_backup_the_app_made_always_restores(tmp_path):
    """What a file cannot hold, or a restore would refuse, is passed over."""
    src = _stores(tmp_path, "src")
    src["main"].setValue("playlists", "not json")        # damaged: read as none
    src["main"].setValue("some_bytes", QByteArray(b"\x00\x01"))
    src["main"].setValue("theme", "nord")
    doc = backup.collect(src, "1.5.0")
    assert doc["stores"]["main"] == {"theme": "nord"}
    path = str(tmp_path / "b.json")
    backup.write(path, doc)
    assert backup.read(path).stores["main"] == {"theme": "nord"}


def test_a_restore_replaces_what_it_covers_and_keeps_the_rest(tmp_path):
    src = _stores(tmp_path, "src")
    _fill(src)
    b = backup.parse(backup.collect(src, "1.5.0"))
    dst = _stores(tmp_path, "dst")
    dst["main"].setValue("favorites_zz", "{}")          # a source the backup has not
    dst["main"].setValue("theme", "oled")
    dst["main"].setValue("window_geometry", QByteArray(b"here"))
    dst["main"].setValue("update_check_ts", "777")
    dst["cache"].setValue("tmdb_poster_cache_v3", '{"mine": 1}')
    dst["resume"].setValue("resume_positions_zz", "{}")
    backup.restore(b, dst, lambda _p: True)
    after = _on_disk(dst)
    assert "favorites_zz" not in after["main"] and "resume_positions_zz" not in after["resume"]
    assert after["main"]["theme"] == "nord"
    assert after["main"]["window_geometry"] == QByteArray(b"here")
    assert after["main"]["update_check_ts"] == "777"
    assert after["cache"]["tmdb_poster_cache_v3"] == '{"mine": 1}'


def test_a_file_cannot_set_what_stays_out(tmp_path):
    doc = {backup.MAGIC: 1, "app_version": "1.5.0", "created": "",
           "stores": {"main": {"selfupdate_old_path": "/home/someone/important",
                               "feedback_tickets": "[]", "update_channel": "nightly",
                               "desktop_entry": "true", "theme": "nord"},
                      "cache": {"tmdb_poster_cache_v3": "{}"},
                      "resume": {"_migrated": "0"}}}
    dst = _stores(tmp_path, "dst")
    backup.restore(backup.parse(doc), dst)
    after = _on_disk(dst)
    assert after["main"] == {"theme": "nord"}
    assert after["cache"] == {} and after["resume"] == {}


def test_folders_this_computer_lacks_are_left_out(tmp_path):
    here = {"/mine/a", "/s1.glsl"}
    src = _stores(tmp_path, "src")
    src["main"].setValue("recordings_dir", "/nope/recordings")
    src["main"].setValue("local_dirs", json.dumps(["/mine/a", "/nope/b"]))
    src["main"].setValue("nerd_shaders", "/s1.glsl\n/nope.glsl")
    src["main"].setValue("nerd_picon_dir", "")           # empty: the default, taken
    b = backup.parse(backup.collect(src, "1.5.0"))
    assert backup.missing_paths(b, here.__contains__) == [
        "recordings_dir", "local_dirs", "nerd_shaders"]
    dst = _stores(tmp_path, "dst")
    dst["main"].setValue("recordings_dir", "/mine/recordings")
    left = backup.restore(b, dst, here.__contains__)
    assert left == ["recordings_dir", "local_dirs", "nerd_shaders"]
    after = _on_disk(dst)["main"]
    assert after["recordings_dir"] == "/mine/recordings"   # this computer's own
    assert json.loads(after["local_dirs"]) == ["/mine/a"]
    assert after["nerd_shaders"] == "/s1.glsl"
    assert after["nerd_picon_dir"] == ""


def test_what_a_backup_holds_is_counted(tmp_path):
    src = _stores(tmp_path, "src")
    _fill(src)
    counts = backup.summary(backup.parse(backup.collect(src, "1.5.0")))
    assert list(counts) == list(backup.KINDS)
    assert {k: v for k, v in counts.items() if k != "settings"} == {
        "sources": 2, "profiles": 0, "favorites": 4, "history": 3, "resume": 1,
        "watched": 1, "watchlist": 2, "followed": 1, "reminders": 1, "recordings": 1,
        "hidden": 3, "numbers": 1}
    assert counts["settings"] >= 10


def test_settings_that_run_something_are_named(tmp_path):
    src = _stores(tmp_path, "src")
    src["main"].setValue("nerd_rec_post", "  /usr/bin/notify {file} ")
    src["main"].setValue("nerd_proxy", "http://proxy.invalid:8080")
    src["main"].setValue("nerd_mpv_options", "")
    b = backup.parse(backup.collect(src, "1.5.0"))
    assert backup.risky(b) == [("nerd_rec_post", "/usr/bin/notify {file}"),
                               ("nerd_proxy", "http://proxy.invalid:8080")]
    assert backup.risky_sources(b) == [] and not backup.new_token_here(b)


def test_every_setting_that_sends_something_elsewhere_is_named(tmp_path):
    """Headers by their names only, the switches that let others in with
    who, and a playlist's own proxy, headers and rewrite rules."""
    src = _stores(tmp_path, "src")
    m = src["main"]
    m.setValue("playlists", json.dumps([
        {**SOURCES[0], "options": {"proxy": "socks5://them.invalid:1080",
                                   "headers": "X-Auth: t0ps3cret",
                                   "rewrite": '[{"find": "http://", "replace": "http://them/"}]',
                                   "user_agent": "kodi"}},
        SOURCES[1]]))
    for key, value in (("nerd_headers", "X-Api-Key: k3y-value\n# a note\nReferer = http://r"),
                       ("nerd_udpxy", "http://router.invalid:4022"),
                       ("nerd_hooks", "true"), ("nerd_hooks_urls", "https://hook.invalid/a"),
                       ("nerd_hooks_header", "h00k-t0ken"),
                       ("nerd_mqtt", "true"), ("nerd_mqtt_host", "broker.invalid"),
                       ("nerd_mqtt_pass", "mq-pass"),
                       ("nerd_remote", "true"), ("nerd_remote_lan", "true"),
                       ("nerd_share", "false"), ("nerd_remote_token", "their-token")):
        m.setValue(key, value)
    b = backup.parse(backup.collect(src, "1.5.0"))
    risky = dict(backup.risky(b))
    assert risky == {"nerd_headers": "X-Api-Key, Referer", "nerd_udpxy": "http://router.invalid:4022",
                     "nerd_hooks_urls": "https://hook.invalid/a",
                     "nerd_hooks_header": "Authorization", "nerd_mqtt_host": "broker.invalid",
                     "nerd_remote": "nerd_remote_lan"}
    assert backup.risky_sources(b) == [("A", ["proxy", "headers", "rewrite"])]
    shown = json.dumps(backup.risky(b))
    for secret in ("k3y-value", "h00k-t0ken", "mq-pass", "their-token", "t0ps3cret"):
        assert secret not in shown
    assert backup.new_token_here(b)


def test_shaders_other_networks_a_log_file_and_sign_ins_are_named(tmp_path):
    """Shaders run on the graphics card, the other networks let more of
    the network reach server mode's tuner (no token), a log file names
    what plays, and a Trakt or Simkl sign-in - another person's, in a file
    of theirs - marks what plays here on their account. Each is named
    before a restore; a token never is."""
    from dopeiptv.i18n import tr
    from dopeiptv.ui.dialogs import RestoreBackupDialog
    src = _stores(tmp_path, "src")
    m = src["main"]
    for key, value in (("nerd_shaders", "/home/them/evil.glsl"),
                       ("nerd_other_networks", "10.0.20.0/24\n10.0.30.0/24"),
                       ("nerd_log_file", "true"),
                       ("profiles/kid/trakt_access_token", "trakt-t0ken"),
                       ("simkl_access_token", "simkl-t0ken")):
        m.setValue(key, value)
    b = backup.parse(backup.collect(src, "1.5.0"))
    risky = backup.risky(b)
    assert dict(risky) == {"nerd_shaders": "/home/them/evil.glsl",
                           "nerd_other_networks": "10.0.20.0/24 10.0.30.0/24",
                           "nerd_log_file": "", "trakt_access_token": "", "simkl_access_token": ""}
    shown = json.dumps(risky) + json.dumps(RestoreBackupDialog.risky_lines(risky, []))
    assert "t0ken" not in shown
    lines = RestoreBackupDialog.risky_lines(risky, [])
    assert tr("restore_signed_in", service="Trakt") in lines
    assert tr("restore_signed_in", service="Simkl") in lines
    assert tr("nerd_log_file") in lines
    assert any(line.startswith(tr("nerd_card_lan")) for line in lines)
    assert any("/home/them/evil.glsl" in line for line in lines)
    # Nothing of these: nothing said.
    plain = _stores(tmp_path, "plain")
    plain["main"].setValue("nerd_log_file", "false")
    plain["main"].setValue("trakt_access_token", "")
    assert backup.risky(backup.parse(backup.collect(plain, "1.5.0"))) == []


def test_the_remote_controls_token_is_never_taken_from_a_backup(tmp_path):
    src = _stores(tmp_path, "src")
    src["main"].setValue("nerd_remote", "true")
    src["main"].setValue("nerd_remote_token", "mine-at-home")
    assert "nerd_remote_token" not in backup.collect(src, "1.5.0")["stores"]["main"]
    # A file that has one all the same (an old one, or made by hand).
    doc = {backup.MAGIC: 1, "app_version": "1.5.0", "created": "",
           "stores": {"main": {"nerd_remote": "true", "nerd_remote_lan": "true",
                               "nerd_remote_token": "known-to-them"}}}
    b = backup.parse(doc)
    assert backup.new_token_here(b)
    dst = _stores(tmp_path, "dst")
    dst["main"].setValue("nerd_remote_token", "this-computers")
    backup.restore(b, dst)
    after = _on_disk(dst)["main"]
    assert after["nerd_remote_token"] == "this-computers" and after["nerd_remote"] == "true"
    fresh = _stores(tmp_path, "fresh")
    backup.restore(b, fresh)
    assert "nerd_remote_token" not in _on_disk(fresh)["main"]    # made anew when it starts


def test_the_phone_remote_stays_with_this_computer_and_mqtt_commands_are_named(tmp_path):
    """The phone remote's address (its token), port and switch belong to
    this computer's home network: never in a backup, never taken from one.
    MQTT's commands let the broker's clients in: shown before a restore."""
    src = _stores(tmp_path, "src")
    for key, value in (("phone_remote", "true"), ("phone_remote_token", "phone-secret"),
                       ("phone_remote_port", "3674"), ("nerd_mqtt_commands", "true"),
                       ("nerd_remote_lan", "true")):
        src["main"].setValue(key, value)
    doc = backup.collect(src, "1.5.0")
    main = doc["stores"]["main"]
    assert not {"phone_remote", "phone_remote_token", "phone_remote_port"} & set(main)
    assert "phone-secret" not in json.dumps(doc)
    b = backup.parse(doc)
    assert dict(backup.risky(b))["nerd_mqtt_commands"] == ""     # not the remote API's home network
    assert not backup.new_token_here(b)
    from dopeiptv.i18n import tr
    from dopeiptv.ui.dialogs import RestoreBackupDialog
    assert RestoreBackupDialog.risky_lines([("nerd_mqtt_commands", "")], []) == [
        tr("nerd_card_mqtt") + " · " + tr("nerd_mqtt_commands")]
    hand = backup.parse({backup.MAGIC: 1, "app_version": "1.5.0", "created": "",
                         "stores": {"main": {"theme": "nord", "phone_remote": "true",
                                             "phone_remote_token": "known"}}})
    dst = _stores(tmp_path, "dst")
    backup.restore(hand, dst)
    assert not {"phone_remote", "phone_remote_token"} & set(_on_disk(dst)["main"])


# -- a backup that would only empty what is here --------------------------------

@pytest.mark.parametrize("stores", [
    {},
    {"resume": {}, "follow": {}, "cache": {}},
    {"main": {}},
    {"main": {"window_geometry": "x", "update_channel": "nightly"}},   # nothing it takes
    {"main": {"favorites_a": "{}", "history_a": "[]"}, "resume": {}},
])
def test_a_backup_without_playlists_or_settings_never_replaces_anything(tmp_path, stores):
    b = backup.parse({backup.MAGIC: 1, "stores": stores})
    assert b.full and not backup.replaceable(b)
    dst = _stores(tmp_path, "dst")
    _fill(dst)
    before = _on_disk(dst)
    with pytest.raises(ValueError):
        backup.restore(b, dst)
    with pytest.raises(ValueError):
        backup.stage(b, str(tmp_path / "pending.json"))
    # Staged by an older copy all the same: refused at the start too.
    pending = tmp_path / backup.PENDING_NAME
    backup.write(str(pending), b.document())
    assert backup.apply_pending(dst, str(pending)) is False and not pending.exists()
    assert _on_disk(dst) == before


def test_a_restore_leaves_alone_the_files_a_backup_does_not_have(tmp_path):
    dst = _stores(tmp_path, "dst")
    _fill(dst)
    before = _on_disk(dst)
    b = backup.parse({backup.MAGIC: 1, "stores": {
        "main": {"playlists": json.dumps(SOURCES[1:]), "theme": "oled"}}})
    assert backup.replaceable(b)
    assert backup.kept(b) == ["resume", "followed", "watched", "watchlist"]
    backup.restore(b, dst, lambda _p: True)
    after = _on_disk(dst)
    assert after["main"]["theme"] == "oled" and "favorites_a" not in after["main"]
    assert json.loads(after["main"]["playlists"]) == SOURCES[1:]
    for name in ("resume", "follow", "cache"):
        assert after[name] == before[name], name
    # A full one replaces every file it has, and keeps nothing back.
    src = _stores(tmp_path, "src")
    _fill(src)
    full = backup.parse(backup.collect(src, "1.5.0"))
    assert backup.replaceable(full) and backup.kept(full) == []


def test_what_is_counted_is_what_a_restore_takes():
    b = backup.parse({backup.MAGIC: 1, "stores": {
        "main": {"theme": "nord", "window_geometry": "x", "update_channel": "nightly",
                 "resume_positions_a": json.dumps({"x": 1})},
        "cache": {"tmdb_poster_cache_v3": "{}"}, "resume": {"_migrated": "1"}}})
    counts = backup.summary(b)
    assert counts["settings"] == 1 and counts["resume"] == 0


def test_an_old_playlists_export_still_reads(tmp_path):
    path = tmp_path / "playlists.json"
    old = _ini(tmp_path / "old.ini")
    old.setValue("playlists", json.dumps(SOURCES))
    path.write_text(json.dumps(PlaylistStore(old).export_data()), encoding="utf-8-sig")
    b = backup.read(str(path))
    assert b.kind == "playlists" and not b.full
    assert [p["server"] for p in b.sources] == ["http://a.invalid", "http://b.invalid/l.m3u"]
    assert backup.summary(b)["sources"] == 2 and backup.missing_paths(b) == []
    with pytest.raises(ValueError):
        backup.restore(b, _stores(tmp_path, "dst"))
    with pytest.raises(ValueError):
        backup.stage(b, str(tmp_path / "pending.json"))
    store = PlaylistStore(_ini(tmp_path / "new.ini"))
    assert store.import_data(b.sources) == 2
    assert store.import_data(b.sources) == 0             # not twice


@pytest.mark.parametrize("text", [
    '{"dopeiptv_backup": 1, "stores": {"main": {"theme": "no',   # cut short
    "not json at all",
    '{"something": "else"}',
    '{"dopeiptv_backup": "1", "stores": {}}',
    '{"dopeiptv_backup": 1, "stores": []}',
    '{"dopeiptv_backup": 1, "stores": {"main": []}}',
    '{"dopeiptv_backup": 1, "stores": {"main": {"theme": {"a": 1}}}}',
    '{"dopeiptv_backup": 1, "stores": {"main": {"a\\\\b": "x"}}}',
    '{"dopeiptv_backup": 1, "stores": {"main": {"playlists": "[{"}}}',
    '{"dopeiptv_backup": 1, "stores": {"main": {"playlists": "{}"}}}',
    '{"dopeiptv_backup": 1, "stores": {"main": {"volume": NaN}}}',
])
def test_a_broken_file_is_refused_and_changes_nothing(tmp_path, text):
    dst = _stores(tmp_path, "dst")
    dst["main"].setValue("theme", "oled")
    pending = tmp_path / backup.PENDING_NAME
    pending.write_text(text, encoding="utf-8")
    with pytest.raises(backup.BackupError) as err:
        backup.read(str(pending))
    assert err.value.reason == "invalid"
    # Waiting for the next start: refused there too, and gone, never retried.
    assert backup.apply_pending(dst, str(pending)) is False
    assert not pending.exists()
    assert _on_disk(dst)["main"] == {"theme": "oled"}


def test_a_newer_or_unreadable_file_is_refused(tmp_path):
    path = tmp_path / "new.json"
    path.write_text(json.dumps({backup.MAGIC: backup.FORMAT + 1, "app_version": "9.0",
                                "stores": {}}), encoding="utf-8")
    with pytest.raises(backup.BackupError) as err:
        backup.read(str(path))
    assert (err.value.reason, err.value.detail) == ("newer", "9.0")
    with pytest.raises(backup.BackupError) as err:
        backup.read(str(tmp_path / "missing.json"))
    assert err.value.reason == "unreadable" and err.value.detail


def test_the_file_is_written_whole_or_not_at_all(tmp_path):
    path = tmp_path / "b.json"
    backup.write(str(path), {backup.MAGIC: 1, "stores": {}})
    assert json.loads(path.read_text(encoding="utf-8"))[backup.MAGIC] == 1
    if os.name == "posix":   # logins inside: the owner's only
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # A document that cannot be written leaves the old file as it was.
    with pytest.raises(ValueError):
        backup.write(str(path), {"bad": float("nan")})
    assert json.loads(path.read_text(encoding="utf-8"))[backup.MAGIC] == 1
    with pytest.raises(OSError):
        backup.write(str(tmp_path / "no" / "such" / "b.json"), {})
    assert sorted(os.listdir(tmp_path)) == ["b.json"]


def test_a_staged_restore_is_put_in_place_once(tmp_path):
    src = _stores(tmp_path, "src")
    _fill(src)
    b = backup.parse(backup.collect(src, "1.5.0"))
    pending = str(tmp_path / backup.PENDING_NAME)
    dst = _stores(tmp_path, "dst")
    assert backup.apply_pending(dst, pending) is None    # nothing waits
    assert backup.stage(b, pending) == pending
    dst["main"].setValue("theme", "oled")                # the app writes on the way out
    assert backup.apply_pending(dst, pending) is True
    assert not os.path.exists(pending)
    assert _on_disk(dst)["main"]["theme"] == "nord"
    assert backup.apply_pending(dst, pending) is None


class _MacSettings:
    """A QSettings with fallbacks on lists the system's global keys too."""

    def __init__(self) -> None:
        self.fallbacks = True
        self.data = {"theme": "nord"}

    def fallbacksEnabled(self) -> bool:  # noqa: N802
        return self.fallbacks

    def setFallbacksEnabled(self, on: bool) -> None:  # noqa: N802
        self.fallbacks = on

    def allKeys(self) -> list:  # noqa: N802
        return list(self.data) + (["AppleLocale", "NSInterfaceStyle"] if self.fallbacks else [])

    def value(self, key, default=None):
        return self.data.get(key, "system")


def test_only_the_apps_own_keys_go_in():
    s = _MacSettings()
    doc = backup.collect({"main": s}, "1.5.0")
    assert doc["stores"]["main"] == {"theme": "nord"}
    assert s.fallbacks is True                           # put back as it was


_WINDOW = r"""
import json, os, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QPushButton
app = QApplication.instance() or QApplication([])
base = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, base)
from dopeiptv.core import backup
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.i18n import tr
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui import dialogs, mw_settings
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui.settings_ui import SettingsShell

s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "a", "name": "A", "kind": "xtream",
           "server": "http://a.invalid", "username": "u", "password": "p"}]))
s.setValue("active_playlist", "a")
s.setValue("theme", "nord")
s.setValue("favorites_a", json.dumps({"Favorites": [{"stream_id": 7, "name": "Seven"}]}))
store = PlaylistStore(s)
w = MainWindow(DemoClient(), s, store)
out = os.path.join(base, "backup.json")
files = {"open": out}
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (files["open"], ""))
said = []
QMessageBox.information = staticmethod(
    lambda parent, title, text, *a, **k: said.append(text) or QMessageBox.StandardButton.Ok)
QMessageBox.warning = staticmethod(
    lambda parent, title, text, *a, **k: said.append("WARN " + text) or QMessageBox.StandardButton.Ok)
shown = []
answer = {"choice": "add"}

def restore_exec(dlg):
    shown.append(dlg)
    (dlg.add if answer["choice"] == "add" else dlg.replace).setChecked(True)
    return 1

dialogs.RestoreBackupDialog.exec = restore_exec
mw_settings.confirm = lambda *a, **k: True
# A backup without a password is asked about first; here it is wanted.
asked_plain = []
type(w)._backup_without_password_ok = lambda self, parent: asked_plain.append(1) or True
closed = []
w.close = lambda: closed.append(1) or True

def run(self):
    shell = self.findChild(SettingsShell)
    shell.build_all()                   # each page is built as it is needed
    general = next(p for p in shell.pages if p.key == "general")
    plists = next(p for p in shell.pages if p.key == "playlists")
    mine = {b.text(): b for b in general.findChildren(QPushButton)}
    assert tr("backup_btn") in mine and tr("restore_btn") in mine, list(mine)
    assert tr("backup_btn") not in [b.text() for b in plists.findChildren(QPushButton)]
    # Back up: one file, everything but what stays out.
    mine[tr("backup_btn")].click()
    assert asked_plain == [1], "a backup without a password is asked about"
    doc = json.load(open(out, encoding="utf-8"))
    assert doc["dopeiptv_backup"] == 1 and "favorites_a" in doc["stores"]["main"]
    assert "window_geometry" not in doc["stores"]["main"]
    assert tr("backup_count", what=tr("backup_kind_favorites"), n=1) in said[-1], said[-1]
    # Its playlists only: the one here already is not added twice.
    mine[tr("restore_btn")].click()
    assert tr("backup_count", what=tr("backup_kind_sources"), n=1) in shown[-1].holds.text()
    assert shown[-1].replace.isEnabled() and said[-1] == tr("pl_import_done", n=0), said[-1]
    # An old playlists file: playlists only, the replace offer greyed out.
    old = os.path.join(base, "playlists.json")
    with open(old, "w", encoding="utf-8") as fh:
        json.dump([{"name": "B", "kind": "m3u", "server": "http://b.invalid/x.m3u"}], fh)
    files["open"] = old
    mine[tr("restore_btn")].click()
    assert not shown[-1].replace.isEnabled() and shown[-1].choice() == "add"
    assert said[-1] == tr("pl_import_done", n=1) and len(store.playlists()) == 2
    # A broken file: said so, nothing staged.
    bad = os.path.join(base, "bad.json")
    with open(bad, "w", encoding="utf-8") as fh:
        fh.write('{"dopeiptv_backup": 1, "stores": {"main": {"the')
    files["open"] = bad
    n = len(shown)
    mine[tr("restore_btn")].click()
    assert len(shown) == n and said[-1] == "WARN " + tr("restore_invalid"), said[-1]
    assert not os.path.exists(backup.pending_path())
    # Over everything: staged for the next start, and the app closes.
    files["open"] = out
    answer["choice"] = "replace"
    s.setValue("theme", "oled")
    mine[tr("restore_btn")].click()
    assert shown[-1].choice() == "replace"
    assert closed and w._resetting and said[-1] == tr("restore_closing")
    assert os.path.exists(backup.pending_path())
    return 0

QDialog.exec = run
w.open_settings()
# The next start puts it in place, before anything reads the settings.
s.setValue("update_check_ts", "123")
s.sync()
ini, user = QSettings.Format.IniFormat, QSettings.Scope.UserScope
stores = {"main": QSettings(s.fileName(), ini),
          "resume": QSettings(ini, user, "dopeiptv", "resume"),
          "follow": QSettings(ini, user, "dopeiptv", "follow"),
          "cache": QSettings(ini, user, "dopeiptv", "cache")}
assert backup.apply_pending(stores) is True
main = stores["main"]
assert main.value("theme") == "nord" and main.value("update_check_ts") == "123"
assert [p["server"] for p in json.loads(main.value("playlists"))] == ["http://a.invalid"]
assert not os.path.exists(backup.pending_path())
print("BACKUP_OK")
"""


def test_the_settings_card_backs_up_and_restores():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "BACKUP_OK" in out.stdout, out.stdout + out.stderr[-3000:]


# -- the restore dialog -------------------------------------------------------------

@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _dialog(b: backup.Backup):
    from dopeiptv.ui.dialogs import RestoreBackupDialog
    return RestoreBackupDialog(None, b, backup.summary(b), len(backup.missing_paths(b)),
                               backup.risky(b))


def test_the_dialog_offers_no_replace_for_a_backup_that_would_only_empty(qapp):
    from dopeiptv.i18n import tr
    d = _dialog(backup.parse({backup.MAGIC: 1, "stores": {}}))
    assert not d.replace.isEnabled() and d.add.isChecked() and d.choice() == "add"
    assert tr("restore_cannot_replace") in d.notes.text() and d.notes.isVisibleTo(d)


def test_the_dialog_says_what_a_restore_replaces_and_keeps(qapp, tmp_path):
    from dopeiptv.i18n import tr
    b = backup.parse({backup.MAGIC: 1, "created": "2026-11-01T10:00:00+00:00", "stores": {
        "main": {"theme": "nord", "nerd_share": "true",
                 "playlists": json.dumps([{**SOURCES[1], "options": {"proxy": "http://p.invalid"}}])}}})
    d = _dialog(b)
    assert d.replace.isEnabled() and d.replace.isChecked() and d.choice() == "replace"
    notes = d.notes.text()
    assert tr("restore_kept", what=", ".join(tr(f"backup_kind_{k}") for k in
                                             ("resume", "followed", "watched", "watchlist"))) in notes
    assert tr("restore_risky_source", name="B", what=tr("nerd_proxy")) in notes
    assert tr("nerd_share") in notes and tr("restore_token_note") in notes
    assert tr("backup_count", what=tr("backup_kind_sources"), n=1) in d.holds.text()
    # A whole backup keeps nothing back and needs no word on the token.
    src = _stores(tmp_path, "src")
    _fill(src)
    whole = _dialog(backup.parse(backup.collect(src, "1.5.0")))
    assert tr("restore_kept", what="")[:12] not in whole.notes.text()
    assert tr("restore_token_note") not in whole.notes.text()


@pytest.mark.parametrize("created", ["0001-01-01T00:00:00", "9999-12-31T23:59:59",
                                     "1900-01-01T00:00:00+00:00", "nonsense", ""])
def test_a_backups_date_never_stops_the_dialog(qapp, created):
    from dopeiptv.ui.dialogs import RestoreBackupDialog
    assert RestoreBackupDialog._when(created)
    d = _dialog(backup.parse({backup.MAGIC: 1, "created": created,
                              "stores": {"main": {"theme": "nord"}}}))
    assert d.replace.isEnabled()


@pytest.mark.parametrize("error", [OSError(22, "Invalid argument"),
                                   OverflowError("timestamp out of range")])
def test_a_date_the_system_clock_cannot_hold_is_shown_as_written(qapp, monkeypatch, error):
    """Windows cannot take a date before 1970 (OSError), nor any system year
    1 or 9999 (OverflowError)."""
    from dopeiptv.ui import qt_i18n
    from dopeiptv.ui.dialogs import RestoreBackupDialog

    def fails(*_a, **_k):
        raise error
    monkeypatch.setattr(qt_i18n, "format_when", fails)
    assert RestoreBackupDialog._when("1900-01-01T00:00:00") == "1900-01-01T00:00:00"


# -- a second start that hands its command over ------------------------------------

@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="XDG folders")
def test_a_start_that_hands_its_command_over_never_puts_a_restore_in_place(qapp, tmp_path,
                                                                            monkeypatch):
    """A restore staged for the next start waits while a second start hands
    --play to the copy still running (that copy would write its stores over
    it as it closes): it goes in at the start that opens the window."""
    from dopeiptv import ORG
    from dopeiptv.core.instance import CommandServer
    from tests.test_commands import _wait_for
    sock_dir = tempfile.mkdtemp(prefix="dpi")
    monkeypatch.setenv("DOPEIPTV_INSTANCE_SOCKET", os.path.join(sock_dir, "s"))
    config = tmp_path / "config"
    pending = config / ORG / backup.PENDING_NAME
    src = _stores(tmp_path, "src")
    _fill(src)
    backup.stage(backup.parse(backup.collect(src, "1.5.0")), str(pending))
    got: list[dict] = []
    server = CommandServer(got.append)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", XDG_CONFIG_HOME=str(config),
               DOPEIPTV_INSTANCE_SOCKET=os.path.join(sock_dir, "s"))
    second = subprocess.Popen(
        [sys.executable, "-c",
         "import sys\n"
         "sys.argv = ['dopeiptv', '--play', 'http://h.invalid/live/1.ts']\n"
         "from dopeiptv.app import main\n"
         "sys.exit(main())"],
        cwd=_REPO_ROOT, env=env)
    try:
        _wait_for(got, second, 60)
        assert second.wait(30) == 0
    finally:
        if second.poll() is None:
            second.kill()
        server.close()
        shutil.rmtree(sock_dir, ignore_errors=True)
    assert got == [{"play": "http://h.invalid/live/1.ts"}]
    assert pending.exists()
    for path in config.rglob("*"):
        if path.is_file() and path != pending:
            assert "nord" not in path.read_text(encoding="utf-8", errors="replace"), path
