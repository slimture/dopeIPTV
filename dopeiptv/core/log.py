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

log = logging.getLogger("dopeiptv")
_configured = False


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
