"""MainWindow mixin: following a series, and hearing of its new episodes.

The provider is asked shortly after start and then every few hours what each
followed series lists; core/stores.py FollowedSeriesStore remembers what was
there before. What is new lands on Home's "New episodes" shelf, in a toast
and - on Linux, when the window is not in front - as a desktop notification.
Opening the series marks it seen and lands on its first new episode.
"""
from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QTimer, QUrl

from ..core.log import log
from ..core.stores import episode_rows
from ..core.workers import run_async
from ..i18n import tr

FIRST_CHECK_MS = 90 * 1000
CHECK_EVERY_MS = 3 * 60 * 60 * 1000
# A series asked about within this long is left alone - a restart, or a
# playlist switch there and back, does not ask every provider again.
MIN_AGE_S = 2.5 * 60 * 60
# One run asks at most this many; the rest wait for the next one.
MAX_PER_RUN = 60


def episode_label(ep: dict) -> str:
    """"S2 E5": short enough for a toast, and free of the series name that
    providers repeat in every episode title."""
    season, num = ep.get("season") or "", ep.get("episode_num") or ""
    if season and num:
        return f"S{season} E{num}"
    return ep.get("title") or (f"E{num}" if num else "")


class _FollowMixin:
    """MainWindow mixin: Follow series, the background check, the news."""

    def _start_follow_checks(self) -> None:
        self._follow_running = False
        self._follow_timer = QTimer(self)
        self._follow_timer.timeout.connect(self._check_followed)
        self._follow_timer.start(CHECK_EVERY_MS)
        QTimer.singleShot(FIRST_CHECK_MS, self._check_followed)

    # -- following ------------------------------------------------------------

    def _add_follow_action(self, menu, it: dict) -> None:
        if it.get("series_id") is None:
            return
        if self.followed.is_followed(it.get("series_id")):
            menu.addAction(tr("ctx_unfollow_series"),
                           lambda: self._unfollow_series(it))
        else:
            menu.addAction(tr("ctx_follow_series"),
                           lambda: self._follow_series(it))

    def _follow_series(self, it: dict) -> None:
        sid = it.get("series_id")
        if sid is None:
            return
        store = self.followed
        store.follow(it)
        name = it.get("name") or it.get("title") or ""
        self._show_toast(tr("follow_on_toast", name=name), 4000)
        # Learn what is out now, so the first episode that arrives after
        # this moment is the first one announced.
        client = self.client
        run_async(self.pool,
                  lambda: episode_rows(client.series_info(sid)),
                  lambda rows: store.update_rows(sid, rows),
                  lambda msg: log.info("follow: first look at %s failed: %s",
                                       sid, msg))

    def _unfollow_series(self, it: dict) -> None:
        self.followed.unfollow(it.get("series_id"))
        name = it.get("name") or it.get("title") or ""
        self._show_toast(tr("follow_off_toast", name=name), 4000)
        if self._home_showing():
            self._home_page.refresh()

    def _follow_mark_seen(self, series_id: Any) -> None:
        self.followed.seen(series_id)
        if self._home_showing():
            self._home_page.refresh()

    def _follow_entered(self, series_id: Any, info: Any,
                        episodes: list) -> None:
        """The series' episode list is open: flag what is new, select the
        first of it, and count it as seen from now on."""
        store = self.followed
        if not store.is_followed(series_id):
            return
        new = {str(i) for i in store.new_ids(series_id)}
        first = None
        for ep in episodes:
            if str(ep.get("id")) in new:
                ep["_new"] = True
                if first is None:
                    first = ep.get("id")
        if first is not None and self._pending_jump_key is None:
            self._pending_jump_key = first
            QTimer.singleShot(8000, self._clear_pending_jump)
        store.seen(series_id, info)

    # -- the background check -------------------------------------------------

    def _check_followed(self) -> None:
        store, client = self.followed, self.client
        if self._follow_running or not store.ids():
            return
        due = store.due(time.time(), MIN_AGE_S)[:MAX_PER_RUN]
        if not due:
            return
        self._follow_running = True
        log.info("follow: asking about %d series", len(due))

        def work() -> dict:
            out = {}
            for sid in due:
                try:
                    out[sid] = episode_rows(client.series_info(sid))
                except Exception as e:
                    # One series failing is not the run failing.
                    log.info("follow: series %s: %s", sid, e)
            return out

        def done(answers: dict) -> None:
            self._follow_running = False
            fresh = []
            for sid, rows in answers.items():
                eps = store.update_rows(sid, rows)
                if eps:
                    fresh.append((store.snapshot(sid), eps))
            # A playlist switch meanwhile: the old one's store is kept up to
            # date, but its news is not this playlist's to announce.
            if fresh and store is self.followed:
                self._announce_new_episodes(fresh)

        def failed(msg: str) -> None:
            self._follow_running = False
            log.warning("follow: check failed: %s", msg)

        run_async(self.pool, work, done, failed)

    def _announce_new_episodes(self, fresh: list) -> None:
        if len(fresh) == 1:
            snap, eps = fresh[0]
            name = snap.get("name") or snap.get("title") or ""
            text = (tr("follow_new_episode", name=name,
                       episode=episode_label(eps[0])) if len(eps) == 1
                    else tr("follow_new_episodes", name=name, n=len(eps)))
        else:
            text = tr("follow_new_series", n=len(fresh))
        log.info("follow: %s", text)
        self._show_toast(text, 6000)
        if not self.isActiveWindow():
            from ..services.notify import notify
            notify(tr("home_new_episodes"), text,
                   self._follow_image(fresh[0][0]))
        if self._home_showing():
            self._home_page.refresh()

    def _follow_image(self, snap: dict) -> str:
        """The series' cover from the image cache, as a file the desktop can
        read - a notification daemon does not fetch from the network."""
        url = snap.get("cover") or snap.get("stream_icon")
        if not url:
            return ""
        try:
            path = self.poster_art.disk_file(url)
        except Exception:
            path = None
        return QUrl.fromLocalFile(str(path)).toString() if path else ""
