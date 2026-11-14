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
        inbox = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                inbox.got.append((self.path, dict(self.headers), json.loads(body)))
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
shown = []

def fake_exec(self):
    shown.append((self.kind, self.text.toPlainText(), self.include.isChecked()))
    if self.message is not None:
        self.message.setPlainText("The guide for Anna TV is empty")
    return 1
dialogs.ReportDialog.exec = fake_exec
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
for _ in range(50):
    app.processEvents(); w.pool.waitForDone(5)
    if len(inbox.got) == 2: break
kind, text, included = shown[-1]
assert kind == "feedback" and not included
sent = inbox.got[-1][2]
assert sent["kind"] == "feedback" and sent["text"] == "The guide for <private> is empty", sent
assert any("sent" in t.lower() or "skick" in t.lower() for t in toasts), toasts
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
