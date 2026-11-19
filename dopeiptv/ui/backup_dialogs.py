"""A full backup put back (core/backup.py reads and stages it): the file
picked and read (``pick_backup``), what it holds and how to take it - over
everything here, or its playlists beside the ones here, with what runs
something, lets others in or sends the connections elsewhere shown first
(``RestoreBackupDialog``) - and one left to be put back at the next start
(``stage_backup``). Settings' restore and the welcome's Bring step both
go through here.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout

from ..i18n import tr
from .theme import P


class RestoreBackupDialog(QDialog):
    """What a backup holds, and how to take it: over everything here, or its
    playlists beside the ones here. *counts*: backup.summary(); *missing*:
    how many folder settings name folders this computer does not have;
    *risky*: backup.risky() - with backup.risky_sources(), what runs
    something, lets others in or sends the connections elsewhere, shown
    before anything is taken. Replacing everything is offered only for a
    backup that is backup.replaceable(); the files it does not have are
    named as kept."""

    WIDTH = 560

    _RISKY_LABELS = {"nerd_rec_post": ("nerd_card_rec_after", "nerd_rec_post"),
                     "nerd_mpv_options": ("nerd_mpv_options",),
                     "nerd_mpv_config_dir": ("nerd_mpv_dir",),
                     "nerd_proxy": ("nerd_proxy",),
                     "nerd_headers": ("nerd_headers",),
                     "nerd_udpxy": ("nerd_card_udpxy",),
                     "nerd_hooks_urls": ("nerd_card_hooks", "nerd_hooks_urls"),
                     "nerd_hooks_header": ("nerd_card_hooks", "nerd_hooks_header"),
                     "nerd_mqtt_host": ("nerd_card_mqtt", "nerd_mqtt_host"),
                     "nerd_remote": ("nerd_remote",),
                     "nerd_share": ("nerd_share",),
                     "nerd_mqtt_commands": ("nerd_card_mqtt", "nerd_mqtt_commands"),
                     "nerd_server": ("nerd_card_server", "nerd_server"),
                     "sync_code": ("sync_title", "sync_code"),
                     # A playlist's own options.
                     "proxy": ("nerd_proxy",),
                     "headers": ("nerd_headers",),
                     "rewrite": ("nerd_src_rewrite",),
                     "udpxy": ("nerd_card_udpxy",),
                     "catchup_template": ("nerd_src_catchup",),
                     "backup_servers": ("nerd_src_mirrors",),
                     "nerd_dns_url": ("nerd_card_dns", "nerd_dns_url"),
                     "dns_url": ("nerd_card_dns", "nerd_dns_url"),
                     "cert_pin": ("cert_row",)}

    #: What is shown as on, not with a value: the switches that let others
    #: in, and the sync a backup joins this computer to (never its code).
    _SWITCHES = frozenset({"nerd_remote", "nerd_share", "nerd_mqtt_commands", "nerd_server",
                           "sync_code"})
    #: What a switch says beside itself for the setting that widens it.
    _WIDER = {"nerd_remote_lan": "nerd_remote_home", "nerd_server_adult": "srv_adult"}
    #: What runs something on this computer: shown whole, up to _WHOLE.
    _RUNS = frozenset({"nerd_rec_post", "nerd_mpv_options", "nerd_mpv_config_dir"})
    _WHOLE = 4000

    @classmethod
    def _label(cls, key: str) -> str:
        return " · ".join(tr(k) for k in cls._RISKY_LABELS.get(key, (key,)))

    @classmethod
    def risky_lines(cls, risky: list, sources: list) -> list[str]:
        """The lines that name what *risky* (backup.risky()) and *sources*
        (backup.risky_sources()) set: a setting with its value, a switch
        that lets others in with who, a playlist with its options."""
        lines = []
        for key, value in risky:
            if key in cls._SWITCHES:
                wider = cls._WIDER.get(value)
                lines.append(cls._label(key) + (" · " + tr(wider) if wider else ""))
            else:
                # What runs something is shown whole: a command cut at 90
                # characters hid whatever came after them.
                most = cls._WHOLE if key in cls._RUNS else 90
                lines.append(tr("backup_count", what=cls._label(key),
                                n=cls._short(value, most)))
        for name, keys in sources:
            lines.append(tr("restore_risky_source", name=cls._short(name, 40),
                            what=", ".join(cls._label(k) for k in keys)))
        return lines

    def __init__(self, parent, backup, counts: dict, missing: int,
                 risky: list, can_add: bool = True, syncing: bool = False) -> None:
        super().__init__(parent)
        from PyQt6.QtWidgets import QButtonGroup, QRadioButton

        from ..core import backup as bk
        replaceable = bk.replaceable(backup)
        self.setWindowTitle(tr("restore_title"))
        self.setModal(True)
        self.setMinimumWidth(self.WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        head = QLabel(tr("restore_title"))
        head.setStyleSheet(f"font-size:16px; font-weight:700; color:{P['text']};")
        col.addWidget(head)
        sub = QLabel(tr("restore_made", version=backup.app_version or "?",
                        date=self._when(backup.created)) if backup.full
                     else tr("restore_old_file"))
        sub.setWordWrap(True)
        sub.setTextFormat(Qt.TextFormat.PlainText)   # the file's own words
        sub.setStyleSheet(f"font-size:13px; color:{P['muted']};")
        col.addWidget(sub)
        lines = [tr("backup_count", what=tr(f"backup_kind_{kind}"), n=n)
                 for kind, n in counts.items() if n or kind == "sources"]
        self.holds = QLabel(tr("restore_holds") + "\n" + "\n".join(
            "  •  " + line for line in lines))
        self.holds.setWordWrap(True)
        self.holds.setTextFormat(Qt.TextFormat.PlainText)
        self.holds.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self.holds.setStyleSheet(f"font-size:13px; color:{P['text']};")
        col.addWidget(self.holds)
        notes = []
        if backup.full and not replaceable:
            notes.append(tr("restore_cannot_replace"))
        elif backup.full and bk.kept(backup):
            notes.append(tr("restore_kept", what=", ".join(
                tr(f"backup_kind_{kind}") for kind in bk.kept(backup))))
        if backup.full and missing:
            notes.append(tr("restore_missing_paths", n=missing))
        shown = self.risky_lines(risky, bk.risky_sources(backup))
        if shown:
            notes.append(tr("restore_risky") + "\n" + "\n".join(
                "  •  " + line for line in shown))
        if backup.full and bk.new_token_here(backup):
            notes.append(tr("restore_token_note"))
        if replaceable and syncing:
            notes.append(tr("restore_sync_note"))
        self.notes = QLabel("\n\n".join(notes))
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.PlainText)
        self.notes.setStyleSheet(
            f"font-size:12px; color:{P.get('warn', P['muted'])};")
        self.notes.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # A backup with many playlists of their own proxy can name dozens:
        # they scroll, the choice below stays on the screen.
        notes_box = QScrollArea()
        notes_box.setWidget(self.notes)
        notes_box.setWidgetResizable(True)
        notes_box.setFrameShape(QScrollArea.Shape.NoFrame)
        notes_box.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget"
                                " { background:transparent; }")
        notes_box.setVisible(bool(notes))
        col.addWidget(notes_box)
        col.addSpacing(4)
        self.replace = QRadioButton(tr("restore_replace"))
        self.add = QRadioButton(tr("restore_add"))
        group = QButtonGroup(self)
        for i, (radio, hint) in enumerate(
                ((self.replace, "restore_replace_hint"),
                 (self.add, "restore_add_hint"))):
            group.addButton(radio, i)
            col.addWidget(radio)
            text = QLabel(tr(hint))
            text.setWordWrap(True)
            text.setContentsMargins(26, 0, 0, 4)
            text.setStyleSheet(f"font-size:12px; color:{P['muted']};")
            col.addWidget(text)
        self.replace.setEnabled(replaceable)
        self.add.setEnabled(can_add)
        (self.replace if replaceable else self.add).setChecked(True)
        col.addSpacing(6)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.go = QPushButton(tr("restore_go"), objectName="Primary")
        cancel.clicked.connect(self.reject)
        self.go.clicked.connect(self.accept)
        self.go.setEnabled(self.replace.isEnabled() or self.add.isEnabled())
        buttons.addWidget(cancel)
        buttons.addWidget(self.go)
        col.addLayout(buttons)
        cancel.setFocus()
        # A dialog does not ask its wrapped lines how tall they are at its
        # width: told here, or they were cut.
        inner = self.WIDTH - 48
        for label in self.findChildren(QLabel):
            if label.wordWrap() and label is not self.notes:
                label.setMinimumHeight(label.heightForWidth(inner - label.contentsMargins().left()))
        notes_box.setFixedHeight(min(260, self.notes.heightForWidth(inner - 16) + 4))

    @staticmethod
    def _when(created: str) -> str:
        from datetime import datetime

        from .qt_i18n import format_when
        try:
            return format_when(datetime.fromisoformat(created),
                               "d MMM yyyy HH:mm")
        except (TypeError, ValueError, OSError, OverflowError):
            # OSError, OverflowError: a date the system clock cannot hold
            # (Windows before 1970, year 1 or 9999).
            return created or "?"

    @staticmethod
    def _short(value: str, most: int = 90) -> str:
        value = " ".join(value.split())
        return value if len(value) <= most else value[:most - 1] + "…"

    def choice(self) -> str:
        """"replace" (everything) or "add" (its playlists only)."""
        if self.replace.isChecked() and self.replace.isEnabled():
            return "replace"
        return "add"


def pick_backup(parent) -> tuple[Any, str]:
    """A backup file picked and read (core/backup.py): (it, "") - or
    (None, why) for a file that is not one, (None, "") for none picked.
    Settings' restore and the welcome's both start here."""
    from PyQt6.QtWidgets import QFileDialog

    from ..core import backup
    from ..core.log import log
    from .widgets import file_dialog_options
    path, _f = QFileDialog.getOpenFileName(
        parent, tr("restore_title"), "",
        tr("file_filter_json") + ";;" + tr("file_filter_all"),
        options=file_dialog_options())
    if not path:
        return None, ""
    try:
        return backup.read(path), ""
    except backup.BackupError as exc:
        log.warning("restore: %s", exc)
        return None, (tr("pl_import_unreadable", error=exc.detail)
                      if exc.reason == "unreadable"
                      else tr("restore_newer", version=exc.detail or "?")
                      if exc.reason == "newer" else tr("restore_invalid"))


def stage_backup(b: Any) -> str:
    """*b* left to be put back over everything at the next start
    (core/backup.stage): "" - or why it could not be."""
    from ..core import backup
    from ..core.log import log
    try:
        backup.stage(b)
    except (OSError, ValueError) as exc:
        log.warning("restore: not staged: %s", exc)
        return tr("pl_export_failed", error=exc)
    return ""
