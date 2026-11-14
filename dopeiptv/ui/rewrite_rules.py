"""A source's address rewrite rules in its Nerd options (core/url_rewrite.py):
one row per rule - find, replace, a switch for a regular expression, up and
remove - in the order they apply, and a row to add one."""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from ..core import url_rewrite
from ..i18n import tr


class _RuleRow(QWidget):
    def __init__(self, rule: url_rewrite.Rule, owner: RulesEditor) -> None:
        super().__init__()
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.find = QLineEdit(rule.find)
        self.find.setPlaceholderText(tr("nerd_rw_find"))
        self.find.setAccessibleName(tr("nerd_rw_find"))
        self.replace = QLineEdit(rule.replace)
        self.replace.setPlaceholderText(tr("nerd_rw_replace"))
        self.replace.setAccessibleName(tr("nerd_rw_replace"))
        arrow = QLabel("→")
        self.regex = QPushButton(".*")
        self.regex.setObjectName("InlineToggle")
        self.regex.setCheckable(True)
        self.regex.setChecked(rule.regex)
        self.regex.setToolTip(tr("nerd_rw_regex"))
        self.regex.setAccessibleName(tr("nerd_rw_regex"))
        self.up = QPushButton("↑")
        self.up.setToolTip(tr("nerd_rw_up"))
        self.up.setAccessibleName(tr("nerd_rw_up"))
        self.remove = QPushButton("✕")
        self.remove.setToolTip(tr("nerd_rw_remove"))
        self.remove.setAccessibleName(tr("nerd_rw_remove"))
        for b in (self.regex, self.up, self.remove):
            # The small square buttons of the app's compact strips: a plain
            # button's padding left no room for its one character.
            b.setObjectName("InlineToggle")
            b.setFixedWidth(34)
        h.addWidget(self.find, 1)
        h.addWidget(arrow)
        h.addWidget(self.replace, 1)
        h.addWidget(self.regex)
        h.addWidget(self.up)
        h.addWidget(self.remove)
        self.find.textChanged.connect(owner.changed)
        self.replace.textChanged.connect(owner.changed)
        self.regex.toggled.connect(owner.changed)
        self.up.clicked.connect(self._up)
        self.remove.clicked.connect(self._remove)
        self._owner = owner

    def rule(self) -> url_rewrite.Rule:
        return url_rewrite.Rule(self.find.text(), self.replace.text(), self.regex.isChecked())

    def set_rule(self, rule: url_rewrite.Rule) -> None:
        self.find.setText(rule.find)
        self.replace.setText(rule.replace)
        self.regex.setChecked(rule.regex)

    def _up(self) -> None:
        self._owner.move_up(self)

    def _remove(self) -> None:
        self._owner.remove(self)


class RulesEditor(QWidget):
    """The rules, in order. ``rules()`` is what the rows say (a row left
    empty is no rule); ``stored()`` is what the source keeps."""

    edited = pyqtSignal()

    def __init__(self, value: object = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(6)
        col.addLayout(self._rows_box)
        self.add_btn = QPushButton(tr("nerd_rw_add"))
        self.add_btn.clicked.connect(self._add_clicked)
        foot = QHBoxLayout()
        foot.addWidget(self.add_btn)
        foot.addStretch(1)
        col.addLayout(foot)
        self.rows: list[_RuleRow] = []
        for rule in url_rewrite.parse(value):
            self.add(rule)
        self._ends()

    # -- rows ----------------------------------------------------------------

    def add(self, rule: url_rewrite.Rule | None = None) -> _RuleRow:
        row = _RuleRow(rule or url_rewrite.Rule(""), self)
        self.rows.append(row)
        self._rows_box.addWidget(row)
        self._ends()
        return row

    def _add_clicked(self) -> None:
        if len(self.rows) >= url_rewrite.MAX_RULES:
            return
        self.add().find.setFocus()
        self.changed()

    def remove(self, row: _RuleRow) -> None:
        if row in self.rows:
            self.rows.remove(row)
            self._rows_box.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
            self._ends()
            self.changed()

    def move_up(self, row: _RuleRow) -> None:
        i = self.rows.index(row) if row in self.rows else 0
        if i <= 0:
            return
        above = self.rows[i - 1]
        mine, theirs = row.rule(), above.rule()
        above.set_rule(mine)
        row.set_rule(theirs)
        above.find.setFocus()
        self.changed()

    def _ends(self) -> None:
        for i, row in enumerate(self.rows):
            row.up.setEnabled(i > 0)
        self.add_btn.setEnabled(len(self.rows) < url_rewrite.MAX_RULES)

    def changed(self, *_a) -> None:
        self.edited.emit()

    # -- what they say ---------------------------------------------------------

    def rules(self) -> list[url_rewrite.Rule]:
        """Every row with something in it, in order - also one that cannot
        work, so the line can say which and why."""
        return [r for r in (row.rule() for row in self.rows) if r.find or r.replace]

    def stored(self) -> str:
        """What the source's options keep: the rules that have something to
        find, as JSON ("" for none)."""
        return url_rewrite.dump([r for r in self.rules() if r.find])
