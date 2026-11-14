"""A small HTTP server in the app (Nerd > Remote and sharing): the remote
API - what is playing, play, pause, volume, the next channel - and, when
sharing is on, the user's channels as one playlist and one guide for other
players on the network.

Off unless the user turns it on. It listens on this computer only unless
they choose the home network, and every request carries the token
(``Authorization: Bearer``, ``X-Dopeiptv-Token`` or ``?token=``), so a web
page in a browser cannot drive the player and a stranger on the network
gets nothing. Reading is GET; anything that changes something is POST.

The server knows nothing about the window: *call(action, params)* does the
work (the window hands it to its own thread), and *share* - an object with
``m3u(base, token)``, ``stream_url(source_id, stream_id)`` and
``guide(gz)`` - answers the shared lists.
"""

from __future__ import annotations

import hmac
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, quote, urlsplit

from .log import log
from .nerd_check import FAIL, OK, WARN, Check

DEFAULT_PORT = 3673            # D-O-P-E on a phone's keypad
MAX_BODY = 64 * 1024
API_VERSION = 1

# What the remote API answers: (method, path) -> the window's action.
READ = {"/api/status": "status", "/api/channels": "channels", "/api/sources": "sources"}
WRITE = {"/api/play": "play", "/api/pause": "pause", "/api/resume": "resume",
         "/api/toggle": "playpause", "/api/stop": "stop", "/api/next": "next",
         "/api/previous": "previous", "/api/volume": "volume", "/api/seek": "seek",
         "/api/raise": "raise"}
SHARE = ("/playlist.m3u", "/guide.xml", "/guide.xml.gz")


def new_token() -> str:
    return secrets.token_urlsafe(24)


def lan_addresses() -> list[str]:
    """This machine's IPv4 address on the network it would reach the
    internet through (nothing is sent: a UDP socket is only pointed)."""
    out: list[str] = []
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        ip = s.getsockname()[0]
        if ip and not ip.startswith("127."):
            out.append(ip)
    except OSError:
        pass
    finally:
        s.close()
    return out


def _host_ok(host: str) -> bool:
    """The Host a client asked for, used to write its playlist's addresses:
    a name, an IPv4 or a bracketed IPv6 address and a port, nothing else."""
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-:[]")
    return 0 < len(host) <= 255 and all(c in allowed for c in host)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError, TimeoutError,
                            ConnectionAbortedError)):
            return
        log.info("remote: a request failed (%s)", exc)


class RemoteServer:
    """The server; *remote* and *share* say which half answers."""

    def __init__(self, host: str, port: int, token: str,
                 call: Callable[[str, dict], Any], share: Any = None,
                 remote: bool = True) -> None:
        self.host, self.port, self.token = host, int(port), token
        self.call, self.share, self.remote = call, share, remote
        self._httpd: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._httpd is not None

    def start(self) -> None:
        """Listen; OSError when the port is taken (the caller says so)."""
        handler = _make_handler(self)
        httpd = _Server((self.host, self.port), handler)
        self.port = httpd.server_address[1]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="dopeiptv-remote",
                                        daemon=True)
        self._thread.start()
        log.info("remote: listening on %s:%d (%s%s)", self.host, self.port,
                 "remote control" if self.remote else "",
                 (" + " if self.remote else "") + "sharing" if self.share is not None else "")

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        log.info("remote: stopped")

    def authorized(self, given: str) -> bool:
        return bool(self.token) and hmac.compare_digest(given.encode("utf-8", "replace"),
                                                        self.token.encode("utf-8"))


def _make_handler(server: RemoteServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "dopeIPTV"
        sys_version = ""

        def log_message(self, fmt, *args):     # noqa: A003 - stdlib hook
            pass

        # -- answers -----------------------------------------------------------
        def _send(self, code: int, body: bytes, ctype: str,
                  extra: dict[str, str] | None = None) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code: int, obj: Any) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _stream(self, ctype: str, chunks: Iterable[bytes]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command == "HEAD":
                return
            for chunk in chunks:
                if chunk:
                    self.wfile.write(chunk)

        # -- the request ---------------------------------------------------------
        def _given_token(self, query: dict[str, list[str]]) -> str:
            auth = self.headers.get("Authorization") or ""
            if auth[:7].lower() == "bearer ":
                return auth[7:].strip()
            return (self.headers.get("X-Dopeiptv-Token") or "").strip() or \
                (query.get("token") or [""])[0]

        def _params(self, query: dict[str, list[str]]) -> dict[str, Any] | None:
            params: dict[str, Any] = {k: v[0] for k, v in query.items() if k != "token" and v}
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return None
            if length < 0 or length > MAX_BODY:
                return None
            if length:
                raw = self.rfile.read(length)
                ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                if ctype == "application/x-www-form-urlencoded":
                    params.update({k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items() if v})
                elif raw.strip():
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        return None
                    if not isinstance(body, dict):
                        return None
                    params.update(body)
            return params

        def _handle(self, method: str) -> None:
            parts = urlsplit(self.path)
            path = parts.path.rstrip("/") or "/"
            query = parse_qs(parts.query, keep_blank_values=False)
            if not server.authorized(self._given_token(query)):
                # A wrong token is slowed a little; the right one is 32
                # random characters, so this is manners, not the lock.
                time.sleep(0.25)
                self._json(401, {"error": "unauthorized", "hint": "the token is in Settings > Nerd"})
                return
            if (path.startswith("/stream/") or path in SHARE) and method in ("GET", "HEAD"):
                try:
                    if path in SHARE:
                        self._share(path)
                    else:
                        self._redirect_stream(path)
                except TimeoutError:
                    self._json(503, {"error": "the app is busy - try again"})
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as e:
                    log.warning("remote: %s failed: %s", path.split("/")[1], e)
                    self._json(502, {"error": type(e).__name__})
                return
            if path == "/" and method in ("GET", "HEAD"):
                self._json(200, {"app": "dopeIPTV", "api": API_VERSION,
                                 "remote": server.remote, "sharing": server.share is not None})
                return
            if not server.remote or not (path in READ or path in WRITE):
                self._json(404, {"error": "not found"})
                return
            if (path in READ) != (method in ("GET", "HEAD")):
                self._json(405, {"error": "use GET to read and POST to change something"})
                return
            params = self._params(query)
            if params is None:
                self._json(400, {"error": "the body is not a JSON object"})
                return
            action = READ.get(path) or WRITE[path]
            try:
                result = server.call(action, params)
            except TimeoutError:
                self._json(503, {"error": "the app is busy - try again"})
                return
            except ValueError as e:
                self._json(400, {"error": str(e)})
                return
            except Exception as e:
                log.warning("remote: %s failed: %s", action, e)
                self._json(500, {"error": type(e).__name__})
                return
            self._json(200, result if isinstance(result, dict) else {"ok": True, "result": result})

        def _share(self, path: str) -> None:
            share = server.share
            if share is None:
                self._json(404, {"error": "sharing is off"})
                return
            if path == "/playlist.m3u":
                host = self.headers.get("Host") or f"127.0.0.1:{server.port}"
                if not _host_ok(host):
                    host = f"127.0.0.1:{server.port}"
                text = share.m3u(f"http://{host}", server.token)
                self._send(200, text.encode("utf-8"), "audio/x-mpegurl; charset=utf-8")
                return
            gz = path.endswith(".gz")
            self._stream("application/gzip" if gz else "application/xml; charset=utf-8",
                         share.guide(gz))

        def _redirect_stream(self, path: str) -> None:
            share = server.share
            bits = path.split("/")
            if share is None or len(bits) != 4:
                self._json(404, {"error": "not found"})
                return
            from urllib.parse import unquote
            url = share.stream_url(unquote(bits[2]), unquote(bits[3]))
            if not url:
                self._json(404, {"error": "no such channel"})
                return
            self._send(302, b"", "text/plain", {"Location": url})

        def do_GET(self):      # noqa: N802 - stdlib hook
            self._handle("GET")

        def do_HEAD(self):     # noqa: N802
            self._handle("HEAD")

        def do_POST(self):     # noqa: N802
            self._handle("POST")

        def do_PUT(self):      # noqa: N802
            self._json(405, {"error": "use GET or POST"})

        do_DELETE = do_PUT
        do_PATCH = do_PUT

    return Handler


def stream_path(source_id: str, stream_id: Any) -> str:
    return f"/stream/{quote(str(source_id), safe='')}/{quote(str(stream_id), safe='')}"


# -- the Nerd page's line -----------------------------------------------------------

def check_server(host: str, port: int, token: str, remote: bool, sharing: bool,
                 running: bool, error: str = "") -> Check:
    """Ask the server as a client would: does it answer, with this token,
    and at which addresses."""
    if not (remote or sharing):
        return Check(WARN, "nerd_ck_remote_off", {})
    if not running:
        return Check(FAIL, "nerd_ck_remote_down", {"port": port}, error)
    local = f"http://127.0.0.1:{port}"
    lines: list[str] = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        req = urllib.request.Request(local + "/", headers={"Authorization": f"Bearer {token}"})
        with opener.open(req, timeout=5) as r:
            info = json.loads(r.read() or b"{}")
        lines.append(f"GET {local}/  →  {json.dumps(info)}")
    except Exception as e:
        return Check(FAIL, "nerd_ck_remote_noanswer", {"port": port}, f"GET {local}/  →  {e}")
    try:
        bad = urllib.request.Request(local + "/", headers={"Authorization": "Bearer wrong"})
        opener.open(bad, timeout=5)
        return Check(FAIL, "nerd_ck_remote_open", {}, "\n".join(lines + ["A wrong token was let in."]))
    except urllib.error.HTTPError as e:
        lines.append(f"A wrong token  →  {e.code} (refused, as it should be)")
    except Exception as e:
        lines.append(f"A wrong token  →  {e}")
    addresses = [f"http://127.0.0.1:{port}"]
    if host in ("0.0.0.0", "::"):
        addresses += [f"http://{ip}:{port}" for ip in lan_addresses()]
    lines.append("")
    lines.append("Addresses:")
    lines += [f"  {a}" for a in addresses]
    if remote:
        lines.append("")
        lines.append("Remote control, for example:")
        lines.append(f'  curl -H "Authorization: Bearer <token>" {addresses[-1]}/api/status')
        lines.append(f'  curl -X POST -H "Authorization: Bearer <token>" {addresses[-1]}/api/toggle')
        lines.append(f'  curl -X POST -H "Authorization: Bearer <token>" -d \'{{"channel": "SVT1"}}\' '
                     f"{addresses[-1]}/api/play")
        lines.append(f'  curl -X POST -H "Authorization: Bearer <token>" -d \'{{"number": 12}}\' '
                     f"{addresses[-1]}/api/play")
    if sharing:
        lines.append("")
        lines.append("For another player:")
        lines.append(f"  {addresses[-1]}/playlist.m3u?token=<token>")
        lines.append(f"  {addresses[-1]}/guide.xml.gz?token=<token>")
    where = addresses[-1].split("//", 1)[1]
    return Check(OK, "nerd_ck_remote_ok", {"where": where}, "\n".join(lines))

