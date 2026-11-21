"""Support (a tip or monthly support), from inside the app - asked now and
then, never in the way.

"Support dopeIPTV…" is always in the app menu and in About. Beyond that
the app asks in one rhythm (core/donate_rhythm.py): one week a month, once
it is used for real - five hours watched in the last 30 days, two weeks
after the first run - a small heart on the sidebar's logo. The first day of
that week the heart has its colour; the other six days it is muted. Nothing
beats, pulses or pops up. Left alone it goes after its week and comes back
next month.

The heart (or its right-click menu) opens the support page on the website
(with a ?ref= saying from where - the site's statistics read it)
and ends the week; the right-click's "Hide for three months" puts it away
for three months; "I've supported" for a year, taken at the person's word. A
supporter key: never asked. Settings, General can switch the asking off altogether. It is not
drawn in full screen, in TV mode, in private viewing or for a kids
profile - the week's days still count.

Every key is the household's (core/donate_rhythm.py), none a profile's own.
"""

from __future__ import annotations

import time

from PyQt6.QtCore import QTimer

from ..core import donate_rhythm as rhythm
from ..core.log import log
from ..core.xdg import open_url
from ..i18n import tr
from .theme import HEART, P

# The support page: why, a tip or monthly support, GitHub Sponsors - and
# the supporter key after paying. Nothing is paid in the app.
DONATE_URL = "https://dopeiptv.org/support/?ref={ref}"
#: How long the seconds watched are taken as known (the household's logs
#: are read again only after this, and only while a period could begin).
WATCH_MEMO_S = 600.0


class _DonateMixin:
    _donate_bright = True          # the heart's ink: the colour, or muted

    def _init_donate(self) -> None:
        try:
            logs = self._donate_logs()
            earliest = min((v.since for v in logs if v.since > 0), default=0.0)
            rhythm.migrate(self.settings, self._donate_time(), earliest)
        except Exception as e:
            log.warning("donate: %s", e)
        self._donate_tick = QTimer(self)
        self._donate_tick.setInterval(60_000)
        self._donate_tick.timeout.connect(self._donate_minute)
        self._donate_tick.start()

    def _donate_time(self) -> float:
        """The clock the rhythm reads (a test sets its own)."""
        return time.time()

    # -- what the rhythm reads ----------------------------------------------
    def _donate_logs(self) -> list:
        """Every profile's viewing log: the household's use of the app. The
        profile in use's as the window holds it (minutes not saved yet
        included), the others' as stored."""
        from ..core import profiles, viewing_stats
        live = getattr(self, "_stats_log", None)
        if not isinstance(self.settings, profiles.ProfileSettings):
            return [live if live is not None else viewing_stats.ViewingLog(self.settings)]
        store = getattr(self, "profiles", None)
        active = profiles.active_id()
        pids = [p.id for p in store.profiles()] if store is not None else []
        if active not in pids:
            pids.append(active)
        return [live if pid == active and live is not None
                else viewing_stats.ViewingLog(profiles.pinned(self.settings, pid))
                for pid in pids]

    def _donate_watched_s(self) -> float:
        """Seconds watched in the last 30 days, every profile's."""
        now = self._donate_time()
        memo = getattr(self, "_donate_watch_memo", None)
        if memo is not None and 0 <= now - memo[0] < WATCH_MEMO_S:
            return memo[1]
        since = now - rhythm.WATCH_DAYS * rhythm.DAY
        total = sum(v.watched_since(since) for v in self._donate_logs())
        self._donate_watch_memo = (now, total)
        return total

    def _donate_quiet(self) -> bool:
        """Where asking would be in the way: video in full screen (docked
        or popped out), TV mode, private viewing, a kids profile."""
        from ..core import privacy
        if privacy.private() or getattr(self, "_tv_on", False) \
                or getattr(self, "_player_fs", False):
            return True
        pop = getattr(self, "_popout_win", None)
        try:
            if pop is not None and pop.isFullScreen():
                return True
        except RuntimeError:
            pass
        kids = getattr(self, "_profile_kids", None)
        try:
            return bool(kids is not None and kids())
        except Exception:
            return False

    def _donate_phase(self, sup=False) -> str:
        """Where the rhythm is now (a period begun if one may, and it would
        be seen). *sup*: the supporter key, if the caller has it read."""
        if sup is False:
            sup = self._supporter()
        if sup is not None or not rhythm.asking(self.settings):
            return rhythm.OFF
        try:
            return rhythm.step(self.settings, self._donate_time(),
                               self._donate_watched_s,
                               can_begin=not self._donate_quiet())
        except Exception as e:
            log.warning("donate: %s", e)
            return rhythm.OFF

    # -- the sidebar's heart ---------------------------------------------------
    def _supporter(self):
        from ..core import supporter
        return supporter.stored(self.settings)

    def _supporter_now(self) -> bool:
        try:
            return self._supporter() is not None
        except Exception:                      # no settings yet
            return False

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

    def _donate_ink(self) -> str:
        """The heart's colour at rest: its own the first day of a period,
        the theme's muted ink after it."""
        return HEART if self._donate_bright else P.get("muted", HEART)

    def _donate_show(self, sup=False) -> str:
        """The heart as the rhythm says: shown in its week, none while it
        would be in the way."""
        try:
            return self._donate_place(sup)
        except Exception as e:                 # never in the way of anything
            log.warning("donate: %s", e)
            return rhythm.OFF

    def _donate_place(self, sup) -> str:
        ph = self._donate_phase(sup)
        shown = ph != rhythm.OFF and not self._donate_quiet()
        self._donate_bright = ph == rhythm.BRIGHT
        btn = getattr(self, "_donate_btn", None)
        if btn is not None:
            btn.set_rest(self._donate_ink())
            btn.setVisible(shown)
        self._place_logo_marks()
        return ph

    def _donate_heart_refresh(self) -> None:
        btn = getattr(self, "_donate_btn", None)
        if btn is None:
            return
        # A supporter's heart is gone: the logo itself says it instead.
        sup = self._supporter()
        btn.setToolTip(tr("donate_support") + "\n" + tr("heart_tip_key"))
        from .qt_i18n import format_month
        logo = getattr(self, "_sidebar_logo", None)
        if logo is not None and hasattr(logo, "set_supporter"):
            logo.set_supporter(sup is not None, tr("supporter_tip", date=format_month(sup["since"]))
                               if sup is not None else "")
        self._donate_show(sup)
        self._supporter_perks(sup)

    def _donate_minute(self) -> None:
        """Once a minute: a period begun or over, its first day gone, a
        snooze run out - and the supporter key's perks kept in step."""
        self._donate_heart_refresh()

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

    # -- the answers -------------------------------------------------------------
    def donated(self) -> None:
        """"I've supported": not asked for a year."""
        rhythm.put_away(self.settings, self._donate_time(), rhythm.DONATED_S)
        self._donate_heart_refresh()

    def hide_donate_heart(self) -> None:
        """"Hide for three months"."""
        rhythm.put_away(self.settings, self._donate_time(), rhythm.LATER_S)
        self._donate_heart_refresh()

    def _donate_go(self, ref: str) -> None:
        """Donate, from where it was asked: the website's donate dialog, and
        this month's asking is over."""
        self.open_donate(ref)
        rhythm.end_period(self.settings, self._donate_time())
        self._donate_heart_refresh()

    def _donate_go_heart(self) -> None:
        self._donate_go("app-heart")

    def _donate_heart_menu(self, pos, btn=None) -> None:
        from PyQt6.QtWidgets import QMenu
        btn = btn or self._donate_btn
        menu = QMenu(btn)
        menu.addAction(tr("donate_support"), self._donate_go_heart)
        menu.addAction(tr("supporter_menu"), self.open_supporter)
        menu.addAction(tr("donate_hide"), self.hide_donate_heart)
        if self._supporter() is None:
            menu.addAction(tr("donate_did"), self.donated)
        menu.exec(btn.mapToGlobal(pos))

    def heart_clicked(self, ref: str = "app-heart") -> None:
        """The heart: the donate page, or for a supporter their thank-you
        (from which they can still donate again)."""
        if self._supporter() is not None:
            self.open_supporter()
        else:
            self._donate_go(ref)

    def open_supporter(self, key: str = "") -> None:
        """The supporter window; *key* (from a dopeiptv://supporter link)
        filled in, so the person sees it checked and kept."""
        from .dialogs import SupporterDialog
        d = SupporterDialog(self, self.settings, donate=lambda: self.open_donate("supporter"))
        if key:
            d.key.setText(key)
        d.exec()
        self._donate_heart_refresh()

    def open_donate(self, ref: str) -> None:
        open_url(DONATE_URL.format(ref=ref))
