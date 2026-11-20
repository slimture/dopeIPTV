"""Reminders and scheduled recordings as a calendar, in the window
(core/ical.py writes it): "Export to calendar…" saves the file, and the
remote API (mw_remote) and the phone remote (mw_phone) serve it at an
address a calendar subscribes to - with their token, as everything they
serve. Every source's reminders go in, each under its source's name."""

from __future__ import annotations

from typing import Any

from ..core import ical
from ..i18n import tr


class _CalendarMixin:
    def _calendar_events(self) -> list[ical.Event]:
        store = getattr(self, "playlist_store", None)
        sources = [p for p in (store.playlists() if store is not None else []) if p.get("id")]
        active = store.active_id if store is not None else None
        note = tr("cal_reminder_note")
        events: list[ical.Event] = []
        if not any(p["id"] == active for p in sources):
            mine = self.reminders.all()
            events += ical.reminder_events(mine, "", "", note, self._calendar_guide_stops(mine))
        for pl in sources:
            if pl["id"] == active:
                rows = self.reminders.all()
                stops = self._calendar_guide_stops(rows)
            else:
                from ..services.reminders import ReminderStore
                rows, stops = ReminderStore(self.settings, pl["id"]).all(), {}
            events += ical.reminder_events(rows, str(pl["id"]), str(pl.get("name") or ""), note, stops)
        events += ical.recording_events(list(self.rec.jobs), tr("cal_recording_note"))
        return events

    def _calendar_guide_stops(self, reminders: list[dict]) -> dict[tuple[Any, int], float]:
        """Where the guide (the active source's) knows a reminded
        programme's end and the reminder does not."""
        out: dict[tuple[Any, int], float] = {}
        guide = getattr(self, "xmltv", None)
        for r in reminders:
            if r.get("stop") or guide is None:
                continue
            try:
                start = int(r.get("start") or 0)
                progs = guide.programmes_in(r.get("ch") or {}, start, start + 1)
            except Exception:
                continue
            for p in progs:
                if int(p.get("start_timestamp") or 0) == start and p.get("stop_timestamp"):
                    out[(r.get("stream_id"), start)] = float(p["stop_timestamp"])
                    break
        return out

    def _calendar_text(self) -> str:
        return ical.calendar(self._calendar_events(), name="dopeIPTV")

    def _calendar_counts(self) -> tuple[int, int]:
        """(reminders, recordings) in the calendar now."""
        events = self._calendar_events()
        reminders = sum(1 for e in events if e.alarm)
        return reminders, len(events) - reminders

    def _calendar_addresses(self) -> list[tuple[str, str]]:
        """(where it reaches, address) for each server that serves the
        calendar now: the remote API (with remote control on), the phone
        remote."""
        from ..core.phone_remote import calendar_url
        from ..core.remote_api import lan_addresses
        out: list[tuple[str, str]] = []
        srv = getattr(self, "_remote", None)
        if srv is not None and srv.remote:
            query = f"/calendar.ics?token={srv.token}"
            out.append((tr("cal_addr_local"), f"http://127.0.0.1:{srv.port}{query}"))
            if srv.host in ("0.0.0.0", "::"):
                out += [(tr("cal_addr_lan"), f"http://{ip}:{srv.port}{query}") for ip in lan_addresses()]
        phone = getattr(self, "_phone", None)
        if phone is not None:
            ips = lan_addresses()
            where = tr("cal_addr_lan") if ips else tr("cal_addr_local")
            # A calendar app cannot sign its requests: an address that opens
            # the calendar alone, never the remote (phone_remote.calendar_token).
            out.append((where, calendar_url(ips[0] if ips else "127.0.0.1", phone.port, phone.token)))
        return out

    def open_calendar_export(self) -> None:
        from .calendar_export import CalendarDialog
        CalendarDialog(self).exec()
