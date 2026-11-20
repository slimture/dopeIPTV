"""About: the app's name and version, the update check with the release
notes cut to what is new to the one reading them, the links, the
supporter's line, TMDB's credit and who makes it.

``show_about(win)`` opens it over the window (Help > About) or over
Settings (General's About row); seven taps on the version turn Nerd on.
The window's mixin keeps ``show_about`` and ``_format_release_notes``
(ui/mw_settings.py), which hand on to these.
"""

from __future__ import annotations

import re
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
    QVBoxLayout, QWidget,
)

from .. import APP_NAME, BUILD_VERSION, VERSION
from ..core.updates import GITHUB_REPO, fetch_update, offered
from ..core.workers import run_async
from ..core.xdg import open_url
from ..i18n import tr
from .theme import P


def format_release_notes(body: str, current: str = "",
                         update: bool = False) -> str:
    """Clean a GitHub release body for QTextBrowser's markdown renderer,
    keeping only what is new to the one reading it.

    The body carries every version back to the last big release, for
    someone who skips several on GitHub; the small box in About showed
    all of it. Up to date (*update* False), only *current*'s own notes
    are kept; with an update waiting, the versions after *current*. One
    version left, its '## dopeIPTV x.y.z' title goes too - the status
    line names it. GitHub's <details>/<summary> wrappers are stripped
    (Qt's markdown swallowed the headings inside them), and so is "On
    the AppImage ...? Click Update now": About has its own button."""
    from ..core.updates import is_newer, same_release
    body = re.sub(r"(?is)<summary>.*?</summary>", "", body)
    body = re.sub(r"(?i)</?details>", "", body)
    parts = re.split(r"(?im)^[ \t]*##[ \t]+dopeIPTV[ \t]+(\S+)[^\n]*$", body)
    sections = list(zip(parts[1::2], parts[2::2], strict=False))
    if sections:
        if current and update:
            keep = [s for s in sections if is_newer(s[0], current)]
        elif current:
            keep = [s for s in sections if same_release(s[0], current)]
        else:
            keep = []
        keep = keep or sections[:1]
        if len(keep) == 1:
            body = keep[0][1]
        else:
            body = "\n\n".join(f"## dopeIPTV {v}\n{text}" for v, text in keep)
    body = re.sub(r"(?ims)^On the AppImage from .*?(?=\n\s*\n|\Z)", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    return body.strip()


def show_about(win: Any, *, parent: QWidget | None = None) -> None:
    """About and the update check; over *parent* when given (Settings),
    else over the window. Keyword-only: a signal's argument never
    lands in it."""
    d = QDialog(parent or win)
    d.setWindowTitle(tr("menu_about"))   # "About dopeIPTV": named already
    # Wide enough for the release notes from the start: see notes below.
    d.setMinimumWidth(520)
    lay = QVBoxLayout(d)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(12)

    # Small, flat text-link buttons so the dialog reads as info, not a
    # wall of chunky buttons.
    link_css = (
        "QPushButton { border:none; background:transparent; padding:2px 0;"
        " font-size:12px; text-align:left; color:%s; }"
        "QPushButton:hover { color:%s; text-decoration:underline; }"
    ) % (P["muted"], P["text"])

    def link(text, url):
        b = QPushButton(text)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(link_css)
        b.clicked.connect(lambda: open_url(url))
        return b

    title = QLabel(
        f"<b style='font-size:18px'>{APP_NAME}</b>"
        f"&nbsp;&nbsp;<span style='color:{P['muted2']}'>{BUILD_VERSION}</span>")
    lay.addWidget(title)
    # Seven taps on the version, as Android's developer options: Nerd.
    taps: list[float] = []

    def tap(_event) -> None:
        import time as _time
        now = _time.monotonic()
        taps[:] = [t for t in taps if now - t < 3.0] + [now]
        if len(taps) >= 7:
            taps.clear()
            win._unlock_nerd(in_settings=parent is not None)

    title.mousePressEvent = tap
    desc = QLabel(tr("about_desc"))
    desc.setWordWrap(True)
    lay.addWidget(desc)
    # Not the official app: a line says so (core/build_check.py); a
    # modified build's online services are off, its update check too.
    from ..core import build_check as _build
    build = _build.kind()
    if build != _build.OFFICIAL:
        modified = build == _build.MODIFIED
        line = QLabel(tr("about_build_modified" if modified else "about_build_source"))
        line.setObjectName("AboutBuild")
        line.setWordWrap(True)
        line.setStyleSheet(f"color:{P['warn'] if modified else P['muted']}; font-size:12px;"
                           + (" font-weight:600;" if modified else ""))
        lay.addWidget(line)

    # -- update section: one clear status line, with an inline re-check --
    status = QLabel(tr("about_checking"))
    status.setWordWrap(True)
    status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
    lay.addWidget(status)
    notes = QTextBrowser()
    notes.setOpenExternalLinks(True)
    # Roomier + a sensible minimum so the release notes are actually
    # readable when an update is offered (it scrolls past the max).
    notes.setMinimumHeight(140)
    notes.setMaximumHeight(340)
    notes.setStyleSheet(
        f"QTextBrowser {{ background:{P['input']}; color:{P['text']};"
        f" border:1px solid {P['border']};"
        f" border-radius:8px; padding:4px 8px; font-size:12px; }}")
    # Its room is kept while the check runs, so the notes never have to
    # make the dialog grow once it is up: on Wayland a dialog can stay at
    # its first size, and the notes then lay over the buttons, the links
    # and the TMDB line below them.
    keep = notes.sizePolicy()
    keep.setRetainSizeWhenHidden(True)
    notes.setSizePolicy(keep)
    notes.hide()
    lay.addWidget(notes)

    act_row = QHBoxLayout()
    act_row.setSpacing(8)
    dl_btn = QPushButton(tr("about_download"))
    dl_btn.setStyleSheet(
        "QPushButton { padding:4px 12px; font-size:12px; }")
    dl_btn.hide()
    recheck = QPushButton(tr("about_check_updates"))
    recheck.setStyleSheet(link_css)
    recheck.setCursor(Qt.CursorShape.PointingHandCursor)
    act_row.addWidget(dl_btn)
    act_row.addWidget(recheck)
    act_row.addStretch(1)
    lay.addLayout(act_row)

    # -- links + attribution ------------------------------------------
    links = QHBoxLayout()
    links.setSpacing(16)
    # ?ref=: from the app, for the site's statistics (see mw_updates).
    links.addWidget(link(tr("about_website"),
                         "https://dopeiptv.org/?ref=app-about"))
    links.addWidget(link(tr("about_github"),
                         f"https://github.com/{GITHUB_REPO}"))
    links.addWidget(link(tr("about_all_releases"),
                         f"https://github.com/{GITHUB_REPO}/releases"))
    links.addStretch(1)
    # Donating, last and in the heart's own colour (mw_donate).
    from .theme import HEART
    from .widgets import heart_pixmap
    give = link(tr("donate_support"), "")
    give.clicked.disconnect()
    give.clicked.connect(lambda: win.open_donate("app-about"))
    give.setIcon(QIcon(heart_pixmap(13, HEART)))
    give.setStyleSheet(link_css.replace(P["muted"], HEART, 1))
    links.addWidget(give)
    lay.addLayout(links)
    # A supporter's thank-you, and where a key goes (mw_donate).
    from ..core import supporter as _supporter
    from .qt_i18n import format_month
    mine = _supporter.stored(getattr(win, "settings", None))
    sup_row = QHBoxLayout()
    if mine is not None:
        mark = QLabel(f"<span style='color:{HEART}'>\u2665</span>&nbsp; "
                      + tr("supporter_tip", date=format_month(mine["since"])))
        mark.setStyleSheet(f"color:{P['text2']}; font-size:12px; font-weight:600;")
        sup_row.addWidget(mark)
    if hasattr(win, "open_supporter"):
        key_link = link(tr("supporter_menu") + "…", "")
        key_link.clicked.disconnect()
        key_link.clicked.connect(lambda: (d.accept(), win.open_supporter()))
        sup_row.addWidget(key_link)
    sup_row.addStretch(1)
    lay.addLayout(sup_row)

    credit = QLabel(tr("about_tmdb_credit"))
    credit.setWordWrap(True)
    credit.setOpenExternalLinks(True)
    credit.setStyleSheet(f"color:{P['muted2']}; font-size:11px;")
    # TMDB's logo beside its credit, as its attribution rules ask.
    from .widgets import TMDB_URL, TmdbLink
    tmdb = TmdbLink()
    tmdb.clicked.connect(lambda: open_url(TMDB_URL))
    credit_row = QHBoxLayout()
    credit_row.setSpacing(10)
    credit_row.addWidget(tmdb, 0, Qt.AlignmentFlag.AlignTop)
    credit_row.addWidget(credit, 1)
    lay.addLayout(credit_row)

    # Who makes it, beside Close: the Solecraft logotype, quiet like the
    # links above (the website's footer says it the same way).
    from .widgets import SolecraftLink
    foot = QHBoxLayout()
    foot.setSpacing(6)
    made = QLabel(tr("about_made_by"))
    made.setStyleSheet(f"color:{P['muted2']}; font-size:12px;")
    foot.addWidget(made)
    maker = SolecraftLink("https://solecraft.se", P["muted"], P["text"])
    maker.clicked.connect(lambda: open_url("https://solecraft.se"))
    foot.addWidget(maker)
    foot.addStretch(1)
    btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    btns.rejected.connect(d.accept)
    btns.accepted.connect(d.accept)
    foot.addWidget(btns)
    lay.addLayout(foot)

    def done(rel) -> None:
        # Opened over Settings, it goes when that window is deleted.
        if sip.isdeleted(d):
            return
        recheck.setEnabled(True)
        if not rel or not rel.get("tag"):
            status.setText(tr("about_check_failed"))
            return
        win._remember_update(rel)
        if offered(rel["tag"], VERSION, win._update_channel()):
            status.setText(tr("about_update_available", version=rel["tag"]))
            status.setStyleSheet(
                f"color:{P['text']}; font-size:12px; font-weight:600;")
            if rel.get("body"):
                notes.setMarkdown(win._format_release_notes(
                    rel["body"], VERSION, update=True))
                notes.show()
            if rel.get("url"):
                try:
                    dl_btn.clicked.disconnect()
                except TypeError:
                    pass
                dl_btn.setText(win._update_action_label())
                dl_btn.clicked.connect(
                    lambda: (d.accept(), win._update_action()))
                dl_btn.show()
        else:
            status.setText("✓ " + tr("about_up_to_date"))
            # Up to date: the room is there anyway, and the newest
            # release's notes are what this version brought.
            if rel.get("body"):
                notes.setMarkdown(win._format_release_notes(
                    rel["body"], VERSION))
                notes.show()
            else:
                notes.hide()
            dl_btn.hide()

    def fail(_e) -> None:
        if sip.isdeleted(d):
            return
        recheck.setEnabled(True)
        status.setText(tr("about_check_failed"))

    def check() -> None:
        recheck.setEnabled(False)
        status.setText(tr("about_checking"))
        status.setStyleSheet(f"color:{P['muted']}; font-size:12px;")
        channel = win._update_channel()
        run_async(win.pool,
                  lambda: fetch_update(GITHUB_REPO, channel), done, fail)

    recheck.clicked.connect(check)
    if build == _build.MODIFIED:
        # Nothing is asked; the line above says why - and no room is
        # kept for release notes that never come.
        status.hide()
        recheck.hide()
        keep.setRetainSizeWhenHidden(False)
        notes.setSizePolicy(keep)
    else:
        check()               # auto-check on open
    d.exec()
