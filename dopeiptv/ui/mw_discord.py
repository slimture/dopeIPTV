"""Discord status in the window: what the player plays, shown in Discord as
"Watching <channel>" (or the film, or the series and its episode) with the
time since it started - or only "Watching TV" - while Settings > General
says so (core/discord_rpc.py talks to Discord, on a thread of its own).

It only watches: each hook returns at once while the switch is off, does no
more on the window's thread than hand over a small dict, and swallows its
own errors. Private viewing shows nothing at all, and what played during it
is not shown afterwards either. Without an application id in the build
(DISCORD_APP_ID) the switch is not offered.
"""

from __future__ import annotations

import time
from typing import Any

from PyQt6.QtCore import QObject

from ..core import discord_rpc, privacy
from ..core.log import log
from ..i18n import tr

#: The settings: the switch, and whether names are shown ("names") or only
#: "Watching TV" ("tv").
KEY_ON = "discord_presence"
KEY_DETAIL = "discord_presence_detail"


def discord_offered() -> bool:
    """Whether this build can show a Discord status at all."""
    return bool(discord_rpc.DISCORD_APP_ID)


class _DiscordMixin:
    _discord: discord_rpc.Presence | None = None
    _discord_now: dict | None = None      # what plays, as the status says it
    _discord_held = False                  # paused

    def _discord_on(self) -> bool:
        return (discord_offered()
                and self.settings.value(KEY_ON, "false") == "true")

    def _discord_names(self) -> bool:
        return self.settings.value(KEY_DETAIL, "names") != "tv"

    def _discord_texts(self) -> dict[str, str]:
        return {"watching": tr("discord_watching", title="{title}"),
                "tv": tr("discord_watching_tv"),
                "paused": tr("discord_paused"),
                "episode": tr("discord_episode", season="{season}",
                              episode="{episode}")}

    def _discord_push(self) -> None:
        """Say what the status should be now; the presence thread does the
        rest. Nothing is started while the switch is off."""
        try:
            if privacy.private():
                self._discord_now = None
            if not self._discord_on():
                if self._discord is not None:
                    self._discord.show(None)
                return
            act = discord_rpc.activity_for(self._discord_now, self._discord_names(),
                                           self._discord_held, self._discord_texts())
            if self._discord is None:
                if act is None:
                    return
                self._discord = discord_rpc.Presence(discord_rpc.DISCORD_APP_ID)
            self._discord.show(act)
        except Exception:
            log.debug("discord: push", exc_info=True)

    def _discord_apply(self) -> None:
        """Settings changed the switch or what it shows."""
        self._discord_push()

    # -- the playback path's hooks ------------------------------------------------

    def _discord_what(self, title: str, item: dict | None, kind: str) -> dict[str, Any]:
        it = item or {}
        out: dict[str, Any] = {"kind": kind, "title": title or it.get("name") or "",
                               "started": time.time()}
        if kind == "live":
            out["channel"] = title or it.get("name") or ""
            try:
                prog = self.xmltv.current_programme(it) if it else None
            except Exception:
                prog = None
            out["programme"] = (prog or {}).get("title") or ""
        elif kind == "episode":
            sctx = getattr(self, "series_ctx", None) or {}
            out["series"] = sctx.get("name") or sctx.get("title") or ""
            out["season"] = it.get("season")
            out["episode"] = it.get("episode_num")
            out["title"] = it.get("title") or title or ""
        return out

    def _discord_started(self, key: Any, title: str, item: dict | None, kind: str,
                         catchup: bool) -> None:
        """_start_playback: the embedded player took a stream."""
        try:
            # Kept while the switch is off too, so turning it on shows what
            # plays; never during private viewing.
            if not discord_offered() or privacy.private():
                self._discord_now = None
                return
            prev = self._discord_now or {}
            ident = (kind, key if key is not None else title)
            if prev.get("_id") == ident:
                return          # a reconnect, a seek in the archive: the same
            what = self._discord_what(title, item, kind)
            what["_id"] = ident
            self._discord_now, self._discord_held = what, False
            self._discord_push()
        except Exception:
            log.debug("discord: started", exc_info=True)

    def _discord_paused(self, paused: bool) -> None:
        if self._discord_now is None or bool(paused) == self._discord_held:
            return
        self._discord_held = bool(paused)
        if not paused:
            # The time shown counts from when it played again.
            self._discord_now["started"] = time.time()
        self._discord_push()

    def _discord_stopped(self) -> None:
        if self._discord_now is None and self._discord is None:
            return
        self._discord_now, self._discord_held = None, False
        self._discord_push()

    def _discord_finished(self) -> None:
        """A live channel's end is a drop the app reconnects: the status
        stays."""
        if (getattr(self, "_last_playback", None) or {}).get("kind") == "live":
            return
        self._discord_stopped()

    def _discord_quit(self) -> None:
        """closeEvent: the connection closes, and Discord clears the status."""
        if self._discord is not None:
            try:
                self._discord.stop()
            except Exception:
                pass

    # -- Settings > General ---------------------------------------------------------

    def _build_discord_card(self, page, dlg) -> None:
        """The Discord card on the General page - only in a build with an
        application id."""
        if discord_offered():
            _DiscordCard(self, page.card(tr("discord_card")), dlg)


class _DiscordCard(QObject):
    """Settings, General: the switch, and what the status says. Lives as
    long as the dialog; applied as it is changed."""

    def __init__(self, win, card, dlg) -> None:
        super().__init__(dlg)
        self.win, self.card = win, card
        self.box = card.toggle(tr("discord_show"), win._discord_on(),
                               tr("discord_show_hint"))
        self.detail = win._combo(
            [("names", tr("discord_detail_names")), ("tv", tr("discord_detail_tv"))],
            "names" if win._discord_names() else "tv")
        card.shown_if(card.row(tr("discord_detail"), self.detail,
                               search="discord status rich presence"),
                      self.box.isChecked)
        card.note(tr("discord_private_note"))
        self.box.toggled.connect(self.switched)
        self.detail.currentIndexChanged.connect(self.detail_changed)

    def switched(self, on: bool) -> None:
        self.win.settings.setValue(KEY_ON, "true" if on else "false")
        self.card.refresh()
        self.win._discord_apply()

    def detail_changed(self, _index: int) -> None:
        self.win.settings.setValue(KEY_DETAIL, self.detail.currentData() or "names")
        self.win._discord_apply()
