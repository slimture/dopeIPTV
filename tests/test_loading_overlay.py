"""The loading indicator over the video (GitHub issue #27): a stream on its
way shows that it is coming - after a moment, so a quick start never
flashes it - with the logo the window holds, the name and "Connecting…",
"Still loading…" after a while, "Reconnecting…" for the app's reconnect,
"Buffering… 45%" when playback waits for its cache. It goes at once on
playback-restart, a stop, an error and the next stream; the spinner turns
only while it is on screen, and every click goes through it to the player.

The player's own checks run in a child process, as the other player tests
do: an offscreen QOpenGLWidget can take the interpreter down at exit."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QPoint, QRect, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QImage, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from dopeiptv.media.loading_overlay import LoadingOverlay, colours, contrast  # noqa: E402

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _logo(w=160, h=90) -> QPixmap:
    pm = QPixmap(w, h)
    pm.fill(QColor(51, 102, 204))
    return pm


def _wait(app, ms: float) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


class _Host(QWidget):
    """What lies under the indicator: it must get every click."""

    def __init__(self):
        super().__init__()
        self.pressed: list[QPoint] = []
        self.resize(640, 360)

    def mousePressEvent(self, event):       # noqa: N802 - Qt hook
        self.pressed.append(event.position().toPoint())


# -- the overlay itself --------------------------------------------------------------

def test_every_click_goes_through_it_and_it_takes_no_focus(app):
    from PyQt6.QtTest import QTest

    host = _Host()
    ov = LoadingOverlay(host)
    ov.set_content(_logo(), "Kanal 1", "Connecting…")
    ov.place(host.rect())
    host.show()
    ov.show()
    app.processEvents()
    centre = ov.geometry().center()
    assert ov.geometry().contains(centre) and ov.isVisible()
    assert host.childAt(centre) is None          # Qt delivers past it
    QTest.mouseClick(host, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
    QTest.mouseDClick(host, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
    assert host.pressed and all(p == centre for p in host.pressed)
    # Neither a click nor Tab ever lands on it.
    assert ov.focusPolicy() == Qt.FocusPolicy.NoFocus and not ov.hasFocus()
    host.focusNextChild()
    assert not ov.hasFocus()
    host.close()


def test_the_spinner_turns_only_while_it_is_on_screen(app):
    host = _Host()
    ov = LoadingOverlay(host)
    ov.set_content(None, "Kanal 1", "Connecting…")
    ov.place(host.rect())
    assert not ov._timer.isActive()
    host.show()
    ov.show()
    app.processEvents()
    assert ov._timer.isActive() and ov._timer.interval() <= 40
    ov.hide()
    assert not ov._timer.isActive()
    ov.show()
    assert ov._timer.isActive()
    host.hide()                       # TV mode's page over the player, say
    assert not ov._timer.isActive()
    host.show()
    app.processEvents()
    assert ov._timer.isActive()
    host.close()
    # Shown in something that is not on screen: it stops at its first tick.
    lone = QWidget()
    ov2 = LoadingOverlay(lone)
    ov2.show()
    ov2._tick()
    assert not ov2._timer.isActive()


@pytest.mark.parametrize("w, h", [(560, 326), (420, 240), (900, 506), (1920, 1080),
                                  (300, 150), (220, 124)])
def test_it_fits_the_picture_and_grows_with_it(app, w, h):
    host = QWidget()
    ov = LoadingOverlay(host)
    ov.set_content(_logo(), "Kanal 1 HD", "Still loading - some channels take a while to start")
    ov.place(QRect(0, 0, w, h))
    g = ov.geometry()
    assert QRect(0, 0, w, h).contains(g), (w, h, g)
    # The logo only where there is room for it; the spinner and the line always.
    assert (ov._geo.logo is not None) == (h >= 240)
    # Clear of the stream banner along the top (12 px in, about 33 high).
    if ov._geo.logo is not None:
        assert g.top() > 45, (w, h, g)
    assert ov._geo.note is not None and not ov.spinner_rect().isEmpty()
    host.deleteLater()


def test_full_screen_is_bigger_and_clear_of_tv_modes_info_bar(app):
    host = QWidget()
    ov = LoadingOverlay(host)
    ov.set_content(_logo(), "Kanal 1 HD", "Connecting…")
    ov.place(QRect(0, 0, 560, 326))
    docked = (ov.spinner_rect().width(), ov._geo.title_font.pixelSize(), ov.height())
    ov.place(QRect(0, 0, 1920, 1080))
    full = (ov.spinner_rect().width(), ov._geo.title_font.pixelSize(), ov.height())
    assert all(f > d for f, d in zip(full, docked, strict=True)), (docked, full)
    # TV mode's info bar: along the bottom, 5 % in, 300 px at 1080 lines.
    assert ov.geometry().bottom() < 1080 * 0.95 - 300
    # A percentage counting up does not make the plate jitter.
    ov.set_content(None, "Kanal 1 HD", "Buffering… 5%", reserve="Buffering… 100%")
    wide = ov.width()
    ov.set_content(None, "Kanal 1 HD", "Buffering… 45%", reserve="Buffering… 100%")
    assert ov.width() == wide
    host.deleteLater()


def _accent_x(img: QImage, rect, accent: QColor) -> float:
    xs = []
    for y in range(int(rect.top()), int(rect.bottom())):
        for x in range(int(rect.left()), int(rect.right())):
            c = img.pixelColor(x, y)
            if (abs(c.red() - accent.red()) + abs(c.green() - accent.green())
                    + abs(c.blue() - accent.blue())) < 60:
                xs.append(x)
    assert xs
    return sum(xs) / len(xs)


def test_the_spinner_turns_the_other_way_right_to_left(app):
    from dopeiptv.ui.theme import P

    host = QWidget()
    ov = LoadingOverlay(host)
    ov.set_content(None, "", "Connecting…")
    ov.place(QRect(0, 0, 640, 360))
    ov.elapsed = lambda: LoadingOverlay.TURN_MS // 4    # a quarter turn on
    r = ov.spinner_rect()
    mid = r.center().x()
    accent = QColor(P["accent"])
    ltr = ov.grab().toImage()
    ov.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    rtl = ov.grab().toImage()
    dpr = ltr.devicePixelRatio()
    scaled = QRectF(r.x() * dpr, r.y() * dpr, r.width() * dpr, r.height() * dpr)
    # Clockwise from the top it has reached the right; mirrored, the left.
    assert _accent_x(ltr, scaled, accent) > mid * dpr
    assert _accent_x(rtl, scaled, accent) < mid * dpr
    host.deleteLater()


def test_it_reads_over_black_in_every_theme(app):
    from dopeiptv.ui import theme

    try:
        for key in theme.THEMES:
            theme.apply_theme(theme=key)
            plate, text, muted = colours()
            over_black = QColor.fromRgbF(plate.redF() * plate.alphaF(),
                                         plate.greenF() * plate.alphaF(),
                                         plate.blueF() * plate.alphaF())
            assert contrast(text, over_black) >= 4.5, key
            assert contrast(muted, over_black) >= 4.5, key
            assert QColor(theme.P["accent"]).isValid()
    finally:
        theme.apply_theme()


def test_compact_rings_the_middle_of_a_cell(app):
    host = QWidget()
    ov = LoadingOverlay(host, compact=True)
    ov.place(QRect(0, 0, 640, 360))
    assert (ov.geometry().center() - QRect(0, 0, 640, 360).center()).manhattanLength() <= 2
    assert ov.width() == ov.height() and 36 < ov.width() < 120
    assert ov._timer.interval() >= LoadingOverlay.FRAME_MS
    assert ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    host.deleteLater()


# -- the player: driven by what mpv says ----------------------------------------------

_PLAYER_CHILD = r"""
import os, tempfile, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QSettings, Qt
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget
from dopeiptv.i18n import tr
from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
p = EmbeddedPlayer(settings=settings)
# Shortened, as the real ones are 0.3 s, 8 s and 1 s.
p.LOADING_SHOW_MS, p.LOADING_SLOW_MS, p.LOADING_WAIT_MS = 40, 250, 80
ov = p._ld_overlay


class FakeMpv(dict):
    pause = False
    core_idle = True
    playback_time = None

    def __init__(self):
        super().__init__()
        self.events, self.props = {}, {}

    def event_callback(self, name):
        def register(cb):
            self.events[name] = cb
            return cb
        return register

    def observe_property(self, name, cb):
        self.props[name] = cb

    def command(self, *args):
        pass

    def play(self, url):
        pass

    def __getattr__(self, n):
        return None


def wait(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.003)


def from_mpv(name, value=None, deliver=True):
    # On a thread of its own, as mpv's event thread calls python-mpv's.
    def run():
        if name in m.events:
            m.events[name](None)
        else:
            m.props[name](name, value)
    t = threading.Thread(target=run)
    t.start()
    t.join()
    if deliver:
        for _ in range(3):
            app.processEvents()


def shown():
    return not ov.isHidden()


m = p.video.mpv = FakeMpv()
logo = QPixmap(160, 90)
logo.fill(QColor(51, 102, 204))
url = "http://x/live/1.ts"

# A quick start never shows it.
p.set_loading_art(logo)
assert p.play(url, "Kanal 1", local_buffer=True)
assert set(m.events) == {"start-file", "playback-restart", "seek", "end-file"}, m.events
assert set(m.props) == {"paused-for-cache", "cache-buffering-state"}, m.props
assert not shown() and p._ld_phase == "start"
from_mpv("start-file")
from_mpv("playback-restart")
wait(100)
assert not shown() and p._ld_phase is None
# One core is followed once.
assert p.play(url, "Kanal 1", local_buffer=True)
assert len(m.events) == 4
from_mpv("start-file"); from_mpv("playback-restart")

# A slow one: after the delay, with its logo, its name and "Connecting…".
p.set_loading_art(logo)
assert p.play(url, "Kanal 1", local_buffer=True)
wait(15)
assert not shown()
wait(80)
assert shown()
assert (ov._title, ov._note) == ("Kanal 1", tr("player_loading_connecting")), (ov._title, ov._note)
assert ov._pixmap is logo
assert p.center_btn.isHidden()
# The last stream's ending and restart are not this one's.
from_mpv("end-file")
from_mpv("playback-restart")
assert shown() and p._ld_phase == "start"
# Still loading after a while.
wait(250)
assert ov._note == tr("player_loading_slow"), ov._note
# Its own start, from mpv's thread: nothing changes until the window's
# thread takes it.
from_mpv("start-file", deliver=False)
assert not p._ld_seen_start
for _ in range(3):
    app.processEvents()
assert p._ld_seen_start
from_mpv("playback-restart")
assert not shown() and p._ld_phase is None
assert not ov._timer.isActive() and not p._ld_slow_timer.isActive()

# Zapping on while it shows: it stays, for the next channel.
assert p.play(url, "Kanal 1", local_buffer=True)
wait(100)
assert shown()
p.set_loading_art(None)
assert p.play("http://x/live/2.ts", "Kanal 2", local_buffer=True)
assert shown() and ov._title == "Kanal 2" and ov._pixmap is None
assert ov._note == tr("player_loading_connecting")
from_mpv("start-file"); from_mpv("playback-restart")
assert not shown()

# Waiting for its cache while it plays: after a second, how full it is.
from_mpv("paused-for-cache", True)
from_mpv("cache-buffering-state", 45)
assert not shown()                       # a short stall does not flash it
wait(130)
assert shown() and ov._note == tr("player_loading_buffering_pct", n=45), ov._note
assert ov._title == "Kanal 2"
from_mpv("cache-buffering-state", 80)
assert ov._note == tr("player_loading_buffering_pct", n=80)
from_mpv("paused-for-cache", False)
assert not shown()
# A short stall never shows it.
from_mpv("paused-for-cache", True)
wait(20)
from_mpv("paused-for-cache", False)
wait(130)
assert not shown()

# A seek (a film, the rewind buffer): the same, until playback goes on.
from_mpv("seek")
wait(130)
assert shown() and ov._note == tr("player_loading_buffering"), ov._note
from_mpv("playback-restart")
assert not shown()
# dopeIPTV's own buffer opened again at another point: a file of its own.
from_mpv("end-file")
from_mpv("start-file")
wait(130)
assert shown()
from_mpv("playback-restart")
assert not shown()

# Stop: at once, nothing left running - and nothing comes back after it.
assert p.play(url, "Kanal 1", local_buffer=True)
wait(100)
assert shown()
p.stop()
assert not shown()
assert not any(t.isActive() for t in (p._ld_show_timer, p._ld_slow_timer,
                                       p._ld_wait_timer, ov._timer))
from_mpv("paused-for-cache", True)
from_mpv("start-file")
from_mpv("seek")
wait(130)
assert not shown()
from_mpv("paused-for-cache", False)

# An error: at once (the window says what went wrong), and still told.
errors = []
p.playback_error.connect(errors.append)
assert p.play(url, "Kanal 1", local_buffer=True)
wait(100)
assert shown()
p._on_playback_error("loading failed")
assert not shown() and errors == ["loading failed"]
# The window's own message about it takes it away too.
assert p.play(url, "Kanal 1", local_buffer=True)
wait(100)
p.hide_loading()
assert not shown()

# The app's reconnect says so, all the while; the next start is fresh.
p.set_loading_art(logo, reconnect=True)
assert p.play(url, "Kanal 1", local_buffer=True)
wait(330)
assert shown() and ov._note == tr("status_reconnecting"), ov._note
from_mpv("start-file"); from_mpv("playback-restart")
assert p.play(url, "Kanal 1", local_buffer=True)
wait(100)
assert ov._note == tr("player_loading_connecting") and ov._pixmap is None

# No restart came, but the position poll sees it play: gone.
m.core_idle, m.playback_time = False, 0.3
p._poll_position()
assert not shown() and p._ld_phase is None
m.core_idle, m.playback_time = True, None

# A stall's indicator stays through the app's reconnect, which says so.
from_mpv("paused-for-cache", True)
wait(130)
assert shown() and ov._note == tr("player_loading_buffering"), ov._note
p.set_loading_art(None, reconnect=True)
assert p.play(url, "Kanal 1", local_buffer=True)
assert shown() and ov._note == tr("status_reconnecting"), ov._note
assert not p._ld_buffering            # the stalled stream's wait is over
from_mpv("start-file"); from_mpv("playback-restart")
assert not shown()

# Paused, it gives way to the play button; going on, it is back.
assert p.play(url, "Kanal 1", local_buffer=True)
wait(100)
assert shown()
p._sync_pause_label(True)
assert not shown() and not p.center_btn.isHidden()
p._sync_pause_label(False)
assert shown() and p.center_btn.isHidden()
p._reveal_center()                     # a pointer move over the video
assert p.center_btn.isHidden()

# Over the picture, never on the GL surface; nothing in it takes a click
# or the keyboard.
assert ov.parent() is p
assert [c for c in p.video.children() if isinstance(c, QWidget)] == []
assert ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
assert ov.focusPolicy() == Qt.FocusPolicy.NoFocus

# The pop-out: over the mirror in its window, and back with the player.
host = QWidget()
host.resize(800, 450)
mirror = p.start_mirror(host)
assert ov.parent() is host and shown()
assert host.isAncestorOf(mirror)
p.stop_mirror()
assert ov.parent() is p and shown()
p.stop()
assert not shown()

# A broken indicator never stands in a stream's way: a core it cannot
# follow, a drawing that fails - the stream plays, and stops, as before.
def broken(*_a, **_k):
    raise RuntimeError("broken")
m2 = FakeMpv()
m2.event_callback = broken
p.video.mpv = m2
assert p.play(url, "Kanal 1", local_buffer=True) and p.current_url == url
p.video.mpv = m
ov.set_content = broken
assert p.play(url, "Kanal 1", local_buffer=True)
wait(100)
p._poll_position()
del ov.set_content
p.stop()
assert p.current_url is None and not shown()

# A play that fails takes nothing with it.
p.video.mpv = None
p.video.show = lambda: None
assert not p.play(url, "Kanal 1", local_buffer=True)
assert not shown() and p._ld_phase is None
print("LOADING_PLAYER_OK", flush=True)
os._exit(0)
"""


def _run(child: str, marker: str) -> None:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-c", child], cwd=_REPO_ROOT, env=env,
                         capture_output=True, text=True, timeout=180)
    assert marker in out.stdout, out.stdout + out.stderr[-3000:]


def test_the_player_shows_it_from_play_to_the_first_picture():
    _run(_PLAYER_CHILD, "LOADING_PLAYER_OK")


_POPOUT_CHILD = r"""
import os, tempfile, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPoint, QRect, QSettings
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, tempfile.mkdtemp())
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget
from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])
settings = QSettings(os.path.join(tempfile.mkdtemp(), "s.ini"), QSettings.Format.IniFormat)
p = EmbeddedPlayer(settings=settings)
p.LOADING_SHOW_MS = 20
ov = p._ld_overlay


class FakeMpv(dict):
    pause = False
    core_idle = True
    playback_time = None

    def event_callback(self, name):
        return lambda cb: cb

    def observe_property(self, name, cb):
        pass

    def command(self, *args):
        pass

    def play(self, url):
        pass

    def __getattr__(self, n):
        return None


def wait(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.003)


def over(surface):
    # Centred across the picture, its middle a little above the centre.
    area = QRect(surface.mapTo(ov.parentWidget(), QPoint(0, 0)), surface.size())
    g = ov.geometry()
    assert abs(g.center().x() - area.center().x()) <= 2, (g, area)
    assert abs(g.center().y() - (area.top() + area.height() * ov.MIDDLE)) <= 3, (g, area)


p.resize(640, 420)
p.show()
p.video.mpv = FakeMpv()
assert p.play("http://x/live/1.ts", "Kanal 1", local_buffer=True)
wait(80)
assert not ov.isHidden()
over(p.video)

# The pop-out's mirror, in the order the window builds it (_popout_macos):
# the indicator is up before the window is shown and the mirror laid out.
win = QWidget()
QVBoxLayout(win).setContentsMargins(0, 0, 0, 0)
mirror = p.start_mirror(win)
win.layout().addWidget(mirror, 1)
win.setGeometry(100, 100, 960, 540)
win.show()
mirror.show()
wait(60)
assert ov.parentWidget() is win and not ov.isHidden()
over(mirror)
p.stop_mirror()
wait(60)
over(p.video)
win.close()

# Windows' pop-out moves the player itself into a window of its own.
win2 = QWidget()
QVBoxLayout(win2).setContentsMargins(0, 0, 0, 0)
win2.layout().addWidget(p)
p.show()
win2.setGeometry(100, 100, 1280, 760)
win2.show()
wait(60)
assert not ov.isHidden()
over(p.video)
print("LOADING_POPOUT_OK", flush=True)
os._exit(0)
"""


def test_the_pop_out_puts_it_over_the_picture():
    # Shown before the pop-out window is: placed once the window lays the
    # picture out, not where the mirror sat before it had a size.
    _run(_POPOUT_CHILD, "LOADING_POPOUT_OK")


# -- a multiview cell: a small spinner while it starts --------------------------------

_CELL_CHILD = r"""
import os, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
from dopeiptv.ui.mw_multiview import _MultiviewCell

app = QApplication.instance() or QApplication([])


class FakeMpv(dict):
    pause = False
    core_idle = True
    time_pos = None

    def command(self, *args):
        pass

    def play(self, url):
        pass

    def __getattr__(self, n):
        return None


def wait(ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.003)


cell = _MultiviewCell(1)
cell.resize(640, 360)
m = cell.video.mpv = FakeMpv()
cell._loading_timer.setInterval(30)
spin = cell._loading
assert spin.parent() is cell.video and spin.compact
assert spin.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

assert cell.play("http://x/live/1.ts", "One")
assert spin.isHidden()
wait(80)
assert not spin.isHidden()
cell.refresh_state()                 # still opening
assert not spin.isHidden()
m.pause = True                       # the pause mark has the middle
cell.refresh_state()
assert spin.isHidden()
m.pause = False
cell.refresh_state()
assert not spin.isHidden()
m.core_idle, m.time_pos = False, 1.0
cell.refresh_state()                 # it plays
assert spin.isHidden() and not cell._loading_on
# A quick start never shows it.
m.core_idle, m.time_pos = True, None
assert cell.play("http://x/live/2.ts", "Two")
m.core_idle, m.time_pos = False, 0.2
cell.refresh_state()
wait(80)
assert spin.isHidden()
# An error, and a cleared cell, take it away.
m.core_idle, m.time_pos = True, None
assert cell.play("http://x/live/3.ts", "Three")
wait(80)
assert not spin.isHidden()
cell._on_error("loading failed")
assert spin.isHidden()
assert cell.play("http://x/live/3.ts", "Three")
wait(80)
cell.clear()
assert spin.isHidden() and not cell._loading_timer.isActive()
print("LOADING_CELL_OK", flush=True)
os._exit(0)
"""


def test_a_multiview_cell_spins_while_its_stream_starts():
    _run(_CELL_CHILD, "LOADING_CELL_OK")


# -- with a real mpv: the events it follows are the ones mpv sends ---------------------

def test_real_mpv_ends_it_when_the_picture_is_on(app):
    from pathlib import Path

    from PyQt6.QtCore import pyqtSignal

    from dopeiptv.media.loading_overlay import LoadingMixin
    from tests.test_nerd import _libmpv

    core = _libmpv()

    class Player(LoadingMixin, QWidget):
        _ld_event = pyqtSignal(str, object)

    path = str(Path(__file__).parent / "data" / "tiny.ts")
    p = Player()
    p.current_url = path
    p._paused = False
    p.video = SimpleNamespace(mpv=core)
    p._ov_surface = QWidget(p)
    p._ov_surface.resize(640, 360)
    p._init_loading()
    said: list[str] = []
    p._ld_event.connect(lambda name, _value: said.append(name))
    try:
        p._ld_hook(core)
        p._ld_begin("tiny", (None, False))
        core.play(path)
        deadline = time.monotonic() + 10
        while p._ld_phase == "start" and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        assert p._ld_phase is None, said
        assert said.index("start-file") < said.index("playback-restart"), said
        assert p._ld_overlay.isHidden()
    finally:
        p._ld_clear()
        core.terminate()
        p.close()
        app.processEvents()


# -- the window: what it hands the player ----------------------------------------------

def test_the_window_hands_over_only_an_image_it_holds(app):
    from dopeiptv.ui.main_window import MainWindow

    class Loader:
        def __init__(self, cache):
            self.cache = cache

        def get(self, *_a, **_k):
            raise AssertionError("no download for the loading indicator")

    poster = _logo(90, 135)
    armed: list = []
    h = SimpleNamespace(
        logos=Loader({}), poster_art=Loader({"http://img/poster.jpg": poster}),
        player=SimpleNamespace(set_loading_art=lambda pm, reconnect=False:
                               armed.append((pm, reconnect))))
    MainWindow._arm_loading(h, "http://img/poster.jpg", True)
    MainWindow._arm_loading(h, "http://img/elsewhere.png")
    MainWindow._arm_loading(h, None)
    assert armed == [(poster, True), (None, False), (None, False)]
    # A player without it (an external one) is no reason to fail.
    h.player = SimpleNamespace()
    MainWindow._arm_loading(h, "http://img/poster.jpg")


def test_the_apps_reconnect_is_said_and_every_start_arms_it():
    import inspect

    from dopeiptv.ui.main_window import MainWindow

    calls: list = []
    h = SimpleNamespace(
        _diag_shown=False, _playing_catchup=False, client=None,
        _last_playback={"url": "http://h/live/1.ts", "title": "One", "key": "k",
                        "kind": "live", "item": None},
        _health_retry=lambda: None,
        _start_playback=lambda *a, **k: calls.append(a))
    MainWindow._retry_last_stream(h)
    assert h._reconnect_key == "k" and calls
    # Taken before anything can turn the start away, armed right before play().
    src = inspect.getsource(MainWindow._start_playback)
    assert src.index("self._reconnect_key = None") < src.index("_guard_stream_switch")
    assert src.index("self._arm_loading(icon_url, reconnect)") < src.index("self.player.play(")
    preview = inspect.getsource(MainWindow._play_preview)
    assert preview.index("self._arm_loading(") < preview.index("self.player.play(")
    errors = inspect.getsource(MainWindow._show_stream_error)
    assert "hide_loading()" in errors


def test_new_strings_are_in_every_language():
    from dopeiptv import i18n

    for key in ("player_loading_connecting", "player_loading_slow",
                "player_loading_buffering", "player_loading_buffering_pct"):
        assert set(i18n._STRINGS[key]) == set(i18n.LANGUAGES), key
        assert all(i18n._STRINGS[key].values()), key
