"""requests.get for an address with a login in it (http://user:pw@host/...),
answered the way the server asks: basic or digest.

requests alone only sends a basic login from the address, so a Tvheadend
channel (digest by default) failed with 401 wherever the app fetched the
stream itself - the rewind buffer's ingest - while mpv, which answers both,
played it. The player and this now agree. Basic still goes with the first
request, as it always did: only a 401 asking for digest changes that.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit


def split_login(url: str) -> tuple[str, str, str] | None:
    """(address without the login, user, password), or None without one."""
    u = urlsplit(url)
    if not u.username:
        return None
    host = u.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    netloc = host + (f":{u.port}" if u.port else "")
    return (urlunsplit((u.scheme, netloc, u.path, u.query, u.fragment)),
            unquote(u.username), unquote(u.password or ""))


def get_with_login(url: str, getter: Any = None, **kw: Any) -> Any:
    """GET *url*; a login in the address goes as basic at once, as
    requests.get(url) always sent it (1.4.5's ingest did: a server that
    answers a request without one with 403, not 401, never asks), and as
    digest when the server's 401 asks for that. *getter* is requests.get
    or a session's get."""
    if getter is None:
        import requests     # not at startup: the window opens without it
        getter = requests.get
    parts = split_login(url)
    if parts is None:
        return getter(url, **kw)
    bare, user, pw = parts
    import requests
    r = getter(bare, auth=requests.auth.HTTPBasicAuth(user, pw), **kw)
    if r.status_code != 401:
        return r
    if "digest" not in r.headers.get("WWW-Authenticate", "").lower():
        return r
    r.close()
    return getter(bare, auth=requests.auth.HTTPDigestAuth(user, pw), **kw)
