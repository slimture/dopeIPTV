"""Failover: a live channel that does not play, found in another of the
user's sources and played from there (Nerd > Channels and guide).
A service: it asks the providers for their channel lists.

The same channel is the one with the same guide id ("tv4.se"), else the
same name once case, punctuation and quality words are set aside
("TV4 HD" = "tv4 hd" = "TV4 FHD" = "SE: TV4"). Sources are tried in the
order the user keeps them; boxes on the home network and local files are
tried too (a box often has the channel the provider lost).
"""

from __future__ import annotations

import re
from typing import Any, Callable

from ..core.log import log

_QUALITY = re.compile(r"\b(uhd|fhd|hd|sd|hevc|h265|4k|1080p?|720p?|50fps|backup)\b")
# A provider's country prefix: "SE: ", "SE | ", "|UK| " (not "SVT-1").
_PREFIX = re.compile(r"^\s*\|?\s*[a-z]{2,3}\s*[:|]\s*")


def name_key(name: str) -> str:
    """A channel's name as two sources would both write it."""
    n = (name or "").casefold()
    n = _PREFIX.sub("", n)
    n = _QUALITY.sub(" ", n)
    return re.sub(r"[^0-9a-zÀ-￿]", "", n)


def same_channel(rows: list[dict], item: dict) -> dict | None:
    """The row in *rows* that is *item*'s channel, or None."""
    epg = str(item.get("epg_channel_id") or item.get("_epg_own") or "").strip().casefold()
    if epg:
        for r in rows:
            if str(r.get("epg_channel_id") or "").strip().casefold() == epg and r.get("stream_id") is not None:
                return r
    key = name_key(str(item.get("name") or ""))
    if not key:
        return None
    for r in rows:
        if r.get("stream_id") is not None and name_key(str(r.get("name") or "")) == key:
            return r
    return None


def candidates(playlists: list[dict], active_id: str | None) -> list[dict]:
    """The other sources to try, in the user's order."""
    return [p for p in playlists if p.get("id") != active_id and p.get("kind") != "local"]


def find_elsewhere(playlists: list[dict], active_id: str | None, item: dict,
                   make_client: Callable[[dict], Any] | None = None) -> tuple[dict, dict, str] | None:
    """(source, row, play address) of *item*'s channel in the first other
    source that has it, or None. Runs off the window's thread (it asks
    each source for its channel list)."""
    if make_client is None:
        from ..providers.client import make_client as _make
        make_client = _make
    for pl in candidates(playlists, active_id):
        name = pl.get("name") or pl.get("server") or "?"
        try:
            client = make_client(pl)
            rows = list(client.live_streams(None) or [])
        except Exception as e:
            log.info("failover: %s has no channel list now: %s", name, e)
            continue
        row = same_channel(rows, item)
        if row is None:
            continue
        try:
            url = client.live_url(row["stream_id"], "ts")
        except Exception as e:
            log.info("failover: %s has the channel but no address: %s", name, e)
            continue
        if url:
            log.info("failover: %s found in %s", item.get("name"), name)
            return pl, row, url
    return None


def check_sources(playlists: list[dict], active_id: str | None) -> Any:
    """The Nerd page's line: which sources a channel can fail over to."""
    from ..core.nerd_check import OK, WARN, Check
    others = candidates(playlists, active_id)
    names = [str(p.get("name") or p.get("server") or "?") for p in others]
    detail = "Tried in this order:\n" + "\n".join(f"{i}. {n}" for i, n in enumerate(names, 1)) if names else ""
    if not others:
        return Check(WARN, "nerd_ck_failover_none", {}, "")
    return Check(OK, "nerd_ck_failover_ok", {"n": len(others), "names": ", ".join(names)}, detail)
