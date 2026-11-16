"""MainWindow mixin: TV mode - the window as a 10-foot interface, run from
a remote.

On and off from the View menu, its key (Ctrl+Shift+T), ``--tv`` and
"Start in TV mode" (Settings > General). On, the window fills the screen
with its own page (ui/tv_mode.py) in the central stack; a title plays
through the window's own path (play_live_channel, _start_playback - the
VPN guard, the connection count and the rest apply as everywhere), and
whatever starts a stream meanwhile - a screen here, the phone remote, a
command - brings the picture up full screen (the docked player's
fullscreen), with a big info bar on any key and channel up/down zapping.
Back over the picture goes back to the screen it came from (a number being
typed and the info bar go first), and so do the guide, search and options
keys to theirs: the stream goes on behind the page, Now playing above its
tabs, and Back, Play or OK on Now playing return to it. Stop stops it.

Keys come to one event filter while TV mode is on: every key without
Ctrl, Alt or Meta is TV mode's (a remote that acts as a keyboard, a CEC
remote the system turns into keys), mapped to the actions of
core/tv_input.py; a game controller and LIRC send the same actions from
their threads. A dialog or menu that opens keeps its keys: the actions of
a pad or LIRC become keys for it.

The phone remote, the remote API and Home Assistant turn it on and off and
press its keys through one method, tv_command - the pad's way in; while TV
mode is off only the player's own controls among those keys do anything.
"""

from __future__ import annotations

import time
from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QWindow
from PyQt6.QtWidgets import QApplication, QWidget

from ..core import tv_input
from ..core.log import log
from ..core.stores import FAV_DEFAULT_GROUP
from ..i18n import tr

_K = Qt.Key
#: The keys TV mode takes, and what each does (digits: digit_<n>).
QT_KEYS: dict[Qt.Key, str] = {
    _K.Key_Up: "up", _K.Key_Down: "down", _K.Key_Left: "left", _K.Key_Right: "right",
    _K.Key_Return: "ok", _K.Key_Enter: "ok", _K.Key_Select: "ok",
    _K.Key_Escape: "back", _K.Key_Back: "back", _K.Key_Backspace: "back",
    _K.Key_Exit: "back",
    _K.Key_Menu: "menu", _K.Key_Option: "menu", _K.Key_Settings: "menu",
    _K.Key_TopMenu: "menu", _K.Key_C: "menu",
    _K.Key_Info: "info", _K.Key_I: "info",
    _K.Key_Guide: "guide", _K.Key_G: "guide",
    _K.Key_Home: "home", _K.Key_HomePage: "home", _K.Key_H: "home",
    _K.Key_Search: "search", _K.Key_Slash: "search",
    _K.Key_MediaTogglePlayPause: "play_pause", _K.Key_Space: "play_pause",
    _K.Key_P: "play_pause", _K.Key_MediaPlay: "play", _K.Key_Play: "play",
    _K.Key_MediaPause: "pause", _K.Key_Pause: "pause",
    _K.Key_MediaStop: "stop", _K.Key_Stop: "stop", _K.Key_X: "stop",
    _K.Key_ChannelUp: "ch_up", _K.Key_PageUp: "ch_up", _K.Key_MediaNext: "ch_up",
    _K.Key_ChannelDown: "ch_down", _K.Key_PageDown: "ch_down",
    _K.Key_MediaPrevious: "ch_down",
    _K.Key_VolumeUp: "vol_up", _K.Key_Plus: "vol_up", _K.Key_Equal: "vol_up",
    _K.Key_VolumeDown: "vol_down", _K.Key_Minus: "vol_down",
    _K.Key_VolumeMute: "mute", _K.Key_M: "mute", _K.Key_F8: "mute",
    _K.Key_Red: "red", _K.Key_F1: "red", _K.Key_Green: "green", _K.Key_F2: "green",
    _K.Key_Yellow: "yellow", _K.Key_F3: "yellow", _K.Key_Blue: "blue",
    _K.Key_F4: "blue",
    _K.Key_Favorites: "fav", _K.Key_AddFavorite: "fav",
    _K.Key_Subtitle: "subtitles", _K.Key_T: "subtitles",
    _K.Key_AudioCycleTrack: "audio", _K.Key_A: "audio",
    _K.Key_AudioForward: "fast_forward", _K.Key_Forward: "fast_forward",
    _K.Key_Period: "fast_forward", _K.Key_AudioRewind: "rewind",
    _K.Key_Comma: "rewind", _K.Key_L: "last",
}
#: An action as a key, for a dialog that opens while TV mode is on.
ACTION_KEYS: dict[str, Qt.Key] = {
    "up": _K.Key_Up, "down": _K.Key_Down, "left": _K.Key_Left,
    "right": _K.Key_Right, "ok": _K.Key_Return, "back": _K.Key_Escape,
    "menu": _K.Key_Menu, "play_pause": _K.Key_Space,
}
_HELD = (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier
         | Qt.KeyboardModifier.MetaModifier)
#: The media keys by the names the system's media controls use (mw_mpris).
_MEDIA_NAMES = {
    _K.Key_MediaTogglePlayPause: "playpause", _K.Key_MediaPlay: "play",
    _K.Key_MediaPause: "pause", _K.Key_MediaStop: "stop",
    _K.Key_MediaNext: "next", _K.Key_MediaPrevious: "previous"}
_SAME_PRESS_S = 0.35
#: How long a typed channel number waits for its next digit.
DIGITS_MS = 1500
#: The info bar's time on screen.
INFO_MS = 6000
_SLEEP_STEPS = (0, 15, 30, 45, 60, 90, 120)


def key_action(key, modifiers) -> str | None:
    """The TV action of a key, or None (one held with Ctrl, Alt or Meta is
    the app's own shortcut)."""
    if modifiers & _HELD:
        return None
    try:
        k = Qt.Key(key)
    except ValueError:
        return None
    if _K.Key_0 <= k <= _K.Key_9:
        return f"digit_{int(k) - int(_K.Key_0)}"
    return QT_KEYS.get(k)


class _TvBridge(QObject):
    """Actions from the readers' threads, onto the window's."""
    action = pyqtSignal(str)


class _TvKeys(QObject):
    """The application's event filter while TV mode is on."""

    def __init__(self, window) -> None:
        super().__init__(window)
        self._w = window

    def eventFilter(self, obj, event) -> bool:  # noqa: N802 - Qt's name
        t = event.type()
        if t not in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            return False
        w = self._w
        if sip.isdeleted(w) or not w._tv_on or not w._tv_takes(obj):
            return False
        return w._tv_key(event, t == QEvent.Type.ShortcutOverride)


class _TvMixin:
    """MainWindow mixin: TV mode on and off, its keys, inputs and picture."""

    _tv_on = False
    _tv_watching = False
    _tv_page = None
    _tv_info = None
    _tv_filter: _TvKeys | None = None
    _tv_hub = None
    _tv_bridge: _TvBridge | None = None
    _tv_saved: tuple | None = None
    _tv_lineup: list | None = None
    _tv_gen = 0
    _tv_finished_gen = -1
    _tv_digits = ""
    _tv_wired = False

    # -- on and off --------------------------------------------------------------------

    def toggle_tv_mode(self) -> None:
        if self._tv_on:
            self.exit_tv_mode()
        else:
            self.enter_tv_mode()

    def _maybe_start_tv_mode(self) -> None:
        """"Start in TV mode": once the window is up - not over the first
        run's welcome."""
        if self.settings.value("tv_mode_start", "false") != "true":
            return
        if self._welcome is not None and self._welcome.isVisible():
            return
        self.enter_tv_mode()

    def enter_tv_mode(self) -> None:
        if self._tv_on:
            return
        from .tv_mode import TvPage
        log.info("tv mode: on")
        self._exit_popout_if_active()
        if self._player_fs:
            # Watching full screen already: what the window was before that.
            self._tv_saved = (getattr(self, "_was_fullscreen", False),
                              getattr(self, "_was_maximized", False),
                              getattr(self, "_pre_fs_geo", None) or self.geometry(),
                              None)
            self._was_fullscreen = True
        else:
            self._tv_saved = (self.isFullScreen(), self.isMaximized(),
                              self.normalGeometry() if self.isMaximized() else self.geometry(),
                              self._center_stack.currentWidget())
        self._tv_wire()
        page = self._tv_page
        if page is None or sip.isdeleted(page):
            page = self._tv_page = TvPage(self)
            self._center_stack.addWidget(page)
        self._tv_on = True
        self._tv_watching = False
        if hasattr(self, "_donate_show"):
            self._donate_show()          # no asking in TV mode (mw_donate)
        self.menuBar().hide()
        self._hide_busy()
        if not self.isFullScreen():
            self.showFullScreen()
        app = QApplication.instance()
        if self._tv_filter is None:
            self._tv_filter = _TvKeys(self)
        app.installEventFilter(self._tv_filter)
        self._tv_inputs_start()
        self._center_stack.setCurrentWidget(page)
        page.show_screen("home")
        if self._tv_has_picture():
            self._tv_picture()
        self._events_tv_mode()
        if getattr(self, "_profile_picker", None) is not None \
                or getattr(self, "_profile_must_pick", False):
            # "Who's watching?" in TV mode's own style (mw_profiles).
            self._profile_close_picker()
            page.show_screen("profiles")

    def exit_tv_mode(self) -> None:
        if not self._tv_on:
            return
        log.info("tv mode: off")
        self._tv_on = False
        app = QApplication.instance()
        if self._tv_filter is not None:
            app.removeEventFilter(self._tv_filter)
        self._tv_inputs_stop()
        self._tv_hide_info()
        self._tv_watching = False
        if self._player_fs:
            # The window's state is put back below, as it was before.
            self._was_fullscreen = True
            self._exit_player_fullscreen()
        fs, maximized, geo, page = self._tv_saved or (False, False, None, None)
        idx = self._center_stack.indexOf(page) if page is not None else -1
        if idx >= 0 and page is not self._tv_page:
            self._center_stack.setCurrentIndex(idx)
        else:
            self._center_stack.setCurrentIndex(0)
        self.menuBar().show()
        if not fs:
            if maximized:
                self.showMaximized()
            else:
                self.showNormal()
                if geo is not None:
                    self.setGeometry(geo)
        QTimer.singleShot(0, self._update_mid_compact)
        self._events_tv_mode()
        if hasattr(self, "_donate_show"):
            self._donate_show()          # the heart back, if its week runs
        if getattr(self, "_profile_must_pick", False):
            # TV mode's "Who's watching?" was up for a profile's PIN: the
            # window's own picker again, never the profile without it.
            self.show_profile_picker()

    def _tv_wire(self) -> None:
        if self._tv_wired:
            return
        self._tv_wired = True
        if self.player is not None:
            self.player.stopped.connect(self._tv_player_stopped)
            self.player.finished.connect(self._tv_player_finished)
        self._tv_digit_timer = QTimer(self)
        self._tv_digit_timer.setSingleShot(True)
        self._tv_digit_timer.timeout.connect(self._tv_digits_done)
        self._tv_info_timer = QTimer(self)
        self._tv_info_timer.setSingleShot(True)
        self._tv_info_timer.timeout.connect(self._tv_hide_info)
        self._tv_info_tick = QTimer(self)
        self._tv_info_tick.setInterval(1000)
        self._tv_info_tick.timeout.connect(self._tv_fill_info)

    # -- the inputs ---------------------------------------------------------------------

    def _tv_inputs_start(self) -> None:
        if self.settings.value("tv_inputs", "true") != "true":
            return
        if self._tv_bridge is None:
            self._tv_bridge = _TvBridge(self)
            self._tv_bridge.action.connect(self._tv_input)
        if self._tv_hub is None:
            self._tv_hub = tv_input.InputHub(self._tv_bridge.action.emit)
        try:
            running = self._tv_hub.start()
            if running:
                log.info("tv mode: reading %s", ", ".join(running))
        except Exception as e:
            log.warning("tv mode: inputs did not start: %s", e)

    def _tv_inputs_stop(self) -> None:
        if self._tv_hub is not None:
            self._tv_hub.stop()

    def _tv_input(self, action: str) -> None:
        """An action from a game controller or LIRC: TV mode's - or, while
        a dialog or menu is open, a key for it."""
        if not self._tv_on or action not in tv_input.ALL:
            return
        other = (QApplication.activeModalWidget() or QApplication.activePopupWidget())
        active = QApplication.activeWindow()
        if other is None and active is not None and active is not self:
            other = active
        if other is not None:
            key = ACTION_KEYS.get(action)
            target = QApplication.focusWidget() or other
            if key is not None and target is not None:
                for t in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
                    QApplication.postEvent(target, QKeyEvent(t, key, Qt.KeyboardModifier.NoModifier))
            return
        self._tv_action(action)

    # -- the phone remote, the remote API, Home Assistant ----------------------------------

    def tv_command(self, what: str, value: Any = None) -> dict[str, Any]:
        """TV mode asked from elsewhere - the phone remote, the remote API,
        Home Assistant: one way in for all of them. *what* "tv": on, off or
        toggle; "tv_key": one of TV mode's actions, as a pad's or LIRC's
        (_tv_input). ValueError for what it cannot take."""
        if what == "tv":
            mode = tv_input.switch_mode(value)
            if mode is None:
                raise ValueError('"mode" is on, off or toggle')
            on = not self._tv_on if mode == "toggle" else mode == "on"
            if on and not self._tv_on:
                self._come_forward()
                self.enter_tv_mode()
            elif not on and self._tv_on:
                self.exit_tv_mode()
            return {"ok": True, "tv_mode": self._tv_on}
        if what != "tv_key":
            raise ValueError(f"unknown action: {what}")
        action = tv_input.remote_action(value)
        if action is None:
            raise ValueError('"action" is one of TV mode\'s keys: up, down, left, right, ok, '
                             'back, menu, red, green, yellow, blue, digit_0 to digit_9, ...')
        if self._tv_on:
            self._tv_input(action)
            return {"ok": True, "tv_mode": True}
        return {"ok": self._tv_off_key(action), "tv_mode": False}

    def _tv_off_key(self, action: str) -> bool:
        """A TV key while TV mode is off: the player's own control where
        there is one (the media keys', channel up and down, the volume);
        the arrows, OK, the colours and the rest are ignored."""
        media = {"play_pause": "playpause", "play": "play", "pause": "pause", "stop": "stop"}
        if action in media:
            self._remote_media(media[action])
            return True
        if action in ("ch_up", "ch_down"):
            return bool(self._play_channel_step(1 if action == "ch_up" else -1))
        p = self.player
        if action not in ("vol_up", "vol_down", "mute") or p is None:
            return False
        if action == "mute":
            p.toggle_mute()
        else:
            p._nudge_volume(5 if action == "vol_up" else -5)
        return True

    def _tv_takes(self, obj) -> bool:
        """Whether a key sent to *obj* is TV mode's: one for this window,
        no dialog or menu open over it."""
        if (QApplication.activeModalWidget() is not None
                or QApplication.activePopupWidget() is not None):
            return False
        if isinstance(obj, QWidget):
            return obj.window() is self
        if isinstance(obj, QWindow):
            return obj is self.windowHandle()
        return False

    def _tv_key(self, event, override: bool) -> bool:
        """A key in TV mode: True when it is taken (all but those with
        Ctrl, Alt or Meta - the app's own one-key shortcuts would undo the
        layout)."""
        if event.modifiers() & _HELD:
            return False
        if override:
            event.accept()
            return True
        search = (self._tv_page.search_screen()
                  if not self._tv_watching and self._tv_page is not None else None)
        if search is not None:
            # A real keyboard types into the search; its letters are not
            # the remote's shortcuts there.
            text = event.text()
            if event.key() == _K.Key_Backspace and search.delete():
                return True
            if len(text) == 1 and (text == " " or (text.isprintable() and not text.isspace())):
                search.type(text)
                return True
        action = key_action(event.key(), event.modifiers())
        if action is None:
            if self._tv_watching:
                self._tv_show_info()
            return True
        if event.isAutoRepeat() and action not in tv_input.REPEATABLE:
            return True
        if not self._tv_media_once(event.key()):
            return True
        self._tv_action(action)
        return True

    def _tv_media_once(self, key) -> bool:
        """A media key acts once: False when the system's media controls
        (mw_mpris) just did the same press - a desktop may send it both
        ways - and the press counted for them too."""
        name = _MEDIA_NAMES.get(key)
        if name is None:
            return True
        pressed = self.__dict__.setdefault("_media_pressed", {})
        k, now = self._media_press_key(name), time.monotonic()
        if now - pressed.get(k, 0.0) < _SAME_PRESS_S:
            return False
        pressed[k] = now
        return True

    # -- actions --------------------------------------------------------------------------

    def _tv_action(self, action: str) -> None:
        if not self._tv_on:
            return
        page = self._tv_page
        if page is not None and getattr(self, "_profile_locked", lambda: False)():
            # Who is watching first: a number, the volume or Play would
            # reach the profile behind the PIN; the page takes only the
            # profiles' tiles.
            page.handle(action)
            return
        if self._tv_watching:
            self._tv_watch_action(action)
            return
        n = tv_input.digit(action)
        if n is not None:
            self._tv_digit(n)
            return
        if action == "ok" and self._tv_digits:
            self._tv_digit_timer.stop()         # OK after a number: at once
            self._tv_digits_done()
            return
        if action in ("vol_up", "vol_down", "mute"):
            self._tv_volume(action)
            return
        if action in ("play", "play_pause") and self._tv_has_picture():
            self._tv_picture()
            return
        if action == "picture":
            # The phone's Now playing: the picture again, when there is one.
            if self._tv_has_picture():
                self._tv_picture()
            return
        if action == "stop" and self._tv_has_picture():
            self.player.stop_btn.click()
            return
        page = self._tv_page
        if page is not None:
            page.handle(action)

    def _tv_watch_action(self, action: str) -> None:
        p = self.player
        if p is None:
            return
        live = (getattr(self, "_last_playback", None) or {}).get("kind") == "live"
        n = tv_input.digit(action)
        if n is not None:
            if live:
                self._channel_digit(str(n), from_player=True)
            return
        if action in ("up", "ch_up", "down", "ch_down"):
            if live:
                self._tv_zap(1 if action in ("up", "ch_up") else -1)
                return
        elif action in ("left", "rewind", "right", "fast_forward"):
            p.arrow_scrub(action in ("left", "rewind"))
        elif action in ("ok", "info"):
            if action == "ok" and self._chan_buffer:
                self._channel_jump()            # OK after a number: at once
                return
            if self._tv_info is not None and self._tv_info.isVisible() and action == "ok":
                self._tv_hide_info()
                return
        elif action == "back":
            # Back is back, one step at a time: a number being typed, the
            # info bar, then the screen the picture came from - the stream
            # goes on behind it (Stop stops it).
            if self._chan_buffer:
                self._chan_timer.stop()
                self._chan_buffer = ""
                self._chan_entry_show("", 0)
            elif self._tv_info is not None and not sip.isdeleted(self._tv_info) \
                    and self._tv_info.isVisible():
                self._tv_hide_info()
            else:
                self._tv_browse()
            return
        elif action == "stop":
            self._tv_stop()
            return
        elif action in ("menu", "blue"):
            self._tv_browse("settings")
            return
        elif action in ("green", "guide"):
            self._tv_browse("guide")
            return
        elif action in ("yellow", "search"):
            self._tv_browse("search")
            return
        elif action == "home":
            self._tv_browse("home")
            return
        elif action in ("red", "fav"):
            it = self._playing_item if live else None
            if it is not None:
                self._tv_toggle_favorite(it)
        elif action == "play_pause":
            p.toggle_pause()
        elif action in ("play", "pause"):
            if bool(getattr(p, "_paused", False)) == (action == "play"):
                p.toggle_pause()
        elif action in ("subtitles", "audio"):
            self._tv_cycle_track(action)
            p.show_number_entry(self._tv_track_label(action), 2500)
        elif action in ("vol_up", "vol_down", "mute"):
            self._tv_volume(action)
        elif action == "last":
            self._last_channel()
            return
        self._tv_show_info()

    # -- the picture ------------------------------------------------------------------------

    def _tv_has_picture(self) -> bool:
        p = self.player
        return p is not None and p.current_url is not None

    def _tv_now_title(self) -> str:
        """What plays, for the Now playing tab: the channel and what is on
        it, or the title."""
        lp = getattr(self, "_last_playback", None) or {}
        p = self.player
        name = str(lp.get("title") or (p.title_lbl.text() if p is not None else "")
                   or "").strip()
        if lp.get("kind") == "live" and lp.get("item"):
            try:
                prog = self.xmltv.current_programme(lp["item"])
            except Exception:
                prog = None
            if prog and prog.get("title"):
                return f"{name} \u00b7 {prog['title']}" if name else str(prog["title"])
        return name

    def _tv_before_play(self) -> None:
        """TV mode, before a stream opens: the player's page shown first.
        Its video surface makes the mpv core when it is first shown - behind
        TV mode's page it never was, play() was refused and the first stream
        from TV mode fell back to an external mpv window behind the app."""
        if not self._tv_on:
            return
        p = self.player
        if p is None or getattr(p.video, "mpv", None) is not None:
            return
        if self._center_stack.currentIndex() != 0:
            self._center_stack.setCurrentIndex(0)
        p.show()
        p.video.show()
        QApplication.processEvents()

    def _tv_started(self, kind: str) -> None:
        """A stream started (_start_playback): in TV mode, the picture."""
        self._tv_gen += 1
        if self._tv_on:
            QTimer.singleShot(0, self._tv_picture)

    def _tv_picture(self) -> None:
        """What plays, full screen."""
        if not self._tv_on or not self._tv_has_picture():
            return
        self._tv_watching = True
        if self._center_stack.currentIndex() != 0:
            self._center_stack.setCurrentIndex(0)
        if not self._player_fs:
            self._enter_docked_fullscreen()
        self.menuBar().hide()
        p = self.player
        p._hide_fs_ui()
        p.video.setFocus(Qt.FocusReason.OtherFocusReason)
        self._tv_show_info()

    def _tv_browse(self, screen: str | None = None) -> None:
        """TV mode's page again - what plays goes on behind it."""
        if not self._tv_on:
            return
        self._tv_watching = False
        self._tv_hide_info()
        if self._player_fs:
            self._was_fullscreen = True
            self._exit_player_fullscreen()
        self.menuBar().hide()
        page = self._tv_page
        if page is None:
            return
        self._center_stack.setCurrentWidget(page)
        if screen:
            page.show_screen(screen)
        else:
            page.show_screen(page.current or "home")
        page.refresh_now()

    def _tv_stop(self) -> None:
        """Stop over the picture (or a film's end): the stream stops, the
        page comes back."""
        p = self.player
        if p is not None and p.current_url is not None:
            # The Stop button's own way: recordings in the player, the wake
            # lock, the scrobbles - all as a click does.
            p.stop_btn.click()
        self._tv_browse()

    def _tv_player_stopped(self) -> None:
        if self._tv_on and self._tv_watching:
            QTimer.singleShot(0, self._tv_after_stop)

    def _tv_after_stop(self) -> None:
        if self._tv_on and self._tv_watching and not self._tv_has_picture():
            self._tv_browse()

    def _tv_player_finished(self) -> None:
        """A film's or an episode's end: the next episode may follow (the
        window's own autoplay); if nothing does, back to the page."""
        last = getattr(self, "_last_playback", None) or {}
        if not self._tv_on or last.get("kind") == "live":
            return
        self._tv_finished_gen = self._tv_gen
        QTimer.singleShot(2500, self._tv_after_finish)

    def _tv_after_finish(self) -> None:
        if self._tv_on and self._tv_gen == self._tv_finished_gen and self._tv_watching:
            self._tv_stop()

    # -- the info bar --------------------------------------------------------------------------

    def _tv_show_info(self) -> None:
        p = self.player
        if p is None or not self._tv_watching:
            return
        if self._tv_info is None or sip.isdeleted(self._tv_info):
            from .tv_widgets import TvInfoBar
            self._tv_info = TvInfoBar(p)
        self._tv_fill_info()
        self._tv_info.place(p.rect())
        self._tv_info.show()
        self._tv_info.raise_()
        self._tv_info_timer.start(INFO_MS)
        self._tv_info_tick.start()

    def _tv_hide_info(self) -> None:
        if getattr(self, "_tv_info_tick", None) is not None:
            self._tv_info_tick.stop()
        if self._tv_info is not None and not sip.isdeleted(self._tv_info):
            self._tv_info.hide()

    def tv_info(self) -> dict:
        """What the info bar says about what plays."""
        last = getattr(self, "_last_playback", None) or {}
        kind = last.get("kind")
        info: dict = {"name": last.get("title") or ""}
        p = self.player
        if kind == "live":
            it = self._playing_item or last.get("item") or {}
            info["name"] = it.get("name") or info["name"]
            info["num"] = it.get("num") or ""
            icon = it.get("stream_icon")
            if icon:
                pm = self.logos.cache.get(icon)
                if pm is None:
                    self.logos.get(icon, self._tv_logo_in)
                info["logo"] = pm
            try:
                progs = self.xmltv.listings_for(it, 2) or []
            except Exception:
                progs = []
            now = time.time()
            if getattr(self, "_playing_catchup", False) and getattr(self, "_ts_program_title", None):
                info["title"] = self._ts_program_title
            elif progs:
                cur = progs[0]
                start = float(cur.get("start_timestamp") or 0)
                stop = float(cur.get("stop_timestamp") or 0)
                info["title"] = cur.get("title") or ""
                info["when"] = (f"{time.strftime('%H:%M', time.localtime(start))}–"
                                f"{time.strftime('%H:%M', time.localtime(stop))}")
                if stop > start:
                    info["progress"] = max(0.0, min(1.0, (now - start) / (stop - start)))
                if len(progs) > 1:
                    nxt = progs[1]
                    info["next"] = tr("tv_next", time=time.strftime(
                        "%H:%M", time.localtime(float(nxt.get("start_timestamp") or 0))),
                        title=nxt.get("title") or "")
            else:
                info["title"] = tr("epg_no_guide_available")
        elif p is not None:
            pos, dur = p.playback_position(), p.playback_duration()
            if dur and dur > 0:
                info["when"] = f"{self._fmt_hms(pos)} / {self._fmt_hms(dur)}"
                info["progress"] = max(0.0, min(1.0, pos / dur))
            if getattr(p, "_paused", False):
                info["title"] = tr("tv_paused")
        return info

    def _tv_fill_info(self) -> None:
        bar = self._tv_info
        if bar is None or sip.isdeleted(bar):
            return
        page = self._tv_page
        legend = page.legend_labels() if page is not None else {}
        bar.set_info(self.tv_info(), legend)
        if self.player is not None:
            bar.place(self.player.rect())

    def _tv_logo_in(self, _pm) -> None:
        if self._tv_watching:
            self._tv_fill_info()

    # -- what the keys do ------------------------------------------------------------------------

    def _tv_play_channel(self, it: dict, lineup: list | None = None) -> None:
        if lineup:
            self._tv_lineup = [c for c in lineup if c and c.get("stream_id") is not None]
        self.play_live_channel(it)

    def _tv_zap(self, step: int) -> None:
        """Channel up/down: the next in the list it was played from, else
        in the whole source."""
        cur = self._playing_item or (getattr(self, "_last_playback", None) or {}).get("item") or {}
        sid = str(cur.get("stream_id"))
        lineup = self._tv_lineup or []
        at = next((i for i, c in enumerate(lineup) if str(c.get("stream_id")) == sid), None)
        if at is not None and len(lineup) > 1:
            self.play_live_channel(lineup[(at + step) % len(lineup)])
            return
        self._play_channel_step(step)

    def _tv_play_movie(self, it: dict) -> None:
        sid = it.get("stream_id")
        ext = it.get("container_extension")
        if sid is not None and self._links_later(
                lambda: self.client.vod_url(sid, ext), lambda: self._tv_play_movie(it)):
            return
        url = self.client.vod_url(sid, ext) if sid is not None else it.get("_url")
        if not url:
            return
        self._current_key = self._item_key(it)
        self._start_playback(url, it.get("name") or it.get("title") or "dopeIPTV",
                             it.get("stream_icon") or it.get("cover"),
                             self._item_key(it), "movie", item=it)

    def _tv_play_continue(self, it: dict) -> None:
        if it.get("_kind") == "episode" and it.get("_series_ctx") is not None:
            self._play_continue_episode(it)
        else:
            self._tv_play_movie(it)

    def _tv_play_episode(self, ep: dict, series: dict, queue: list) -> None:
        """An episode, with the season after it queued for autoplay."""
        eid, ext = ep.get("id"), ep.get("container_extension")
        if eid is None:
            return
        if self._links_later(lambda: self.client.episode_url(eid, ext),
                             lambda: self._tv_play_episode(ep, series, queue)):
            return
        url = self.client.episode_url(eid, ext)
        if not url:
            return
        saved = self.series_ctx
        self.series_ctx = series
        try:
            self._ep_queue_override = list(queue)
            self._ep_index_override = next((i for i, e in enumerate(queue) if e is ep), -1)
            self._start_playback(url, ep.get("name") or ep.get("title") or "dopeIPTV",
                                 ep.get("stream_icon"), self._item_key(ep), "episode",
                                 item=ep)
        finally:
            self.series_ctx = saved

    def _tv_toggle_favorite(self, it: dict) -> None:
        """The red key: a channel in or out of the favorites."""
        sid = it.get("stream_id")
        if sid is None or it.get("_kind") not in (None, "live"):
            return
        if self.favs.is_favorite(sid):
            self.favs.remove(sid)
            text = tr("tv_fav_removed", name=it.get("name") or "")
        else:
            row = {k: it.get(k) for k in ("stream_id", "name", "stream_icon", "num",
                                          "epg_channel_id", "category_id", "tv_archive",
                                          "tv_archive_duration") if it.get(k) is not None}
            self.favs.add(FAV_DEFAULT_GROUP, row)
            text = tr("tv_fav_added", name=it.get("name") or "")
        self._tv_say(text)

    def _tv_say(self, text: str, ms: int = 2000) -> None:
        if self._tv_watching and self.player is not None:
            self.player.show_number_entry(text, ms)
        elif self._tv_page is not None:
            self._tv_page.flash(text, ms)

    def _tv_digit(self, n: int) -> None:
        """A number typed on the page: the channel with it, after a pause."""
        self._tv_digits = (self._tv_digits + str(n))[:5]
        self._tv_say(self._tv_digits, DIGITS_MS + 800)
        self._tv_digit_timer.start(DIGITS_MS)

    def _tv_digits_done(self) -> None:
        buf, self._tv_digits = self._tv_digits, ""
        if buf and self._tv_on:
            self._tv_lineup = None
            self._play_channel_number(int(buf))

    def _tv_volume(self, action: str) -> None:
        p = self.player
        if p is None or p.current_url is None:
            return
        if action == "mute":
            p.toggle_mute()
            self._tv_say(tr("tv_muted") if getattr(p, "_muted", False)
                         else tr("tv_volume", n=p.vol.value()))
            return
        p._nudge_volume(5 if action == "vol_up" else -5)
        self._tv_say(tr("tv_volume", n=p.vol.value()), 1200)

    # -- sound, subtitles, sleep ------------------------------------------------------------------------

    def _tv_tracks(self, kind: str) -> list[dict]:
        p = self.player
        m = p.video.mpv if p is not None else None
        want = "audio" if kind == "audio" else "sub"
        try:
            return [t for t in (m.track_list or []) if t.get("type") == want] if m else []
        except Exception:
            return []

    @staticmethod
    def _tv_track_name(t: dict) -> str:
        from ..core import teletext_subs
        if teletext_subs.track_kind(t):
            # Teletext and DVB subtitles by language, as the player's menu.
            from ..media.teletext_subs import choice_label
            return choice_label(teletext_subs.choices(t)[0])
        label = " ".join(x for x in (t.get("lang") or "", t.get("title") or "") if x).strip()
        return label or tr("player_track_n", n=t.get("id"))

    def _tv_subs_shown(self) -> bool:
        p = self.player
        m = p.video.mpv if p is not None else None
        try:
            return bool(m["sub-visibility"]) if m else False
        except Exception:
            return True

    def _tv_track_label(self, kind: str) -> str:
        tracks = self._tv_tracks(kind)
        sel = next((t for t in tracks if t.get("selected")), None)
        if kind != "audio" and (sel is None or not self._tv_subs_shown()):
            return tr("opt_off")
        if sel is None:
            return tr("opt_no_audio_tracks") if not tracks else self._tv_track_name(tracks[0])
        return self._tv_track_name(sel)

    def _tv_cycle_track(self, kind: str) -> None:
        """The next audio track, or subtitles: off, each track, off."""
        p = self.player
        tracks = self._tv_tracks(kind)
        if p is None or not tracks:
            return
        ids = [t.get("id") for t in tracks]
        sel = next((t.get("id") for t in tracks if t.get("selected")), None)
        if kind == "audio":
            at = ids.index(sel) if sel in ids else -1
            p._pick_track("aid", ids[(at + 1) % len(ids)])
            return
        if sel is None or not self._tv_subs_shown():
            p._pick_track("sid", ids[0])
            return
        at = ids.index(sel)
        p._pick_track("sid", ids[at + 1] if at + 1 < len(ids) else "no")

    def _tv_sleep_minutes(self) -> int:
        p = self.player
        if p is None or not p._sleep_timer.isActive():
            return 0
        return max(1, int(round((p._sleep_deadline - time.monotonic()) / 60)))

    def _tv_set_sleep(self, minutes: int) -> None:
        if self.player is not None:
            self.player._start_sleep_timer(int(minutes))

    # -- Settings > General ------------------------------------------------------------------------

    @staticmethod
    def tv_inputs_text() -> str:
        """The remotes and controllers TV mode finds here, in words."""
        found = tv_input.detect()
        parts = [tr("tv_input_keys")]
        if found.get("lirc"):
            parts.append(tr("tv_input_lirc", path=found["lirc"]))
        pads = found.get("pads") or []
        for name, _path, readable in pads:
            parts.append(tr("tv_input_pad", name=name) if readable
                         else tr("tv_input_pad_locked", name=name))
        if found.get("sdl"):
            parts.append(tr("tv_input_sdl"))
        elif not pads:
            parts.append(tr("tv_input_no_pad"))
        return " · ".join(parts)
