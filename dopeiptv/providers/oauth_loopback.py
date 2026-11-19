"""One-shot loopback HTTP server that captures an OAuth redirect.

The browser (authorization-code) sign-in sends the user to the provider's
site and is redirected back to ``http://127.0.0.1:<port>/callback?code=...``.
This module runs a tiny local server that waits for exactly that request,
hands the query parameters back to the caller and shows the user a plain
"you can close this tab" page. No Qt, no third-party deps - safe to run on a
worker thread.
"""

from __future__ import annotations

import hmac
import html
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from ..i18n import tr
from ..core.exclusive_bind import ExclusiveBindMixin

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>dopeIPTV</title></head>
<body style="font-family:sans-serif;background:#17171C;color:#C9C9D2;
text-align:center;padding-top:16vh;margin:0">
<h2 style="color:#ed1c24">dopeIPTV</h2>
<p style="font-size:16px">{msg}</p>
<p style="color:#6b6b76;font-size:13px">{close}</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    """The loopback OAuth redirect: the ?code / ?error query (with the
    state asked for, when one was) taken and a small done page answered.
    Any other request - a favicon, a page on this computer guessing, one
    with no code or the wrong state - is turned away and the wait goes
    on; a connection that says nothing is let go after *timeout*."""

    #: Seconds a connection may take to send its request: one that sends
    #: nothing would hold handle_request() - and with it Cancel and the
    #: time limit - for as long as it stayed open.
    timeout = 5.0

    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        if parsed.path not in ("/callback", "/"):
            self._answer(404, b"")
            return
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        if not self._taken(params, getattr(self.server, "oauth_state", None)):
            self._answer(400, b"")
            return
        self.server.oauth_result = params        # type: ignore[attr-defined]
        ok = "code" in params and "error" not in params
        msg = (tr("oauth_page_ok") if ok else tr("oauth_page_failed"))
        body = _PAGE.format(msg=html.escape(msg),
                            close=html.escape(tr("oauth_page_close"))
                            ).encode("utf-8")
        self._answer(200, body)

    @staticmethod
    def _taken(params: dict, want: str | None) -> bool:
        """A code, with the state asked for (in constant time); or an
        error - with that state too when it names one: a sign-in turned
        down at the provider is said so, however its answer is written."""
        if "code" not in params and "error" not in params:
            return False
        if want is None or ("code" not in params and "state" not in params):
            return True
        return hmac.compare_digest(params.get("state", "").encode("utf-8"),
                                   want.encode("utf-8"))

    def _answer(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The address held the sign-in's code: kept by nothing, read as
        # nothing but what it says it is, and sent on to no one.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def log_message(self, *_a) -> None:  # silence the default stderr logging
        pass


class _Server(ExclusiveBindMixin, HTTPServer):
    """The sign-in listener; its port never shared (core/exclusive_bind.py)."""

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client gone before its answer (or one that said nothing) is
        nothing to print a traceback for."""


def capture_oauth_redirect(
        port: int, timeout: float = 180.0,
        should_cancel: Callable[[], bool] | None = None,
        state: str | None = None) -> dict | None:
    """Serve one OAuth redirect on 127.0.0.1:*port* and return its query
    parameters as a dict (e.g. ``{"code": ..., "state": ...}``) - only one
    with a code or an error, and with *state* (compared in constant time)
    when one is given; anything else is turned away and the wait goes on.

    Returns None if the wait is cancelled or *timeout* seconds elapse first.
    Raises OSError if the port can't be bound (e.g. already in use).
    """
    httpd = _Server(("127.0.0.1", port), _Handler)
    httpd.oauth_result = None                    # type: ignore[attr-defined]
    httpd.oauth_state = state                    # type: ignore[attr-defined]
    httpd.timeout = 1.0                          # wake up once a second
    deadline = time.time() + timeout
    try:
        while httpd.oauth_result is None:        # type: ignore[attr-defined]
            if should_cancel is not None and should_cancel():
                return None
            if time.time() > deadline:
                return None
            # Blocks up to httpd.timeout (and a connection that says
            # nothing up to _Handler.timeout), then returns so we can
            # re-check the cancel flag / deadline above.
            httpd.handle_request()
        return httpd.oauth_result                # type: ignore[attr-defined]
    finally:
        httpd.server_close()
