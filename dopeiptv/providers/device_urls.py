"""What a device on the network names for itself, kept to that device.

A SAT>IP server's description names its channel list, an HDHomeRun's
broadcast answer its address, a Jellyfin's its server: each is the
device's word (AGENTS.md rule 5), and none may send the app elsewhere - to
a file of this computer, to its loopback, to another host on the network or
out to the internet. ``on_host`` takes such an address over http or https
only and puts it on the device that answered (its port, path and query
kept: a device on two networks names the other one now and then, and its
other address is still itself). What the person typed is theirs and never
passes through here. (DLNA keeps its own: providers/dlna.py.)
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

SCHEMES = ("http", "https")


def _bare(host: str | None) -> str:
    return (host or "").strip().strip("[]").lower()


def host_of(url: Any) -> str:
    """The host *url* names ("" for none, or an address that does not parse)."""
    try:
        return _bare(urlsplit(str(url or "").strip()).hostname)
    except ValueError:
        return ""


def on_host(url: Any, host: str | None, schemes: tuple[str, ...] = SCHEMES) -> str:
    """*url*, a device's own address, on *host* - the device that answered:
    "" when it is no address of *schemes* (a file, an option, nothing);
    the same address when it is on *host* already; else its port, path and
    query on *host*, with no login."""
    text = str(url or "").strip()
    target = _bare(host)
    if any(c <= " " or c == "\x7f" for c in text):
        # No address holds a space or a control character; urlsplit drops a
        # tab or a CR without a word, and the text with it in was handed on
        # ("h\rttp://...", tests/test_fuzz_readers.py).
        return ""
    try:
        u = urlsplit(text)
        port = u.port
    except ValueError:
        return ""
    if u.scheme.lower() not in schemes or not u.hostname or not target:
        return ""
    if _bare(u.hostname) == target:
        return text
    netloc = f"[{target}]" if ":" in target else target
    return urlunsplit((u.scheme, f"{netloc}:{port}" if port else netloc, u.path, u.query, ""))
