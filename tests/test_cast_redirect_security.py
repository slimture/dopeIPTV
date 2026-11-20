"""Security regressions for Chromecast redirect preparation."""

import socket
from urllib.parse import urljoin

from dopeiptv.providers import chromecast


class _Response:
    def __init__(self, location=None):
        self.headers = {"Location": location} if location else {"Content-Type": "video/mp4"}

    def close(self):
        pass

    def iter_content(self, size):
        yield b"video"


def test_public_source_cannot_redirect_to_local_service(monkeypatch):
    calls = []

    class Requests:
        compat = type("Compat", (), {"urljoin": staticmethod(urljoin)})

        @staticmethod
        def get(url, **kwargs):
            calls.append(url)
            return _Response("http://127.0.0.1:8080/admin")

    from dopeiptv.core import _lazy_requests
    monkeypatch.setattr(_lazy_requests, "requests", Requests)
    monkeypatch.setattr(chromecast.socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 80))
    ])
    original = "https://provider.example/live.m3u8"
    assert chromecast._resolve_redirects(original) == (original, None)
    assert calls == [original]


def test_public_source_cannot_redirect_to_dns_private_address(monkeypatch):
    def dns(host, port, **kwargs):
        ip = "192.168.1.10" if host == "internal.example" else "93.184.215.14"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(chromecast.socket, "getaddrinfo", dns)
    assert not chromecast._safe_cast_probe_url("http://internal.example/private")
    assert chromecast._safe_cast_probe_url("https://provider.example/stream")


def test_local_lan_source_remains_supported(monkeypatch):
    calls = []

    class Requests:
        compat = type("Compat", (), {"urljoin": staticmethod(urljoin)})

        @staticmethod
        def get(url, **kwargs):
            calls.append(url)
            return _Response("http://192.168.1.51/video") if len(calls) == 1 else _Response()

    from dopeiptv.core import _lazy_requests
    monkeypatch.setattr(_lazy_requests, "requests", Requests)
    assert chromecast._resolve_redirects("http://192.168.1.50/start") == (
        "http://192.168.1.51/video", "video/mp4")
    assert len(calls) == 2


def test_non_http_redirects_are_rejected():
    assert not chromecast._safe_cast_probe_url("file:///etc/passwd")
    assert not chromecast._safe_cast_probe_url("http://127.0.0.1/")
    assert not chromecast._safe_cast_probe_url("http://169.254.169.254/")
