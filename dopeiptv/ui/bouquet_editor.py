"""An Enigma2 box's channel lists edited in the app (providers/bouquets.py,
OpenWebif's bouquet editor): the bouquets on the left (TV or radio), the
chosen bouquet's channels beside them - dragged into order, renamed,
removed, markers and spacers added - and on the right what to add from:
every channel the box knows, or a satellite's or a provider's, searched.

Every change goes to the box off the window's thread, one after another in
the order made (``Session``); the list shows the change at once and, once
the box has answered everything, what the box itself then lists. One the
box refuses stops the ones after it, and the lists are read again. Before
the first change of a window the box takes a backup of every list (the
editor's own tar in its /tmp), fetched here so it can be saved and put
back. A window closed while changes are still on their way stays until
the box has them all; then the window's source reads its channels again,
so the app shows the new order. Nothing here touches what plays."""

from __future__ import annotations

import html
import os
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QItemSelectionModel, QObject, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QPushButton, QSplitter, QVBoxLayout, QWidget,
)

from ..core.log import log
from ..core.palette_match import fold, words
from ..core.workers import run_async
from ..i18n import tr
from ..providers import bouquets as bq
from . import message_box
from .theme import P

#: The item data role a row keeps its index in.
ROW_ROLE = int(Qt.ItemDataRole.UserRole) + 1


def why(err: Any) -> str:
    msg = getattr(err, "localized", None)
    return msg() if callable(msg) else str(err or "")


class Change:
    """One change for the box: an editor call and its arguments; *target*
    the bouquet whose entries it changes ("" for the list of bouquets)."""

    __slots__ = ("call", "args", "target")

    def __init__(self, call: str, args: tuple, target: str = "") -> None:
        self.call, self.args, self.target = call, args, target

    def __repr__(self) -> str:
        return f"Change({self.call})"


class Session(QObject):
    """What the window shows of the box's lists and what it sends: the
    bouquets of one kind (TV or radio), the entries of the one chosen, the
    changes in the order made (sent one at a time), the backup taken
    before the first. *view* is told what to show (the window, or a test's
    stand-in): show_bouquets, show_entries, say, no_editor, backup_taken,
    go_on_without_backup, idle."""

    def __init__(self, view: Any, editor: Any, pool: Any) -> None:
        super().__init__(view if isinstance(view, QObject) else None)
        self.view, self.editor, self.pool = view, editor, pool
        self.radio = False
        self.present: bool | None = None
        self.bouquets: list[bq.Entry] = []
        self.current = ""
        self.entries: list[bq.Entry] = []
        self.loading = True
        self.queue: list[Change] = []
        self.busy = False
        self.sent: list[str] = []
        self.changed = False
        self.closed = False
        # Bumped by every change: a list read before one is dropped (the
        # read after the change follows).
        self.gen = 0
        self.backup = ""            # "", "taking", "taken", "skipped"
        self.backup_tar = ""
        self.backup_data = b""
        self._sending: Change | None = None
        self._dirty_bouquets = False
        self._dirty_entries = False
        self._known: set[str] | None = None
        self._final = ""
        # A restore waiting for the backup taken before it.
        self._restore_next: bytes | None = None

    # -- state ----------------------------------------------------------------

    @property
    def idle(self) -> bool:
        return not (self.busy or self.queue)

    @property
    def holding(self) -> bool:
        """A row of this list the box is still making: its edits wait."""
        return any(e.pending for e in self.entries)

    def can_edit(self) -> bool:
        return bool(self.present) and bool(self.current) and not self.loading and not self.holding

    # -- reading ----------------------------------------------------------------

    def load(self) -> None:
        self._dirty_bouquets = True
        if self.idle:
            self._refresh()

    def set_radio(self, radio: bool) -> None:
        if radio == self.radio:
            return
        self.radio, self.current, self.bouquets, self.entries = radio, "", [], []
        self.loading = True
        self.view.show_bouquets()
        self.view.show_entries()
        self.load()

    def select(self, row: int) -> None:
        """The bouquet at *row* shown."""
        if not 0 <= row < len(self.bouquets):
            return
        b = self.bouquets[row]
        if b.ref == self.current:
            return
        self.current, self.entries, self.loading = b.ref, [], True
        self.view.show_entries()
        if b.pending:
            return
        self._dirty_entries = True
        if self.idle:
            self._refresh()

    def _refresh(self) -> None:
        """What changed read again from the box, once nothing is on its way.
        A closed session asks the box nothing more."""
        if self.closed:
            return
        if self._dirty_bouquets:
            self._dirty_bouquets = False
            self._dirty_entries = True
            run_async(self.pool, self._get_bouquets, self._bouquets_came, self._read_failed,
                      self.gen, self.radio)
        elif self._dirty_entries:
            self._dirty_entries = False
            if not self.current:
                self.entries, self.loading = [], False
                self.view.show_entries()
                self._settled()
                return
            run_async(self.pool, self._get_entries, self._entries_came, self._read_failed,
                      self.gen, self.current)
        else:
            self._settled()

    def _get_bouquets(self, gen: int, radio: bool) -> tuple[int, bool, Any]:
        return gen, radio, self.editor.bouquets(radio)

    def _get_entries(self, gen: int, bref: str) -> tuple[int, str, Any]:
        return gen, bref, self.editor.entries(bref)

    def _stale(self, gen: int) -> bool:
        return self.closed or gen != self.gen or not self.idle

    def _bouquets_came(self, got: Any) -> None:
        gen, radio, lists = got
        if self.closed or radio != self.radio:
            return
        if self._stale(gen):
            # Read again once what came since is on the box.
            self._dirty_bouquets = True
            return
        self.present = bool(lists.present)
        if not lists.present:
            self.loading = False
            self.view.no_editor(lists.said)
            self.view.idle()
            return
        self.bouquets = list(lists.bouquets)
        known, self._known = self._known, None
        refs = [b.ref for b in self.bouquets]
        new = [r for r in refs if known is not None and r not in known]
        if new:
            self.current = new[-1]
        elif not any(bq.same_ref(r, self.current) for r in refs):
            self.current = next((r for r in refs if r), "")
        else:
            self.current = next(r for r in refs if bq.same_ref(r, self.current))
        self.view.show_bouquets()
        self._dirty_entries = True
        self._refresh()

    def _entries_came(self, got: Any) -> None:
        gen, bref, entries = got
        if self.closed or bref != self.current:
            return
        if self._stale(gen):
            self._dirty_entries = True
            return
        self.entries, self.loading = list(entries), False
        self.view.show_entries()
        self._refresh()

    def _read_failed(self, err: Any = None) -> None:
        if self.closed:
            return
        self.loading = False
        self._final = ""
        if isinstance(err, bq.NoEditor):
            self.present = False
            self.view.no_editor(err.said)
        else:
            self.view.show_entries()
            self.view.say(tr("box_failed", why=why(err)))
        # Nothing more on its way: a window closed meanwhile may go.
        self.view.idle()

    def _settled(self) -> None:
        """Everything sent and read back: say how it went."""
        text = self._final
        if not text and self.sent:
            text = tr("bq_saved")
        self._final = ""
        self.sent = []
        self.view.say(text)
        self.view.idle()

    # -- sending ----------------------------------------------------------------

    def _send(self, change: Change) -> None:
        self.gen += 1
        self.queue.append(change)

    def _dirty(self, change: Change | None) -> None:
        if change is None:
            return
        if change.target:
            self._dirty_entries = True
        else:
            self._dirty_bouquets = True

    def pump(self) -> None:
        """The next change to the box - the backup first, before the first
        of all."""
        if self.busy or self.closed:
            return
        if not self.queue:
            self._refresh()
            return
        if not self.backup:
            self.backup, self.busy = "taking", True
            self.view.say(tr("bq_backup_taking"))
            run_async(self.pool, self.editor.take_backup, self._backed_up, self._backup_failed)
            return
        change = self._sending = self.queue.pop(0)
        self.busy = True
        self.view.say(tr("bq_sending", n=len(self.queue) + 1))
        run_async(self.pool, getattr(self.editor, change.call), self._answered,
                  self._change_failed, *change.args)

    def _backed_up(self, got: Any) -> None:
        if self.closed:
            return
        self.busy = False
        tar, data = got if isinstance(got, tuple) else (str(got or ""), b"")
        self.backup, self.backup_tar, self.backup_data = "taken", tar, bytes(data or b"")
        self.view.backup_taken()
        self._go_on()

    def _go_on(self) -> None:
        """After the backup: the restore it was taken for, else the changes."""
        data, self._restore_next = self._restore_next, None
        if data is not None:
            self._put_back(data)
        else:
            self.pump()

    def _backup_failed(self, err: Any = None) -> None:
        if self.closed:
            return
        self.busy = False
        if isinstance(err, bq.NoEditor):
            self.backup = ""
            self._restore_next = None
            self._no_editor(err)
            return
        if self.view.go_on_without_backup(why(err)):
            log.warning("enigma2: the bouquets changed without a backup (%s)", err)
            self.backup = "skipped"
            self._go_on()
            return
        self.backup = ""
        self._restore_next = None
        self._stop(tr("bq_nothing_changed"), plain=True)

    def _answered(self, result: Any) -> None:
        if self.closed:
            return
        self.busy = False
        change, self._sending = self._sending, None
        ok, text = result if isinstance(result, tuple) else (bool(result), "")
        self._dirty(change)
        if ok:
            self.changed = True
            self.sent.append(text)
            self.pump()
            return
        self._stop(tr("box_failed", why=text))

    def _change_failed(self, err: Any = None) -> None:
        if self.closed:
            return
        self.busy = False
        change, self._sending = self._sending, None
        self._dirty(change)
        if isinstance(err, bq.NoEditor):
            self._no_editor(err)
            return
        self._stop(tr("box_failed", why=why(err)))

    def _no_editor(self, err: Any) -> None:
        self.queue.clear()
        self.present = False
        self.view.no_editor(getattr(err, "said", ""))
        self.view.idle()

    def _stop(self, text: str, plain: bool = False) -> None:
        """A change the box did not make: the ones after it are not sent,
        and the lists are read again to show what the box has."""
        dropped = len(self.queue)
        self.queue.clear()
        self.gen += 1
        self._dirty_bouquets = self._dirty_entries = True
        self._known = None
        self._final = text if plain or not dropped else f"{text} {tr('bq_dropped', n=dropped)}"
        self.sent = []
        self._refresh()

    # -- the bouquets -------------------------------------------------------------

    def _bouquet(self, row: int) -> bq.Entry | None:
        b = self.bouquets[row] if 0 <= row < len(self.bouquets) else None
        return b if b is not None and not b.pending and self.present else None

    def add_bouquet(self, name: str) -> None:
        name = " ".join((name or "").split())
        if not name or not self.present:
            return
        if self._known is None:
            self._known = {b.ref for b in self.bouquets}
        self._send(Change("add_bouquet", (name, self.radio)))
        self.bouquets.append(bq.Entry("", f"{name} ({'Radio' if self.radio else 'TV'})", bq.BOUQUET))
        self.view.show_bouquets()
        self.pump()

    def rename_bouquet(self, row: int, name: str) -> None:
        b = self._bouquet(row)
        name = " ".join((name or "").split())
        if b is None or not name or name == b.name:
            return
        self._send(Change("rename_bouquet", (b.ref, name, self.radio)))
        self.bouquets[row] = bq.Entry(b.ref, name, b.kind, b.locked)
        self.view.show_bouquets()
        self.pump()

    def remove_bouquet(self, row: int) -> None:
        b = self._bouquet(row)
        if b is None:
            return
        self._send(Change("remove_bouquet", (b.ref, self.radio)))
        del self.bouquets[row]
        if bq.same_ref(b.ref, self.current):
            near = self.bouquets[min(row, len(self.bouquets) - 1)] if self.bouquets else None
            self.current = near.ref if near is not None else ""
            self.entries, self.loading = [], bool(self.current)
            self._dirty_entries = True
            self.view.show_entries()
        self.view.show_bouquets()
        self.pump()

    def move_bouquet(self, row: int, to: int) -> None:
        """The bouquet at *row* to before row *to*."""
        b = self._bouquet(row)
        if b is None:
            return
        new, moves = bq.plan_move(len(self.bouquets), [row], to)
        if not moves:
            return
        for old, pos in moves:
            self._send(Change("move_bouquet", (self.bouquets[old].ref, pos, self.radio)))
        self.bouquets = [self.bouquets[i] for i in new]
        self.view.show_bouquets()
        self.pump()

    # -- the chosen bouquet's entries ----------------------------------------------

    def _rows(self, rows: list[int]) -> list[int]:
        return sorted({r for r in rows if 0 <= r < len(self.entries) and not self.entries[r].pending})

    def _after(self, row: int) -> str:
        """The entry after *row*: what something put below *row* goes before."""
        return next((e.ref for e in self.entries[row + 1:] if not e.pending), "")

    def move_entries(self, rows: list[int], to: int) -> list[int]:
        """Rows moved together to before row *to*; the rows they are at now."""
        rows = self._rows(rows)
        if not rows or not self.can_edit():
            return []
        new, moves = bq.plan_move(len(self.entries), rows, to)
        if not moves:
            return rows
        for old, pos in moves:
            self._send(Change("move_service", (self.current, self.entries[old].ref, pos, self.radio),
                              self.current))
        self.entries = [self.entries[i] for i in new]
        self.view.show_entries()
        self.pump()
        return [new.index(r) for r in rows]

    def remove_entries(self, rows: list[int]) -> None:
        rows = self._rows(rows)
        if not rows or not self.can_edit():
            return
        for r in sorted(rows, reverse=True):
            e = self.entries.pop(r)
            self._send(Change("remove_service", (self.current, e.ref), self.current))
        self.view.show_entries()
        self.pump()

    def rename_entry(self, row: int, name: str) -> None:
        name = " ".join((name or "").split())
        rows = self._rows([row])
        if not rows or not name or not self.can_edit():
            return
        e = self.entries[row]
        if e.kind == bq.SPACER or name == e.name:
            return
        self._send(Change("rename_service", (self.current, e.ref, name, self._after(row)),
                          self.current))
        self.entries[row] = bq.Entry(e.ref, name, e.kind, e.locked)
        self.view.show_entries()
        self.pump()

    def add_channels(self, channels: list[bq.Entry], below: int | None = None) -> int:
        """Channels added below row *below* (at the end without one); those
        the list has already are left out - how many, returned."""
        if not self.can_edit():
            return 0
        have = {bq.ref_id(e.ref) for e in self.entries if e.ref}
        add: list[bq.Entry] = []
        for c in channels:
            key = bq.ref_id(c.ref)
            if c.ref and key not in have:
                have.add(key)
                add.append(c)
        skipped = len(channels) - len(add)
        if not add:
            return skipped
        at = len(self.entries) if below is None or not 0 <= below < len(self.entries) else below + 1
        before = next((e.ref for e in self.entries[at:] if not e.pending), "")
        for c in add:
            self._send(Change("add_service", (self.current, c.ref, before), self.current))
        self.entries[at:at] = [bq.Entry(c.ref, c.name, bq.SERVICE) for c in add]
        self.view.show_entries()
        self.pump()
        return skipped

    def add_marker(self, name: str, below: int | None = None, spacer: bool = False) -> None:
        name = " ".join((name or "").split())
        if not self.can_edit() or not (name or spacer):
            return
        at = len(self.entries) if below is None or not 0 <= below < len(self.entries) else below + 1
        before = next((e.ref for e in self.entries[at:] if not e.pending), "")
        if spacer:
            self._send(Change("add_spacer", (self.current, before), self.current))
        else:
            self._send(Change("add_marker", (self.current, name, before), self.current))
        self.entries.insert(at, bq.Entry("", "" if spacer else name, bq.SPACER if spacer else bq.MARKER))
        self.view.show_entries()
        self.pump()

    # -- backup and restore ---------------------------------------------------------

    def take_backup_now(self) -> None:
        """A backup asked for by hand (nothing changed yet): it is the
        window's backup from then on."""
        if not self.idle or not self.present or self.closed:
            return
        self.backup, self.busy = "taking", True
        self.view.say(tr("bq_backup_taking"))
        run_async(self.pool, self.editor.take_backup, self._backed_up, self._backup_failed)

    def restore(self, data: bytes) -> None:
        """A backup put back on the box: every list replaced - the lists it
        replaces backed up first, as before any change, when this window
        has no backup yet (so Undo can put them back)."""
        if not self.idle or not self.present or self.closed:
            return
        if not self.backup:
            self._restore_next = bytes(data)
            self.take_backup_now()
            return
        self._put_back(data)

    def _put_back(self, data: bytes) -> None:
        self.busy = True
        self.gen += 1
        self.view.say(tr("bq_restoring"))
        run_async(self.pool, self.editor.restore, self._restored, self._change_failed, data)

    def _restored(self, result: Any) -> None:
        if self.closed:
            return
        self.busy = False
        ok, text = result if isinstance(result, tuple) else (bool(result), "")
        self.changed = self.changed or ok
        self._dirty_bouquets = self._dirty_entries = True
        if not ok:
            # Not put back: a change made meanwhile is not sent either.
            self._stop(tr("box_failed", why=text))
            return
        self._final = tr("bq_restored")
        # A change made while it was put back goes next, then the lists are
        # read again (with nothing queued, pump reads them at once).
        self.pump()


class _List(QListWidget):
    """A list whose rows are dragged into order: the drop is reported
    (``dropped``: the rows, the row they go before) and the session
    reorders - Qt moves nothing itself. Ctrl+Up / Ctrl+Down move the
    selection a row, F2 renames, Delete removes."""

    dropped = pyqtSignal(list, int)
    nudged = pyqtSignal(int)
    removing = pyqtSignal()
    renaming = pyqtSignal()

    def __init__(self, drag: bool, multi: bool) -> None:
        super().__init__()
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection if multi
                              else QAbstractItemView.SelectionMode.SingleSelection)
        self.setStyleSheet(f"QListWidget {{ background:{P['input']}; border-radius:8px; }}")
        self.setUniformItemSizes(True)
        if drag:
            self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
            self.setDefaultDropAction(Qt.DropAction.MoveAction)
            self.setDropIndicatorShown(True)

    def rows(self) -> list[int]:
        out = []
        for it in self.selectedItems():
            r = it.data(ROW_ROLE)
            if isinstance(r, int):
                out.append(r)
        return sorted(out)

    def drop_row(self, y: int) -> int:
        """The row a drop at *y* goes before."""
        idx = self.indexAt(QPoint(4, y))
        if not idx.isValid():
            return self.count()
        rect = self.visualRect(idx)
        return idx.row() + (1 if y > rect.center().y() else 0)

    def dropEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        if event.source() is not self:
            event.ignore()
            return
        rows = self.rows()
        to = self.drop_row(int(event.position().y()))
        # Accepted as nothing done: the session reorders and the list is
        # drawn again from it.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        if rows:
            self.dropped.emit(rows, to)

    def keyPressEvent(self, event: Any) -> None:  # noqa: N802 - Qt's name
        mods = event.modifiers()
        ctrl = bool(mods & Qt.KeyboardModifier.ControlModifier)
        key = event.key()
        if ctrl and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self.nudged.emit(-1 if key == Qt.Key.Key_Up else 1)
            event.accept()
            return
        if key == Qt.Key.Key_F2:
            self.renaming.emit()
            event.accept()
            return
        if key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and not mods:
            self.removing.emit()
            event.accept()
            return
        super().keyPressEvent(event)


def _button(text: str, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    # Enter in a list or the search field never presses a button.
    b.setAutoDefault(False)
    b.setDefault(False)
    if tip:
        b.setToolTip(tip)
    return b


def _head(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setTextFormat(Qt.TextFormat.PlainText)     # a bouquet's name, the box's words
    lbl.setStyleSheet("font-weight:700;")
    return lbl


class BouquetEditorDialog(QDialog):
    """The box's channel lists, edited. ``edited`` (the box's client) once
    the window is gone, when something on the box changed."""

    edited = pyqtSignal(object)

    def __init__(self, parent: Any, client: Any, pool: Any, name: str = "") -> None:
        super().__init__(parent)
        self.client, self.pool = client, pool
        self.editor = client.bouquet_editor()
        self.setWindowTitle(tr("bq_title", name=name or "Enigma2"))
        self.resize(1120, 660)
        self.session = Session(self, self.editor, pool)
        self._avail: list[bq.Entry] = []
        self._avail_ref = ""
        self._avail_cache: dict[str, list[bq.Entry]] = {}
        self._folded: list[str] = []
        self._shown: list[str] = []
        self._sources_for: bool | None = None
        self._finishing = False
        self._finished = False
        self._started = False
        self._build()
        QTimer.singleShot(0, self._start)

    # -- building -------------------------------------------------------------

    def _build(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(10)
        top = QHBoxLayout()
        top.setSpacing(6)
        self.kind = QButtonGroup(self)
        self.kind_buttons: dict[bool, QPushButton] = {}
        for radio, key in ((False, "bq_tv"), (True, "bq_radio")):
            b = _button(tr(key))
            b.setObjectName("FilterChip")
            b.setCheckable(True)
            b.setChecked(not radio)
            self.kind.addButton(b)
            self.kind_buttons[radio] = b
            top.addWidget(b)
        self.kind.buttonClicked.connect(self._kind_picked)
        top.addStretch(1)
        self.backup_btn = _button(tr("bq_backup"))
        menu = QMenu(self.backup_btn)
        self.act_save = menu.addAction(tr("bq_backup_save"), self.save_backup)
        self.act_undo = menu.addAction(tr("bq_backup_undo"), self.undo_session)
        menu.addSeparator()
        self.act_restore = menu.addAction(tr("bq_restore_file"), self.restore_file)
        self.backup_btn.setMenu(menu)
        top.addWidget(self.backup_btn)
        lay.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(14)
        # The bouquets.
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(_head(tr("bq_bouquets")))
        self.bq_list = _List(drag=True, multi=False)
        self.bq_list.setToolTip(tr("bq_move_hint"))
        self.bq_list.setAccessibleName(tr("bq_bouquets"))
        self.bq_list.currentRowChanged.connect(self._bouquet_picked)
        self.bq_list.dropped.connect(self._bouquet_dropped)
        self.bq_list.nudged.connect(self._bouquet_nudged)
        self.bq_list.removing.connect(self.remove_bouquet)
        self.bq_list.renaming.connect(self.rename_bouquet)
        v.addWidget(self.bq_list, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.new_btn = _button(tr("bq_new"), "Ctrl+N")
        self.new_btn.clicked.connect(self.new_bouquet)
        self.bq_rename_btn = _button(tr("ctx_rename"), "F2")
        self.bq_rename_btn.clicked.connect(self.rename_bouquet)
        self.bq_delete_btn = _button(tr("ctx_delete"), "Del")
        self.bq_delete_btn.clicked.connect(self.remove_bouquet)
        for b in (self.new_btn, self.bq_rename_btn, self.bq_delete_btn):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        split.addWidget(col)
        # The chosen bouquet's entries.
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        self.entries_head = _head(tr("bq_channels"))
        v.addWidget(self.entries_head)
        self.entry_list = _List(drag=True, multi=True)
        self.entry_list.setToolTip(tr("bq_move_hint"))
        self.entry_list.setAccessibleName(tr("bq_channels"))
        self.entry_list.dropped.connect(self._entries_dropped)
        self.entry_list.nudged.connect(self._entries_nudged)
        self.entry_list.removing.connect(self.remove_entries)
        self.entry_list.renaming.connect(self.rename_entry)
        self.entry_list.itemSelectionChanged.connect(self._sync)
        v.addWidget(self.entry_list, 1)
        row = QHBoxLayout()
        row.setSpacing(6)
        self.marker_btn = _button(tr("bq_marker"), "Ctrl+M")
        self.marker_btn.clicked.connect(self.add_marker)
        self.spacer_btn = _button(tr("bq_spacer"))
        self.spacer_btn.clicked.connect(self.add_spacer)
        self.rename_btn = _button(tr("ctx_rename"), "F2")
        self.rename_btn.clicked.connect(self.rename_entry)
        self.remove_btn = _button(tr("btn_remove"), "Del")
        self.remove_btn.clicked.connect(self.remove_entries)
        for b in (self.marker_btn, self.spacer_btn, self.rename_btn, self.remove_btn):
            row.addWidget(b)
        row.addStretch(1)
        v.addLayout(row)
        split.addWidget(col)
        # What to add from.
        col = QWidget()
        v = QVBoxLayout(col)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)
        v.addWidget(_head(tr("bq_add_from")))
        self.source = QComboBox()
        self.source.setAccessibleName(tr("bq_add_from"))
        self.source.currentIndexChanged.connect(self._source_picked)
        v.addWidget(self.source)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("bq_search"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.returnPressed.connect(self._search_enter)
        v.addWidget(self.search)
        self.avail_list = _List(drag=False, multi=True)
        self.avail_list.setAccessibleName(tr("bq_add_from"))
        self.avail_list.itemActivated.connect(self.add_selected)
        self.avail_list.itemSelectionChanged.connect(self._sync)
        v.addWidget(self.avail_list, 1)
        row = QHBoxLayout()
        self.add_btn = _button(tr("bq_add"), tr("bq_add_hint"))
        self.add_btn.clicked.connect(self.add_selected)
        row.addWidget(self.add_btn)
        row.addStretch(1)
        v.addLayout(row)
        split.addWidget(col)
        split.setSizes([280, 420, 380])
        lay.addWidget(split, 1)

        self.backup_line = QLabel("")
        self.backup_line.setTextFormat(Qt.TextFormat.RichText)
        self.backup_line.setWordWrap(True)
        self.backup_line.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self.backup_line.linkActivated.connect(self._backup_link)
        self.backup_line.hide()
        lay.addWidget(self.backup_line)
        bottom = QHBoxLayout()
        self.status = QLabel(tr("bq_loading"))
        self.status.setTextFormat(Qt.TextFormat.PlainText)     # the box's words
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        bottom.addWidget(self.status, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close = buttons.button(QDialogButtonBox.StandardButton.Close)
        if close is not None:
            close.setAutoDefault(False)
        buttons.rejected.connect(self.reject)
        bottom.addWidget(buttons)
        lay.addLayout(bottom)

        for keys, slot in (("Ctrl+N", self.new_bouquet), ("Ctrl+M", self.add_marker),
                           ("Ctrl+F", self._focus_search)):
            sc = QShortcut(QKeySequence(keys), self)
            sc.activated.connect(slot)
        self._sync()

    def _start(self) -> None:
        """The first read, once - never after the window is done."""
        if self._gone() or self._started or self._finished:
            return
        self._started = True
        self.session.load()

    def _gone(self) -> bool:
        """Deleted, or done with: nothing more is asked of the box."""
        return sip.isdeleted(self) or self._finished

    # -- what the session shows -------------------------------------------------

    def say(self, text: str) -> None:
        if not self._gone():
            self.status.setText(text)

    def idle(self) -> None:
        if self._gone():
            return
        self._sync()
        if self._finishing and self.session.idle:
            self._finish()

    def no_editor(self, said: str = "") -> None:
        if self._gone():
            return
        log.info("enigma2: bouquet editor window - the box has none (%s)", said or "-")
        self.bq_list.clear()
        self.entry_list.clear()
        self.avail_list.clear()
        self.status.setText(tr("bq_none"))
        self._sync()

    def backup_taken(self) -> None:
        if self._gone():
            return
        s = self.session
        if s.backup_data:
            self.backup_line.setText(
                html.escape(tr("bq_backup_taken", name=s.backup_tar))
                + f' <a href="save" style="color:{P["text2"]};">'
                + html.escape(tr("bq_backup_save")) + "</a>")
        else:
            self.backup_line.setText(html.escape(tr("bq_backup_on_box", name=s.backup_tar)))
        self.backup_line.show()
        self._sync()
        if getattr(self, "_save_when_taken", False):
            self._save_when_taken = False
            self.save_backup()

    def go_on_without_backup(self, text: str) -> bool:
        if self._gone():
            return False
        return message_box.confirm(self, tr("bq_backup"), tr("bq_backup_failed_q", why=text),
                                   default_yes=False)

    def show_bouquets(self) -> None:
        if self._gone():
            return
        s = self.session
        lst = self.bq_list
        lst.blockSignals(True)
        lst.clear()
        at = -1
        for i, b in enumerate(s.bouquets):
            item = QListWidgetItem(b.name or b.ref)
            item.setData(ROW_ROLE, i)
            self._look(item, b)
            lst.addItem(item)
            if b.ref and bq.same_ref(b.ref, s.current):
                at = i
        if at >= 0:
            lst.setCurrentRow(at)
        lst.blockSignals(False)
        self._name_head()
        self._load_sources()
        self._sync()

    def _name_head(self) -> None:
        s = self.session
        name = next((b.name for b in s.bouquets if b.ref and bq.same_ref(b.ref, s.current)), "")
        self.entries_head.setText(tr("bq_channels_in", name=name) if name else tr("bq_channels"))

    def show_entries(self) -> None:
        if self._gone():
            return
        s = self.session
        self._name_head()
        lst = self.entry_list
        # The rows selected as they were drawn, kept by what they are.
        keep = {self._shown[r] for r in lst.rows() if r < len(self._shown)}
        self._shown = [e.ref for e in s.entries]
        lst.blockSignals(True)
        lst.clear()
        if not s.entries:
            text = "…" if s.loading else (tr("bq_empty") if s.current else "")
            if text:
                item = QListWidgetItem(text)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                lst.addItem(item)
        for i, e in enumerate(s.entries):
            item = QListWidgetItem(self._label(e))
            item.setData(ROW_ROLE, i)
            self._look(item, e)
            lst.addItem(item)
            if e.ref and e.ref in keep:
                item.setSelected(True)
        lst.setDragEnabled(s.can_edit())
        lst.blockSignals(False)
        self._mark_avail()
        self._sync()

    @staticmethod
    def _label(e: bq.Entry) -> str:
        if e.kind == bq.SPACER:
            text = "· · ·"
        elif e.kind == bq.GROUP:
            text = tr("bq_group_row", name=e.name)
        elif e.kind == bq.BOUQUET:
            text = f"{e.name} ›"
        else:
            text = e.name or e.ref
        return f"{text}  …" if e.pending else text

    @staticmethod
    def _look(item: QListWidgetItem, e: bq.Entry) -> None:
        if e.kind == bq.MARKER:
            f = QFont(item.font())
            f.setBold(True)
            item.setFont(f)
            item.setForeground(QColor(P["text3"]))
        elif e.kind == bq.SPACER:
            item.setForeground(QColor(P["muted2"]))
        if e.pending:
            f = QFont(item.font())
            f.setItalic(True)
            item.setFont(f)
            item.setForeground(QColor(P["muted"]))
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(tr("bq_pending"))
        elif e.locked:
            item.setToolTip(tr("bq_locked"))

    def _sync(self) -> None:
        """The buttons as the box, the lists and the selection allow."""
        if self._gone():
            return
        s = self.session
        on = bool(s.present)
        brow = self.bq_list.currentRow()
        b = s.bouquets[brow] if 0 <= brow < len(s.bouquets) else None
        self.new_btn.setEnabled(on)
        self.bq_rename_btn.setEnabled(on and b is not None and not b.pending)
        self.bq_delete_btn.setEnabled(on and b is not None and not b.pending)
        for btn in self.kind_buttons.values():
            btn.setEnabled(s.present is not False)
        edit = s.can_edit()
        rows = self.entry_list.rows()
        one = s.entries[rows[0]] if len(rows) == 1 and rows[0] < len(s.entries) else None
        self.marker_btn.setEnabled(edit)
        self.spacer_btn.setEnabled(edit)
        self.rename_btn.setEnabled(edit and one is not None and one.kind != bq.SPACER)
        self.remove_btn.setEnabled(edit and bool(rows))
        self.add_btn.setEnabled(edit and bool(self.avail_list.rows()))
        self.source.setEnabled(on)
        self.search.setEnabled(on)
        self.backup_btn.setEnabled(on)
        self.act_save.setEnabled(on and s.idle)
        self.act_undo.setEnabled(on and s.idle and bool(s.backup_data))
        self.act_restore.setEnabled(on and s.idle)

    # -- the bouquets ---------------------------------------------------------------

    def _kind_picked(self, button: Any) -> None:
        self.session.set_radio(button is self.kind_buttons[True])

    def _bouquet_picked(self, row: int) -> None:
        self.session.select(row)
        self._sync()

    def _bouquet_dropped(self, rows: list, to: int) -> None:
        if rows:
            self.session.move_bouquet(rows[0], to)

    def _bouquet_nudged(self, step: int) -> None:
        row = self.bq_list.currentRow()
        if row >= 0:
            self.session.move_bouquet(row, row - 1 if step < 0 else row + 2)

    def _ask_text(self, title: str, label: str, text: str = "") -> str:
        got, ok = message_box.get_text(self, title, label, text=text)
        return " ".join((got or "").split()) if ok else ""

    def new_bouquet(self) -> None:
        if not self.session.present:
            return
        name = self._ask_text(tr("bq_new"), tr("bq_new_q"))
        if name:
            self.session.add_bouquet(name)

    def rename_bouquet(self) -> None:
        row = self.bq_list.currentRow()
        b = self.session._bouquet(row)
        if b is None:
            return
        name = self._ask_text(tr("ctx_rename"), tr("bq_rename_q", name=b.name), b.name)
        if name:
            self.session.rename_bouquet(row, name)

    def remove_bouquet(self) -> None:
        row = self.bq_list.currentRow()
        b = self.session._bouquet(row)
        if b is None:
            return
        if message_box.confirm(self, tr("ctx_delete"), tr("bq_delete_q", name=b.name),
                               default_yes=False, danger=True):
            self.session.remove_bouquet(row)

    # -- the entries ------------------------------------------------------------------

    def _select_rows(self, rows: list[int]) -> None:
        """The moved rows still selected where they went, the first current."""
        lst = self.entry_list
        lst.blockSignals(True)
        lst.clearSelection()
        first = None
        for i in range(lst.count()):
            it = lst.item(i)
            if it is not None and it.data(ROW_ROLE) in rows:
                it.setSelected(True)
                first = first or it
        if first is not None:
            lst.setCurrentItem(first, QItemSelectionModel.SelectionFlag.NoUpdate)
            lst.scrollToItem(first)
        lst.blockSignals(False)
        self._sync()

    def _entries_dropped(self, rows: list, to: int) -> None:
        self._select_rows(self.session.move_entries(list(rows), to))

    def _entries_nudged(self, step: int) -> None:
        rows = self.entry_list.rows()
        if not rows:
            return
        to = rows[0] - 1 if step < 0 else rows[-1] + 2
        if to < 0 or to > len(self.session.entries):
            return
        self._select_rows(self.session.move_entries(rows, to))

    def remove_entries(self) -> None:
        """The selected rows out; the row after them selected, for the keys."""
        rows = self.entry_list.rows()
        before = len(self.session.entries)
        self.session.remove_entries(rows)
        left = len(self.session.entries)
        if rows and left < before and left:
            self._select_rows([min(rows[0], left - 1)])

    def rename_entry(self) -> None:
        rows = self.entry_list.rows()
        if len(rows) != 1 or not self.session.can_edit():
            return
        e = self.session.entries[rows[0]]
        if e.kind == bq.SPACER or e.pending:
            return
        name = self._ask_text(tr("ctx_rename"), tr("bq_rename_q", name=e.name), e.name)
        if name:
            self.session.rename_entry(rows[0], name)

    def _below(self) -> int | None:
        rows = self.entry_list.rows()
        return rows[-1] if rows else None

    def add_marker(self) -> None:
        if not self.session.can_edit():
            return
        name = self._ask_text(tr("bq_marker"), tr("bq_marker_q"))
        if name:
            self.session.add_marker(name, self._below())

    def add_spacer(self) -> None:
        self.session.add_marker("", self._below(), spacer=True)

    # -- what to add from -----------------------------------------------------------------

    def _load_sources(self) -> None:
        """All channels, the satellites and the providers of this kind."""
        s = self.session
        if self._gone() or not s.present or self._sources_for is s.radio:
            return
        self._sources_for = s.radio
        self.source.blockSignals(True)
        self.source.clear()
        self.source.addItem(tr("bq_all"), bq.all_ref(s.radio))
        self.source.setCurrentIndex(0)
        self.source.blockSignals(False)
        self._avail, self._avail_ref = [], ""
        self._source_picked()
        run_async(self.pool, self._get_sources, self._sources_came, None, s.radio)

    def _get_sources(self, radio: bool) -> tuple[bool, list, list]:
        return radio, self.editor.satellites(radio), self.editor.providers(radio)

    def _sources_came(self, got: Any) -> None:
        if self._gone():
            return
        radio, sats, provs = got
        if radio != self.session.radio:
            return
        self.source.blockSignals(True)
        if sats:
            self.source.insertSeparator(self.source.count())
            for e in sats:
                self.source.addItem(e.name, e.ref)
        if provs:
            self.source.insertSeparator(self.source.count())
            for e in provs:
                self.source.addItem(tr("bq_provider", name=e.name), e.ref)
        self.source.blockSignals(False)

    def _source_picked(self, *_a: Any) -> None:
        ref = self.source.currentData()
        if self._gone() or not isinstance(ref, str) or not ref:
            return
        self._avail_ref = ref
        cached = self._avail_cache.get(ref)
        if cached is not None:
            self._show_avail(cached)
            return
        self.avail_list.clear()
        self.avail_list.addItem("…")
        run_async(self.pool, self._get_channels, self._channels_came, self._channels_failed, ref)

    def _get_channels(self, ref: str) -> tuple[str, list]:
        return ref, self.editor.channels_in(ref)

    def _channels_came(self, got: Any) -> None:
        if self._gone():
            return
        ref, found = got
        self._avail_cache[ref] = list(found)
        if ref == self._avail_ref:
            self._show_avail(found)

    def _channels_failed(self, err: Any = None) -> None:
        if self._gone():
            return
        self.avail_list.clear()
        self.say(tr("box_failed", why=why(err)))

    def _show_avail(self, found: list[bq.Entry]) -> None:
        self._avail = list(found)
        # Folded once: the search then is substring tests only.
        self._folded = [fold(e.name) for e in self._avail]
        lst = self.avail_list
        lst.setUpdatesEnabled(False)
        lst.clear()
        for i, e in enumerate(self._avail):
            item = QListWidgetItem(e.name or e.ref)
            item.setData(ROW_ROLE, i)
            lst.addItem(item)
        lst.setUpdatesEnabled(True)
        self._mark_avail()
        self._filter(self.search.text())

    def _mark_avail(self) -> None:
        """Channels the chosen bouquet has already, shown dimmed."""
        have = {bq.ref_id(e.ref) for e in self.session.entries if e.ref}
        muted, plain = QColor(P["muted2"]), QColor(P["text2"])
        for i in range(self.avail_list.count()):
            item = self.avail_list.item(i)
            r = item.data(ROW_ROLE) if item is not None else None
            if item is None or not isinstance(r, int) or r >= len(self._avail):
                continue
            inside = bq.ref_id(self._avail[r].ref) in have
            item.setForeground(muted if inside else plain)
            item.setToolTip(tr("bq_in_list") if inside else "")

    def _filter(self, text: str) -> None:
        terms = words(text or "")
        for i in range(self.avail_list.count()):
            item = self.avail_list.item(i)
            r = item.data(ROW_ROLE) if item is not None else None
            if item is None or not isinstance(r, int) or r >= len(self._folded):
                continue
            folded = self._folded[r]
            item.setHidden(bool(terms) and not all(t in folded for t in terms))
        self._sync()

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def _search_enter(self) -> None:
        """Enter in the search: into the list, on the first channel found."""
        lst = self.avail_list
        for i in range(lst.count()):
            item = lst.item(i)
            if item is not None and not item.isHidden() and isinstance(item.data(ROW_ROLE), int):
                lst.setCurrentItem(item)
                lst.setFocus()
                return

    def add_selected(self, *_a: Any) -> None:
        """The channels picked on the right (those the search shows) into
        the list, below its selection."""
        rows = sorted(it.data(ROW_ROLE) for it in self.avail_list.selectedItems()
                      if not it.isHidden() and isinstance(it.data(ROW_ROLE), int))
        picked = [self._avail[r] for r in rows if r < len(self._avail)]
        if not picked or not self.session.can_edit():
            return
        skipped = self.session.add_channels(picked, self._below())
        if skipped:
            self.say(tr("bq_already", n=skipped))

    # -- backup ---------------------------------------------------------------------------

    def _backup_link(self, _href: str) -> None:
        self.save_backup()

    def save_backup(self) -> None:
        """The window's backup to a file - taken now when there is none yet."""
        s = self.session
        if not s.backup_data:
            if s.backup in ("", "skipped") and s.idle:
                self._save_when_taken = True
                s.take_backup_now()
            return
        name = (s.backup_tar or bq.backup_name() + ".tar")
        path, _f = QFileDialog.getSaveFileName(self, tr("bq_backup_save"), name, "tar (*.tar)")
        if not path:
            return
        try:
            with open(path, "wb") as fh:
                fh.write(s.backup_data)
        except OSError as e:
            log.warning("bouquet backup not saved: %s", e)
            self.say(tr("box_failed", why=str(e)))
            return
        self.say(tr("box_grab_saved", path=path))

    def _restore(self, data: bytes, question: str) -> None:
        if not self.session.idle:
            return
        if not bq.is_backup(data):
            self.say(tr("bq_not_backup"))
            return
        if message_box.confirm(self, tr("bq_backup"), question, default_yes=False, danger=True):
            self.session.restore(data)

    def undo_session(self) -> None:
        if self.session.backup_data:
            self._restore(self.session.backup_data, tr("bq_backup_undo_q"))

    def restore_file(self) -> None:
        path, _f = QFileDialog.getOpenFileName(self, tr("bq_restore_file"), "", "tar (*.tar)")
        if not path:
            return
        try:
            if os.path.getsize(path) > bq.MAX_BACKUP:
                raise OSError("too large")
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as e:
            self.say(tr("box_failed", why=str(e)))
            return
        self._restore(data, tr("bq_restore_q", file=os.path.basename(path)))

    # -- closing --------------------------------------------------------------------------

    def done(self, result: int) -> None:
        """Closed: gone at once when nothing is on its way to the box; else
        hidden until the box has it all."""
        if self.session.idle:
            self._finish()
        else:
            self._finishing = True
            log.info("enigma2: bouquet editor closed with %d change(s) still on the way",
                     len(self.session.queue) + 1)
        super().done(result)

    def _finish(self) -> None:
        if self._gone():
            return
        self._finished = True
        self.session.closed = True
        if self.session.changed:
            self.edited.emit(self.client)
        self.hide()
        self.deleteLater()
