"""Nerd: test the connection to a source, step by step, each with its time.

    dns     the server's name looked up, and the addresses found (through a
            proxy: the proxy's - the proxy looks the server up itself)
    tcp     a connection to it on its port
    tls     for https, the handshake: the protocol, who issued the
            certificate and when it ends (plain http has nothing to check)
    login   Xtream's player_api.php - the HTTP status and, from its answer,
            when the account ends and how many of its connections are in
            use; a playlist downloaded; a portal's or a box's login
    stream  the first byte of a live channel: the one last played on the
            source, else the first in its list
    speed   that stream read for about SPEED_SECS, in Mbit/s, set against
            what SD, HD, Full HD and 4K need (NEEDS)

The source's own user agent, headers and proxy are used, as the app uses
them: the login asks through the source's client (providers/client.py's
make_client, handed in - core does not build clients), the stream as the
player asks. ``run`` runs off the window's thread; ``cancel`` stops it
between steps and in the middle of a read. Each step says what it found as
an i18n key and its values; ``copy_text`` is the whole of it scrubbed of
anything that identifies a person or a stream (core/report.py).
"""

from __future__ import annotations

import socket
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import unquote, urljoin, urlsplit

from . import mirrors, nerd
from .log import log

STEPS = ("dns", "tcp", "tls", "login", "stream", "speed")
#: What a stream needs, about (Mbit/s): the comparison the speed step makes.
NEEDS = (("sd", 3.0), ("hd", 6.0), ("fhd", 10.0), ("uhd", 25.0))
NEED_NAMES = {"sd": "SD", "hd": "HD", "fhd": "Full HD", "uhd": "4K"}
SPEED_SECS = 5.0
WAIT, RUN, OK, WARN, FAIL, SKIP = "wait", "run", "ok", "warn", "fail", "skip"
#: A certificate that ends sooner than this is worth a word.
CERT_WARN_DAYS = 14
CONNECT_SECS = 6.0
READ_SECS = 10.0


@dataclass
class Step:
    name: str
    state: str = WAIT
    ms: int | None = None
    key: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    #: Lines under the result: (i18n key, its values).
    more: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    #: What was asked and answered, for the copy (scrubbed there).
    detail: str = ""


class Cancelled(Exception):
    pass


class _Stop(Exception):
    """A step failed: the ones that need it are not tried."""


def _ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _err(e: BaseException) -> str:
    text = str(e) or type(e).__name__
    return text if len(text) <= 160 else text[:157] + "…"


def last_live(settings: Any, source_id: str) -> tuple[Any, str] | None:
    """(stream id, name) of the live channel last played on the source, as
    its history keeps it - None without one."""
    if settings is None or not source_id:
        return None
    import json
    try:
        rows = json.loads(settings.value(f"history_{source_id}", "") or "[]")
    except Exception:
        return None
    for e in rows if isinstance(rows, list) else []:
        if isinstance(e, dict) and e.get("_kind") == "live" and e.get("stream_id") is not None:
            return e["stream_id"], str(e.get("name") or "")
    return None


def speed_key(mbps: float) -> str:
    """The speed step's words for *mbps*: the best of NEEDS it is enough
    for."""
    met = [name for name, need in NEEDS if mbps >= need]
    return "ct_speed_" + (met[-1] if met else "low")


def met(mbps: float | None) -> list[bool]:
    """For each of NEEDS, whether *mbps* is enough."""
    return [mbps is not None and mbps >= need for _n, need in NEEDS]


class ConnTest:
    """One run of the test. *make_client* builds the source's client as the
    app does; *settings*: the app's (the source's history, its stream
    format and the backup address in use)."""

    def __init__(self, source: dict, make_client: Callable[[dict], Any] | None = None,
                 settings: Any = None, speed_secs: float = SPEED_SECS,
                 profile: nerd.Nerd | None = None, stream: bool = True) -> None:
        self.source = dict(source or {})
        # The stream and speed steps open a connection to the account: False
        # when the window was told not to (one more beside what plays).
        self.stream = stream
        self.kind = str(self.source.get("kind") or "xtream")
        self.opts = dict(self.source.get("options") or {}, _kind=self.kind)
        self.settings = settings
        self.speed_secs = speed_secs
        self._make_client = make_client
        self._nerd = profile or nerd.current()
        self.steps = [Step(n) for n in STEPS]
        self.mbps: float | None = None
        self.paced = True           # a live TS stream comes as fast as it plays
        self.address = ""
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._open: list[Any] = []  # what cancel() closes to end a wait
        self.finished = False
        self._client: Any = None

    # -- from the window's thread -----------------------------------------------

    def cancel(self) -> None:
        self._cancel.set()
        with self._lock:
            for thing in list(self._open):
                try:
                    thing.close()
                except Exception:
                    pass

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def snapshot(self) -> list[Step]:
        with self._lock:
            return [Step(s.name, s.state, s.ms, s.key, dict(s.params), list(s.more), s.detail)
                    for s in self.steps]

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="conn-test", daemon=True)
        t.start()
        return t

    # -- the run -----------------------------------------------------------------

    def _step(self, name: str) -> Step:
        return next(s for s in self.steps if s.name == name)

    def _set(self, name: str, state: str, key: str = "", params: dict | None = None,
             ms: int | None = None, detail: str = "",
             more: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        with self._lock:
            s = self._step(name)
            s.state, s.key, s.params, s.ms = state, key, dict(params or {}), ms
            s.detail = detail
            s.more = list(more or [])

    def _check(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def _hold(self, thing: Any) -> None:
        with self._lock:
            self._open.append(thing)
        if self._cancel.is_set():
            try:
                thing.close()
            except Exception:
                pass

    def _let_go(self, thing: Any) -> None:
        with self._lock:
            if thing in self._open:
                self._open.remove(thing)
        try:
            thing.close()
        except Exception:
            pass

    def run(self) -> None:
        try:
            self._run()
        except Cancelled:
            pass
        except Exception as e:      # a step nobody foresaw: said, not raised
            if not self._cancel.is_set():   # else: what closing it mid-read raised
                log.info("connection test: %s: %s", type(e).__name__, e)
                for s in self.steps:
                    if s.state in (WAIT, RUN):
                        self._set(s.name, FAIL, "ct_error", {"error": _err(e)})
                        break
        finally:
            with self._lock:
                for s in self.steps:
                    if s.state in (WAIT, RUN):
                        s.state = SKIP
                        s.key = "ct_skip_cancel" if self._cancel.is_set() else "ct_skip"
                        s.params = {}
                self.finished = True

    def _run(self) -> None:
        self._client = client = self._make_client(self.source) if self._make_client else None
        url = str(getattr(client, "server", "") or self.source.get("server") or "")
        self.address = url
        if "://" not in url or url.lower().startswith("file:"):
            for name in ("dns", "tcp", "tls"):
                self._set(name, SKIP, "ct_file")
            ok = True
        else:
            ok = self._network(url)
        self._check()
        if not ok:
            return
        try:
            self._login(client, url)
            self._check()
            if not self.stream:
                for name in ("stream", "speed"):
                    self._set(name, SKIP, "ct_skip_conn")
                return
            self._stream(client)
        except _Stop:
            return

    # -- dns, tcp, tls -----------------------------------------------------------

    def _proxy(self, url: str) -> str:
        return self._nerd.proxy(self.opts, url)

    def _network(self, url: str) -> bool:
        u = urlsplit(url)
        host = u.hostname or ""
        try:
            port = u.port or (443 if u.scheme == "https" else 80)
        except ValueError:
            port = 443 if u.scheme == "https" else 80
        px = self._proxy(url)
        via = urlsplit(px if "://" in px else "http://" + px) if px else None
        if via is not None and via.scheme.lower().startswith("socks"):
            for name in ("dns", "tcp", "tls"):
                self._set(name, SKIP, "ct_socks", {"proxy": via.hostname or ""})
            return True
        dhost = (via.hostname or "") if via is not None else host
        try:
            dport = (via.port or (443 if via.scheme == "https" else 8080)) if via is not None else port
        except ValueError:
            dport = 8080
        # -- dns
        self._set("dns", RUN)
        t0 = time.monotonic()
        family = {"ipv4": socket.AF_INET, "ipv6": socket.AF_INET6}.get(
            self._nerd.get("nerd_ip_family", "auto") if self._nerd.on else "auto", socket.AF_UNSPEC)
        try:
            infos = socket.getaddrinfo(dhost, dport, family, socket.SOCK_STREAM)
        except OSError as e:
            self._set("dns", FAIL, "ct_dns_fail", {"host": dhost, "error": _err(e)}, _ms(t0),
                      f"{dhost}: {type(e).__name__}: {e}")
            return False
        ms = _ms(t0)
        addrs: list[str] = []
        for info in infos:
            a = str(info[4][0])
            if a not in addrs:
                addrs.append(a)
        shown = ", ".join(addrs[:3]) + (f" (+{len(addrs) - 3})" if len(addrs) > 3 else "")
        self._set("dns", OK, "ct_dns_proxy" if via is not None else "ct_dns_ok",
                  {"host": dhost, "addrs": shown, "n": len(addrs)}, ms,
                  f"{dhost} -> {', '.join(addrs)}")
        self._check()
        # -- tcp
        self._set("tcp", RUN)
        sock = None
        last: OSError | None = None
        t0 = time.monotonic()
        tried = []
        for info in infos[:4]:
            fam, _t, _p, _c, addr = info
            tried.append(str(addr[0]))
            s = socket.socket(fam, socket.SOCK_STREAM)
            s.settimeout(CONNECT_SECS)
            self._hold(s)
            try:
                s.connect(addr)
                sock = s
                break
            except OSError as e:
                last = e
                self._let_go(s)
                self._check()
        ms = _ms(t0)
        if sock is None:
            err = last or OSError("no address")
            if isinstance(err, ConnectionRefusedError):
                key = "ct_tcp_refused"
            elif isinstance(err, (socket.timeout, TimeoutError)):
                key = "ct_tcp_timeout"
            else:
                key = "ct_tcp_fail"
            self._set("tcp", FAIL, key, {"port": dport, "s": int(CONNECT_SECS), "error": _err(err)}, ms,
                      f"{', '.join(tried)} port {dport}: {type(err).__name__}: {err}")
            return False
        peer = str(sock.getpeername()[0])
        self._set("tcp", OK, "ct_tcp_proxy" if via is not None else "ct_tcp_ok",
                  {"addr": peer, "port": dport}, ms, f"{peer} port {dport}: connected")
        self._check()
        # -- tls
        try:
            if u.scheme != "https":
                self._set("tls", SKIP, "ct_tls_plain")
                return True
            self._set("tls", RUN)
            t0 = time.monotonic()
            try:
                if via is not None:
                    self._tunnel(sock, via, host, port)
                ctx = ssl.create_default_context()
                tls = ctx.wrap_socket(sock, server_hostname=host)
                self._hold(tls)
                sock = tls
            except (OSError, ValueError) as e:
                self._set("tls", FAIL, "ct_tls_fail", {"error": _err(e)}, _ms(t0),
                          f"{host}:{port}: {type(e).__name__}: {e}")
                return False
            ms = _ms(t0)
            self._tls_said(tls, ms)
            return True
        finally:
            self._let_go(sock)

    def _tunnel(self, sock: socket.socket, via: Any, host: str, port: int) -> None:
        """Through an http proxy, as requests goes: CONNECT host:port."""
        import base64
        lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
        if via.username:
            login = f"{unquote(via.username)}:{unquote(via.password or '')}"
            lines.append("Proxy-Authorization: Basic " + base64.b64encode(login.encode()).decode())
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head and len(head) < 8192:
            chunk = sock.recv(1024)
            if not chunk:
                break
            head += chunk
        status = head.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = status.split()
        if len(parts) < 2 or parts[1] != "200":
            raise OSError(f"the proxy said: {status or 'nothing'}")

    def _tls_said(self, tls: ssl.SSLSocket, ms: int) -> None:
        proto = tls.version() or "TLS"
        cert: dict[str, Any] = dict(tls.getpeercert() or {})
        names: dict[str, str] = {}
        for rdn in cert.get("issuer") or ():
            for pair in rdn:
                if isinstance(pair, tuple) and len(pair) == 2:
                    names.setdefault(str(pair[0]), str(pair[1]))
        issuer = names.get("organizationName") or names.get("commonName") or ""
        end = cert.get("notAfter")
        date, days = "", None
        if end:
            try:
                ts = ssl.cert_time_to_seconds(str(end))
                date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
                days = int((ts - time.time()) // 86400)
            except ValueError:
                date = str(end)
        cipher = (tls.cipher() or ("", "", 0))[0]
        detail = f"{proto}, {cipher}; issuer {issuer or '?'}; valid until {date or '?'}"
        params = {"proto": proto, "issuer": issuer or "?", "date": date or "?", "days": days or 0}
        if days is not None and days < CERT_WARN_DAYS:
            self._set("tls", WARN, "ct_tls_ending", params, ms, detail)
        else:
            self._set("tls", OK, "ct_tls_ok", params, ms, detail)

    # -- the login -----------------------------------------------------------------

    def _login(self, client: Any, url: str) -> None:
        self._set("login", RUN)
        t0 = time.monotonic()
        if self.kind == "xtream" and client is not None:
            self._xtream_login(client, url, t0)
            return
        try:
            if client is None:
                raise RuntimeError("no client for this source")
            client.authenticate()
        except Exception as e:
            ms = _ms(t0)
            code = getattr(getattr(e, "response", None), "status_code", None)
            loc = getattr(e, "localized", None)
            said = loc() if callable(loc) else _err(e)
            if code:
                self._set("login", FAIL, "ct_login_http", {"code": code}, ms, f"HTTP {code}: {said}")
            else:
                self._set("login", FAIL, "ct_login_fail", {"error": said}, ms,
                          f"{type(e).__name__}: {said}")
            raise _Stop() from e
        ms = _ms(t0)
        if self.kind == "m3u":
            try:
                n = len(client.live_streams(None) or [])
            except Exception:
                n = 0
            self._set("login", OK, "ct_playlist_ok", {"n": n}, ms, f"{n} channels")
        elif self.kind == "stalker":
            self._set("login", OK, "ct_portal_ok", {}, ms, "handshake and profile: ok")
        else:
            self._set("login", OK, "ct_box_ok", {}, ms, "the box answered")

    def _xtream_login(self, client: Any, url: str, t0: float) -> None:
        import requests
        params = {"username": getattr(client, "username", ""), "password": getattr(client, "password", "")}
        try:
            r = client.session.get(f"{url}/player_api.php", params=params,
                                   timeout=(CONNECT_SECS, READ_SECS))
        except requests.Timeout as e:
            self._set("login", FAIL, "ct_login_timeout", {"s": int(READ_SECS)}, _ms(t0), _err(e))
            raise _Stop() from e
        except requests.RequestException as e:
            self._set("login", FAIL, "ct_login_fail", {"error": _err(e)}, _ms(t0),
                      f"{type(e).__name__}: {e}")
            raise _Stop() from e
        ms = _ms(t0)
        code = r.status_code
        head = f"HTTP {code} ({ms} ms, {len(r.content)} bytes, {r.headers.get('content-type', '?')})"
        if code in (401, 403):
            self._set("login", FAIL, "ct_login_denied", {"code": code}, ms, head)
            raise _Stop()
        if code >= 500:
            self._set("login", FAIL, "ct_login_server", {"code": code}, ms, head)
            raise _Stop()
        if code != 200:
            self._set("login", FAIL, "ct_login_http", {"code": code}, ms, head)
            raise _Stop()
        try:
            data = r.json()
        except ValueError as e:
            self._set("login", FAIL, "ct_login_odd", {"code": code}, ms, head + "; not JSON")
            raise _Stop() from e
        ui = (data or {}).get("user_info") if isinstance(data, dict) else None
        if not isinstance(ui, dict) or str(ui.get("auth", 0)) != "1":
            self._set("login", FAIL, "ct_login_wrong", {"code": code}, ms, head + "; auth refused")
            raise _Stop()
        self._set("login", OK, "ct_login_ok", {"code": code}, ms, head,
                  more=account_lines(ui))

    # -- the stream ------------------------------------------------------------------

    def _channel(self, client: Any) -> tuple[str, str] | None:
        """(address, name) of the channel to try."""
        fmt = "ts"
        if self.settings is not None:
            try:
                fmt = str(self.settings.value("stream_format", "ts") or "ts")
            except Exception:
                fmt = "ts"
        last = last_live(self.settings, str(self.source.get("id") or ""))
        if last is not None:
            try:
                url = client.live_url(last[0], fmt) or ""
            except Exception:
                url = ""
            if url:
                return url, last[1]
        rows = [r for r in (client.live_streams(None) or []) if r.get("stream_id") is not None]
        if not rows:
            return None
        row = rows[0]
        url = client.live_url(row["stream_id"], fmt) or ""
        return (url, str(row.get("name") or "")) if url else None

    def _stream_session(self, url: str) -> Any:
        import requests
        n = self._nerd
        session = requests.Session()
        session.headers["User-Agent"] = n.user_agent(self.opts)
        session.headers.update(n.headers(self.opts))
        px = n.proxy(self.opts, url) if n.flag("nerd_proxy_player", True) else ""
        if px:
            session.trust_env = False
            session.proxies.update({"http": px, "https": px})
        return session

    def _stream(self, client: Any) -> None:
        import requests
        self._set("stream", RUN)
        t0 = time.monotonic()
        try:
            found = self._channel(client) if client is not None else None
        except Exception as e:
            self._set("stream", FAIL, "ct_stream_list", {"error": _err(e)}, _ms(t0), _err(e))
            raise _Stop() from e
        self._check()
        if not found:
            self._set("stream", FAIL, "ct_stream_none")
            raise _Stop()
        url, name = found
        name = name or "?"
        session = self._stream_session(url)
        self._hold(session)
        try:
            t0 = time.monotonic()
            try:
                r = session.get(url, stream=True, timeout=(CONNECT_SECS, READ_SECS))
            except requests.Timeout as e:
                self._set("stream", FAIL, "ct_stream_timeout", {"channel": name, "s": int(READ_SECS)},
                          _ms(t0), _err(e))
                raise _Stop() from e
            except requests.RequestException as e:
                self._check()
                self._set("stream", FAIL, "ct_stream_fail", {"channel": name, "error": _err(e)},
                          _ms(t0), f"{type(e).__name__}: {e}")
                raise _Stop() from e
            self._hold(r)
            code = r.status_code
            if code >= 400:
                self._set("stream", FAIL, "ct_stream_http", {"channel": name, "code": code}, _ms(t0),
                          f"HTTP {code}")
                raise _Stop()
            chunks = r.iter_content(32768)
            try:
                first = next(chunks, b"")
            except requests.RequestException as e:
                self._check()
                self._set("stream", FAIL, "ct_stream_fail", {"channel": name, "error": _err(e)},
                          _ms(t0), _err(e))
                raise _Stop() from e
            ms = _ms(t0)
            if not first:
                self._set("stream", FAIL, "ct_stream_empty", {"channel": name}, ms, f"HTTP {code}, no data")
                raise _Stop()
            kind = str(r.headers.get("content-type") or "?")
            self._set("stream", OK, "ct_stream_ok", {"channel": name, "ms": ms}, ms,
                      f"HTTP {code}, {kind}")
            self._check()
            self._set("speed", RUN)
            if first.lstrip()[:7] == b"#EXTM3U":
                self.paced = False
                got, secs = self._hls(session, r.url or url, first, chunks)
            else:
                got, secs = self._read(first, chunks)
            self._speed_said(got, secs)
        finally:
            self._let_go(session)

    def _read(self, first: bytes, chunks: Any) -> tuple[int, float]:
        """A live stream read for speed_secs from its first byte: (bytes,
        seconds)."""
        t0 = time.monotonic()
        got = len(first)
        try:
            for chunk in chunks:
                if self._cancel.is_set():
                    raise Cancelled()
                got += len(chunk)
                if time.monotonic() - t0 >= self.speed_secs:
                    break
        except Cancelled:
            raise
        except Exception as e:      # the stream ended early: what came counts
            self._check()
            log.info("connection test: the stream ended after %d bytes (%s)", got,
                     type(e).__name__)
        return got, max(0.001, time.monotonic() - t0)

    def _hls(self, session: Any, url: str, first: bytes, chunks: Any) -> tuple[int, float]:
        """An adaptive stream: its playlist (the best variant's, from a
        master), then its segments one after another for speed_secs."""
        text = (first + b"".join(_take(chunks, 512 * 1024))).decode("utf-8", "replace")
        t_end = time.monotonic() + self.speed_secs
        variants = _variants(text, url)
        if variants:
            url = variants[0]
            text = session.get(url, timeout=(CONNECT_SECS, READ_SECS)).text
        segments = [urljoin(url, ln.strip()) for ln in text.splitlines()
                    if ln.strip() and not ln.startswith("#")]
        got, t0 = 0, time.monotonic()
        for seg in segments:
            if self._cancel.is_set():
                raise Cancelled()
            r = session.get(seg, stream=True, timeout=(CONNECT_SECS, READ_SECS))
            self._hold(r)
            try:
                for chunk in r.iter_content(65536):
                    if self._cancel.is_set():
                        raise Cancelled()
                    got += len(chunk)
                    if time.monotonic() >= t_end:
                        break
            finally:
                self._let_go(r)
            if time.monotonic() >= t_end:
                break
        return got, max(0.001, time.monotonic() - t0)

    def _speed_said(self, got: int, secs: float) -> None:
        if got <= 0:
            self._set("speed", FAIL, "ct_speed_none", {"secs": int(self.speed_secs)})
            return
        mbps = got * 8 / secs / 1e6
        self.mbps = mbps
        key = speed_key(mbps)
        state = OK if mbps >= NEEDS[1][1] else WARN
        detail = f"{got} bytes in {secs:.1f} s = {mbps:.1f} Mbit/s"
        self._set("speed", state, key, {"mbps": f"{mbps:.1f}"}, int(secs * 1000), detail,
                  more=[("ct_speed_paced", {})] if self.paced else [])


def _take(chunks: Any, limit: int) -> list[bytes]:
    out, n = [], 0
    for c in chunks:
        out.append(c)
        n += len(c)
        if n >= limit:
            break
    return out


def _variants(text: str, base: str) -> list[str]:
    """A master playlist's variants, the highest bandwidth first."""
    out: list[tuple[int, str]] = []
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.startswith("#EXT-X-STREAM-INF"):
            bw = 0
            for part in ln.split(":", 1)[-1].split(","):
                k, _s, v = part.partition("=")
                if k.strip().upper() == "BANDWIDTH":
                    try:
                        bw = int(v.strip())
                    except ValueError:
                        bw = 0
            nxt = next((x.strip() for x in lines[i + 1:] if x.strip() and not x.startswith("#")), "")
            if nxt:
                out.append((bw, urljoin(base, nxt)))
    return [u for _b, u in sorted(out, key=lambda t: -t[0])]


def account_lines(ui: dict) -> list[tuple[str, dict[str, Any]]]:
    """What an Xtream user_info says of the account, as lines: when it
    ends, and how many of its connections are in use."""
    from .connections import numbers
    out: list[tuple[str, dict[str, Any]]] = []
    exp = ui.get("exp_date")
    if exp in (None, "", "null", "0", 0):
        out.append(("ct_acct_open", {}))
    else:
        try:
            ts = int(float(str(exp)))
            date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            days = int((ts - time.time()) // 86400)
            out.append(("ct_acct_ends", {"date": date, "days": days}) if days >= 0
                       else ("ct_acct_ended", {"date": date}))
        except (TypeError, ValueError, OSError):
            pass
    limit, active = numbers(ui)
    if limit is not None and active is not None:
        out.append(("conn_in_use", {"n": active, "max": limit}))
    elif active is not None:
        out.append(("conn_in_use_only", {"n": active}))
    return out


def copy_text(lines: list[str], source: dict | None) -> str:
    """The test as text for a forum or a support mail: *lines* (said in the
    app's language) without the source's name, address, login or box, nor
    any address, IP or MAC address at all (core/report.py's scrub)."""
    from .report import personal_values, scrub
    s = dict(source or {})
    personal = personal_values([s])
    for a in mirrors.parse((s.get("options") or {}).get(mirrors.KEY), str(s.get("kind") or "xtream")):
        personal.append(a)
        host = urlsplit(a).hostname or ""
        if host:
            personal.append(host)
    return scrub("\n".join(lines), personal)
