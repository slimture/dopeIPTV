"""Construction smoke test for the embedded player.

Regression guard: eventFilter() reads self._fs_ui / self._popout_mode, and
events can be delivered to the filtered widgets (font/style changes on the
control bar) while __init__ is still running. If those flags are not set
up front, building the player raises AttributeError and the whole window
fails to open.

Subprocess pattern (see test_multiview / test_home): each EmbeddedPlayer owns
a QOpenGLWidget, and tearing it down under the offscreen platform can segfault
the interpreter at shutdown. Running it in a child and asserting on a printed
marker (not the return code) keeps a teardown crash from failing the run - the
assertions have already passed and printed OK by the time any crash happens.
"""
import os
import subprocess
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHILD = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])

player = EmbeddedPlayer()
# The event-filter guard flags must exist immediately after __init__.
assert player._fs_ui is False
assert player._popout_mode is False
# Force pending events (font/style changes) through the filter; this is
# what triggered the original AttributeError.
app.processEvents()

# Centre play/pause button: hidden with no stream, shown while paused, and a
# double-click cancels the pending single-click pause. (isHidden, not
# isVisible: the player widget itself is never shown in this offscreen run.)
player._reveal_center()
assert player.center_btn.isHidden()
player.current_url = "http://x/stream.ts"
player._paused = True
player._reveal_center()
assert not player.center_btn.isHidden()
player._click_timer.start()
player._on_video_dbl_click()
assert not player._click_timer.isActive()
assert player._ignore_next_release is True

# Fullscreen idle-hide must never blank the cursor (or pull the controls)
# under an open menu - the subtitle/audio pickers and the right-click menu
# overlap the video, and hiding mid-choice stranded the user without a
# pointer until they clicked.
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QMenu

player._fs_ui = True
menu = QMenu()
menu.addAction("Subtitles")
menu.popup(QPoint(10, 10))
app.processEvents()
assert QApplication.activePopupWidget() is menu
player._hide_fs_ui()
assert player.cursor().shape() != Qt.CursorShape.BlankCursor
assert player._overlay_timer.isActive()   # re-armed, not given up
menu.close()
app.processEvents()
player._hide_fs_ui()
assert player.cursor().shape() == Qt.CursorShape.BlankCursor

# Render-context lifecycle: reparenting the player (docking in/out of the
# pop-out window) recreates the GL context, which the video widget handles by
# freeing and rebuilding ONLY the mpv render context. Freeing it must NEVER
# touch the mpv instance - that is what keeps the stream and audio alive across
# a pop-out toggle (the "audio but frozen video" bug).
class _FakeCtx:
    freed = False

    def free(self):
        _FakeCtx.freed = True

vid = player.video
sentinel_mpv = object()
vid.mpv = sentinel_mpv
vid._ctx = _FakeCtx()
vid._free_render_context()
assert vid._ctx is None, "render context must be cleared"
assert _FakeCtx.freed is True, "old render context must be freed"
assert vid.mpv is sentinel_mpv, "mpv instance must be untouched (audio lives)"

# The post-reparent settle (geometry re-lock + framebuffer nudge) must be
# pure widget work: it must never touch the mpv instance or the render
# context - freeing/recreating the render context outside the paint cycle
# is what wedged libmpv into a black-for-the-session state.
vid._ctx = _FakeCtx()
_FakeCtx.freed = False
player._settle_after_reparent()
assert vid.mpv is sentinel_mpv, "settle must not touch mpv"
assert vid._ctx is not None and _FakeCtx.freed is False, \
    "settle must not free/recreate the render context"

# The GL video widget must stay CHILD-FREE: a widget on top of a
# QOpenGLWidget forces Qt/macOS onto the render-to-texture composition
# path, which goes stale (frozen picture) when the player is reparented
# into the pop-out window - "stats for nerds" was the reproducible trigger.
# The stats and black-cover overlays are the player's children, drawn over
# the video as siblings.
from PyQt6.QtWidgets import QWidget as _QW
child_widgets = [c for c in vid.children() if isinstance(c, _QW)]
assert child_widgets == [], f"video widget must have no child widgets: {child_widgets}"
assert player._stats_overlay.parent() is player, "stats overlay is the player's child"
assert player._blackout.parent() is player, "black cover is the player's child"

# macOS mirror pop-out wiring: start_mirror builds a MIRROR surface bound to
# the docked video (renders its render context, owns no mpv/ctx of its own),
# covers the docked video with a placeholder, and routes frame updates;
# stop_mirror tears it all down. The docked mpv/render context are never
# touched - the whole point is that the real GL surface is not reparented.
host = _QW()
mirror = player.start_mirror(host)
assert mirror._mirror_of is player.video, "mirror renders the docked video ctx"
assert mirror.mpv is None and mirror._ctx is None, "mirror owns no mpv/ctx"
assert player._mirror is mirror
assert player._dock_ph is not None and not player._dock_ph.isHidden(), \
    "docked video is covered while mirrored"
assert player.video.mpv is sentinel_mpv, "docked mpv untouched by start_mirror"
# The floating overlays follow the mirror into the pop-out window and anchor
# to it; the docked GL surface itself never moves.
assert player._ov_surface is mirror, "overlays anchor to the mirror"
assert player.seek_overlay.parent() is host, "seek bar moved to the pop-out"
assert player.ts_timeline.parent() is host, "timeshift timeline moved"
assert player._stats_overlay.parent() is host, "stats moved"
assert player.video.parent() is player, "the GL surface is NOT reparented"
# Auto-hide: the mirror path counts as a pop-out context, so the control bar
# fades on idle and returns on a mirror hover (the guards used to key off
# _popout_mode, which the mirror path never sets - the bar stayed pinned).
assert player._in_popout() is True, "mirror is a pop-out context"
player._fs_ui = False               # left True by the fullscreen checks above
player.set_popout_autohide(True)
player._hide_popout_bar()
assert player.bar.isHidden(), "auto-hide must fade the bar while mirrored"
player.reveal_pop_overlays()
assert not player.bar.isHidden(), "a mirror hover brings the bar back"
player.stop_mirror()
assert player._mirror is None
assert player._in_popout() is False, "docked again: not a pop-out context"
assert player._ov_surface is player.video, "overlays anchor back to the video"
assert player.seek_overlay.parent() is player, "seek bar back on the player"
assert player.ts_timeline.parent() is player and \
    player._stats_overlay.parent() is player, "overlays back on the player"
assert player._dock_ph.isHidden(), "placeholder lifts on dock-back"
assert player.video.mpv is sentinel_mpv, "docked mpv still untouched"

# The macOS/Windows GL mirror wiring must stay intact even though CI runs the
# Linux raster path above: fake the platform and run one mirror cycle through
# the cross-context widget branch - the exact branch macOS uses.
import sys as _sys
_real_platform = _sys.platform
_sys.platform = "darwin"
try:
    from dopeiptv.media.embedded import _MpvGLWidget as _GLW
    host2 = _QW()
    m2 = player.start_mirror(host2)
    assert isinstance(m2, _GLW), "darwin branch must build the GL widget mirror"
    assert m2._mirror_of is player.video, "mirror renders the docked video ctx"
    assert m2.mpv is None and m2._ctx is None, "mirror owns no mpv/ctx"
    assert player._ov_surface is m2
    assert player.video.parent() is player, "GL surface still not reparented"
    assert player.video.mpv is sentinel_mpv, "docked mpv untouched (darwin)"
    player.stop_mirror()
    assert player._mirror is None
    assert player.video.mpv is sentinel_mpv
finally:
    _sys.platform = _real_platform

# Sleep-timer badge: fades like the controls, but is pinned on in the final
# SLEEP_PIN_SECS so the imminent stop is unmissable.
import time as _time
player._start_sleep_timer(45)                 # arms the timer, flashes the pill
assert not player.sleep_badge.isHidden(), "pill shown when the timer is set"
player._maybe_hide_sleep_badge()              # idle fade, plenty of time left
assert player.sleep_badge.isHidden(), "pill fades while > pin window remains"
player._update_sleep_badge()                  # a tick must not un-fade it
assert player.sleep_badge.isHidden(), "an idle tick keeps it faded"
# Inside the pinned window it shows on the next tick and never fades.
player._sleep_deadline = _time.monotonic() + player.SLEEP_PIN_SECS - 5
player._update_sleep_badge()
assert not player.sleep_badge.isHidden(), "pill pinned on in the final seconds"
player._maybe_hide_sleep_badge()
assert not player.sleep_badge.isHidden(), "pinned pill never fades"
player._start_sleep_timer(0)                  # cancel
assert player.sleep_badge.isHidden(), "cancel hides the pill"
assert not player._sleep_tick.isActive() and not player._sleep_badge_timer.isActive()

print("EMBEDDED_OK")
"""


def test_embedded_player_constructs():
    try:
        import PyQt6  # noqa: F401
    except Exception:
        pytest.skip("PyQt6 not available")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD], capture_output=True, text=True,
        env=env, cwd=_REPO_ROOT, timeout=180)
    # Assert on the marker, not the return code: an offscreen-GL teardown
    # segfault after the checks passed must not fail the test.
    assert "EMBEDDED_OK" in proc.stdout, (
        f"embedded checks failed\n"
        f"stdout={proc.stdout!r}\nstderr={proc.stderr[-2000:]!r}")


def test_unmuting_a_stream_that_started_muted_restores_the_volume():
    """Starting a film while muted and then unmuting must give sound back
    without replaying it.

    The sliders are moved with their signals blocked while muting (so the
    user's real level isn't overwritten in settings), which means mpv is never
    told the new level. play() then handed mpv the muted slider's 0 as the
    volume, and clearing the mute flag can't undo a zero volume - the film
    stayed silent until it was restarted.
    """
    import subprocess
    import sys as _sys
    child = r"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication
from dopeiptv.media.embedded import EmbeddedPlayer

app = QApplication.instance() or QApplication([])
p = EmbeddedPlayer()


class FakeMpv(dict):
    pause = False
    def __getattr__(self, n): return None


p.video.mpv = FakeMpv()
p.vol.setValue(80)

# Mute: the sliders drop to 0, the real level is remembered.
p.toggle_mute()
assert p._muted is True
assert p.vol.value() == 0
assert p.video.mpv["mute"] is True
assert p.video.mpv["volume"] == 0.0, p.video.mpv

# Unmute: mpv must hear the restored level, not just the cleared flag.
p.toggle_mute()
assert p._muted is False
assert p.vol.value() == 80
assert p.video.mpv["mute"] is False
assert p.video.mpv["volume"] == 80.0, p.video.mpv
print("MUTE_OK")
"""
    proc = subprocess.run([_sys.executable, "-c", child], capture_output=True,
                          text=True, cwd=_REPO_ROOT, timeout=180,
                          env=dict(os.environ, QT_QPA_PLATFORM="offscreen"))
    assert "MUTE_OK" in proc.stdout, (
        f"mute/volume check failed\nstdout={proc.stdout!r}\n"
        f"stderr={proc.stderr[-1500:]!r}")


def test_video_playback_is_never_touched_by_the_music_features():
    """The audio visuals hook must do NOTHING for a video stream - writing
    mpv's filter properties there is what broke clicking a TV channel."""
    from dopeiptv.ui.main_window import MainWindow

    touched = []

    class _W:
        AUDIO_EXTS = MainWindow.AUDIO_EXTS
        _apply_audio_visuals = MainWindow._apply_audio_visuals
        _eq_settings = MainWindow._eq_settings

        class settings:
            @staticmethod
            def value(k, d=None):
                return d

        class player:
            @staticmethod
            def set_visualiser(*a, **k):
                touched.append("vis")

            @staticmethod
            def set_equaliser(*a, **k):
                touched.append("eq")

    w = _W()
    w._apply_audio_visuals("http://host/live/1.m3u8")
    w._apply_audio_visuals("/films/Film.2020.1080p.mkv")
    assert touched == []                       # video: hands off entirely

    w._apply_audio_visuals("/music/02.flac")
    assert touched == ["vis", "eq"]            # music: both applied

    # Music -> video takes the visualiser down again (once), then leaves
    # every later video play completely alone.
    touched.clear()
    w._apply_audio_visuals("http://host/live/1.m3u8")
    assert touched == ["vis"]
    touched.clear()
    w._apply_audio_visuals("http://host/live/2.m3u8")
    assert touched == []


def test_the_embedded_branch_reaches_player_play():
    """The music bookkeeping once sat at top level inside _start_playback,
    which CLOSED the `if self.player:` block - so player.play() landed in
    the else and every stream opened in an external window. Pin the
    structure: with a player present, play() is called and nothing is
    launched externally."""
    import inspect
    import re

    from dopeiptv.ui.main_window import MainWindow

    src = inspect.getsource(MainWindow._start_playback)
    # Find the embedded branch and the two calls that must sit inside it.
    at = src.index("if self.player:\n")
    indent = len(src[at:].split("if self.player:")[0])
    body = src[at:]
    play_at = body.index("self.player.play(url, title, start=resume_at)")
    # Everything between must stay INSIDE the branch: no line may return to
    # the branch's own indentation level (which would close it).
    between = body[:play_at].splitlines()[1:]
    for line in between:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assert len(line) - len(line.lstrip()) > indent + 8, (
            f"line escapes the embedded branch: {line!r}")
    assert re.search(r"else:\s*\n\s*# No embedded player", body)


def test_the_raster_mirror_heals_a_lost_frame_signal():
    """The mirror only renders when mpv's update callback opened the gate.
    If that signal is lost across a window transition (going fullscreen on
    macOS), the picture froze while the audio played on. After a quarter
    second of silence the tick must force a frame through by itself."""
    import time as _t

    from dopeiptv.media.embedded import EmbeddedPlayer

    class _P:
        _tick = EmbeddedPlayer._tick_raster_mirror

        def __init__(self):
            self._raster_pending_frame = False
            self._mirror_pending = None
            self._mirror_fbos = [None, None]
            self._mirror_fbo_i = 0
            self.rendered = 0

            class _M:
                def devicePixelRatioF(self):
                    return 1.0

                def width(self):
                    return 640

                def height(self):
                    return 360

            class _V:
                mpv = object()
                _ctx = object()

            self._mirror, self.video = _M(), _V()

    p = _P()
    # The gate is shut and no time has passed: nothing is forced.
    p._raster_frame_at = _t.monotonic()
    before = p._raster_frame_at
    try:
        p._tick()
    except Exception:
        pass                      # the GL work beyond the gate is not ours
    assert p._raster_frame_at == before
    assert getattr(p, "_raster_stalls", 0) == 0

    # A quarter second of silence: the watchdog opens the gate itself.
    p._raster_frame_at = _t.monotonic() - 0.3
    try:
        p._tick()
    except Exception:
        pass
    assert p._raster_stalls == 1


def test_a_pause_nobody_asked_for_is_cleared_but_a_real_one_is_not():
    """keep-open=yes pauses mpv at end of file, and that pause is player
    state, not file state - it survives loading the next one, which is why
    autoplay put the next episode on screen and left it standing still.
    The watchdog clears that, and only that: a pause the user pressed, and
    a stale timer from an earlier stream, must both be left alone."""
    from dopeiptv.media.embedded import EmbeddedPlayer

    class _M:
        def __init__(self):
            self.pause = True

    class _V:
        def __init__(self):
            self.mpv = _M()

    class _P:
        _ensure_unpaused = EmbeddedPlayer._ensure_unpaused
        current_url = "http://host/ep2.mp4"
        _play_gen = 4

        def __init__(self):
            self.video = _V()
            self.synced = []

        def _sync_pause_label(self, paused):
            self.synced.append(paused)

    # The keep-open pause: cleared.
    p = _P()
    p._ensure_unpaused(4)
    assert p.video.mpv.pause is False
    assert p.synced == [False]

    # A pause the user pressed: untouched.
    p = _P()
    p._user_paused = True
    p._ensure_unpaused(4)
    assert p.video.mpv.pause is True
    assert p.synced == []

    # A timer armed by an earlier stream: untouched.
    p = _P()
    p._ensure_unpaused(3)
    assert p.video.mpv.pause is True

    # Nothing loaded: untouched.
    p = _P()
    p.current_url = None
    p._ensure_unpaused(4)
    assert p.video.mpv.pause is True


def test_the_macos_mirror_settles_its_surface_after_a_fullscreen_resize():
    """Going fullscreen resizes the mirror several times as the animation
    runs, and on macOS a GL widget's backing framebuffer can stay bound to
    the OLD window - frozen picture, audio fine, rendering reporting
    success throughout. The player already carries this remedy for the
    pop-out reparent; the mirror path never got it.

    The settle is debounced, so only the LAST resize of a transition pays
    for it, and it must never fire for the docked surface."""
    from dopeiptv.media.embedded import _MpvGLWidget

    class _Win:
        def __init__(self):
            self.repaints = 0

        def repaint(self):
            self.repaints += 1

    class _M:
        _settle_mirror_soon = _MpvGLWidget._settle_mirror_soon
        _settle_mirror_surface = _MpvGLWidget._settle_mirror_surface
        _settle_done = _MpvGLWidget._settle_done

        def __init__(self, mirror_of):
            self._mirror_of = mirror_of
            self._w, self._h = 1512, 917
            self.resizes = []
            self.updates = 0
            self._win = _Win()

        class _Sz:
            def __init__(self, w, h):
                self._w, self._h = w, h

            def isValid(self):
                return True

            def width(self):
                return self._w

            def height(self):
                return self._h

        def size(self):
            return self._Sz(self._w, self._h)

        def width(self):
            return self._w

        def height(self):
            return self._h

        def resize(self, *a):
            # Qt takes resize(w, h) and resize(QSize) alike; the code uses
            # both, so the stub has to as well or the second call raises
            # into the settle's own try/except and hides the failure.
            w, h = (a[0].width(), a[0].height()) if len(a) == 1 else a
            self.resizes.append((w, h))
            self._w, self._h = w, h

        def update(self):
            self.updates += 1

        def window(self):
            return self._win

    m = _M(mirror_of=object())
    m._settle_mirror_surface()

    # The 1 px nudge and back, then a SYNCHRONOUS host-window repaint - an
    # async update loses the race with the stale layer.
    assert m.resizes == [(1512, 916), (1512, 917)]
    assert m.updates == 1
    assert m._win.repaints == 1

    # A surface with no real height is left alone rather than resized to 0.
    m2 = _M(mirror_of=object())
    m2._h = 1
    m2._settle_mirror_surface()
    assert m2.resizes == []
    assert m2._win.repaints == 1


def test_the_mirror_settle_cannot_feed_itself():
    """The settle resizes the widget, and that comes straight back as
    another resizeGL. The first cut re-armed on it and fed itself at 4 Hz -
    89 settles in one session, each with a synchronous full-window repaint,
    which showed up as stutter in fullscreen."""
    from dopeiptv.media.embedded import _MpvGLWidget

    class _M:
        _settle_mirror_soon = _MpvGLWidget._settle_mirror_soon
        _settle_mirror_surface = _MpvGLWidget._settle_mirror_surface
        _settle_done = _MpvGLWidget._settle_done

        def __init__(self):
            self._mirror_of = object()
            self._w, self._h = 1512, 917
            self.settles = 0
            self.armed = 0
            self._pending = []

        # -- the bits the settle touches ---------------------------------
        class _Sz:
            def __init__(self, w, h): self._w, self._h = w, h
            def isValid(self): return True
            def width(self): return self._w
            def height(self): return self._h

        def size(self): return self._Sz(self._w, self._h)
        def width(self): return self._w
        def height(self): return self._h
        def update(self): pass
        def window(self): return None

        def resize(self, *a):
            w, h = (a[0].width(), a[0].height()) if len(a) == 1 else a
            self._w, self._h = w, h
            self._settle_mirror_soon()      # what resizeGL does

        # -- stand in for the QTimer -------------------------------------
        def _fire_pending(self):
            while self._pending:
                self._pending.pop(0)()

    m = _M()

    class _T:
        def __init__(self, owner): self.owner = owner
        def setSingleShot(self, *_a): pass
        class timeout:
            @staticmethod
            def connect(*_a): pass
        def start(self, *_a): m.armed += 1

    m._settle_timer = _T(m)

    real = _MpvGLWidget._settle_mirror_surface

    def counted(self):
        self.settles += 1
        real(self)

    m._settle_mirror_surface = counted.__get__(m)

    import dopeiptv.media.embedded as em
    orig = em.QTimer

    class _QT:
        @staticmethod
        def singleShot(_ms, fn): m._pending.append(fn)

    em.QTimer = _QT
    try:
        m._settle_mirror_surface()          # the transition's settle
        m._fire_pending()                   # end of the event-loop turn
    finally:
        em.QTimer = orig

    assert m.settles == 1                   # not 89
    assert m.armed == 0                     # its own resizes armed nothing

    # And once settled at this size, a repeat resize to the same size is a
    # no-op rather than another synchronous repaint.
    m._settle_mirror_soon()
    assert m.armed == 0


def test_the_cursor_and_focus_follow_the_mirror_in_fullscreen():
    """In fullscreen on macOS the picture lives in the MIRROR, in its own
    window. Blanking the cursor on the player and the docked video left the
    pointer visible over the video, and focusing the docked widget sent
    every key press to the window nobody was looking at."""
    from dopeiptv.media.embedded import EmbeddedPlayer

    class _W:
        def __init__(self, name):
            self.name = name
            self.cursor = None
            self.focused = False

        def setCursor(self, c):
            self.cursor = c

        def unsetCursor(self):
            self.cursor = None

        def setFocus(self, *_a):
            self.focused = True

    class _P:
        _cursor_surfaces = EmbeddedPlayer._cursor_surfaces

        def __init__(self, mirror=None):
            self.video = _W("video")
            self._mirror = mirror
            self.cursor = None

        def setCursor(self, c):
            self.cursor = c

        def unsetCursor(self):
            self.cursor = None

    # Docked: the player and the docked video, nothing else.
    p = _P()
    assert [getattr(w, "name", "player") for w in p._cursor_surfaces()] == \
        ["player", "video"]

    # Mirrored: the mirror is in the list, so it gets blanked too.
    mirror = _W("mirror")
    p = _P(mirror=mirror)
    assert mirror in p._cursor_surfaces()

    from PyQt6.QtCore import Qt as _Qt
    for w in p._cursor_surfaces():
        w.setCursor(_Qt.CursorShape.BlankCursor)
    assert mirror.cursor == _Qt.CursorShape.BlankCursor

    for w in p._cursor_surfaces():
        w.unsetCursor()
    assert mirror.cursor is None


def test_shortcuts_are_application_wide_so_they_work_in_the_popout():
    """Qt's default shortcut context only fires while THIS window is
    active, and in fullscreen on macOS the active window is the pop-out
    mirror - so space did not pause, and nor did anything else."""
    import inspect

    from PyQt6.QtCore import Qt as _Qt

    from dopeiptv.ui.mw_shortcuts import _ShortcutsMixin

    src = inspect.getsource(_ShortcutsMixin._install_shortcuts)
    assert "ApplicationShortcut" in src, \
        "every rebindable shortcut must be application-wide"
    assert _Qt.ShortcutContext.ApplicationShortcut is not None
