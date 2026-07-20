"""Xtream Codes API client and EPG helper functions."""

from __future__ import annotations

import base64
import html
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..core._lazy_requests import requests

from ..core.log import log


class XtreamClient:
    """HTTP client for the Xtream Codes player_api.php endpoint."""

    # How long a fetched channel/movie/series list stays served from memory.
    # Lineups change rarely, and every consumer (mode switch, EPG guide, Home
    # shelves) used to re-download the entire multi-thousand-row list on each
    # visit - the dominant cost of "switching to TV feels slow". The Refresh
    # button clears this (see clear_list_cache), so a manual refresh is
    # always a real re-fetch.
    LIST_CACHE_SECS = 300

    def __init__(self, server: str, username: str, password: str,
                 cache_path: str | None = None) -> None:
        self.server = server.rstrip("/")
        if not self.server.startswith(("http://", "https://")):
            self.server = "http://" + self.server
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "dopeIPTV/1.0"
        self._list_cache: dict[tuple, tuple[float, list]] = {}
        self._list_lock = threading.Lock()
        # Optional on-disk copy of the list cache (per playlist): lets the app
        # start with the last session's channel lineup when the provider is
        # down/overloaded, instead of empty lists and timeouts.
        self._cache_path = cache_path
        self._disk_loaded = cache_path is None
        # Monotonic deadline for the fail-fast cooldown after a network error.
        self._net_down_until = 0.0

    def _load_disk_lists(self) -> None:
        """(under _list_lock) Lazily merge the previous session's lists in as
        stale entries - fresh fetches still win, but the stale-fallback path
        can serve them when the provider doesn't answer."""
        if self._disk_loaded or self._cache_path is None:
            return
        self._disk_loaded = True
        try:
            import json
            with open(self._cache_path, encoding="utf-8") as fh:
                raw = json.load(fh)
            for k, (t, data) in raw.items():
                key = tuple(None if p == "" else p for p in k.split("\x1f"))
                self._list_cache.setdefault(key, (float(t), data))
            log.info("xtream list cache: loaded %d lists from disk", len(raw))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("xtream list cache: could not read %s: %s",
                        self._cache_path, e)

    def _save_disk_lists(self) -> None:
        """(worker thread, after a successful fetch) Persist the cache."""
        if self._cache_path is None:
            return
        try:
            import json
            with self._list_lock:
                raw = {"\x1f".join("" if p is None else str(p) for p in k):
                       [t, data] for k, (t, data) in self._list_cache.items()}
            tmp = f"{self._cache_path}.part"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(raw, fh)
            import os
            os.replace(tmp, self._cache_path)
        except Exception as e:
            log.warning("xtream list cache: could not write %s: %s",
                        self._cache_path, e)

    def _cached_list(self, key: tuple, fetch) -> list:
        """Serve *key* from the short list cache, else *fetch* and store.

        On a network failure a STALE copy (older than the TTL) is served when
        one exists - including the previous session's disk copy - so a down
        or overloaded provider degrades to slightly-old data, not to an error
        and an empty list. Thread-safe - list loads run on the worker pool."""
        now = time.time()
        with self._list_lock:
            self._load_disk_lists()
            hit = self._list_cache.get(key)
            if hit and now - hit[0] < self.LIST_CACHE_SECS:
                return hit[1]
        try:
            data = fetch() or []
        except requests.RequestException:
            with self._list_lock:
                hit = self._list_cache.get(key)
            if hit is not None:
                log.warning(
                    "xtream %s @ %s: network failure - serving the cached "
                    "list from %.0f min ago instead", key[0], self.server,
                    (now - hit[0]) / 60)
                return hit[1]
            raise
        with self._list_lock:
            self._list_cache[key] = (now, data)
        self._save_disk_lists()
        return data

    def clear_list_cache(self) -> None:
        """Drop every cached list so the next call re-fetches (Refresh).
        The disk copy is kept: it only ever serves as a last resort when the
        provider doesn't answer at all, and Refresh overwrites it with fresh
        data as soon as a fetch succeeds."""
        with self._list_lock:
            self._list_cache.clear()
            self._disk_loaded = False   # re-mergeable if the fetch fails

    def _redact(self, text: str) -> str:
        """Strip the username/password from a string before logging it -
        requests embeds the full credentialed URL in its exception messages,
        and these logs are meant to be shared in bug reports."""
        for secret in (self.password, self.username):
            if secret:
                text = text.replace(secret, "***")
        return text

    # Actions whose replies are large (the full stream/series lists, often
    # several MB from a slow provider): these get a long read timeout. Small
    # calls (authenticate, categories, short_epg, ...) keep a short one so a
    # struggling server can't pin "Connecting to ..." at startup for a minute.
    _BIG_ACTIONS = ("get_live_streams", "get_vod_streams", "get_series")

    # After a network-level failure, further calls fail IMMEDIATELY for this
    # long instead of each waiting out its own full timeout. Without this, a
    # degraded link (heavy packet loss) had every consumer (channels, movies,
    # series, EPG) queue a fresh long wait behind the last one and the app
    # felt dead for minutes; with it, failures surface in seconds, the cached
    # lists take over, and one call re-probes the server every cooldown.
    NET_COOLDOWN_SECS = 30

    def _api(self, **params: Any) -> Any:
        # Single choke point for every Xtream call, so log here for
        # troubleshooting: which action, to which server, the HTTP status and
        # timing. Credentials are never logged (only the server host). Turn it
        # on with DOPEIPTV_LOG=debug (successes) - warnings always show.
        url = f"{self.server}/player_api.php"
        base: dict[str, Any] = {"username": self.username, "password": self.password}
        base.update(params)
        action = params.get("action") or "authenticate"
        now = time.monotonic()
        if now < self._net_down_until:
            raise requests.ConnectionError(
                f"provider unreachable - retrying in "
                f"{self._net_down_until - now:.0f} s (cooldown)")
        t0 = time.monotonic()
        read_to = 60 if action in self._BIG_ACTIONS else 20
        try:
            r = self.session.get(url, params=base, timeout=(10, read_to))
        except (requests.Timeout, requests.ConnectionError) as e:
            self._net_down_until = time.monotonic() + self.NET_COOLDOWN_SECS
            log.warning("xtream %s @ %s: request failed - %s: %s "
                        "(failing fast for the next %d s)",
                        action, self.server, type(e).__name__,
                        self._redact(str(e)), self.NET_COOLDOWN_SECS)
            raise
        except requests.RequestException as e:
            log.warning("xtream %s @ %s: request failed - %s: %s",
                        action, self.server, type(e).__name__,
                        self._redact(str(e)))
            raise
        self._net_down_until = 0.0   # the server answered - lift any cooldown
        dt = (time.monotonic() - t0) * 1000
        if r.status_code != 200:
            log.warning("xtream %s @ %s: HTTP %s (%.0f ms)",
                        action, self.server, r.status_code, dt)
        else:
            log.debug("xtream %s @ %s: HTTP 200 (%.0f ms, %d bytes)",
                      action, self.server, dt, len(r.content))
        r.raise_for_status()
        try:
            return r.json()
        except ValueError:
            log.warning(
                "xtream %s @ %s: non-JSON reply (content-type=%s, %d bytes) - "
                "usually a captive portal, wrong URL, or the provider is down",
                action, self.server, r.headers.get("content-type", "?"),
                len(r.content))
            raise

    def authenticate(self) -> dict:
        data = self._api()
        if not isinstance(data, dict) or "user_info" not in data:
            log.warning("xtream auth @ %s: unexpected response (no user_info)",
                        self.server)
            raise RuntimeError("Unexpected response from the server.")
        ui = data["user_info"]
        if str(ui.get("auth", 0)) != "1":
            log.warning("xtream auth @ %s: rejected (auth != 1, status=%s)",
                        self.server, ui.get("status"))
            raise RuntimeError("Wrong username or password.")
        log.info(
            "xtream auth @ %s: ok - status=%s exp=%s connections=%s/%s trial=%s",
            self.server, ui.get("status"), ui.get("exp_date"),
            ui.get("active_cons"), ui.get("max_connections"),
            ui.get("is_trial"))
        return data

    def account_info(self) -> dict:
        """The Xtream account status: user_info (expiry, active/max
        connections, trial, status) plus server_info. Used by the Account
        panel. Returns {} on any error rather than raising."""
        try:
            data = self._api()
        except Exception as e:
            log.warning("xtream account_info @ %s failed: %s: %s",
                        self.server, type(e).__name__,
                        self._redact(str(e)))
            return {}
        if not isinstance(data, dict):
            return {}
        return {"user_info": data.get("user_info") or {},
                "server_info": data.get("server_info") or {}}

    def live_categories(self) -> list[dict]:
        return self._cached_list(
            ("get_live_categories",),
            lambda: self._api(action="get_live_categories"))

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_live_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._cached_list(("get_live_streams", category_id),
                                 lambda: self._api(**p))

    def vod_categories(self) -> list[dict]:
        return self._cached_list(
            ("get_vod_categories",),
            lambda: self._api(action="get_vod_categories"))

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_vod_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._cached_list(("get_vod_streams", category_id),
                                 lambda: self._api(**p))

    def series_categories(self) -> list[dict]:
        return self._cached_list(
            ("get_series_categories",),
            lambda: self._api(action="get_series_categories"))

    def series_list(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_series"}
        if category_id:
            p["category_id"] = category_id
        return self._cached_list(("get_series", category_id),
                                 lambda: self._api(**p))

    def series_info(self, series_id: int | str) -> dict:
        return self._api(action="get_series_info", series_id=series_id) or {}

    def vod_info(self, vod_id: int | str) -> dict:
        return self._api(action="get_vod_info", vod_id=vod_id) or {}

    def short_epg(self, stream_id: int | str, limit: int = 8) -> list[dict]:
        data = self._api(action="get_short_epg", stream_id=stream_id, limit=limit)
        return (data or {}).get("epg_listings", [])

    def epg_table(self, stream_id: int | str) -> list[dict]:
        """Full EPG table - fallback when get_short_epg returns nothing."""
        data = self._api(action="get_simple_data_table", stream_id=stream_id)
        return (data or {}).get("epg_listings", [])

    def xmltv(self) -> bytes:
        """The provider's full XMLTV guide."""
        t0 = time.monotonic()
        try:
            r = self.session.get(f"{self.server}/xmltv.php",
                                 params={"username": self.username,
                                         "password": self.password},
                                 timeout=(20, 180))
        except requests.RequestException as e:
            log.warning("xtream xmltv @ %s: request failed - %s: %s",
                        self.server, type(e).__name__,
                        self._redact(str(e)))
            raise
        if r.status_code != 200:
            log.warning("xtream xmltv @ %s: HTTP %s", self.server, r.status_code)
        r.raise_for_status()
        log.debug("xtream xmltv @ %s: %d bytes (%.0f ms)",
                  self.server, len(r.content), (time.monotonic() - t0) * 1000)
        return r.content

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        ext = "m3u8" if fmt == "m3u8" else "ts"
        return f"{self.server}/live/{self.username}/{self.password}/{stream_id}.{ext}"

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        ext = ext or "mp4"
        return f"{self.server}/movie/{self.username}/{self.password}/{stream_id}.{ext}"

    def episode_url(self, episode_id: int | str, ext: str | None = None) -> str:
        ext = ext or "mp4"
        return f"{self.server}/series/{self.username}/{self.password}/{episode_id}.{ext}"

    def timeshift_url(self, stream_id: int | str, start_dt: datetime,
                      duration_min: int) -> str:
        return self.timeshift_urls(stream_id, start_dt, duration_min)[0]

    def timeshift_urls(self, stream_id: int | str, start_dt: datetime,
                       duration_min: int) -> list[str]:
        """Candidate catch-up URLs in preference order. Panels differ in how
        they expose archive - the standard /timeshift .ts path, an .m3u8 (HLS)
        variant, and the older timeshift.php form - so the player tries each in
        turn and keeps the first that actually plays, instead of the caller
        having to know which scheme a given provider uses."""
        dur = int(duration_min)
        start = int(start_dt.timestamp())
        now = int(time.time())
        # The /timeshift/ path takes a formatted stamp; panels disagree on
        # whether it's local or UTC, so try both (a wrong-timezone stamp points
        # outside the archive and comes back as "unrecognized file format").
        stamp = start_dt.strftime("%Y-%m-%d:%H-%M")
        stamp_utc = datetime.utcfromtimestamp(start).strftime("%Y-%m-%d:%H-%M")
        base = f"{self.server}/timeshift/{self.username}/{self.password}"
        live = f"{self.server}/{self.username}/{self.password}/{stream_id}"

        def php(s):
            return (f"{self.server}/streaming/timeshift.php?"
                    f"username={self.username}&password={self.password}"
                    f"&stream={stream_id}&start={s}&duration={dur}")

        # Archive-specific endpoints only. The utc/lutc-on-the-live-URL forms
        # were dropped: many panels ignore those params and just serve live,
        # which is worse than a clean "not available" - and the background probe
        # can't tell that apart, so we don't offer them for Xtream.
        _ = (start, now, live)  # (kept for clarity re: what we deliberately omit)
        out = [f"{base}/{dur}/{stamp}/{stream_id}.ts",
               f"{base}/{dur}/{stamp}/{stream_id}.m3u8"]
        if stamp_utc != stamp:
            out += [f"{base}/{dur}/{stamp_utc}/{stream_id}.ts",
                    f"{base}/{dur}/{stamp_utc}/{stream_id}.m3u8"]
        out += [php(stamp)]
        if stamp_utc != stamp:
            out += [php(stamp_utc)]
        return out


class OfflineClient:
    """A do-nothing stand-in for :class:`XtreamClient`, used when the app is
    opened without a provider (first-run "explore" mode).

    It exposes the exact same interface but every data call returns empty, so
    the whole UI works with no server configured - lists are simply empty and
    nothing hits the network. It is a Null Object: the window never has to
    special-case a missing client, and swapping in a real client later
    (``MainWindow.switch_playlist``) needs no teardown.
    """

    def __init__(self) -> None:
        self.server = ""
        self.username = ""
        self.password = ""
        self.session = requests.Session()

    def authenticate(self) -> dict:
        return {}

    def account_info(self) -> dict:
        # M3U / demo / offline providers have no account to report.
        return {}

    def clear_list_cache(self) -> None:
        # Interface parity with XtreamClient's short list cache; these
        # clients serve from local data, so there is nothing to drop.
        return None

    def live_categories(self) -> list[dict]:
        return []

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        return []

    def vod_categories(self) -> list[dict]:
        return []

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        return []

    def series_categories(self) -> list[dict]:
        return []

    def series_list(self, category_id: str | None = None) -> list[dict]:
        return []

    def series_info(self, series_id: int | str) -> dict:
        return {}

    def vod_info(self, vod_id: int | str) -> dict:
        return {}

    def short_epg(self, stream_id: int | str, limit: int = 8) -> list[dict]:
        return []

    def epg_table(self, stream_id: int | str) -> list[dict]:
        return []

    def xmltv(self) -> bytes:
        return b""

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        return ""

    def vod_url(self, stream_id: int | str, ext: str | None = None) -> str:
        return ""

    def episode_url(self, episode_id: int | str, ext: str | None = None) -> str:
        return ""

    def timeshift_url(self, stream_id: int | str, start_dt: datetime,
                      duration_min: int) -> str:
        return ""

    def timeshift_urls(self, stream_id: int | str, start_dt: datetime,
                       duration_min: int) -> list[str]:
        return []


class M3UClient(OfflineClient):
    """Backed by a plain M3U/M3U8 playlist (a URL or local file) instead of an
    Xtream API. Exposes the same interface as XtreamClient but Live-only -
    M3U lists have no movies/series/EPG API, so those stay empty (the guide
    can still come from the playlist's separate EPG URL). Channels are grouped
    by their ``group-title`` into categories."""

    _EXTINF = re.compile(r'#EXTINF:-?\d*\s*(?P<attrs>[^,]*),(?P<name>.*)')
    _ATTR = re.compile(r'([\w-]+)="([^"]*)"')

    def __init__(self, url: str) -> None:
        super().__init__()
        self.server = (url or "").strip()
        self._channels: list[dict] = []
        self._by_stream_id: dict[int, dict] = {}
        self._loaded = False
        # EPG URL advertised by the playlist's #EXTM3U header
        # (url-tvg / x-tvg-url), auto-detected while parsing.
        self.epg_url = ""

    # -- fetch + parse --------------------------------------------------------

    def _fetch(self) -> str:
        url = self.server
        if url.startswith(("http://", "https://")):
            r = self.session.get(url, timeout=(20, 180))
            r.raise_for_status()
            return r.text
        if url.startswith("file://"):
            url = url[7:]
        with open(url, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def _parse(self, text: str) -> None:
        channels: list[dict] = []
        pending: dict | None = None
        sid = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                m = self._EXTINF.match(line)
                if not m:
                    pending = None
                    continue
                attrs = dict(self._ATTR.findall(m.group("attrs")))
                name = (m.group("name").strip()
                        or attrs.get("tvg-name", "")).strip() or "?"
                # Catch-up / archive metadata (IPTV M3U convention): a channel
                # advertises archive via catchup / catchup-source / catchup-days
                # (or the older timeshift / tvg-rec). Keep the raw type+template
                # so timeshift_urls can build the archive URL this provider
                # actually uses, and expose tv_archive(+duration) so the generic
                # timeshift detection lights up just like on Xtream.
                catchup = (attrs.get("catchup")
                           or attrs.get("catchup-type", "")).strip()
                catchup_src = attrs.get("catchup-source", "").strip()
                days = (attrs.get("catchup-days") or attrs.get("timeshift")
                        or attrs.get("tvg-rec") or "").strip()
                has_archive = bool(catchup or catchup_src or days)
                pending = {
                    "name": name,
                    "stream_icon": attrs.get("tvg-logo", ""),
                    "epg_channel_id": attrs.get("tvg-id", ""),
                    "category_name": attrs.get("group-title", "").strip()
                    or "Uncategorized",
                    "catchup": catchup,
                    "catchup_source": catchup_src,
                    "tv_archive": 1 if has_archive else 0,
                    "tv_archive_duration": int(days) if days.isdigit() else (
                        7 if has_archive else 0),
                }
            elif line.startswith("#EXTM3U"):
                # Header line: many playlists advertise their XMLTV guide here
                # as url-tvg="..." (or x-tvg-url="..."). Grab it as an EPG hint.
                hdr = dict(self._ATTR.findall(line))
                url = (hdr.get("url-tvg") or hdr.get("x-tvg-url") or "").strip()
                if url:
                    # A playlist may list several comma-separated guides; the
                    # first reachable one is the sensible default.
                    self.epg_url = url.split(",")[0].strip()
            elif line.startswith("#"):
                continue                       # other directives: ignore
            elif pending is not None:
                sid += 1
                pending["stream_id"] = sid
                pending["num"] = sid
                pending["category_id"] = pending["category_name"]
                pending["_url"] = line
                channels.append(pending)
                pending = None
        self._channels = channels
        # Index by stream_id for O(1) lookup - _channel() (and thus stream_url,
        # hit on every zap) would otherwise linear-scan the whole list. Rebuilt
        # here, the only place _channels is (re)assigned, so it can't go stale.
        self._by_stream_id = {c["stream_id"]: c for c in channels}

    def authenticate(self) -> dict:
        # Fetching + parsing the list is this provider's "auth": it proves the
        # URL is reachable and yields at least one channel.
        self._parse(self._fetch())
        self._loaded = True
        if not self._channels:
            raise RuntimeError("No channels found in the M3U playlist.")
        return {"user_info": {"auth": 1}}

    def _ensure(self) -> None:
        if not self._loaded:
            try:
                self.authenticate()
            except Exception:
                self._loaded = True            # don't retry-storm on failure

    # -- interface ------------------------------------------------------------

    def live_categories(self) -> list[dict]:
        self._ensure()
        seen: list[str] = []
        for c in self._channels:
            if c["category_id"] not in seen:
                seen.append(c["category_id"])
        return [{"category_id": g, "category_name": g} for g in seen]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        self._ensure()
        return [c for c in self._channels
                if category_id is None or c["category_id"] == category_id]

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        self._ensure()
        return (self._channel(stream_id) or {}).get("_url", "")

    def _channel(self, stream_id: int | str) -> dict | None:
        try:
            sid = int(stream_id)
        except (TypeError, ValueError):
            return None
        return self._by_stream_id.get(sid)

    def timeshift_urls(self, stream_id: int | str, start_dt: datetime,
                       duration_min: int) -> list[str]:
        """Build catch-up URLs for an M3U channel from its catchup / catchup-
        source tags (the IPTV convention). Handles the common schemes -
        explicit template, ``append``, ``shift`` (utc/lutc) and ``flussonic`` -
        and always adds the utc/lutc and flussonic forms as fallbacks so a
        provider that doesn't spell out a source still gets a fair try."""
        self._ensure()
        ch = self._channel(stream_id)
        if ch is None:
            return []
        base = ch.get("_url", "")
        if not base:
            return []
        start = int(start_dt.timestamp())
        dur = int(duration_min) * 60
        end = start + dur
        now = int(time.time())
        offset = max(0, now - start)
        src = ch.get("catchup_source") or ""

        def subst(t: str) -> str:
            rep = {
                "${start}": str(start), "${end}": str(end),
                "${timestamp}": str(start), "${utc}": str(start),
                "${lutc}": str(now), "${now}": str(now),
                "${duration}": str(dur), "${offset}": str(offset),
                "{utc}": str(start), "{utcend}": str(end),
                "{start}": str(start), "{end}": str(end),
                "{duration}": str(dur), "{offset}": str(offset),
                "{lutc}": str(now),
                "{Y}": start_dt.strftime("%Y"), "{m}": start_dt.strftime("%m"),
                "{d}": start_dt.strftime("%d"), "{H}": start_dt.strftime("%H"),
                "{M}": start_dt.strftime("%M"), "{S}": start_dt.strftime("%S"),
            }
            for k, v in rep.items():
                t = t.replace(k, v)
            return t

        cands: list[str] = []
        if src:
            s = subst(src)
            if s.startswith(("?", "&")):
                cands.append(base.split("?")[0] + s)
            elif "://" in s:
                cands.append(s)
            else:
                cands.append(base.rstrip("/") + "/" + s.lstrip("/"))
        # shift / utc query form (also a generic fallback).
        sep = "&" if "?" in base else "?"
        cands.append(f"{base}{sep}utc={start}&lutc={now}")
        # flussonic path forms.
        m = re.match(r"(?P<pre>.*/)[^/]+\.(?P<ext>m3u8|ts)(?P<qs>\?.*)?$", base)
        if m:
            pre, ext, qs = m.group("pre"), m.group("ext"), m.group("qs") or ""
            cands.append(f"{pre}timeshift_abs-{start}.{ext}{qs}")
            cands.append(f"{pre}index-{start}-{dur}.{ext}{qs}")
        seen: set[str] = set()
        out: list[str] = []
        for u in cands:
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out


def make_client(playlist: dict):
    """Build the right provider client for a stored playlist: an M3U-backed
    client when kind == 'm3u', otherwise the Xtream API client (with a
    per-playlist on-disk list cache so a down provider still shows the last
    known lineup)."""
    if (playlist or {}).get("kind") == "m3u":
        return M3UClient(playlist.get("server", ""))
    cache = None
    pid = (playlist or {}).get("id")
    if pid:
        from .epg import _epg_cache_dir
        try:
            d = _epg_cache_dir()
            d.mkdir(parents=True, exist_ok=True)
            cache = str(d / f"lists_{pid}.json")
        except Exception:
            cache = None
    return XtreamClient(playlist["server"], playlist["username"],
                        playlist["password"], cache_path=cache)


class DemoClient(OfflineClient):
    """A no-account 'try it out' provider: one Live category of official,
    free, public HLS test streams so the app can be exercised end to end
    without any real credentials. Everything else (movies, series, EPG) is
    empty, inherited from OfflineClient. These are well-known test assets
    (Mux, Apple, Bitmovin) - not a third-party IPTV service."""

    # (name, HLS url). Kept deliberately small and to stable public streams.
    STREAMS: list[tuple[str, str]] = [
        ("Big Buck Bunny",
         "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"),
        ("Apple BipBop",
         "https://devstreaming-cdn.apple.com/videos/streaming/examples/"
         "bipbop_4x3/bipbop_4x3_variant.m3u8"),
        ("Sintel",
         "https://bitdash-a.akamaihd.net/content/sintel/hls/playlist.m3u8"),
        ("Art of Motion",
         "https://bitdash-a.akamaihd.net/content/MI201109210084_1/m3u8s/"
         "f08e80da-bf1d-4e3d-8899-f0f6155f6efa.m3u8"),
        ("Tears of Steel",
         "https://test-streams.mux.dev/tos_ismc/main.m3u8"),
    ]
    CATEGORY_ID = "demo"

    def __init__(self) -> None:
        super().__init__()
        # stream_id -> url, with 1-based ids matching the list order.
        self._urls = {i + 1: url for i, (_n, url) in enumerate(self.STREAMS)}

    def live_categories(self) -> list[dict]:
        return [{"category_id": self.CATEGORY_ID,
                 "category_name": "Demo channels"}]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        return [{"stream_id": i + 1, "num": i + 1, "name": name,
                 "stream_icon": "", "category_id": self.CATEGORY_ID,
                 "epg_channel_id": ""}
                for i, (name, _url) in enumerate(self.STREAMS)]

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        try:
            return self._urls.get(int(stream_id), "")
        except (TypeError, ValueError):
            return ""


def parse_xtream_url(text: str) -> tuple[str, str, str] | None:
    """Pull ``(server, username, password)`` out of a pasted Xtream link.

    Many providers hand out a single ready-made URL rather than three separate
    fields, e.g. ``http://host:port/get.php?username=U&password=P&type=m3u_plus``
    or the API form ``http://host:port/player_api.php?username=U&password=P``.
    Some instead give a direct stream path ``http://host:port/live/U/P/1.ts``
    (or ``/U/P/1.ts``). This teases the three parts out of any of those so the
    onboarding/playlist form can be filled from a single paste, while manual
    server/username/password entry keeps working untouched.

    ``server`` is normalised to ``scheme://host[:port]`` (no trailing path or
    query). Returns ``None`` when *text* isn't a URL carrying credentials, so
    the caller can leave a plain hostname or a half-typed value alone.
    """
    from urllib.parse import parse_qs, urlparse

    s = (text or "").strip()
    if not s or "://" not in s:
        return None
    try:
        u = urlparse(s)
    except ValueError:
        return None
    if not u.scheme or not u.netloc:
        return None
    server = f"{u.scheme}://{u.netloc}"

    # Preferred form: credentials in the query string (get.php / player_api.php
    # and most panel exports). parse_qs lower-cases nothing, so accept the exact
    # Xtream keys.
    q = parse_qs(u.query)
    user = (q.get("username") or [""])[0].strip()
    pw = (q.get("password") or [""])[0].strip()
    if user and pw:
        return server, user, pw

    # Fallback: path-based stream URLs embed the credentials as the first two
    # path segments — /live/USER/PASS/ID.ext, /movie/..., /series/..., or the
    # bare /USER/PASS/ID form. Skip a leading media-type segment when present.
    parts = [p for p in u.path.split("/") if p]
    if parts and parts[0] in ("live", "movie", "series", "timeshift"):
        parts = parts[1:]
    if len(parts) >= 2 and parts[0] and parts[1]:
        return server, parts[0], parts[1]
    return None


def detect_provider_link(text: str) -> tuple[str, str, str, str] | None:
    """Classify a pasted provider link for the onboarding/playlist form.

    Returns ``(kind, server, username, password)`` where *kind* is:

    * ``"xtream"`` — the link carried credentials (``get.php`` / ``player_api``
      / a direct stream URL). Xtream is always preferred over M3U because the
      API also serves movies, series and EPG, so even a ``get.php`` *M3U*
      export URL configures an Xtream provider (same host/user/pass drive
      ``player_api.php``). ``username``/``password`` are filled.
    * ``"m3u"`` — a plain playlist URL with no credentials (path ends in
      ``.m3u``/``.m3u8`` or a ``type=m3u`` query). ``username``/``password``
      are empty.

    Returns ``None`` when *text* isn't a recognisable link (a bare hostname or
    half-typed value), so manually typed server/username/password entry is
    never disturbed.
    """
    s = (text or "").strip()
    if "://" not in s:
        return None
    got = _detect_provider_link_once(s)
    if got is not None and _sane_link_netloc(got[1]):
        return got
    # Pasting a link into a server field that already held text concatenates
    # the old content and the link ("http://old-hosthttp://real-host/get.php?
    # ...", or "my creds: http://real-host/..."), which either "parses" into a
    # garbage host or not at all. The pasted link is the LAST url-shaped run
    # in the text - retry from there (unless that IS the whole text, already
    # tried above), so the paste still auto-fills and the clean server
    # replaces the garbage.
    starts = [m.start() for m in re.finditer(r"https?://", s, re.IGNORECASE)]
    if starts and starts[-1] > 0:
        got = _detect_provider_link_once(s[starts[-1]:])
        if got is not None and _sane_link_netloc(got[1]):
            return got
    return None


def _detect_provider_link_once(s: str) -> tuple[str, str, str, str] | None:
    """One classification pass over exactly the given text (no substring
    rescue) - see detect_provider_link for the semantics."""
    parsed = parse_xtream_url(s)
    if parsed:
        return ("xtream", *parsed)
    from urllib.parse import urlparse

    try:
        u = urlparse(s)
    except ValueError:
        return None
    if u.scheme and u.netloc and (
            u.path.lower().endswith((".m3u", ".m3u8"))
            or "type=m3u" in u.query.lower()):
        return "m3u", s, "", ""
    return None


def _sane_link_netloc(url: str) -> bool:
    """False for the host artifact of a paste-into-prefilled-field concat: a
    netloc with a second scheme inside ("old-server:1234http:") is never a
    real host, so the caller should retry from the last url start instead."""
    from urllib.parse import urlparse

    try:
        n = urlparse(url).netloc.lower()
    except ValueError:
        return False
    return bool(n) and "http:" not in n and "https:" not in n


def b64(text: str | None) -> str:
    """Decode Xtream's base64-encoded EPG text fields."""
    if not text:
        return ""
    try:
        return html.unescape(base64.b64decode(text).decode("utf-8", "replace")).strip()
    except Exception:
        return str(text)


def epg_times(entry: dict) -> tuple[datetime | None, datetime | None]:
    """Returns (start, stop) as local datetimes, or (None, None)."""
    def parse(ts_key: str, str_key: str) -> datetime | None:
        v = entry.get(ts_key)
        if v:
            try:
                return datetime.fromtimestamp(int(v), tz=timezone.utc).astimezone()
            except Exception:
                pass
        v = entry.get(str_key)
        if v:
            for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(v, f).astimezone()
                except Exception:
                    continue
        return None
    return parse("start_timestamp", "start"), parse("stop_timestamp", "end")
