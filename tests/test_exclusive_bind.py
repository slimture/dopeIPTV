from __future__ import annotations

import socket

from dopeiptv.core import exclusive_bind


class _Base:
    allow_reuse_address = True

    def __init__(self):
        self.socket = self
        self.opts = []
        self.bound_with_reuse = None

    def setsockopt(self, level, opt, value):
        self.opts.append(opt)

    def server_bind(self):
        self.bound_with_reuse = self.allow_reuse_address


class _Server(exclusive_bind.ExclusiveBindMixin, _Base):
    pass


def test_on_windows_a_taken_port_is_never_shared(monkeypatch):
    monkeypatch.setattr(exclusive_bind.sys, "platform", "win32")
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", 0x7FFF, raising=False)
    s = _Server()
    s.server_bind()
    assert s.bound_with_reuse is False and 0x7FFF in s.opts


def test_elsewhere_nothing_changes(monkeypatch):
    monkeypatch.setattr(exclusive_bind.sys, "platform", "linux")
    s = _Server()
    s.server_bind()
    assert s.bound_with_reuse is True and not s.opts


def test_a_server_starts_without_looking_a_name_up(monkeypatch):
    """http.server asks socket.getfqdn() for the address it bound to, only
    to keep the name; for 0.0.0.0 on Windows that took seconds, and every
    cast waited for it (the bridge's grace after a stop was used up before
    the converter was even asked for: "started after only 0.0 s")."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from dopeiptv.providers.cast_bridge import CastBridge

    def lookup(*_a):
        raise AssertionError("a name lookup")
    monkeypatch.setattr(socket, "getfqdn", lookup)

    class Server(exclusive_bind.ExclusiveBindMixin, ThreadingHTTPServer):
        pass
    for platform in ("linux", "win32"):
        monkeypatch.setattr(exclusive_bind.sys, "platform", platform)
        srv = Server(("127.0.0.1", 0), BaseHTTPRequestHandler)
        try:
            port = srv.server_address[1]
            assert port > 0 and (srv.server_name, srv.server_port) == ("127.0.0.1", port)
        finally:
            srv.server_close()
    monkeypatch.setattr(exclusive_bind.sys, "platform", "linux")
    b = CastBridge()
    try:
        assert b.start("http://p/x.m3u8").endswith("/stream.mp4")      # on 0.0.0.0
    finally:
        b.stop()
