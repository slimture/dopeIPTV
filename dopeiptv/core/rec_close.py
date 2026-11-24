"""The window closing while it records, no Qt (ui/mw_rec_close.py does the
rest): what a ``dopeiptv --server`` that runs now carries on, and what
closing would stop or miss.

**Carried on** (``carriable``) - only when a server runs already; one is
never started. A scheduled recording the window records now - claimed here
as it came due (core/rec_claims.py) - of a source the server takes, with
an address or a way to ask for one, at least CARRY_LEAST_S from its end,
that does not ride what plays privately. Its connection here ends first,
then its claim is let go and the server opens its own
(core/recording.py's ``shutdown`` *carry*, ``let_go_carried``): never two
connections at once for it, and a few seconds between the two parts, each
a recording of its own that says so in its info. A recording started by
hand ("until stopped") is never carried: nothing would end it.

**At stake** (``at_stake``) - why the window asks before it closes: a
recording running here that is not carried (closing stops it), and one due
within HORIZON_S - or due now and waiting for a free connection - that no
server takes (closing misses it). A recording the other recorder makes, or
holds, is its own and none of these.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .num import number

#: How far ahead a scheduled recording counts as due when the window closes.
HORIZON_S = 12 * 3600.0
#: A recording this close to its end stops as the window closes: a second
#: connection and a file of its own are not worth what is left of it.
CARRY_LEAST_S = 60.0


def _asks(fn: Callable[[dict], bool], j: dict) -> bool:
    try:
        return bool(fn(j))
    except Exception:
        return False


def carriable(j: Any, now: float, takes: Callable[[dict], bool],
              has_address: Callable[[dict], bool], private: bool = False) -> bool:
    """Whether recording *j*, recording here, goes on in the server that
    runs: *takes* - the server takes its source; *has_address* - it has an
    address or asks for one as it starts; *private* - it rides what plays
    privately (never carried: the server would name it)."""
    if not isinstance(j, dict) or private:
        return False
    if j.get("status") != "recording" or j.get("_elsewhere") or "_claim_state" not in j:
        return False
    stop = number(j.get("stop"))
    if stop is None or stop - now < CARRY_LEAST_S:
        return False
    return _asks(has_address, j) and _asks(takes, j)


@dataclass(frozen=True)
class Stake:
    """What closing the window now would cost: recordings running here it
    stops, and scheduled ones it misses."""
    running: int = 0
    due: int = 0

    @property
    def any(self) -> bool:
        return bool(self.running or self.due)


def at_stake(jobs: Iterable[Any], now: float, server: bool,
             takes: Callable[[dict], bool], carried: Iterable[str] = ()) -> Stake:
    """*jobs* (the window's recorder's) as closing now would leave them:
    *server* - a ``dopeiptv --server`` runs, which takes what is due of the
    sources it *takes*; *carried* - the ids it carries on."""
    gone = {str(c) for c in carried}
    running = due = 0
    for j in jobs or ():
        if not isinstance(j, dict) or j.get("_elsewhere"):
            continue                    # the other recorder's
        status = j.get("status")
        if status == "recording":
            if str(j.get("id")) not in gone:
                running += 1
        elif status == "scheduled":
            start, stop = number(j.get("start")), number(j.get("stop"))
            if start is None or start > now + HORIZON_S:
                continue
            if stop is not None and stop <= now:
                continue
            if server and _asks(takes, j):
                continue                # the server records it as it comes due
            due += 1
    return Stake(running, due)
