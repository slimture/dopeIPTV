"""Update-check mixin for MainWindow.

The once-a-day background "is there a newer release?" check and the badge/status
wiring it drives. Split out of main_window.py; every method operates on
MainWindow state (self.settings, self._sidebar_logo, self.update_status_btn,
...) through the mixin, so behaviour is identical.
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton

from .. import VERSION
from ..core.updates import GITHUB_REPO, fetch_latest_release, is_newer
from ..core.workers import run_async
from ..i18n import tr
from .theme import P

# Where "Download" sends people - the website, not the raw GitHub release.
_WEBSITE = "https://iptv.dope.rs"


class _UpdatesMixin:
    """MainWindow mixin: the startup update check and its status-row link."""
    def _maybe_check_updates(self) -> None:
        """Once-a-day background check for a newer release; on a hit, light the
        badge on the Settings button. Cached in QSettings so it doesn't hit
        GitHub every launch and works offline (fails silently). Opt-out via the
        'check for updates' setting."""
        if self.settings.value("check_updates", "true") != "true":
            return
        # Test hook: force the badge on without a network call, so the update
        # indicator can be seen even when you're already on the latest release.
        # Placed after the opt-out check so opting out still suppresses it.
        if os.environ.get("DOPEIPTV_FAKE_UPDATE") == "1":
            self._apply_update_state("v99.0.0")
            return
        try:
            last = float(self.settings.value("update_check_ts", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if time.time() - last < 86400:
            self._apply_update_state(
                self.settings.value("update_latest_tag", "") or "")
            return

        def done(rel):
            tag = (rel or {}).get("tag", "") or ""
            self.settings.setValue("update_check_ts", str(int(time.time())))
            self.settings.setValue("update_latest_tag", tag)
            self._apply_update_state(tag)

        run_async(self.pool, lambda: fetch_latest_release(GITHUB_REPO),
                  done, lambda _e: None)

    def _apply_cached_update(self) -> None:
        """Light the badge from the last cached result at startup so it appears
        right away, without waiting for (or making) a network call."""
        if self.settings.value("check_updates", "true") != "true":
            return
        if os.environ.get("DOPEIPTV_FAKE_UPDATE") == "1":
            self._apply_update_state("v99.0.0")
            return
        tag = self.settings.value("update_latest_tag", "") or ""
        if tag:
            self._apply_update_state(tag)

    def _apply_update_state(self, latest_tag: str) -> None:
        newer = bool(latest_tag) and is_newer(latest_tag, VERSION)
        logo = self._sidebar_logo
        if not newer:
            logo.set_update(False)
            logo.setToolTip(tr("tooltip_jump_playing"))
            self.update_status_btn.hide()
            return
        logo.setToolTip(tr("about_update_available", version=latest_tag))
        # The attention-grabbing part (status link, red badge, bounce,
        # red->accent settle) runs once per version, so the cached apply at
        # startup and the later network check for the same tag don't double up.
        if getattr(self, "_update_shown_tag", None) == latest_tag:
            return
        self._update_shown_tag = latest_tag
        # A clear, dismissible banner over the whole window (visible on Home
        # too, unlike the sidebar badge) - the main "a new version is out"
        # notice. The status-row link and logo badge stay as quiet reminders.
        self._show_update_banner(latest_tag)
        # Subtle, non-overlay link in the status row - shown for 30 s (as long
        # as the logo badge stays red) then hidden; the badge remains after.
        self.update_status_btn.setText(tr("update_status", version=latest_tag))
        self.update_status_btn.show()
        QTimer.singleShot(30_000, self.update_status_btn.hide)
        logo.set_update(True, "#E5484D")   # red first, to catch the eye
        logo.bounce()
        # After 30 s, settle from the attention-grabbing red to the theme
        # accent - in follow mode, so it keeps matching if the theme changes.
        QTimer.singleShot(30_000, lambda: logo.set_update(
            True, follow_accent=True))

    # -- "new version" banner (visible over any view, Home included) ---------

    def _show_update_banner(self, tag: str) -> None:
        """A dismissible accent banner across the top of the window announcing a
        new release, with a Download button (to the website). Overlays the
        central widget so it shows even on the full-window Home page. Once
        dismissed for a given version it stays gone for that version."""
        if self.settings.value("update_banner_dismissed", "") == tag:
            return
        parent = self.centralWidget()
        if parent is None:
            return
        banner = getattr(self, "_update_banner", None)
        if banner is None:
            banner = self._update_banner = QFrame(parent)
            banner.setObjectName("UpdateBanner")
            row = QHBoxLayout(banner)
            row.setContentsMargins(14, 8, 8, 8)
            row.setSpacing(12)
            self._update_banner_lbl = QLabel("")
            self._update_banner_lbl.setStyleSheet(
                "color:#FFFFFF; font-size:12px; font-weight:600;"
                " background:transparent;")
            dl = QPushButton(tr("about_download"))
            dl.setCursor(Qt.CursorShape.PointingHandCursor)
            dl.setStyleSheet(
                "QPushButton { background:rgba(255,255,255,0.18);"
                " color:#FFFFFF; border:none; border-radius:6px;"
                " padding:4px 12px; font-size:12px; font-weight:600; }"
                "QPushButton:hover { background:rgba(255,255,255,0.30); }")
            dl.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(_WEBSITE)))
            close = QPushButton("✕")   # ✕
            close.setCursor(Qt.CursorShape.PointingHandCursor)
            close.setFixedSize(24, 24)
            close.setStyleSheet(
                "QPushButton { background:transparent; color:#FFFFFF;"
                " border:none; font-size:13px; }"
                "QPushButton:hover { color:#111111; }")
            close.clicked.connect(self._dismiss_update_banner)
            row.addWidget(self._update_banner_lbl, 1)
            row.addWidget(dl)
            row.addWidget(close)
        banner.setStyleSheet(
            f"#UpdateBanner {{ background:{P['accent']}; border-radius:10px; }}")
        self._update_banner_lbl.setText(
            "\U0001F389 " + tr("about_update_available", version=tag))
        self._update_banner_tag = tag
        self._reposition_update_banner(force=True)
        banner.show()
        banner.raise_()
        # Fade it away on its own after a while so it doesn't linger forever;
        # the sidebar badge stays as the quiet reminder, and it returns next
        # launch unless the user dismissed it with the ✕.
        QTimer.singleShot(30_000, self._autohide_update_banner)

    def _autohide_update_banner(self) -> None:
        b = getattr(self, "_update_banner", None)
        if b is not None:
            b.hide()

    def _dismiss_update_banner(self) -> None:
        tag = getattr(self, "_update_banner_tag", "")
        if tag:
            self.settings.setValue("update_banner_dismissed", tag)
        b = getattr(self, "_update_banner", None)
        if b is not None:
            b.hide()

    def _reposition_update_banner(self, force: bool = False) -> None:
        """Keep the banner centred along the BOTTOM of the central widget on
        resize - the top would collide with Home's own menu row (back button +
        TV/Movies/Series pills)."""
        b = getattr(self, "_update_banner", None)
        parent = self.centralWidget()
        if b is None or parent is None or (not force and b.isHidden()):
            return
        w = max(300, min(560, parent.width() - 40))
        b.setFixedWidth(w)
        b.adjustSize()
        b.move((parent.width() - w) // 2,
               max(10, parent.height() - b.height() - 16))
        b.raise_()
