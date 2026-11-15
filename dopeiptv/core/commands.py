"""What the app is asked to do from outside: the command line, a
dopeiptv:// link, another program (the remote API asks the same way).

    dopeiptv --play <address>        play a stream or file
    dopeiptv --channel "SVT1 HD"     play the active source's channel
    dopeiptv <address>               as --play
    dopeiptv dopeiptv://play?url=<address, url-encoded>   (a stream only)
    dopeiptv dopeiptv://channel/<name>   (or ?name=<name>)
    dopeiptv dopeiptv://open             bring the window forward
    dopeiptv dopeiptv://supporter?key=DS1-...   a supporter key from the
                                         site's thank-you, shown to be kept
    dopeiptv --tv                    TV mode (with any of the above too)

A command is a small dict - {"play": url}, {"channel": name},
{"open": True}, {"supporter": key}, {"tv": True} - or None when there
is nothing to do.
"""

from __future__ import annotations

import sys
from urllib.parse import parse_qs, unquote, urlsplit

SCHEME = "dopeiptv"
# What may be played when asked from elsewhere - a dopeiptv:// link (any web
# page can open one) or the remote API: a stream, never a file here.
_STREAMS = ("http://", "https://", "rtsp://", "rtmp://", "rtp://", "udp://",
            "srt://")
# What a bare argument may be to count as something to play: the person's
# own command line may name a file too.
_PLAYABLE = (*_STREAMS, "file://")


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
        return {"play": url} if stream_url_ok(url) else None
    if what == "channel":
        name = (q.get("name") or unquote(rest)).strip()
        return {"channel": name} if name else None
    if what in ("open", ""):
        return {"open": True}
    if what == "supporter":
        key = "".join(q.get("key", "").split())
        return {"supporter": key} if key.upper().startswith("DS1-") and 20 < len(key) < 200 else None
    return None


def _ok_url(url: str) -> bool:
    return url.lower().startswith(_PLAYABLE)


def _is_path(value: str, windows: bool | None = None) -> bool:
    """A file's own path on this computer: /... - and on Windows one with
    its drive (C:\\Films\\a.mkv) or a network path (\\\\nas\\films\\a.mkv)."""
    if value.startswith("/"):
        return True
    if windows is None:
        windows = sys.platform == "win32"
    if not windows:
        return False
    drive = len(value) > 2 and value[0].isalpha() and value[1] == ":" and value[2] in "\\/"
    return drive or value.startswith("\\\\")


def file_url_path(url: str, windows: bool | None = None) -> str:
    """The path a file:// address names, percent-decoded: on Windows with
    its drive (file:///C:/Films/a.mkv is C:\\Films\\a.mkv) or the computer
    of a network path (file://nas/films/a.mkv is \\\\nas\\films\\a.mkv)."""
    if windows is None:
        windows = sys.platform == "win32"
    u = urlsplit(url)
    if not windows:
        return unquote(u.path)
    host, path = u.netloc, u.path
    if host[1:2] in (":", "|"):                 # file://C:/Films/a.mkv
        path = host + path
    elif host and host.lower() != "localhost":
        path = "//" + host + path
    elif path[:1] == "/" and path[2:3] in (":", "|"):
        path = path[1:]                          # /C:/Films -> C:/Films
    if path[1:2] == "|":
        path = path[:1] + ":" + path[2:]
    return unquote(path).replace("/", "\\")


def stream_url_ok(url: str) -> bool:
    """A stream's address, as a link or the remote API may ask to play:
    no file:// and no path - a page or a program on the network must not
    open the person's own files."""
    return url.strip().lower().startswith(_STREAMS)


def parse_command(argv: list[str]) -> dict | None:
    """The command in a command line (argv without the program), or None.
    Unknown options are left to whoever reads them (--self-check, ...).
    ``--tv`` asks for TV mode, alone or with any other command."""
    args = [a for a in argv if a != "--tv"]
    cmd = _parse_command(args)
    if len(args) != len(argv):
        cmd = dict(cmd or {})
        cmd["tv"] = True
    return cmd


def _parse_command(args: list[str]) -> dict | None:
    for i, a in enumerate(args):
        if a in ("--play", "--channel") and i + 1 < len(args):
            value = args[i + 1].strip()
            if a == "--play":
                return {"play": value} if (_ok_url(value) or _is_path(value)) else None
            return {"channel": value} if value else None
        if a.startswith("--play="):
            value = a.split("=", 1)[1].strip()
            return {"play": value} if (_ok_url(value) or _is_path(value)) else None
        if a.startswith("--channel="):
            value = a.split("=", 1)[1].strip()
            return {"channel": value} if value else None
    for a in args:
        if a.lower().startswith(SCHEME + "://"):
            return parse_link(a)
        if not a.startswith("-") and _ok_url(a):
            return {"play": a}
    return None
