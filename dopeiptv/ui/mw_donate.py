"""Donating, from inside the app - there, never in the way.

Four places: a small heart beside the sidebar's logo (its words in the
tooltip) - and at the top right of Home, which hides the sidebar - "Support dopeIPTV…" in the app menu and in About, and one card on
Home once the app has been used for real (about
ten hours of watching). The card's "Not now" puts it away for three
months; "Donate" for a year; left alone, it goes by itself after a week,
for three months too. "I've already donated" in the heart's menu puts the
heart and the card away for good (the app cannot know: no accounts, the
payment is on the website), until the Settings switch turns it on. The heart hides for three months too - its
right-click menu, or a switch in Settings, General - and comes back by
itself. Nothing ever pops up, and nothing shows on a first run or over a
playing video. Every way opens the donate dialog on
the website, with a ?ref= saying which (the site's statistics read it).
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QTimer

from ..core.log import log
from ..core.xdg import open_url
from ..i18n import tr
from .theme import P

# #support: the donate band, not the payment form - an invitation, not
# a bill (#donate opens the form, for links from people already decided).
DONATE_URL = "https://dopeiptv.org/?ref={ref}#support"
#: Minutes of watching before Home shows the card.
CARD_AFTER_MIN = 600
SNOOZE_SECS = 90 * 86400          # "Not now"
THANKS_SECS = 365 * 86400         # "Donate": not asked again for a year
SHOWN_SECS = 7 * 86400            # left alone this long, the card goes by itself
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

    # -- the sidebar's heart ---------------------------------------------------
    def _donate_thanked(self) -> bool:
        return str(self.settings.value("donate_thanked", "false")) == "true"

    def _supporter(self):
        from ..core import supporter
        return supporter.stored(self.settings)

    def _supporter_now(self) -> bool:
        try:
            return self._supporter() is not None
        except Exception:                      # no settings yet
            return False

    def _donate_heart_due(self) -> bool:
        if self._donate_thanked() and self._supporter() is None:
            return False
        try:
            until = float(self.settings.value("donate_heart_until", 0) or 0)
        except (TypeError, ValueError):
            until = 0.0
        return time.time() >= until

    def _build_heart_badge(self) -> None:
        """The donate heart, laid over the logo like the private marker
        (mw_privacy) - in no layout, so the logo stays centred and nothing
        beside it moves: on the mark's lower right corner, on a ring of the
        sidebar's colour. A supporter has no heart: the logo itself is their
        mark (widgets._SidebarLogo.set_supporter)."""
        from .mw_privacy import _Follow
        logo = getattr(self, "_sidebar_logo", None)
        btn = getattr(self, "_donate_btn", None)
        if logo is None or btn is None:
            return
        host = logo.parentWidget()
        btn.setParent(host)
        self._heart_chip()
        follow = _Follow(btn, self._place_logo_marks)
        logo.installEventFilter(follow)
        host.installEventFilter(follow)

    def _heart_chip(self) -> None:
        from PyQt6.QtGui import QColor
        btn = getattr(self, "_donate_btn", None)
        if btn is not None and hasattr(btn, "set_chip"):
            btn.set_chip(QColor(P["side"]), QColor(P["side"]), QColor(P["border"]))

    def _place_logo_marks(self) -> None:
        """The heart on the mark's lower right corner."""
        logo = getattr(self, "_sidebar_logo", None)
        btn = getattr(self, "_donate_btn", None)
        if logo is None or btn is None or not btn.isVisible():
            return
        g = logo.geometry()
        w, h = logo.LOGO_W, logo.LOGO_H
        x0 = g.left() + (g.width() - w) // 2
        y0 = g.top() + (g.height() - h) // 2
        btn.move(x0 + w - btn.width() // 2 - 3, y0 + h - btn.height() // 2 - 1)
        btn.raise_()

    def _donate_heart_refresh(self) -> None:
        btn = getattr(self, "_donate_btn", None)
        if btn is None:
            return
        from ..core import privacy
        sup = self._supporter()
        # A supporter's heart is gone: the logo itself says it instead.
        # While private viewing is on its marker has the logo to itself.
        due = self._donate_heart_due() and sup is None
        shown = due and not privacy.private()
        tip = tr("donate_support") + "\n" + tr("heart_tip_key")
        for heart in (btn, getattr(self, "_home_heart", None)):
            try:
                if heart is not None:
                    heart.setToolTip(tip)
            except RuntimeError:
                pass
        btn.setVisible(shown)
        from .qt_i18n import format_month
        logo = getattr(self, "_sidebar_logo", None)
        if logo is not None and hasattr(logo, "set_supporter"):
            logo.set_supporter(sup is not None, tr("supporter_tip", date=format_month(sup["since"]))
                               if sup is not None else "")
        self._place_logo_marks()
        home = getattr(self, "_home_heart", None)      # Home's, top right
        if home is not None:
            try:
                home.setVisible(due)
            except RuntimeError:                       # Home was rebuilt
                self._home_heart = None
        self._supporter_perks(sup)

    # -- what the key opens ----------------------------------------------------
    def _supporter_perks(self, sup) -> None:
        """What a supporter key opens, kept in step with it - here, after
        the key's window, and once a minute: the supporter's app icon
        (unless switched off in Settings) and the supporter themes. With
        the key gone, a supporter theme in use gives way to the default,
        quietly. The first call, while the window is being built, only
        corrects the stored choice: the start already showed the default
        (theme.effective_theme)."""
        from .theme import THEMES, drop_locked_themes, effective_theme
        first = not hasattr(self, "_perks_seen")
        self._perks_seen = True
        if sup is None and drop_locked_themes(self.settings):
            log.info("supporter theme: no supporter key - back to the default")
            if not first and P.get("name") != THEMES[effective_theme(self.settings)]["name"]:
                self._apply_theme_now()
        from . import app_icon
        try:
            app_icon.show(sup is not None and app_icon.switched_on(self.settings))
        except Exception as e:
            log.warning("supporter icon: %s", e)

    def _app_icon_refresh(self) -> None:
        """The app's icon after the Settings switch: the supporter's or the
        plain one, live."""
        from . import app_icon
        try:
            app_icon.show(self._supporter() is not None
                          and app_icon.switched_on(self.settings))
        except Exception as e:
            log.warning("supporter icon: %s", e)

    def donated(self) -> None:
        """Said they have: the heart and Home's card go for good."""
        self.settings.setValue("donate_thanked", "true")
        self._donate_heart_refresh()

    def show_donate_heart(self) -> None:
        self.settings.remove("donate_thanked")
        self.settings.setValue("donate_heart_until", 0)
        self._donate_heart_refresh()

    def hide_donate_heart(self) -> None:
        self.settings.setValue("donate_heart_until", time.time() + SNOOZE_SECS)
        self._donate_heart_refresh()

    def _donate_heart_menu(self, pos, btn=None) -> None:
        from PyQt6.QtWidgets import QMenu
        btn = btn or self._donate_btn
        menu = QMenu(btn)
        menu.addAction(tr("donate_support"), lambda: self.open_donate("app-heart"))
        menu.addAction(tr("supporter_menu"), self.open_supporter)
        menu.addAction(tr("donate_hide"), self.hide_donate_heart)
        if self._supporter() is None:
            menu.addAction(tr("donate_done"), self.already_donated)
        menu.exec(btn.mapToGlobal(pos))

    def already_donated(self) -> None:
        """"I've already donated": the key's window first - with a key the
        app knows for sure, and the heart says thank you; closed without one,
        taken at their word as before."""
        self.open_supporter()
        if self._supporter() is None:
            self.donated()

    def heart_clicked(self, ref: str = "app-heart") -> None:
        """The heart: the donate page, or for a supporter their thank-you
        (from which they can still donate again)."""
        if self._supporter() is not None:
            self.open_supporter()
        else:
            self.open_donate(ref)

    def open_supporter(self, key: str = "") -> None:
        """The supporter window; *key* (from a dopeiptv://supporter link)
        filled in, so the person sees it checked and kept."""
        from .dialogs import SupporterDialog
        d = SupporterDialog(self, self.settings, donate=lambda: self.open_donate("supporter"))
        if key:
            d.key.setText(key)
        d.exec()
        self._donate_heart_refresh()

    def _donate_count(self) -> None:
        """A minute of watching, by the same sign that keeps the screen
        awake while something plays (the docked player or multiview)."""
        if not getattr(self, "_donate_btn", None) or not self._donate_btn.isVisible():
            self._donate_heart_refresh()        # its three months may be up
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
        # A supporter is not asked (they can still donate: the menu, About,
        # their heart's thank-you).
        if self._donate_thanked() or self._supporter() is not None:
            return False
        if self._donate_watched() + getattr(self, "_donate_unsaved", 0) \
                < CARD_AFTER_MIN:
            return False
        try:
            until = float(self.settings.value("donate_card_until", 0) or 0)
            shown = float(self.settings.value("donate_card_shown", 0) or 0)
        except (TypeError, ValueError):
            until = shown = 0.0
        if time.time() < until:
            return False
        if shown and time.time() - shown > SHOWN_SECS:
            # A week on Home and neither button pressed: it has been seen.
            self.settings.setValue("donate_card_until", time.time() + SNOOZE_SECS)
            self.settings.remove("donate_card_shown")
            return False
        return True

    def _donate_card(self):
        """The Home card, or None while it is not due."""
        if not self._donate_card_due():
            return None
        from .home_widgets import DonateCard
        if not self.settings.value("donate_card_shown"):
            self.settings.setValue("donate_card_shown", time.time())
        card = DonateCard(tr("home_donate_title"), tr("home_donate_body"),
                          tr("home_donate_btn"), tr("home_donate_later"),
                          tr("donate_done"))

        def away(secs: int) -> None:
            self.settings.setValue("donate_card_until", time.time() + secs)
            self.settings.remove("donate_card_shown")
            card.hide()
            card.deleteLater()

        def donate() -> None:
            self.open_donate("app-home")
            away(THANKS_SECS)

        card.donate.connect(donate)
        card.later.connect(lambda: away(SNOOZE_SECS))

        def already() -> None:
            self.already_donated()
            card.hide()
            card.deleteLater()

        card.already.connect(already)
        return card
