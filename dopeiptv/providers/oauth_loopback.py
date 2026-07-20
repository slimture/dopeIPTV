"""One-shot loopback HTTP server that captures an OAuth redirect.

The browser (authorization-code) sign-in sends the user to the provider's
site and is redirected back to ``http://127.0.0.1:<port>/callback?code=...``.
This module runs a tiny local server that waits for exactly that request,
hands the query parameters back to the caller and shows the user a plain
"you can close this tab" page. No Qt, no third-party deps - safe to run on a
worker thread.
"""

from __future__ import annotations

import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>dopeIPTV</title></head>
<body style="font-family:sans-serif;background:#17171C;color:#C9C9D2;
text-align:center;padding-top:16vh;margin:0">
<h2 style="color:#ed1c24">dopeIPTV</h2>
<p style="font-size:16px">{msg}</p>
<p style="color:#6b6b76;font-size:13px">You can close this tab and return to
the app.</p></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    """Handles the single loopback OAuth redirect: captures the ?code / ?error query and returns a small self-closing done page."""
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        parsed = urlparse(self.path)
        # Ignore favicon and any stray requests - only the callback carries
        # the OAuth response.
        if parsed.path not in ("/callback", "/"):
            self.send_response(404)
            self.end_headers()
            return
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.server.oauth_result = params        # type: ignore[attr-defined]
        ok = "code" in params and "error" not in params
        msg = ("Connected to Trakt ✓" if ok
               else "Sign-in was cancelled or failed.")
        body = _PAGE.format(msg=msg).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_a) -> None:  # silence the default stderr logging
        pass


def capture_oauth_redirect(
        port: int, timeout: float = 180.0,
        should_cancel: Callable[[], bool] | None = None) -> dict | None:
    """Serve one OAuth redirect on 127.0.0.1:*port* and return its query
    parameters as a dict (e.g. ``{"code": ..., "state": ...}``).

    Returns None if the wait is cancelled or *timeout* seconds elapse first.
    Raises OSError if the port can't be bound (e.g. already in use).
    """
    httpd = HTTPServer(("127.0.0.1", port), _Handler)
    httpd.oauth_result = None                    # type: ignore[attr-defined]
    httpd.timeout = 1.0                          # wake up once a second
    deadline = time.time() + timeout
    try:
        while httpd.oauth_result is None:        # type: ignore[attr-defined]
            if should_cancel is not None and should_cancel():
                return None
            if time.time() > deadline:
                return None
            # Blocks up to httpd.timeout, then returns so we can re-check the
            # cancel flag / deadline above.
            httpd.handle_request()
        return httpd.oauth_result                # type: ignore[attr-defined]
    finally:
        httpd.server_close()
