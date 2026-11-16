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
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from ..core.log import log
from ..core.privacy import for_log

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


@dataclass(frozen=True)
class Shown:
    """What one source's lists leave out of its channels, as the window has
    it for that source: a hidden category, a locked one while it is locked,
    a channel hidden by hand - and the names given to its categories (what a
    kids profile's test reads them by). Made on the window's thread (of()),
    used on a worker."""
    excluded: frozenset[str] = frozenset()
    hidden: frozenset[str] = frozenset()
    names: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, cats: Any, chans: Any, locked: bool = True) -> Shown:
        """From that source's CategoryOverrides and ChannelOverrides
        (core/stores.py); *locked*: its locked categories are left out too."""
        live = cats.data.get("live") if isinstance(getattr(cats, "data", None), dict) else None
        names = {str(cid): str(e["name"]) for cid, e in (live or {}).items()
                 if isinstance(e, dict) and e.get("name")}
        own = chans.data.get("live") if isinstance(getattr(chans, "data", None), dict) else None
        hidden = frozenset(str(k) for k, e in (own or {}).items()
                           if isinstance(e, dict) and e.get("hidden"))
        return cls(frozenset(str(c) for c in cats.excluded_ids("live", include_locked=locked)),
                   hidden, names)

    def keep(self, row: dict) -> bool:
        if str(row.get("category_id")) in self.excluded:
            return False
        key = row.get("stream_id") or row.get("series_id") or row.get("id") or row.get("_key")
        return key is None or str(key) not in self.hidden


def kids_rows(client: Any, rows: list[dict], names: Mapping[str, str] | None = None) -> list[dict]:
    """*rows* of another source as a kids profile may see them: none the
    provider marks for adults, none in a category named for them (by the
    name the user gave it, *names*, else the provider's). A source whose
    categories cannot be read gives none of its categorised rows."""
    from ..core.profiles import adult_category, adult_item
    rows = [r for r in rows if not adult_item(r)]
    try:
        cats = list(client.live_categories() or [])
    except Exception as e:
        log.info("failover: no categories to check for a kids profile: %s", type(e).__name__)
        return [r for r in rows if r.get("category_id") in (None, "")]
    given = names or {}
    adult = {str(c.get("category_id")) for c in cats
             if isinstance(c, dict) and adult_category(
                 given.get(str(c.get("category_id"))) or str(c.get("category_name") or ""))}
    return [r for r in rows if str(r.get("category_id")) not in adult]


def find_elsewhere(playlists: list[dict], active_id: str | None, item: dict,
                   make_client: Callable[[dict], Any] | None = None,
                   kids: bool = False,
                   shown: Mapping[str, Shown] | None = None) -> tuple[dict, dict, str] | None:
    """(source, row, play address) of *item*'s channel in the first other
    source that has it, or None. Runs off the window's thread (it asks
    each source for its channel list). *shown*: per source id, what its
    lists leave out (Shown) - never a choice here either. *kids*: a kids
    profile asks - none of a source's rows for adults (kids_rows)."""
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
        limits = (shown or {}).get(str(pl.get("id")))
        if limits is not None:
            rows = [r for r in rows if limits.keep(r)]
        if kids:
            rows = kids_rows(client, rows, limits.names if limits is not None else None)
        row = same_channel(rows, item)
        if row is None:
            continue
        try:
            url = client.live_url(row["stream_id"], "ts")
        except Exception as e:
            log.info("failover: %s has the channel but no address: %s", name, e)
            continue
        if url:
            log.info("failover: %s found in %s", for_log(item.get("name")), name)
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
