"""macOS: AirPlay to an Apple TV through Apple's own frameworks, used the
way Apple documents them.

The cast plays in an AVPlayer that allows external playback. Apple's own
AirPlay button - AVKit's AVRoutePickerView with that player set - sits in a
small dark native panel of its own (an NSPanel in Apple's HUD style),
under a small AVPlayerLayer, the player's video output - which has to stay
on a real layer, or AirPlay sends sound only. Nothing else is in it: what
the cast is doing is said in the cast strip, as for any receiver. Once the
Apple TV has held the stream for PANEL_HIDE_S, the panel steps aside
(orderOut); the cast strip's AirPlay button brings it back. The user clicks the button and picks the Apple TV;
the Apple TV then fetches the stream itself: HLS or MP4 as it is, or what
providers/airplay_bridge.py makes of anything else.

Why a panel with AVRoutePickerView, and not AVPlayerView: AVRoutePickerView
with its `player` set (macOS 10.15+) is Apple's control for routing one
AVPlayer - the routes it lists are that player's, and picking one sends the
player's video, not the Mac's sound, to the Apple TV. AVPlayerView would
bring its own play button and timeline beside the cast strip, and its play
would play on this Mac. The panel is a window of its own, never an NSView
inside a Qt widget (there the two toolkits painted over each other, and
each refresh of the cast dialog's list made the button again); it is made
once per cast and nothing in the dialog touches it. The app never clicks
Apple's button for the user - AppKit offers no way to open its menu - but
puts it within one click: the panel opens at the top right of the cast card
in the window (``_Panel.place``, the receiver's *anchor*; else with the
button under the pointer, else beside the window in front), as the key
window with the button focused, Apple TVs first in Apple's menu
(prioritizesVideoDevices) - each step only where AppKit takes it, logged,
the panel shown as before where it does not. It floats over the app's own
windows only while the app is in front: it hides with the app
(hidesOnDeactivate), never over other programs.

The player never plays on this Mac: it is paused and muted until
externalPlaybackActive has stayed on for ROUTE_SETTLE_S, muted the moment
it goes off and paused once it has stayed off as long - a route that blinks
while the Apple TV takes over neither starts nor stops anything.

Another channel while it casts (a zap, a track, the bridge's second try)
is a new item in the same player (replaceCurrentItemWithPlayerItem): the
route, Apple's button and the window stay, the Apple TV stays picked. The
bridge stops the old ffmpeg and starts the new one; a route that goes while
the Apple TV loads the new item does not end the cast (SWAP_GRACE_S).

Whether the cast dialog lists AirPlay is AVRouteDetector's answer while the
dialog is open (listed when it gives none).

AirPlayReceiver is one more kind of receiver behind CastHub (the window
adds it on macOS, ui/mw_airplay.py): it answers what the cast strip asks -
position, state, pause, seek, volume, stop - from what the GUI thread last
read off the player. AVFoundation and AppKit are driven on the GUI thread,
the main thread, only: calls from the cast's worker threads are queued onto
it, as the system's media controls are (services/media_session.py).
Key-value observing, which Apple may deliver on any thread, and the
notifications only queue a kick to that thread; a 1 s tick reads the
position and keeps the deadlines. An exception never crosses back into
Objective-C.

pyobjc-framework-AVFoundation and -AVKit are macOS-only requirements;
without them there is no AirPlay entry and the app runs as before.
"""
from __future__ import annotations

from dataclasses import replace

import hashlib
import importlib.util
import re
import sys
import threading
import time
import types
import weakref
from typing import Any, Callable, cast

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from ..core import nerd, privacy
from ..core.log import log, redact, redact_url
from ..i18n import LocalizedError, tr
from ..providers import airplay_bridge as ab
from .media_session import say_once

#: The entry in the cast dialog's list (CastHub routes it here).
NAME = "AirPlay (Apple TV)"
TICK_MS = 1000          # the position and the deadlines; changes come by KVO
ROUTE_SETTLE_S = 1.0    # externalPlaybackActive held this long is believed
ROUTE_WAIT_S = 120.0    # how long Apple's button may go unclicked
ROUTE_GRACE_S = 4.0     # moving to another Apple TV drops the route a moment
SWAP_GRACE_S = 20.0     # another stream on the route: the TV loading it
UNREACHED_S = 20.0      # on the TV that long and nothing asked the bridge
OPEN_WAIT_S = 10.0      # the GUI thread making the player
SETTLE_S = 2.0          # a provider still counting the player's connection
DETECT_SETTLE_S = 3.0   # AVRouteDetector's first answer once it is on
MENU_HINT_S = 8.0       # Apple's menu closed this long, and still no route
PANEL_HIDE_S = 4.0      # on the Apple TV this long: the panel steps aside
ERROR_LOG_MAX = 20      # AVFoundation error-log entries logged per cast
SEEN_MAX = 256          # streams whose codecs this session remembers

_NEEDS = ("objc", "Foundation", "AppKit", "AVFoundation", "AVKit")
# AVPlayerItemStatus, AVPlayerTimeControlStatus
_FAILED = 2
_ITEM_STATUS = {0: "unknown", 1: "ready to play", 2: "failed"}
_TC_PAUSED, _TC_WAITING, _TC_PLAYING = 0, 1, 2
_TC_NAMES = {_TC_PAUSED: "paused", _TC_WAITING: "waiting",
             _TC_PLAYING: "playing"}
# CMTimeFlags: valid, and none of +inf, -inf, indefinite.
_CM_VALID, _CM_NOT_A_NUMBER = 1, 4 | 8 | 16
_KVO_NEW = 1            # NSKeyValueObservingOptionNew
_PLAYER_KEYS = ("externalPlaybackActive", "timeControlStatus")
_ITEM_KEYS = ("status",)
_ITEM_NOTES = (("AVPlayerItemDidPlayToEndTimeNotification", "end"),
               ("AVPlayerItemFailedToPlayToEndTimeNotification", "failed"),
               ("AVPlayerItemPlaybackStalledNotification", "stalled"),
               ("AVPlayerItemNewErrorLogEntryNotification", "errorlog"))
_ROUTES_NOTE = "AVRouteDetectorMultipleRoutesDetectedDidChangeNotification"
_URL = re.compile(r"https?://[^\s\"'<>]+")


def _frameworks() -> Any:
    import AppKit
    import AVFoundation
    import AVKit
    import Foundation
    return types.SimpleNamespace(av=AVFoundation, kit=AVKit, fd=Foundation,
                                 ak=AppKit)


def supported() -> bool:
    """macOS with PyObjC's AVFoundation and AVKit."""
    if sys.platform != "darwin":
        return False
    try:
        return all(importlib.util.find_spec(n) is not None for n in _NEEDS)
    except (ImportError, ValueError):
        return False


def check() -> None:
    """Raises when this build lacks what AirPlay uses (--self-check)."""
    f = _frameworks()
    for module, names in (
            (f.av, ("AVPlayer", "AVPlayerItem", "AVPlayerLayer",
                    "AVRouteDetector")),
            (f.kit, ("AVRoutePickerView",)),
            (f.fd, ("NSObject", "NSURL", "NSNotificationCenter",
                    "NSOperationQueue")),
            (f.ak, ("NSPanel", "NSView", "NSColor"))):
        for name in names:
            getattr(module, name)
    _observer_class()


def bundle_problem() -> str | None:
    """Why this build could not AirPlay, or None (--self-check)."""
    try:
        check()
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return None


def seconds(t: Any) -> float | None:
    """A CMTime in seconds; None for an invalid or endless one."""
    try:
        value, scale, flags = t.value, t.timescale, t.flags
    except AttributeError:
        try:
            value, scale, flags = t[0], t[1], t[2]
        except (TypeError, IndexError):
            return None
    try:
        if not int(flags) & _CM_VALID or int(flags) & _CM_NOT_A_NUMBER \
                or not scale:
            return None
        return float(value) / float(scale)
    except (TypeError, ValueError):
        return None


def cmtime(at: float) -> Any:
    try:
        import CoreMedia
        return CoreMedia.CMTimeMakeWithSeconds(float(at), 600)
    except Exception:
        # PyObjC takes a struct argument as a tuple: value, timescale,
        # flags (valid), epoch.
        return (int(round(float(at) * 600)), 600, _CM_VALID, 0)


def _external(player: Any) -> bool:
    for getter in ("isExternalPlaybackActive", "externalPlaybackActive"):
        fn = getattr(player, getter, None)
        if callable(fn):
            return bool(fn())
    return False


def _clean(text: str) -> str:
    """Text for the log: an address in it without its login, and no
    credential the app knows of."""
    return redact(_URL.sub(lambda m: redact_url(m.group(0)), text))


def _error_text(err: Any) -> str:
    """An NSError as its domain, code and description, and the error under
    it - what tells an App Transport Security refusal from a playlist the
    TV could not read."""
    if err is None:
        return ""
    try:
        parts = [f"{err.domain()} {int(err.code())}: "
                 f"{err.localizedDescription()}"]
        info = err.userInfo()
        under = info.get("NSUnderlyingError") if info is not None else None
        if under is not None:
            parts.append(f"under it {under.domain()} {int(under.code())}: "
                         f"{under.localizedDescription()}")
        return _clean("; ".join(parts))
    except Exception:
        return _clean(str(err))


def _failure_of(note: Any) -> str:
    """The error an AVPlayerItemFailedToPlayToEndTime notification carries."""
    try:
        key = getattr(_frameworks().av,
                      "AVPlayerItemFailedToPlayToEndTimeErrorKey",
                      "AVPlayerItemFailedToPlayToEndTimeErrorKey")
        info = note.userInfo()
        return _error_text(info.get(key) if info is not None else None)
    except Exception:
        return ""


def _post(observer: Any, what: str) -> None:
    """From one of Apple's callbacks, on whichever thread Apple chose: a
    kick queued to the GUI thread, and nothing raised into Objective-C."""
    try:
        ref = getattr(observer, "sink", None)
        rx = ref() if callable(ref) else None
        if rx is not None:
            rx._kick.emit(what)
    except Exception:
        pass


def _note_block(receiver: AirPlayReceiver,
                what: str) -> Callable[[Any], None]:
    """A notification's block (run on the main queue): it writes down what
    happened and kicks the GUI thread."""
    ref = weakref.ref(receiver)

    def block(note: Any) -> None:
        try:
            rx = ref()
            if rx is None:
                return
            if what == "failed":
                rx._note_error = _failure_of(note)
            if what in ("end", "failed"):
                rx._note = what
            rx._kick.emit(what)
        except Exception:
            pass
    return block


_OBSERVER_CLASS: list[Any] = []


def _observer_class() -> Any:
    """The NSObject subclass Apple's callbacks go to - key-value observing
    and the route picker's delegate. Made once: the Objective-C runtime
    keeps a class by its name."""
    if _OBSERVER_CLASS:
        return _OBSERVER_CLASS[0]
    import objc
    base = _frameworks().fd.NSObject
    # AVRoutePickerViewDelegate's methods return nothing.
    returns_nothing = objc.typedSelector(b"v@:@")

    class DopeIPTVAirPlayObserver(base):  # type: ignore[misc,valid-type]
        def observeValueForKeyPath_ofObject_change_context_(
                self, path: Any, _obj: Any, _change: Any,
                _context: Any) -> None:
            _post(self, str(path))

        @returns_nothing
        def routePickerViewWillBeginPresentingRoutes_(self, _view: Any) -> None:
            _post(self, "menu_open")

        @returns_nothing
        def routePickerViewDidEndPresentingRoutes_(self, _view: Any) -> None:
            _post(self, "menu_closed")

    _OBSERVER_CLASS.append(DopeIPTVAirPlayObserver)
    return DopeIPTVAirPlayObserver


def _beside_front_window(width: float) -> tuple[float, float] | None:
    """Where the panel's top-left corner goes, in AppKit's screen points:
    beside the window in front (the cast dialog), or None to centre it."""
    try:
        from PyQt6.QtGui import QGuiApplication
        from PyQt6.QtWidgets import QApplication
        win = QApplication.activeModalWidget() or QApplication.activeWindow()
        primary = QGuiApplication.primaryScreen()
        if win is None or primary is None:
            return None
        frame = win.frameGeometry()
        room = (win.screen() or primary).availableGeometry()
        x = frame.right() + 12
        if x + width > room.right():
            x = frame.left() - 12 - int(width)
        if x < room.left():
            return None
        # Qt counts down from the top of the primary screen, AppKit up
        # from its bottom.
        return float(x), float(primary.geometry().height() - frame.top())
    except Exception:
        return None


def place_at_pointer(pointer: tuple[float, float], room: tuple[float, float, float, float],
                     primary_h: float, width: float, height: float,
                     button: tuple[float, float]) -> tuple[float, float]:
    """The panel's top-left corner, in AppKit's screen points, that puts
    Apple's button (*button*: its middle from the panel's top-left, y down)
    under the pointer (*pointer*, Qt's coordinates) - kept inside *room*
    (the screen's free area: x, y, width, height). Qt counts down from the
    top of the primary screen (*primary_h* tall), AppKit up from its
    bottom."""
    px, py = pointer
    rx, ry, rw, rh = room
    left = min(max(px - button[0], rx), rx + rw - width)
    top = min(max(py - button[1], ry), ry + rh - height)
    return float(left), float(primary_h - top)


def place_in(anchor: tuple[float, float, float, float],
             room: tuple[float, float, float, float], primary_h: float,
             width: float, height: float, inset: float = 16.0) -> tuple[float, float]:
    """The panel's top-left corner, in AppKit's screen points, at the top
    right inside *anchor* (the cast card: x, y, width, height in Qt's
    coordinates), *inset* in from its corner - kept inside *room* (the
    screen's free area)."""
    ax, ay, aw, _ah = anchor
    rx, ry, rw, rh = room
    left = min(max(ax + aw - inset - width, rx), rx + rw - width)
    top = min(max(ay + inset, ry), ry + rh - height)
    return float(left), float(primary_h - top)


def _in_anchor(anchor: tuple[int, int, int, int] | None, width: float,
               height: float) -> tuple[float, float] | None:
    """place_in for the window's cast card, or None when there is none."""
    if anchor is None:
        return None
    try:
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QGuiApplication
        x, y, w, h = anchor
        if w < width or h < 60:
            return None
        primary = QGuiApplication.primaryScreen()
        screen = QGuiApplication.screenAt(QPoint(x + w // 2, y + h // 2)) or primary
        if primary is None or screen is None:
            return None
        room = screen.availableGeometry()
        return place_in((float(x), float(y), float(w), float(h)),
                        (float(room.x()), float(room.y()),
                         float(room.width()), float(room.height())),
                        float(primary.geometry().height()), width, height)
    except Exception as e:
        log.debug("airplay: the cast card's place was not told: %s", e)
        return None


def _at_pointer(width: float, height: float,
                button: tuple[float, float]) -> tuple[float, float] | None:
    """place_at_pointer for where the pointer is now (the click that cast
    to AirPlay), or None when Qt cannot say."""
    try:
        from PyQt6.QtGui import QCursor, QGuiApplication
        primary = QGuiApplication.primaryScreen()
        pos = QCursor.pos()
        screen = QGuiApplication.screenAt(pos) or primary
        if primary is None or screen is None:
            return None
        room = screen.availableGeometry()
        return place_at_pointer((float(pos.x()), float(pos.y())),
                                (float(room.x()), float(room.y()),
                                 float(room.width()), float(room.height())),
                                float(primary.geometry().height()), width, height, button)
    except Exception as e:
        log.debug("airplay: where the pointer is was not told: %s", e)
        return None


class _Panel:
    """Apple's AirPlay button and a small video output, in a dark HUD panel
    of their own (the module's docstring says why)."""

    VIDEO_W, VIDEO_H, BUTTON, PAD = 160.0, 90.0, 32.0, 10.0
    W = VIDEO_W + 2 * PAD
    H = PAD + VIDEO_H + 6.0 + BUTTON + PAD
    #: About what a HUD panel's title bar takes above its content.
    TITLE = 22.0

    @classmethod
    def place(cls, anchor: tuple[int, int, int, int] | None = None
              ) -> tuple[float, float] | None:
        """Where the panel opens: at the top right of the cast card in the
        window (*anchor*), else with Apple's button under the pointer, else
        beside the window in front, else (None) centred."""
        at = _in_anchor(anchor, cls.W, cls.H + cls.TITLE)
        if at is not None:
            log.info("airplay: the AirPlay window opens at the top right of the cast card")
            return at
        at = _at_pointer(cls.W, cls.H + cls.TITLE,
                         (cls.W / 2, cls.TITLE + cls.H - cls.PAD - cls.BUTTON / 2))
        if at is not None:
            log.info("airplay: the AirPlay window opens with Apple's button under the pointer")
            return at
        return _beside_front_window(cls.W)

    def __init__(self, player: Any, delegate: Any,
                 top_left: tuple[float, float] | None) -> None:
        f = _frameworks()
        ak, av, kit = f.ak, f.av, f.kit
        style = (getattr(ak, "NSWindowStyleMaskTitled", 1)
                 | getattr(ak, "NSWindowStyleMaskClosable", 2)
                 | getattr(ak, "NSWindowStyleMaskUtilityWindow", 1 << 4)
                 | getattr(ak, "NSWindowStyleMaskHUDWindow", 1 << 13))
        panel = ak.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            ((0.0, 0.0), (self.W, self.H)), style,
            getattr(ak, "NSBackingStoreBuffered", 2), False)
        panel.setReleasedWhenClosed_(False)
        panel.setTitle_(tr("airplay_window_title"))
        try:
            dark = ak.NSAppearance.appearanceNamed_(getattr(
                ak, "NSAppearanceNameVibrantDark", "NSAppearanceNameVibrantDark"))
            if dark is not None:
                panel.setAppearance_(dark)
        except Exception as e:
            log.debug("airplay: the panel's dark appearance: %s", e)
        # Above the cast dialog, and clickable while it is open: Qt runs a
        # modal dialog as an AppKit modal session, which passes clicks only
        # to the modal window and to panels that work when modal.
        panel.setFloatingPanel_(True)
        panel.setWorksWhenModal_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        # Gone while another program is in front, back with the app: a
        # floating panel otherwise stays over every program's windows.
        panel.setHidesOnDeactivate_(True)
        panel.setCollectionBehavior_(getattr(
            ak, "NSWindowCollectionBehaviorFullScreenAuxiliary", 1 << 8))
        content = panel.contentView()
        pad = self.PAD
        video = ak.NSView.alloc().initWithFrame_(
            ((pad, self.H - pad - self.VIDEO_H), (self.VIDEO_W, self.VIDEO_H)))
        # The player's video output: AirPlay sends a player's picture while
        # a layer shows it; a player without one goes over as sound only.
        # Paused until the Apple TV has it, it shows a still at most here.
        layer = av.AVPlayerLayer.playerLayerWithPlayer_(player)
        try:
            layer.setBackgroundColor_(ak.NSColor.blackColor().CGColor())
            layer.setCornerRadius_(4.0)
            layer.setMasksToBounds_(True)
        except Exception:
            pass
        video.setLayer_(layer)          # a layer-hosting view: the layer
        video.setWantsLayer_(True)      # first, then wantsLayer
        content.addSubview_(video)
        side = self.BUTTON
        picker = kit.AVRoutePickerView.alloc().initWithFrame_(
            (((self.W - side) / 2, pad), (side, side)))
        picker.setPlayer_(player)
        if delegate is not None:
            picker.setDelegate_(delegate)
        # Apple TVs first in Apple's menu (macOS 10.15 on; older ones have
        # no such setter, and the menu is as it was).
        for setter, value in (("setRoutePickerButtonBordered_", False),
                              ("setPrioritizesVideoDevices_", True),
                              ("setToolTip_", tr("airplay_button"))):
            try:
                getattr(picker, setter)(value)
            except Exception:
                pass
        content.addSubview_(picker)
        if top_left is not None:
            panel.setFrameTopLeftPoint_(top_left)
        else:
            panel.center()
        self.panel, self.layer, self.picker = panel, layer, picker

    def show(self, focus: bool = False) -> None:
        """In front. *focus* (the first time, as a cast to AirPlay begins):
        made the key window with Apple's button as what the keyboard is on,
        so nothing else is in its way - and where AppKit will not, shown as
        it always was."""
        if focus:
            try:
                self.panel.makeKeyAndOrderFront_(None)
                took = bool(self.panel.makeFirstResponder_(self.picker))
                log.info("airplay: the AirPlay window is the key window, Apple's button %s",
                         "has the keyboard" if took else "did not take the keyboard")
                return
            except Exception as e:
                log.info("airplay: the AirPlay window did not take the focus (%s: %s) - "
                         "shown as before", type(e).__name__, e)
        self.panel.orderFront_(None)

    def hide(self) -> None:
        """Out of the way, not closed: the player keeps its layer, so the
        Apple TV keeps the picture."""
        self.panel.orderOut_(None)

    def close(self) -> None:
        for step in (lambda: self.picker.setDelegate_(None),
                     lambda: self.picker.setPlayer_(None),
                     lambda: self.layer.setPlayer_(None),
                     lambda: self.panel.orderOut_(None),
                     self.panel.close):
            try:
                step()
            except Exception as e:
                log.debug("airplay: closing the AirPlay window: %s", e)


class AirPlayReceiver(QObject):
    """AirPlay as CastHub sees a receiver: one entry, one cast at a time."""

    KIND = "airplay"
    #: CastHub hands this receiver the pieces of an archive or a buffer.
    TAKES_FEED = True
    #: The cast ended by itself; a string key says why (tr), "" for none.
    ended = pyqtSignal(str)
    #: Something the user should know while it plays (a string key).
    problem = pyqtSignal(str)
    #: AVRouteDetector found AirPlay routes while the cast dialog is open.
    routes_changed = pyqtSignal(bool)
    _call = pyqtSignal(str, object)
    #: From Apple's callbacks, any thread: something changed (what).
    _kick = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None,
                 bridge: ab.AirPlayBridge | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge or ab.AirPlayBridge()
        #: The window's cast context (ui/mw_airplay.py): says whether a
        #: channel or a film is cast, which an address cannot always tell.
        self.context: Callable[[], dict] | None = None
        #: Where the AirPlay window goes: the cast card's rectangle on the
        #: screen (Qt's coordinates), or None.
        self.anchor: Callable[[], tuple[int, int, int, int] | None] | None = None
        self.active: str | None = None
        self.state = "IDLE/NONE"
        self.duration = 0.0
        self.plan: ab.Plan | None = None
        self._req: dict[str, Any] = {}
        #: What each stream cast this session was found to hold (its
        #: address's hash -> codecs), in memory alone and never while
        #: watching privately: a zap back to it needs no look into it.
        self._seen: dict[str, list[str]] = {}
        self._offset = 0.0
        self._pos = 0.0
        self._volume, self._muted = 1.0, False
        self._player: Any = None
        self._panel: _Panel | None = None
        self._obs: Any = None
        self._obs_failed = False
        self._kvo_player: list[tuple[Any, str]] = []
        self._kvo_item: list[tuple[Any, str]] = []
        self._item_notes: list[Any] = []
        self._panel_notes: list[Any] = []
        self._note = ""
        self._note_error = ""
        self._errors_said = 0
        # The route as last read (raw) and as believed once it has held for
        # ROUTE_SETTLE_S (_external) - only the second moves the player.
        self._raw_ext = False
        self._raw_at = 0.0
        self._external = False
        self._ever_external = False
        self._external_since = 0.0
        self._lost_at = 0.0
        self._opened_at = 0.0
        #: What the player was last handed (the bridge's playlist or a
        #: direct address): a new bridge is the TV's to reach only once it is.
        self._opened_url = ""
        # When a new item went into a player already on the Apple TV.
        self._swapped_at = 0.0
        self._user_paused = False
        self._fell_back = False
        self._said_unreached = False
        self._closing = False
        self._row: tuple[QLabel, QPushButton] | None = None
        # AVRouteDetector, on while the cast dialog is open: None while it
        # has no answer (or there is no detector), else whether it found
        # AirPlay routes.
        self._detector: Any = None
        self._detector_notes: list[Any] = []
        self._routes: bool | None = None
        self._detecting_since = 0.0
        self._detected = threading.Event()
        self._detected.set()
        self._generation = 0
        # A stream being readied (a track change, a seek, the fallback): the
        # old one failing meanwhile is not the cast failing.
        self._busy = 0
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._sync)
        self._settle = self._one_shot(int(ROUTE_SETTLE_S * 1000) + 50,
                                      self._sync)
        self._menu_timer = self._one_shot(int(MENU_HINT_S * 1000),
                                          self._menu_hint)
        self._detect_timer = self._one_shot(int(DETECT_SETTLE_S * 1000),
                                            self._detect_settled)
        self._hide_timer = self._one_shot(int(PANEL_HIDE_S * 1000),
                                          self._hide_panel)
        cast(Any, self._call).connect(self._carry_out,
                                     Qt.ConnectionType.QueuedConnection)
        cast(Any, self._kick).connect(self._on_kick,
                                     Qt.ConnectionType.QueuedConnection)

    def _one_shot(self, ms: int, slot: Callable[[], None]) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.setInterval(ms)
        timer.timeout.connect(slot)
        return timer

    # -- what CastHub asks -------------------------------------------------------

    def knows(self, name: str | None) -> bool:
        return name == NAME

    def entries(self) -> list[str]:
        """The dialog's entry - unless AVRouteDetector, on while the dialog
        is open, has looked and found no AirPlay route. Called on the scan's
        worker thread, which waits for that first answer a moment."""
        if not supported():
            return []
        self._wait_for_routes()
        return [] if self._routes is False else [NAME]

    def scan(self) -> list[str]:
        return self.entries()

    def position(self) -> float:
        return self._offset + self._pos

    def bridged(self) -> bool:
        return (self.active is not None and self.plan is not None
                and not self.plan.direct)

    def volume(self) -> tuple[float, bool]:
        return self._volume, self._muted

    def set_volume(self, level: float) -> None:
        self._volume = max(0.0, min(1.0, float(level)))
        self._gui("volume", self._volume)

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self._gui("muted", self._muted)

    def pause(self) -> None:
        self._user_paused = True
        self._gui("pause")

    def resume(self) -> None:
        self._user_paused = False
        self._gui("play")

    def seek(self, to: float) -> None:
        """*to* seconds into the title. A converted film starts where the
        cast began (_offset): before that, the bridge starts over there."""
        to = max(0.0, float(to))
        if self.bridged() and to < self._offset:
            try:
                self._prepare(dict(self._req, start=to,
                                   generation=self._generation))
            except Exception as e:
                log.info("airplay: could not start over at %d s (%s)", to, e)
            return
        self._gui("seek", to - self._offset)

    def stop(self) -> None:
        self.active = None
        self._generation += 1
        self._gui("close")
        self.bridge.stop()

    def shutdown(self) -> None:
        self.stop()
        self._gui("detect", False)

    def show_window(self, *_args: Any) -> None:
        """Bring the AirPlay window to the front (the cast dialog's
        button, the cast strip's AirPlay button). Shown again on a route
        that holds, it stays until it is closed."""
        self._gui("show")

    def waiting_for_route(self) -> bool:
        """Apple's button not clicked yet, or the Apple TV has not taken
        the stream: what the cast strip says while it is so."""
        return self.active is not None and not self._external

    def cast(self, device_name: str, url: str, title: str,
             codecs: list[str] | None = None, audio: Any = None,
             subs: Any = None, start: float = 0.0, duration: float = 0.0,
             settle: bool = False, source: str | None = None,
             quality: str = "original", height: int = 0, fps: float = 0.0,
             dvr: bool = False, feed: Any = None) -> str:
        """Ready *url* for the Apple TV and open the AirPlay window; returns
        *device_name*. Runs on a worker thread: the probe and the bridge's
        first segments take seconds. Subtitles and the picture tiers are
        Chromecast's; a chosen sound track goes through the bridge. *feed*:
        the archive or the rewind buffer in pieces (cast_feed.Feed), read
        by the bridge into one playlist - never handed to the TV."""
        if not supported():
            raise LocalizedError("airplay_unavailable")
        src = source or url
        ctx = self.context() if callable(self.context) else {}
        kind = (ctx or {}).get("kind")
        live = feed is not None or (
            kind == "live" if kind else
            bool(dvr) or ab.endless(src) or
            (duration <= 0 and not nerd.is_own_file(src)))
        track = 0
        if isinstance(audio, dict):
            try:
                track = max(0, int(audio.get("index") or 0))
            except (TypeError, ValueError):
                track = 0
        self._generation += 1
        gen = self._generation
        self._fell_back = False
        log.info("airplay: cast asked (%s)", "a channel" if live else "a title")
        self._prepare({"url": url, "source": src, "codecs": list(codecs or []),
                       "track": track, "live": live, "start": float(start or 0),
                       "duration": float(duration or 0), "settle": settle,
                       "generation": gen, "feed": feed})
        if self._generation != gen:
            raise LocalizedError("cast_stopped")
        self.active = device_name
        return device_name

    # -- getting the stream ready (a worker thread) ------------------------------

    def _network(self, url: str) -> tuple[list[str], bool]:
        """ffmpeg's input options as the source plays (Nerd), and whether
        the source sends anything of its own the Apple TV would not - the
        source the cast is from (nerd.cast_source). Its proxy and the
        headers that may hold a token are the app's bridge's to add
        (_headers, airplay_bridge.hand_over), never ffmpeg's arguments."""
        n, src = nerd.current(), nerd.cast_source()
        ua = n.user_agent(src)
        headers = n.headers(src)
        proxy = n.proxy(src, url)
        own = n.on and (bool(headers) or bool(proxy) or ua != nerd.DEFAULT_UA)
        return ab.input_options(url, ua, headers), own

    @staticmethod
    def _headers() -> dict[str, str]:
        """The headers of the source the cast is from (Nerd's), which the
        app's bridge adds to what the converter reads."""
        return nerd.current().headers(nerd.cast_source())

    def _prepare(self, req: dict[str, Any], force_bridge: bool = False) -> None:
        with self._lock:
            self._busy += 1
        try:
            self._ready(req, force_bridge)
        finally:
            with self._lock:
                self._busy -= 1

    def _ready(self, req: dict[str, Any], force_bridge: bool) -> None:
        url, src, feed = req["url"], req["source"], req.get("feed")
        if feed is not None:
            # The pieces are read here and the TV gets the playlist; what is
            # probed (when nothing is known) is the first piece.
            src = feed.first_url or src
        net, own = self._network(src)
        codecs = list(req["codecs"])
        p = ab.plan(url, src, codecs, req["live"], req["track"],
                    own or force_bridge or feed is not None)
        found: dict[str, Any] | None = None
        if codecs:
            self._remember(url or src, codecs)
        elif p.direct:
            # Nothing known of what is in it (a zap, a cast from a list):
            # HEVC in a provider's HLS plays on an Apple TV with a black
            # picture (ab.plan), so it is looked into first.
            codecs, found = self._look_into(p.url, url or src, net, req)
            if codecs:
                p = ab.plan(url, src, codecs, req["live"], req["track"],
                            own or force_bridge or feed is not None)
        duration = req["duration"]
        if p.direct:
            self.bridge.stop()
            target, offset, seek_to = p.url, 0.0, req["start"]
            log.info("airplay: the Apple TV fetches the stream itself (%s)",
                     p.why)
        else:
            if not self.bridge.available() and self.bridge.exe is None:
                raise LocalizedError("airplay_needs_ffmpeg")
            if ab.firewall_blocks():
                raise LocalizedError("airplay_firewall")
            if req.get("settle"):
                # Another channel on this cast: the old ffmpeg goes first,
                # so the provider has let go of its connection before this
                # one asks (the old item failing meanwhile is _busy).
                if self.bridge.running():
                    self.bridge.stop()
                time.sleep(SETTLE_S)
            last = self.plan
            if (feed is not None and not req["codecs"] and not force_bridge
                    and self.active is not None and last is not None and not last.direct):
                # A seek in the channel already on the TV: its archive or
                # rewind buffer is that channel, so what the bridge does with
                # it is known - not asked of the provider once more (one
                # more opening of the archive, seconds before the stream).
                p = replace(p, video=last.video, audio=last.audio, hevc=last.hevc,
                            adts=last.adts)
                log.info("airplay: a seek in the same channel - its stream as before")
            elif not codecs or force_bridge:
                if found is None:
                    found = ab.probe(p.url, net, self.bridge.exe, headers=self._headers())
                p = ab.refine(p, found)
                if not duration and found["duration"] > 0 \
                        and req["live"] is not True:
                    duration = found["duration"]
            gen = req["generation"]
            target = self.bridge.start(
                p, req["start"], net, feed=feed,
                net_for=lambda u: self._network(u)[0], headers=self._headers())
            if not self.bridge.wait_ready(
                    stopping=lambda: self._generation != gen):
                why = self.bridge.error
                self.bridge.stop()
                if self._generation != gen:
                    raise LocalizedError("cast_stopped")
                raise LocalizedError("airplay_bridge_failed", msg=why or "?")
            target, offset, seek_to = self.bridge.url, req["start"], 0.0
            log.info("airplay: the bridge has its first segments")
        self._open_blocking(target, offset, seek_to)
        # Only once the player has it: until then the strip asks about the
        # stream still playing.
        self._req = dict(req, codecs=codecs, settle=False)
        self.plan = p
        self.duration = 0.0 if p.live else duration
        self._gui("row")

    def _remember(self, address: str, codecs: list[str]) -> None:
        if not address or not codecs or privacy.playing_private():
            return
        key = hashlib.sha256(address.encode("utf-8", "replace")).hexdigest()
        self._seen.pop(key, None)
        self._seen[key] = list(codecs)
        while len(self._seen) > SEEN_MAX:
            self._seen.pop(next(iter(self._seen)))

    def _look_into(self, target: str, address: str, net: list[str],
                   req: dict[str, Any]) -> tuple[list[str], dict[str, Any] | None]:
        """What the stream at *target* holds - remembered from this session,
        else asked of ffmpeg - as codecs ([] when it cannot be told) and
        ffmpeg's answer (None when remembered). A quick look - the program
        map, a quarter of a megabyte - so a zap stays as quick as it was. On
        a zap the stream on the TV lets go first: paused - as quick as the
        TV's own swap to the next channel - or, through the bridge, ffmpeg
        stopped and the provider given its moment, as before."""
        key = hashlib.sha256((address or "").encode("utf-8", "replace")).hexdigest()
        known = self._seen.get(key)
        if known:
            log.info("airplay: the stream is %s, as cast before", ",".join(known))
            return list(known), None
        if req.get("settle"):
            if self.bridge.running():
                # ffmpeg held the provider's connection: it goes first, and
                # the provider has its moment, as a zap through the bridge.
                self.bridge.stop()
                time.sleep(SETTLE_S)
                req["settle"] = False       # let go once: not again before the bridge
            done = threading.Event()
            self._gui("let_go", done)
            done.wait(OPEN_WAIT_S)
        found = ab.probe(target, net, self.bridge.exe, headers=self._headers(),
                         quick=True)
        codecs = [c for c in [str(found.get("video") or "")]
                  + [str(a) for a in found.get("audio") or []] if c]
        self._remember(address, codecs)
        return codecs, found

    def _open_blocking(self, target: str, offset: float, seek_to: float) -> None:
        box: dict[str, Any] = {}
        done = threading.Event()
        self._gui("open", (target, offset, seek_to, box, done))
        if not done.wait(OPEN_WAIT_S):
            raise LocalizedError("airplay_failed", msg="no answer")
        if "error" in box:
            raise LocalizedError("airplay_failed", msg=box["error"])

    def _where(self, url: str) -> str:
        """An address for the log: no login, and not the bridge's token."""
        text = redact_url(url)
        token = (self.bridge.prefix or "").strip("/")
        return text.replace(token, "***") if token else text

    # -- the GUI thread ----------------------------------------------------------

    def _gui(self, name: str, arg: Any = None) -> None:
        if QThread.currentThread() is self.thread():
            self._carry_out(name, arg)
        else:
            self._call.emit(name, arg)

    def _carry_out(self, name: str, arg: Any) -> None:
        try:
            if name == "open":
                target, offset, seek_to, box, done = arg
                try:
                    self._open(target, offset, seek_to)
                except Exception as e:
                    box["error"] = f"{type(e).__name__}: {e}"
                    log.info("airplay: the player did not open (%s)", e)
                finally:
                    done.set()
                return
            if name == "close":
                self._close()
                return
            if name == "let_go":
                # The stream on the TV stops asking the provider: paused,
                # the route and the window kept for the next one.
                try:
                    if self._player is not None:
                        self._player.pause()
                finally:
                    arg.set()
                return
            if name == "row":
                self._show_row()
                return
            if name == "failed":
                self._finish(str(arg or "airplay_failed_play"))
                return
            if name == "detect":
                self._detect(bool(arg))
                return
            if name == "show":
                if self._panel is not None:
                    self._hide_timer.stop()
                    self._panel.show()
                    log.debug("airplay: the AirPlay window is shown again")
                return
            p = self._player
            if p is None:
                return
            if name == "play":
                if self._external and self._raw_ext:
                    p.play()
            elif name == "pause":
                p.pause()
            elif name == "seek":
                p.seekToTime_(cmtime(max(0.0, float(arg))))
            elif name == "volume":
                p.setVolume_(float(arg))
            elif name == "muted":
                self._apply_mute(p)
        except Exception as e:
            log.debug("airplay: %s: %s", name, e)

    def _open(self, target: str, offset: float, seek_to: float) -> None:
        f = _frameworks()
        nsurl = f.fd.NSURL.URLWithString_(target)
        if nsurl is None:
            raise ValueError("not an address")
        item = f.av.AVPlayerItem.playerItemWithURL_(nsurl)
        log.info("airplay: opening %s", self._where(target))
        self._unwatch_item()
        now = time.monotonic()
        first = self._player is None
        if first:
            p = f.av.AVPlayer.playerWithPlayerItem_(item)
            p.setAllowsExternalPlayback_(True)
            self._player = p
            self._raw_ext = self._external = self._ever_external = False
            self._raw_at, self._lost_at, self._swapped_at = now, 0.0, 0.0
            self._errors_said = 0
            log.info("airplay: AVPlayer made, external playback allowed (%s)",
                     bool(p.allowsExternalPlayback()))
        else:
            p = self._player
            p.replaceCurrentItemWithPlayerItem_(item)
            # A route already lost stays lost from now: the grace starts
            # over, not from before the new stream was readied.
            if self._ever_external and not self._external:
                self._lost_at = now
            if self._ever_external:
                self._swapped_at = now
            # A new bridge the TV has yet to ask: its time to reach it
            # starts now, not when the route first came up (a seek in the
            # archive said "not reached" at once).
            if self._external:
                self._external_since = now
            log.info("airplay: a new stream in the same player - the route "
                     "and the AirPlay window stay")
        self._offset, self._pos, self._note, self._note_error = (
            offset, 0.0, "", "")
        self._opened_at = now
        self._opened_url = target
        self._user_paused = False
        self._said_unreached = False
        p.setVolume_(self._volume)
        # Never on this Mac's speakers or screen: paused and muted until the
        # Apple TV has it; a new stream on a route already chosen goes on.
        self._apply_mute(p)
        if self._external and self._raw_ext:
            p.play()
        else:
            p.pause()
        if seek_to > 0:
            p.seekToTime_(cmtime(seek_to))
        self._watch_item(item)
        if first:
            self._watch(p, _PLAYER_KEYS, self._kvo_player)
            try:
                self._make_panel(p)
            except Exception:
                self._close()
                raise
        self.state = "BUFFERING/NONE" if self._external else "WAITING/ROUTE"
        self._timer.start()
        self._show_row()

    def _anchor_rect(self) -> tuple[int, int, int, int] | None:
        try:
            return self.anchor() if callable(self.anchor) else None
        except Exception as e:
            log.debug("airplay: the window did not say where the cast card is: %s", e)
            return None

    def _make_panel(self, player: Any) -> None:
        """The AirPlay window, once per cast: Apple's button for this
        player, which the user clicks."""
        try:
            panel = _Panel(player, self._observer(), _Panel.place(self._anchor_rect()))
        except Exception as e:
            log.info("airplay: the AirPlay window could not be made (%s: %s)",
                     type(e).__name__, e)
            raise
        self._panel = panel
        try:
            f = _frameworks()
            name = getattr(f.ak, "NSWindowWillCloseNotification",
                           "NSWindowWillCloseNotification")
            self._panel_notes.append(
                f.fd.NSNotificationCenter.defaultCenter()
                .addObserverForName_object_queue_usingBlock_(
                    name, panel.panel, f.fd.NSOperationQueue.mainQueue(),
                    _note_block(self, "window_closed")))
        except Exception as e:
            log.debug("airplay: not told when the window closes (%s)", e)
        panel.show(focus=True)
        log.info("airplay: the AirPlay window is open - waiting for an Apple "
                 "TV to be picked with its AirPlay button")

    # -- what Apple tells (KVO, notifications, the picker's delegate) -------------

    def _observer(self) -> Any:
        if self._obs is None and not self._obs_failed:
            try:
                obs = _observer_class().alloc().init()
                obs.sink = weakref.ref(self)
                self._obs = obs
            except Exception as e:
                self._obs_failed = True
                log.info("airplay: no key-value observing (%s: %s) - the 1 s "
                         "check reads the player instead", type(e).__name__, e)
        return self._obs

    def _watch(self, obj: Any, keys: tuple[str, ...],
               into: list[tuple[Any, str]]) -> None:
        obs = self._observer()
        if obs is None:
            return
        for key in keys:
            try:
                obj.addObserver_forKeyPath_options_context_(
                    obs, key, _KVO_NEW, None)
                into.append((obj, key))
            except Exception as e:
                say_once(f"airplay:kvo:{key}", "airplay: cannot observe %s "
                         "(%s) - the 1 s check covers it", key, e)

    def _unwatch(self, into: list[tuple[Any, str]]) -> None:
        for obj, key in into:
            try:
                obj.removeObserver_forKeyPath_(self._obs, key)
            except Exception as e:
                log.debug("airplay: removing the %s observer: %s", key, e)
        into.clear()

    def _watch_item(self, item: Any) -> None:
        f = _frameworks()
        center = f.fd.NSNotificationCenter.defaultCenter()
        queue = f.fd.NSOperationQueue.mainQueue()
        for key, what in _ITEM_NOTES:
            name = getattr(f.av, key, key)
            try:
                self._item_notes.append(
                    center.addObserverForName_object_queue_usingBlock_(
                        name, item, queue, _note_block(self, what)))
            except Exception as e:
                log.debug("airplay: no %s (%s)", key, e)
        self._watch(item, _ITEM_KEYS, self._kvo_item)

    def _unwatch_item(self) -> None:
        self._unwatch(self._kvo_item)
        self._forget(self._item_notes)

    def _forget(self, tokens: list[Any]) -> None:
        if not tokens:
            return
        try:
            center = _frameworks().fd.NSNotificationCenter.defaultCenter()
            for token in tokens:
                center.removeObserver_(token)
        except Exception as e:
            log.debug("airplay: removing an observer: %s", e)
        tokens.clear()

    def _on_kick(self, what: str) -> None:
        try:
            if what == "routes":
                self._read_routes()
                return
            if what == "window_closed":
                self._window_closed()
                return
            self._explain(what)
            if what == "menu_closed":
                self._menu_timer.start()
        except Exception as e:
            log.debug("airplay: %s: %s", what, e)
        self._sync()

    def _explain(self, what: str) -> None:
        """What changed, in the log. The route itself is logged where it is
        read (_route), whichever way the change was seen first."""
        p = self._player
        if p is None:
            return
        if what == "timeControlStatus":
            tc = int(p.timeControlStatus())
            reason = ""
            if tc == _TC_WAITING:
                reason = str(p.reasonForWaitingToPlay() or "")
            log.debug("airplay: the player is %s%s", _TC_NAMES.get(tc, tc),
                      f" ({reason})" if reason else "")
        elif what == "status":
            item = p.currentItem()
            if item is None:
                return
            st = int(item.status())
            if st != _FAILED:           # a failure is told by _failed, with why
                log.info("airplay: the stream is %s", _ITEM_STATUS.get(st, st))
        elif what == "menu_open":
            log.info("airplay: Apple's AirPlay menu opened")
        elif what == "menu_closed":
            log.info("airplay: Apple's AirPlay menu closed (external playback "
                     "%s)", "on" if _external(p) else "off")
        elif what == "stalled":
            log.info("airplay: playback stalled - the stream did not come "
                     "fast enough")
        elif what == "errorlog":
            self._log_error_entry(p.currentItem())
        elif what == "end":
            log.info("airplay: the title played to its end")

    def _log_error_entry(self, item: Any) -> None:
        """The newest entry of the item's error log: an HLS playlist or
        segment the player or the Apple TV could not read, and why."""
        if item is None or self._errors_said >= ERROR_LOG_MAX:
            return
        errors = item.errorLog()
        events = errors.events() if errors is not None else None
        if not events:
            return
        e = events[len(events) - 1]
        self._errors_said += 1
        log.info("airplay: AVFoundation's error log: %s %s: %s (%s)",
                 e.errorDomain(), int(e.errorStatusCode()),
                 _clean(str(e.errorComment() or "")),
                 self._where(str(e.URI() or "")))

    def _menu_hint(self) -> None:
        p = self._player
        if p is None or self._external or _external(p):
            return
        log.info("airplay: Apple's menu closed %d s ago and the player is not "
                 "on an AirPlay route - no Apple TV picked, or it did not take "
                 "the stream", MENU_HINT_S)

    def _hide_panel(self) -> None:
        """The Apple TV has held the stream for PANEL_HIDE_S: the panel
        steps aside - only while the route still holds; one that went
        meanwhile leaves Apple's button where the user can reach it."""
        panel = self._panel
        if panel is None or not (self._external and self._raw_ext):
            return
        try:
            panel.hide()
            log.info("airplay: on the Apple TV - the AirPlay window steps "
                     "aside (the cast strip's AirPlay button brings it back)")
        except Exception as e:
            log.debug("airplay: hiding the AirPlay window: %s", e)

    def _window_closed(self) -> None:
        if self._closing or self._player is None:
            return
        if self._ever_external or self._raw_ext:
            log.info("airplay: the AirPlay window was closed; the cast goes on "
                     "(the cast panel brings the window back)")
            return
        log.info("airplay: the AirPlay window was closed before an Apple TV "
                 "was picked")
        self._finish("airplay_no_route")

    # -- AVRouteDetector, while the cast dialog is open ---------------------------

    def _detect(self, on: bool) -> None:
        """Apple says route detection costs power: it runs only while the
        cast dialog is open."""
        if not on:
            self._detect_timer.stop()
            if self._detector is not None and self._detecting_since:
                try:
                    self._detector.setRouteDetectionEnabled_(False)
                except Exception as e:
                    log.debug("airplay: route detection off: %s", e)
                log.debug("airplay: route detection off")
            self._routes, self._detecting_since = None, 0.0
            self._detected.set()
            return
        try:
            det = self._detector
            if det is None:
                f = _frameworks()
                det = f.av.AVRouteDetector.alloc().init()
                self._detector_notes.append(
                    f.fd.NSNotificationCenter.defaultCenter()
                    .addObserverForName_object_queue_usingBlock_(
                        getattr(f.av, _ROUTES_NOTE, _ROUTES_NOTE), det,
                        f.fd.NSOperationQueue.mainQueue(),
                        _note_block(self, "routes")))
                self._detector = det
            det.setRouteDetectionEnabled_(True)
        except Exception as e:
            say_once("airplay:detect", "airplay: no route detection (%s: %s) "
                     "- AirPlay stays listed", type(e).__name__, e)
            self._routes, self._detecting_since = None, 0.0
            self._detected.set()
            return
        self._routes, self._detecting_since = None, time.monotonic()
        self._detected.clear()
        log.debug("airplay: looking for AirPlay routes (AVRouteDetector)")
        self._detect_timer.start()
        self._read_routes()

    def _detect_settled(self) -> None:
        self._read_routes(settled=True)

    def _read_routes(self, settled: bool = False) -> None:
        det = self._detector
        if det is None or not self._detecting_since:
            return
        try:
            found = bool(det.multipleRoutesDetected())
        except Exception as e:
            log.debug("airplay: reading route detection: %s", e)
            self._routes = None
            self._detected.set()
            return
        if not found and not settled and \
                time.monotonic() - self._detecting_since < DETECT_SETTLE_S:
            return                  # no answer yet: it starts out as "no"
        was, self._routes = self._routes, found
        self._detected.set()
        if found != was:
            log.info("airplay: AVRouteDetector %s", "found AirPlay routes"
                     if found else "found no AirPlay route")
        if found:
            self.routes_changed.emit(True)

    def _wait_for_routes(self) -> None:
        since = self._detecting_since
        if not since or self._detected.is_set() \
                or QThread.currentThread() is self.thread():
            return
        left = since + DETECT_SETTLE_S + 0.5 - time.monotonic()
        if left > 0:
            self._detected.wait(left)

    # -- the cast's own life ------------------------------------------------------

    def _apply_mute(self, p: Any) -> None:
        """Sound only on the Apple TV: muted unless the route is on and has
        held, or as the strip says."""
        p.setMuted_(bool(self._muted or not (self._external and self._raw_ext)))

    def _close(self) -> None:
        self._timer.stop()
        self._settle.stop()
        self._menu_timer.stop()
        self._hide_timer.stop()
        self._closing = True
        try:
            # Observers first, while what they watch is still there.
            self._unwatch_item()
            self._unwatch(self._kvo_player)
            self._forget(self._panel_notes)
            panel, self._panel = self._panel, None
            if panel is not None:
                panel.close()
            p, self._player = self._player, None
            if p is not None:
                try:
                    p.pause()
                    p.replaceCurrentItemWithPlayerItem_(None)
                except Exception as e:
                    log.debug("airplay: closing the player: %s", e)
                log.debug("airplay: the player and the AirPlay window are "
                          "closed")
        finally:
            self._closing = False
        self._raw_ext = self._external = self._ever_external = False
        self._swapped_at = 0.0
        self.state = "IDLE/NONE"
        self._show_row()

    def _finish(self, why: str) -> None:
        """The cast is over by itself: the route gone, nothing picked, the
        stream failed. The bridge is stopped off this thread - ffmpeg can
        take a second to go."""
        if self.active is None and self._player is None:
            return
        log.info("airplay: ended (%s)", why)
        self.active = None
        self._generation += 1
        self._close()
        threading.Thread(target=self.bridge.stop, daemon=True).start()
        self.ended.emit(why)

    def _sync(self) -> None:
        """Read the player and act on it: from a kick, the settle timer or
        the 1 s tick - the same answer whichever comes first."""
        p = self._player
        if p is None:
            self._timer.stop()
            return
        try:
            self._sync_player(p)
        except Exception as e:
            say_once("airplay:sync", "airplay: reading the player: %s", e)

    def _sync_player(self, p: Any) -> None:
        now = time.monotonic()
        item = p.currentItem()
        if not self._busy and (self._note == "failed" or (
                item is not None and int(item.status()) == _FAILED)):
            self._note = ""
            self._failed(item)
            return
        if not self._route(p, now):
            return
        t = seconds(p.currentTime())
        if t is not None:
            self._pos = max(0.0, t)
        if item is not None and not self.duration and self.plan is not None \
                and not self.plan.live:
            d = seconds(item.duration())
            if d and d > 0:
                self.duration = self._offset + d
        if self._note == "end":
            self.state = "IDLE/FINISHED"
        elif not self._external:
            self.state = "WAITING/ROUTE"
        else:
            tc = int(p.timeControlStatus())
            self.state = ("PLAYING/NONE" if tc == _TC_PLAYING else
                          "BUFFERING/NONE" if tc == _TC_WAITING else
                          "PAUSED/NONE")
        if (self._external and self.bridged() and not self.bridge.remote_seen
                and self._opened_url == self.bridge.url
                and not self._said_unreached
                and now - self._external_since > UNREACHED_S):
            # On the TV, and the TV never asked: macOS's firewall said no,
            # or the TV cannot see this Mac's address.
            self._said_unreached = True
            log.info("airplay: the Apple TV has not reached the bridge")
            self.problem.emit("airplay_unreachable")
        self._show_row()

    def _route(self, p: Any, now: float) -> bool:
        """The route, debounced: externalPlaybackActive moves the player
        only once it has held for ROUTE_SETTLE_S. Off, the sound goes at
        once; the pause, the grace and the end follow. False once the cast
        has ended here."""
        raw = _external(p)
        if raw != self._raw_ext:
            self._raw_ext, self._raw_at = raw, now
            log.info("airplay: externalPlaybackActive is %s", raw)
            self._apply_mute(p)
            self._settle.start()
        if raw != self._external and now - self._raw_at >= ROUTE_SETTLE_S:
            self._external = raw
            if raw:
                self._ever_external, self._lost_at = True, 0.0
                self._external_since = now
                self._apply_mute(p)
                self._hide_timer.start()
                if not self._user_paused and self._note != "end":
                    p.play()
                log.info("airplay: on the Apple TV - %s", "paused as asked"
                         if self._user_paused else "playing there")
            else:
                p.pause()
                self._apply_mute(p)
                if self._ever_external:
                    self._lost_at = now
                log.info("airplay: no longer on an AirPlay route - paused")
        if not raw and not self._external and float(p.rate()) != 0.0:
            p.pause()                   # nothing plays on this Mac
            log.debug("airplay: paused - not on an AirPlay route")
        if self._external or self._busy or self._swapping(p, now):
            return True
        if self._ever_external:
            if self._lost_at and now - self._lost_at > ROUTE_GRACE_S:
                self._finish("airplay_route_lost")
                return False
        elif now - self._opened_at > ROUTE_WAIT_S:
            self._finish("airplay_no_route")
            return False
        return True

    def _swapping(self, p: Any, now: float) -> bool:
        """A new item on a route the Apple TV had, not ready yet (for
        SWAP_GRACE_S at most): the route going meanwhile is the TV loading
        it, not the cast ending."""
        if not self._swapped_at or now - self._swapped_at > SWAP_GRACE_S:
            return False
        item = p.currentItem()
        try:
            ready = item is not None and int(item.status()) == 1
        except Exception:
            ready = False
        if ready:
            # Loaded: a route still gone has the ordinary grace from now.
            self._swapped_at = 0.0
            if self._lost_at:
                self._lost_at = now
        return not ready

    def _failed(self, item: Any) -> None:
        why = self._note_error
        if not why and item is not None:
            try:
                why = _error_text(item.error())
            except Exception:
                why = ""
        self._note_error = ""
        log.info("airplay: the stream did not play (%s)",
                 why or "no error given")
        if self.plan is not None and self.plan.direct and not self._fell_back \
                and self._req:
            # The address as it was did not play on the Apple TV: once
            # more through the bridge, which reads it as the player does.
            self._fell_back = True
            log.info("airplay: trying again through the bridge")
            with self._lock:
                self._busy += 1     # before the thread: no second failure
            threading.Thread(target=self._fall_back, args=(self._generation,),
                             daemon=True).start()
            return
        self._finish("airplay_failed_play")

    def _fall_back(self, generation: int) -> None:
        try:
            self._prepare(dict(self._req, start=self.position(),
                               generation=generation), force_bridge=True)
        except Exception as e:
            log.info("airplay: the bridge did not help (%s)", e)
            if self._generation == generation:
                self._call.emit("failed", "airplay_failed_play")
        finally:
            with self._lock:
                self._busy -= 1

    # -- the cast dialog's row (plain Qt) -----------------------------------------

    def dialog_row(self, parent: QWidget) -> QWidget:
        """The AirPlay row of the cast dialog: what to do next, and a button
        that brings the AirPlay window back - Apple's button is in that
        window, never in the dialog. While the dialog is open,
        AVRouteDetector looks for AirPlay routes."""
        holder = QWidget(parent)
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        label = QLabel(holder)
        label.setWordWrap(True)
        label.setObjectName("DetailMeta")
        button = QPushButton(tr("airplay_show_window"), holder)
        button.clicked.connect(self.show_window)
        button.hide()
        lay.addWidget(label, 1)
        lay.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
        self._row = (label, button)
        finished = getattr(parent, "finished", None)
        if finished is not None:
            finished.connect(self._dialog_finished)
        self._gui("detect", True)
        self._show_row()
        return holder

    def _dialog_finished(self, *_args: Any) -> None:
        self._row = None
        self._detect(False)

    def _row_alive(self) -> tuple[QLabel, QPushButton] | None:
        if self._row is None:
            return None
        from PyQt6 import sip
        if any(sip.isdeleted(w) for w in self._row):
            self._row = None
            return None
        return self._row

    def _show_row(self) -> None:
        row = self._row_alive()
        if row is None:
            return
        label, button = row
        if self._player is None:
            text = tr("airplay_note")
        elif self._external:
            text = tr("airplay_on_tv_window")
        else:
            text = tr("airplay_pick_window")
        if self.plan is not None and self.plan.costs_cpu and self._player is not None:
            text += "\n" + tr("airplay_cpu")
        if label.text() != text:
            label.setText(text)
        want = self._panel is not None
        if button.isHidden() == want:
            button.setVisible(want)
