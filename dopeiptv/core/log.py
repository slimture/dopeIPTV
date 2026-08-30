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

import logging
import os
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
_CRED_PARAMS = frozenset((
    "username", "user", "password", "pass", "pwd", "token", "auth",
    "api_key", "apikey", "key", "secret", "sig", "signature"))

# Xtream path layouts: /<kind>/<username>/<password>/<id>.<ext>. The bare
# /<username>/<password>/<id> form is deliberately NOT guessed at - it is
# indistinguishable from an ordinary three-segment URL, and the account's
# own values are masked by register_secrets() anyway.
_CRED_PREFIXES = frozenset(("live", "movie", "series", "timeshift"))


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


class _RedactFilter(logging.Filter):
    """The safety net: mask registered values in every record, whether or
    not the call site remembered to. Failure here must lose nothing, so any
    error leaves the record exactly as it was."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not _secrets:
            return True
        try:
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
            if isinstance(record.args, tuple):
                # Only str arguments are touched, so %d and friends keep the
                # types their format specifiers need.
                record.args = tuple(
                    redact(a) if isinstance(a, str) else a
                    for a in record.args)
        except Exception:
            pass
        return True


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
