"""An Enigma2 box's AutoTimer in the window (providers/autotimer.py): the
editor of one rule, the preview of what a rule would record, and the box's
rules beside the app's own under Settings > Recording > Series recordings,
each row saying who records it. Every request to the box runs off the
window's thread; an answer that comes after its window closed is dropped,
and a box without the plugin is said so in a line, never an error dialog."""

from __future__ import annotations

from typing import Any, Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, Qt, QTime
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTimeEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from ..core.log import log
from ..core.workers import run_async
from ..i18n import tr
from ..providers import autotimer as at
from .settings_ui import Chip, GridSpin, Stepper
from .theme import P

#: The item data role a box rule's row keeps its rule in.
BOX_ROLE = int(Qt.ItemDataRole.UserRole) + 1
_SEARCH = ((at.EXACT, "series_match_title"), (at.PARTIAL, "series_match_word"),
           (at.START, "at_search_start"), (at.DESCRIPTION, "at_search_description"))
_PAD = [0, 1, 2, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120]


def why(err: Any) -> str:
    msg = getattr(err, "localized", None)
    return msg() if callable(msg) else str(err or "")


def _key(sref: str) -> str:
    from ..providers.enigma2 import ref_key
    return ref_key(sref)


def rule_label(rule: at.Rule) -> str:
    """A box rule as the list says it: its words, where, and off when off."""
    text = rule.label
    word = rule.search_type != at.EXACT
    names = [rule.names.get(r) or "" for r in rule.services + rule.bouquets]
    names = [n for n in names if n]
    count = len(rule.bouquets) + len(rule.services)
    if count:
        where = ", ".join(names[:2]) if names else str(count)
        if count > 2:
            where += f" +{count - 2}"
        label = (tr("series_row_word_on", word=text, channel=where) if word
                 else tr("series_row_title_on", title=text, channel=where))
    else:
        label = (tr("series_row_word_any", word=text) if word
                 else tr("series_row_title_any", title=text))
    return label if rule.enabled else tr("at_row_off", rule=label)


def marked(label: str, by_box: bool) -> str:
    """A row with who records it."""
    return tr("series_row_by", rule=label,
              who=tr("series_by_box" if by_box else "series_by_app"))


class AutoTimerDialog(QDialog):
    """One rule of the box's AutoTimer, made or changed: the words and how
    they match, the channels from the box's bouquets (none ticked: any),
    a time span, the days, its own padding, record or only zap, the folder
    (the box's own list of them) and duplicates. What the editor does not
    show stays on the box as it is (``/autotimer/edit`` changes only what
    it is sent). Saved off the window's thread; the box's answer is said
    here, and the window closes once the box took it."""

    def __init__(self, parent: Any, client: Any, pool: Any,
                 rule: at.Rule | None = None) -> None:
        super().__init__(parent)
        self.client, self.pool = client, pool
        self.before = rule if rule is not None and rule.id is not None else None
        self.rule = rule.copy() if rule is not None else at.Rule()
        self.said = ""
        self.setWindowTitle(tr("at_title"))
        self.setMinimumWidth(520)
        f = QFormLayout(self)
        f.setSpacing(10)
        r = self.rule
        self.enabled = QCheckBox(tr("at_enabled"))
        self.enabled.setChecked(r.enabled)
        f.addRow("", self.enabled)
        self.match = QLineEdit(r.match)
        f.addRow(tr("series_text"), self.match)
        self.search = QComboBox()
        for kind, key in _SEARCH:
            self.search.addItem(tr(key), kind)
        if r.search_type not in at.SEARCH_TYPES:
            self.search.addItem(tr("at_search_other", kind=r.search_type), r.search_type)
        self.search.setCurrentIndex(max(0, self.search.findData(r.search_type)))
        self.case = QCheckBox(tr("at_case"))
        self.case.setChecked(r.case)
        f.addRow(tr("series_match"), self._line(self.search, self.case))
        self.name = QLineEdit(r.name if r.name != r.match else "")
        f.addRow(tr("at_name"), self.name)
        f.addRow(tr("box_channels"), self._channels())
        self.span = QCheckBox(tr("at_span"))
        self.span_from, self.span_to = QTimeEdit(), QTimeEdit()
        for edit, text in zip((self.span_from, self.span_to),
                              (r.timespan or ("20:00", "23:00")), strict=True):
            edit.setDisplayFormat("HH:mm")
            edit.setTime(QTime.fromString(text, "HH:mm"))
        self.span.setChecked(r.timespan is not None)
        f.addRow("", self._line(self.span, self.span_from, QLabel("–"), self.span_to))
        self.days = [Chip(self._day(i), str(i) in r.day_set()) for i in range(7)]
        days_hint = QLabel(tr("at_days_hint"))
        days_hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        f.addRow(tr("at_days"), self._line(*self.days, days_hint))
        self.own_pad = QCheckBox(tr("at_pad"))
        self.own_pad.setChecked(r.offset is not None)
        self.pad_before = self._pad((r.offset or (0, 0))[0], tr("rec_pad_before"))
        self.pad_after = self._pad((r.offset or (0, 0))[1], tr("rec_pad_after"))
        f.addRow("", self._line(self.own_pad, QLabel(tr("rec_pad_before")), self.pad_before,
                                QLabel(tr("rec_pad_after")), self.pad_after))
        self.justplay = QComboBox()
        self.justplay.addItem(tr("series_do_record"), False)
        self.justplay.addItem(tr("at_do_zap"), True)
        self.justplay.setCurrentIndex(1 if r.justplay else 0)
        f.addRow(tr("series_do"), self.justplay)
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.addItem(tr("at_folder_default"), "")
        if r.location:
            self.folder.addItem(r.location, r.location)
            self.folder.setCurrentIndex(1)
        f.addRow(tr("at_folder"), self.folder)
        self.dup = QComboBox()
        for n in at.DUPLICATES:
            self.dup.addItem(tr(f"at_dup_{n}"), n)
        self.dup.setCurrentIndex(max(0, self.dup.findData(r.avoid_dup)))
        f.addRow(tr("at_dup"), self.dup)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        f.addRow(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        f.addRow(self.buttons)
        self._ask(client.movie_folders, self._folders)
        if client.autotimer().present is None:
            self._ask(client.autotimer().rules, self._checked)

    # -- building -------------------------------------------------------------

    @staticmethod
    def _line(*widgets: QWidget) -> QWidget:
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)
        for x in widgets:
            h.addWidget(x)
        h.addStretch(1)
        return w

    @staticmethod
    def _day(i: int) -> str:
        from PyQt6.QtCore import QLocale

        from .qt_i18n import app_locale
        return app_locale().dayName(i + 1, QLocale.FormatType.ShortFormat)

    @staticmethod
    def _pad(value: int, name: str) -> Stepper:
        spin = GridSpin(sorted(set(_PAD) | {value}), lambda v: tr("opt_minutes", n=v))
        spin.setValue(value)
        box = Stepper(spin, width=spin.widest() + 80)
        box.setAccessibleName(name)
        return box

    def _channels(self) -> QWidget:
        """The box's bouquets, each with its channels: a bouquet ticked takes
        them all (the rule follows the bouquet), a channel ticked only it."""
        r = self.rule
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(170)
        services = {_key(s) for s in r.services}
        seen_b: set[str] = set()
        seen_s: set[str] = set()
        bouquets = self.client.bouquet_channels() if hasattr(self.client, "bouquet_channels") else []
        for bref, name, items in bouquets:
            top = QTreeWidgetItem([name or bref])
            top.setData(0, Qt.ItemDataRole.UserRole, ("b", bref))
            top.setCheckState(0, Qt.CheckState.Checked if bref in r.bouquets
                              else Qt.CheckState.Unchecked)
            seen_b.add(bref)
            for sref, cname in items:
                ch = QTreeWidgetItem([cname or sref])
                ch.setData(0, Qt.ItemDataRole.UserRole, ("s", sref))
                ch.setCheckState(0, Qt.CheckState.Checked if _key(sref) in services
                                 else Qt.CheckState.Unchecked)
                seen_s.add(_key(sref))
                top.addChild(ch)
            self.tree.addTopLevelItem(top)
            if any(top.child(i).checkState(0) == Qt.CheckState.Checked
                   for i in range(top.childCount())):
                top.setExpanded(True)
        # What the rule has that this list does not (another bouquet, a
        # radio channel left out): kept as it is.
        self.kept_b = [b for b in r.bouquets if b not in seen_b]
        self.kept_s = [s for s in r.services if _key(s) not in seen_s]
        hint = QLabel(tr("at_channels_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(4)
        v.addWidget(self.tree)
        v.addWidget(hint)
        if self.kept_b or self.kept_s:
            more = QLabel(tr("at_channels_more", n=len(self.kept_b) + len(self.kept_s)))
            more.setWordWrap(True)
            more.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
            v.addWidget(more)
        return box

    # -- the box --------------------------------------------------------------

    def _ask(self, fn: Callable[..., Any], done: Callable[[Any], None], *args: Any) -> None:
        run_async(self.pool, fn, done, self._failed, *args)

    def _gone(self) -> bool:
        return sip.isdeleted(self)

    def _failed(self, err: Any = None) -> None:
        if self._gone():
            return
        self.buttons.setEnabled(True)
        self.status.setText(tr("box_failed", why=why(err)))

    def _folders(self, found: Any) -> None:
        if self._gone():
            return
        for d in found or []:
            if isinstance(d, str) and self.folder.findData(d) < 0:
                self.folder.addItem(d, d)

    def _checked(self, got: Any) -> None:
        if self._gone() or getattr(got, "present", True):
            return
        self.status.setText(tr("at_none"))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(False)

    def value(self) -> at.Rule:
        """The rule as the window has it now."""
        r = self.rule.copy()
        r.enabled = self.enabled.isChecked()
        r.match = " ".join(self.match.text().split())
        r.name = " ".join(self.name.text().split()) or r.match
        r.search_type = str(self.search.currentData())
        r.case = self.case.isChecked()
        bouquets, services = list(self.kept_b), list(self.kept_s)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top is None:
                continue
            if top.checkState(0) == Qt.CheckState.Checked:
                bouquets.append(top.data(0, Qt.ItemDataRole.UserRole)[1])
            for j in range(top.childCount()):
                ch = top.child(j)
                sref = ch.data(0, Qt.ItemDataRole.UserRole)[1] if ch is not None else ""
                if ch is not None and ch.checkState(0) == Qt.CheckState.Checked and sref not in services:
                    services.append(sref)
        r.bouquets, r.services = bouquets, services
        r.timespan = ((self.span_from.time().toString("HH:mm"), self.span_to.time().toString("HH:mm"))
                      if self.span.isChecked() else None)
        r.days = [str(i) for i, c in enumerate(self.days) if c.isChecked()]
        r.offset = ((self.pad_before.value(), self.pad_after.value())
                    if self.own_pad.isChecked() else None)
        r.justplay = bool(self.justplay.currentData())
        folder = self.folder.currentText().strip()
        r.location = "" if self.folder.currentIndex() == 0 and folder == self.folder.itemText(0) else folder
        r.avoid_dup = int(self.dup.currentData() or 0)
        return r

    def _save(self) -> None:
        rule = self.value()
        if not rule.match:
            self.status.setText(tr("at_need_match"))
            return
        self.buttons.setEnabled(False)
        self.status.setText(tr("at_saving"))
        self._ask(self.client.autotimer().save, self._saved, rule, self.before)

    def _saved(self, result: Any) -> None:
        if self._gone():
            return
        ok, text = result if isinstance(result, tuple) else (bool(result), "")
        if not ok:
            self.buttons.setEnabled(True)
            self.status.setText(tr("box_failed", why=text))
            return
        self.said = text
        self.accept()


class AutoTimerPreview(QDialog):
    """What a rule would record now, as the box's own simulate says - the
    box looks through its whole guide, which can take a minute."""

    def __init__(self, parent: Any, client: Any, pool: Any, rule: at.Rule) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("at_preview_title", name=rule.label))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMinimumSize(480, 320)
        v = QVBoxLayout(self)
        self.status = QLabel(tr("at_asking"))
        self.status.setWordWrap(True)
        self.status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        self.list = QListWidget()
        v.addWidget(self.status)
        v.addWidget(self.list, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        run_async(pool, client.autotimer().simulate, self._came, self._failed, rule)

    def _came(self, found: Any) -> None:
        if sip.isdeleted(self):
            return
        from .qt_i18n import format_when
        found = [x for x in (found or []) if isinstance(x, dict)]
        self.status.setText(tr("at_preview_n", n=len(found)) if found else tr("at_preview_none"))
        for x in found:
            when = f"{format_when(x['start'])}–{format_when(x['stop'], 'HH:mm')}"
            self.list.addItem(f"{when}  {x.get('title') or ''} · {x.get('channel') or ''}")

    def _failed(self, err: Any = None) -> None:
        if not sip.isdeleted(self):
            self.status.setText(tr("box_failed", why=why(err)))


class BoxRules(QObject):
    """The box's AutoTimer rules in the series card: read off the window's
    thread when the card is built, listed after the app's own with who
    records each, and their buttons - new, edit, on/off, preview, run now,
    delete. Only for an Enigma2 box; for any other source it shows
    nothing and marks nothing."""

    def __init__(self, window: Any, rule_list: QListWidget,
                 refill: Callable[[], None], parent_dialog: QWidget,
                 relayout: Callable[[], None] | None = None) -> None:
        super().__init__(rule_list)
        self.window, self.client = window, window.client
        self.list, self.refill, self.dialog = rule_list, refill, parent_dialog
        self.relayout = relayout
        self.active = callable(getattr(self.client, "autotimer", None))
        self.rules: list[at.Rule] = []
        self.loaded = False
        self.status = QLabel("")
        self.status.setObjectName("SettingsHint")
        self.status.setWordWrap(True)
        self.buttons = {k: QPushButton(tr(k)) for k in
                        ("at_new", "btn_edit", "at_off", "at_preview", "at_run")}
        self.buttons["at_new"].clicked.connect(self.new)
        self.buttons["btn_edit"].clicked.connect(self.edit)
        self.buttons["at_off"].clicked.connect(self.toggle)
        self.buttons["at_preview"].clicked.connect(self.preview)
        self.buttons["at_run"].clicked.connect(self.run)

    @property
    def present(self) -> bool:
        return self.active and self.client.autotimer().present is True

    @property
    def marks(self) -> bool:
        """Whether the rows say who records each: a box that has (or may
        have) the plugin."""
        return self.active and self.client.autotimer().present is not False

    def say(self, text: str) -> None:
        """The line under the buttons (hidden while it has nothing to say)."""
        if self._gone():
            return
        self.status.setText(text)
        if self.relayout is not None:
            self.relayout()

    def load(self) -> None:
        if not self.active:
            return
        self.say(tr("at_loading"))
        self.sync()
        run_async(self.window.pool, self.client.autotimer().rules, self._came, self._failed)

    def _gone(self) -> bool:
        return sip.isdeleted(self) or sip.isdeleted(self.list)

    def _came(self, got: Any) -> None:
        if self._gone():
            return
        self.loaded = True
        self.rules = list(getattr(got, "rules", []) or [])
        self.refill()
        self.say("" if getattr(got, "present", False) else tr("at_none"))

    def _failed(self, err: Any = None) -> None:
        if self._gone():
            return
        self.say(tr("box_failed", why=why(err)))
        self.sync()

    def items(self, icon: QIcon) -> list[QListWidgetItem]:
        """A row for each of the box's rules, marked as the box's."""
        out = []
        for r in sorted(self.rules, key=lambda r: r.label.casefold()):
            item = QListWidgetItem(icon, marked(rule_label(r), True))
            item.setData(BOX_ROLE, r)
            out.append(item)
        return out

    def current(self) -> at.Rule | None:
        item = self.list.currentItem()
        rule = item.data(BOX_ROLE) if item is not None else None
        return rule if isinstance(rule, at.Rule) else None

    def sync(self) -> None:
        """The buttons as the selected row and the box allow."""
        rule = self.current()
        for k, b in self.buttons.items():
            b.setEnabled(k in ("at_new", "at_run") or rule is not None)
        if rule is not None:
            self.buttons["at_off"].setText(tr("at_off" if rule.enabled else "at_on"))

    # -- what the buttons do ----------------------------------------------------

    def _changed(self, result: Any) -> None:
        if self._gone():
            return
        ok, text = result if isinstance(result, tuple) else (bool(result), "")
        if not ok:
            self.say(tr("box_failed", why=text))
            return
        self.load()
        check = getattr(self.window, "_check_series_rules", None)
        if callable(check):
            check()

    def _do(self, fn: Callable[..., Any], *args: Any) -> None:
        self.say(tr("at_saving"))
        run_async(self.window.pool, fn, self._changed, self._failed, *args)

    def new(self) -> None:
        self.open_editor(None)

    def edit(self) -> None:
        rule = self.current()
        if rule is not None:
            self.open_editor(rule)

    def open_editor(self, rule: at.Rule | None) -> None:
        d = AutoTimerDialog(self.dialog, self.client, self.window.pool, rule)
        if d.exec():
            self._changed((True, d.said))
        d.deleteLater()

    def toggle(self) -> None:
        rule = self.current()
        if rule is not None:
            self._do(self.client.autotimer().set_enabled, rule, not rule.enabled)

    def remove(self, rule: at.Rule) -> None:
        from .widgets import confirm
        if confirm(self.dialog, tr("ctx_delete"), tr("at_delete_q", name=rule.label),
                   default_yes=False):
            self._do(self.client.autotimer().remove, rule)

    def preview(self) -> None:
        rule = self.current()
        if rule is not None:
            AutoTimerPreview(self.dialog, self.client, self.window.pool, rule).show()

    def run(self) -> None:
        self.say(tr("at_running"))
        run_async(self.window.pool, self.client.autotimer().run, self._ran, self._failed)

    def _ran(self, result: Any) -> None:
        if self._gone():
            return
        ok, text = result if isinstance(result, tuple) else (bool(result), "")
        log.info("enigma2: AutoTimer run now - %s", "done" if ok else "failed")
        self.say(tr("at_ran", said=" ".join(text.split())) if ok else tr("box_failed", why=text))
        check = getattr(self.window, "_check_series_rules", None)
        if ok and callable(check):
            check()
