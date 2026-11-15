"""The app's own requests on a source's route (core/net_route.py): a
requests adapter whose connections look the host up as the route says -
own DNS, IP version - and connect to that address while the hostname stays
what the request is about: the Host header, the TLS server name and the
certificate check are the name's, as without it. A pinned certificate
(core/cert_pin.py) is checked by its SHA-256 fingerprint on that host and
port only; everywhere else verification is what it always was.

Mounted on a provider client's session (providers/client.py make_client)
and used for the guide, logos and the rewind buffer's connection through
getter()/session_for(). An inactive route mounts nothing: the session is
exactly what it was. Through a proxy the proxy makes the connection (its
DNS, its IP version); a pin is still checked at the end of the tunnel.
"""

from __future__ import annotations

import socket
import threading
from typing import Any, Callable

from . import cert_pin, net_route
from .net_route import Route


def _classes(route: Route, resolve: bool) -> dict[str, type]:
    """Pool classes whose connections follow *route* (*resolve*: look names
    up as it says - not for a proxy's pools, whose connection is the
    proxy's)."""
    from urllib3.connection import HTTPConnection, HTTPSConnection
    from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool
    from urllib3.exceptions import ConnectTimeoutError, NewConnectionError

    def new_conn(conn: Any, base: Any) -> socket.socket:
        host = str(conn.host)
        if (not resolve or getattr(conn, "_tunnel_host", None) or getattr(conn, "proxy", None)
                or not route.changes(host, conn.port, "http")):
            return base._new_conn(conn)
        try:
            return net_route.open_connection(route, host, conn.port, conn.timeout,
                                             conn.source_address, conn.socket_options)
        except (socket.timeout, TimeoutError) as e:
            raise ConnectTimeoutError(conn, f"Connection to {host} timed out. "
                                            f"(connect timeout={conn.timeout})") from e
        except OSError as e:
            raise NewConnectionError(conn, f"Failed to establish a new connection: {e}") from e

    class Http(HTTPConnection):
        def _new_conn(self) -> socket.socket:
            return new_conn(self, HTTPConnection)

    class Https(HTTPSConnection):
        def _new_conn(self) -> socket.socket:
            return new_conn(self, HTTPSConnection)

        def connect(self) -> None:
            origin = str(getattr(self, "_tunnel_host", None) or self.host)
            port = int(getattr(self, "_tunnel_port", None) or self.port or 443)
            pinned = route.pin(origin, port)
            if pinned is None:
                super().connect()
                return
            # This one certificate, whoever signed it: the chain is not
            # asked (it is the one the system does not trust), the
            # fingerprint is - after the handshake, before a byte is sent.
            self.cert_reqs = "CERT_NONE"
            self.ca_certs = self.ca_cert_dir = self.ca_cert_data = None
            self.ssl_context = None
            self.assert_fingerprint = None
            self.assert_hostname = False
            super().connect()
            sock: Any = self.sock
            der = sock.getpeercert(binary_form=True) if sock is not None else None
            got = cert_pin.fingerprint(der or b"")
            if got != pinned:
                cert_pin.record_mismatch(origin, port, got)
                self.close()
                raise cert_pin.PinMismatch(origin, port, pinned, got)
            cert_pin.clear_mismatch(origin, port)
            self.is_verified = True

    class HttpPool(HTTPConnectionPool):
        ConnectionCls = Http

    class HttpsPool(HTTPSConnectionPool):
        ConnectionCls = Https

    return {"http": HttpPool, "https": HttpsPool}


def adapter(route: Route) -> Any:
    """A requests adapter for *route*."""
    from requests.adapters import HTTPAdapter

    class NetAdapter(HTTPAdapter):
        def __init__(self) -> None:
            self.route = route
            super().__init__()

        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = _classes(self.route, resolve=True)

        def proxy_manager_for(self, proxy: str, **kwargs: Any) -> Any:
            manager = super().proxy_manager_for(proxy, **kwargs)
            if not str(proxy).lower().startswith("socks") and not getattr(manager, "_net_route", False):
                manager.pool_classes_by_scheme = _classes(self.route, resolve=False)
                manager._net_route = True
            return manager

    return NetAdapter()


def mount(session: Any, route: Route) -> bool:
    """*session* on *route*: both schemes through its adapter. Nothing for
    an inactive route."""
    if not route.active:
        return False
    a = adapter(route)
    session.mount("http://", a)
    session.mount("https://", a)
    return True


def remount(session: Any, route: Route) -> None:
    """Settings changed: *session* on *route* from its next connection - or
    back on requests' own adapter when the route is gone."""
    if route.active:
        mount(session, route)
        return
    if type(session.get_adapter("https://x/")).__name__ == "NetAdapter":
        from requests.adapters import HTTPAdapter
        session.mount("http://", HTTPAdapter())
        session.mount("https://", HTTPAdapter())


_sessions: dict[Route, Any] = {}
_lock = threading.Lock()
MAX_SESSIONS = 8


def session_for(route: Route) -> Any:
    """A session of its own for *route* (kept, so connections are reused)."""
    import requests
    with _lock:
        s = _sessions.get(route)
        if s is None:
            if len(_sessions) >= MAX_SESSIONS:
                # Dropped, not closed: a request may still be under way on it.
                _sessions.pop(next(iter(_sessions)))
            s = _sessions[route] = requests.Session()
            mount(s, route)
        return s


def getter(route: Route | None) -> Callable[..., Any]:
    """requests.get as *route* says: requests.get itself while it changes
    nothing."""
    if route is None or not route.active:
        from ._lazy_requests import requests
        return requests.get
    return session_for(route).get
