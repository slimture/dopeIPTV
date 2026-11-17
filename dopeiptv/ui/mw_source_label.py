"""The active source in plain sight, for a household with several: its name
over the categories (its kind in a chip beside it with two sources or more;
a click opens the source menu the avatar at the foot opens), the list's
source in the status line under the list, what plays from another source
said so there ("Playing: X - from Pirat"), and its name in TV mode's header
beside the clock. Every way the source changes - the menu, a profile, sync,
a source added, renamed or removed in Settings - goes through
_update_playlist_btn, which updates all of it."""

from __future__ import annotations

from typing import Any

from PyQt6 import sip
from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QMenu

from ..i18n import tr
from .source_page import KIND_NAMES

#: The places whose list is the active source's: Local files, Radio,
#: Recordings, Watch later and Watched are this computer's or an account's.
SOURCE_PLACES = frozenset({"live", "vod", "series", "fav", "history"})
#: What plays from no source at all.
NO_SOURCE_KINDS = frozenset({"local", "radio", "web", "recording"})


def source_name(pl: dict | None) -> str:
    return str((pl or {}).get("name") or (pl or {}).get("server") or "")


def kind_name(pl: dict | None) -> str:
    kind = str((pl or {}).get("kind") or "xtream")
    return KIND_NAMES.get(kind, kind.capitalize())


class _SourceLabelMixin:
    #: (id, name) of the source what plays came from; None for a file of
    #: one's own, a station, nothing.
    _playing_src: tuple[str, str] | None = None
    #: The list's own line (mode, words) before its source was put to it.
    _list_readout: tuple[str, str] | None = None
    #: The last list line and playing line written, to know the status
    #: line still shows one of them.
    _list_shown: str = ""
    _playing_shown: str = ""
    #: The sidebar as last drawn: the icon rail (no room for a name).
    _chrome_rail = False

    # -- the source -------------------------------------------------------------------

    def _active_source(self) -> dict | None:
        store = getattr(self, "playlist_store", None)
        if not store or not store.active_id:
            return None
        return store.get(store.active_id)

    def _source_total(self) -> int:
        store = getattr(self, "playlist_store", None)
        return len(store.playlists()) if store else 0

    # -- over the categories ----------------------------------------------------------

    def _update_source_label(self) -> None:
        """The source's name and kind over the categories, its name in TV
        mode's header, and the status line's source - all as they are now."""
        lbl = getattr(self, "_source_label", None)
        if lbl is not None and not sip.isdeleted(lbl):
            pl = self._active_source()
            name = source_name(pl)
            kind = kind_name(pl) if pl is not None else ""
            # One source: its name alone, quiet. Two or more: the kind too.
            chip = kind if self._source_total() > 1 else ""
            full = f"{name} · {kind}" if kind else name
            hint = tr("source_label_hint")
            said = tr("source_label_name", name=full)
            lbl.set_source(name, chip, tip=f"{said}\n{hint}", accessible=said, hint=hint)
            # Not shown: the avatar at the sidebar's foot opens the same menu,
            # and two ways to one menu were one too many over the categories.
            lbl.setVisible(False)
        tv = getattr(self, "_tv_page", None)
        if tv is not None and not sip.isdeleted(tv) and hasattr(tv, "refresh_source"):
            tv.refresh_source()
        self._refresh_source_status()

    def _show_source_menu(self) -> None:
        """The avatar's menu - switch source, this source's settings, the
        profiles - under the name, from its leading edge."""
        lbl = self._source_label
        menu = QMenu(self)
        self._fill_playlist_menu(menu)
        hint = menu.sizeHint()
        x = lbl.width() - hint.width() if lbl.isRightToLeft() else 0
        menu.exec(lbl.mapToGlobal(QPoint(x, lbl.height() + 2)))
        menu.deleteLater()

    def _tv_source_name(self) -> str:
        """The source's name for TV mode's header ("" without one)."""
        return source_name(self._active_source())

    # -- what plays -------------------------------------------------------------------

    def _note_playing_source(self, url: str, key: Any, kind: str) -> None:
        """Which source what starts now comes from: failover's other source,
        the same source again for the same address (Play after Stop), else
        the one in use. Never raises: it is on the playback path."""
        try:
            if kind in NO_SOURCE_KINDS:
                self._playing_src = None
                return
            fo = getattr(self, "_failover_source", None)
            if fo is not None and key is not None and key == getattr(self, "_failover_key", None):
                self._playing_src = (str(fo.get("id") or ""), source_name(fo))
                return
            last = getattr(self, "_last_playback", None) or {}
            if self._playing_src is not None and url and last.get("url") == url:
                return
            pl = self._active_source()
            self._playing_src = (str(pl.get("id") or ""), source_name(pl)) if pl else None
        except Exception:
            self._playing_src = None

    def _playing_elsewhere(self) -> str:
        """The name of the other source what plays comes from, or ""."""
        src = self._playing_src
        if src is None or getattr(self, "_playing_key", None) is None:
            return ""
        pl = self._active_source()
        if pl is not None and str(pl.get("id") or "") == src[0]:
            return ""
        store = getattr(self, "playlist_store", None)
        now = store.get(src[0]) if (store and src[0]) else None
        return source_name(now) or src[1]       # renamed since, or removed

    def _failover_playing(self) -> bool:
        """Failover's stream plays (its title names its source already)."""
        fk = getattr(self, "_failover_key", None)
        return (fk is not None and getattr(self, "_playing_key", None) == fk
                and getattr(self, "_failover_named", True))

    def _status_playing(self, title: str) -> str:
        """"Playing: X" - and "from Pirat" when X is not the shown source's
        (failover's title names its source already)."""
        other = self._playing_elsewhere()
        if other and not self._failover_playing():
            text = tr("status_playing_from", title=title, source=other)
        else:
            text = tr("status_playing", title=title)
        self._playing_shown = text
        return text

    # -- the status line --------------------------------------------------------------

    def _source_place(self) -> bool:
        mode = getattr(self, "mode", "")
        if mode == "fav" and getattr(self, "_fav_section", None) in ("radio", "web"):
            return False
        return mode in SOURCE_PLACES

    def _list_status_text(self, text: str) -> str:
        """A source's list line with its source: "Pirat · 120 channels" with
        two sources or more; while something plays from another source,
        which one instead."""
        if not self._source_place():
            return text
        if self._playing_elsewhere():
            title = str((getattr(self, "_last_playback", None) or {}).get("title") or "")
            if title:
                return self._status_playing(title)
        pl = self._active_source()
        if pl is None or self._source_total() < 2:
            return text
        return tr("status_list_source", source=source_name(pl), text=text)

    def _set_list_status(self, text: str) -> None:
        """The status line's resting words for the list shown."""
        self._list_readout = (str(getattr(self, "mode", "")), text)
        count = getattr(self, "_place_count", None)
        if count is not None:
            count(text)                 # under the place's name (mw_places)
        shown = self._list_status_text(text)
        self._list_shown = shown
        self._set_status(shown)

    def _refresh_source_status(self) -> None:
        """The source changed, or its name: a status line that still shows
        the list's words or what plays says it anew."""
        rest = getattr(self, "_rest_status", None)
        if not rest or not rest[0] or rest[1] or rest[2]:
            return
        lp = getattr(self, "_last_playback", None) or {}
        if rest[0] == self._list_shown and self._list_readout is not None \
                and self._list_readout[0] == getattr(self, "mode", ""):
            new = self._list_status_text(self._list_readout[1])
            self._list_shown = new
        elif rest[0] == self._playing_shown and getattr(self, "_playing_key", None) is not None:
            new = self._status_playing(str(lp.get("title") or ""))
        else:
            return
        if new != rest[0]:
            self._set_status(new)

    def _source_status_stopped(self) -> None:
        """Stop: the line said what played; it says the list's again."""
        rest = getattr(self, "_rest_status", None)
        if not rest or not rest[0] or rest[0] != self._playing_shown:
            return
        if self._list_readout is not None and self._list_readout[0] == getattr(self, "mode", ""):
            self._set_list_status(self._list_readout[1])
