"""Error reports and feedback: nothing in them identifies a stream or a
person, the person sees and edits all of it, nothing goes until Send - and
a native crash is written down and offered at the next start."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import report
from dopeiptv.core.log import log

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_nothing_that_identifies_a_stream_or_a_person_is_left():
    home = os.path.expanduser("~")
    text = "\n".join([
        "GET http://anna:pw-anna@iptv.example.com:8080/live/anna/pw-anna/401.ts failed",
        "player_api.php?username=anna&password=pw-anna&action=get_live_streams",
        "portal mac=00:1A:79:12:34:56 token=abcdef123456 at 192.168.1.20 and fe80::1:2:3:4",
        "mail anna.svensson+iptv@example.se",
        f'  File "{home}/dopeIPTV/dopeiptv/ui/main_window.py", line 42, in play',
        "playing TV4 HD from Anna's IPTV (Nyheterna)",
        "loopback 127.0.0.1:3673 stays",
    ])
    personal = report.personal_values(
        [{"name": "Anna's IPTV", "server": "http://iptv.example.com:8080", "username": "anna",
          "password": "pw-anna", "mac": "00:1A:79:12:34:56"}], ["TV4 HD"], ["Nyheterna"])
    out = report.scrub(text, personal)
    for gone in ("anna", "pw-anna", "iptv.example.com", "00:1A:79", "abcdef123456", "192.168.1.20",
                 "fe80::1", "anna.svensson", "TV4 HD", "Anna's IPTV", "Nyheterna", home):
        assert gone.lower() not in out.lower(), (gone, out)
    assert "http://<address>" in out and "<ip>" in out and "<email>" in out
    assert 'File "~/dopeIPTV/dopeiptv/ui/main_window.py", line 42, in play' in out   # still a traceback
    assert "127.0.0.1:3673" in out


def test_a_report_holds_versions_the_error_and_the_recent_log_scrubbed():
    log.warning("stream http://user:secret@h.example/live/u/p/1.ts did not start")
    text = report.build("error", trace="Traceback (most recent call last):\nValueError: boom",
                        personal=["Kanal Fem"])
    from dopeiptv import __version__
    assert text.startswith("Kind: error") and f"dopeIPTV {__version__}" in text
    assert "ValueError: boom" in text and "Recent log:" in text
    assert "secret" not in text and "h.example" not in text
    assert len(report.build("error", trace="x" * 100_000)) <= report.MAX_TEXT
    assert "Recent log:" not in report.build("feedback", with_log=False)


class Inbox:
    def __init__(self, status=200):
        self.got = []
        self.answers = {}            # reply hash -> answers, for /feedback/replies
        inbox = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if self.path.endswith("/replies"):
                    out = json.dumps({"replies": {h: inbox.answers[h] for h in body.get("codes", [])
                                                  if h in inbox.answers}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(out)))
                    self.end_headers()
                    self.wfile.write(out)
                    return
                inbox.got.append((self.path, dict(self.headers), body))
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/feedback/report"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_sent_as_the_person_left_it(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    inbox = Inbox()
    try:
        assert report.send("feedback", "Love the guide.", endpoint=inbox.url)
        path, headers, body = inbox.got[-1]
        from dopeiptv import __version__
        assert path == "/feedback/report" and body["text"] == "Love the guide."
        assert body["kind"] == "feedback" and body["version"] == __version__ and body["os"]
        assert headers["X-Dopeiptv-Report"] == "1" and headers["User-Agent"] == f"dopeIPTV/{__version__}"
        assert set(body) == {"kind", "version", "os", "text"}          # nothing else rides along
        report.send("whatever", "x", endpoint=inbox.url)
        assert inbox.got[-1][2]["kind"] == "feedback"
    finally:
        inbox.close()
    assert not report.send("error", "x", endpoint="http://127.0.0.1:9/none", timeout=3)


def test_the_crash_file_is_read_once_and_emptied(tmp_path):
    folder = str(tmp_path)
    assert report.take_last_crash(folder) == ""
    with open(report.crash_path(folder), "w") as fh:
        fh.write("Fatal Python error: Segmentation fault\n\nCurrent thread 0x1 (most recent call first):\n  File x")
    assert report.take_last_crash(folder).startswith("Fatal Python error")
    assert report.take_last_crash(folder) == ""
    with open(report.crash_path(folder), "w") as fh:
        fh.write("nothing to see")
    assert report.take_last_crash(folder) == ""


_CRASH = r"""
import sys
from dopeiptv import app
app._install_crash_hooks()
import ctypes
ctypes.string_at(0)        # a real segmentation fault
"""

_NEXT = r"""
from dopeiptv import app
from dopeiptv.core import report
app._install_crash_hooks()
text = report.pending_crash()
print("PENDING", "Fatal Python error" in text, "string_at" in text or "Current thread" in text)
print("AGAIN", report.pending_crash() == "")
"""


@pytest.mark.skipif(sys.platform == "win32", reason="no native crash handler on Windows")
def test_a_real_crash_is_offered_at_the_next_start(tmp_path):
    env = dict(os.environ, XDG_STATE_HOME=str(tmp_path), QT_QPA_PLATFORM="offscreen")
    crashed = subprocess.run([sys.executable, "-c", _CRASH], cwd=_REPO_ROOT, env=env,
                             capture_output=True, text=True, timeout=60)
    assert crashed.returncode != 0
    assert os.path.getsize(os.path.join(tmp_path, "dopeiptv", "logs", report.CRASH_FILE)) > 0
    nxt = subprocess.run([sys.executable, "-c", _NEXT], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=60)
    assert "PENDING True True" in nxt.stdout and "AGAIN True" in nxt.stdout, nxt.stdout + nxt.stderr[-2000:]
    # And a clean run after it leaves nothing to offer.
    again = subprocess.run([sys.executable, "-c", _NEXT], cwd=_REPO_ROOT, env=env,
                           capture_output=True, text=True, timeout=60)
    assert "PENDING False" in again.stdout


_WINDOW = r"""
import os, sys, json, tempfile
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, QTimer
from PyQt6.QtWidgets import QApplication
from dopeiptv.core import report
from dopeiptv.core.stores import PlaylistStore
from dopeiptv.providers.client import DemoClient
from dopeiptv.ui.main_window import MainWindow
from dopeiptv.ui import dialogs
from tests.test_report import Inbox

from dopeiptv import app as dopeiptv_app
dopeiptv_app._install_crash_hooks()          # as the app starts
app = QApplication.instance() or QApplication([])
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
s = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
s.setValue("playlists", json.dumps([{"id": "a", "name": "Anna TV", "kind": "m3u", "server": "http://anna-tv.example/l.m3u"}]))
s.setValue("active_playlist", "a")
w = MainWindow(DemoClient(), s, PlaylistStore(s))
toasts = []
w._show_toast = lambda text, ms=0: toasts.append(text)
inbox = Inbox()
report.ENDPOINT = inbox.url
report.REPLIES = inbox.url.replace("/report", "/replies")
shown = []

def fake_exec(self):
    shown.append((self.kind, self.text.toPlainText(), self.include.isChecked()))
    if self.message is not None:
        self.message.setPlainText("The guide for Anna TV is empty")
    return 1
dialogs.ReportDialog.exec = fake_exec
asked = []

def fake_feedback(self):
    # What the person picks: something does not work, in the guide, every
    # time; their words; this run's log stays attached, versions too.
    asked.append(self)
    # The second time, the answer to the first is shown at the top.
    if len(asked) == 2:
        assert self.answers_box is not None
        said = self.answers_box.widget().text()
        assert "1.5.1" in said and "The guide for &lt;private&gt; is empty" in said, said
    self.topic_chips["bug"].setChecked(True)
    self.area.setCurrentIndex(self.area.findData("guide"))
    self.message.setPlainText("The guide for Anna TV is empty")
    assert self.bug_row.isVisibleTo(self) and self.attach["log"].isChecked()
    assert self.send_btn.isEnabled()
    return 1
dialogs.FeedbackDialog.exec = fake_feedback
# An uncaught error in a slot: offered once, scrubbed, sent on Send.
def boom():
    raise RuntimeError("cannot open http://anna-tv.example/live/x/y/1.ts")
QTimer.singleShot(0, boom)
for _ in range(50):
    app.processEvents(); w.pool.waitForDone(5)
    if inbox.got: break
kind, text, included = shown[-1]
assert kind == "error" and included and "RuntimeError" in text, text
assert "anna-tv" not in text and "Anna TV" not in text, text
body = inbox.got[-1][2]
assert body["kind"] == "error" and "RuntimeError" in body["text"] and "anna-tv" not in body["text"]
# The same error again: not offered twice.
QTimer.singleShot(0, boom)
for _ in range(20):
    app.processEvents()
assert len(shown) == 1, shown
# Feedback from the menu: the person's words, scrubbed of their own source's name.
w._feedback_action.trigger()
for _ in range(100):
    app.processEvents(); w.pool.waitForDone(5)
    if len(inbox.got) == 2 and len(toasts) == 2: break
assert len(asked) == 1
sent = inbox.got[-1][2]
assert sent["kind"] == "feedback" and sent["text"].startswith("The guide for <private> is empty"), sent
assert "Log from this run (warnings, errors, guide" in sent["text"] and "dopeIPTV " in sent["text"], sent["text"][:400]
assert "anna-tv" not in sent["text"] and "Anna TV" not in sent["text"]
assert (sent["topic"], sent["area"], sent["often"]) == ("bug", "guide", "always"), sent
assert any("sent" in t.lower() or "skick" in t.lower() for t in toasts), toasts
# An answer in the app: the code stays here, only its hash went with it.
import hashlib
kept = report.tickets(s)
assert len(kept) == 1 and hashlib.sha256(kept[0]["c"].encode()).hexdigest() == sent["reply"], (kept, sent)
assert kept[0]["w"] == "The guide for <private> is empty"
inbox.answers[sent["reply"]] = [{"t": 1763100000, "code": "fixed", "version": "1.5.1"}]
w._ask_answers()
for _ in range(100):
    app.processEvents(); w.pool.waitForDone(5)
    if any("answered" in t for t in toasts): break
assert any("answered" in t for t in toasts), toasts
assert report.tickets(s)[0]["r"][0]["version"] == "1.5.1"
# Right after: not again within the minute - said, nothing opened or sent.
w._feedback_action.trigger()
assert len(asked) == 1 and len(inbox.got) == 2
assert "minute" in toasts[-1], toasts
# A while later, the same words again: not sent twice.
s.setValue("feedback_sent", "1")
w._feedback_action.trigger()
for _ in range(20):
    app.processEvents(); w.pool.waitForDone(5)
assert len(asked) == 2 and len(inbox.got) == 2, inbox.got
assert "already" in toasts[-1], toasts
inbox.close()
print("REPORT_WINDOW_OK")
"""


def test_the_window_offers_scrubs_and_sends_only_on_send():
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(var, None)
    out = subprocess.run([sys.executable, "-c", _WINDOW], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=120)
    assert "REPORT_WINDOW_OK" in out.stdout, out.stdout + out.stderr[-3000:]


@pytest.mark.parametrize("keep", ["21:05:03 INFO started", "std::string", "Qt::AlignLeft", "1.4.5", "a:b:c"])
def test_ordinary_text_is_left_alone(keep):
    assert report.scrub(keep) == keep


def test_on_a_mac_no_entry_is_left_for_a_second_app_menu():
    """Every app-menu entry without a standard role is folded into the real
    application menu on macOS (Send feedback… once was not: two dopeIPTV
    menus side by side)."""
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtGui import QAction
    from PyQt6.QtWidgets import QApplication, QMenu

    from dopeiptv.ui.main_window import fold_into_app_menu
    app = QApplication.instance() or QApplication([])  # noqa: F841
    menu = QMenu()
    plain = menu.addAction("Send feedback…")
    about = menu.addAction("About")
    about.setMenuRole(QAction.MenuRole.AboutRole)
    menu.addSeparator()
    fold_into_app_menu(menu)
    assert plain.menuRole() == QAction.MenuRole.ApplicationSpecificRole
    assert about.menuRole() == QAction.MenuRole.AboutRole


def test_feedback_waits_a_minute_and_stops_for_the_day():
    now = 1_000_000.0
    assert report.feedback_wait([], now) == ""
    assert report.feedback_wait([now - 20], now) == "wait"
    assert report.feedback_wait([now - 61], now) == ""
    day = [now - 3600 * i - 100 for i in range(report.FEEDBACK_PER_DAY)]
    assert report.feedback_wait(day, now) == "day"
    assert report.feedback_wait([t - 86400 for t in day], now) == ""
    assert report.sent_times("12,x,,15.5") == [12.0, 15.5]
    assert report.text_hash("Hello  World\n") == report.text_hash("hello world")


def test_only_the_short_answers_go_with_feedback():
    """topic/area/often travel as single words from a short list; anything
    else a caller passes stays home."""
    import json as _json
    got = {}

    def fake(url, body, timeout):
        got.update(_json.loads(body))
        return 200, b"{}"
    real, report._post = report._post, fake
    try:
        assert report.send("feedback", "hi", endpoint="http://127.0.0.1:9/x",
                           extra={"topic": "bug", "area": "guide", "often": "once",
                                  "name": "Anna", "topicx": "y", "area2": "z", "often_": "a b"})
    finally:
        report._post = real
    assert got["topic"] == "bug" and got["area"] == "guide" and got["often"] == "once"
    assert not {"name", "topicx", "area2", "often_"} & set(got)


def test_a_supporter_key_never_goes_in_a_report():
    from dopeiptv.core import supporter
    key = supporter.make(bytes(32))
    out = report.scrub(f"my key is {key} - does it work?")
    assert key not in out and "<supporter key>" in out


def test_only_the_part_of_the_log_that_matters_goes_with_feedback():
    """Warnings and errors with their neighbours, the lines of where it went
    wrong, the last lines; the same line over and over once, with a count."""
    lines = [f"21:00:{i:02d} INFO tmdb: poster {i}" for i in range(40)]
    lines += ["21:01:00 INFO EPG: loaded 300 channels"]
    lines += [f"21:01:{i:02d} INFO tmdb: poster {i}" for i in range(1, 20)]
    lines += ["21:02:00 INFO play: starting hls"]
    lines += [f"21:02:{i:02d} WARNING mpv: demuxer cache low ({i}%)" for i in range(1, 9)]
    lines += ["21:02:10 ERROR play: stream ended unexpectedly"]
    lines += [f"21:03:{i:02d} INFO tmdb: poster {i}" for i in range(30)]
    play = report.relevant_log(lines, "playback")
    assert "21:02:10 ERROR play: stream ended unexpectedly" in play
    assert "21:02:00 INFO play: starting hls" in play
    assert any("the same line ×8" in ln for ln in play), play
    assert "21:01:00 INFO EPG: loaded 300 channels" not in play
    assert len(play) < len(lines) / 3
    guide = report.relevant_log(lines, "guide")
    assert "21:01:00 INFO EPG: loaded 300 channels" in guide
    assert "…" in guide
    assert len(report.relevant_log(lines * 20, "playback", most=50)) <= 50
    # The log file's own form (a date and milliseconds) reads the same.
    assert report._parts("2026-11-14 21:02:10,123 ERROR play: x") == ("ERROR", "play: x")


def test_answers_in_the_apps_own_words():
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.dialogs import answer_text
    assert answer_text({"code": "fixed", "version": "1.5.1"}) == "This is fixed in version 1.5.1 - update dopeIPTV to get it."
    assert answer_text({"code": "fixed"}) == "This is fixed in the next version."
    assert answer_text({"code": "note", "text": "Try the other server"}) == "Try the other server"
    assert answer_text({"code": "thanks", "text": "Great idea!"}).endswith("noted. Great idea!")
    assert answer_text({"code": "nonsense"}) == ""


def test_codes_kept_here_and_old_ones_let_go():
    import time as _t

    class S(dict):
        def value(self, k, d=None):
            return self.get(k, d)

        def setValue(self, k, v):
            self[k] = v
    s = S()
    now = _t.time()
    report.save_tickets(s, [{"c": "a", "t": now - 5 * 86400},
                            {"c": "b", "t": now - 40 * 86400},
                            {"c": "c", "t": now - 40 * 86400, "r": [{"code": "thanks"}]},
                            {"c": "d", "t": now - 100 * 86400, "r": [{"code": "thanks"}]}])
    assert [t["c"] for t in report.tickets(s)] == ["a", "c"]
    code, digest = report.new_code()
    assert len(code) > 20 and report.code_hash(code) == digest and len(digest) == 64


def test_asking_for_answers_sends_only_hashes(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    inbox = Inbox()
    try:
        inbox.answers[report.code_hash("mine")] = [{"t": 1, "code": "known"}]
        got = report.fetch_replies(["mine", "other"], endpoint=inbox.url.replace("/report", "/replies"))
        assert got == {"mine": [{"t": 1, "code": "known"}]}
        assert report.fetch_replies(["x"], endpoint="http://127.0.0.1:9/replies", timeout=2) is None
    finally:
        inbox.close()


def test_a_long_report_is_cut_to_what_the_site_takes():
    """A long log with letters outside ASCII once made a report bigger than
    the site keeps; it goes as UTF-8, cut to fit, on a character boundary."""
    import json as _json
    sent = []

    def fake(url, body, timeout):
        sent.append(body)
        return 200, b"{}"
    real, report._post = report._post, fake
    try:
        assert report.send("feedback", "Någon åt äpplet – ж" * 4000)
    finally:
        report._post = real
    assert len(sent[0]) <= report.MAX_BODY
    data = _json.loads(sent[0].decode("utf-8"))
    assert data["text"].startswith("Någon åt äpplet") and "\\u00e5" not in sent[0].decode("utf-8")


def test_a_refused_report_is_not_called_sent():
    real, report._post = report._post, lambda url, body, timeout: (400, b'{"error": "empty"}')
    try:
        assert not report.send("feedback", "hello")
        assert report.fetch_replies(["x"]) is None
    finally:
        report._post = real
