"""The window's side of its notices (ui/notices.py): what the app is doing
and what it tells the person, in one corner of the window.

Every older way in leads there, so each producer keeps its call:

- ``_show_busy`` / ``_hide_busy`` - the list's (and the other places') work:
  one job under the key "list", the newest words win, ended by
  ``_hide_busy``, by a newer one, or after 25 s by its safety timeout. The
  centred spinner over an empty list ("Loading movies…") stays as the
  list's own empty state.
- The programme guide (``_guide_busy_begin`` / ``_on_epg_progress`` /
  ``_guide_busy_end``): one owner, a token per load. Only the load the
  window started last shows a job ("guide") or ends it - a late word from an
  older load (a source switched away from, a refresh replaced) changes
  nothing, and a list loaded meanwhile does not end it. A load that says
  nothing for ``GUIDE_QUIET_MS`` is let go with a calm note.
- ``_show_toast`` / ``_show_action_toast`` / ``_flash_status`` - a card.
- ``_set_status`` - the status line keeps only the list's state and what
  plays; activity words (``emphasis``) are a job ("status") until the next
  status, an error a card.

Where the corner is (``_notices_place``): the bottom of the list pane on the
side away from the sidebar, above its status line - never over the video;
nothing drawn while the video fills the screen or TV mode shows (cards wait).
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtWidgets import QLabel

from ..core.log import log
from ..i18n import tr
from .notices import Follow, NoticeArea, NoticeHub, hub_of
from .theme import P


class _BusyMixin:
    """MainWindow mixin: the notice hub and its corner, the list's and the
    guide's work, and the older notice calls routed to them."""
    _SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    #: A guide load that says nothing for this long (no progress, no end) is
    #: let go: its job ends with a calm note, the load itself goes on.
    #: A big guide's parse says nothing for 10-25 s on a usual computer.
    GUIDE_QUIET_MS = 60_000
    #: The list's work is let go after this if nothing ended it.
    LIST_TIMEOUT_MS = 25_000
    #: Activity words in the status line ("Reconnecting…") last until the
    #: next status - or this long.
    STATUS_JOB_MS = 30_000

    # The list's own (None: not busy, "" busy without words) and the guide's:
    # the load that owns it (0: none), whether its job shows (a guide from
    # the disk cache comes in without a word) and in whose words.
    _busy_main: str | None = None
    _guide_busy_seq = 0
    _guide_busy_token = 0
    _guide_busy_job = 0
    _guide_busy_msg: str | None = None

    # -- the hub and its corner -------------------------------------------------

    def _init_notices(self) -> None:
        """Made once in _build_ui: the hub, its corner, and what moves it."""
        self.notices = NoticeHub(self)
        self._notice_area = NoticeArea(self.notices, self)
        self._notice_follow = Follow(self._notices_place, self)
        for w in (self, getattr(self, "_mid", None), getattr(self, "_center_stack", None),
                  getattr(self, "count_lbl", None)):
            if w is not None:
                self._notice_follow.watch(w)
        cs = getattr(self, "_center_stack", None)
        if cs is not None:
            cs.currentChanged.connect(self._notice_follow.poke)
        self.notices.changed.connect(self._notice_follow.poke)

    def _notices_held(self) -> bool:
        """The video fills the screen, or TV mode shows: no card drawn."""
        return bool(getattr(self, "_player_fs", False) or getattr(self, "_tv_on", False))

    def _notices_place(self) -> None:
        area = getattr(self, "_notice_area", None)
        if area is None:
            return
        held = self._notices_held()
        self.notices.hold(held)
        if held:
            area.set_anchor(None)
            return
        cs = getattr(self, "_center_stack", None)
        mid = getattr(self, "_mid", None)
        root = getattr(self, "_root", None)
        if (cs is not None and mid is not None and cs.currentWidget() is root
                and mid.isVisible()):
            rect = QRect(mid.mapTo(self, QPoint(0, 0)), mid.size())
            lbl = getattr(self, "count_lbl", None)
            if lbl is not None and lbl.isVisible():
                top = lbl.mapTo(self, QPoint(0, 0)).y()
                if rect.top() < top < rect.bottom():
                    rect.setBottom(top - 2)
        elif cs is not None:
            rect = QRect(cs.mapTo(self, QPoint(0, 0)), cs.size())
        else:
            rect = self.rect()
        area.set_anchor(rect, self.layoutDirection() == Qt.LayoutDirection.RightToLeft)

    def _notices_restyle(self) -> None:
        area = getattr(self, "_notice_area", None)
        if area is not None:
            area.restyle()

    # -- the list's work ----------------------------------------------------------

    def _show_busy(self, message: str | None = None) -> None:
        """The list's (or another place's) work, as a job; and - when the
        list is empty and a message is given - a centred spinner + label
        over it ('Loading movies…'), so it reads as 'working', not a broken
        blank list. Its job ends by itself after LIST_TIMEOUT_MS."""
        self._busy_main = message or ""
        hub_of(self).start(message or tr("status_loading_content"), key="list",
                           timeout_ms=self.LIST_TIMEOUT_MS,
                           on_expire=self._busy_expired)
        self._update_busy_overlay(message)

    def _busy_say(self, text: str) -> None:
        """New words for the list's work while it runs (a scan's count)."""
        hub = hub_of(self)
        hub.update(hub.token_for("list"), text)

    def _hide_busy(self) -> None:
        """The list's work is over. A guide load still on its way keeps its
        own job until it ends."""
        self._busy_main = None
        hub_of(self).end_key("list")
        self._hide_busy_overlay()

    def _busy_expired(self) -> None:
        self._busy_main = None
        self._hide_busy_overlay()

    def _hide_busy_overlay(self) -> None:
        ov = getattr(self, "_busy_overlay", None)
        if ov is not None:
            ov.hide()
            self._busy_spin_timer.stop()

    def _ensure_busy_overlay(self):
        ov = getattr(self, "_busy_overlay", None)
        if ov is None:
            ov = QLabel(self.listw)
            ov.setObjectName("BusyOverlay")
            ov.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ov.setStyleSheet(
                "#BusyOverlay {"
                f" background: {P['hover']}; color: {P['text']};"
                " border: none; border-radius: 10px;"
                " padding: 12px 20px; font-size: 13px; font-weight: 600; }")
            ov.hide()
            self._busy_overlay = ov
            self._busy_spin_i = 0
            self._busy_text = ""
            t = QTimer(self)
            t.setInterval(110)
            t.timeout.connect(self._tick_busy_overlay)
            self._busy_spin_timer = t
        return self._busy_overlay

    def _tick_busy_overlay(self) -> None:
        ov = self._busy_overlay
        self._busy_spin_i = (self._busy_spin_i + 1) % len(self._SPIN)
        ov.setText(f"{self._SPIN[self._busy_spin_i]}   {self._busy_text}")
        ov.adjustSize()
        ov.move((self.listw.width() - ov.width()) // 2,
                max(24, (self.listw.height() - ov.height()) // 3))

    def _update_busy_overlay(self, message: str | None) -> None:
        if not hasattr(self, "listw") or not hasattr(self, "list_model"):
            return
        ov = self._ensure_busy_overlay()
        if message and self.list_model.rowCount() == 0:
            self._busy_text = message
            self._tick_busy_overlay()
            ov.show()
            ov.raise_()
            self._busy_spin_timer.start()
        else:
            ov.hide()
            self._busy_spin_timer.stop()

    # -- the programme guide's work -------------------------------------------

    def _guide_busy_begin(self, message: str | None = None) -> int:
        """A guide load the window starts: it owns the guide's job from now
        on, and its token is what its progress and its end carry. Shown only
        once it reports progress (a download); *message* names the wait after
        what the person did ("Refreshing playlist…")."""
        token = self._guide_busy_seq + 1
        self._guide_busy_seq = token
        self._guide_busy_token = token
        self._guide_busy_msg = message
        # What an older load showed is not this one's to keep.
        hub_of(self).cancel(self._guide_busy_job)
        self._guide_busy_job = 0
        return token

    def _on_epg_progress(self, token: int, value: int) -> None:
        """A guide download's progress (delivered on the window's thread).
        Shown only for the load that owns the guide's job; percentages are
        erratic (often no total, then nothing through the parse), so the
        job only says it is loading."""
        if not token:
            # A load nobody here started (a lookup on a worker found no guide
            # and loads it itself): the window owns one beside it, which
            # waits for that load and ends the job with it.
            adopt = getattr(self, "_ensure_xmltv_loaded", None)
            if callable(adopt):
                adopt()
            return
        if token != self._guide_busy_token:
            return          # an older load's late word
        hub = hub_of(self)
        if not hub.update(self._guide_busy_job):
            self._guide_busy_job = hub.start(
                self._guide_busy_msg or tr("status_loading_programme_guide"),
                key="guide", timeout_ms=self.GUIDE_QUIET_MS,
                on_expire=self._guide_busy_quiet)

    def _guide_busy_shown(self) -> bool:
        return bool(self._guide_busy_job) and hub_of(self).alive(self._guide_busy_job)

    def _guide_busy_end(self, token: int, note: str | None = None) -> bool:
        """The load with *token* ended: its job goes, and *note* is said
        when the job was showing. An older load's end changes nothing
        (False)."""
        if not token or token != self._guide_busy_token:
            return False
        shown = self._guide_busy_shown()
        self._guide_busy_token = 0
        self._guide_busy_msg = None
        hub = hub_of(self)
        hub.finish(self._guide_busy_job)
        self._guide_busy_job = 0
        if note and shown:
            hub.post(note, level="error")
        return True

    def _guide_busy_quiet(self) -> None:
        """The safety net: the load that owns the guide's job said nothing
        for GUIDE_QUIET_MS (the hub let its job go). The load goes on, and
        when it is in the lists show it as usual."""
        token = self._guide_busy_token
        if not token:
            return
        log.info("EPG: the guide load said nothing for %d s - its job ends, "
                 "the load goes on", self.GUIDE_QUIET_MS // 1000)
        self._guide_busy_job = 0
        self._guide_busy_token = 0
        self._guide_busy_msg = None
        hub_of(self).post(tr("status_guide_still_loading"))

    # -- the older ways in --------------------------------------------------------

    def _show_toast(self, text: str, duration_ms: int = 0) -> None:
        hub_of(self).post(text, ms=duration_ms or None)

    def _show_action_toast(self, text: str, duration_ms: int, on_click,
                           label: str | None = None) -> None:
        """A card that does *on_click* - its button with *label*, else a
        click on the card (words written as "click here")."""
        hub_of(self).post(text, ms=duration_ms or None, action=on_click, action_label=label)

    def _flash_status(self, text: str, ms: int = 4000) -> None:
        """A momentary word (guide refreshed, cache cleared, ...): a card."""
        hub_of(self).post(text, ms=ms)

    def _write_status(self, text: str, error: bool = False,
                      emphasis: bool = False) -> None:
        # The resting readout (channel count, what plays) stays calm and muted.
        self.count_lbl.setStyleSheet(f"color:{P['muted3']}; font-size:11px;")
        self.count_lbl.setText(text)

    def _set_status(self, text: str, error: bool = False,
                    emphasis: bool = False) -> None:
        """The status line's resting readout (the list's count, what plays).
        Activity words (*emphasis*: "Reconnecting…") are a job until the next
        status; an *error* is a card, and a line that said what plays then
        says the list's again."""
        hub = hub_of(self)
        if emphasis and not error:
            if text:
                hub.start(text, key="status", timeout_ms=self.STATUS_JOB_MS)
            else:
                hub.end_key("status")
            return
        hub.end_key("status")
        if error:
            hub.post(text, level="error")
            rest = getattr(self, "_rest_status", None)
            if rest and rest[0] and rest[0] == getattr(self, "_playing_shown", None):
                stopped = getattr(self, "_source_status_stopped", None)
                if callable(stopped):
                    stopped()
            return
        self._rest_status = (text, False, False)
        self._write_status(text)
