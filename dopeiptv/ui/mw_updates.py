"""Update-check mixin for MainWindow.

The once-a-day background "is there a newer release?" check and the badge/status
wiring it drives. Split out of main_window.py; every method operates on
MainWindow state (self.settings, self._sidebar_logo, self.update_status_btn,
...) through the mixin, so behaviour is identical.
"""
from __future__ import annotations

import os
import time

from PyQt6.QtCore import QTimer

from .. import VERSION
from ..core.updates import GITHUB_REPO, fetch_latest_release, is_newer
from ..core.workers import run_async
from ..i18n import tr


class _UpdatesMixin:
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
