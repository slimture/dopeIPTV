"""Internet radio from radio-browser.info: a community directory of radio
stations, open to anyone without a key or an account.

The directory runs on several servers. Their names are what the DNS answers
for ``all.api.radio-browser.info`` resolve back to (the way the API's own
documentation asks clients to find them); one is picked at random and the
next one tried when it does not answer, so no single server carries the app.

Every request names the app (``dopeIPTV/<version>``) and nothing about the
person. A click - "this station was played", which ranks the directory's
most-listened list - is sent only when the person turned that on
(``radio_report_clicks``, off by default; the window decides).

No Qt: every call is made on a worker thread, the window marshals the
answer back.
"""

from __future__ import annotations

import random
import socket
import threading
from collections.abc import Callable
from typing import Any

from .. import VERSION
from ..core._lazy_requests import requests
from ..core.log import log

DNS_NAME = "all.api.radio-browser.info"
_SUFFIX = ".api.radio-browser.info"
TIMEOUT = 10.0
#: Rows asked for one list; the directory has ~50,000 stations.
LIMIT = 200
#: Stored and kept setting: report a click when a station is played.
SETTING_CLICKS = "radio_report_clicks"

#: The browse lists, in the order the sidebar shows them.
TOP_VOTED, TOP_CLICKED = "topvote", "topclick"
COUNTRY, LANGUAGE, TAG = "country", "language", "tag"


class RadioError(Exception):
    """No server answered, or the one that did refused the request."""


def user_agent() -> str:
    return f"dopeIPTV/{VERSION}"


def discover_servers(
        resolve: Callable[..., Any] = socket.getaddrinfo,
        reverse: Callable[[str], Any] = socket.gethostbyaddr) -> list[str]:
    """The directory's servers by name, from its DNS round robin: each
    address resolved back to the server it belongs to (IPv4 and IPv6 name
    the same server once). Empty when the lookup fails."""
    try:
        infos = resolve(DNS_NAME, 443, 0, socket.SOCK_STREAM)
    except (OSError, UnicodeError) as e:
        log.info("radio-browser: no server list from DNS (%s)", e)
        return []
    names: list[str] = []
    for info in infos or ():
        try:
            ip = info[4][0]
            name = str(reverse(ip)[0]).strip().rstrip(".").lower()
        except (OSError, IndexError, TypeError, UnicodeError):
            continue
        if name.endswith(_SUFFIX) and name not in names:
            names.append(name)
    return names


def _text(v: Any, limit: int = 200) -> str:
    return " ".join(str(v or "").split())[:limit]


def _int(v: Any) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _http(url: Any) -> str:
    u = str(url or "").strip()
    return u if u.lower().startswith(("http://", "https://")) else ""


def station(d: Any) -> dict | None:
    """A station as the lists, the player and a favourite keep it - or
    None for one that cannot be played (no address, no id)."""
    if not isinstance(d, dict):
        return None
    uuid = _text(d.get("stationuuid"), 64)
    url = _http(d.get("url_resolved")) or _http(d.get("url"))
    if not uuid or not url:
        return None
    tags = [t for t in (_text(x, 40) for x in str(d.get("tags") or "").split(","))
            if t][:6]
    return {
        "stationuuid": uuid,
        "name": _text(d.get("name")) or url,
        "url": url,
        "stream_icon": _http(d.get("favicon")),
        "homepage": _http(d.get("homepage")),
        "country": _text(d.get("country"), 80),
        "countrycode": _text(d.get("countrycode"), 2).upper(),
        "language": _text(d.get("language"), 80),
        "tags": tags,
        "codec": _text(d.get("codec"), 16),
        "bitrate": _int(d.get("bitrate")),
        "hls": bool(_int(d.get("hls"))),
        "votes": _int(d.get("votes")),
        "_kind": "radio",
        "_key": "radio:" + uuid,
    }


def stations(rows: Any) -> list[dict]:
    """Every playable station of a list, each once."""
    out: list[dict] = []
    seen: set[str] = set()
    for d in rows if isinstance(rows, list) else []:
        st = station(d)
        if st is not None and st["stationuuid"] not in seen:
            seen.add(st["stationuuid"])
            out.append(st)
    return out


def groups(kind: str, rows: Any) -> list[dict]:
    """Countries, languages or genres as rows to open: name, what a
    station search for it asks (``_group``), and how many stations."""
    out: list[dict] = []
    for d in rows if isinstance(rows, list) else []:
        if not isinstance(d, dict):
            continue
        n = _int(d.get("stationcount"))
        name = _text(d.get("name"), 80)
        if not name or n <= 0:
            continue
        if kind == COUNTRY:
            code = _text(d.get("iso_3166_1") or d.get("countrycode"), 2).upper()
            if len(code) != 2:
                continue
            value = code
        else:
            value = name
            if kind == LANGUAGE:
                name = name[:1].upper() + name[1:]
        out.append({"name": name, "_kind": "radiogroup", "_group": [kind, value],
                    "_count": n, "_key": f"radiogroup:{kind}:{value}"})
    return out


class RadioBrowser:
    """One client for the window; thread-safe (each call its own request).

    *servers* fixes the server names (tests, a fake server: "127.0.0.1:8080"
    with *scheme* "http"); otherwise they come from the DNS on first use."""

    def __init__(self, servers: list[str] | None = None, scheme: str = "https",
                 discover: Callable[[], list[str]] = discover_servers,
                 timeout: float = TIMEOUT) -> None:
        self._servers = list(servers) if servers is not None else None
        self._discover = discover
        self.scheme = scheme
        self.timeout = timeout
        self._current: str | None = None
        self._lock = threading.Lock()

    def servers(self) -> list[str]:
        with self._lock:
            if self._servers is None:
                found = list(self._discover())
                random.shuffle(found)
                # Without a list the round-robin name answers by itself.
                self._servers = found or [DNS_NAME]
            return list(self._servers)

    def _order(self) -> list[str]:
        order = self.servers()
        cur = self._current
        if cur in order:
            order.remove(cur)
            order.insert(0, cur)
        return order

    def get(self, path: str, params: dict | None = None) -> Any:
        """GET /json/<path> from the first server that answers; the next
        one on a connection error, a timeout, a 5xx or a 429."""
        last = "no server"
        for host in self._order():
            url = f"{self.scheme}://{host}/json/{path}"
            try:
                r = requests.get(url, params=params, timeout=self.timeout,
                                 headers={"User-Agent": user_agent(),
                                          "Accept": "application/json"})
            except requests.RequestException as e:
                last = f"{host}: {type(e).__name__}"
                log.info("radio-browser: %s did not answer (%s)", host, type(e).__name__)
                continue
            if r.status_code >= 500 or r.status_code == 429:
                last = f"{host}: HTTP {r.status_code}"
                log.info("radio-browser: %s answered %s", host, r.status_code)
                continue
            if r.status_code != 200:
                raise RadioError(f"HTTP {r.status_code}")
            try:
                data = r.json()
            except ValueError:
                last = f"{host}: not JSON"
                continue
            self._current = host
            return data
        raise RadioError(last)

    # -- lists ---------------------------------------------------------------

    def top(self, which: str = TOP_VOTED, limit: int = LIMIT) -> list[dict]:
        which = which if which in (TOP_VOTED, TOP_CLICKED) else TOP_VOTED
        return stations(self.get(f"stations/{which}",
                                 {"limit": int(limit), "hidebroken": "true"}))

    def search(self, name: str = "", *, country: str = "", language: str = "",
               tag: str = "", limit: int = LIMIT) -> list[dict]:
        """Stations by name (any part), or every one of a country (its
        two-letter code), a language or a genre - the most voted first."""
        params: dict[str, Any] = {"limit": int(limit), "hidebroken": "true",
                                  "order": "votes", "reverse": "true"}
        if name.strip():
            params["name"] = name.strip()
        if country:
            params["countrycode"] = country
        if language:
            params["language"] = language
            params["languageExact"] = "true"
        if tag:
            params["tag"] = tag
            params["tagExact"] = "true"
        return stations(self.get("stations/search", params))

    def group(self, kind: str, value: str, limit: int = LIMIT) -> list[dict]:
        """The stations of a country, a language or a genre row."""
        if kind == COUNTRY:
            return self.search(country=value, limit=limit)
        if kind == LANGUAGE:
            return self.search(language=value, limit=limit)
        return self.search(tag=value, limit=limit)

    def countries(self) -> list[dict]:
        return groups(COUNTRY, self.get("countries", {"hidebroken": "true",
                                                      "order": "name"}))

    def languages(self, limit: int = LIMIT) -> list[dict]:
        return groups(LANGUAGE, self.get(
            "languages", {"hidebroken": "true", "order": "stationcount",
                          "reverse": "true", "limit": int(limit)}))

    def tags(self, limit: int = LIMIT) -> list[dict]:
        return groups(TAG, self.get(
            "tags", {"hidebroken": "true", "order": "stationcount",
                     "reverse": "true", "limit": int(limit)}))

    def lists(self, kind: str) -> list[dict]:
        return {COUNTRY: self.countries, LANGUAGE: self.languages,
                TAG: self.tags}[kind]()

    def click(self, uuid: str) -> bool:
        """Tell the directory a station was played (only when the person
        turned it on). True when it was counted."""
        if not uuid:
            return False
        try:
            data = self.get(f"url/{uuid}")
        except RadioError as e:
            log.info("radio-browser: click not counted (%s)", e)
            return False
        return bool(isinstance(data, dict) and data.get("ok") in (True, "true"))
