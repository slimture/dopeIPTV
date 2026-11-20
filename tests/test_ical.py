"""Reminders and scheduled recordings as iCalendar (core/ical.py), read the
way RFC 5545 has it: CRLF line endings, no line past 75 octets (a UTF-8
character never cut), text escaped, times in UTC, a UID per programme that
stays the same between exports, DTSTAMP, and the events a calendar needs."""
from __future__ import annotations

import http.client
import os
import re
import subprocess
import sys

import pytest

from dopeiptv.core import ical

NOW = 1_800_000_000           # 2027-01-15T08:00:00Z


def props(text: str) -> list[tuple[str, str]]:
    out = []
    for line in ical.unfold(text):
        if line:
            name, _sep, value = line.partition(":")
            out.append((name, value))
    return out


def events(text: str) -> list[dict[str, str]]:
    found, cur = [], None
    for name, value in props(text):
        if (name, value) == ("BEGIN", "VEVENT"):
            cur = {}
        elif (name, value) == ("END", "VEVENT"):
            found.append(cur)
            cur = None
        elif cur is not None and name not in cur:
            cur[name] = value
    return found


def test_text_is_escaped():
    assert ical.escape("News; sport, weather\\more\nline") == "News\\; sport\\, weather\\\\more\\nline"
    assert ical.escape("a\r\nb\rc") == "a\\nb\\nc"
    assert ical.escape("bell\x07 and nul\x00") == "bell and nul"
    assert ical.escape(None) == ""


def test_long_lines_are_folded_at_75_octets_never_inside_a_character():
    line = "SUMMARY:" + "Nyheterna – 東京 Ünïcödé " * 12
    folded = ical.fold(line)
    parts = folded.split("\r\n")
    assert len(parts) > 3
    assert all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert all(p.startswith(" ") for p in parts[1:])
    assert "".join([parts[0]] + [p[1:] for p in parts[1:]]) == line
    assert ical.fold("SHORT:x") == "SHORT:x"
    exact = "X:" + "a" * 73
    assert ical.fold(exact) == exact and ical.fold(exact + "b") == exact + "\r\n b"


def test_times_are_utc():
    assert ical.utc(NOW) == "20270115T080000Z"
    assert ical.utc(NOW + 0.9) == "20270115T080000Z"


def test_a_calendar_with_a_reminder_and_recordings():
    reminders = [{"stream_id": 401, "ch": {"name": "TV4 HD", "stream_id": 401}, "title": "Nyheterna, kväll; del 2",
                  "start": NOW + 3600, "stop": NOW + 5400},
                 {"stream_id": 7, "ch": {"name": "SVT1"}, "title": "Sporten", "start": NOW + 7200},
                 {"stream_id": 9, "ch": {}, "title": "", "start": "not a time"}]
    jobs = [{"id": "abc123", "title": "Film", "channel": "SVT2", "start": NOW + 600, "stop": NOW + 8000,
             "status": "scheduled"},
            {"id": "open1", "title": "Open-ended", "channel": "", "start": NOW - 60, "stop": None,
             "status": "recording"},
            {"id": "gone", "title": "Done", "start": NOW - 9000, "stop": NOW - 100, "status": "done"},
            {"id": "late", "title": "Missed", "start": NOW - 9000, "stop": NOW - 100, "status": "scheduled"}]
    evs = (ical.reminder_events(reminders, "src-a", "Main", "Reminder",
                                stops={(7, NOW + 7200): NOW + 9000})
           + ical.recording_events(jobs, "Recording", now=NOW))
    text = ical.calendar(evs, now=NOW)
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    p = props(text)
    assert p[:3] == [("BEGIN", "VCALENDAR"), ("VERSION", "2.0"), ("PRODID", ical.PRODID)]
    assert ("METHOD", "PUBLISH") in p and ("CALSCALE", "GREGORIAN") in p and p[-1] == ("END", "VCALENDAR")
    evs_read = events(text)
    assert [e["SUMMARY"] for e in evs_read] == ["Open-ended", "Film", "Nyheterna\\, kväll\\; del 2", "Sporten"]
    for e in evs_read:
        assert e["DTSTAMP"] == "20270115T080000Z"
        assert re.fullmatch(r"\d{8}T\d{6}Z", e["DTSTART"])
        assert re.fullmatch(r"[0-9a-f]{24}@dopeiptv", e["UID"])
    news = evs_read[2]
    assert (news["DTSTART"], news["DTEND"]) == ("20270115T090000Z", "20270115T093000Z")
    assert news["LOCATION"] == "TV4 HD" and news["DESCRIPTION"] == "Reminder · TV4 HD · Main"
    assert evs_read[3]["DTEND"] == "20270115T103000Z"            # the guide's end
    assert "DTEND" not in evs_read[0]                              # open at the end
    assert len({e["UID"] for e in evs_read}) == 4
    # The reminders have an alarm at their start, the recordings none.
    assert text.count("BEGIN:VALARM") == 2 and text.count("TRIGGER;RELATED=START:PT0S") == 2
    # The same programmes another time: the same UIDs (a subscription updates, never doubles).
    again = events(ical.calendar(ical.reminder_events(reminders, "src-a") + ical.recording_events(jobs, now=NOW),
                                 now=NOW + 99))
    assert [e["UID"] for e in again] == [e["UID"] for e in evs_read]
    # Another source's reminder of the same channel and time is another event.
    other = ical.reminder_events(reminders[:1], "src-b")
    assert other[0].uid != ical.reminder_events(reminders[:1], "src-a")[0].uid


def test_an_empty_calendar_is_still_a_calendar():
    text = ical.calendar([], name="dopeIPTV; mine", now=NOW)
    assert "BEGIN:VEVENT" not in text and "X-WR-CALNAME:dopeIPTV\\; mine\r\n" in text
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT15M\r\n" in text


# -- served by the remote API ---------------------------------------------------------------

def test_the_remote_api_serves_it_with_the_token_and_remote_control_on():
    from dopeiptv.core.remote_api import RemoteServer
    token = "cal-token-0123456789abcdef"
    text = ical.calendar([], now=NOW)

    def get(port, path, auth=True):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers={"Authorization": f"Bearer {token}"} if auth else {})
        r = c.getresponse()
        body = r.read()
        c.close()
        return r.status, r.getheader("Content-Type"), body
    on = RemoteServer("127.0.0.1", 0, token, lambda a, p: {}, calendar=lambda: text)
    off = RemoteServer("127.0.0.1", 0, token, lambda a, p: {}, share=object(), remote=False,
                       calendar=lambda: text)
    on.start()
    off.start()
    try:
        code, ctype, body = get(on.port, f"/calendar.ics?token={token}", auth=False)
        assert code == 200 and ctype == "text/calendar; charset=utf-8" and body.decode() == text
        assert get(on.port, "/calendar.ics", auth=False)[0] == 401
        assert get(off.port, "/calendar.ics")[0] == 404            # sharing alone: no calendar
    finally:
        on.stop()
        off.stop()


# -- the window ---------------------------------------------------------------------------------

_CHILD = r"""
import faulthandler, json, os, sys, tempfile, time, http.client, threading
faulthandler.dump_traceback_later(150, exit=True)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])
from dopeiptv.core import ical, nerd
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.services.reminders import ReminderStore
from dopeiptv.ui import main_window as mw
from dopeiptv.ui.calendar_export import CalendarDialog
from dopeiptv.ui.reminders import RemindersDialog

def _failed(*exc):
    import traceback
    traceback.print_exception(*exc)
    sys.stderr.flush()
    os._exit(1)
sys.excepthook = _failed

s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "a", "name": "Main", "kind": "m3u", "server": "http://127.0.0.1:9/a.m3u"},
                                    {"id": "b", "name": "Other", "kind": "m3u", "server": "http://127.0.0.1:9/b.m3u"}]))
s.setValue("active_playlist", "a")
w = mw.MainWindow(DemoClient(), s, PlaylistStore(s))
w._show_toast = lambda *a, **k: None
t = int(time.time()) + 3600
w._add_reminder({"name": "TV4 HD", "stream_id": 401}, {"title": "Nyheterna", "start_timestamp": t,
                                                      "stop_timestamp": t + 1800})
assert w.reminders.all()[0]["stop"] == t + 1800
ReminderStore(s, "b").add({"name": "SVT1", "stream_id": 7}, "Sporten", t + 7200)
w.rec.add_job("http://127.0.0.1:9/x.ts", "Film", t, t + 5400, channel="SVT2")
text = w._calendar_text()
lines = ical.unfold(text)
assert lines.count("BEGIN:VEVENT") == 3, text
assert "SUMMARY:Nyheterna" in lines and "SUMMARY:Sporten" in lines and "SUMMARY:Film" in lines
assert any(l.startswith("DESCRIPTION:") and "Other" in l for l in lines)
assert w._calendar_counts() == (2, 1)
assert w._calendar_addresses() == []

# The dialog: the counts, a file, and no address while nothing serves it.
d = CalendarDialog(w)
assert "2" in d.counts.text() and "1" in d.counts.text() and d.fields == []
path = os.path.join(tempfile.mkdtemp(), "dopeIPTV.ics")
assert d.write(path)
raw = open(path, "rb").read()
assert raw.startswith(b"BEGIN:VCALENDAR\r\n") and raw.endswith(b"END:VCALENDAR\r\n") and b"\r\n\r\n" not in raw
d.close()

# The remote API on: an address on this computer, with the token, that a calendar can read.
s.setValue("nerd_enabled", "true")
nerd.install(s)
st = w._remote_apply({"remote": True, "share": False, "port": 0, "host": "127.0.0.1", "token": ""})
assert st["running"], st
addrs = w._calendar_addresses()
assert len(addrs) == 1 and addrs[0][1] == f"http://127.0.0.1:{w._remote.port}/calendar.ics?token={w._remote.token}"
out = {}
def get():
    c = http.client.HTTPConnection("127.0.0.1", w._remote.port, timeout=10)
    c.request("GET", addrs[0][1].split(str(w._remote.port), 1)[1])
    r = c.getresponse(); out["code"] = r.status; out["body"] = r.read(); c.close()
th = threading.Thread(target=get); th.start()
while th.is_alive():
    app.processEvents(); th.join(0.01)
assert out["code"] == 200 and b"SUMMARY:Film" in out["body"], out
d = CalendarDialog(w)
assert [f.text() for f in d.fields] == [addrs[0][1]]
d.close()
# The phone remote too.
assert w.phone_remote_start()
from dopeiptv.core.phone_remote import calendar_token
assert any(u.endswith(f"/c/{calendar_token(w._phone.token)}/calendar.ics") for _w, u in w._calendar_addresses())
assert not any(w._phone.token in u for _w, u in w._calendar_addresses()), "the remote's own key never"
w.phone_remote_stop()
w._remote_stop()

# The reminders' own window offers it as well.
r = RemindersDialog(w)
assert r.calendar_btn.isVisibleTo(r)
r.close()
print("CAL_WINDOW_OK", flush=True)
os._exit(0)
"""


def test_the_window_exports_and_serves_the_calendar(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"):
        env[var] = str(tmp_path / var.lower())
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out = subprocess.run([sys.executable, "-c", _CHILD], cwd=root, env=env,
                         capture_output=True, text=True, timeout=180)
    assert "CAL_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-4000:]
