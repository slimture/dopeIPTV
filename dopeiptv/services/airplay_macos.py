"""macOS: AirPlay to an Apple TV through Apple's own frameworks.

An AVPlayer that allows external playback, and AVKit's AVRoutePickerView
with that player set: the user picks the Apple TV in Apple's own AirPlay
menu and the Apple TV fetches the stream itself - HLS or MP4 as it is, or
what providers/airplay_bridge.py makes of anything else. The player never
plays on this Mac: it starts once the Apple TV has it and pauses the moment
it has not.

AirPlayReceiver is one more kind of receiver behind CastHub (the window
adds it on macOS, ui/mw_airplay.py): it answers what the cast strip asks -
position, state, pause, seek, volume, stop - from what the GUI thread's
poll last read off the player. AVFoundation is driven on the GUI thread
only: calls from the cast's worker threads are queued onto it, as the
system's media controls are (services/media_session.py), and Apple's end
notifications run on the main queue, which is that thread. An exception
never crosses back into Objective-C.

pyobjc-framework-AVFoundation and -AVKit are macOS-only requirements;
without them there is no AirPlay entry and the app runs as before.
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
import weakref
from typing import Any, Callable

from PyQt6.QtCore import QObject, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..core import nerd
from ..core.log import log
from ..i18n import LocalizedError, tr
from ..providers import airplay_bridge as ab
from .media_session import say_once

#: The entry in the cast dialog's list (CastHub routes it here).
NAME = "AirPlay (Apple TV)"
POLL_MS = 500
ROUTE_WAIT_S = 120.0    # how long Apple's menu may go unanswered
ROUTE_GRACE_S = 4.0     # moving to another Apple TV drops the route a moment
UNREACHED_S = 20.0      # on the TV that long and nothing asked the bridge
OPEN_WAIT_S = 10.0      # the GUI thread making the player
SETTLE_S = 2.0          # a provider still counting the player's connection

_NEEDS = ("objc", "Foundation", "AppKit", "AVFoundation", "AVKit")
# AVPlayerStatus / AVPlayerItemStatus, AVPlayerTimeControlStatus
_FAILED = 2
_TC_PAUSED, _TC_WAITING, _TC_PLAYING = 0, 1, 2
# CMTimeFlags: valid, and none of +inf, -inf, indefinite.
_CM_VALID, _CM_NOT_A_NUMBER = 1, 4 | 8 | 16
# NSViewWidthSizable | NSViewHeightSizable
_FILL = 2 | 16


def _frameworks() -> tuple[Any, Any, Any, Any, Any]:
    import AppKit
    import AVFoundation
    import AVKit
    import Foundation
    import objc
    return AVFoundation, AVKit, Foundation, AppKit, objc


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
    av, kit, fd, ak, objc = _frameworks()
    for module, names in (
            (av, ("AVPlayer", "AVPlayerItem")),
            (kit, ("AVRoutePickerView",)),
            (fd, ("NSURL", "NSNotificationCenter", "NSOperationQueue")),
            (ak, ("NSButton",)),
            (objc, ("objc_object",))):
        for name in names:
            getattr(module, name)


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


class _Note:
    """An AVPlayerItem notification's block: it only writes down what
    happened, for the next poll."""

    def __init__(self, receiver: "AirPlayReceiver", what: str) -> None:
        self._rx = weakref.ref(receiver)
        self.what = what

    def __call__(self, _note: Any) -> None:
        try:
            rx = self._rx()
            if rx is not None:
                rx._note = self.what
        except Exception:
            pass


class AirPlayReceiver(QObject):
    """AirPlay as CastHub sees a receiver: one entry, one cast at a time."""

    KIND = "airplay"
    #: The cast ended by itself; a string key says why (tr), "" for none.
    ended = pyqtSignal(str)
    #: Something the user should know while it plays (a string key).
    problem = pyqtSignal(str)
    _call = pyqtSignal(str, object)

    def __init__(self, parent: QObject | None = None,
                 bridge: ab.AirPlayBridge | None = None) -> None:
        super().__init__(parent)
        self.bridge = bridge or ab.AirPlayBridge()
        #: The window's cast context (ui/mw_airplay.py): says whether a
        #: channel or a film is cast, which an address cannot always tell.
        self.context: Callable[[], dict] | None = None
        self.active: str | None = None
        self.state = "IDLE/NONE"
        self.duration = 0.0
        self.plan: ab.Plan | None = None
        self._req: dict[str, Any] = {}
        self._offset = 0.0
        self._pos = 0.0
        self._volume, self._muted = 1.0, False
        self._player: Any = None
        self._picker: Any = None
        self._observers: list[Any] = []
        self._note = ""
        self._external = False
        self._ever_external = False
        self._external_since = 0.0
        self._lost_at = 0.0
        self._opened_at = 0.0
        self._user_paused = False
        self._fell_back = False
        self._said_unreached = False
        self._row: tuple[QWidget, QLabel] | None = None
        self._generation = 0
        # A stream being readied (a track change, a seek, the fallback): the
        # old one failing meanwhile is not the cast failing.
        self._busy = 0
        self._lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._call.connect(self._carry_out, Qt.ConnectionType.QueuedConnection)

    # -- what CastHub asks -------------------------------------------------------

    def knows(self, name: str | None) -> bool:
        return name == NAME

    def entries(self) -> list[str]:
        return [NAME] if supported() else []

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

    def cast(self, device_name: str, url: str, title: str,
             codecs: list[str] | None = None, audio: Any = None,
             subs: Any = None, start: float = 0.0, duration: float = 0.0,
             settle: bool = False, source: str | None = None,
             quality: str = "original", height: int = 0, fps: float = 0.0,
             dvr: bool = False) -> str:
        """Ready *url* for the Apple TV and open Apple's menu; returns
        *device_name*. Runs on a worker thread: the probe and the bridge's
        first segments take seconds. Subtitles and the picture tiers are
        Chromecast's; a chosen sound track goes through the bridge."""
        if not supported():
            raise LocalizedError("airplay_unavailable")
        src = source or url
        ctx = self.context() if callable(self.context) else {}
        kind = (ctx or {}).get("kind")
        live = (kind == "live" if kind else
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
        self._prepare({"url": url, "source": src, "codecs": list(codecs or []),
                       "track": track, "live": live, "start": float(start or 0),
                       "duration": float(duration or 0), "settle": settle,
                       "generation": gen})
        if self._generation != gen:
            raise LocalizedError("cast_stopped")
        self.active = device_name
        return device_name

    # -- getting the stream ready (a worker thread) ------------------------------

    def _network(self, url: str) -> tuple[list[str], bool]:
        """ffmpeg's input options as the source plays (Nerd), and whether
        the source sends anything of its own the Apple TV would not."""
        n, src = nerd.current(), nerd.source()
        ua = n.user_agent(src)
        headers = n.headers(src)
        proxy = n.proxy(src, url)
        own = n.on and (bool(headers) or bool(proxy) or ua != nerd.DEFAULT_UA)
        return ab.input_options(url, ua, headers, proxy), own

    def _prepare(self, req: dict[str, Any], force_bridge: bool = False) -> None:
        with self._lock:
            self._busy += 1
        try:
            self._ready(req, force_bridge)
        finally:
            with self._lock:
                self._busy -= 1

    def _ready(self, req: dict[str, Any], force_bridge: bool) -> None:
        url, src = req["url"], req["source"]
        net, own = self._network(src)
        p = ab.plan(url, src, req["codecs"], req["live"], req["track"],
                    own or force_bridge)
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
                time.sleep(SETTLE_S)
            if not req["codecs"] or force_bridge:
                found = ab.probe(p.url, net, self.bridge.exe)
                p = ab.refine(p, found)
                if not duration and found["duration"] > 0 \
                        and req["live"] is not True:
                    duration = found["duration"]
            gen = req["generation"]
            target = self.bridge.start(p, req["start"], net)
            if not self.bridge.wait_ready(
                    stopping=lambda: self._generation != gen):
                why = self.bridge.error
                self.bridge.stop()
                if self._generation != gen:
                    raise LocalizedError("cast_stopped")
                raise LocalizedError("airplay_bridge_failed", msg=why or "?")
            target, offset, seek_to = self.bridge.url, req["start"], 0.0
        self._open_blocking(target, offset, seek_to)
        # Only once the player has it: until then the strip asks about the
        # stream still playing.
        self._req = dict(req, codecs=req["codecs"], settle=False)
        self.plan = p
        self.duration = 0.0 if p.live else duration
        self._gui("row")

    def _open_blocking(self, target: str, offset: float, seek_to: float) -> None:
        box: dict[str, Any] = {}
        done = threading.Event()
        self._gui("open", (target, offset, seek_to, box, done))
        if not done.wait(OPEN_WAIT_S):
            raise LocalizedError("airplay_failed", msg="no answer")
        if "error" in box:
            raise LocalizedError("airplay_failed", msg=box["error"])

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
            if name == "row":
                self._show_row()
                return
            if name == "failed":
                self._finish(str(arg or "airplay_failed_play"))
                return
            p = self._player
            if p is None:
                return
            if name == "play":
                if self._external:
                    p.play()
            elif name == "pause":
                p.pause()
            elif name == "seek":
                p.seekToTime_(cmtime(max(0.0, float(arg))))
            elif name == "volume":
                p.setVolume_(float(arg))
            elif name == "muted":
                p.setMuted_(bool(arg))
        except Exception as e:
            log.debug("airplay: %s: %s", name, e)

    def _open(self, target: str, offset: float, seek_to: float) -> None:
        av, _kit, fd, _ak, _objc = _frameworks()
        nsurl = fd.NSURL.URLWithString_(target)
        if nsurl is None:
            raise ValueError("not an address")
        item = av.AVPlayerItem.playerItemWithURL_(nsurl)
        self._unobserve()
        if self._player is None:
            self._player = av.AVPlayer.playerWithPlayerItem_(item)
            self._player.setAllowsExternalPlayback_(True)
            self._ever_external = False
            self._external = False
        else:
            self._player.replaceCurrentItemWithPlayerItem_(item)
        p = self._player
        # Never on this Mac's speakers: it plays once the Apple TV has it
        # (_poll), and a new item on a route already chosen plays at once.
        p.pause()
        p.setVolume_(self._volume)
        p.setMuted_(self._muted)
        if seek_to > 0:
            p.seekToTime_(cmtime(seek_to))
        self._observe(item)
        self._offset, self._pos, self._note = offset, 0.0, ""
        self._opened_at = time.monotonic()
        self._user_paused = False
        self._said_unreached = False
        self.state = "BUFFERING/NONE" if self._external else "WAITING/ROUTE"
        self._timer.start()
        self._show_row(open_menu=not self._external)

    def _observe(self, item: Any) -> None:
        av, _kit, fd, _ak, _objc = _frameworks()
        center = fd.NSNotificationCenter.defaultCenter()
        queue = fd.NSOperationQueue.mainQueue()
        for key, what in (("AVPlayerItemDidPlayToEndTimeNotification", "end"),
                          ("AVPlayerItemFailedToPlayToEndTimeNotification",
                           "failed")):
            name = getattr(av, key, key)
            try:
                self._observers.append(
                    center.addObserverForName_object_queue_usingBlock_(
                        name, item, queue, _Note(self, what)))
            except Exception as e:
                log.debug("airplay: no %s (%s)", key, e)

    def _unobserve(self) -> None:
        if not self._observers:
            return
        try:
            _av, _kit, fd, _ak, _objc = _frameworks()
            center = fd.NSNotificationCenter.defaultCenter()
            for token in self._observers:
                center.removeObserver_(token)
        except Exception as e:
            log.debug("airplay: removing an observer: %s", e)
        self._observers.clear()

    def _close(self) -> None:
        self._timer.stop()
        self._unobserve()
        p, self._player = self._player, None
        if p is not None:
            try:
                p.pause()
                p.replaceCurrentItemWithPlayerItem_(None)
            except Exception as e:
                log.debug("airplay: closing the player: %s", e)
        if self._picker is not None:
            try:
                self._picker.setPlayer_(None)
            except Exception:
                pass
        self._external = self._ever_external = False
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

    def _poll(self) -> None:
        p = self._player
        if p is None:
            self._timer.stop()
            return
        if self._busy:
            return
        try:
            self._poll_player(p)
        except Exception as e:
            say_once("airplay:poll", "airplay: reading the player: %s", e)

    def _poll_player(self, p: Any) -> None:
        now = time.monotonic()
        item = p.currentItem()
        failed = self._note == "failed" or (
            item is not None and int(item.status()) == _FAILED)
        if failed:
            self._note = ""
            self._failed(item)
            return
        ext = _external(p)
        if ext and not self._external:
            self._external_since = now
            log.info("airplay: playing on the Apple TV")
        self._external = ext
        ended = self._note == "end"
        if ext:
            self._ever_external, self._lost_at = True, 0.0
            if not self._user_paused and not ended and float(p.rate()) == 0.0:
                p.play()
        else:
            if float(p.rate()) != 0.0:
                p.pause()
            if self._ever_external:
                self._lost_at = self._lost_at or now
                if now - self._lost_at > ROUTE_GRACE_S:
                    self._finish("airplay_route_lost")
                    return
            elif now - self._opened_at > ROUTE_WAIT_S:
                self._finish("airplay_no_route")
                return
        t = seconds(p.currentTime())
        if t is not None:
            self._pos = max(0.0, t)
        if item is not None and not self.duration and self.plan is not None \
                and not self.plan.live:
            d = seconds(item.duration())
            if d and d > 0:
                self.duration = self._offset + d
        if ended:
            self.state = "IDLE/FINISHED"
        elif not ext:
            self.state = "WAITING/ROUTE"
        else:
            tc = int(p.timeControlStatus())
            self.state = ("PLAYING/NONE" if tc == _TC_PLAYING else
                          "BUFFERING/NONE" if tc == _TC_WAITING else
                          "PAUSED/NONE")
        if (ext and self.bridged() and not self.bridge.remote_seen
                and not self._said_unreached
                and now - self._external_since > UNREACHED_S):
            # On the TV, and the TV never asked: macOS's firewall said no,
            # or the TV cannot see this Mac's address.
            self._said_unreached = True
            log.info("airplay: the Apple TV has not reached the bridge")
            self.problem.emit("airplay_unreachable")
        self._show_row()

    def _failed(self, item: Any) -> None:
        why = ""
        try:
            err = item.error() if item is not None else None
            why = str(err.localizedDescription()) if err is not None else ""
        except Exception:
            pass
        log.info("airplay: the stream did not play%s", f" ({why})" if why else "")
        if self.plan is not None and self.plan.direct and not self._fell_back \
                and self._req:
            # The address as it was did not play on the Apple TV: once
            # more through the bridge, which reads it as the player does.
            self._fell_back = True
            log.info("airplay: trying again through the bridge")
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

    # -- Apple's menu in the cast dialog ------------------------------------------

    def dialog_row(self, parent: QWidget) -> QWidget:
        """The AirPlay row of the cast dialog: Apple's AirPlay button (an
        AVRoutePickerView in a native child of the dialog, never of the main
        window, where the video surface is) and what to do next."""
        holder = QWidget(parent)
        lay = QHBoxLayout(holder)
        lay.setContentsMargins(0, 0, 0, 0)
        host = QWidget(holder)
        host.setFixedSize(36, 28)
        host.setToolTip(tr("airplay_button"))
        host.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        host.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        host.hide()
        label = QLabel(holder)
        label.setWordWrap(True)
        label.setObjectName("DetailMeta")
        lay.addWidget(host)
        lay.addWidget(label, 1)
        self._row = (host, label)
        self._show_row()
        return holder

    def _row_alive(self) -> tuple[QWidget, QLabel] | None:
        if self._row is None:
            return None
        from PyQt6 import sip
        if any(sip.isdeleted(w) for w in self._row):
            self._row = None
            return None
        return self._row

    def _show_row(self, open_menu: bool = False) -> None:
        row = self._row_alive()
        if row is None:
            return
        host, label = row
        if self._player is None:
            text = tr("airplay_note")
        elif self._external:
            text = tr("airplay_on_tv")
        else:
            text = tr("airplay_pick")
        if self.plan is not None and self.plan.costs_cpu and self._player is not None:
            text += "\n" + tr("airplay_cpu")
        if label.text() != text:
            label.setText(text)
        if self._player is None:
            host.hide()
            return
        if host.isHidden():
            host.show()
            self._attach(host, open_menu)
        elif open_menu:
            self._attach(host, True)

    def _attach(self, host: QWidget, open_menu: bool) -> None:
        """Apple's AirPlay button into *host*, for this cast's player."""
        try:
            _av, kit, _fd, _ak, objc = _frameworks()
            view = objc.objc_object(c_void_p=int(host.winId()))
            picker = self._picker
            if picker is None:
                picker = kit.AVRoutePickerView.alloc().initWithFrame_(
                    view.bounds())
                self._picker = picker
            elif picker.superview() is not None:
                picker.removeFromSuperview()
            picker.setFrame_(view.bounds())
            picker.setAutoresizingMask_(_FILL)
            view.addSubview_(picker)
            picker.setPlayer_(self._player)
        except Exception as e:
            say_once("airplay:picker", "airplay: no AirPlay button (%s: %s)",
                     type(e).__name__, e)
            return
        if open_menu:
            QTimer.singleShot(250, self._open_menu)

    def _open_menu(self) -> None:
        """Open Apple's menu as if its button were clicked - the button is
        there for the user to click whenever this does not."""
        picker = self._picker
        if picker is None or self._player is None or self._external:
            return
        try:
            _av, _kit, _fd, ak, _objc = _frameworks()
            for view in list(picker.subviews() or []):
                if view.isKindOfClass_(ak.NSButton):
                    view.performClick_(None)
                    return
        except Exception as e:
            say_once("airplay:menu", "airplay: the menu did not open by "
                     "itself (%s)", e)
