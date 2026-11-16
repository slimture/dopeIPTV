"""RTSP streams of a source that chooses how they travel (a SAT>IP server,
providers/satip.py): over TCP, interleaved in the RTSP connection itself -
what holds up over Wi-Fi, and the default - or as RTP over UDP to a port of
this computer, which a wired network with a server that takes no TCP may
need. The source's ``transport`` option says which ("http" plays the
server's HTTP address instead, which is no RTSP at all).

What it gives is set for that one stream: mpv's ``rtsp-transport`` as an
option of the file alone (the next stream has the player's own again), the
recorder's ``-rtsp_transport`` (ffmpeg) or ``--rtsp-transport`` (mpv). An
RTSP address of any other source, and every other address, gets nothing:
it opens exactly as it always did.

``options`` asks a server OPTIONS - which sets nothing up and takes no
tuner: how a SAT>IP server with no description is told apart, and what
Test connection asks of an RTSP stream instead of reading it.
"""

from __future__ import annotations

import re
import socket
from typing import Any
from urllib.parse import urlsplit

TCP, UDP = "tcp", "udp"
PORT = 554
#: The sources whose RTSP streams take their own transport.
KINDS = ("satip",)


def is_rtsp(url: str | None) -> bool:
    return str(url or "").strip().lower().startswith(("rtsp://", "rtsps://"))


def options(host: str, port: int = PORT, timeout: float = 2.0,
            user_agent: str = "dopeIPTV/1.0") -> dict[str, Any] | None:
    """What an RTSP server says to OPTIONS: {"status", "public", "server"};
    None when nothing answers RTSP there."""
    h = f"[{host}]" if ":" in host and not host.startswith("[") else host
    ask = (f"OPTIONS rtsp://{h}:{port}/ RTSP/1.0\r\nCSeq: 1\r\n"
           f"User-Agent: {user_agent}\r\n\r\n").encode()
    data = b""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(ask)
            while b"\r\n\r\n" not in data and len(data) < 16384:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
    except OSError:
        return None
    head, *lines = data.split(b"\r\n\r\n", 1)[0].decode("latin-1").split("\r\n")
    m = re.match(r"RTSP/1\.\d\s+(\d{3})", head)
    if not m:
        return None
    fields = {k.strip().lower(): v.strip() for k, sep, v in (x.partition(":") for x in lines) if sep}
    return {"status": int(m.group(1)), "public": fields.get("public", ""),
            "server": fields.get("server", "")}


def options_at(url: str, timeout: float = 2.0) -> dict[str, Any] | None:
    """OPTIONS asked of the server an rtsp:// address names (its port, else
    554)."""
    try:
        u = urlsplit(url)
        host, port = u.hostname or "", u.port or PORT
    except ValueError:
        return None
    return options(host, port, timeout) if host else None


def transport(source: dict[str, Any] | None, url: str | None) -> str:
    """"tcp" or "udp" for an RTSP address of a source that chooses (its
    ``transport`` option: UDP when it says so, else TCP); "" for anything
    else. *source*: the source's options with its kind as ``_kind`` (as
    core/nerd.py's ``source()`` keeps them)."""
    src = source or {}
    if src.get("_kind") not in KINDS or not is_rtsp(url):
        return ""
    return UDP if str(src.get("transport") or "").strip().lower() == UDP else TCP


def mpv_options(source: dict[str, Any] | None, url: str | None) -> dict[str, str]:
    """The player's options for *url* alone ({} for none)."""
    t = transport(source, url)
    return {"rtsp-transport": t} if t else {}


def ffmpeg_options(source: dict[str, Any] | None, url: str | None) -> list[str]:
    """ffmpeg's input options for recording *url* (before its -i)."""
    t = transport(source, url)
    return ["-rtsp_transport", t] if t else []


def mpv_args(source: dict[str, Any] | None, url: str | None) -> list[str]:
    """mpv's command-line option for recording *url* with mpv."""
    t = transport(source, url)
    return [f"--rtsp-transport={t}"] if t else []
