"""Xtream Codes API client and EPG helper functions."""

from __future__ import annotations

import base64
import os
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

from ..core._lazy_requests import requests

from ..core import dest_guard, m3u_ids, mirrors, stream_address, tagless
from ..core.log import log, register_secrets
from ..i18n import LocalizedError, tr


class PlaylistTooLarge(LocalizedError, ValueError):
    """A playlist past what is read of one (core/_lazy_requests.LIST_MAX):
    said in the user's language, a ValueError as it always was."""


class NoList(LocalizedError):
    """A list action answered with something that is no list - a refused
    login's {"user_info": {"auth": 0}}, an error object. Treated as a failed
    request: nothing is stored, the last good copy is served if there is
    one, else the load fails with the reason."""


def list_rows(data: Any) -> list | None:
    """A list action's answer as rows: a list as it is, with anything in it
    that is no object left out; an object of rows keyed by id (some panels
    answer so) as its rows; anything else - a refused login's user_info, an
    error object, a number - None."""
    if isinstance(data, list):
        if all(isinstance(r, dict) for r in data):
            return data
        return [r for r in data if isinstance(r, dict)]
    if (isinstance(data, dict) and data and "user_info" not in data
            and all(isinstance(v, dict) for v in data.values())):
        return list(data.values())
    return None


def json_object(pairs: list[tuple[str, Any]]) -> dict:
    """object_pairs_hook for the big lists. json.loads() is one C call that
    holds the interpreter (the GIL) to the end: a 30 MB movie list stopped
    the window, and the player's frames with it, for a third of a second.
    A Python call per object lets the window in between objects (6 ms at
    most), for a fifth more parse time. Keys stay shared between rows, as
    with a plain json.loads()."""
    return dict(pairs)


class ListCache:
    """The short list cache a provider client keeps, with its on-disk copy
    per playlist: shared by the clients whose lists come from a provider's
    API (Xtream, Stalker), so each degrades the same way when it is down."""

    # How long a fetched channel/movie/series list stays served from memory.
    # Lineups change rarely, and every consumer (mode switch, EPG guide, Home
    # shelves) used to re-download the entire multi-thousand-row list on each
    # visit - the dominant cost of "switching to TV feels slow". The Refresh
    # button clears this (see clear_list_cache), so a manual refresh is
    # always a real re-fetch.
    LIST_CACHE_SECS = 300
    # What this client's log lines start with.
    LOG_NAME = "list"

    server: str

    def __init__(self, cache_path: str | None = None) -> None:
        self._list_cache: dict[tuple, tuple[float, list]] = {}
        self._list_lock = threading.Lock()
        # Optional on-disk copy of the list cache (per playlist): lets the app
        # start with the last session's channel lineup when the provider is
        # down/overloaded, instead of empty lists and timeouts. One file per
        # list in <cache_path minus .json>.d/; cache_path itself is the
        # single file older versions wrote, read only as a last resort.
        self._cache_path = cache_path
        self._disk_loaded = cache_path is None
        # The reply behind the list _cached_list is fetching, per thread:
        # its bytes go to the disk copy as they came.
        self._tls = threading.local()
        # Wall-clock time of the last clear_list_cache(): a cached entry older
        # than this (the re-merged disk copy) is NOT treated as a fresh hit, so
        # Refresh forces a real re-fetch while the disk copy stays as a
        # network-failure fallback.
        self._refetch_after = 0.0

    def _load_disk_lists(self) -> None:
        """(under _list_lock) The single file of every list that older
        versions wrote, merged in as stale entries: the last resort when the
        provider doesn't answer and a list has no file of its own yet."""
        if self._disk_loaded or self._cache_path is None:
            return
        self._disk_loaded = True
        try:
            import json
            with open(self._cache_path, encoding="utf-8") as fh:
                raw = json.load(fh, object_pairs_hook=json_object)
            for k, (t, data) in raw.items():
                key = tuple(None if p == "" else p for p in k.split("\x1f"))
                self._list_cache.setdefault(key, (float(t), data))
            log.info("%s list cache: loaded %d lists from disk",
                     self.LOG_NAME, len(raw))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("%s list cache: could not read %s: %s",
                        self.LOG_NAME, self._cache_path, e)

    def _list_file(self, key: tuple) -> str | None:
        """The disk copy of one list. A file per list: with one file for
        all of them, every fetch - a category opened - wrote the whole
        catalogue again (tens of MB for a big provider), and every start
        read all of it back in, wanted or not."""
        if self._cache_path is None:
            return None
        import hashlib
        import os
        base = self._cache_path
        if base.endswith(".json"):
            base = base[:-5]
        name = "\x1f".join("" if p is None else str(p) for p in key)
        return os.path.join(base + ".d", hashlib.sha1(
            name.encode("utf-8")).hexdigest()[:24] + ".json")

    def _disk_hit(self, key: tuple, fresh_after: float | None = None
                  ) -> tuple[float, Any] | None:
        """*key*'s disk copy as a cache entry, read only when it will be
        used: written after *fresh_after* (None: whatever its age)."""
        import json
        import os
        path = self._list_file(key)
        if path is None:
            return None
        try:
            t = os.stat(path).st_mtime
            if fresh_after is not None and t < fresh_after:
                return None
            with open(path, "rb") as fh:
                body = fh.read()
            data = json.loads(body, object_pairs_hook=json_object) or []
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            log.warning("%s list cache: could not read %s: %s",
                        self.LOG_NAME, path, e)
            return None
        with self._list_lock:
            hit = self._list_cache.get(key)
            if hit is None or hit[0] < t:
                hit = self._list_cache[key] = (t, data)
        return hit

    def _save_list(self, key: tuple, t: float, body: bytes) -> None:
        """(worker thread) Keep one list's reply on disk, as it came: bytes
        out, nothing to encode. Whole or not at all; the file's time is
        the fetch's."""
        import os
        path = self._list_file(key)
        if path is None:
            return
        # Unique temp per writer: the same list fetched twice at once must
        # not have one writer's replace consume the other's file.
        tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.part"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            try:
                with open(tmp, "wb") as fh:
                    fh.write(body)
                os.utime(tmp, (t, t))
                os.replace(tmp, path)
                self._retire_single_file()
            finally:
                # Never leave a crumb if the write or replace failed.
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        except Exception as e:
            log.warning("%s list cache: could not write %s: %s",
                        self.LOG_NAME, path, e)

    # The single file of all lists that older versions wrote: read when a
    # list has no file of its own and the provider is down, and deleted once
    # it is this old - by then the lists have files of their own.
    SINGLE_FILE_KEEP_SECS = 14 * 86400

    def _retire_single_file(self) -> None:
        import os
        if self._cache_path is None or getattr(self, "_single_checked", False):
            return
        self._single_checked = True
        try:
            if (time.time() - os.stat(self._cache_path).st_mtime
                    > self.SINGLE_FILE_KEEP_SECS):
                os.unlink(self._cache_path)
                log.info("%s list cache: removed the old single file %s",
                         self.LOG_NAME, self._cache_path)
        except OSError:
            pass

    @staticmethod
    def _reply_body(data: Any, got: tuple | None) -> bytes:
        """The bytes of the reply *data* was parsed from (*got*: the reply
        and what it parsed to) - as the provider sent them, unless they were
        read in a declared non-UTF-8 charset - or else *data* encoded."""
        import json
        if got is not None and got[1] is data:
            r = got[0]
            enc = (r.encoding or "").lower().replace("-", "").replace("_", "")
            if enc in ("", "utf8", "utf8sig"):
                return r.content
            return r.text.encode("utf-8")
        return json.dumps(data).encode("utf-8")

    def _cached_list(self, key: tuple, fetch) -> list:
        """Serve *key* from the short list cache, else *fetch* and store.

        On a network failure a STALE copy (older than the TTL) is served when
        one exists - including the previous session's disk copy - so a down
        or overloaded provider degrades to slightly-old data, not to an error
        and an empty list. Thread-safe - list loads run on the worker pool."""
        now = time.time()
        # A hit counts as fresh only if it is under the TTL AND was fetched
        # since the last Refresh - otherwise Refresh would never re-fetch.
        fresh_after = max(now - self.LIST_CACHE_SECS, self._refetch_after)
        with self._list_lock:
            hit = self._list_cache.get(key)
        if hit is None:
            hit = self._disk_hit(key, fresh_after)
        if hit and hit[0] > fresh_after:
            return hit[1]
        self._tls.want_reply = True
        self._tls.reply = None
        try:
            data = fetch() or []
        except (requests.RequestException, NoList):
            self._tls.reply = None
            with self._list_lock:
                hit = self._list_cache.get(key)
            if hit is None:
                hit = self._disk_hit(key)
            if hit is None:
                with self._list_lock:
                    self._load_disk_lists()
                    hit = self._list_cache.get(key)
            if hit is not None:
                log.warning(
                    "%s %s @ %s: network failure - serving the cached "
                    "list from %.0f min ago instead", self.LOG_NAME, key[0],
                    self.server, (now - hit[0]) / 60)
                return hit[1]
            raise
        finally:
            self._tls.want_reply = False
        got, self._tls.reply = self._tls.reply, None
        with self._list_lock:
            self._list_cache[key] = (now, data)
        self._save_list(key, now, self._reply_body(data, got))
        return data

    def held(self, key: tuple) -> Any:
        """*key*'s list as this client holds it - in memory, else its disk
        copy, whatever its age - or None: never a request (a worker reads
        the disk)."""
        with self._list_lock:
            hit = self._list_cache.get(key)
        if hit is None:
            hit = self._disk_hit(key)
        return hit[1] if hit is not None else None

    def clear_list_cache(self) -> None:
        """Make the next call of every list re-fetch it (Refresh). The lists
        in memory stay as the last resort for a provider that doesn't
        answer - the same lists the disk copy holds. Dropping them meant
        reading that copy back in, tens of MB in one go for a big provider,
        on every refresh."""
        with self._list_lock:
            # Any entry older than now is no longer "fresh", so the next
            # call actually re-fetches.
            self._refetch_after = time.time()


#: Making a client's session, or asking something of it, one thread at a time.
_SESSION_LOCK = threading.RLock()


def _agent(name: str) -> Any:
    """A session's User-Agent as *name* (Mirrored.on_session)."""
    def put(session: Any) -> None:
        session.headers["User-Agent"] = name
    return put


class Mirrored:
    """Nerd's backup addresses on a client (core/mirrors.py): the addresses
    a request tries, the one that answered kept as ``server``. Without a
    Picker - no backup addresses, or Nerd off - a request asks ``server``
    alone, as it always did."""

    server: str
    picker: mirrors.Picker | None = None
    LOG_NAME = "list"

    # -- the requests session, made at its first use ---------------------------------

    @property
    def session(self) -> Any:
        """The client's requests session - made as it is first used (on the
        first request, usually on a worker), with what was asked of it so
        far (on_session) put on it then: a client is made before the window
        at every start, and making the session there brought requests,
        urllib3 and certifi onto the start's import chain, before the first
        frame (docs/PERFORMANCE.md)."""
        made = self.__dict__.get("_session")
        if made is None:
            with _SESSION_LOCK:
                made = self.__dict__.get("_session")
                if made is None:
                    made = requests.Session()
                    # Kept until every one has taken: a setup that failed
                    # is tried again with the next session, never left out.
                    for setup in self.__dict__.get("_session_setup", ()):
                        setup(made)
                    self.__dict__.pop("_session_setup", None)
                    self.__dict__["_session"] = made
        return made

    @session.setter
    def session(self, value: Any) -> None:
        self.__dict__["_session"] = value

    def on_session(self, setup: Any) -> None:
        """*setup(session)* on the client's session: at once when it is
        made, else as it is made - in the order asked."""
        with _SESSION_LOCK:
            made = self.__dict__.get("_session")
            if made is None:
                self.__dict__.setdefault("_session_setup", []).append(setup)
                return
        setup(made)

    def use_mirrors(self, picker: mirrors.Picker | None) -> None:
        self.picker = picker
        if picker is not None:
            self.server = picker.current

    def _bases(self) -> list[str]:
        """The addresses a request tries, the one in use first."""
        m = self.picker
        if m is None or len(m.addresses) < 2:
            return [self.server]
        from ..core import nerd
        if not nerd.current().on:
            # Nerd turned off since: the source's own address again.
            self.server = m.primary
            return [self.server]
        return m.order()

    def _landed(self, base: str) -> None:
        m = self.picker
        if m is not None and m.worked(base):
            self.server = base

    def _next_address(self, what: str, base: str, after: str, why: str) -> None:
        log.warning("%s %s @ %s: %s - trying the next address, %s",
                    self.LOG_NAME, what, mirrors.shown(base), why,
                    mirrors.shown(after))

    def get_from_server(self, path: str, params: dict[str, Any] | None,
                        timeout: Any, what: str = "", stream: bool = False) -> Any:
        """GET *path* of the server - of the next address when the one in
        use cannot be reached or answers 5xx. Raises what the last address
        tried raised; an answer that is not 5xx (401, 404...) is returned
        as it came, from the address that gave it."""
        bases = self._bases()
        for i, base in enumerate(bases):
            after = bases[i + 1] if i + 1 < len(bases) else None
            kw: dict[str, Any] = {"params": params, "timeout": timeout}
            if stream:
                kw["stream"] = True
            try:
                r = self.session.get(base + path, **kw)
            except (requests.Timeout, requests.ConnectionError) as e:
                if after is not None and mirrors.fails_over_error(e):
                    self._next_address(what, base, after, type(e).__name__)
                    continue
                raise
            if after is not None and mirrors.fails_over_status(r.status_code):
                self._next_address(what, base, after, f"HTTP {r.status_code}")
                try:
                    r.close()
                except Exception:
                    pass
                continue
            if len(bases) > 1:
                self._landed(base)
            return r
        raise requests.ConnectionError("no address to ask")

    def stream_failover(self, url: str, status: int | None) -> str | None:
        """(worker thread) A stream of this client did not play, and asked
        again it answered *status* (None: it could not be reached at all).
        When that says the address in use is down, the next address that
        serves the same stream is used from now on - its address returned;
        None when nothing changed."""
        bases = self._bases()
        if len(bases) < 2 or (status is not None and not mirrors.fails_over_status(status)):
            return None
        rest = mirrors.tail(url, bases[0])
        if rest is None:
            return None                 # rewritten elsewhere: not this server's
        for base in bases[1:]:
            try:
                r = self.session.get(base + rest, stream=True, timeout=(4, 4))
                code = r.status_code
                r.close()
            except requests.RequestException as e:
                log.info("%s stream @ %s: %s", self.LOG_NAME, mirrors.shown(base),
                         type(e).__name__)
                continue
            if 200 <= code < 400:
                self._landed(base)
                return base
            log.info("%s stream @ %s: HTTP %s", self.LOG_NAME, mirrors.shown(base), code)
        return None


class XtreamClient(Mirrored, ListCache):
    """HTTP client for the Xtream Codes player_api.php endpoint."""

    LOG_NAME = "xtream"

    def __init__(self, server: str, username: str, password: str,
                 cache_path: str | None = None) -> None:
        self.server = server.rstrip("/")
        if not self.server.startswith(("http://", "https://")):
            self.server = "http://" + self.server
        self.username = username
        self.password = password
        self.max_connections: int | None = None
        # What the provider last said of the account's connections: limit,
        # in use, and when (time.monotonic) - core/connections.py.
        self.account_seen: dict[str, Any] | None = None
        # From here on these two strings are masked out of every log record,
        # wherever they turn up - a stream URL, a requests exception, a line
        # written long after this call.
        register_secrets(username, password)
        self.on_session(_agent("dopeIPTV/1.0"))
        super().__init__(cache_path)
        # Monotonic deadline for the fail-fast cooldown after a network error.
        self._net_down_until = 0.0

    def _redact(self, text: str) -> str:
        """Strip the username/password from a string before logging it -
        requests embeds the full credentialed URL in its exception messages,
        and these logs are meant to be shared in bug reports."""
        for secret in (self.password, self.username):
            # The same floor as core.log.register_secrets: a one- or two-
            # letter value would mask every "p" or "u" in the line.
            if secret and len(secret) > 3:
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
        base: dict[str, Any] = {"username": self.username, "password": self.password}
        base.update(params)
        action = params.get("action") or "authenticate"
        now = time.monotonic()
        if now < self._net_down_until:
            raise requests.ConnectionError(tr(
                "err_provider_cooldown",
                secs=f"{self._net_down_until - now:.0f}"))
        t0 = time.monotonic()
        read_to = 60 if action in self._BIG_ACTIONS else 20
        try:
            # Nerd's backup addresses, when the source has them: the next
            # one when this one is down (core/mirrors.py).
            r = self.get_from_server("/player_api.php", base, (10, read_to), action)
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
            data = r.json(object_pairs_hook=json_object)
        except ValueError:
            log.warning(
                "xtream %s @ %s: non-JSON reply (content-type=%s, %d bytes) - "
                "usually a captive portal, wrong URL, or the provider is down",
                action, self.server, r.headers.get("content-type", "?"),
                len(r.content))
            raise
        if getattr(self._tls, "want_reply", False):
            self._tls.reply = (r, data)     # for _cached_list's disk copy
        return data

    def authenticate(self) -> dict:
        data = self._api()
        # A user_info that is no object ("", [], a number) is none: asked
        # .get() it raised (tests/test_fuzz_xtream.py).
        ui = data.get("user_info") if isinstance(data, dict) else None
        if not isinstance(ui, dict):
            log.warning("xtream auth @ %s: unexpected response (no user_info)",
                        self.server)
            raise LocalizedError("err_unexpected_response")
        if str(ui.get("auth", 0)) != "1":
            log.warning("xtream auth @ %s: rejected (auth != 1, status=%s)",
                        self.server, ui.get("status"))
            raise LocalizedError("err_wrong_login")
        # How many streams the account allows at once, when it says: a
        # recording that would need a second one is not offered with one.
        try:
            self.max_connections = int(ui.get("max_connections") or 0) or None
        except (TypeError, ValueError, OverflowError):     # 1e400 is infinity
            self.max_connections = None
        self._saw_account(ui)
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
        ui = data.get("user_info") or {}
        if isinstance(ui, dict):
            self._saw_account(ui)
        return {"user_info": ui,
                "server_info": data.get("server_info") or {}}

    def _saw_account(self, ui: dict) -> None:
        """What user_info says of the connections, kept with when."""
        from ..core import connections
        limit, active = connections.numbers(ui)
        if limit is not None or active is not None:
            self.account_seen = {"max": limit, "active": active, "at": time.monotonic()}

    def _rows(self, key: tuple, **params: Any) -> list[dict]:
        """A list action through the list cache, as rows (list_rows): what
        the panel answered, or the copy kept from before - an old copy that
        is no list (an older version stored a refused login's answer) is
        none."""
        return list_rows(self._cached_list(key, lambda: self._list_api(**params))) or []

    def _list_api(self, **params: Any) -> list:
        """A list action asked of the panel. A panel that refuses the login
        answers it with {"user_info": {"auth": 0}} - an object, not a list:
        its keys walked as rows crashed the category list. That, or any
        other answer that is no list, is a failed request (NoList)."""
        data = self._api(**params)
        rows = list_rows(data)
        if rows is not None:
            return rows
        ui = data.get("user_info") if isinstance(data, dict) else None
        refused = isinstance(ui, dict) and str(ui.get("auth", 0)) != "1"
        log.warning("xtream %s @ %s: no list in the answer (%s)",
                    params.get("action"), self.server,
                    "the login was refused" if refused else type(data).__name__)
        raise NoList("err_wrong_login" if refused else "err_unexpected_response")

    def live_categories(self) -> list[dict]:
        return self._rows(("get_live_categories",), action="get_live_categories")

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_live_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._rows(("get_live_streams", category_id), **p)

    def vod_categories(self) -> list[dict]:
        return self._rows(("get_vod_categories",), action="get_vod_categories")

    def vod_streams(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_vod_streams"}
        if category_id:
            p["category_id"] = category_id
        return self._rows(("get_vod_streams", category_id), **p)

    def series_categories(self) -> list[dict]:
        return self._rows(("get_series_categories",), action="get_series_categories")

    def series_list(self, category_id: str | None = None) -> list[dict]:
        p: dict[str, Any] = {"action": "get_series"}
        if category_id:
            p["category_id"] = category_id
        return self._rows(("get_series", category_id), **p)

    def held_series(self) -> tuple[list[dict], list[dict]] | None:
        """Every series and their categories as held already (memory or the
        disk copy), or None - never a request (the command palette)."""
        rows = list_rows(self.held(("get_series", None)))
        if rows is None:
            return None
        return rows, list_rows(self.held(("get_series_categories",))) or []

    def series_info(self, series_id: int | str) -> dict:
        return self._info(self._api(action="get_series_info", series_id=series_id))

    def vod_info(self, vod_id: int | str) -> dict:
        return self._info(self._api(action="get_vod_info", vod_id=vod_id))

    @staticmethod
    def _info(data: Any) -> dict:
        """An info answer as the object it is meant to be: a panel's [] (or
        a list of anything, a number) for an unknown title is none - every
        reader asks it .get() (tests/test_fuzz_xtream.py)."""
        return data if isinstance(data, dict) else {}

    def short_epg(self, stream_id: int | str, limit: int = 8) -> list[dict]:
        data = self._api(action="get_short_epg", stream_id=stream_id, limit=limit)
        return self._listings(data)

    @staticmethod
    def _listings(data) -> list[dict]:
        # Some panels answer an unknown stream with a bare [] instead of an
        # object; .get on it raised and lost the whole info panel.
        # And a row that is no object is none: each is asked .get().
        rows = data.get("epg_listings") if isinstance(data, dict) else None
        return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []

    def epg_table(self, stream_id: int | str) -> list[dict]:
        """Full EPG table - fallback when get_short_epg returns nothing."""
        data = self._api(action="get_simple_data_table", stream_id=stream_id)
        return self._listings(data)

    def xmltv(self) -> bytes:
        """The provider's full XMLTV guide."""
        t0 = time.monotonic()
        try:
            r = self.get_from_server("/xmltv.php",
                                     {"username": self.username,
                                      "password": self.password},
                                     (20, 180), "xmltv")
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

    def timeshift_urls(self, stream_id: int | str, start_dt: datetime,
                       duration_min: int) -> list[str]:
        """Candidate catch-up URLs in preference order. Panels differ in how
        they expose archive - the standard /timeshift .ts path, an .m3u8 (HLS)
        variant, and the older timeshift.php form - so the player tries each in
        turn and keeps the first that actually plays, instead of the caller
        having to know which scheme a given provider uses."""
        dur = int(duration_min)
        start = int(start_dt.timestamp())
        # The /timeshift/ path takes a formatted stamp; panels disagree on
        # whether it's local or UTC, so try both (a wrong-timezone stamp points
        # outside the archive and comes back as "unrecognized file format").
        stamp = start_dt.strftime("%Y-%m-%d:%H-%M")
        stamp_utc = datetime.fromtimestamp(start, timezone.utc).strftime(
            "%Y-%m-%d:%H-%M")
        base = f"{self.server}/timeshift/{self.username}/{self.password}"

        def php(s):
            return (f"{self.server}/streaming/timeshift.php?"
                    f"username={self.username}&password={self.password}"
                    f"&stream={stream_id}&start={s}&duration={dur}")

        # Archive-specific endpoints only. The utc/lutc-on-the-live-URL forms
        # were dropped: many panels ignore those params and just serve live,
        # which is worse than a clean "not available" - and the background probe
        # can't tell that apart, so we don't offer them for Xtream.
        out = [f"{base}/{dur}/{stamp}/{stream_id}.ts",
               f"{base}/{dur}/{stamp}/{stream_id}.m3u8"]
        if stamp_utc != stamp:
            out += [f"{base}/{dur}/{stamp_utc}/{stream_id}.ts",
                    f"{base}/{dur}/{stamp_utc}/{stream_id}.m3u8"]
        out += [php(stamp)]
        if stamp_utc != stamp:
            out += [php(stamp_utc)]
        return out


class OfflineClient(Mirrored):
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

    def authenticate(self) -> dict:
        return {}

    def account_info(self) -> dict:
        # M3U / demo / offline providers have no account to report.
        return {}

    def clear_list_cache(self) -> None:
        # Interface parity with XtreamClient's short list cache; this client
        # serves nothing, so there is nothing to drop.
        return None

    def held(self, key: tuple) -> Any:
        # Interface parity with XtreamClient's list cache: nothing is held.
        return None

    def held_series(self) -> tuple[list[dict], list[dict]] | None:
        """Interface parity: no list held ahead of a request - the command
        palette asks series_list as before (not a slow source)."""
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

    def timeshift_urls(self, stream_id: int | str, start_dt: datetime,
                       duration_min: int) -> list[str]:
        return []


class M3UClient(OfflineClient):
    """Backed by a plain M3U/M3U8 playlist (a URL or local file) instead of an
    Xtream API. Exposes the same interface as XtreamClient but Live-only -
    M3U lists have no movies/series/EPG API, so those stay empty (the guide
    can still come from the playlist's separate EPG URL). Channels are grouped
    by their ``group-title`` into categories."""

    LOG_NAME = "m3u"

    # A quoted attribute may hold a comma (group-title="News, Sports"); the
    # loose form still reads a line whose quotes do not balance. Possessive
    # (*+): what one part takes no other part takes back - the same match,
    # in linear time on a provider's line of any length (a run of digits
    # with no comma took quadratic time, the whole window waiting).
    _EXTINF = re.compile(
        r'#EXTINF:\s*+-?[\d.]*+(?P<attrs>(?:[^,"]|"[^"]*")*+),(?P<name>.*)')
    _EXTINF_LOOSE = re.compile(r'#EXTINF:-?\d*+\s*+(?P<attrs>[^,]*+),(?P<name>.*)')
    # A failed download is tried again after this long, not on every call.
    RETRY_SECS = 60.0
    # An attribute begins where a word begins: findall never starts again
    # inside a long word that is no attribute.
    _ATTR = re.compile(r'(?<![\w-])([\w-]++)="([^"]*)"')

    def _guide_ok(self, url: str) -> bool:
        """The playlist's own guide address (url-tvg) as the app may fetch
        it itself: from a playlist over the network only http(s), and from
        one on the internet never a host on this computer or a link-local
        one (core/stream_address.py, as for its streams). A playlist file
        on this computer names what it likes, as before."""
        if not self.server.lower().startswith(("http://", "https://")):
            return True
        if not url.lower().startswith(("http://", "https://")):
            return False
        if not stream_address.on_internet(self.server):
            return True
        if stream_address.host_refusal(url, home=not self.home_ok):
            return False
        if not self.home_ok:
            dest_guard.note_outside(url)
        return True

    def __init__(self, url: str, stable: bool = False, map_path: str = "") -> None:
        super().__init__()
        self.server = (url or "").strip()
        #: The source's own option (core/stream_address.HOME_OPTION), set by
        #: make_client: a list on the internet may name devices at home.
        self.home_ok = False
        # core/m3u_ids.py: *stable* - a channel's id made from the channel,
        # else its place in the list (and *map_path* told what each place's
        # stable id is, for the move at the next start).
        self.stable = stable
        self.map_path = map_path
        self._channels: list[dict] = []
        self._by_stream_id: dict[int, dict] = {}
        self._by_line: dict[int, dict] = {}
        self._loaded = False
        self._stale = False
        self._failed_at = 0.0
        self._load_lock = threading.Lock()
        # EPG URL advertised by the playlist's #EXTM3U header
        # (url-tvg / x-tvg-url), auto-detected while parsing.
        self.epg_url = ""

    # -- fetch + parse --------------------------------------------------------

    def _fetch(self) -> str:
        url = self.server
        if url.startswith(("http://", "https://")):
            # Nerd's backup addresses are other addresses of the playlist
            # itself: the next one when this one is down.
            r = self.get_from_server("", None, (20, 180), "playlist")
            r.raise_for_status()
            body = r.content
            if body[:2] == b"\x1f\x8b":
                # A playlist sent as a .gz file (not as the transfer's own
                # gzip, which requests unpacks): unpacked, as far as a
                # playlist is read.
                return self._text(self._gunzip(body))
            # UTF-8 first, whatever the server claims: requests falls back to
            # ISO-8859-1 for a text/* type without a charset, which turned
            # every å, ö and ü in a channel name into "Ã¥"-style junk.
            try:
                return body.decode("utf-8-sig")
            except UnicodeDecodeError:
                return r.text           # a declared charset, or a good guess
        if url.startswith("file:"):
            # A dropped file arrives as file:///C:/My%20Lists/tv.m3u - the
            # path has to be unquoted, and on Windows lose its leading slash.
            from urllib.parse import urlparse
            from urllib.request import url2pathname
            url = url2pathname(urlparse(url).path)
        from ..core import picture_files
        from ..core._lazy_requests import LIST_MAX
        with open(url, "rb") as fh:
            # As far as a playlist from the network is read, and no further
            # (a path that is no file - a device - never ends).
            raw = fh.read(LIST_MAX + 1)
        if len(raw) > LIST_MAX:
            raise PlaylistTooLarge("err_m3u_too_large", mb=LIST_MAX >> 20)
        # A playlist file of the person's own may name logos beside it:
        # its folder is one the image loader reads pictures from
        # (core/picture_files.py).
        picture_files.trust_also("playlists", os.path.dirname(os.path.abspath(url)))
        if raw[:2] == b"\x1f\x8b":
            raw = self._gunzip(raw)
        return self._text(raw)

    @staticmethod
    def _text(raw: bytes) -> str:
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return raw.decode("cp1252", errors="replace")

    @staticmethod
    def _gunzip(raw: bytes) -> bytes:
        """A gzip file's playlist, unpacked no further than a playlist is
        read (core/_lazy_requests.LIST_MAX)."""
        import zlib
        from ..core._lazy_requests import LIST_MAX
        unpack = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            out = unpack.decompress(raw, LIST_MAX + 1)
        except zlib.error:
            raise LocalizedError("err_m3u_no_channels") from None
        if len(out) > LIST_MAX or unpack.unconsumed_tail:
            raise PlaylistTooLarge("err_m3u_too_large", mb=LIST_MAX >> 20)
        return out

    def _parse(self, text: str) -> None:
        channels: list[dict] = []
        pending: dict | None = None
        sid = 0
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("#EXTINF"):
                m = (self._EXTINF.match(line)
                     or self._EXTINF_LOOSE.match(line))
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
                    "tv_archive_duration": int(days) if days.isdecimal() and len(days) <= 9 else (
                        7 if has_archive else 0),
                }
                # What a Nerd catch-up template's {catchup-id} stands for.
                cid = attrs.get("catchup-id", "").strip()
                if cid:
                    pending["catchup_id"] = cid
            elif line.startswith("#EXTM3U"):
                # Header line: many playlists advertise their XMLTV guide here
                # as url-tvg="..." (or x-tvg-url="..."). Grab it as an EPG hint.
                hdr = dict(self._ATTR.findall(line))
                url = (hdr.get("url-tvg") or hdr.get("x-tvg-url") or "").strip()
                if url:
                    # A playlist may list several comma-separated guides; the
                    # first reachable one is the sensible default.
                    first = url.split(",")[0].strip()
                    if self._guide_ok(first):
                        self.epg_url = first
            elif line.startswith("#"):
                continue                       # other directives: ignore
            elif pending is not None or "://" in line[:24]:
                if pending is None:
                    # A playlist of bare addresses, one per line, no
                    # #EXTINF: each a channel named from its address.
                    pending = self._bare(line)
                sid += 1
                pending["stream_id"] = sid
                pending["num"] = sid
                pending["category_id"] = pending["category_name"]
                # What is no stream whoever lists it (an edit list or a
                # filter graph in the address, core/stream_address.py) is no
                # address: the channel keeps its place and does not play.
                pending["_url"] = stream_address.played(line, False, "m3u")
                channels.append(pending)
                pending = None
        stable = m3u_ids.stable_ids(
            m3u_ids.channel_key(c["epg_channel_id"], c["name"]) for c in channels)
        if self.stable:
            for c, new_id in zip(channels, stable, strict=True):
                c["stream_id"] = new_id
        elif self.map_path and channels:
            m3u_ids.save_map(self.map_path, [
                (c["num"], new_id, c["name"], c["_url"])
                for c, new_id in zip(channels, stable, strict=True)])
        self._channels = channels
        # Index by stream_id for O(1) lookup - _channel() (and thus stream_url,
        # hit on every zap) would otherwise linear-scan the whole list. Rebuilt
        # here, the only place _channels is (re)assigned, so it can't go stale.
        self._by_stream_id = {c["stream_id"]: c for c in channels}
        # The other kind of id still plays its channel: read with stable
        # ids, one by place (kept by an older version on another computer, a
        # tuner's old lineup) the channel at that place; read by place, a
        # stable one that sync brought from a computer that moved already.
        self._by_line = ({c["num"]: c for c in channels} if self.stable else
                         dict(zip(stable, channels, strict=True)))

    @staticmethod
    def _bare(line: str) -> dict:
        """A channel for an address alone on its line: named by the last
        part of its path (no extension), else its host - never its login."""
        from urllib.parse import unquote, urlsplit
        try:
            parts = urlsplit(line)
            leaf = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1])
            name = leaf.rsplit(".", 1)[0] if "." in leaf[1:] else leaf
            name = name.strip() or (parts.hostname or "")
        except ValueError:
            name = ""
        return {"name": name[:120] or "?", "stream_icon": "", "epg_channel_id": "",
                "category_name": "Uncategorized", "catchup": "", "catchup_source": "",
                "tv_archive": 0, "tv_archive_duration": 0}

    def authenticate(self) -> dict:
        # Fetching + parsing the list is this provider's "auth": it proves the
        # URL is reachable and yields at least one channel.
        old = (self._channels, self._by_stream_id, self._by_line, self.epg_url)
        text = self._fetch()
        self._parse(text)
        if not self._channels:
            # A refresh that comes back empty keeps the lineup it had.
            self._channels, self._by_stream_id, self._by_line, self.epg_url = old
            raise LocalizedError(self.why_empty(text))
        self._loaded = True
        self._stale = False
        return {"user_info": {"auth": 1}}

    #: What a list with no channel in it is said as (why_empty).
    EMPTY_KEYS = ("err_m3u_empty", "err_m3u_html", "err_m3u_no_channels")

    @staticmethod
    def why_empty(text: str) -> str:
        """Why *text* gave no channel: nothing at all, a web page in its
        place (a login or an error page, a link to the page about the list),
        or a list with no entry that plays."""
        head = text.lstrip("\ufeff \t\r\n")[:4096].lower()
        if not head:
            return "err_m3u_empty"
        if head.startswith(("<!doctype html", "<html", "<head", "<body")) \
                or ("<html" in head and "#extinf" not in head):
            return "err_m3u_html"
        return "err_m3u_no_channels"

    def clear_list_cache(self) -> None:
        # Refresh: download the list again on the next call. The current
        # channels stay served until a new copy has parsed.
        self._stale = True
        self._failed_at = 0.0

    def _ensure(self, refresh: bool = True) -> None:
        """Load the list if there is none; with *refresh* also re-download it
        after clear_list_cache(). URL lookups pass refresh=False: they run on
        the UI thread when something plays, and must never wait on a
        download the category reload is already doing."""
        if self._loaded and (not self._stale or not refresh):
            return
        with self._load_lock:
            if self._loaded and (not self._stale or not refresh):
                return
            if (self._failed_at
                    and time.monotonic() - self._failed_at < self.RETRY_SECS):
                return
            try:
                self.authenticate()
                self._failed_at = 0.0
            except Exception as e:
                self._failed_at = time.monotonic()
                log.warning("m3u: loading the playlist failed - %s: %s",
                            type(e).__name__, e)

    # -- interface ------------------------------------------------------------

    def live_categories(self) -> list[dict]:
        self._ensure()
        # Each group once, in list order - kept by a dict: looked for in a
        # list, a playlist with a group per channel took time with the
        # square of its length (tests/test_fuzz_m3u.py).
        seen = dict.fromkeys(c["category_id"] for c in self._channels)
        return [{"category_id": g, "category_name": g} for g in seen]

    def live_streams(self, category_id: str | None = None) -> list[dict]:
        self._ensure()
        return [c for c in self._channels
                if category_id is None or c["category_id"] == category_id]

    def live_url(self, stream_id: int | str, fmt: str = "ts") -> str:
        self._ensure(refresh=False)
        return (self._channel(stream_id) or {}).get("_url", "")

    def _channel(self, stream_id: int | str) -> dict | None:
        try:
            sid = int(stream_id)
        except (TypeError, ValueError):
            return None
        return self._by_stream_id.get(sid) or self._by_line.get(sid)

    def timeshift_urls(self, stream_id: int | str, start_dt: datetime,
                       duration_min: int) -> list[str]:
        """Build catch-up URLs for an M3U channel from its catchup / catchup-
        source tags (the IPTV convention). Handles the common schemes -
        explicit template, ``append``, ``shift`` (utc/lutc) and ``flussonic`` -
        and always adds the utc/lutc and flussonic forms as fallbacks so a
        provider that doesn't spell out a source still gets a fair try."""
        self._ensure(refresh=False)
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


def _list_cache_path(playlist: dict) -> str | None:
    """Where a playlist's lists are kept on disk, or None without an id."""
    pid = (playlist or {}).get("id")
    if not pid:
        return None
    from ..core.source_files import file_part
    from .epg import _epg_cache_dir
    try:
        d = _epg_cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        return str(d / f"lists_{file_part(pid)}.json")
    except Exception:
        return None


# Boxes and servers on the home network (providers/boxes.py): an address,
# an optional login, and their own options under "options".
BOX_KINDS = ("enigma2", "tvheadend", "hdhomerun", "vdr", "dvbviewer", "nextpvr", "mythtv", "satip")


def make_box_client(playlist: dict):
    """The client for a box on the home network (kind in BOX_KINDS)."""
    kind = playlist.get("kind")
    args = (playlist.get("server", ""), playlist.get("username", ""),
            playlist.get("password", ""), playlist.get("options") or {})
    # Imported here, like stalker.py: they build on this module.
    if kind == "enigma2":
        from .enigma2 import Enigma2Client
        return Enigma2Client(*args)
    if kind == "tvheadend":
        from .tvheadend import TvheadendClient
        return TvheadendClient(*args)
    if kind == "vdr":
        from .vdr import VdrClient
        return VdrClient(*args)
    if kind == "dvbviewer":
        from .dvbviewer import DVBViewerClient
        return DVBViewerClient(*args)
    if kind == "nextpvr":
        from .nextpvr import NextPVRClient
        return NextPVRClient(*args)
    if kind == "mythtv":
        from .mythtv import MythTVClient
        return MythTVClient(*args)
    if kind == "satip":
        from .satip import SatIPClient
        return SatIPClient(*args)
    from .hdhomerun import HDHomeRunClient
    return HDHomeRunClient(*args)


# Media servers (providers/mediaserver.py): an address and the token the
# sign-in left ("token"; never a password), their ids under "options".
MEDIA_KINDS = ("jellyfin", "emby", "plex")


def make_media_client(playlist: dict):
    """The client for a media server (kind in MEDIA_KINDS)."""
    kind = playlist.get("kind")
    options = dict(playlist.get("options") or {})
    for key in ("user_id", "server_id", "connections"):
        # The source form's Test hands them over beside the options.
        if not options.get(key) and playlist.get(key):
            options[key] = playlist[key]
    server, token = str(playlist.get("server") or ""), str(playlist.get("token") or "")
    account = str(playlist.get("username") or "")
    if kind == "plex":
        from .plex import PlexClient
        return PlexClient(server, token, str(options.get("server_id") or ""), account, options)
    from .jellyfin import EmbyClient, JellyfinClient
    cls = EmbyClient if kind == "emby" else JellyfinClient
    return cls(server, token, str(options.get("user_id") or ""), account, options)


def make_client(playlist: dict):
    """The client for *playlist* (see _make_client), with the Nerd page's
    user agent, headers and proxy on its session - and the source's own
    route (core/net_route.py: own DNS, IP version, a pinned certificate)
    under it, which its guide download takes too (client.net_route)."""
    client = _make_client(playlist)
    _note_own_names(playlist or {})
    from ..core import nerd, net_route
    source = dict((playlist or {}).get("options") or {},
                  _kind=(playlist or {}).get("kind") or "xtream",
                  _server=str((playlist or {}).get("server") or ""))
    route = net_route.route_for(source)
    # Nerd as it is now, whenever the session is made (Mirrored.session).
    profile = nerd.current()

    def setup(session: Any) -> None:
        # A source's lists and playlists are the largest answers there are:
        # their own ceiling, above any real one (core/_lazy_requests.py).
        from ..core._lazy_requests import LIST_MAX
        session.max_response = LIST_MAX
        profile.apply_session(session, source)
        if route.active:
            from ..core import net_adapter
            net_adapter.mount(session, route)

    later = getattr(client, "on_session", None)
    if callable(later):
        later(setup)
    elif getattr(client, "session", None) is not None:
        setup(client.session)
    try:
        client.net_route = route
    except AttributeError:
        pass
    _use_mirrors(client, playlist or {})
    _nerd_lists(client, source, (playlist or {}).get("id"))
    return client


def _note_own_names(playlist: dict) -> None:
    """The names the person gave this source - its own server, its guide's
    address and its backup addresses - as theirs (dest_guard.note_own): a
    server at home by its public name stays reachable, its pictures too."""
    options = playlist.get("options") or {}
    kind = str(playlist.get("kind") or "xtream")
    for key in ("server", "url", "epg_url", "portal"):
        value = playlist.get(key)
        if isinstance(value, str) and value.strip():
            dest_guard.note_own(value.strip())
    if kind in mirrors.KINDS and options.get(mirrors.KEY):
        try:
            for address in mirrors.parse(options.get(mirrors.KEY), kind):
                dest_guard.note_own(address)
        except Exception:
            pass


def _use_mirrors(client, playlist: dict) -> None:
    """Nerd: the source's backup addresses on its client, starting from the
    one that answered last time (core/mirrors.py) - nothing while Nerd is
    off or the source has none."""
    use = getattr(client, "use_mirrors", None)
    kind = playlist.get("kind") or "xtream"
    primary = str(getattr(client, "server", "") or "")
    if use is None or not primary.startswith(("http://", "https://")):
        return
    found = mirrors.addresses(primary, playlist.get("options") or {}, kind)
    if len(found) < 2:
        return
    from ..core import nerd
    pid = str(playlist.get("id") or "")
    settings: Any = nerd.current().s

    def remember(address: str) -> None:
        # Told on whichever thread got the answer (the lists, the guide,
        # the player's probe): the setting is written on the window's.
        from ..core.workers import on_gui_thread
        on_gui_thread(mirrors.remember, settings, pid, address)
    use(mirrors.Picker(found, mirrors.remembered(settings, pid), remember, name=kind))


def _nerd_lists(client, source: dict, pid: str | None) -> None:
    """Channel lists through Nerd's picons and guide mapping, play
    addresses through the source's rewrite rules (core/url_rewrite.py) and
    then udpxy (core/nerd_lists.py) - each a no-op while Nerd is off or the
    setting is empty. Every address the window plays, records, casts or
    shares comes from these calls."""
    from ..core import nerd_lists
    from ..core.url_rewrite import rewrite
    streams, url = client.live_streams, client.live_url
    kind = str(source.get("_kind") or "xtream")
    server = str(source.get("_server") or "")
    remote = _is_remote(kind, server)
    internet = remote and _on_internet(kind, server, source)
    # A source on the internet never sends the app to a device at home -
    # unless the person said this one's list is their own (its option).
    home_ok = stream_address.home_allowed(source)
    if hasattr(client, "home_ok"):
        client.home_ok = home_ok

    def theirs(address: Any) -> str:
        """*address* as the source's own word may be played: what a stream
        can be from where the source is (core/stream_address.py)."""
        return stream_address.played(address, remote, kind, internet=internet, home_ok=home_ok)

    def handed(raw: Any, udpxy: bool = False) -> Any:
        """What is played for the address the source gave: through the
        person's rewrite rules (and udpxy), and only what a stream can be
        (core/stream_address.py) - the source says what goes to the player,
        the recorder and a cast. The source's word is judged as the source's,
        from the internet as such; what the person's own rules made of it is
        theirs - only what is no stream whoever lists it is kept from it -
        and their udpxy in front of a multicast group a stream of the home
        network's."""
        if not raw:
            return raw
        out = rewrite(raw, source)
        from_source = out == raw
        if from_source and not theirs(out):
            return ""
        if udpxy:
            out = nerd_lists.udpxy_url(out, source)
        return stream_address.played(out, remote and from_source, kind)

    def live_streams(category_id: str | None = None) -> list[dict]:
        return nerd_lists.decorate(streams(category_id), source, pid)

    def live_url(stream_id: int | str, fmt: str = "ts") -> str:
        return handed(url(stream_id, fmt), udpxy=True)

    client.live_streams = live_streams
    client.live_url = live_url
    vod, episode, archive, copy = (getattr(client, n, None)
                                   for n in ("vod_url", "episode_url", "timeshift_urls", "copy_url"))
    if copy is not None:
        def copy_url(stream_id: int | str) -> str:
            return handed(copy(stream_id), udpxy=True)
        client.copy_url = copy_url
    if vod is not None:
        def vod_url(stream_id: int | str, ext: str | None = None) -> str:
            return handed(vod(stream_id, ext))
        client.vod_url = vod_url
    if episode is not None:
        def episode_url(episode_id: int | str, ext: str | None = None) -> str:
            return handed(episode(episode_id, ext))
        client.episode_url = episode_url
    if archive is not None:
        def timeshift_urls(stream_id: int | str, start_dt: datetime,
                           duration_min: int) -> list[str]:
            urls = archive(stream_id, start_dt, duration_min)
            if not urls:
                return urls
            return [u for u in map(handed, urls) if u]
        client.timeshift_urls = timeshift_urls
    link = getattr(client, "archive_link", None)
    if link is not None:
        def archive_link(stream_id: int | str, start: float,
                         stop: float | None = None) -> dict | None:
            # A portal's archive by programme: its link is the portal's word
            # too (not rewritten: it was never one of the source's lists').
            got = link(stream_id, start, stop)
            if got is not None and not theirs(got.get("url")):
                return None
            return got
        client.archive_link = archive_link
    # A whole address the source gave outside these calls (a channel's own
    # catch-up address, ui/mw_timeshift.py): judged as these are.
    client.played_from_source = theirs
    if internet:
        _keep_pictures(client, home=not home_ok)


def _is_remote(kind: str, server: str) -> bool:
    """Whether a source's addresses come over the network: every kind but a
    playlist file on this computer (core/stream_address.py)."""
    return kind != "m3u" or server.strip().lower().startswith(("http://", "https://"))


#: Where a row or an info answer names a picture the lists fetch: a logo, a
#: poster, a backdrop, a still - by the app's own names for them and the
#: panels' and portals' that come through as they are.
PICTURE_KEYS = frozenset((
    "stream_icon", "cover", "cover_big", "movie_image", "backdrop_path", "backdrop",
    "screenshot_uri", "poster", "poster_path", "poster_url", "logo", "icon", "icon_url",
    "thumb", "image", "still", "still_path", "profile_path"))
#: The calls whose answers carry a source's pictures.
_PICTURE_CALLS = ("live_streams", "vod_streams", "series_list", "series_info", "vod_info",
                  "held_series", "resume_items")
#: How deep an info answer is looked through (a series: its episodes by
#: season, each with its info).
_PICTURE_DEPTH = 6


def _picture_kept(value: Any, home: bool = False) -> Any:
    """*value* (an address or a list of them) without what names this
    computer or a link-local host - with *home*, a device at home too
    (core/stream_address.picture_refusal). With *home* each picture kept
    is noted as an internet source's (dest_guard.note_outside): its public
    name may not lead to the home network when the app fetches it."""
    if isinstance(value, str):
        if value and stream_address.picture_refusal(value, home):
            return ""
        if home and value:
            dest_guard.note_outside(value)
        return value
    if isinstance(value, list):
        kept = [v for v in value if not (isinstance(v, str) and v
                                         and stream_address.picture_refusal(v, home))]
        if home:
            for v in kept:
                if isinstance(v, str) and v:
                    dest_guard.note_outside(v)
        return kept if len(kept) != len(value) else value
    return value


def _row_pictures(row: dict, home: bool = False) -> None:
    for key in row.keys() & PICTURE_KEYS:
        value = row[key]
        kept = _picture_kept(value, home)
        if kept is not value:
            row[key] = kept


def _deep_pictures(found: Any, depth: int = 0, home: bool = False) -> None:
    """An info answer's pictures, at any depth (a series' seasons and its
    episodes' own)."""
    if depth > _PICTURE_DEPTH:
        return
    if isinstance(found, dict):
        _row_pictures(found, home)
        found = found.values()
    elif not isinstance(found, (list, tuple)):
        return
    for value in found:
        if isinstance(value, (dict, list, tuple)):
            _deep_pictures(value, depth + 1, home)


def _pictures_kept(found: Any, home: bool = False) -> Any:
    """*found* - a list of rows, an info answer - with every picture a
    source on the internet named on this computer or a link-local host
    taken out, in place: the app itself fetches a row's pictures, and such
    an address had it send requests to this computer's or the cloud's
    services. With *home* a device of the home network's too (a source the
    person did not let in there). A list's rows are looked at for their own
    pictures; an info answer through and through."""
    if isinstance(found, dict):
        _deep_pictures(found, 0, home)
    elif isinstance(found, (list, tuple)):
        for item in found:
            if isinstance(item, dict):
                _row_pictures(item, home)
            elif isinstance(item, (list, tuple)):
                _pictures_kept(item, home)
    return found


def _picture_call(call: Any, home: bool = False) -> Any:
    """*call* with what it answers through _pictures_kept - a list the
    source's cache hands out again as it was looked at once."""
    last: dict[Any, Any] = {}

    def kept(*args: Any, **kwargs: Any) -> Any:
        got = call(*args, **kwargs)
        key: Any = (args, tuple(sorted(kwargs.items())))
        try:
            if last.get(key) is got:
                return got
        except TypeError:
            key = None
        _pictures_kept(got, home)
        if key is not None:
            if len(last) >= 64:
                last.clear()
            last[key] = got
        return got
    return kept


def _keep_pictures(client: Any, home: bool = False) -> None:
    """A source on the internet's lists and pages handed out with their
    pictures through _pictures_kept (*home*: a device at home's too)."""
    for name in _PICTURE_CALLS:
        call = getattr(client, name, None)
        if call is not None:
            setattr(client, name, _picture_call(call, home))


def _on_internet(kind: str, server: str, options: dict) -> bool:
    """Whether a source is on the internet (core/stream_address.on_internet):
    its own address, or one of the backup addresses its lists may come from
    instead (core/mirrors.py) - a source is the home network's only when
    every address it is read from is."""
    found = [server]
    if kind in mirrors.KINDS and options.get(mirrors.KEY):
        try:
            found += mirrors.parse(options.get(mirrors.KEY), kind)
        except Exception:
            return True
    return any(stream_address.on_internet(a) for a in found)


def _make_client(playlist: dict):
    """Build the right provider client for a stored playlist: an M3U-backed
    client when kind == 'm3u', a box on the home network for the kinds in
    BOX_KINDS, a media server for those in MEDIA_KINDS, a Stalker / Ministra
    portal client when kind == 'stalker',
    otherwise the Xtream API client (the API clients with a
    per-playlist on-disk list cache so a down provider still shows the last
    known lineup)."""
    kind = (playlist or {}).get("kind")
    if kind == "m3u":
        pid = str(playlist.get("id") or "")
        stable = m3u_ids.is_stable(pid)
        cache = _list_cache_path(playlist) if pid and not stable else None
        return M3UClient(playlist.get("server", ""), stable=stable,
                         map_path=m3u_ids.map_path(os.path.dirname(cache), pid)
                         if cache else "")
    if kind in BOX_KINDS:
        return make_box_client(playlist)
    if kind in MEDIA_KINDS:
        return make_media_client(playlist)
    if kind == "stalker":
        # Imported here: stalker.py builds on this module, and a playlist
        # of another kind never needs it.
        from .stalker import StalkerClient
        return StalkerClient(playlist.get("server", ""),
                             playlist.get("mac", ""),
                             cache_path=_list_cache_path(playlist))
    return XtreamClient(playlist["server"], playlist["username"],
                        playlist["password"],
                        cache_path=_list_cache_path(playlist))


def __getattr__(name: str) -> Any:
    """``DemoClient`` lives in providers/demo.py (it builds on this module);
    still importable from here."""
    if name == "DemoClient":
        from .demo import DemoClient
        return DemoClient
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


#: The pages of an Xtream panel a provider's link names (parse_xtream_url).
_XTREAM_ENDPOINTS = frozenset(("get.php", "player_api.php", "panel_api.php", "xmltv.php"))


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
    query) - with the path a panel sits under kept when the link names its
    get.php there (``http://host/iptv/get.php?...``: ``http://host/iptv``).
    Returns ``None`` when *text* isn't a URL carrying credentials, so
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
        # A panel under a path of its own (http://host/iptv/get.php?...):
        # its API is beside get.php there, not at the host's root.
        head, _, leaf = u.path.rpartition("/")
        if leaf.lower() in _XTREAM_ENDPOINTS and head.strip("/"):
            server += "/" + head.strip("/")
        return server, user, pw

    # Fallback: path-based stream URLs embed the credentials as the first two
    # path segments — /live/USER/PASS/ID.ext, /movie/..., /series/..., the
    # timeshift form /timeshift/USER/PASS/DURATION/START/ID.ext, or the bare
    # /USER/PASS/ID. What makes it a STREAM url is the numeric stream id at
    # the end; without one, any two-segment path read as credentials - an M3U
    # served at http://host:9191/output/m3u (Dispatcharr and friends) became
    # Xtream user "output", password "m3u", and the form flipped to Xtream
    # the moment the link was pasted (issue #17). A path ending in
    # .m3u/.m3u8 is a plain playlist, not an Xtream stream.
    if u.path.lower().endswith((".m3u", ".m3u8")):
        return None
    parts = [p for p in u.path.split("/") if p]
    if parts and parts[0] in ("live", "movie", "series", "timeshift"):
        parts = parts[1:]
    if (len(parts) >= 3 and parts[0] and parts[1]
            and re.fullmatch(r"\d+(\.[A-Za-z0-9]{1,5})?", parts[-1])):
        # As the panel wrote them into its address: a login with a space or
        # an "@" in it comes percent-encoded (pa%20ss is "pa ss").
        from urllib.parse import unquote
        return server, unquote(parts[0]), unquote(parts[1])
    return None


PLAYLIST_FILE_EXTS = (".m3u", ".m3u8")


def dropped_playlist(sources: list[str]) -> tuple[str, str, str, str] | None:
    """What a drop on the window adds as a playlist, if anything.

    *sources* are the dropped local paths and link addresses. A local
    .m3u/.m3u8 file is an M3U playlist; a link is classified exactly like a
    pasted one (see detect_provider_link). Returns the first that
    qualifies, as ``(kind, server, username, password)``."""
    for src in sources:
        s = (src or "").strip()
        if not s:
            continue
        if "://" not in s:
            if s.lower().endswith(PLAYLIST_FILE_EXTS):
                return ("m3u", s, "", "")
            continue
        if s.lower().startswith(("http://", "https://")):
            found = detect_provider_link(s)
            if found is not None:
                return found
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
    local = _playlist_file(s)
    if local:
        return "m3u", local, "", ""
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


def _playlist_file(s: str) -> str:
    """*s* when it names a playlist file on this computer - a path from its
    root (``/home/me/tv.m3u``, ``C:\\Lists\\tv.m3u8``) or a ``file:``
    address of one - else ""."""
    low = s.lower()
    if not low.endswith(PLAYLIST_FILE_EXTS) or "\n" in s:
        return ""
    if low.startswith("file:"):
        return s
    if s.startswith("/") or re.match(r"[A-Za-z]:[\\/]", s) or s.startswith("\\\\"):
        return s
    return ""


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
    """Decode Xtream's base64-encoded EPG text fields - as plain text, the
    HTML a provider copied in from a web page left out (tagless.plain)."""
    if not text:
        return ""
    try:
        return tagless.plain(base64.b64decode(text).decode("utf-8", "replace")).strip()
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
