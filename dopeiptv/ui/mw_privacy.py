"""Private viewing and Clear recent history in the window (core/privacy.py
holds the rules): the two entries in the app menu, their shortcuts as a
browser has them, the marker in the sidebar while private viewing is on,
and what clearing refreshes."""

from __future__ import annotations

import sys

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QPushButton

from ..core import privacy
from ..core.log import log
from ..i18n import tr
from .theme import P

PRIVATE_SHORTCUT = "Ctrl+Shift+N"
# A Mac's delete key is Backspace (⇧⌘⌫, as Safari has it); few have the other.
CLEAR_SHORTCUT = "Ctrl+Shift+Backspace" if sys.platform == "darwin" else "Ctrl+Shift+Delete"


class _Follow(QObject):
    """Calls *place* whenever the watched widget moves, resizes or shows."""

    def __init__(self, parent: QObject, place) -> None:
        super().__init__(parent)
        self._place = place

    def eventFilter(self, obj, event) -> bool:        # noqa: N802 - Qt hook
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show,
                            QEvent.Type.LayoutRequest):
            self._place()
        return False


def note_titles(ctx: dict | None) -> None:
    """The names what just began to play goes by (*ctx*: the window's
    _last_playback, or a cast's context), said to core/privacy right after
    playback_started: for a private playback nothing is asked about them
    elsewhere (TMDB) while it lasts."""
    try:
        lp = ctx or {}
        item = lp.get("item") if isinstance(lp.get("item"), dict) else {}
        series = lp.get("series_ctx") if isinstance(lp.get("series_ctx"), dict) else {}
        privacy.playback_titles(
            lp.get("title"), item.get("name"), item.get("title"),
            item.get("_clean_title"), item.get("_series_title"),
            series.get("name"), series.get("title"))
    except Exception:
        log.debug("private viewing: titles", exc_info=True)


def wake_reason(title: str) -> str:
    """Why the screen is kept awake, as the system is told (and lists to
    whoever asks it): never the title of a private playback."""
    return tr("wake_playing", title="dopeIPTV" if privacy.playing_private() else title)


class _PrivacyMixin:
    def _build_privacy_actions(self, menu=None) -> list[QAction]:
        """Private viewing (on/off) and Clear recent history - in *menu*
        when given (the app menu lays them out itself otherwise)."""
        self._private_action = QAction(tr("private_menu"), self)
        self._private_action.setCheckable(True)
        self._private_action.setShortcut(QKeySequence(PRIVATE_SHORTCUT))
        self._private_action.toggled.connect(self.set_private_viewing)
        self._clear_action = QAction(tr("clear_menu") + "…", self)
        self._clear_action.setShortcut(QKeySequence(CLEAR_SHORTCUT))
        self._clear_action.triggered.connect(lambda: self.open_clear_history())
        if menu is not None:
            menu.addAction(self._private_action)
            menu.addAction(self._clear_action)
        return [self._private_action, self._clear_action]

    def _build_private_pill(self) -> None:
        """The marker while private viewing is on: a small label laid over
        the logo's lower edge, centred on it - in no layout, so the logo,
        the heart beside it and the row under it stay exactly where they
        are. A click turns private viewing off."""
        logo = self._sidebar_logo
        pill = QPushButton(tr("private_pill"), logo.parentWidget(), objectName="PrivatePill")
        pill.setCursor(Qt.CursorShape.PointingHandCursor)
        pill.setToolTip(tr("private_pill_tip"))
        pill.setAccessibleName(tr("private_pill_tip"))
        # Ringed in the sidebar's colour, so it reads as lying on the mark.
        pill.setStyleSheet(
            f"QPushButton#PrivatePill {{ background:{P['sel']}; color:{P['text']};"
            f" border:2px solid {P['side']}; border-radius:10px; padding:1px 9px;"
            f" font-size:10px; font-weight:700; }}"
            f"QPushButton#PrivatePill:hover {{ background:{P['btn_hover']}; }}")
        pill.clicked.connect(lambda: self.set_private_viewing(False))
        pill.hide()
        self._private_pill = pill
        follow = _Follow(pill, self._place_private_pill)
        logo.installEventFilter(follow)
        logo.parentWidget().installEventFilter(follow)
        # A rewind buffer watched privately that a crash left on disk goes
        # at this start, not at the first channel watched with the buffer.
        QTimer.singleShot(4000, self._privacy_sweep_buffers)

    def _private_reopen_if_on_disk(self, why: str) -> None:
        """The live channel playing privately opened again when its buffer
        is on the disk (media/dvr_player.private_reopen_needed): in memory
        out of a disk's reach, or with none."""
        p = getattr(self, "player", None)
        lp = getattr(self, "_last_playback", None) or {}
        reopen = getattr(p, "private_reopen_needed", None)
        if callable(reopen) and reopen() and lp.get("kind") == "live":
            log.info("private viewing: %s - opened again", why)
            QTimer.singleShot(0, self._retry_last_stream)

    def _private_ask_kept(self) -> None:
        """Private viewing turned on while a recording or a cast keeps the
        channel's buffer on the disk: nothing is stopped by itself - a card
        asks, Stop it ending what keeps it there (the recording, the cast),
        dismissed letting it finish. Once it is done the channel moves off
        the disk (_private_buffer_on_disk)."""
        p = getattr(self, "player", None)
        pool = getattr(p, "_dvr_pool", None)
        kept = getattr(pool, "kept_on_disk_private", None)
        s = kept() if callable(kept) else None
        if s is None:
            return
        recorders = [r for r in list(getattr(s, "recorders", [])) if getattr(r, "alive", False)]
        rec = getattr(self, "rec", None)
        jobs = [j["id"] for j in (getattr(rec, "jobs", None) or [])
                if j.get("status") == "recording" and j.get("recorder") in recorders]
        casting = bool(s.held and getattr(self, "_cast_device", None))
        log.info("private viewing: a %s keeps the channel's buffer on the disk - asked",
                 "recording and a cast" if jobs and casting else "cast" if casting else "recording")

        def stop() -> None:
            for jid in jobs:
                try:
                    self.rec.cancel(jid)
                except Exception:
                    log.debug("private viewing: a recording not stopped", exc_info=True)
            if casting and hasattr(self, "_end_cast"):
                self._end_cast("private viewing asked it")
        from .notices import hub_of
        hub_of(self).post(tr("private_kept_body"), title=tr("private_kept_title"), level="warning",
                          action=stop, action_label=tr("private_kept_stop"),
                          key="private_kept_on_disk", decide=True)

    def _private_buffer_on_disk(self) -> None:
        """The recording or cast that kept a private channel's buffer on the
        disk is done (core/dvr_pool.on_private_disk): nothing more of it is
        written there. The pool asks again whether it still is (the
        session's own private mark) - and nothing is started again when
        the player let go of the channel before this came."""
        if getattr(getattr(self, "player", None), "_dvr", None) is None:
            return
        self._private_reopen_if_on_disk("the recording or cast that kept its buffer "
                                        "on the disk is done")

    def _privacy_sweep_buffers(self) -> None:
        try:
            from ..media.dvr_player import dvr_base
            dvr_base()                  # sweeps once, on a thread of its own
        except Exception:
            log.debug("private viewing: buffers not swept", exc_info=True)
        # A private cast's work and a private subtitle a crash left in
        # memory (core/memory_safe): gone now, not at the next logout.
        import threading

        def sweep() -> None:
            from ..core import memory_safe
            for name in ("cast", "subs"):
                base = memory_safe.private_base(name)
                if base is not None and base.is_dir():
                    memory_safe.sweep(base)
        threading.Thread(target=sweep, name="private-sweep", daemon=True).start()

    def _place_private_pill(self) -> None:
        pill = getattr(self, "_private_pill", None)
        logo = getattr(self, "_sidebar_logo", None)
        if pill is None or logo is None or not pill.isVisible():
            return
        pill.adjustSize()
        g = logo.geometry()
        mark_top = g.top() + (g.height() - logo.LOGO_H) // 2
        # On the icon rail the heart sits under the logo: the top edge there.
        edge = mark_top if getattr(self, "_sidebar_collapsed", False) else mark_top + logo.LOGO_H
        pill.move(g.center().x() + 1 - pill.width() // 2, edge - pill.height() // 2)
        pill.raise_()

    def set_private_viewing(self, on: bool) -> None:
        """Turn private viewing on or off (it ends with the app anyway)."""
        on = bool(on)
        was = privacy.private()
        if on and not was and hasattr(self, "_stats_flush"):
            self._stats_flush()         # what played before counts
        privacy.set_private(on)
        if on:
            # What plays now is private to the end of its run (core/privacy):
            # here, or on a TV (casting stops the player here) - where the
            # TV got to, History and the reporters keep nothing of it after
            # private viewing ends either.
            p = getattr(self, "player", None)
            lp = getattr(self, "_last_playback", None) or {}
            cast = getattr(self, "_cast_ctx", None) or {}
            if getattr(self, "_cast_device", None) and cast.get("key") is not None \
                    and cast.get("kind"):
                privacy.playback_started(cast["kind"], cast["key"])
                note_titles(cast)
            elif p is not None and getattr(p, "current_url", None) and lp:
                privacy.playback_started(lp.get("kind") or "", lp.get("key"))
                note_titles(lp)
                # Its rewind buffer goes when the channel is left, and
                # stream health counts no more of it.
                mark = getattr(p, "dvr_mark_private", None)
                if callable(mark):
                    mark()
                # A buffer on the disk - the app's or mpv's own - holds
                # nothing more of it: the channel opened again, its buffer
                # in memory out of a disk's reach, or none (core/dvr_pool).
                self._private_reopen_if_on_disk("the channel's buffer is on the disk")
                # One a recording or a cast keeps on the disk: asked about.
                self._private_ask_kept()
            # A cast whose converter works on the disk: sent again, its work
            # in memory out of a disk's reach - or stopped and said so.
            if hasattr(self, "_cast_private_on"):
                self._cast_private_on()
            health = getattr(self, "health", None)
            if health is not None:
                health.leave()
            # A Trakt or Simkl scrobble already under way is let go, not
            # stopped: a stop would report the progress and file it in the
            # history.
            self._trakt_active = None
            self._simkl_active = None
            # The statistics count nothing more of it; a media server hears
            # where it got to, and no more.
            self._stats_now = None
            if hasattr(self, "_ms_leave"):
                self._ms_leave()
            # Watch together tells the others what plays: out of the room.
            if hasattr(self, "_together_private_on"):
                self._together_private_on()
        elif was:
            # Private viewing ended: the rewind buffers of the channels
            # left in it go now (the one still playing when it is left).
            forget = getattr(getattr(self, "player", None), "dvr_forget_private", None)
            if callable(forget):
                forget()
        # Home Assistant's retained state says it at once, either way.
        if hasattr(self, "_events_private_changed"):
            self._events_private_changed(on)
        # Discord's status goes as private viewing starts; what is chosen
        # after it ends shows again.
        if hasattr(self, "_discord_push"):
            self._discord_push()
        action = getattr(self, "_private_action", None)
        if action is not None and action.isChecked() != on:
            action.blockSignals(True)
            action.setChecked(on)
            action.blockSignals(False)
        pill = getattr(self, "_private_pill", None)
        if pill is not None:
            pill.setVisible(on)
            self._place_private_pill()
        if hasattr(self, "_donate_heart_refresh"):   # the heart gives way
            self._donate_heart_refresh()
        if was != on:
            self._show_toast(tr("private_on_toast") if on else tr("private_off_toast"), 5000)

    def open_clear_history(self, preselect: str | None = None) -> dict | None:
        """The Clear recent history window; clears what it says and says
        what went. Returns the counts, or None when cancelled."""
        from .dialogs import ClearHistoryDialog
        d = ClearHistoryDialog(self, preselect)
        if not d.exec():
            return None
        c = d.choice()
        resume_settings = getattr(self, "_resume_settings", self.settings)
        self._stats_drop_pending()
        counts = privacy.clear_recent(self.settings, resume_settings, self.history,
                                      c["seconds"], c["channels"], c["titles"], c["positions"],
                                      viewing=self._stats_store())
        # The resume store holds its positions in memory: read them again.
        if c["positions"] and getattr(self, "resume", None) is not None:
            from ..services.resume import ResumeStore
            pl = self.playlist_store.active() if self.playlist_store else None
            own = getattr(self, "_own", None)
            self.resume = ResumeStore(own(resume_settings) if own is not None else resume_settings,
                                      (pl or {}).get("id"))
        # So do the channels' stream details, gone with the channels.
        if c["channels"] and hasattr(self, "_stream_info_forget"):
            self._stream_info_forget()
            try:
                self.listw.viewport().update()
            except Exception:
                pass
        total = sum(counts.values())
        self._show_toast(tr("clear_done", n=total) if total else tr("clear_nothing"), 5000)
        self._after_clear()
        return counts

    def _after_clear(self) -> None:
        """Lists that show history or positions, shown again."""
        if getattr(self, "mode", None) == "history":
            try:
                self._load_items(getattr(self, "_history_subcat", None))
            except Exception:
                pass
        refresh = getattr(self, "_refresh_places", None)
        if callable(refresh):
            try:
                refresh("clear")
            except Exception:
                pass
