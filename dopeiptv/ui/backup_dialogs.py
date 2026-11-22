"""A full backup put back (core/backup.py reads and stages it): the file
picked and read (``pick_backup``) - one with a password opened with it
first (``UnlockBackupDialog``) -, what it holds and how to take it - over
everything here, or its playlists beside the ones here, with what runs
something, lets others in or sends the connections elsewhere shown first,
each brought only when its box is ticked (``RestoreBackupDialog``,
core/backup.without) - and one left to be put back at the next start
(``stage_backup``). Settings' restore and the welcome's Bring step both
go through here. A backup made with a password is encrypted off the
window's thread (``run_working``).

A password's key is made and the file opened or locked on a thread of its
own (PBKDF2's rounds, then the whole file in plain Python: up to a few
seconds), the window's thread going on meanwhile. The password is never
logged nor kept: the field is emptied as it is sent, the thread lets go of
it when it ends.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from PyQt6.QtCore import QEventLoop, Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from ..i18n import tr
from .theme import P

T = TypeVar("T")
#: How often the window looks whether the work on its own thread is done (ms).
_POLL_MS = 30


def _off_thread(work: Callable[[], Any], name: str) -> tuple[threading.Thread, dict]:
    """*work()* started on a thread of its own: (the thread, where its
    result lands - "ok", or "err" with what it raised)."""
    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["ok"] = work()
        except Exception as exc:
            box["err"] = exc

    thread = threading.Thread(target=run, name=name, daemon=True)
    thread.start()
    return thread, box


def _busy(text: str, muted: bool = False) -> QWidget:
    """*text* over the thin moving bar the app shows while it waits."""
    box = QWidget()
    col = QVBoxLayout(box)
    col.setContentsMargins(0, 0, 0, 0)
    col.setSpacing(8)
    line = QLabel(text)
    line.setTextFormat(Qt.TextFormat.PlainText)
    line.setWordWrap(True)
    line.setStyleSheet(f"font-size:{12 if muted else 13}px; "
                       f"color:{P['muted'] if muted else P['text']};")
    col.addWidget(line)
    bar = QProgressBar(objectName="LoadBar")
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.setFixedHeight(3)
    bar.setAccessibleName(text)
    col.addWidget(bar)
    return box


class _Working(QDialog):
    """What is being done while it runs off the window's thread: a line and
    a moving bar, no button, and it does not close until the work is done
    (``run_working``)."""

    def __init__(self, parent, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.done_working = False
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 24)
        col.addWidget(_busy(text))
        self.setMinimumWidth(380)

    def reject(self) -> None:
        if self.done_working:
            super().reject()

    def closeEvent(self, e) -> None:
        if self.done_working:
            super().closeEvent(e)
        else:
            e.ignore()


def run_working(parent, title: str, text: str, work: Callable[[], T]) -> T:
    """*work()* on a thread of its own while a small window over *parent*
    says *text*: the window's thread goes on - the app answers, nothing
    else can be clicked meanwhile. Its result; what it raised is raised
    here."""
    thread, box = _off_thread(work, "dopeiptv-backup")
    dlg = _Working(parent, title, text)
    loop = QEventLoop()
    timer = QTimer(dlg)
    timer.setInterval(_POLL_MS)
    timer.timeout.connect(lambda: None if thread.is_alive() else loop.quit())
    dlg.show()
    timer.start()
    loop.exec()
    timer.stop()
    # The loop also ends when the application is told to quit: the work
    # (seconds at most) is still let finish, whole or not at all.
    thread.join()
    dlg.done_working = True
    dlg.close()
    dlg.deleteLater()
    if "err" in box:
        raise box.pop("err")
    return box["ok"]


class UnlockBackupDialog(QDialog):
    """A backup's password asked for (*locked*: core/backup.Locked), and
    the backup opened with it on a thread of its own while the window says
    so. A wrong password - or a file changed since it was made, never told
    apart - says so and asks again; Cancel, at any moment, leaves
    everything as it was. Accepted: ``backup`` is what the file holds.
    Rejected with ``refused`` (a reason and a detail, as BackupError's): the
    file opened, and what is inside is no backup this app can put back."""

    WIDTH = 440

    def __init__(self, parent, locked) -> None:
        super().__init__(parent)
        self.locked = locked
        self.backup: Any = None
        self.refused: tuple[str, str] | None = None
        self._job: tuple[threading.Thread, dict] | None = None
        self.setWindowTitle(tr("restore_title"))
        self.setModal(True)
        self.setMinimumWidth(self.WIDTH)
        col = QVBoxLayout(self)
        col.setContentsMargins(24, 22, 24, 20)
        col.setSpacing(10)
        ask = QLabel(tr("restore_pw_ask"))
        ask.setTextFormat(Qt.TextFormat.PlainText)
        ask.setWordWrap(True)
        ask.setStyleSheet(f"font-size:13px; color:{P['text']};")
        col.addWidget(ask)
        self.field = QLineEdit()
        self.field.setEchoMode(QLineEdit.EchoMode.Password)
        self.field.setAccessibleName(tr("login_password"))
        self.field.setPlaceholderText(tr("login_password"))
        col.addWidget(self.field)
        self.wrong = QLabel(tr("restore_pw_wrong"))
        self.wrong.setTextFormat(Qt.TextFormat.PlainText)
        self.wrong.setWordWrap(True)
        self.wrong.setStyleSheet(f"font-size:12px; color:{P.get('error', P['muted'])};")
        self.wrong.setVisible(False)
        col.addWidget(self.wrong)
        self.busy = _busy(tr("restore_unlocking"), muted=True)
        self.busy.setVisible(False)
        col.addWidget(self.busy)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton(tr("common_cancel"))
        self.go = QPushButton(tr("restore_unlock"), objectName="Primary")
        self.go.setDefault(True)
        self.go.setEnabled(False)
        cancel.clicked.connect(self.reject)
        self.go.clicked.connect(self._try)
        self.field.textChanged.connect(
            lambda text: self.go.setEnabled(bool(text) and self._job is None))
        buttons.addWidget(cancel)
        buttons.addWidget(self.go)
        col.addLayout(buttons)
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)
        self.field.setFocus()

    def working(self) -> bool:
        return self._job is not None

    def _try(self) -> None:
        password = self.field.text()
        if not password or self._job is not None:
            return
        # Nothing of it left in the field while the key is made.
        self.field.clear()
        self.field.setEnabled(False)
        self.go.setEnabled(False)
        self.wrong.setVisible(False)
        self.busy.setVisible(True)
        self._job = _off_thread(lambda locked=self.locked, pw=password: locked.unlock(pw),
                                "dopeiptv-restore")
        del password
        self._timer.start()

    def _poll(self) -> None:
        if self._job is None or self._job[0].is_alive():
            return
        box = self._job[1]
        self._job = None
        self._timer.stop()
        self.busy.setVisible(False)
        self.field.setEnabled(True)
        if "ok" in box:
            self.backup = box["ok"]
            self.accept()
            return
        from ..core import backup
        exc = box.get("err")
        if isinstance(exc, backup.BackupError) and exc.reason == "password":
            from ..core.log import log
            log.info("restore: the password did not open the backup")
            self.wrong.setVisible(True)
            self.field.setFocus()
            return
        self.refused = ((exc.reason, exc.detail) if isinstance(exc, backup.BackupError)
                        else ("invalid", type(exc).__name__))
        self.reject()

    def done(self, r: int) -> None:
        # Cancel while the key is made: the thread ends by itself, its
        # answer goes nowhere.
        self._timer.stop()
        self._job = None
        super().done(r)


class _Words(QLabel):
    """A risky line's words, in plain text: a click on them ticks its box."""

    def __init__(self, text: str, box: QCheckBox) -> None:
        super().__init__(text)
        self._box = box
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setStyleSheet(f"font-size:12px; color:{P['text']};")

    def mousePressEvent(self, e) -> None:  # noqa: N802 - Qt's name
        self._box.toggle()
        e.accept()


class RestoreBackupDialog(QDialog):
    """What a backup holds, and how to take it: over everything here, or its
    playlists beside the ones here. *counts*: backup.summary(); *missing*:
    how many folder settings name folders this computer does not have;
    *risky*: backup.risky() - with backup.risky_sources(), what runs
    something, lets others in or sends the connections elsewhere: each a box
    of its own, none ticked, and only what is ticked comes, either way
    (backup.without - *backup*, the one given, is kept to what is ticked as
    the boxes change: from the start, none of them). Replacing everything is
    offered only for a backup that is backup.replaceable(); the files it
    does not have are named as kept."""

    WIDTH = 560

    _RISKY_LABELS = {"nerd_rec_post": ("nerd_card_rec_after", "nerd_rec_post"),
                     "nerd_mpv_options": ("nerd_mpv_options",),
                     "nerd_mpv_config_dir": ("nerd_mpv_dir",),
                     "nerd_shaders": ("nerd_card_player", "nerd_shaders"),
                     "nerd_other_networks": ("nerd_card_lan",),
                     "nerd_log_file": ("nerd_log_file",),
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
                           "nerd_log_file", "sync_code", "trakt_access_token",
                           "simkl_access_token"})
    #: A watch history signed in to: by the service's name.
    _SIGN_INS = {"trakt_access_token": "Trakt", "simkl_access_token": "Simkl"}
    #: What a switch says beside itself for the setting that widens it.
    _WIDER = {"nerd_remote_lan": "nerd_remote_home", "nerd_server_adult": "srv_adult"}
    #: What runs something on this computer: shown whole, up to _WHOLE.
    _RUNS = frozenset({"nerd_rec_post", "nerd_mpv_options", "nerd_mpv_config_dir",
                       "nerd_shaders"})
    _WHOLE = 4000

    @classmethod
    def _label(cls, key: str) -> str:
        if key in cls._SIGN_INS:
            return tr("restore_signed_in", service=cls._SIGN_INS[key])
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
        if backup.full and bk.new_token_here(backup):
            notes.append(tr("restore_token_note"))
        if backup.logins_out:
            notes.append(tr("restore_no_logins"))
        if replaceable and syncing:
            notes.append(tr("restore_sync_note"))
        self.notes = QLabel("\n\n".join(notes))
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.PlainText)
        self.notes.setStyleSheet(
            f"font-size:12px; color:{P.get('warn', P['muted'])};")
        self.notes.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.notes.setVisible(bool(notes))
        # What runs something, lets others in or sends the connections
        # elsewhere: one box each, none ticked - only what is ticked comes.
        self.backup = backup
        self._original = bk.Backup(backup.kind, dict(backup.stores), list(backup.sources),
                                   backup.app_version, backup.created, list(backup.keep))
        self.picks: list[tuple[str, QCheckBox, QLabel]] = []
        shown = self.risky_lines(risky, bk.risky_sources(backup))
        choices = bk.risky_choices(backup)
        self._picks_box = QWidget()
        rows = QVBoxLayout(self._picks_box)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        rows.addWidget(self.notes)
        self.risky_head: QLabel | None = None
        if shown and len(shown) == len(choices):
            self.risky_head = QLabel(tr("restore_risky") + "\n" + tr("restore_risky_ticks"))
            self.risky_head.setWordWrap(True)
            self.risky_head.setTextFormat(Qt.TextFormat.PlainText)
            self.risky_head.setStyleSheet(
                f"font-size:12px; color:{P.get('warn', P['muted'])};")
            rows.addWidget(self.risky_head)
            for key, line in zip(choices, shown, strict=True):
                rows.addWidget(self._pick_row(key, line))
        rows.addStretch(1)
        # Nothing ticked: the backup given brings none of them from here on.
        self._take()
        # A backup with many playlists of their own proxy can name dozens:
        # they scroll, the choice below stays on the screen.
        notes_box = QScrollArea()
        notes_box.setWidget(self._picks_box)
        notes_box.setWidgetResizable(True)
        notes_box.setFrameShape(QScrollArea.Shape.NoFrame)
        notes_box.setStyleSheet("QScrollArea, QScrollArea > QWidget > QWidget"
                                " { background:transparent; }")
        notes_box.setVisible(bool(notes) or bool(self.picks))
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
            if label.wordWrap() and not self._picks_box.isAncestorOf(label):
                label.setMinimumHeight(label.heightForWidth(inner - label.contentsMargins().left()))
        need = rows.totalHeightForWidth(inner - 16)
        if need <= 0:
            need = self._picks_box.sizeHint().height()
        notes_box.setFixedHeight(min(300, need + 4))

    def _pick_row(self, key: str, line: str) -> QWidget:
        """One risky thing the backup would bring: a box, not ticked, and
        its line beside it in plain text (a click on the words ticks it)."""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        box = QCheckBox()
        box.setChecked(False)
        box.setAccessibleName(line[:300])
        text = _Words(line, box)
        h.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
        h.addWidget(text, 1)
        box.toggled.connect(lambda _on: self._take())
        self.picks.append((key, box, text))
        return row

    def chosen(self) -> list[str]:
        """The risky things ticked (backup.risky_choices' names)."""
        return [key for key, box, _text in self.picks if box.isChecked()]

    def _take(self) -> None:
        """The backup given kept to what is ticked (backup.without): the
        window that opened this restores what it handed in, whichever way."""
        from ..core import backup as bk
        took = bk.without(self._original, self.chosen())
        self.backup.stores, self.backup.sources = took.stores, took.sources
        self.backup.keep = took.keep

    def shown_text(self) -> str:
        """Every word the window shows of what the backup holds beside its
        counts: the notes, and each risky line."""
        parts = [self.notes.text()]
        if self.risky_head is not None:
            parts.append(self.risky_head.text())
        parts += [text.text() for _key, _box, text in self.picks]
        return "\n".join(p for p in parts if p)

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


def _refusal(reason: str, detail: str) -> str:
    """Why a file is not put back, in the person's words."""
    if reason == "unreadable":
        return tr("pl_import_unreadable", error=detail)
    if reason == "newer":
        return tr("restore_newer", version=detail or "?")
    return tr("restore_invalid")


def pick_backup(parent) -> tuple[Any, str]:
    """A backup file picked and read (core/backup.py): (it, "") - or
    (None, why) for a file that is not one, (None, "") for none picked or
    a password not given. One with a password is opened with it first
    (UnlockBackupDialog); what it holds then goes on as any backup's.
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
        got = backup.read_any(path)
    except backup.BackupError as exc:
        log.warning("restore: %s", exc)
        return None, _refusal(exc.reason, exc.detail)
    if not isinstance(got, backup.Locked):
        return got, ""
    dlg = UnlockBackupDialog(parent, got)
    if dlg.exec():
        return dlg.backup, ""
    if dlg.refused is not None:
        log.warning("restore: what the password opened is refused (%s)", dlg.refused[0])
        return None, _refusal(*dlg.refused)
    return None, ""


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
