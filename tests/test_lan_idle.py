"""The servers a TV reads from on the home network (the cast bridge, the
AirPlay bridge, the DLNA file share) let go of a client that holds a
connection open and asks nothing - and never of one that is reading a
stream slowly, as a paused TV does."""
from __future__ import annotations

import socket
import threading
import time

import pytest

from dopeiptv.providers import airplay_bridge, cast_bridge, dlna

IDLE = 0.4


@pytest.fixture(autouse=True)
def _short_idle(monkeypatch):
    for handler in (cast_bridge._Handler, airplay_bridge._Handler, dlna._FileHandler):
        monkeypatch.setattr(handler, "idle_timeout", IDLE, raising=False)


def _serving(server_cls, handler):
    srv = server_cls(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return srv


def _closed_within(port: int, send: bytes, secs: float) -> float | None:
    """Seconds until the server closed a connection that sent *send* and
    then nothing; None when it was still open after *secs*."""
    with socket.create_connection(("127.0.0.1", port), timeout=secs) as c:
        if send:
            c.sendall(send)
        t0 = time.monotonic()
        try:
            while c.recv(4096):
                pass
        except TimeoutError:
            return None
        return time.monotonic() - t0


@pytest.mark.parametrize("server_cls, handler", [
    (cast_bridge._Server, cast_bridge._Handler),
    (airplay_bridge._Server, airplay_bridge._Handler),
    (dlna._FileServer, dlna._FileHandler),
])
def test_a_client_that_asks_nothing_is_let_go(server_cls, handler):
    srv = _serving(server_cls, handler)
    try:
        port = srv.server_address[1]
        took = _closed_within(port, b"", 5.0)
        assert took is not None and took < IDLE + 1.5, took
        # Halfway through its request: the same.
        took = _closed_within(port, b"GET / HTTP/1.1\r\nHost: tv\r\n", 5.0)
        assert took is not None and took < IDLE + 1.5, took
    finally:
        srv.shutdown()
        srv.server_close()


def test_a_tv_reading_slowly_keeps_its_stream_and_an_idle_one_goes_after_it(tmp_path):
    """A paused TV stops reading for longer than the idle limit: the file
    keeps coming once it reads again. Then, kept alive and asking nothing
    more, the connection is closed."""
    body = bytes(range(256)) * (8 * 4096)            # 8 MB, more than any socket holds
    path = tmp_path / "film.mkv"
    path.write_bytes(body)
    share = dlna.FileShare()
    url = share.start(str(path))
    try:
        port = share._server.server_address[1]
        req = (f"GET {share._server.url_path} HTTP/1.1\r\nHost: tv\r\n\r\n").encode()
        with socket.socket() as c:
            c.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8192)
            c.settimeout(10)
            c.connect(("127.0.0.1", port))
            c.sendall(req)
            got = c.recv(65536)
            time.sleep(IDLE * 3)                      # paused, reading nothing
            head, _, data = got.partition(b"\r\n\r\n")
            assert b" 200 " in head.split(b"\r\n")[0], head
            data = bytearray(data)
            while len(data) < len(body):
                chunk = c.recv(1 << 20)
                assert chunk, f"cut off after {len(data)} of {len(body)} bytes"
                data += chunk
            assert bytes(data) == body
            # Kept alive, then idle: let go.
            t0 = time.monotonic()
            assert c.recv(4096) == b""
            assert time.monotonic() - t0 < IDLE + 1.5
        assert url.endswith(share._server.url_path)
    finally:
        share.stop()
