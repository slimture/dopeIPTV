"""Locate external player binaries (mpv / VLC) on the host.

A small OS-integration helper. It lives in ``core`` because both the
recording manager (``core``) and the external-player launchers
(``media``) need it, and neither layer should reach up into the other.
"""

from __future__ import annotations

import os
import shutil
import sys

#: Popen creationflags for a console program (ffmpeg, ffprobe) started from
#: the app. A windowed Windows build has no console of its own, so Windows
#: gave each one a console window: one flashing up per thumbnail and probe,
#: and one sitting open for the whole of a recording - close it and the
#: recording died. CREATE_NO_WINDOW; 0 everywhere else, where Popen accepts
#: nothing but 0.
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


#: What a stream's address starts with, for a program that is handed one to
#: fetch (the recorder, a cast's converter). Not on it, on purpose: what
#: reads this computer's own files and sockets by another name (concat:,
#: subfile:, fd:, pipe:, unix:, cache:, async:) and gopher:, which sends
#: whatever the address says to any port. A playlist's line is whatever its
#: author wrote.
STREAM_SCHEMES = frozenset({
    "http", "https", "rtsp", "rtsps", "rtp", "srtp", "udp", "udplite",
    "rtmp", "rtmpe", "rtmps", "rtmpt", "rtmpte", "rtmpts",
    "mms", "mmsh", "mmst", "srt", "rist", "ftp"})
#: ffmpeg's own names for what those are read with, and what is inside them:
#: an HLS list's segments (http, https), their keys (crypto, data), a proxy
#: (httpproxy), RTSP's and RTMP's carriers (tcp, tls, udp, rtp, ffrtmp*).
FFMPEG_PROTOCOLS = ("http,https,tcp,tls,udp,udplite,rtp,srtp,crypto,httpproxy,data,"
                    "rtmp,rtmpe,rtmps,rtmpt,rtmpte,rtmpts,ffrtmpcrypt,ffrtmphttp,"
                    "mmsh,mmst,srt,rist,ftp")
_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.")


def address_scheme(address: str) -> str:
    """What ffmpeg would read *address* with, as ffmpeg itself decides it
    (libavformat's url_find_protocol): the word before the first colon, in
    lower case - "" for a file's own path, a Windows drive's too."""
    text = str(address or "")
    n = 0
    while n < len(text) and text[n] in _SCHEME_CHARS:
        n += 1
    if text.startswith("subfile,") and ":" in text[n + 1:]:
        return "subfile"
    if n == 0 or n >= len(text) or text[n] != ":":
        return ""
    if n == 1:
        return ""                       # C:\Films\a.ts
    return text[:n].lower()


def is_local_file(address: str) -> bool:
    """A file's own path, or a file:// address of one."""
    return address_scheme(address) in ("", "file")


def fetchable(address: str) -> bool:
    """Whether *address* may be handed to a program that fetches or plays
    it: a stream's address (STREAM_SCHEMES) or a file's own path - never
    what starts with a dash, which a program reads as one of its options
    (the app's own paths are whole ones, from a root or a drive)."""
    text = str(address or "").strip()
    if not text or text.startswith("-") or "\x00" in text:
        return False
    scheme = address_scheme(text)
    return scheme in STREAM_SCHEMES or scheme in ("", "file")


def ffmpeg_protocols(address: str) -> list[str]:
    """ffmpeg's input options that keep what it reads for a stream at
    *address* to a stream's protocols (FFMPEG_PROTOCOLS) - the address
    itself, a redirect's, and whatever a playlist behind it names. None for
    a file's own path: ffmpeg's own rule for a file stands (the file and
    what lies beside it)."""
    if is_local_file(address):
        return []
    return ["-protocol_whitelist", FFMPEG_PROTOCOLS]


def find_player_executable(player: str) -> str | None:
    """Locate the mpv or vlc binary, or None if it isn't installed."""
    if player == "mpv":
        candidates = ["mpv"]
    else:
        candidates = ["vlc", "cvlc"]
    if sys.platform == "darwin":
        from .platform_macos import extra_player_candidates
        candidates += extra_player_candidates(player)
    for c in candidates:
        if os.path.isabs(c):
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c
        else:
            found = shutil.which(c)
            if found:
                return found
    return None
