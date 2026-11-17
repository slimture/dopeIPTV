"""The phone remote in the window (core/phone_remote.py serves it,
core/phone_page.py is its page, ui/phone_remote.py its dialog): turned on
and off from "Phone remote…", on again at the next start once it is on,
and each request's work done on the window's own thread - the same
controls as the media keys (mw_mpris) and the remote API (mw_remote), TV
mode on and off and its keys through mw_tv's tv_command (the page turns
into a remote while TV mode is on).

Only what the lists show can be listed or played from the phone: no channel
from a hidden or locked category, no favourite from a locked folder. Films,
series and their episodes, Recent, local files, radio and recordings are
mw_phone_browse's. While an episode plays the state says which (its series,
"S2 E5 · Title", whether one comes before and after it, the version of its
Episodes view), and the phone's previous and next are the episode before
and after it in its queue - the media keys' and autoplay's path, where it
got to without the resume question.

The window's thread does as little as it can for a poll: what plays is read
there, while what is on now and next is looked up on the server's thread from
a guide already in memory (never loaded for a poll: that was a download and
parse on the window's thread), the playing channel's number and logo are
kept per channel, and the stamp is worked out again only when something it
depends on changed. The same state again costs the phone nothing (its ETag,
core/phone_remote.py), and the page asks less often while nothing changes."""

from __future__ import annotations

import hashlib
import math
import secrets
import threading
import time
from typing import Any

from ..core import channel_numbers
from ..core.log import log
from ..core.phone_remote import (
    DEFAULT_PORT, LOGO_MAX, LOGO_TIMEOUT, PORT_KEY, PhoneServer, new_token, page_url,
)
from ..core.remote_api import lan_addresses
from ..i18n import tr
from .mw_phone_browse import _PhoneBrowseMixin
from .mw_phone_rec import _PhoneRecMixin
from .mw_remote import _Bridge

ON_KEY = "phone_remote"
TOKEN_KEY = "phone_remote_token"


def _home_icon(size: int, maskable: bool = False) -> bytes:
    """The app's tile, square and edge to edge, as a phone's home screen
    wants it: iOS rounds its corners itself (a transparent corner turns
    black there), and a maskable icon keeps its picture inside the middle
    80 % that every launcher's shape leaves."""
    from PyQt6.QtCore import QBuffer, QRectF
    from PyQt6.QtGui import QImage, QPainter

    from .app_icon import make_app_icon
    tile = make_app_icon(sizes=(size,)).pixmap(size, size).toImage()
    if tile.isNull():
        return b""
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(tile.pixelColor(size // 2, max(1, size // 12)))   # the tile's own colour
    pr = QPainter(img)
    pr.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    inner = size * (0.8 if maskable else 1.0)
    off = (size - inner) / 2
    pr.drawImage(QRectF(off, off, inner, inner), tile)
    pr.end()
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    ok = img.save(buf, "PNG")
    buf.close()
    return bytes(buf.data()) if ok else b""


def _home_screen_assets() -> dict[str, tuple[bytes, str]]:
    """What "Add to Home screen" asks for: the manifest (Android, Chrome)
    and the icons (apple-touch-icon on iOS), so the remote sits on a phone
    as "dopeIPTV Remote" with the app's icon and opens without an address
    bar."""
    from ..core import phone_page
    from .theme import THEMES
    dark = next((t for t in THEMES.values() if not t.get("light")), {})
    out = {"manifest.webmanifest": (phone_page.manifest(str(dark.get("bg") or "")),
                                    "application/manifest+json")}
    for name, size in phone_page.ICONS.items():
        png = _home_icon(size, maskable=name.startswith("icon-maskable"))
        if png:
            out[name] = (png, "image/png")
    return out


#: How long after the phone asked for a channel what stops it is the phone's
#: to hear, and how long its page then says it.
PHONE_ASKED_S = 15.0
PHONE_NOTE_S = 20.0
#: The Episodes view of what plays is asked for again at least this often
#: (a watched mark made on the computer meanwhile).
EPISODES_FRESH_S = 300
#: What the phone asks that may start a stream or a recording: the app's
#: questions while it runs are the phone's (mw_phone_rec).
_ASKING = frozenset({"_play", "_pb_play", "next", "previous", "toggle", "rec", "_rec_next", "rec_go"})
#: Ports tried after the one used last: the default and the next few, then
#: any the system gives.
_PORTS = tuple(range(DEFAULT_PORT, DEFAULT_PORT + 10)) + (0,)
#: Logos fetched for the phone: a few at once, and the addresses that
#: failed this session (never asked again; bounded).
_LOGO_FETCHES = threading.BoundedSemaphore(4)
_LOGO_LOCK = threading.Lock()
_LOGO_FAILED_MAX = 4096


def _now_next(guide: Any, item: dict, now: float) -> dict[str, Any]:
    """What is on *item*'s channel now and next, from a guide already in
    memory - on the server's thread; a guide not loaded yet is not loaded
    for this (it was a download and parse on the window's thread)."""
    out: dict[str, Any] = {}
    try:
        if guide is None or not guide.is_loaded():
            return out
        listings = guide.listings_for(item, 3)
    except Exception:
        return out
    for prog in listings:
        entry = {"title": prog.get("title") or "", "start": prog.get("start_timestamp"),
                 "stop": prog.get("stop_timestamp")}
        if "now" not in out and (prog.get("start_timestamp") or 0) <= now < (prog.get("stop_timestamp") or 0):
            out["now"] = entry
        elif (prog.get("start_timestamp") or 0) > now and "next" not in out:
            out["next"] = entry
    return out


def _logo_address(icon: Any, picons: frozenset[str]) -> str:
    """*icon* when the phone may be shown it: a web address, or a picon
    from Nerd's folder (*picons*) - no other file on this computer; else ""."""
    icon = str(icon or "").strip()
    if icon.lower().startswith(("http://", "https://")):
        return icon
    if not icon:
        return ""
    return icon if icon.removeprefix("file://") in picons else ""


def _listed(shown: list[dict], numbers: dict, picons: frozenset[str]) -> tuple[list[dict], dict[str, str]]:
    """The phone's rows and their logos' addresses (on the server's thread)."""
    icons = {str(r.get("stream_id")): icon for r in shown
             if (icon := _logo_address(r.get("stream_icon"), picons))}
    listed = [{"id": r.get("stream_id"), "name": str(r.get("name") or ""),
               "number": channel_numbers.shown(r, numbers)} for r in shown]
    return listed, icons


def _picon_files() -> frozenset[str]:
    """The picons in Nerd's folder while Nerd is on: the only files on this
    computer a logo for the phone may be."""
    from ..core import nerd, nerd_lists
    n = nerd.current()
    folder = n.get("nerd_picon_dir").strip() if n.on else ""
    return frozenset(nerd_lists.picon_index(folder).values()) if folder else frozenset()


def page_words() -> dict[str, str]:
    """The page's words in the app's language."""
    return {"channels": tr("fav_channels"), "favorites": tr("nav_favorites"),
            "search": tr("phone_page_search"), "nothing": tr("phone_page_nothing"),
            "next": tr("phone_page_next"), "paused": tr("phone_page_paused"),
            "offline": tr("phone_page_offline"), "expired": tr("phone_page_expired"),
            "none_found": tr("phone_page_none"), "no_favorites": tr("phone_page_no_favs"),
            "play_pause": tr("phone_page_play_pause"), "stop": tr("btn_stop"),
            "ch_up": tr("phone_page_ch_up"), "ch_down": tr("phone_page_ch_down"),
            "mute": tr("tooltip_mute_unmute"), "volume": tr("tooltip_volume"),
            "loading": tr("common_loading"), "profile": tr("profile_who_short"),
            "profile_pin": tr("phone_page_profile_pin"), "show_in_list": tr("phone_page_show_in_list"),
            "ep_prev": tr("phone_page_ep_prev"), "ep_next": tr("tooltip_next_episode"),
            "episodes": tr("sp_episodes"), "movies": tr("nav_movies"), "series": tr("nav_series"),
            "recent": tr("label_recent"), "local": tr("nav_local"), "radio": tr("nav_radio"),
            "recordings": tr("nav_recordings"), "back_to": tr("btn_back"), "min": tr("opt_minutes"),
            "watched": tr("history_chip_watched"), "playing": tr("sp_playing"),
            "search_radio": tr("radio_search_placeholder"), "search_here": tr("local_search_placeholder"),
            "nothing_here": tr("tv_nothing_here"), "find": tr("nav_search"),
            "skip_back": tr("tooltip_back_10s"), "skip_fwd": tr("tooltip_forward_30s"),
            "seek": tr("phone_page_seek"), "tv_type": tr("phone_tv_type"),
            "tracks": tr("phone_page_tracks"), "audio": tr("opt_audio_track"),
            "subtitles": tr("opt_subtitles"), "no_audio": tr("opt_no_audio_tracks"),
            "sleep": tr("opt_sleep_timer"), "off": tr("opt_off"), "minutes": tr("opt_minutes"),
            "sleep_left": tr("phone_page_sleep_left"), "close": tr("common_close"),
            "rec": tr("rec_record"), "rec_stop": tr("rec_stop_recording"),
            "rec_this": tr("rec_record_programme"), "remind": tr("reminder_add"),
            "dont_ask": tr("dont_show_again"), "cancel": tr("common_cancel"),
            "rec_anyway": tr("rec_record_anyway"), "rec_cancel": tr("rec_cancel_scheduled"),
            "rec_ask": tr("phone_rec_ask"), "rec_stop_ask": tr("phone_rec_stop_ask"),
            "more": tr("phone_page_more"), "less": tr("phone_page_less"),
            **tv_words()}


def tv_words() -> dict[str, str]:
    """The remote's words while TV mode is on: its keys, and what each
    colour key does there (TV mode's own legend)."""
    from .tv_mode import TvPage
    colours = TvPage.legend_labels()
    return {"tv_mode": tr("tv_mode"), "tv_switch": tr("sc_tv_mode"), "ok": tr("phone_tv_ok"),
            "up": tr("phone_tv_up"), "down": tr("phone_tv_down"), "left": tr("phone_tv_left"),
            "right": tr("phone_tv_right"), "back": tr("btn_back"), "options": tr("tv_options"),
            "vol_up": tr("phone_tv_vol_up"), "vol_down": tr("phone_tv_vol_down"),
            "digits": tr("phone_tv_digits"), "touchpad": tr("phone_tv_touchpad"),
            "touch_hint": tr("phone_tv_touch_hint"), "tv_picture": tr("phone_tv_picture"),
            "tv_now_playing": tr("phone_tv_now_playing"),
            **{f"key_{c}": colours.get(c, "") for c in ("red", "green", "yellow", "blue")}}


def page_palettes(light_now: bool) -> tuple[dict[str, str], dict[str, str]]:
    """The app's colours for the page, (dark, light): the theme in use for
    its own kind, the default theme of the other kind beside it, the
    accent and the remote's colour keys (TV mode's) in both."""
    from .theme import THEMES, P, on_accent
    from .tv_widgets import COLOUR_KEYS
    own = dict(P)
    accent = {"accent": P.get("accent", ""), "on_accent": P.get("on_accent") or on_accent(P.get("accent", ""))}
    accent.update({f"key_{name}": value for name, value in COLOUR_KEYS})
    other = {k: v for k, v in THEMES["graphite" if light_now else "light"].items() if isinstance(v, str)}
    dark, light = (other, own) if light_now else (own, other)
    return dict(dark, **accent), dict(light, **accent)


class _PhoneMixin(_PhoneBrowseMixin, _PhoneRecMixin):
    _phone: PhoneServer | None = None
    _phone_error: str = ""
    _phone_bridge: _Bridge | None = None
    _phone_icons: dict | None = None        # stream id -> its logo's address, as last listed
    #: (what the stamp depends on, the stamp) and (the playing item's key,
    #: what the page is told of it) - worked out again only on a change.
    _phone_stamp_memo: tuple | None = None
    _phone_item_memo: tuple | None = None

    # -- on and off (the window's thread) ---------------------------------------------
    def _phone_apply(self) -> None:
        """At start: on again if it was on when the app closed."""
        if str(self.settings.value(ON_KEY, "false")) == "true" and self._phone is None:
            self.phone_remote_start()

    def phone_remote_running(self) -> bool:
        return self._phone is not None

    def phone_remote_start(self) -> bool:
        """Turn it on (kept on for the next start); False when no port
        would take it (the reason in _phone_error)."""
        if self._phone is not None:
            return True
        token = str(self.settings.value(TOKEN_KEY, "") or "").strip()
        if len(token) < 30:
            token = new_token()
        if self._phone_bridge is None:
            self._phone_bridge = _Bridge(self._phone_do)
        try:
            last = int(self.settings.value(PORT_KEY, DEFAULT_PORT) or DEFAULT_PORT)
        except (TypeError, ValueError):
            last = DEFAULT_PORT
        ports = ([last] if 1024 <= last <= 65535 else []) + [p for p in _PORTS if p != last]
        # Drawn here, on the window's thread; the server only reads them.
        try:
            assets = _home_screen_assets()
        except Exception as e:
            log.debug("phone remote: no home-screen icons (%s)", e)
            assets = {}
        error = ""
        for port in ports:
            srv = PhoneServer(port, token, self._phone_call, self._phone_page,
                              asset=assets.get)
            try:
                srv.start()
            except OSError as e:
                error = f"{type(e).__name__}: {e}"
                continue
            self._phone, self._phone_error = srv, ""
            self.settings.setValue(ON_KEY, "true")
            self.settings.setValue(TOKEN_KEY, token)
            self.settings.setValue(PORT_KEY, srv.port)
            return True
        self._phone_error = error
        log.warning("phone remote: no port to listen on (%s)", error)
        return False

    def phone_remote_stop(self, keep: bool = False) -> None:
        """Turn it off; *keep*: only for now (the app is closing), on again
        at the next start."""
        srv, self._phone = self._phone, None
        if srv is not None:
            srv.stop()
        if not keep:
            self.settings.setValue(ON_KEY, "false")

    def phone_remote_new_token(self) -> None:
        """A fresh address: the old one stops working on every phone."""
        self.settings.setValue(TOKEN_KEY, new_token())
        if self._phone is not None:
            self.phone_remote_stop(keep=True)
            self.phone_remote_start()

    def phone_remote_url(self) -> str:
        """The address the code holds: this computer on the home network,
        or this computer alone when it is on none."""
        srv = self._phone
        if srv is None:
            return ""
        ips = lan_addresses()
        return page_url(ips[0] if ips else "127.0.0.1", srv.port, srv.token)

    def open_phone_remote(self) -> None:
        from .phone_remote import PhoneRemoteDialog
        PhoneRemoteDialog(self).exec()

    # -- a request (the server's thread) ----------------------------------------------
    def _phone_page(self) -> tuple[bytes, str]:
        bridge = self._phone_bridge
        if bridge is None:
            raise TimeoutError("page")
        return bridge.call("_page", {})

    def _phone_rows(self, bridge: _Bridge) -> list[dict]:
        client = bridge.call("_client", {})
        rows = [r for r in (client.live_streams(None) or []) if r.get("stream_id") is not None]
        # A kids profile's categories read here, off the window's thread:
        # the window's goes through the rows (_chno_rows) at once.
        kids = bridge.call("_kids", {})
        if kids is not None:
            kids.ready()
        return rows

    def _phone_call(self, action: str, params: dict) -> Any:
        bridge = self._phone_bridge
        if bridge is None:
            raise TimeoutError(action)
        if action == "state":
            # The window's thread reads what plays; now and next are looked
            # up here, in a guide already in memory.
            state, target = bridge.call("_state", {})
            state["time"] = time.time()
            if target is not None:
                state.update(_now_next(target[0], target[1], state["time"]))
            return state
        if action == "play":
            want = str(params.get("id") or "")
            row = next((r for r in self._phone_rows(bridge) if str(r.get("stream_id")) == want), None)
            if row is None:
                raise ValueError("no such channel")
            return bridge.call("_play", {"row": row, "answers": params.get("answers")})
        if action == "rec_next":
            # The channel's next programme as the poll finds it, here in a
            # guide already in memory - the one the page saw, or nothing.
            _state, target = bridge.call("_state", {})
            nxt = _now_next(target[0], target[1], time.time()).get("next") if target else None
            try:
                same = nxt is not None and int(nxt.get("start") or 0) == int(params.get("start"))
            except (TypeError, ValueError):
                same = False
            if not same:
                return {"ok": False}
            return bridge.call("_rec_next", {"item": target[1], "prog": nxt, "what": params.get("what"),
                                             "answers": params.get("answers")})
        if action == "channels":
            got = bridge.call("_channels", {"rows": self._phone_rows(bridge)})
            listed, icons = _listed(got["rows"], got["numbers"], got["picons"])
            for c in listed:
                c["logo"] = bool(icons.get(str(c["id"])))
            self._phone_icons = icons
            return {"channels": listed, "favorites": got["favorites"]}
        if action == "logo":
            ident = str(params.get("id") or "")
            icon = (self._phone_icons or {}).get(ident) or bridge.call("_now_icon", {"id": ident})
            return self._phone_logo_file(str(icon or ""))
        # Everything else the app plays (mw_phone_browse): films, series and
        # their episodes, Recent, local files, radio, recordings.
        if action == "browse":
            return self._phone_browse(bridge, params)
        if action == "episodes":
            return self._phone_episodes(bridge, params)
        if action == "play_ref":
            return self._phone_play_ref(bridge, params)
        if action == "thumb":
            return self._phone_thumb(params)
        return bridge.call(action, params)

    def _phone_logo_file(self, icon: str) -> str | None:
        """The file to serve for a logo address the window passed (a web
        address, or a picon from Nerd's folder): the image cache's copy -
        fetched into it once, here on the server's thread, when it is not
        there yet. A fetch that failed is not tried again this session."""
        logos = getattr(self, "logos", None)
        if not icon or logos is None:
            return None
        if not icon.lower().startswith(("http://", "https://")):
            return icon.removeprefix("file://")
        path = logos.disk_file(icon)
        if path is not None:
            return str(path)
        with _LOGO_LOCK:
            failed = self.__dict__.setdefault("_phone_logo_failed", set())
            if icon in failed:
                return None
        with _LOGO_FETCHES:
            path = logos.disk_file(icon) or logos.fetch_file(
                icon, timeout=LOGO_TIMEOUT, max_bytes=LOGO_MAX)
        if path is None:
            with _LOGO_LOCK:
                if len(failed) >= _LOGO_FAILED_MAX:
                    failed.clear()
                failed.add(icon)
            return None
        return str(path)

    def _phone_logo_address(self, icon: Any, picons: frozenset[str] | None = None) -> str:
        """*icon* when the phone may be shown it (the window's thread): a
        web address, or a picon from Nerd's folder - no other file on this
        computer; else ""."""
        text = str(icon or "").strip()
        if not text or text.lower().startswith(("http://", "https://")):
            return text
        return _logo_address(text, _picon_files() if picons is None else picons)

    # -- the work (the window's thread) -----------------------------------------------
    def _phone_do(self, action: str, params: dict) -> Any:
        if action in _ASKING and self.__dict__.get("_phone_ask_hook") is None:
            # The app's questions in it are the phone's: nothing it would
            # ask about starts without the person's answer there.
            with self._phone_asking(params.get("answers")) as q:
                result = self._phone_do_now(action, params)
            return self._phone_answered(q, result)
        return self._phone_do_now(action, params)

    def _phone_do_now(self, action: str, params: dict) -> Any:
        if action.startswith("_pb_"):
            return self._pb_do(action, params)
        if action == "_client":
            return self.client
        if action == "_kids":
            return getattr(self, "_kids_keep", lambda _m: None)("live")
        if action == "_page":
            from ..core import phone_page
            from ..i18n import current_language, is_rtl
            from .theme import effective_theme, is_light
            dark, light = page_palettes(is_light(effective_theme(self.settings)))
            from ..media.embedded import EmbeddedPlayer
            from .mw_tv import _SLEEP_STEPS
            return phone_page.render(dark, light, page_words(), current_language(), is_rtl(),
                                     skips=(EmbeddedPlayer.SEEK_BACK, EmbeddedPlayer.SEEK_FWD),
                                     sleep_steps=tuple(m for m in _SLEEP_STEPS if m))
        if action in ("_play", "toggle", "next", "previous", "calendar", "rec", "rec_stop",
                      "rec_go", "_rec_next") \
                and getattr(self, "_profile_locked", lambda: False)():
            # "Who's watching?" waits for the PIN at start: nothing of the
            # profile's is played before it is given (the page says why).
            self._profile_refuse_locked()
        if action == "_channels":
            return self._phone_shown(params.get("rows") or [])
        if action == "_play":
            row = params.get("row") or {}
            if not self._chno_rows([row]):
                raise ValueError("no such channel")
            self._phone_asked()
            self._come_forward()
            self.play_live_channel(row)
            return {"ok": True}
        if action == "_now_icon":
            item = (getattr(self, "_last_playback", None) or {}).get("item") or {}
            if str(item.get("stream_id")) != params.get("id"):
                return None
            return self._phone_logo_address(item.get("stream_icon")) or None
        if action == "state":
            return self._phone_state()
        if action == "_state":
            return self._phone_state_parts()
        if action == "calendar":
            return self._calendar_text()
        if action == "tv":
            return self.tv_command("tv", params.get("mode", "toggle"))
        if action == "tv_key":
            return self.tv_command("tv_key", params.get("action"))
        if action == "rec":
            return self._phone_rec(params)
        if action == "rec_stop":
            return self._phone_rec_stop(params)
        if action == "rec_go":
            return self._phone_rec_go(params)
        if action == "_rec_next":
            return self._phone_rec_next(params)
        if action == "rec_ask":
            from .mw_phone_rec import REC_ASK_KEY
            self.settings.setValue(REC_ASK_KEY, "true" if params.get("ask") is not False else "false")
            return {"ok": True, "rec_ask": self._phone_rec_asks()}
        if action == "tv_text":
            # The phone's own keyboard into TV mode's search (mw_tv).
            return self.tv_command("text", params.get("text"))
        if action in ("toggle", "stop"):
            self._remote_media("playpause" if action == "toggle" else "stop")
            return {"ok": True}
        if action in ("next", "previous"):
            self._phone_asked()
            step = 1 if action == "next" else -1
            if (getattr(self, "_last_playback", None) or {}).get("kind") == "episode":
                # The episode after or before it in its queue, as the media
                # keys and autoplay go - where it got to, without the question.
                return {"ok": self._advance_episode(step, ask=False)}
            return {"ok": self._play_channel_step(step)}
        if action == "seek":
            return self._phone_seek(params)
        if action == "tracks":
            return self._phone_tracks()
        if action == "track":
            return self._phone_pick_track(params)
        if action == "sleep":
            return self._phone_sleep(params)
        if action == "volume":
            try:
                value = max(0.0, min(100.0, float(params.get("value"))))
            except (TypeError, ValueError):
                raise ValueError('"value" is a number from 0 to 100') from None
            # The player's own slider: raising it while muted unmutes.
            self._remote_media("volume", value / 100.0)
            return {"ok": True, "volume": int(round(value))}
        if action == "profile":
            # The profile chip: another profile (one with a PIN only with it).
            return self._profile_switch_remote(params)
        if action == "mute":
            p = self.player
            if p is None:
                return {"ok": False}
            p.toggle_mute()
            return {"ok": True, "muted": bool(getattr(p, "_muted", False))}
        raise ValueError(f"unknown action: {action}")

    def _phone_channels(self, rows: list[dict]) -> dict[str, Any]:
        """The phone's list of *rows*, whole (a request builds it on the
        server's thread from _phone_shown)."""
        got = self._phone_shown(rows)
        listed, icons = _listed(got["rows"], got["numbers"], got["picons"])
        return {"channels": listed, "favorites": got["favorites"], "_icons": icons}

    def _phone_shown(self, rows: list[dict]) -> dict[str, Any]:
        """What the lists show of *rows*, with what the server's thread
        needs to turn them into the phone's (_listed)."""
        if getattr(self, "_profile_locked", lambda: False)():
            # Who is watching is not said yet: no list of the profile's.
            return {"rows": [], "numbers": {}, "picons": frozenset(), "favorites": []}
        shown = self._chno_rows(rows)
        here = {str(r.get("stream_id")) for r in shown}
        favs = [f.get("stream_id") for f in self._favorite_channels() if str(f.get("stream_id")) in here]
        return {"rows": shown, "numbers": channel_numbers.active(self._chno_pid()),
                "picons": _picon_files(), "favorites": favs}

    # -- why a play the phone asked for did not start ------------------------------------

    def _phone_asked(self) -> None:
        """The phone asks for a channel: what stops it in the next moments
        is told to its page (an earlier note goes)."""
        self._phone_asked_at = time.monotonic()
        self._phone_notice = None

    def _phone_note(self, text: str) -> None:
        """What stopped a play (the VPN guard, the connection question) -
        on the phone's page too, when the phone asked for it just now."""
        asked = getattr(self, "_phone_asked_at", None)
        if not text or asked is None or time.monotonic() - asked > PHONE_ASKED_S:
            return
        p = getattr(self, "player", None)
        self._phone_notice = (text, time.monotonic() + PHONE_NOTE_S, getattr(p, "current_url", None))

    def _phone_note_now(self) -> str:
        """The note while it stands: for a while, and only until something
        else plays."""
        n = getattr(self, "_phone_notice", None)
        if n is None:
            return ""
        p = getattr(self, "player", None)
        if time.monotonic() > n[1] or getattr(p, "current_url", None) != n[2]:
            self._phone_notice = None
            return ""
        return n[0]

    def _phone_stamp(self) -> str:
        """Changes when the page should list the channels again: another
        source, a list fetched again, the favourites changed, another
        profile, who is watching said at start, a kids profile's limits
        known (mw_profiles)."""
        from ..core import profiles
        locked = getattr(self, "_profile_locked", lambda: False)()
        pid, active = self._chno_pid(), profiles.active_id()
        kids_ver = self.__dict__.get("_kids_ver", 0)
        favs = getattr(self, "favs", None)
        version = getattr(favs, "version", None)
        unlocked = bool(getattr(getattr(self, "parental", None), "session_unlocked", False))
        # Everything the favourites as listed depend on: the store, its
        # changes, a locked folder opened, the profile and its limits.
        key = (pid, id(self.client), active, locked, kids_ver, id(favs), version, unlocked)
        memo = self._phone_stamp_memo
        if version is not None and memo is not None and memo[0] == key:
            return memo[1]
        ids = ",".join(str(f.get("stream_id")) for f in self._favorite_channels())
        raw = f"{pid}|{id(self.client)}|{active}|{locked}|{kids_ver}|{ids}"
        stamp = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        self._phone_stamp_memo = (key, stamp)
        return stamp

    def _phone_channel(self, item: dict, title: str) -> dict[str, Any]:
        """The playing channel as the page shows and finds it in its list;
        *here*: it plays from the source the lists show (a channel failover
        or the search played from another source has an id of that one's)."""
        from ..core import nerd
        sid = item.get("stream_id")
        number = channel_numbers.active(self._chno_pid()).get(str(sid))
        key = (id(item), sid, item.get("name"), item.get("stream_icon"), title, number,
               id(nerd.current()))
        memo = self._phone_item_memo
        if memo is None or memo[0] != key:
            memo = (key, {"id": sid, "name": item.get("name") or title or "",
                          "number": number if number is not None else channel_numbers.own_number(item),
                          "logo": bool(self._phone_logo_address(item.get("stream_icon")))})
            self._phone_item_memo = memo
        elsewhere = getattr(self, "_playing_elsewhere", None)
        return dict(memo[1], here=not (elsewhere() if callable(elsewhere) else ""))

    def _phone_state(self) -> dict[str, Any]:
        """The page's state, whole (a poll asks _phone_state_parts and looks
        up now and next on the server's thread)."""
        out, target = self._phone_state_parts()
        out["time"] = time.time()
        if target is not None:
            out.update(_now_next(target[0], target[1], out["time"]))
        return out

    def _phone_state_parts(self) -> tuple[dict[str, Any], tuple | None]:
        """What the window's thread reads for a poll: the state but for the
        clock and the guide, and (guide, channel) to look now and next up
        in - or None."""
        st = self._remote_status()
        p = self.player
        muted = bool(getattr(p, "_muted", False)) if p is not None else False
        volume = st.get("volume", 0)
        if muted:
            volume = int(getattr(p, "_vol_before_mute", volume) or 0)
        out: dict[str, Any] = {"state": st["state"], "volume": volume, "muted": muted,
                               "source": st.get("source") or "", "stamp": self._phone_stamp(),
                               "tv_mode": bool(st.get("tv_mode")), "sections": self._phone_sections()}
        if out["tv_mode"]:
            # A text entry on the TV (its search): the phone offers its own
            # keyboard for it. Its text changes only as it is typed.
            entry = getattr(self, "_tv_text_entry", lambda: None)()
            if entry is not None:
                out["tv_entry"] = str(entry.query)
        note = self._phone_note_now()
        locked = getattr(self, "_profile_locked", lambda: False)()
        if locked:
            # Who is watching waits for a PIN at start: said, and nothing of
            # the profile's shown (the lists come empty, _phone_channels).
            out["locked"] = True
            note = tr("phone_page_locked")
        if note:
            out["note"] = note
        if getattr(self, "_profile_many", lambda: False)():
            # The chip at the top: who is watching, and the others to pick.
            from .profiles_ui import profile_fill
            state = self._profile_state()
            fills = {p.id: profile_fill(p) for p in self.profiles.profiles()}
            for row in [state["profile"], *state["profiles"]]:
                if row is not None:
                    row["fill"] = fills.get(row["id"], "")
            out.update(state)
        # Recording: whether the page asks first (one key for every phone),
        # what records now, a question asked later in a request of the phone's.
        out["rec_ask"] = self._phone_rec_asks()
        rec = self._phone_rec_state()
        if rec is not None:
            out["rec"] = rec
        late = self._phone_late_ask()
        if late is not None:
            out["ask"] = late
        if st["state"] == "stopped" or locked:
            return out, None
        last = getattr(self, "_last_playback", None) or {}
        kind = st.get("kind") or ""
        out.update(kind=kind, title=st.get("title") or "", position=st.get("position") or 0,
                   duration=st.get("duration") or 0)
        # A title with a length can be sought in (the media keys' own test: a
        # film, an episode, a file, a recording - never a live channel).
        out["seek"] = bool(out["duration"])
        # Small and steady: the sheet of tracks is read only when it is
        # asked for (this says when to read it again), the sleep timer as
        # the moment it stops what plays.
        out["tracks"] = f"{id(getattr(p, 'current_url', None)) % 99991}.{self.__dict__.get('_track_ver', 0)}"
        left = p.sleep_left() if p is not None and hasattr(p, "sleep_left") else 0.0
        if left > 0:
            out["sleep_at"] = int(round(time.time() - time.monotonic() + p._sleep_deadline))
        item = last.get("item") or {}
        if kind == "live" and item.get("stream_id") is not None:
            out["channel"] = self._phone_channel(item, st.get("title") or "")
            if self._phone_rec_item() is not None:
                out["can_rec"] = True       # what airs now, recorded from what plays
            return out, (getattr(self, "xmltv", None), item)
        if kind == "episode":
            out["episode"] = self._phone_episode(last)
        return out, None

    def _phone_seek(self, params: dict) -> dict[str, Any]:
        """A place in what plays, from the phone - ``to`` seconds from its
        start (the bar let go), or ``by`` seconds from where it is (the skip
        keys): through the media keys' own seek (``set_position``, the
        player's _do_seek, as its seek bar does), clamped to the title's
        length. Nothing for a stream that cannot seek: a live channel, a
        stream with no length."""
        given = "to" if params.get("to") is not None else "by"
        try:
            value = float(params.get(given))
        except (TypeError, ValueError):
            raise ValueError('give "to" (seconds from the start) or "by" (seconds, + or -)') from None
        if not math.isfinite(value):
            raise ValueError('give "to" (seconds from the start) or "by" (seconds, + or -)')
        snap = self.mpris_snapshot()
        length = float(snap.get("length") or 0)
        if snap.get("status") == "Stopped" or not snap.get("can_seek") or length <= 0:
            return {"ok": False}
        target = value if given == "to" else float(snap.get("position") or 0) + value
        target = max(0.0, min(length, target))
        self._remote_media("set_position", target)
        return {"ok": True, "position": round(target, 1)}

    def _phone_tracks(self) -> dict[str, Any]:
        """The sheet of tracks: what plays' audio tracks and subtitles as the
        player's own menu lists them (built into menus that are never shown,
        kept until the next sheet), the one on marked - read only now, never
        in a poll. *stamp* names this sheet: a pick is of its entries."""
        from PyQt6.QtWidgets import QMenu
        p = self.player
        if p is None or not getattr(p, "current_url", None) or not hasattr(p, "fill_sub_tracks"):
            return {"stamp": "", "audio": [], "subs": []}
        audio, subs = QMenu(), QMenu()
        p.fill_audio_tracks(audio)
        p.fill_sub_tracks(subs)
        old = self.__dict__.get("_phone_sheet")
        stamp = secrets.token_hex(4)
        self._phone_sheet = (stamp, audio, subs)
        if old is not None:
            for menu in old[1:]:
                menu.deleteLater()

        def entries(menu) -> list[dict]:
            return [{"label": a.text().split("\t")[0], "on": a.isChecked(), "ok": a.isEnabled()}
                    for a in self._phone_sheet_actions(menu)]
        return {"stamp": stamp, "audio": entries(audio), "subs": entries(subs)}

    @staticmethod
    def _phone_sheet_actions(menu) -> list:
        return [a for a in menu.actions() if not a.isSeparator() and a.text()]

    def _phone_pick_track(self, params: dict) -> dict[str, Any]:
        """One entry of the sheet picked: its action triggered - the
        player's own path, so a title's remembered tracks and a channel's
        teletext page are kept as when it is picked on the computer."""
        sheet = self.__dict__.get("_phone_sheet")
        kind = params.get("kind")
        try:
            index = int(params.get("i"))
        except (TypeError, ValueError):
            raise ValueError('"i" is the entry\'s number') from None
        if sheet is None or params.get("stamp") != sheet[0] or kind not in ("audio", "subs"):
            return {"ok": False, "stale": True}
        actions = self._phone_sheet_actions(sheet[1] if kind == "audio" else sheet[2])
        if not 0 <= index < len(actions) or not actions[index].isEnabled():
            raise ValueError("no such entry")
        actions[index].trigger()
        return {"ok": True}

    def _phone_sleep(self, params: dict) -> dict[str, Any]:
        """The sleep timer TV mode's Options sets (the player's own): one of
        its steps, 0 to cancel it."""
        from .mw_tv import _SLEEP_STEPS
        try:
            minutes = int(params.get("minutes"))
        except (TypeError, ValueError):
            raise ValueError('"minutes" is one of the sleep timer\'s steps') from None
        if minutes not in _SLEEP_STEPS:
            raise ValueError('"minutes" is one of the sleep timer\'s steps')
        p = self.player
        if p is None or (minutes and not getattr(p, "current_url", None)):
            return {"ok": False}
        self._tv_set_sleep(minutes)
        return {"ok": True}

    def _phone_sections(self) -> list[str]:
        """The page's sections: Movies and Series only where the source has
        them (the nav's own probe)."""
        avail = getattr(self, "_mode_avail", None) or {}
        return [s for s in ("all", "fav", "vod", "series", "recent", "local", "radio", "rec")
                if avail.get(s) is not False]

    def _phone_episode(self, last: dict) -> dict[str, Any]:
        """The playing episode as Now playing shows it - its series, "S2 E5
        · Title", whether there is one before and after it - and the version
        of its series' Episodes view (mw_phone_browse); worked out again
        only when something of it changed."""
        from ..core import series_view as sv
        item = last.get("item") or {}
        ctx = last.get("series_ctx") or {}
        key = getattr(self, "_playing_key", None)
        prev, nxt = self._has_previous_episode(), self._has_next_episode()
        bucket = int(time.time() // EPISODES_FRESH_S)
        memo_key = (id(item), str(key), id(ctx), prev, nxt, bucket, last.get("title"))
        memo = self.__dict__.get("_phone_ep_memo")
        if memo is not None and memo[0] == memo_key:
            return memo[1]
        sid = ctx.get("series_id")
        series = str(ctx.get("name") or ctx.get("title") or "")
        try:
            if sid is not None:
                series = self.channel_ov.display_name("series", sid, series)
        except Exception:
            pass
        s, e = sv.season_of(item.get("season")), sv.episode_number(item)
        se = (tr("sp_se", s=s, e=e) if s is not None and e is not None
              else tr("sp_episode_short", n=e) if e is not None else "")
        title = sv.episode_title(item, series)
        label = f"{se} · {title}" if se and title else se or title or str(last.get("title") or "")
        art = next((str(v) for v in (ctx.get("cover"), ctx.get("stream_icon"), item.get("stream_icon"))
                    if str(v or "").lower().startswith(("http://", "https://"))), "")
        img = self._pb()[1].put("img", art, {"cover": art}) if art else ""
        version = hashlib.sha1(f"{sid}|{key}|{bucket}".encode()).hexdigest()[:10]
        info = {"series": series, "sid": "" if sid is None else str(sid), "label": label,
                "key": "" if key is None else str(key), "prev": prev, "next": nxt,
                "v": version, "img": img}
        self.__dict__["_phone_ep_memo"] = (memo_key, info)
        return info
