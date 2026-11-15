"""Central logging for dopeIPTV.

One ``dopeiptv`` logger, configured once by :func:`configure_logging` at
startup. Modules do ``from ..core.log import log`` and call
``log.info/warning/error/debug`` instead of ``print()``.

- **stderr** keeps the historic ``[dopeIPTV] ...`` prefix, so the release
  smoke tests that grep for it still match and existing user habits hold.
- **Level** comes from ``DOPEIPTV_LOG`` (default ``INFO``). ``DOPEIPTV_LOG=debug``
  turns on the verbose probe / timeshift / image traces that used to hide
  behind separate ``DOPEIPTV_*_DEBUG`` flags - now one switch.
- Set ``DOPEIPTV_LOG_FILE=/path`` to also tee everything to a small rotating
  file, which makes user bug reports easy to capture.

Importing this module never configures logging (so tests importing app code
don't get handlers); only :func:`configure_logging`, called from ``main()``,
installs handlers. Before that, Python's last-resort handler still surfaces
WARNING+ to stderr, so nothing is ever silently lost.
"""
from __future__ import annotations

from typing import Any

import logging
import os
import re
import sys
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

log = logging.getLogger("dopeiptv")
_configured = False

# ------------------------------------------------------------- redaction ---
# A log gets pasted into a bug report, so a password in a log is a password
# published - and an Xtream stream URL carries the whole account in it:
#   http://host:8080/live/USERNAME/PASSWORD/1234.ts
# Two layers, because neither alone is enough. redact_url() understands the
# shapes credentials arrive in and is called where a URL is logged, so the
# intent is visible at the call site; register_secrets() masks the account's
# own values in EVERY record, which covers the shapes this file has not been
# taught and the ones added after it was written.

_secrets: set[str] = set()

# Query parameters that carry an account, whatever the provider calls it.
# A Stalker portal's links name the box (mac) and a play token, Plex's
# carry X-Plex-Token.
_CRED_PARAMS = frozenset((
    "username", "user", "password", "pass", "pwd", "token", "auth",
    "api_key", "apikey", "key", "secret", "sig", "signature",
    "mac", "play_token", "x-plex-token"))

# Xtream path layouts: /<kind>/<username>/<password>/<id>.<ext>. The bare
# /<username>/<password>/<id> form is deliberately NOT guessed at - it is
# indistinguishable from an ordinary three-segment URL, and the account's
# own values are masked by register_secrets() anyway.
_CRED_PREFIXES = frozenset(("live", "movie", "series", "timeshift"))

#: An address inside free text - the pattern the error report takes whole
#: addresses out by (core/report.py uses this one).
URL_IN_TEXT = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]{1,15})://[^\s'\"<>()\[\]]+")

# In text the app did not write (an ffmpeg or mpv line, an exception), the
# query values kept: they say what was asked, never who asked. Every other
# value goes - a plain M3U's ?token=..., a CDN's signature by any name.
_PLAIN_PARAMS = frozenset((
    "action", "type", "output", "format", "category_id", "stream_id",
    "vod_id", "series_id", "page", "limit", "offset"))

# The last segment of the bare Xtream form /<username>/<password>/<id>.<ext>.
_STREAM_ID = re.compile(r"\d+(?:\.[A-Za-z0-9]{1,5})?")


def register_secrets(*values: str | None) -> None:
    """Remember credential values so they never reach a log or a log file.

    Masking by VALUE rather than by position catches every shape the same
    credential arrives in - a path segment, a query parameter, a requests
    exception message quoting the full URL - without a parser that has to
    keep up with each new one."""
    for v in values:
        # A very short "secret" would blank out ordinary words all over the
        # log, and a credential that short protects nothing to begin with.
        if v and len(v) > 3:
            _secrets.add(v)


def redact(text: str) -> str:
    """Mask every registered credential value in *text*.

    Iterates a SNAPSHOT: a client is built on a worker thread (the account
    panel fetches through run_async), so register_secrets can add to the
    set while another thread is formatting a log line, and iterating a set
    that grows under you raises RuntimeError."""
    for s in tuple(_secrets):
        text = text.replace(s, "***")
    return text


def redact_url(url: object) -> str:
    """A URL with its account removed, safe to log.

    Handles the three places a credential hides: userinfo
    (http://user:pass@host), an Xtream path (/live/user/pass/1234.ts) and a
    query parameter (player_api.php?username=...&password=...). Anything
    that is not a URL - a local file path, None - passes through with only
    the registered values masked.

    Never raises: a malformed URL must not take down the log line that was
    trying to report it."""
    text = str(url) if url is not None else ""
    try:
        parts = urlsplit(text)
        if not parts.scheme or not parts.netloc:
            return redact(text)          # a local path, not a URL
        netloc = parts.netloc
        if "@" in netloc:
            netloc = "***@" + netloc.rsplit("@", 1)[1]
        segs = parts.path.split("/")
        # segs[0] is empty for a rooted path, so the kind is segs[1].
        if len(segs) > 3 and segs[1] in _CRED_PREFIXES:
            segs[2] = segs[3] = "***"
        query = parts.query
        if query:
            pairs = parse_qsl(query, keep_blank_values=True)
            # Rebuilt only when there is actually something to mask.
            # parse_qsl/urlencode is not a round trip for a query that is
            # not key=value pairs - "?token" comes back as "token=" - and
            # rewriting one we are not censoring only makes the log wrong.
            if any(k.lower() in _CRED_PARAMS for k, _ in pairs):
                query = urlencode([(k, "***" if k.lower() in _CRED_PARAMS
                                    else v) for k, v in pairs])
        return redact(urlunsplit((parts.scheme, netloc, "/".join(segs),
                                  query, parts.fragment)))
    except Exception:
        return redact(text)


def _masked_pair(pair: str) -> str:
    key, eq, value = pair.partition("=")
    if not eq:
        return "***" if pair else pair       # a bare value: "?<token>"
    if not value or key.lower() in _PLAIN_PARAMS:
        return pair
    return key + "=***"


def _address_masked(url: str) -> str:
    """An address found in free text, its account taken out: the login
    before the host, an Xtream account in the path - the bare
    /<user>/<pass>/<id> form too, which in text the app did not write
    cannot be told from a plain path and is masked either way - and every
    query value but those that only say what was asked."""
    end = len(url)
    while end and url[end - 1] in ".,;:!?":
        end -= 1                             # the sentence's, not the address's
    url, tail = url[:end], url[end:]
    try:
        parts = urlsplit(url)
    except ValueError:
        return url.split("://", 1)[0] + "://***" + tail
    netloc = parts.netloc
    if "@" in netloc:
        netloc = "***@" + netloc.rsplit("@", 1)[1]
    segs = parts.path.split("/")
    if len(segs) > 3 and segs[1] in _CRED_PREFIXES:
        segs[2] = segs[3] = "***"
    elif len(segs) == 4 and segs[1] and segs[2] and _STREAM_ID.fullmatch(segs[3]):
        segs[1] = segs[2] = "***"
    query = "&".join(_masked_pair(p) for p in parts.query.split("&")) if parts.query else ""
    frag = parts.fragment
    if "=" in frag:
        frag = "&".join(_masked_pair(p) for p in frag.split("&"))
    return urlunsplit((parts.scheme, netloc, "/".join(segs), query, frag)) + tail


def redact_text(text: str) -> str:
    """*text* the app did not write - a line ffmpeg or mpv printed, an
    exception's message - safe to log: every address in it masked
    (_address_masked), then every registered value. Never raises."""
    try:
        if "://" in text:
            text = URL_IN_TEXT.sub(lambda m: _address_masked(m.group(0)), text)
    except Exception:
        pass
    return redact(text)


def _redact_arg(value: object) -> object:
    """One argument of a record, masked. A number stays what it is (%d and
    friends need it); anything else that is not text - an exception, above
    all, whose message quotes the URL it failed on - becomes its masked
    text when masking changes it, and is left alone otherwise."""
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (int, float)):
        return value
    try:
        text = str(value)
    except Exception:
        return value
    clean = redact_text(text)
    return value if clean == text else clean


class _RedactFilter(logging.Filter):
    """The safety net: every record masked, whether or not the call site
    remembered to - the addresses in it (an ffmpeg or mpv line quoting a
    stream's address with its token or login, redact_text) and the
    registered values, in its message, its arguments and the traceback it
    carries (exc_info). Failure here must lose nothing, so any error leaves
    the record as it was."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                # Formatted first, then masked whole: an address can be
                # split between the message and an argument, and masking
                # the message alone could eat a %s an argument needs.
                text = record.getMessage()
                record.msg, record.args = redact_text(text), ()
            else:
                record.msg = _redact_arg(record.msg)
        except Exception:
            pass
        try:
            # Formatted here, once, masked: every handler's formatter uses
            # exc_text when it is there instead of formatting it again.
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact_text(record.exc_text)
            if record.stack_info:
                record.stack_info = redact_text(record.stack_info)
        except Exception:
            pass
        return True


_file_handler: logging.Handler | None = None


def log_dir() -> str:
    """Where the app keeps its own log files (Nerd: Save a log file):
    ~/.local/state/dopeiptv/logs on Linux (XDG_STATE_HOME; a Flatpak's own),
    ~/Library/Logs/dopeIPTV on a Mac, %LOCALAPPDATA%\\dopeiptv\\logs on
    Windows."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "dopeiptv", "logs")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Logs/dopeIPTV")
    state = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    return os.path.join(state, "dopeiptv", "logs")


def log_file() -> str:
    return os.path.join(log_dir(), "dopeiptv.log")


def set_file_logging(on: bool) -> str | None:
    """Keep a log file (rotating: 2 MB, three files at most) or stop.
    Scrubbed like every other line (the filter is on the logger). Returns
    the file written to, or None."""
    global _file_handler
    if not on:
        if _file_handler is not None:
            log.removeHandler(_file_handler)
            _file_handler.close()
            _file_handler = None
        return None
    if _file_handler is not None:
        return log_file()
    try:
        from logging.handlers import RotatingFileHandler
        os.makedirs(log_dir(), exist_ok=True)
        fh = RotatingFileHandler(log_file(), maxBytes=2_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    except Exception as e:
        log.warning("log file not kept: %s", e)
        return None
    log.addHandler(fh)
    _file_handler = fh
    log.info("log file: %s", log_file())
    return log_file()


class _Recent(logging.Handler):
    """The last few hundred lines, kept in memory (already redacted by the
    logger's filter): what an error report shows of what led up to it."""

    def __init__(self, size: int = 300) -> None:
        super().__init__()
        from collections import deque
        self.lines: Any = deque(maxlen=size)
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:
            pass


_recent = _Recent()
# From the first line on, configured or not (a test, an early error).
log.addHandler(_recent)


def recent_lines(n: int = 120) -> list[str]:
    """The last *n* log lines of this run."""
    return list(_recent.lines)[-n:]


def configure_logging() -> None:
    """Install the stderr (and optional file) handlers. Idempotent."""
    global _configured
    if _configured:
        return
    _configured = True
    level = getattr(
        logging, (os.environ.get("DOPEIPTV_LOG") or "INFO").upper(),
        logging.INFO)
    log.setLevel(level)
    log.propagate = False
    # On the logger, not on a handler: a handler-level filter would scrub
    # stderr and leave the file copy - the one that gets attached to bug
    # reports - untouched.
    log.addFilter(_RedactFilter())

    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("[dopeIPTV] %(message)s"))
    log.addHandler(sh)

    path = os.environ.get("DOPEIPTV_LOG_FILE")
    if path:
        try:
            from logging.handlers import RotatingFileHandler
            fh = RotatingFileHandler(
                path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(fh)
        except Exception:
            pass   # a bad log path must never stop the app from starting
