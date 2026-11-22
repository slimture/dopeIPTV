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

from itertools import permutations
from typing import Any, Callable

import base64
import json
import logging
import os
import re
import sys
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urlsplit, urlunsplit

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
#: Credentials of three characters or fewer: masked as words of their own.
_short: set[str] = set()

# Query parameters that carry an account, whatever the provider calls it.
# A Stalker portal's links name the box (mac) and a play token, Plex's
# carry X-Plex-Token, Jellyfin's and Emby's X-Emby-Token or api_key, an
# OAuth answer access_token and refresh_token.
_CRED_PARAMS = frozenset((
    "username", "user", "password", "pass", "pwd", "token", "auth",
    "api_key", "apikey", "api-key", "x-api-key", "key", "secret", "sig",
    "signature", "mac", "play_token", "x-plex-token", "x-emby-token",
    "x-mediabrowser-token", "access_token", "refresh_token"))

# -- logins in free text, not inside an address: the error report's own
# patterns (core/report.py, which imports this module - so they are written
# out here; tests/test_log_redaction.py holds the two to the same). A login
# written name=value or name: value - a quoted name or value too, as JSON
# and config files write them ("password": "x y"); the quotes stay. The
# report's has "login" and "host" too; a log keeps them, or nothing in it
# would say which server a line is about. And "name = ..." is left alone:
# a line of Python in a traceback, a space before the "=" its style.
KEYVAL = re.compile(r"(?i)\b(user(?:name)?|pass(?:word)?|pwd|(?:access_|refresh_|play_)?token|key|"
                    r"api[_-]?key|secret|auth|sid|session|mac)([\"']?(?:\s*:|=)\s*)"
                    r"(\"[^\"\n]{0,200}\"|'[^'\n]{0,200}'|[^\s&'\",;]+)")
#: A header that carries a login, to the end of its line, and a bearer
#: token wherever it stands.
AUTH_LINE = re.compile(r"(?i)\b((?:proxy-)?authorization|(?:set-)?cookie)([\"']?\s*[:=]\s*)[^\r\n]*")
BEARER = re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}")
#: A box's MAC (a Stalker portal's login).
MAC = re.compile(r"(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}(?![0-9A-Fa-f])")
# A query without its address ("Max retries exceeded with url:
# /get.php?username=...&password=..."), and an Xtream account in a path
# without its address ("url: /live/<user>/<pass>/1234.ts").
_QUERY_IN_TEXT = re.compile(r"(?<=[\w/.%~-])\?(?=[\w.~%+-]+=)[^\s'\"<>()\[\]#]+")
_PATH_IN_TEXT = re.compile(r"(?<![\w/.:%@-])/(live|movie|series|timeshift)/"
                           r"[^/\s'\"<>?#]+/[^/\s'\"<>?#]+/")
# What a terminal or a log viewer would act on (a colour, a cursor move, a
# line rewritten): every C0 and C1 control but tab and newline.
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")

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


#: A login is masked as written and as each of these writes it.
_HEX = re.compile(r"%[0-9A-F]{2}")
_U_HEX = re.compile(r"\\u([0-9a-f]{4})")


def encoded_forms(value: str) -> set[str]:
    """*value* as an address, a form, JSON or Python's repr writes it, where
    that is not the value itself: percent-encoded (quote, quote_plus; the
    hex upper and lower case) and escaped (JSON with and without \\uXXXX,
    either case, an escaped "/"; a repr). The error report hides a login in
    all of them too (core/report.py)."""
    out: set[str] = set()
    for safe in ("", "/"):
        for q in (quote(value, safe=safe), quote_plus(value, safe=safe)):
            out |= {q, _HEX.sub(lambda m: m.group(0).lower(), q)}
    for j in (json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1]):
        for k in (j, _U_HEX.sub(lambda m: "\\u" + m.group(1).upper(), j)):
            out |= {k, k.replace("/", "\\/")}
    out.add(repr(value)[1:-1])
    out.discard(value)
    return {v for v in out if v}


def basic_forms(user: str, password: str) -> set[str]:
    """HTTP Basic's base64 of "user:password" - standard and URL-safe,
    padded and not, from UTF-8 and (as requests sends it) Latin-1."""
    out: set[str] = set()
    text = f"{user}:{password}"
    raws = {text.encode("utf-8")}
    try:
        raws.add(text.encode("latin-1"))
    except UnicodeEncodeError:
        pass
    for raw in raws:
        for enc in (base64.b64encode, base64.urlsafe_b64encode):
            b64 = enc(raw).decode("ascii")
            out |= {b64, b64.rstrip("=")}
    return out


#: A run that may be base64 (either alphabet, padded or not), long enough
#: to be worth reading; past the longest it is read in parts.
_B64_RUN = re.compile(r"[A-Za-z0-9+/_-]{8,2048}={0,2}")
_URLSAFE = str.maketrans("-_", "+/")


def _b64_text(run: str) -> str | None:
    """What *run* reads as when it is base64 of printable text, else None."""
    body = run.rstrip("=").translate(_URLSAFE)
    if len(body) % 4 == 1:
        return None
    try:
        text = base64.b64decode(body + "=" * (-len(body) % 4), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return text if text and text.isprintable() else None


def base64_masked(text: str, holds: Callable[[str], bool], mark: str) -> str:
    """*text* with every run that is base64 of text holding one of the
    person's values (*holds*) put as *mark* - HTTP Basic's user:password
    above all, padded or not, wherever it stands. A run read as one, else
    each of its "/" parts; what reads as no printable text is left alone,
    so ordinary words stay as they are."""
    def one(m: re.Match[str]) -> str:
        run = m.group(0)
        said = _b64_text(run)
        if said is not None and holds(said):
            return mark
        if "/" not in run:
            return run
        return "/".join(mark if len(part) >= 8 and (s := _b64_text(part)) is not None and holds(s)
                        else part for part in run.split("/"))
    return _B64_RUN.sub(one, text)


def _short_pattern(s: str) -> str:
    return r"(?<![0-9A-Za-z])" + re.escape(s) + r"(?![0-9A-Za-z])"


def _holds_secret(text: str) -> bool:
    """Whether *text* holds a registered value (a short one as a word)."""
    return any(s in text for s in tuple(_secrets)) or any(
        s in text and re.search(_short_pattern(s), text) for s in tuple(_short))


def register_secrets(*values: str | None) -> None:
    """Remember credential values so they never reach a log or a log file.

    Masking by VALUE rather than by position catches every shape the same
    credential arrives in - a path segment, a query parameter, a requests
    exception message quoting the full URL - without a parser that has to
    keep up with each new one. Each is masked in its encoded forms too
    (encoded_forms), and values registered together - a user name and its
    password - as HTTP Basic's base64 of the two (basic_forms)."""
    given = [v for v in values if v and v.strip()]
    for v in given:
        # One of three characters or fewer is masked only where it stands
        # as a word of its own (_short): as a substring it would blank out
        # ordinary words all over the log. Its encoded forms with it.
        (_secrets if len(v) > 3 else _short).update({v, *encoded_forms(v)})
    for a, b in permutations(given[:4], 2):
        _secrets.update(basic_forms(a, b))


def redact(text: str) -> str:
    """Mask every registered credential value in *text*.

    Iterates a SNAPSHOT: a client is built on a worker thread (the account
    panel fetches through run_async), so register_secrets can add to the
    set while another thread is formatting a log line, and iterating a set
    that grows under you raises RuntimeError.

    A value inside a longer word takes the whole word with it: "st***d"
    told where a login of "arte" began and what it was. Base64 that reads
    as text holding a value goes whole (base64_masked)."""
    for s in tuple(_secrets):
        if s in text:
            text = re.sub(r"\w*" + re.escape(s) + r"\w*", "***", text)
    for s in tuple(_short):
        if s in text:
            text = re.sub(_short_pattern(s), "***", text)
    if _secrets or _short:
        text = base64_masked(text, _holds_secret, "***")
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


def _query_masked(m: re.Match[str]) -> str:
    query = m.group(0)
    end = len(query)
    while end > 1 and query[end - 1] in ".,;:!?":
        end -= 1
    query, tail = query[1:end], query[end:]
    return "?" + "&".join(_masked_pair(p) for p in query.split("&")) + tail


def _value_masked(m: re.Match[str]) -> str:
    value = m.group(3)
    quote = value[0] if value[:1] in ("'", '"') else ""
    return f"{m.group(1)}{m.group(2)}{quote}***{quote}"


def redact_text(text: str) -> str:
    """*text* the app did not write - a line ffmpeg or mpv printed, an
    exception's message - safe to log: every address in it masked
    (_address_masked), a query or an Xtream account without its address,
    a login header, a bearer token, a login written name=value and a box's
    MAC, then every registered value. Never raises - and fails closed: a
    fault in any step gives WITHHELD, never the text half-masked."""
    try:
        if "://" in text:
            text = URL_IN_TEXT.sub(lambda m: _address_masked(m.group(0)), text)
        if "?" in text:
            text = _QUERY_IN_TEXT.sub(_query_masked, text)
        if "/" in text:
            text = _PATH_IN_TEXT.sub(lambda m: f"/{m.group(1)}/***/***/", text)
        text = AUTH_LINE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)
        text = BEARER.sub(lambda m: f"{m.group(1)} ***", text)
        text = MAC.sub("***", text)
        text = KEYVAL.sub(_value_masked, text)
        return redact(text)
    except Exception:
        return WITHHELD


def _escaped(text: str) -> str:
    """*text* with its control characters shown as \\x1b, not acted on."""
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group(0)):02x}", text)


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


#: What is written in place of a log line, or its traceback, that could
#: not be masked (_RedactFilter).
WITHHELD = "log line withheld - it could not be masked"


class _RedactFilter(logging.Filter):
    """The safety net: every record masked, whether or not the call site
    remembered to - the addresses in it (an ffmpeg or mpv line quoting a
    stream's address with its token or login, redact_text) and the
    registered values, in its message, its arguments and the traceback it
    carries (exc_info). It fails closed: what could not be masked - a
    message whose arguments do not fit it, a value whose str() raises, a
    fault in the masking itself - is never written as it was, only a line
    that says one was withheld (WITHHELD), with the logger and the level
    still there; the record is kept, so a log still shows that something
    happened."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if record.args:
                # Formatted first, then masked whole: an address can be
                # split between the message and an argument, and masking
                # the message alone could eat a %s an argument needs.
                text = record.getMessage()
                record.msg, record.args = _escaped(redact_text(text)), ()
            else:
                msg = _redact_arg(record.msg)
                if not isinstance(msg, str):
                    text = str(msg)
                    msg = msg if _escaped(text) == text else text
                record.msg = _escaped(msg) if isinstance(msg, str) else msg
        except Exception as e:
            record.msg, record.args = f"{WITHHELD} ({type(e).__name__})", ()
        try:
            # Formatted here, once, masked: every handler's formatter uses
            # exc_text when it is there instead of formatting it again.
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = _escaped(redact_text(record.exc_text))
            # The exception itself goes: a handler that read it would see
            # what the masked text left out. Formatters print exc_text.
            record.exc_info = None
            if record.stack_info:
                record.stack_info = _escaped(redact_text(record.stack_info))
        except Exception as e:
            # Never the traceback as it was: a handler would format exc_info
            # again, unmasked.
            record.exc_info = None
            record.exc_text = f"{WITHHELD} - its traceback ({type(e).__name__})"
            record.stack_info = None
        return True


_file_handler: logging.Handler | None = None


def private_folder(path: str) -> None:
    """*path* made, or narrowed, to this user alone (0700) - a folder of
    the app's own: its logs, a rewind buffer's. Never raises."""
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        if os.name == "posix":
            now = os.stat(path).st_mode & 0o777
            if now & ~0o700:
                os.chmod(path, now & 0o700)
    except OSError:
        pass


def _narrowed(fd: int) -> None:
    if os.name == "posix":
        now = os.fstat(fd).st_mode & 0o777
        if now & ~0o600:
            os.fchmod(fd, now & 0o600)


def private_fd(path: str, append: bool = False) -> int:
    """A file opened for writing that this user alone reads (0600): made
    so, or narrowed when it was there before - a log is pasted into a bug
    report, not read by every account on the computer."""
    flags = (os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
             | getattr(os, "O_BINARY", 0))
    fd = os.open(path, flags, 0o600)
    try:
        _narrowed(fd)
    except OSError:
        pass
    return fd


def open_private(path: str, mode: str = "w", encoding: str | None = "utf-8",
                 errors: str | None = None) -> Any:
    """open() of a file this user alone reads (private_fd)."""
    fd = private_fd(path, append="a" in mode)
    try:
        return open(fd, mode, encoding=None if "b" in mode else encoding,  # noqa: SIM115
                    errors=errors)
    except Exception:
        os.close(fd)
        raise


def _private_handler(path: str) -> logging.Handler:
    """A rotating log file (2 MB, three files at most) that this user alone
    reads, its rolled-over copies too."""
    from logging.handlers import RotatingFileHandler

    class PrivateFileHandler(RotatingFileHandler):
        def _open(self) -> Any:
            return open_private(self.baseFilename, self.mode,
                                encoding=self.encoding, errors=self.errors)

        def doRollover(self) -> None:
            super().doRollover()
            if os.name != "posix":
                return
            for n in range(1, self.backupCount + 1):
                try:
                    name = f"{self.baseFilename}.{n}"
                    now = os.stat(name).st_mode & 0o777
                    if now & ~0o600:
                        os.chmod(name, now & 0o600)
                except OSError:
                    pass

    return PrivateFileHandler(path, maxBytes=2_000_000, backupCount=2, encoding="utf-8")


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
        private_folder(log_dir())
        fh = _private_handler(log_file())
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
            fh = _private_handler(path)
            fh.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(message)s"))
            log.addHandler(fh)
        except Exception:
            pass   # a bad log path must never stop the app from starting
