"""Donating, from inside the app - there, never in the way.

Three places: a heart in the sidebar's row of buttons, "Support dopeIPTV"
in About, and one card on Home once the app has been used for real (about
ten hours of watching). The card's "Not now" puts it away for three
months; "Donate" for a year. Nothing ever pops up, and nothing shows on a
first run or over a playing video. Every way opens the donate dialog on
the website, with a ?ref= saying which (the site's statistics read it).
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QTimer

from ..core.xdg import open_url
from ..i18n import tr

DONATE_URL = "https://iptv.dope.rs/?ref={ref}#donate"
#: Minutes of watching before Home shows the card.
CARD_AFTER_MIN = 600
SNOOZE_SECS = 90 * 86400          # "Not now"
THANKS_SECS = 365 * 86400         # "Donate": not asked again for a year
#: Minutes counted in memory before they are written (the settings file is
#: not rewritten every minute of a film).
_WRITE_EVERY = 5


class _DonateMixin:
    def _init_donate(self) -> None:
        self._donate_unsaved = 0
        self._donate_tick = QTimer(self)
        self._donate_tick.setInterval(60_000)
        self._donate_tick.timeout.connect(self._donate_count)
        self._donate_tick.start()

    def _donate_count(self) -> None:
        """A minute of watching, by the same sign that keeps the screen
        awake while something plays (the docked player or multiview)."""
        wake = getattr(self, "wake", None)
        if wake is None or not wake.held:
            return
        self._donate_unsaved += 1
        if self._donate_unsaved >= _WRITE_EVERY:
            self.settings.setValue("donate_watch_min",
                                   self._donate_watched() + self._donate_unsaved)
            self._donate_unsaved = 0

    def _donate_watched(self) -> int:
        try:
            return int(self.settings.value("donate_watch_min", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def open_donate(self, ref: str) -> None:
        open_url(DONATE_URL.format(ref=ref))

    def _donate_card_due(self) -> bool:
        if self._donate_watched() + getattr(self, "_donate_unsaved", 0) \
                < CARD_AFTER_MIN:
            return False
        try:
            until = float(self.settings.value("donate_card_until", 0) or 0)
        except (TypeError, ValueError):
            until = 0.0
        return time.time() >= until

    def _donate_card(self):
        """The Home card, or None while it is not due."""
        if not self._donate_card_due():
            return None
        from .home_widgets import DonateCard
        card = DonateCard(tr("home_donate_title"), tr("home_donate_body"),
                          tr("home_donate_btn"), tr("home_donate_later"))

        def away(secs: int) -> None:
            self.settings.setValue("donate_card_until", time.time() + secs)
            card.hide()
            card.deleteLater()

        def donate() -> None:
            self.open_donate("app-home")
            away(THANKS_SECS)

        card.donate.connect(donate)
        card.later.connect(lambda: away(SNOOZE_SECS))
        return card
