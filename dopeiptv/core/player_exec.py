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
#: What ffmpeg reads for an address it is handed through the bridge (an
#: alias, core/net_bridge.py): http to the bridge, an https segment elsewhere
#: through its tunnel (httpproxy, tls), an HLS list's keys (crypto, data) -
#: nothing more, since nothing but the bridge is reached.
BRIDGED_PROTOCOLS = "http,https,tcp,tls,httpproxy,crypto,data"
_SCHEME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+-.")
#: A query's words that name what opens an account: a password, a key, a
#: token (srt's passphrase, an rtmp server's token...).
LOGIN_WORDS = frozenset((
    "user", "username", "login", "pass", "password", "passwd", "pwd", "passphrase",
    "token", "auth", "key", "secret", "sig", "signature", "apikey", "api_key",
    "access_token", "session", "sessionid"))


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


def ffmpeg_protocols(address: str, bridged: bool = False) -> list[str]:
    """ffmpeg's input options that keep what it reads for a stream at
    *address* to a stream's protocols (FFMPEG_PROTOCOLS) - the address
    itself, a redirect's, and whatever a playlist behind it names; with
    *bridged* (an alias read through core/net_bridge.py) to what the bridge
    needs, BRIDGED_PROTOCOLS. A private list (private_list): read as the
    concat list it is, the file and a stream's protocols. None for a file's
    own path: ffmpeg's own rule for a file stands (the file and what lies
    beside it)."""
    if is_private_list(address):
        return ["-f", "concat", "-safe", "0", "-protocol_whitelist", "file," + FFMPEG_PROTOCOLS]
    if is_local_file(address):
        return []
    return ["-protocol_whitelist", BRIDGED_PROTOCOLS if bridged else FFMPEG_PROTOCOLS]


def _concat_word(text: str) -> str:
    """*text* as one word of ffmpeg's concat list: in single quotes, a quote
    in it closed, escaped and opened again."""
    return "'" + text.replace("'", "'\\''") + "'"


def concat_list(url: str, options: list[str] | tuple[str, ...] = ()) -> str:
    """ffmpeg's concat list (ffconcat) that reads *url* - the one way to hand
    ffmpeg an address that is not its argument - with *options* (["-key",
    "value", ...], the input's own) as its ``option`` lines (which an
    ffmpeg older than 5 does not know: its caller gives none then)."""
    lines = ["ffconcat version 1.0", f"file {_concat_word(url)}"]
    for key, value in zip(options[0::2], options[1::2], strict=False):
        lines.append(f"option {key.lstrip('-')} {_concat_word(value)}")
    return "\n".join(lines) + "\n"


#: A private concat list a converter reads a stream from (private_list).
LIST_PREFIX = "dopeiptv-read-"
LIST_SUFFIX = ".ffconcat"
#: How long one may stay while its program may start again - a cast's
#: converter on a receiver's new request: its feature drops it first.
LIST_KEEP_S = 12 * 3600.0


def private_list(url: str) -> str:
    """A private concat list (private_file) that hands a converter *url*
    - an address handed_privately says is not to be an argument -, kept
    until its feature drops it (drop_file) or LIST_KEEP_S; "" when none can
    be made, or for an address that would be two lines."""
    if any(c in url for c in "\r\n"):
        return ""
    return private_file(concat_list(url), LIST_PREFIX, LIST_SUFFIX, keep=LIST_KEEP_S)


def is_private_list(address: str) -> bool:
    """Whether *address* is a private list private_list made."""
    text = str(address or "")
    return (is_local_file(text) and text.endswith(LIST_SUFFIX)
            and os.path.basename(text).startswith(LIST_PREFIX))


def carries_login(address: str) -> bool:
    """Whether *address* holds what opens an account: a login before its
    host (user:password@), or a query word for a password, a key or a token
    (LOGIN_WORDS). An Xtream login in an http path is not seen here - every
    http(s) address is handed to a program as if it held one."""
    from urllib.parse import parse_qsl, urlsplit
    try:
        parts = urlsplit(str(address or ""))
        netloc = parts.netloc
    except ValueError:
        return True
    if "@" in netloc and netloc.rpartition("@")[0]:
        return True
    words = {k.strip().lower() for k, _v in parse_qsl(parts.query, keep_blank_values=True)}
    return bool(words & LOGIN_WORDS)


#: A multicast group's schemes: an address and a port, nothing that opens
#: an account - the one kind of network address still handed to a program
#: as an argument (handed_privately), unless a login stands in it.
GROUP_SCHEMES = frozenset(("udp", "udplite", "rtp"))


def handed_privately(address: str) -> bool:
    """Whether a program the app starts gets *address* in a private file
    (private_file) rather than as an argument: every network address of a
    stream (STREAM_SCHEMES) but a multicast group's - a token may stand in
    an rtsp or rtmp address's path, or under a query word carries_login
    does not know - and a group's with a login in it. A file's path and
    anything else as before. http(s) the bridge's or a handoff's anyway."""
    scheme = address_scheme(address)
    if scheme not in STREAM_SCHEMES:
        return False
    return scheme not in GROUP_SCHEMES or carries_login(address)


#: How long a private file that hands a program its address waits before it
#: is deleted: the program reads it as it starts.
HANDOFF_S = 60.0


def private_folder() -> str | None:
    """Where a private file is made: on Linux the user's own runtime folder
    (in memory, 0700, theirs); None for the system's temporary folder, where
    the file is made 0600 under a name nobody can guess."""
    run = os.environ.get("XDG_RUNTIME_DIR") or ""
    if sys.platform.startswith("linux") and os.path.isabs(run):
        try:
            st = os.stat(run)
            if st.st_uid == os.getuid() and not st.st_mode & 0o077:
                return run
        except OSError:
            pass
    return None


def private_file(text: str, prefix: str, suffix: str, folder: str | None = None,
                 keep: float = HANDOFF_S) -> str:
    """A file holding *text* for a program the app starts, this user's
    alone (0600, a name nobody can guess, in *folder* or private_folder()),
    deleted after *keep* seconds; one a crash left behind is swept. Its path,
    or "" when it cannot be made. An address goes to a program this way, not
    as an argument: every account on a Linux computer can read a process's
    command line (/proc/<pid>/cmdline)."""
    import tempfile
    import threading
    import time
    where = folder if folder is not None else private_folder()
    base = where or tempfile.gettempdir()
    try:
        for old in os.listdir(base):
            path = os.path.join(base, old)
            if old.startswith(prefix) and time.time() - os.path.getmtime(path) > 10 * max(keep, 1.0):
                os.remove(path)
    except OSError:
        pass
    try:
        fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=where)
    except OSError as e:
        from .log import log
        log.warning("no private file to hand a program its address (%s)", e)
        return ""
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)

    def gone() -> None:
        drop_file(path)
    timer = threading.Timer(keep, gone)
    timer.daemon = True
    timer.start()
    return path


def drop_file(path: str) -> None:
    """A private file gone (one already gone too)."""
    try:
        os.remove(path)
    except OSError:
        pass


def system_program(*parts: str) -> str:
    """A program of Windows' own by its full path under System32
    (%SystemRoot%\\System32\\ipconfig.exe): a bare name is looked for in
    the app's folder and the current one before the system's, where a file
    of the same name would be started instead."""
    import ntpath
    base = os.environ.get("SystemRoot") or os.environ.get("windir") or "C:\\Windows"
    return ntpath.join(base, "System32", *parts)


#: What a program on Windows' PATH may be: a program, never a batch file
#: (cmd.exe reads its arguments again - a stream's address among them).
_WIN_PROGRAM = (".exe", ".com")


def find_program(name: str) -> str | None:
    """shutil.which - but on Windows never the current folder, which it (and
    Windows itself, for a bare name) searches first: a folder the person
    opened a file from could hold an ffmpeg.exe of its own. Only the PATH's
    own folders, whole paths, and only a program found there."""
    if sys.platform != "win32":
        return shutil.which(name)
    exts = ("",) if name.lower().endswith(_WIN_PROGRAM) else _WIN_PROGRAM
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip().strip('"')
        if not folder or not os.path.isabs(folder):
            continue                    # "." or a relative one: the current folder's
        for ext in exts:
            path = os.path.join(folder, name + ext)
            if os.path.isfile(path):
                return path
    return None


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
            found = find_program(c)
            if found:
                return found
    return None
