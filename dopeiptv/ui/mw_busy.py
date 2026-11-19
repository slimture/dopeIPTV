"""The window's side of its notices (ui/notices.py): what the app is doing
and what it tells the person, all at the foot of the sidebar.

Every older way in leads there, so each producer keeps its call:

- ``_show_busy`` / ``_hide_busy`` - the list's (and the other places') work:
  one job under the key "list", the newest words win, ended by
  ``_hide_busy``, by a newer one, or after 25 s by its safety timeout. The
  centred spinner over an empty list ("Loading movies…") stays as the
  list's own empty state.
- The programme guide (``_guide_busy_begin`` / ``_on_epg_progress`` /
  ``_guide_busy_end``): one owner, a token per load. Only the load the
  window started last shows a job ("guide", with its percentage) or ends it
  - a late word from an older load (a source switched away from, a refresh
  replaced) changes nothing, and a list loaded meanwhile does not end it. A
  load that says nothing for ``GUIDE_QUIET_MS`` is let go with a calm note.
- ``_show_toast`` / ``_flash_status`` - a passing note on the activity line.
- ``_show_action_toast`` - a message with its action in the panel; with
  *decide* the card that waits for a decision.
- ``_set_status`` - the status line keeps only the list's state and what
  plays; activity words (``emphasis``) are a job ("status") until the next
  status, an error a message.

Where things are (``_notices_place``): the activity line and the decision
card are part of the sidebar's own layout, at its foot under the category
list, just above the source avatar - the list only gets shorter while the
card shows (the chosen category kept in view), nothing lies over a row.
On the icon rail the line is a ring with the count and the card sits beside
the rail; on a page with no sidebar (one over the whole window) both are
at the same corner.
Nothing is drawn while the video fills the screen, TV mode shows or the
picture is popped out.
"""
from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, QTimer
from PyQt6.QtWidgets import QLabel

from ..core.log import log
from ..i18n import tr
from .notice_widgets import ActivityLine, ActivityPanel, DecisionCard, Follow
from .notices import NoticeHub, hub_of
from .theme import P


def _guide_group(n: int) -> str:
    """Guides loading together (the window's and the served sources')."""
    return tr("notice_guide_sources", n=n)


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
    #: The app's margin from the window's edges and between its surfaces.
    NOTICE_MARGIN = 16

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
        """Made once in _build_ui: the hub; the decision card and the
        activity line in the sidebar's fixed foot, under the part that
        scrolls and over the source avatar (and their stand-ins for the icon
        rail and a page with no sidebar); the panel; and what moves them."""
        hub = self.notices = NoticeHub(self)
        line = self._activity_line = ActivityLine(hub)
        card = self._decision_card = DecisionCard(hub)
        self._notices_foot(card, line)
        self._activity_float = ActivityLine(hub, self, floating=True)
        self._decision_float = DecisionCard(hub, self, floating=True)
        self._notice_panel = ActivityPanel(hub, self)
        for ln in (line, self._activity_float):
            ln.open_panel.connect(self._notices_open_panel)
        self._notice_follow = Follow(self._notices_place, self)
        for w in (self, getattr(self, "_mid", None), getattr(self, "_center_stack", None),
                  getattr(self, "_side", None), self._notice_panel):
            if w is not None:
                self._notice_follow.watch(w)
        cs = getattr(self, "_center_stack", None)
        if cs is not None:
            cs.currentChanged.connect(self._notice_follow.poke)
        hub.changed.connect(self._notices_changed)

    def _notices_foot(self, card: DecisionCard, line: ActivityLine) -> None:
        """The decision card and the activity line in the sidebar's foot, the
        fixed area under the part that scrolls (main_window), over the
        hairline and the avatar: in the layout, not over it - the scrolling
        part gives up the room they take and takes it back."""
        foot = getattr(self, "_side_foot_lay", None)
        if foot is not None:
            foot.insertWidget(0, card)
            foot.insertWidget(1, line)

    def _notices_changed(self) -> None:
        # Placed at once (cheap): a stand-in must not show a frame at the
        # window's corner before it is moved to its own.
        self._notices_place()

    def _notices_held(self) -> bool:
        """The video fills the screen, TV mode shows, or the picture is
        popped out: nothing drawn."""
        pop = getattr(self, "_popout_win", None)
        try:
            popped = pop is not None and pop.isVisible()
        except RuntimeError:            # deleted under us
            popped = False
        return bool(getattr(self, "_player_fs", False) or getattr(self, "_tv_on", False)
                    or popped)

    def _notices_sidebar_shown(self) -> bool:
        cs = getattr(self, "_center_stack", None)
        side = getattr(self, "_side", None)
        return (cs is not None and side is not None
                and cs.currentWidget() is getattr(self, "_root", None) and side.isVisible())

    def _notices_place(self) -> None:
        card = getattr(self, "_decision_card", None)
        if card is None:
            return
        held = self._notices_held()
        self.notices.hold(held)
        line, floater = self._activity_line, self._activity_float
        float_card = self._decision_float
        rail = bool(getattr(self, "_sidebar_collapsed", False))
        line.set_ring(rail)
        was_shown = self._notices_foot_shown()
        if held:
            for w in (line, floater, card, float_card):
                w.hide()
            self._notice_panel.close_panel()
            self._notices_keep_category(was_shown)
            return
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        m = self.NOTICE_MARGIN
        if self._notices_sidebar_shown():
            floater.hide()
            line.refresh()
            if rail:
                card.hide()
                self._notices_float_card(float_card, beside=line)
            else:
                float_card.hide()
                card.refresh()
        else:
            # A page with no sidebar (over the whole window): the same
            # corner, its own pair.
            cs = getattr(self, "_center_stack", None)
            area = (QRect(cs.mapTo(self, QPoint(0, 0)), cs.size()) if cs is not None
                    else self.rect())
            card.hide()
            floater.refresh()
            bottom = area.bottom() - m + 1
            if not floater.isHidden():
                fw = min(floater.sizeHint().width(), 300)
                fx = area.right() - m - fw + 1 if rtl else area.left() + m
                floater.setGeometry(fx, bottom - floater.height(), fw, floater.height())
                floater.raise_()
                bottom -= floater.height() + 8
            self._notices_float_card(float_card, corner=(area, bottom))
        panel = self._notice_panel
        if not panel.isHidden():
            panel._place()
        self._notices_keep_category(was_shown)

    def _notices_float_card(self, card: DecisionCard, beside=None, corner=None) -> None:
        """The decision card where the sidebar has no room for it: beside the
        icon rail's ring, or at the corner of a page with no sidebar."""
        if not card.refresh():
            return
        rtl = self.layoutDirection() == Qt.LayoutDirection.RightToLeft
        w = 280
        card.setFixedWidth(w)
        card.adjustSize()
        h = card.sizeHint().height()
        side = getattr(self, "_side", None)
        if beside is not None and side is not None and side.isVisible():
            # Beside the rail, not over it: from the sidebar's edge, its foot
            # level with the ring's - or, with no ring, over the hairline.
            edge = side.mapTo(self, QPoint(0, 0)).x()
            x = edge - w - 8 if rtl else edge + side.width() + 8
            if beside.isVisible():
                y = beside.mapTo(self, QPoint(0, beside.height())).y() - h
            else:
                rule = getattr(self, "_bottom_rule", None) or beside
                y = rule.mapTo(self, QPoint(0, 0)).y() - 6 - h
        elif corner is not None:
            area, bottom = corner
            x = area.right() - self.NOTICE_MARGIN - w + 1 if rtl else area.left() + self.NOTICE_MARGIN
            y = bottom - h
        else:
            card.hide()
            return
        card.setGeometry(max(8, x), max(8, y), w, h)
        card.raise_()

    def _notices_foot_shown(self) -> tuple[bool, bool]:
        return (not self._decision_card.isHidden(), not self._activity_line.isHidden())

    def _notices_keep_category(self, was_shown: tuple[bool, bool]) -> None:
        """The card or the line came or went, so the part of the sidebar that
        scrolls changed height: the chosen category stays in view."""
        if was_shown == self._notices_foot_shown():
            return
        cats = getattr(self, "cat_list", None)
        if cats is not None:
            QTimer.singleShot(0, self._notices_scroll_category)

    def _notices_scroll_category(self) -> None:
        cats = getattr(self, "cat_list", None)
        item = cats.currentItem() if cats is not None else None
        if item is None or cats.isHidden():
            return
        side = getattr(self, "_side", None)
        if side is not None and side.layout() is not None:
            side.layout().activate()        # the room it has now, not before
        cats.scrollToItem(item)
        # The part of the sidebar that scrolls got shorter too: the chosen
        # row kept in its view as well.
        area = getattr(self, "_side_scroll", None)
        if area is not None and area.widget() is not None:
            r = cats.visualItemRect(item)
            at = cats.viewport().mapTo(area.widget(), r.center())
            area.ensureVisible(at.x(), at.y(), 0, r.height() // 2 + 4)

    def _notices_open_panel(self) -> None:
        panel = self._notice_panel
        if not panel.isHidden():
            panel.close_panel()
            return
        line = self.sender() if isinstance(self.sender(), ActivityLine) else self._activity_line
        rail = bool(getattr(self, "_sidebar_collapsed", False)) and line is self._activity_line
        panel.open_at(line, beside=(getattr(self, "_side", None) or True) if rail else False)

    def _notices_restyle(self) -> None:
        card = getattr(self, "_decision_card", None)
        if card is not None:
            for c in (card, self._decision_float):
                c.restyle()
            self._notice_panel.restyle()
            self._activity_line.update()
            self._activity_float.update()

    # -- the list's work ----------------------------------------------------------

    def _show_busy(self, message: str | None = None, kind: str = "list") -> None:
        """The list's (or another place's) work, as a job; and - when the
        list is empty and a message is given - a centred spinner + label
        over it ('Loading movies…'), so it reads as 'working', not a broken
        blank list. Its job ends by itself after LIST_TIMEOUT_MS."""
        self._busy_main = message or ""
        hub_of(self).start(message or tr("status_loading_content"), key="list",
                           kind=kind, mine=True, timeout_ms=self.LIST_TIMEOUT_MS,
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
            ov.setTextFormat(Qt.TextFormat.PlainText)      # it names a category
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
        words = self._guide_busy_msg or tr("status_loading_programme_guide")
        if value >= 100:
            # Downloaded: the parse and index say nothing until they are done.
            words, done, total = tr("notice_guide_preparing"), 0.0, 0.0
        elif value >= 0:
            done, total = float(value), 100.0
        else:
            done = total = None
        if not hub.update(self._guide_busy_job, words, done=done, total=total):
            self._guide_busy_job = hub.start(
                words, key="guide", kind="guide", mine=bool(self._guide_busy_msg),
                group="guide", group_text=_guide_group, done=done, total=total,
                timeout_ms=self.GUIDE_QUIET_MS, on_expire=self._guide_busy_quiet)

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
        """Passing words: the activity line's for a few seconds."""
        hub_of(self).note(text, ms=duration_ms or None)

    def _show_action_toast(self, text: str, duration_ms: int, on_click,
                           label: str | None = None, decide: bool = False,
                           key: str | None = None) -> None:
        """A message that does *on_click* (its button, *label*), counted on
        the activity line and listed in its panel; with *decide* it needs a
        decision now - the card over the line. Under *key* one message,
        refreshed, and taken away by that key (hub.dismiss_key)."""
        hub_of(self).post(text, action=on_click, action_label=label, decide=decide, key=key)

    def _flash_status(self, text: str, ms: int = 4000) -> None:
        """A momentary word (guide refreshed, cache cleared, ...): a note."""
        hub_of(self).note(text, ms=ms)

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
            self._status_play_ended()
            return
        self._rest_status = (text, False, False)
        self._write_status(text)

    def _status_play_ended(self) -> None:
        """What played failed: a line that said so says the list's again."""
        rest = getattr(self, "_rest_status", None)
        if rest and rest[0] and rest[0] == getattr(self, "_playing_shown", None):
            stopped = getattr(self, "_source_status_stopped", None)
            if callable(stopped):
                stopped()

    def _stream_failed_quietly(self) -> None:
        """A stream error: said on the video (main_window), never as a
        message - only the activity words for it ("Checking stream…") end."""
        hub_of(self).end_key("status")
        self._status_play_ended()
