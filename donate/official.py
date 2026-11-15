"""Official builds only, for the services the app reaches on dopeiptv.org.

The app says what it is in X-dopeIPTV-Build: "official" (a release or test
build from the project's own workflows), "source" (run from a checkout) or
"modified" (a build made from changed code). Watch together's relay,
settings sync and feedback / error reports refuse "modified" with 403 and
words the app shows as they are (REFUSAL). Official apps from before the
header send none, so a missing header is never a reason to refuse; nor is a
value this does not know.

The header is the app's own word and proves nothing: a build that wants to
can say "official". It keeps the services and the inbox for the app as the
project ships it, nothing more.

Kept: how many requests were refused, per day and per service, in
refused-builds.json beside the statistics - no address, no room, no slot,
nothing else about a request. The service's log says the first refusal of
each day and the sum once a day (donate.py).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from typing import Callable

HEADER = "X-dopeIPTV-Build"
KINDS = ("official", "modified", "source")
#: The services that refuse a modified build, as the counts name them.
SERVICES = ("together", "sync", "feedback")
REFUSAL = {"error": "unofficial_build",
           "message": "This is not an official version of dopeIPTV. This service is only in the official app, "
                      "from dopeiptv.org."}
#: Days of counts kept (the admin page's "All" reaches back this far).
KEEP_DAYS = 800
#: A refusal is counted at once in memory and written out at most this often:
#: a modified build that asks in a loop costs no disk writes.
SAVE_EVERY = 10.0
FILE = "refused-builds.json"


def kind(value: str | None) -> str:
    """What the header says: one of KINDS, or "unknown" (none, or a word
    this does not know)."""
    v = (value or "").strip().lower()
    return v if v in KINDS else "unknown"


def refused(value: str | None) -> bool:
    return kind(value) == "modified"


class Refused:
    """The counts, per statistics folder: {day: {service: n}}."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._days: dict[str, dict[str, dict[str, int]]] = {}
        self._saved: dict[str, float] = {}
        self._dirty: set[str] = set()
        self._since: dict[str, int] = {}
        self._logged_day = ""

    def _today(self) -> str:
        return dt.date.fromtimestamp(self._clock()).isoformat()

    def _load(self, stats_dir: str) -> dict[str, dict[str, int]]:
        days = self._days.get(stats_dir)
        if days is not None:
            return days
        days = {}
        try:
            with open(os.path.join(stats_dir, FILE), encoding="utf-8") as fh:
                got = json.load(fh).get("days") or {}
            for day, row in got.items():
                dt.date.fromisoformat(day)
                days[day] = {s: int(n) for s, n in row.items() if s in SERVICES and int(n) > 0}
        except (OSError, ValueError, TypeError, AttributeError):
            pass
        self._days[stats_dir] = days
        return days

    def _save(self, stats_dir: str) -> None:
        days = self._days.get(stats_dir) or {}
        cut = (dt.date.fromtimestamp(self._clock()) - dt.timedelta(days=KEEP_DAYS)).isoformat()
        for day in [d for d in days if d < cut]:
            del days[day]
        path = os.path.join(stats_dir, FILE)
        try:
            os.makedirs(stats_dir, exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump({"days": days}, fh, sort_keys=True)
            os.replace(path + ".tmp", path)
        except OSError:
            return                          # kept in memory; the next one tries again
        self._saved[stats_dir] = self._clock()
        self._dirty.discard(stats_dir)

    def add(self, stats_dir: str, service: str) -> bool:
        """One refused request to *service*. True for the first one today
        (since the service started), worth a line in the log."""
        if service not in SERVICES:
            raise ValueError(service)
        with self._lock:
            days = self._load(stats_dir)
            today = self._today()
            row = days.setdefault(today, {})
            row[service] = row.get(service, 0) + 1
            self._since[service] = self._since.get(service, 0) + 1
            first, self._logged_day = self._logged_day != today, today
            self._dirty.add(stats_dir)
            if self._clock() - self._saved.get(stats_dir, 0.0) >= SAVE_EVERY:
                self._save(stats_dir)
        return first

    def flush(self, stats_dir: str | None = None) -> None:
        """Write out what is only in memory yet."""
        with self._lock:
            for where in [stats_dir] if stats_dir else list(self._dirty):
                if where in self._dirty:
                    self._save(where)

    def totals(self, stats_dir: str, days: int) -> dict:
        """The last *days* days (0: every day kept), as the admin page shows
        them: {"total", "by": {service: n}, "all"}."""
        with self._lock:
            kept = self._load(stats_dir)
            today = dt.date.fromtimestamp(self._clock())
            first = (today - dt.timedelta(days=days - 1)).isoformat() if days > 0 else ""
            by = dict.fromkeys(SERVICES, 0)
            every = 0
            for day, row in kept.items():
                n = sum(row.values())
                every += n
                if day >= first:
                    for s, v in row.items():
                        by[s] += v
            return {"total": sum(by.values()), "by": by, "all": every}

    def take(self) -> dict[str, int]:
        """The refusals since the last call, per service (the daily line)."""
        with self._lock:
            out, self._since = {s: n for s, n in self._since.items() if n}, {}
            return out


REFUSED = Refused()
