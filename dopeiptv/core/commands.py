"""What the app is asked to do from outside: the command line, a
dopeiptv:// link, another program (the remote API asks the same way).

    dopeiptv --play <address>        play a stream or file
    dopeiptv --channel "SVT1 HD"     play the active source's channel
    dopeiptv <address>               as --play
    dopeiptv dopeiptv://play?url=<address, url-encoded>   (a stream only,
                                         asked about in the window first)
    dopeiptv dopeiptv://channel/<name>   (or ?name=<name>; asked about in
                                         the window first)
    dopeiptv dopeiptv://open             bring the window forward
    dopeiptv --tv                    TV mode (with any of the above too)

A command is a small dict - {"play": url}, {"channel": name},
{"open": True}, {"tv": True} - or None when there
is nothing to do. A link's play and channel carry {"link": True} beside
them: any web page can open a link, so the window asks before it plays the
address or tunes the channel (ui/mw_commands.py). ``--channel`` on the
person's own command line, the remote API and Home Assistant are no links:
theirs plays as asked.

A link is all a start by one takes from its command line
(``own_arguments``): the system puts the link where the menu entry or the
registry says, and what follows it there is the link's own text - on
Windows a link with a quote in it otherwise ends its argument and goes on
as options of the app's, or of Qt's (-platformpluginpath and the like).
"""

from __future__ import annotations

import sys
import unicodedata
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

SCHEME = "dopeiptv"
# What may be played when asked from elsewhere - a dopeiptv:// link (any web
# page can open one) or the remote API: a stream, never a file here.
_STREAMS = ("http://", "https://", "rtsp://", "rtmp://", "rtp://", "udp://",
            "srt://")
# What a bare argument may be to count as something to play: the person's
# own command line may name a file too.
_PLAYABLE = (*_STREAMS, "file://")

#: The longest address a link may ask to play.
MAX_ADDRESS = 2000
#: The longest address or path of the person's own command line.
MAX_OWN = 8192
#: The longest channel name asked for.
MAX_NAME = 200
#: How much of a long address the window's question shows.
SHOWN = 500


def is_link(arg: str) -> bool:
    """Whether *arg* is a dopeiptv: link, as the system hands one over
    (whatever case its scheme is typed in)."""
    return str(arg or "").strip().lower().startswith(SCHEME + ":")


def own_arguments(argv: list[str]) -> list[str]:
    """*argv* (without the program) as far as it is the person's or the
    menu entry's own: up to and with the first dopeiptv: link. What comes
    after a link came with the link - nothing of it is read, by the app or
    by Qt."""
    for i, arg in enumerate(argv):
        if is_link(arg):
            return list(argv[:i + 1])
    return list(argv)


def plain(text: str) -> bool:
    """No space, control or invisible character in *text*: an address is
    shown as it is, and one that could be drawn as another (a right-to-left
    override) or runs over several lines is none."""
    return not any(c.isspace() or unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp")
                   for c in text)


def host_of(url: str) -> str:
    """The server an address names ("example.org", "example.org:8080",
    "[fd00::5]:554"); "" when it names none."""
    try:
        u = urlsplit(url)
        host, port = u.hostname or "", u.port
    except ValueError:
        return ""
    if not host:
        return ""
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{port}" if port else host


def link_address_ok(url: str) -> bool:
    """An address a link may ask to play: a stream's (``stream_url_ok``),
    of a size and in letters that can be shown whole, on a server it names."""
    return (stream_url_ok(url) and len(url) <= MAX_ADDRESS and plain(url)
            and bool(host_of(url)))


def shown(url: str, limit: int = SHOWN) -> str:
    """*url* for the window's question: a very long one with its middle
    left out, and places to break a line in (a zero-width space after each
    part - a label wraps at words, and an address is one word)."""
    if len(url) > limit:
        url = url[:limit - 80] + " … " + url[-60:]
    out = []
    run = 0
    for c in url:
        out.append(c)
        run = 0 if c == " " else run + 1
        if c in "/?&=,;" or run >= 48:
            out.append("\u200b")
            run = 0
    return "".join(out)


def parse_link(link: str) -> dict | None:
    """A dopeiptv:// link as a command."""
    try:
        u = urlsplit((link or "").strip())
        q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
    except ValueError:
        return None                 # "dopeiptv://[": no address at all
    if u.scheme.lower() != SCHEME:
        return None
    what = (u.netloc or u.path.lstrip("/").split("/", 1)[0]).lower()
    rest = u.path.lstrip("/") if u.netloc else u.path.lstrip("/").partition("/")[2]
    if what == "play":
        url = q.get("url", "").strip()
        return {"play": url, "link": True} if link_address_ok(url) else None
    if what == "channel":
        name = _name(q.get("name") or unquote(rest))
        return {"channel": name, "link": True} if name else None
    if what in ("open", ""):
        return {"open": True}
    return None


def _name(value: Any) -> str:
    """A channel's name as asked for: one line, no control characters; ""
    for none, or one longer than any channel's."""
    if not isinstance(value, str):
        return ""
    text = " ".join("".join(c for c in value if unicodedata.category(c) != "Cc"
                            or c.isspace()).split())
    return text if len(text) <= MAX_NAME else ""


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


def checked(cmd: Any) -> dict | None:
    """*cmd* as the window carries it out, whoever handed it over (the
    command line, a link, a second start's message, the remote API): only
    the keys there are, each of its kind and size - or None for anything
    else. An address is a stream's, or (not from a link) a file's by its
    whole path; never mpv's own schemes (av://, edl://, memory://...)."""
    if not isinstance(cmd, dict):
        return None
    out: dict[str, Any] = {}
    link = cmd.get("link") is True
    play = cmd.get("play")
    if play not in (None, ""):
        if not isinstance(play, str):
            return None
        play = play.strip()
        if link:
            ok = link_address_ok(play)
        else:
            ok = (len(play) <= MAX_OWN and "\x00" not in play
                  and (_ok_url(play) or _is_path(play)))
        if not ok:
            return None
        out["play"] = play
        if link:
            out["link"] = True
    if cmd.get("channel") not in (None, ""):
        name = _name(cmd.get("channel"))
        if not name:
            return None
        out["channel"] = name
        if link:
            out["link"] = True
    if cmd.get("number") is not None:
        number = cmd.get("number")
        if isinstance(number, bool) or not isinstance(number, (int, str)):
            return None
        out["number"] = number
    for flag in ("open", "tv"):
        if cmd.get(flag) is True:
            out[flag] = True
    return out


def parse_command(argv: list[str]) -> dict | None:
    """The command in a command line (argv without the program), or None.
    Unknown options are left to whoever reads them (--self-check, ...).
    ``--tv`` asks for TV mode, alone or with any other command. Nothing
    after a link is read (``own_arguments``)."""
    argv = own_arguments(argv)
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
