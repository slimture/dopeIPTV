"""What the app is asked to do from outside: the command line, a
dopeiptv:// link, another program (the remote API asks the same way).

    dopeiptv --play <address>        play a stream or file
    dopeiptv --channel "SVT1 HD"     play the active source's channel
    dopeiptv <address>               as --play
    dopeiptv dopeiptv://play?url=<address, url-encoded>
    dopeiptv dopeiptv://channel/<name>   (or ?name=<name>)
    dopeiptv dopeiptv://open             bring the window forward

A command is a small dict - {"play": url}, {"channel": name},
{"open": True} - or None when there is nothing to do.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlsplit

SCHEME = "dopeiptv"
# What a bare argument may be to count as something to play.
_PLAYABLE = ("http://", "https://", "rtsp://", "rtmp://", "rtp://", "udp://",
             "srt://", "file://")


def parse_link(link: str) -> dict | None:
    """A dopeiptv:// link as a command."""
    u = urlsplit((link or "").strip())
    if u.scheme.lower() != SCHEME:
        return None
    what = (u.netloc or u.path.lstrip("/").split("/", 1)[0]).lower()
    rest = u.path.lstrip("/") if u.netloc else u.path.lstrip("/").partition("/")[2]
    q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
    if what == "play":
        url = q.get("url", "").strip()
        return {"play": url} if _ok_url(url) else None
    if what == "channel":
        name = (q.get("name") or unquote(rest)).strip()
        return {"channel": name} if name else None
    if what in ("open", ""):
        return {"open": True}
    return None


def _ok_url(url: str) -> bool:
    return url.lower().startswith(_PLAYABLE)


def parse_command(argv: list[str]) -> dict | None:
    """The command in a command line (argv without the program), or None.
    Unknown options are left to whoever reads them (--self-check, ...)."""
    args = list(argv)
    for i, a in enumerate(args):
        if a in ("--play", "--channel") and i + 1 < len(args):
            value = args[i + 1].strip()
            if a == "--play":
                return {"play": value} if (_ok_url(value) or value.startswith("/")) else None
            return {"channel": value} if value else None
        if a.startswith("--play="):
            value = a.split("=", 1)[1].strip()
            return {"play": value} if (_ok_url(value) or value.startswith("/")) else None
        if a.startswith("--channel="):
            value = a.split("=", 1)[1].strip()
            return {"channel": value} if value else None
    for a in args:
        if a.lower().startswith(SCHEME + "://"):
            return parse_link(a)
        if not a.startswith("-") and _ok_url(a):
            return {"play": a}
    return None
