"""Connections to the account: how many the app holds itself, what the
provider said, and whether one more would go over the account's limit.

An Xtream account allows so many streams at once (``max_connections``) and
says how many are open (``active_cons``). The app opens connections of its
own - the player, the rewind buffer's (core/dvr_*.py), a recording made
over its own connection, each multiview cell, a cast (the TV, Kodi or the
converter here fetches the stream), a download let run beside them
(core/downloads.py) - and counts them as they are now (``Use``). What the provider counted besides them (another device on
the account) is taken from its last answer while it is fresh, less the
app's own at the time it was given.

It only counts and says: nothing here stops a stream. Unknown numbers
(no limit given, a source of another kind) say nothing at all.

Two sources of one provider account - the same server and user name, added
twice under other names or with other categories, or as Xtream and as its
M3U - share that account's connections: counted and checked as one
(``account_of``, ``accounts``, ``on_accounts``).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import parse_qs, urlsplit

from .num import whole

#: The app's kinds of connection, in the order a sentence names them ("server":
#: one server mode holds for a device watching through it).
KINDS = ("player", "buffer", "recording", "multiview", "cast", "download", "server")
#: How long what the provider said of other devices stands (its count
#: changes as soon as they stop or start).
FRESH_SECS = 180.0


@dataclass(frozen=True)
class Use:
    """One connection the app holds: its kind and the source (id) it is
    to - "" when not known, which counts as the source asked about - and
    the address it plays (what a box's tuners are asked about,
    core/tuners.py)."""

    kind: str
    source: str = ""
    url: str = field(default="", compare=False, repr=False)


@dataclass(frozen=True)
class Verdict:
    """One more connection would go over the limit: *limit* allowed,
    *in_use* now (*own* of them the app's, *others* another device's), and
    *after* with the new one."""

    limit: int
    in_use: int
    after: int
    own: int
    others: int
    kinds: tuple[str, ...] = ()


def _int(value: Any) -> int | None:
    n = whole(value)
    return n if n is not None and n >= 0 else None


def numbers(user_info: dict | None) -> tuple[int | None, int | None]:
    """(limit, in use) as an Xtream user_info gives them. A limit of 0 is
    none given (some panels write 0 for unlimited)."""
    ui = user_info or {}
    limit = _int(ui.get("max_connections"))
    return (limit or None), _int(ui.get("active_cons"))


def counted(kinds: Iterable[str]) -> list[tuple[str, int]]:
    """(kind, how many) in the order of KINDS - what a sentence lists."""
    got = list(kinds)
    return [(k, got.count(k)) for k in KINDS if k in got]


def is_network(url: str | None) -> bool:
    """A stream from the network that a provider counts - not a file of
    this computer's, nor the rewind buffer's own server on the loopback."""
    u = (url or "").strip().lower()
    if not u or "://" not in u:
        return False
    scheme, rest = u.split("://", 1)
    if scheme == "file":
        return False
    host = rest.split("/", 1)[0].rsplit("@", 1)[-1]
    host = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
    return host not in ("127.0.0.1", "localhost", "[::1]") and not host.startswith("127.")


_DEFAULT_PORTS = {"http": 80, "https": 443}


def account_of(playlist: dict | None) -> str:
    """The provider account a source is on, normalised: scheme, host, port
    and user name ("http://h.example.com:8080|u") - an M3U's from the
    ``username`` in its address, a portal's its MAC address. "" when it
    tells none (no server, or no user name: a box, a plain M3U)."""
    pl = playlist or {}
    server = str(pl.get("server") or "").strip()
    if not server:
        return ""
    if "://" not in server:
        server = "http://" + server
    try:
        parts = urlsplit(server)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").rstrip(".")
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return ""
    if not host:
        return ""
    user = str(pl.get("username") or "").strip()
    if not user:
        user = str((parse_qs(parts.query).get("username") or [""])[0]).strip()
    if not user and pl.get("mac"):
        user = "mac=" + str(pl.get("mac")).strip().lower()
    if not user:
        return ""
    where = f"[{host}]" if ":" in host else host
    return f"{scheme}://{where}:{port or ''}|{user}"


def accounts(playlists: Iterable[dict]) -> dict[str, str]:
    """Source id -> the id that stands for its account: the first source,
    in the list's order, on the same provider account (account_of); itself
    when no other source shares it."""
    first: dict[str, str] = {}
    out: dict[str, str] = {}
    for pl in playlists:
        pid = str((pl or {}).get("id") or "")
        if not pid:
            continue
        key = account_of(pl)
        out[pid] = first.setdefault(key, pid) if key else pid
    return out


def on_accounts(uses: Iterable[Use], account: Mapping[str, str]) -> list[Use]:
    """*uses* with each one's source put as its account (accounts()); an
    unknown source stays unknown."""
    return [Use(u.kind, account.get(u.source, u.source) if u.source else "", u.url)
            for u in uses]


def own_count(uses: Iterable[Use], source: str) -> int:
    """The app's connections to *source* (an unknown source counts)."""
    return sum(1 for u in uses if not u.source or u.source == source)


def kinds_of(uses: Iterable[Use], source: str) -> tuple[str, ...]:
    """The kind of each of *uses* to *source*, in the order of KINDS."""
    found = [u.kind for u in uses if not u.source or u.source == source]
    return tuple(sorted(found, key=lambda k: KINDS.index(k) if k in KINDS else len(KINDS)))


class Ledger:
    """What each source's provider last said, kept with the app's own count
    at that moment - so that what it counted besides the app (another
    device) can be told from the app's own connections later. Keyed by the
    id that stands for the source's account (accounts()), so two sources of
    one account are one entry."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._seen: dict[str, tuple[int | None, int | None, int, float]] = {}

    def report(self, source: str, limit: int | None, active: int | None,
               own: int, at: float | None = None) -> None:
        """The provider said *limit* and *active* (at *at*, monotonic; now
        when None) while the app held *own* connections to it."""
        when = self._clock() if at is None else at
        old = self._seen.get(source)
        if old is not None and old[3] >= when:
            return                      # this answer, or an older one, is kept
        if limit is None and old is not None:
            limit = old[0]              # a later answer that gives none
        self._seen[source] = (limit, active, own, when)

    def limit(self, source: str) -> int | None:
        got = self._seen.get(source)
        return got[0] if got else None

    def others(self, source: str) -> int:
        """Connections the provider counted that were not the app's, while
        that answer is fresh."""
        got = self._seen.get(source)
        if got is None or got[1] is None or self._clock() - got[3] > FRESH_SECS:
            return 0
        return max(0, got[1] - got[2])

    def forget(self, source: str) -> None:
        self._seen.pop(source, None)

    def check(self, source: str, uses: Iterable[Use], adding: int = 1,
              limit: int | None = None) -> Verdict | None:
        """A Verdict when *adding* more connections to *source*, next to
        *uses* (the app's own as they will be - one let go first left out),
        would go over its limit (*limit*, else the one the provider gave);
        None when it would not, or the limit is not known."""
        cap = limit if limit else self.limit(source)
        if not cap or adding <= 0:
            return None
        listed = list(uses)
        own = own_count(listed, source)
        others = self.others(source)
        after = own + others + adding
        if after <= cap:
            return None
        return Verdict(cap, own + others, after, own, others, kinds_of(listed, source))
