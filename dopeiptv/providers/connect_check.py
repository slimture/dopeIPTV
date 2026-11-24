"""A new source tried before it is kept - the welcome's connect step
(ui/welcome.py, ui/mw_onboarding.py): logged in, its lists read and counted,
and, when it does not work, why in plain words with what to do about it.

``check`` runs on a worker and never raises: its ``Outcome`` says how it
went. What it says comes from the app's own words - never the address, a
login or an error's raw text, which can hold both (a request's error names
the whole address, an Xtream login in its query). The lists it reads stay
in the client (its cache, an M3U's channels in memory), so the window that
takes the client next asks nothing again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from ..core.log import for_people, log
from ..core.num import whole
from ..i18n import LocalizedError, tr

#: What the window is told as the check goes, in order.
STEPS = ("login", "live", "vod", "series")


@dataclass
class Outcome:
    """How a try went: *ok* with the login's answer and how many channels,
    films and series came (None: not counted - a portal's are read page by
    page as they are opened), else *reason*. *warning*: said beside a good
    outcome (every connection of the account in use right now)."""

    ok: bool
    auth: Any = None
    channels: int = 0
    movies: int | None = None
    series: int | None = None
    reason: str = ""
    warning: str = ""
    stopped: bool = False


def _count(fn: Callable[..., Any] | None) -> int | None:
    """How many rows a list call gives; None when it cannot be read (a
    provider without films answers an error for them: no reason to fail)."""
    if not callable(fn):
        return None
    try:
        rows = fn(None)
    except Exception as e:
        log.info("first look: a list was not read - %s", type(e).__name__)
        return None
    return len(rows) if isinstance(rows, list) else None


def account_refusal(info: Any, now: float | None = None) -> str:
    """Why an account the server let in cannot be used: not active (banned,
    disabled), or its time is up - "" when it can."""
    if not isinstance(info, dict):
        return ""
    status = str(info.get("status") or "").strip()
    if status and status.lower() != "active":
        if status.lower() == "expired":
            return tr("diag_expired")
        return tr("diag_account_status", status=status[:40].capitalize())
    exp = whole(info.get("exp_date"), 0)
    if exp > 0 and exp < (time.time() if now is None else now):
        return tr("diag_expired")
    return ""


def connections_warning(info: Any) -> str:
    """Every connection the account has in use right now - it works, but a
    channel will not play until one is free."""
    if not isinstance(info, dict):
        return ""
    active = whole(info.get("active_cons"), 0)
    most = whole(info.get("max_connections"), 0)
    if most > 0 and active >= most:
        return tr("diag_conn_limit", active=active, maxc=most)
    return ""


def check(client: Any, say: Callable[[str], None] | None = None,
          stopped: Callable[[], bool] | None = None, kind: str = "") -> Outcome:
    """(a worker) Log *client* in and count what it has. *say(step)* hears
    each step of STEPS as it begins; *stopped()* true ends it at the next
    step, nothing more asked of the server. *kind*: the source's, for the
    words of a failure."""
    tell = say or (lambda _step: None)
    halt = stopped or (lambda: False)
    try:
        tell("login")
        auth = client.authenticate()
        if halt():
            return Outcome(False, stopped=True)
        info = auth.get("user_info") if isinstance(auth, dict) else None
        refused = account_refusal(info)
        if refused:
            return Outcome(False, auth, reason=refused)
        tell("live")
        live = client.live_streams(None)
        channels = len(live) if isinstance(live, list) else 0
        movies = series = None
        if not getattr(client, "paged_lists", False):
            for step, name in (("vod", "vod_streams"), ("series", "series_list")):
                if halt():
                    return Outcome(False, stopped=True)
                tell(step)
                got = _count(getattr(client, name, None))
                if step == "vod":
                    movies = got
                else:
                    series = got
        if halt():
            return Outcome(False, stopped=True)
        if not (channels or movies or series):
            return Outcome(False, auth, reason=tr("onb_err_nothing"))
        return Outcome(True, auth, channels, movies, series,
                       warning=connections_warning(info))
    except Exception as e:      # noqa: BLE001 - every failure said in words
        log.info("first look: the source did not answer as hoped - %s", type(e).__name__)
        return Outcome(False, reason=reason(e, kind))


#: An error of the app's own words, said with what to do about it.
_OWN = {"err_wrong_login": "onb_err_login", "err_unexpected_response": "onb_err_not_xtream"}


def reason(e: BaseException, kind: str = "") -> str:
    """*e* - why a try of a source of *kind* failed - in plain words with
    what to do."""
    from ..core._lazy_requests import requests
    if isinstance(e, LocalizedError):
        better = _OWN.get(e.key)
        if better:
            return tr(better)
        # A server's own words come through as they are: masked as the
        # notices mask them (an address its host alone, no login).
        return for_people(e.localized())
    exc = requests.exceptions
    if isinstance(e, exc.SSLError):
        return tr("diag_certificate") + " " + tr("onb_err_certificate_do")
    if isinstance(e, exc.Timeout):
        return tr("onb_err_timeout")
    if isinstance(e, exc.ConnectionError):
        return tr("onb_err_unreachable")
    if isinstance(e, exc.HTTPError):
        code = getattr(getattr(e, "response", None), "status_code", 0) or 0
        if code in (401, 403):
            return tr("onb_err_refused", code=code)
        if code == 404:
            return tr("onb_err_404")
        return tr("onb_err_http", code=code)
    if isinstance(e, getattr(requests, "ResponseTooLarge", ())):
        return tr("onb_err_too_large")
    if isinstance(e, getattr(requests, "RedirectRefused", ())):
        return tr("onb_err_redirect")
    if isinstance(e, ValueError) and kind in ("", "xtream"):
        # An answer that was not an Xtream server's (an error page, a
        # playlist's text instead of JSON).
        return tr("onb_err_not_xtream")
    if isinstance(e, OSError):
        return tr("onb_err_file")
    return tr("onb_err_other")
