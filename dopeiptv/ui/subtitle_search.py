"""Find subtitles on OpenSubtitles.com for what is playing, and load one.

Opened from the player's Subtitles menu (ui/mw_subtitles.py). The search
starts on its own with what is known about the title - its TMDB id, the
series' id with season and episode, the hash of a local file - and falls
back to its name; the text can be edited and searched again.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QLocale, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ..core.log import log
from ..core.workers import default_image_cache_dir, run_async
from ..i18n import tr
from ..providers.opensubtitles import LANGUAGES, movie_hash
from .theme import P

_KEY_URL = "https://www.opensubtitles.com/consumers"


def language_name(code: str) -> str:
    """A language in its own words ("svenska", "Deutsch") - read by the
    people who speak it, whichever language the app is in."""
    loc = QLocale(code.replace("-", "_"))
    if loc.language() == QLocale.Language.C:
        return code
    name = loc.nativeLanguageName() or code
    if "-" in code:
        name = f"{name} ({loc.nativeTerritoryName()})"
    return name[:1].upper() + name[1:]


def subtitle_dir() -> Path:
    return default_image_cache_dir("subtitles")


def cached_file(file_id: int) -> Path | None:
    """A subtitle downloaded before: loading it again costs no download."""
    try:
        return next(subtitle_dir().glob(f"*[[]{int(file_id)}[]].*"), None)
    except OSError:
        return None


def save_subtitle(row: dict, name: str, data: bytes) -> Path:
    """Write a fetched subtitle where it can be found again. Named after
    the release and the language, so the player's track list reads well
    and mpv takes the language from the name."""
    ext = (name.rsplit(".", 1)[-1] if "." in name else "srt").lower()
    if not re.fullmatch(r"[a-z0-9]{2,4}", ext):
        ext = "srt"
    stem = re.sub(r"[^\w .()-]+", "_", row.get("release") or "subtitle")
    stem = stem.strip(" ._")[:80] or "subtitle"
    lang = re.sub(r"[^A-Za-z-]", "", row.get("language") or "") or "und"
    folder = subtitle_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{stem} [{int(row['file_id'])}].{lang}.{ext}"
    path.write_bytes(data)
    return path


class SubtitleSearchDialog(QDialog):
    """The search, its results, and loading the one picked."""

    def __init__(self, window, ctx: dict[str, Any], language: str) -> None:
        super().__init__(window)
        self.window_ = window
        self.ctx = ctx
        self._gen = 0
        self._busy = False
        self.setWindowTitle(tr("subs_title"))
        self.resize(720, 460)
        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        top = QHBoxLayout()
        self.query = QLineEdit(ctx.get("query") or "")
        self.query.returnPressed.connect(self._search)
        top.addWidget(self.query, 1)
        self.lang = QComboBox()
        for code in sorted(LANGUAGES, key=lambda c: language_name(c).lower()):
            self.lang.addItem(language_name(code), code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(language)))
        self.lang.currentIndexChanged.connect(lambda _i: self._search())
        top.addWidget(self.lang)
        self.search_btn = QPushButton(tr("tmdb_search_btn"))
        self.search_btn.clicked.connect(self._search)
        top.addWidget(self.search_btn)
        lay.addLayout(top)

        self.tree = QTreeWidget()
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.setHeaderLabels([tr("setting_language"),
                                   tr("subs_col_subtitle"),
                                   tr("subs_col_downloads")])
        head = self.tree.header()
        head.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        head.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemDoubleClicked.connect(lambda *_a: self._use())
        self.tree.currentItemChanged.connect(lambda *_a: self._sync())
        lay.addWidget(self.tree, 1)

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setOpenExternalLinks(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.status)

        bottom = QHBoxLayout()
        credit = QLabel(tr("subs_credit"))
        credit.setStyleSheet(f"color:{P['muted']}; font-size:11px;")
        bottom.addWidget(credit, 1)
        self.settings_btn = QPushButton(tr("btn_settings"))
        self.settings_btn.clicked.connect(self._open_settings)
        bottom.addWidget(self.settings_btn)
        self.use_btn = QPushButton(tr("btn_use"), objectName="Primary")
        self.use_btn.clicked.connect(self._use)
        bottom.addWidget(self.use_btn)
        close = QPushButton(tr("common_close"))
        close.clicked.connect(self.reject)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self._sync()
        self._search()

    # -- state ------------------------------------------------------------------

    def _say(self, text: str, error: bool = False, rich: bool = False) -> None:
        """Rich text only for our own link: an error is the server's words,
        and a "<" in them is a character, not markup."""
        color = P["error"] if error else P["muted"]
        self.status.setStyleSheet(f"color:{color};")
        self.status.setText(text if rich else html.escape(text))

    def _sync(self) -> None:
        has_key = self.window_._subtitle_client() is not None
        self.settings_btn.setVisible(not has_key)
        self.search_btn.setEnabled(has_key and not self._busy)
        self.use_btn.setEnabled(has_key and not self._busy
                                and self.tree.currentItem() is not None)

    def _alive(self, gen: int) -> bool:
        return gen == self._gen and self.isVisible()

    def _open_settings(self) -> None:
        # Straight to the page with the key on it.
        self.window_._settings_page = "audio"
        self.window_.open_settings()
        self._sync()
        if self.window_._subtitle_client() is not None:
            self._search()

    # -- searching ----------------------------------------------------------------

    def _search(self) -> None:
        client = self.window_._subtitle_client()
        if client is None:
            link = f'<a href="{_KEY_URL}">opensubtitles.com/consumers</a>'
            self._say(tr("subs_need_key", url=link), rich=True)
            self._sync()
            return
        query = self.query.text().strip()
        ctx = dict(self.ctx)
        if query != (ctx.get("query") or ""):
            # Other words typed: the ids were for the title as it was.
            ctx["tmdb_id"] = ctx["parent_tmdb_id"] = None
        if not query and not (ctx.get("tmdb_id") or ctx.get("parent_tmdb_id")
                              or ctx.get("path")):
            return
        lang = self.lang.currentData() or ""
        self._gen += 1
        gen = self._gen
        self._busy = True
        self._sync()
        self.tree.clear()
        self._say(tr("tmdb_searching"))

        def work() -> list[dict]:
            mh = movie_hash(ctx["path"]) if ctx.get("path") else None
            rows = client.search(
                languages=lang, query=query, tmdb_id=ctx.get("tmdb_id"),
                parent_tmdb_id=ctx.get("parent_tmdb_id"),
                season=ctx.get("season"), episode=ctx.get("episode"),
                moviehash=mh)
            if not rows and query and (mh or ctx.get("tmdb_id")
                                       or ctx.get("parent_tmdb_id")):
                # Nothing under the ids (or for this exact file): the name
                # still finds what was filed loosely.
                rows = client.search(languages=lang, query=query)
            return rows

        def done(rows: list[dict]) -> None:
            if not self._alive(gen):
                return
            self._busy = False
            self._fill(rows)
            self._sync()

        def failed(msg: str) -> None:
            if not self._alive(gen):
                return
            self._busy = False
            self._say(msg, error=True)
            self._sync()

        run_async(self.window_.pool, work, done, failed)

    def _fill(self, rows: list[dict]) -> None:
        self.tree.clear()
        for row in rows:
            flags = []
            if row["hash_match"]:
                flags.append(tr("subs_flag_match"))
            if row["trusted"]:
                flags.append(tr("subs_flag_trusted"))
            if row["hearing_impaired"]:
                flags.append(tr("subs_flag_hi"))
            if row["machine"]:
                flags.append(tr("subs_flag_machine"))
            name = row["release"] + (" ✓" if row["hash_match"] else "")
            if row["hearing_impaired"]:
                name += "  [HI]"
            it = QTreeWidgetItem([language_name(row["language"]), name,
                                  QLocale().toString(row["downloads"])])
            it.setData(0, Qt.ItemDataRole.UserRole, row)
            it.setTextAlignment(2, Qt.AlignmentFlag.AlignRight
                                | Qt.AlignmentFlag.AlignVCenter)
            if flags:
                it.setToolTip(1, "\n".join(flags))
            self.tree.addTopLevelItem(it)
        if rows:
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
            self._say(tr("subs_found", n=len(rows)))
        else:
            self._say(tr("subs_none"))

    # -- loading the pick ---------------------------------------------------------

    def _use(self) -> None:
        item = self.tree.currentItem()
        client = self.window_._subtitle_client()
        if item is None or client is None or self._busy:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        self._gen += 1
        gen = self._gen
        self._busy = True
        self._sync()
        self._say(tr("subs_downloading"))

        def work() -> tuple[str, int | None]:
            have = cached_file(row["file_id"])
            if have is not None:
                return str(have), None
            name, data = client.download(row["file_id"])
            return str(save_subtitle(row, name, data)), client.remaining

        def done(res: tuple[str, int | None]) -> None:
            path, remaining = res
            if not self._alive(gen):
                return
            self._busy = False
            player = self.window_.player
            if player is None or not player.add_subtitle(
                    path, row["release"], row["language"]):
                self._say(tr("subs_err_bad_file"), error=True)
                self._sync()
                return
            text = tr("subs_loaded", name=language_name(row["language"]))
            if remaining is not None:
                text += " — " + tr("subs_remaining", n=remaining)
            log.info("subtitles: loaded %s (%s left today)", path, remaining)
            self.window_._show_toast(text, 5000)
            self.accept()

        def failed(msg: str) -> None:
            if not self._alive(gen):
                return
            self._busy = False
            self._say(msg, error=True)
            self._sync()

        run_async(self.window_.pool, work, done, failed)
