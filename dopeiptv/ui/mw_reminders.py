"""Programme-reminder mixin for MainWindow.

Set a reminder on a future EPG entry, review the list, and fire a "watch now"
prompt when one comes due. Split out of main_window.py to keep the core window
smaller; every method operates on MainWindow state (self.reminders,
self.switch_mode, self.play_live_channel, ...) through the mixin, so behaviour
is identical to when these lived on MainWindow directly.
"""
from __future__ import annotations

import time

from ..i18n import tr


class _RemindersMixin:
    def _add_reminder(self, ch: dict, p: dict) -> None:
        """Set an EPG reminder for a future programme."""
        self.reminders.add(ch, p.get("title"), p.get("start_timestamp"))
        self._show_toast(
            tr("reminder_set", title=p.get("title") or ch.get("name") or ""),
            4000)

    def _offer_upcoming_actions(self, it) -> None:
        """A channel that isn't broadcasting yet (HTTP 407): offer to remind
        the user, record open-ended until it ends, or schedule a recording with
        a chosen time. Shown from the failed-playback path and the middle
        column's right-click menu."""
        if not it or it.get("stream_id") is None:
            return
        # The user can silence this automatic prompt (resettable in Settings >
        # Playback). The right-click channel menu still offers the same actions.
        if self.settings.value("hide_upcoming_prompt", "false") == "true":
            return
        idx = self._choice_dialog(
            tr("upcoming_title"),
            tr("upcoming_body", channel=it.get("name") or ""),
            [(tr("upcoming_remind"), "primary"),
             (tr("upcoming_record_stop"), "normal"),
             (tr("upcoming_schedule"), "normal"),
             (tr("common_cancel"), "normal")],
            dont_ask_setting="hide_upcoming_prompt")
        if idx == 0:
            self._remind_upcoming(it)
        elif idx == 1:
            self._record_now(it, None)
        elif idx == 2:
            self._schedule_recording(it)

    def _remind_upcoming(self, it) -> None:
        """Set a reminder for the next programme on a not-yet-live channel,
        looking its start time up from the short EPG."""
        from ..core.workers import run_async
        from ..providers.client import b64
        sid = it.get("stream_id")
        if sid is None:
            return

        def work():
            return self.client.short_epg(sid, limit=4)

        def done(epg):
            now = time.time()
            for e in (epg or []):
                try:
                    st = int(e.get("start_timestamp") or 0)
                except (TypeError, ValueError):
                    st = 0
                if st and st >= now - 600:
                    self._add_reminder(
                        it, {"title": b64(e.get("title")) or it.get("name"),
                             "start_timestamp": st})
                    return
            self._show_toast(tr("upcoming_no_epg"), 4000)

        run_async(self.pool, work, done,
                  lambda _e: self._show_toast(tr("upcoming_no_epg"), 4000))

    def _open_reminders(self) -> None:
        from .reminders import RemindersDialog
        RemindersDialog(self).exec()

    def _check_reminders(self) -> None:
        due = self.reminders.due(int(time.time()))
        if not due:
            return
        if len(due) == 1:
            self._fire_reminder(due[0])
        else:
            self._fire_reminders(due)

    def _fire_reminder(self, r: dict) -> None:
        ch = r.get("ch") or {}
        title = r.get("title") or ch.get("name") or ""
        idx = self._choice_dialog(
            tr("reminder_now_title"),
            tr("reminder_now_body", title=title,
               channel=ch.get("name") or ""),
            [(tr("reminder_watch_now"), "primary"),
             (tr("common_dismiss"), "normal")])
        if idx == 0 and ch.get("stream_id") is not None:
            self.switch_mode("live")
            self.play_live_channel(ch)

    def _fire_reminders(self, due: list) -> None:
        """Several reminders came due at once: one dialog listing them, each a
        button that tunes that channel - instead of stacked pop-ups."""
        options = []
        for r in due:
            t = r.get("title") or (r.get("ch") or {}).get("name") or "?"
            options.append((tr("reminder_watch_named", title=t), "primary"))
        options.append((tr("common_dismiss"), "normal"))
        idx = self._choice_dialog(
            tr("reminder_now_title"),
            tr("reminder_multi_body", n=len(due)), options)
        if idx is not None and idx < len(due):
            ch = (due[idx].get("ch") or {})
            if ch.get("stream_id") is not None:
                self.switch_mode("live")
                self.play_live_channel(ch)
