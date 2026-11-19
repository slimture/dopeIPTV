"""What a web page, a link or another program can make the app do.

Any page can open a dopeiptv:// link with one click on "open", so a link
is read narrowly: nothing after it on the command line (a quote in a link
ends its argument on Windows, and what follows would be options of the
app's or of Qt's), an address only after a question in the window, never a
file. And whoever hands a command over - a second start's message too - it
is checked before the window does anything.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types

import pytest

from dopeiptv.core import commands
from dopeiptv.core.commands import checked, own_arguments, parse_command, parse_link

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# -- nothing after a link is read -------------------------------------------------

@pytest.mark.parametrize("argv, want", [
    # As Windows splits  "dopeiptv.exe" "dopeiptv://open" --server ""  -
    # a link with a quote in it.
    (["dopeiptv://open", "--server", ""], ["dopeiptv://open"]),
    (["dopeiptv://open", "-platformpluginpath", r"\\evil\share"], ["dopeiptv://open"]),
    (["DopeIPTV:x", "--play", "/etc/passwd"], ["DopeIPTV:x"]),
    (["dopeiptv:", "--self-check"], ["dopeiptv:"]),
    # What stands before the link is the menu entry's or the person's own.
    (["--tv", "dopeiptv://channel/SVT1", "--server"], ["--tv", "dopeiptv://channel/SVT1"]),
    (["--no-x11", "dopeiptv://open"], ["--no-x11", "dopeiptv://open"]),
    # No link: the command line is the person's own, all of it.
    (["--server", "--port", "8000"], ["--server", "--port", "8000"]),
    (["--play", "http://h/x.ts", "--tv"], ["--play", "http://h/x.ts", "--tv"]),
    ([], []),
])
def test_a_start_by_a_link_reads_nothing_after_the_link(argv, want):
    assert own_arguments(argv) == want


def test_what_follows_a_link_is_no_command():
    assert parse_command(["dopeiptv://channel/SVT1", "--tv"]) == {"channel": "SVT1"}
    assert parse_command(["--tv", "dopeiptv://channel/SVT1"]) == {"channel": "SVT1", "tv": True}
    assert parse_command(["dopeiptv://open", "--play", "/etc/passwd"]) == {"open": True}
    assert parse_command(["dopeiptv://nothing", "http://evil.example/x.ts"]) is None


def test_a_link_never_starts_server_mode(monkeypatch):
    from dopeiptv import launcher
    called: list = []
    monkeypatch.setitem(sys.modules, "dopeiptv.headless", types.SimpleNamespace(
        main=lambda argv: called.append(("server", list(argv))) or 0))
    monkeypatch.setitem(sys.modules, "dopeiptv.app", types.SimpleNamespace(
        main=lambda: called.append(("window", list(sys.argv[1:]))) or 0))
    monkeypatch.setattr(sys, "argv", ["dopeiptv", "dopeiptv://open", "--server", "--port", "9"])
    assert launcher.main() == 0
    assert called == [("window", ["dopeiptv://open"])]
    # The person's own command line still does.
    called.clear()
    monkeypatch.setattr(sys, "argv", ["dopeiptv", "--server", "--port", "9"])
    assert launcher.main() == 0
    assert called == [("server", ["--port", "9"])]


def test_the_bundle_s_entry_drops_what_follows_a_link_before_it_reads_options():
    script = open(os.path.join(_REPO_ROOT, "dopeiptv.py"), encoding="utf-8").read()
    cut = script.index("sys.argv[1:] = own_arguments(sys.argv[1:])")
    assert cut < script.index('if "--server" in sys.argv[1:]:')
    assert cut < script.index("maybe_preload_allocator()")
    assert cut < script.index("from dopeiptv.app import main")


# -- a link is read narrowly ------------------------------------------------------

@pytest.mark.parametrize("link", [
    "dopeiptv://[",                                   # no address at all: was a crash
    "dopeiptv://play?url=http%3A%2F%2F%5B%3A%3A1",    # a broken address inside
    "dopeiptv://play?url=http%3A%2F%2Fh%3Aport%2Fx",
    "dopeiptv://play?url=http%3A%2F%2F%2Fno-server",
    # Drawn as another address, or over several lines: not shown, not played.
    "dopeiptv://play?url=http%3A%2F%2Fgood.example%E2%80%AE%2Fx",
    "dopeiptv://play?url=http%3A%2F%2Fh%2Fa%0Ab",
    "dopeiptv://play?url=http%3A%2F%2Fh%2Fa%20b",
    "dopeiptv://play?url=http%3A%2F%2Fh%2F" + "a" * (commands.MAX_ADDRESS + 1),
    # mpv's own schemes are no streams.
    "dopeiptv://play?url=av%3A%2F%2Fv4l2%3A%2Fdev%2Fvideo0",
    "dopeiptv://play?url=edl%3A%2F%2F%2Fetc%2Fpasswd",
    "dopeiptv://channel/" + "x" * (commands.MAX_NAME + 1),
])
def test_a_link_that_is_refused(link):
    assert parse_link(link) is None


def test_a_link_s_words_are_one_plain_line():
    assert parse_link("dopeiptv://channel/SVT1%0A%00HD") == {"channel": "SVT1 HD"}
    assert parse_link("dopeiptv:channel/TV4") == {"channel": "TV4"}
    got = parse_link("dopeiptv://play?url=udp%3A%2F%2F%40239.0.0.1%3A1234")
    assert got == {"play": "udp://@239.0.0.1:1234", "link": True}


def test_the_question_shows_the_server_and_the_whole_address():
    assert commands.host_of("http://user:pw@evil.example:8080/live/1.ts") == "evil.example:8080"
    assert commands.host_of("https://example.org/x") == "example.org"
    assert commands.host_of("rtsp://[fd00::5]:554/s") == "[fd00::5]:554"
    assert commands.host_of("http:///x") == "" and commands.host_of("http://h:x/") == ""
    url = "http://example.org/live/abc/def.m3u8?token=1&x=2"
    assert commands.shown(url).replace("\u200b", "") == url     # nothing but places to wrap
    assert "\u200b" in commands.shown(url)
    long = "http://example.org/" + "a" * 1500 + "/end.ts"
    shown = commands.shown(long).replace("\u200b", "")
    assert len(shown) <= commands.SHOWN and shown.startswith("http://example.org/")
    assert shown.endswith("/end.ts") and " … " in shown
    # No run of letters a label could not wrap.
    import re
    assert max(len(part) for part in re.split("[\u200b ]", commands.shown(long))) <= 48


# -- every command is checked, whoever hands it over ---------------------------------

@pytest.mark.parametrize("cmd, want", [
    ({"play": "http://h/x.ts"}, {"play": "http://h/x.ts"}),
    ({"play": "/home/u/film.mkv"}, {"play": "/home/u/film.mkv"}),
    ({"play": "file:///home/u/film.mkv"}, {"play": "file:///home/u/film.mkv"}),
    ({"play": "http://h/x.ts", "link": True}, {"play": "http://h/x.ts", "link": True}),
    ({"channel": " SVT1\tHD "}, {"channel": "SVT1 HD"}),
    ({"number": 7}, {"number": 7}),
    ({"number": "7"}, {"number": "7"}),
    ({"open": True, "tv": True}, {"open": True, "tv": True}),
    ({"supporter": "DS1-abcdefghijklmnopqrstuv"}, {"supporter": "DS1-abcdefghijklmnopqrstuv"}),
    ({"open": True, "anything": {"else": 1}}, {"open": True}),
    ({}, {}),
    # A file never from a link; no relative path, no scheme of mpv's own.
    ({"play": "/etc/passwd", "link": True}, None),
    ({"play": "file:///etc/passwd", "link": True}, None),
    ({"play": "film.mkv"}, None),
    ({"play": "av://v4l2:/dev/video0"}, None),
    ({"play": "edl://x"}, None),
    ({"play": "memory://#EXTM3U"}, None),
    ({"play": "smb://nas/films/a.mkv"}, None),
    ({"play": ["http://h/x.ts"]}, None),
    ({"play": "http://h/" + "a" * commands.MAX_OWN}, None),
    ({"play": "/home/u/a\x00b.mkv"}, None),
    ({"channel": "x" * (commands.MAX_NAME + 1)}, None),
    ({"channel": {"name": "SVT1"}}, None),
    ({"number": True}, None),
    ({"number": [1]}, None),
    ({"supporter": "hello"}, None),
    ("play", None),
    ([{"play": "http://h/x.ts"}], None),
    (None, None),
])
def test_a_command_is_checked(cmd, want):
    assert checked(cmd) == want


# -- the window: a link's address is asked about first --------------------------------

class _Favs:
    def __init__(self, urls=()):
        self.urls = set(urls)

    def is_favorite(self, url):
        return url in self.urls


def _host(answer, favs=()):
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_commands import _CommandsMixin

    class Host(_CommandsMixin):
        def __init__(self):
            self.played, self.asked, self.forward = [], [], 0
            self.web_favs = _Favs(favs)

        def _come_forward(self):
            self.forward += 1

        def _play_address(self, url, title=None):
            self.played.append(url)

        def _choice_dialog(self, title, message, options, dont_ask_setting=None):
            self.asked.append((title, message, [label for label, _kind in options],
                               getattr(self, "_phone_ask_hook", None)))
            return answer(self) if callable(answer) else answer
    return Host()


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _spin(app, n=5):
    for _ in range(n):
        app.processEvents()


def test_a_link_s_address_plays_only_after_yes_in_the_window(app):
    from dopeiptv.i18n import tr
    url = "http://evil.example:8080/live/a/b/1.ts?t=1"
    h = _host(1)                                            # Cancel
    assert h.handle_command(parse_link("dopeiptv://play?url=" + url.replace(":", "%3A")
                                       .replace("/", "%2F").replace("?", "%3F")
                                       .replace("=", "%3D")))["ok"] is True
    assert h.played == [] and h.asked == []                 # asked after the call returns
    _spin(app)
    assert h.played == [] and len(h.asked) == 1
    title, message, labels, _hook = h.asked[0]
    assert title == tr("link_play_title")
    assert "evil.example:8080" in message and url in message.replace("\u200b", "")
    assert labels == [tr("open_address_play"), tr("common_cancel")]
    # The window closed without an answer: not played either.
    h = _host(None)
    h.handle_command({"play": url, "link": True})
    _spin(app)
    assert h.played == [] and len(h.asked) == 1
    # Play.
    h = _host(0)
    h.handle_command({"play": url, "link": True})
    _spin(app)
    assert h.played == [url] and len(h.asked) == 1


def test_the_person_s_own_address_plays_without_a_question(app):
    url = "http://my.example/cam.m3u8"
    # --play from a terminal, the remote API: no link, no question.
    h = _host(1)
    assert h.handle_command({"play": url}) == {"ok": True}
    assert h.played == [url] and h.asked == []
    # A link to an address kept among the favourites: theirs, played.
    h = _host(1, favs=[url])
    h.handle_command({"play": url, "link": True})
    _spin(app)
    assert h.played == [url] and h.asked == []
    # A channel by its name, the window forward: as before.
    h = _host(1)
    named = []
    h._play_channel_named = named.append
    assert h.handle_command(parse_link("dopeiptv://channel/SVT1%20HD")) == {"ok": True}
    assert named == ["SVT1 HD"] and h.asked == []
    assert h.handle_command(parse_link("dopeiptv://open")) == {"ok": True}


def test_the_question_is_the_computer_s_and_one_at_a_time(app):
    url = "http://evil.example/x.ts"
    seen = []

    def answer(host):
        # A second link while the first is asked about: left, no pile.
        host.handle_command({"play": "http://other.example/y.ts", "link": True})
        _spin(app)
        seen.append(len(host.asked))
        return 1
    h = _host(answer)
    h._phone_ask_hook = phone = object()                    # a phone request runs
    h.handle_command({"play": url, "link": True})
    _spin(app)
    assert seen == [1] and len(h.asked) == 1
    assert h.asked[0][3] is None                            # not the phone's question
    assert h._phone_ask_hook is phone                       # and the phone's is back
    assert h.played == []


def test_a_command_that_is_none_does_nothing(app):
    h = _host(0)
    for cmd in ({"play": "av://lavfi:testsrc"}, {"play": "/etc/passwd", "link": True},
                {"play": "film.mkv"}, "play", None):
        assert h.handle_command(cmd) == {"ok": False}
    _spin(app)
    assert h.played == [] and h.asked == [] and h.forward == 0


def test_nothing_of_a_link_plays_while_who_is_watching_waits(app):
    h = _host(0)
    h._profile_locked = lambda: True
    assert h.handle_command({"play": "http://h/x.ts", "link": True}) == {"ok": False, "locked": True}
    _spin(app)
    assert h.played == [] and h.asked == []


# -- a second start's message -----------------------------------------------------------

@pytest.fixture
def socket_name(monkeypatch):
    pytest.importorskip("PyQt6.QtNetwork")
    d = tempfile.mkdtemp(prefix="dpo")
    name = os.path.join(d, "s")
    monkeypatch.setenv("DOPEIPTV_INSTANCE_SOCKET", name)
    yield name
    shutil.rmtree(d, ignore_errors=True)


def _client(app, name):
    from PyQt6.QtNetwork import QLocalSocket
    sock = QLocalSocket()
    sock.connectToServer(name)
    assert sock.waitForConnected(2000)
    return sock


def _until(app, test, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not test():
        app.processEvents()
        time.sleep(0.01)
    return test()


def test_a_connection_that_sends_no_command_is_closed(app, socket_name):
    from PyQt6.QtNetwork import QLocalSocket

    from dopeiptv.core import instance
    got: list[dict] = []
    server = instance.CommandServer(got.append)
    assert server.listening
    try:
        gone = QLocalSocket.LocalSocketState.UnconnectedState
        # Bytes without an end: not kept in memory, the connection closed.
        sock = _client(app, socket_name)
        sock.write(b"x" * (instance.MAX_MESSAGE + 4096))
        sock.flush()
        assert _until(app, lambda: sock.state() == gone)
        # One line too long to be a command, then a real one: neither taken.
        sock = _client(app, socket_name)
        sock.write(b'{"play": "http://h/' + b"a" * instance.MAX_MESSAGE + b'"}\n{"open": true}\n')
        sock.flush()
        assert _until(app, lambda: sock.state() == gone)
        # More lines than a start ever sends.
        sock = _client(app, socket_name)
        sock.write(b'{"open": true}\n' * (instance.MAX_MESSAGES + 5))
        sock.flush()
        assert _until(app, lambda: sock.state() == gone)
        assert got == [{"open": True}] * instance.MAX_MESSAGES
        # A second start's own message goes through as ever.
        got.clear()
        sock = _client(app, socket_name)
        sock.write(json.dumps({"channel": "SVT1 HD"}).encode() + b"\n")
        sock.flush()
        assert _until(app, lambda: got == [{"channel": "SVT1 HD"}])
        sock.disconnectFromServer()
    finally:
        server.close()


def _start(argv, env):
    return subprocess.Popen(
        [sys.executable, "-c",
         "import sys\n"
         f"sys.argv = {['dopeiptv'] + argv!r}\n"
         "from dopeiptv.app import main\n"
         "sys.exit(main())"],
        cwd=_REPO_ROOT, env=env)


@pytest.mark.parametrize("argv, want", [
    # A link with more after it (a quote in it, on Windows): the link alone.
    (["dopeiptv://channel/SVT1", "--self-check"], {"channel": "SVT1"}),
    # A link that asks nothing known: the window forward, no second copy.
    (["dopeiptv://rm?path=/"], {"open": True}),
    (["dopeiptv://play?url=file%3A%2F%2F%2Fetc%2Fpasswd"], {"open": True}),
    (["dopeiptv://play?url=http%3A%2F%2Fh%2Fx.ts"], {"play": "http://h/x.ts", "link": True}),
])
def test_a_start_by_a_link_hands_the_link_over_and_nothing_else(app, socket_name, argv, want):
    from dopeiptv.core.instance import CommandServer
    got: list[dict] = []
    server = CommandServer(got.append)
    second = _start(argv, dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    try:
        end = time.monotonic() + 60
        while time.monotonic() < end and (not got or second.poll() is None):
            app.processEvents()
            time.sleep(0.01)
        assert second.wait(30) == 0
    finally:
        if second.poll() is None:
            second.kill()
        server.close()
    assert got == [want]


# -- dropped on the window ---------------------------------------------------------------

class _Mime:
    def __init__(self, urls, text=""):
        self._urls, self._text = urls, text

    def urls(self):
        return self._urls

    def hasText(self):
        return bool(self._text)

    def text(self):
        return self._text


class _Drop:
    def __init__(self, mime):
        self._mime, self.accepted = mime, False

    def mimeData(self):
        return self._mime

    def acceptProposedAction(self):
        self.accepted = True


def _dropper(locked=False):
    pytest.importorskip("PyQt6.QtWidgets")
    from dopeiptv.ui.mw_queue import _QueueMixin

    class Host(_QueueMixin):
        MEDIA_EXTS = (".mkv", ".mp3")

        def __init__(self):
            self.played, self.asked_pin = [], 0
            self.playlist_store = types.SimpleNamespace(add=lambda values: pytest.fail("added"))

        def _profile_locked(self):
            return locked

        def _play_local_path(self, path):
            self.played.append(path)

        def _kids_settings_unlock(self):
            self.asked_pin += 1
            return False
    return Host()


def test_nothing_dropped_plays_while_who_is_watching_waits(app, tmp_path):
    from PyQt6.QtCore import QUrl
    film = tmp_path / "Film.mkv"
    film.write_bytes(b"")
    mime = _Mime([QUrl.fromLocalFile(str(film))])
    h = _dropper(locked=True)
    for event in (h.dragEnterEvent, h.dropEvent):
        e = _Drop(mime)
        event(e)
        assert not e.accepted
    assert h.played == []
    h = _dropper()
    e = _Drop(mime)
    h.dragEnterEvent(e)
    assert e.accepted
    h.dropEvent(_Drop(mime))
    assert h.played == [str(film)]


def test_a_kids_profile_adds_no_source_by_a_drop_without_a_grown_up(app, monkeypatch):
    from dopeiptv.ui import mw_queue
    monkeypatch.setattr(mw_queue, "PlaylistDialog",
                        lambda *a, **k: pytest.fail("the Add source window opened"))
    h = _dropper()
    h._add_dropped_playlist(("m3u", "http://h/list.m3u", "", ""))
    assert h.asked_pin == 1
