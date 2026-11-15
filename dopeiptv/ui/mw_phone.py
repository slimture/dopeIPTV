"""The phone remote in the window (core/phone_remote.py serves it,
core/phone_page.py is its page, ui/phone_remote.py its dialog): turned on
and off from "Phone remote…", on again at the next start once it is on,
and each request's work done on the window's own thread - the same
controls as the media keys (mw_mpris) and the remote API (mw_remote).

Only what the lists show can be listed or played from the phone: no channel
from a hidden or locked category, no favourite from a locked folder."""

from __future__ import annotations

import hashlib
import time
from typing import Any

from ..core import channel_numbers
from ..core.log import log
from ..core.phone_remote import DEFAULT_PORT, PhoneServer, new_token, page_url
from ..core.remote_api import lan_addresses
from ..i18n import tr
from .mw_remote import _Bridge

ON_KEY = "phone_remote"
TOKEN_KEY = "phone_remote_token"
PORT_KEY = "phone_remote_port"
#: Ports tried after the one used last: the default and the next few, then
#: any the system gives.
_PORTS = tuple(range(DEFAULT_PORT, DEFAULT_PORT + 10)) + (0,)


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
            "loading": tr("common_loading")}


def page_palettes(light_now: bool) -> tuple[dict[str, str], dict[str, str]]:
    """The app's colours for the page, (dark, light): the theme in use for
    its own kind, the default theme of the other kind beside it, the
    accent in both."""
    from .theme import THEMES, P, on_accent
    own = dict(P)
    accent = {"accent": P.get("accent", ""), "on_accent": P.get("on_accent") or on_accent(P.get("accent", ""))}
    other = {k: v for k, v in THEMES["graphite" if light_now else "light"].items() if isinstance(v, str)}
    dark, light = (other, own) if light_now else (own, other)
    return dict(dark, **accent), dict(light, **accent)


class _PhoneMixin:
    _phone: PhoneServer | None = None
    _phone_error: str = ""
    _phone_bridge: _Bridge | None = None
    _phone_icons: dict | None = None        # stream id -> its logo's address, as last listed

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
        error = ""
        for port in ports:
            srv = PhoneServer(port, token, self._phone_call, self._phone_page)
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
        return [r for r in (client.live_streams(None) or []) if r.get("stream_id") is not None]

    def _phone_call(self, action: str, params: dict) -> Any:
        bridge = self._phone_bridge
        if bridge is None:
            raise TimeoutError(action)
        if action == "channels":
            listed = bridge.call("_channels", {"rows": self._phone_rows(bridge)})
            icons: dict[str, str] = listed.pop("_icons")
            logos = getattr(self, "logos", None)
            for c in listed["channels"]:
                icon = icons.get(str(c["id"]))
                c["logo"] = bool(icon and logos is not None and logos.disk_file(icon))
            self._phone_icons = icons
            return listed
        if action == "play":
            want = str(params.get("id") or "")
            row = next((r for r in self._phone_rows(bridge) if str(r.get("stream_id")) == want), None)
            if row is None:
                raise ValueError("no such channel")
            return bridge.call("_play", {"row": row})
        if action == "logo":
            ident = str(params.get("id") or "")
            icon = (self._phone_icons or {}).get(ident) or bridge.call("_now_icon", {"id": ident})
            logos = getattr(self, "logos", None)
            path = logos.disk_file(icon) if icon and logos is not None else None
            return str(path) if path is not None else None
        return bridge.call(action, params)

    # -- the work (the window's thread) -----------------------------------------------
    def _phone_do(self, action: str, params: dict) -> Any:
        if action == "_client":
            return self.client
        if action == "_page":
            from ..core import phone_page
            from ..i18n import current_language, is_rtl
            from .theme import effective_theme, is_light
            dark, light = page_palettes(is_light(effective_theme(self.settings)))
            return phone_page.render(dark, light, page_words(), current_language(), is_rtl())
        if action == "_channels":
            return self._phone_channels(params.get("rows") or [])
        if action == "_play":
            row = params.get("row") or {}
            if not self._chno_rows([row]):
                raise ValueError("no such channel")
            self._come_forward()
            self.play_live_channel(row)
            return {"ok": True}
        if action == "_now_icon":
            item = (getattr(self, "_last_playback", None) or {}).get("item") or {}
            return item.get("stream_icon") if str(item.get("stream_id")) == params.get("id") else None
        if action == "state":
            return self._phone_state()
        if action == "calendar":
            return self._calendar_text()
        if action in ("toggle", "stop"):
            self._remote_media("playpause" if action == "toggle" else "stop")
            return {"ok": True}
        if action in ("next", "previous"):
            return {"ok": self._play_channel_step(1 if action == "next" else -1)}
        if action == "volume":
            try:
                value = max(0.0, min(100.0, float(params.get("value"))))
            except (TypeError, ValueError):
                raise ValueError('"value" is a number from 0 to 100') from None
            # The player's own slider: raising it while muted unmutes.
            self._remote_media("volume", value / 100.0)
            return {"ok": True, "volume": int(round(value))}
        if action == "mute":
            p = self.player
            if p is None:
                return {"ok": False}
            p.toggle_mute()
            return {"ok": True, "muted": bool(getattr(p, "_muted", False))}
        raise ValueError(f"unknown action: {action}")

    def _phone_channels(self, rows: list[dict]) -> dict[str, Any]:
        shown = self._chno_rows(rows)
        numbers = channel_numbers.active(self._chno_pid())
        icons = {str(r.get("stream_id")): str(r.get("stream_icon") or "") for r in shown if r.get("stream_icon")}
        listed = [{"id": r.get("stream_id"), "name": str(r.get("name") or ""),
                   "number": channel_numbers.shown(r, numbers)} for r in shown]
        here = {str(r.get("stream_id")) for r in shown}
        favs = [f.get("stream_id") for f in self._favorite_channels() if str(f.get("stream_id")) in here]
        return {"channels": listed, "favorites": favs, "_icons": icons}

    def _phone_stamp(self) -> str:
        """Changes when the page should list the channels again: another
        source, a list fetched again, the favourites changed."""
        favs = ",".join(str(f.get("stream_id")) for f in self._favorite_channels())
        raw = f"{self._chno_pid()}|{id(self.client)}|{favs}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]

    def _phone_state(self) -> dict[str, Any]:
        st = self._remote_status()
        p = self.player
        muted = bool(getattr(p, "_muted", False)) if p is not None else False
        volume = st.get("volume", 0)
        if muted:
            volume = int(getattr(p, "_vol_before_mute", volume) or 0)
        now = time.time()
        out: dict[str, Any] = {"state": st["state"], "time": now, "volume": volume, "muted": muted,
                               "source": st.get("source") or "", "stamp": self._phone_stamp()}
        if st["state"] == "stopped":
            return out
        last = getattr(self, "_last_playback", None) or {}
        kind = st.get("kind") or ""
        out.update(kind=kind, title=st.get("title") or "", position=st.get("position") or 0,
                   duration=st.get("duration") or 0)
        item = last.get("item") or {}
        if kind == "live" and item.get("stream_id") is not None:
            icon = str(item.get("stream_icon") or "")
            logos = getattr(self, "logos", None)
            out["channel"] = {"id": item.get("stream_id"), "name": item.get("name") or st.get("title") or "",
                              "number": channel_numbers.shown(item, channel_numbers.active(self._chno_pid())),
                              "logo": bool(icon and logos is not None and logos.disk_file(icon))}
            try:
                listings = self.xmltv.listings_for(item, 3)
            except Exception:
                listings = []
            for prog in listings:
                entry = {"title": prog.get("title") or "", "start": prog.get("start_timestamp"),
                         "stop": prog.get("stop_timestamp")}
                if "now" not in out and (prog.get("start_timestamp") or 0) <= now < (prog.get("stop_timestamp") or 0):
                    out["now"] = entry
                elif (prog.get("start_timestamp") or 0) > now and "next" not in out:
                    out["next"] = entry
        return out
