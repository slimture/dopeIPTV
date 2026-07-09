"""Manual TMDB match dialog: search for a movie/series on TMDB and pick the
correct entry, overriding whatever the auto-matcher chose. Solves the
"provider titles are messy" problem (EN|Movie Title (2023) 1080p WEB) and
ambiguous names (multiple films sharing a title)."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout, QWidget,
)

from .i18n import tr
from .metadata import PosterResolver
from .theme import P
from .workers import run_async


class TmdbMatchDialog(QDialog):
    """Modal picker: type a title, hit search, pick a candidate poster."""

    def __init__(self, window, title: str, kind: str,
                 on_pick: Callable[[dict], None]) -> None:
        super().__init__(window)
        self.window = window
        self._original_title = title
        self._kind = kind          # "vod" (movie) or "series" (tv)
        self._on_pick = on_pick
        self._results: list[dict] = []
        self._token = 0

        self.setWindowTitle(tr("tmdb_match_title"))
        self.resize(560, 620)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 14)
        outer.setSpacing(10)

        clean, year = PosterResolver.clean_title(title)
        info = QLabel(tr("tmdb_match_hint"))
        info.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        info.setWordWrap(True)
        outer.addWidget(info)

        # Search field + year + button.
        row = QHBoxLayout()
        self.search_edit = QLineEdit(clean or title)
        self.search_edit.setPlaceholderText(tr("tmdb_search_placeholder"))
        self.search_edit.returnPressed.connect(self._do_search)
        row.addWidget(self.search_edit, 1)
        self.year_edit = QLineEdit(str(year) if year else "")
        self.year_edit.setPlaceholderText(tr("tmdb_year_placeholder"))
        self.year_edit.setFixedWidth(70)
        self.year_edit.returnPressed.connect(self._do_search)
        row.addWidget(self.year_edit)
        search_btn = QPushButton(tr("tmdb_search_btn"), objectName="Primary")
        search_btn.clicked.connect(self._do_search)
        row.addWidget(search_btn)
        outer.addLayout(row)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        # Result list with poster thumbnails.
        self.result_list = QListWidget()
        self.result_list.setIconSize(QSize(64, 96))
        self.result_list.setSpacing(2)
        self.result_list.itemDoubleClicked.connect(lambda _i: self._apply())
        outer.addWidget(self.result_list, 1)

        # Buttons row.
        bb = QDialogButtonBox()
        self.use_btn = bb.addButton(
            tr("tmdb_use_this"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.use_btn.setObjectName("Primary")
        self.use_btn.setEnabled(False)
        self.use_btn.clicked.connect(self._apply)
        self.clear_btn = bb.addButton(
            tr("tmdb_clear_override"), QDialogButtonBox.ButtonRole.ResetRole)
        self.clear_btn.clicked.connect(self._clear)
        bb.addButton(tr("common_close"),
                     QDialogButtonBox.ButtonRole.RejectRole)
        for b in bb.buttons():
            b.setIcon(QIcon())
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self.result_list.currentItemChanged.connect(
            lambda *_: self.use_btn.setEnabled(
                self.result_list.currentItem() is not None))

        # Kick off the initial search so the user sees candidates instantly.
        self._do_search()

    # -- search flow --------------------------------------------------------

    def _do_search(self) -> None:
        q = self.search_edit.text().strip()
        if not q:
            self.status.setText(tr("tmdb_enter_title"))
            return
        year_txt = self.year_edit.text().strip()
        year = None
        if year_txt.isdigit():
            year = int(year_txt)
        self.status.setText(tr("tmdb_searching"))
        self.result_list.clear()
        self._results = []
        self._token += 1
        token = self._token
        kind = self._kind

        def fetch():
            return self.window.tmdb.client.search(q, kind, year)

        def done(results, token=token):
            if token != self._token:
                return
            self._on_results(results or [])

        def fail(msg, token=token):
            if token != self._token:
                return
            self.status.setText(tr("tmdb_search_failed", msg=msg))

        run_async(self.window.pool, fetch, done, fail)

    def _on_results(self, results: list[dict]) -> None:
        self._results = results
        if not results:
            self.status.setText(tr("tmdb_no_results"))
            return
        self.status.setText(tr("tmdb_n_matches", n=len(results)))
        for r in results:
            year = f" ({r['year']})" if r.get("year") else ""
            overview = (r.get("overview") or "").strip()
            if len(overview) > 120:
                overview = overview[:117] + "…"
            label = f"{r['title']}{year}"
            if overview:
                label += "\n" + overview
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, r)
            self.result_list.addItem(item)
            poster_url = r.get("poster_url")
            if poster_url:
                self._load_poster(poster_url, item)
        self.result_list.setCurrentRow(0)

    def _load_poster(self, url: str, item: QListWidgetItem) -> None:
        # Reuse the poster-art async loader that the detail panel uses so
        # the same cache serves both flows.
        def on_ready(pm: QPixmap, item=item):
            try:
                item.setIcon(QIcon(pm))
            except RuntimeError:
                pass
        self.window.poster_art.get(url, on_ready)

    # -- pick / clear -------------------------------------------------------

    def _apply(self) -> None:
        item = self.result_list.currentItem()
        if not item:
            return
        picked = item.data(Qt.ItemDataRole.UserRole)
        if not picked:
            return
        # Delegate to the resolver so the manual pick is written to the
        # persistent cache and any listeners on the detail panel refresh.
        # Pass the search-result fields as a preview so the poster
        # appears immediately even if the follow-up details call
        # fails - the user already saw this poster in the dialog and
        # it should just work.
        self.window.tmdb.set_manual_match(
            self._original_title, self._kind, picked["tmdb_id"],
            lambda d: self._on_pick(d),
            preview=picked)
        self.accept()

    def _clear(self) -> None:
        self.window.tmdb.clear_manual_match(self._original_title, self._kind)
        # Re-trigger the auto-search flow for the caller so the detail
        # panel shows the (now fresh) auto-match again.
        self._on_pick({})
        self.accept()
