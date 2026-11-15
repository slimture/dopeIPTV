"""The phone remote's channel logos: a channel with a logo address says so
(not only one the app has on disk already), and a logo the image cache
does not have yet is fetched into it once - on the phone server's thread,
never the window's - then served from disk; a missing one, one that is no
picture or too big is left out and not asked for again this session. Only
web addresses and Nerd's picons, never another file on this computer."""
from __future__ import annotations

import http.client
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dopeiptv.core import phone_remote
from dopeiptv.core.phone_remote import PhoneServer

TOKEN = "Ab3_-x9Ab3_-x9Ab3_-x9Ab3_-x9Qz12"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


def _png() -> bytes:
    from PyQt6.QtCore import QBuffer
    from PyQt6.QtGui import QColor, QImage
    img = QImage(8, 8, QImage.Format.Format_ARGB32)
    img.fill(QColor(200, 40, 40))
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    assert img.save(buf, "PNG")
    return bytes(buf.data())


class _Logos(ThreadingHTTPServer):
    """A logo host on 127.0.0.1: what each path answers, and how often it
    was asked."""
    daemon_threads = True

    def __init__(self, files: dict[str, tuple[int, str, bytes]]) -> None:
        self.files, self.asked = files, {}
        self.lock = threading.Lock()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # noqa: N802 - stdlib hook
                srv = self.server
                with srv.lock:
                    srv.asked[self.path] = srv.asked.get(self.path, 0) + 1
                code, ctype, body = srv.files.get(self.path, (404, "text/plain", b"no"))
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        super().__init__(("127.0.0.1", 0), H)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}{path}"


class _NoBridge:
    def call(self, action, params):
        return None


def _window(tmp_path):
    from PyQt6.QtCore import QThreadPool
    from PyQt6.QtWidgets import QApplication

    from dopeiptv.core.workers import LogoLoader
    from dopeiptv.ui.mw_phone import _PhoneMixin
    app = QApplication.instance() or QApplication([])

    class W(_PhoneMixin):
        def __init__(self):
            self.logos = LogoLoader(QThreadPool(), cache_dir=tmp_path / "img", disk_budget=0)
            self._phone_bridge = _NoBridge()
            self._phone_icons = {}
            self.fetch_threads: list[bool] = []
            fetch = self.logos.fetch_file

            def watched(url, **kw):
                # Off the window's (the QApplication's) thread, always.
                self.fetch_threads.append(threading.current_thread() is threading.main_thread())
                return fetch(url, **kw)
            self.logos.fetch_file = watched

    return app, W()


def _get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    c.request("GET", path)
    r = c.getresponse()
    body = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), body


def test_a_logo_not_on_disk_is_fetched_once_off_the_window_thread_and_served(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    png = _png()
    big = b"\x89PNG\r\n\x1a\n" + b"\0" * phone_remote.LOGO_MAX
    host = _Logos({"/a.png": (200, "image/png", png),
                   "/page.png": (200, "text/html; charset=utf-8", b"<html>no</html>"),
                   "/big.png": (200, "image/png", big),
                   "/s3.png": (200, "binary/octet-stream", png),
                   "/logo.svg": (200, "image/svg+xml",
                                 b"<svg xmlns='http://www.w3.org/2000/svg' width='4' height='4'/>")})
    _app, w = _window(tmp_path)
    w._phone_icons = {"1": host.url("/a.png"), "2": host.url("/missing.png"),
                      "3": host.url("/page.png"), "4": host.url("/big.png"),
                      "5": host.url("/s3.png"), "6": host.url("/logo.svg")}
    srv = PhoneServer(0, TOKEN, w._phone_call, lambda: (b"", ""), host="127.0.0.1")
    srv.start()
    try:
        assert w.logos.disk_file(host.url("/a.png")) is None
        code, ctype, body = _get(srv.port, f"/{TOKEN}/logo/1")
        assert code == 200 and ctype == "image/png" and body == png
        assert w.logos.disk_file(host.url("/a.png")) is not None      # the lists' own cache
        code, _c, body = _get(srv.port, f"/{TOKEN}/logo/1")             # from disk now
        assert code == 200 and body == png and host.asked["/a.png"] == 1
        # A generic type some hosts send: kept only as a real picture.
        assert _get(srv.port, f"/{TOKEN}/logo/5")[0] == 200
        # Missing, not a picture, too big, a vector: left out - and not asked again.
        for ident, path in (("2", "/missing.png"), ("3", "/page.png"), ("4", "/big.png"),
                            ("6", "/logo.svg")):
            for _ in range(3):
                assert _get(srv.port, f"/{TOKEN}/logo/{ident}")[0] == 404, path
            assert host.asked[path] == 1, (path, host.asked)
            if path != "/logo.svg":
                assert w.logos.disk_file(host.url(path)) is None, path
        assert w.fetch_threads and not any(w.fetch_threads), w.fetch_threads
    finally:
        srv.stop()
        host.shutdown()
        host.server_close()


def test_a_dead_host_is_not_asked(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    host = _Logos({"/a.png": (200, "image/png", _png())})
    _app, w = _window(tmp_path)
    url = host.url("/a.png")
    w.logos.dead_hosts[w.logos._host_of(url)] = 10 ** 12
    try:
        assert w._phone_logo_file(url) is None and host.asked == {}
        assert w.logos.fetch_file(url) is None and host.asked == {}
    finally:
        host.shutdown()
        host.server_close()


def test_a_channel_with_a_logo_address_says_so_and_no_other_file_is_served(tmp_path):
    pytest.importorskip("PyQt6.QtWidgets")
    _app, w = _window(tmp_path)
    picon = tmp_path / "svt1.png"
    picon.write_bytes(_png())
    picons = frozenset({str(picon)})
    addr = w._phone_logo_address
    assert addr("https://logos.example/svt1.png", picons) == "https://logos.example/svt1.png"
    assert addr("HTTP://logos.example/a.png", picons) == "HTTP://logos.example/a.png"
    assert addr(str(picon), picons) == str(picon)
    assert addr("file://" + str(picon), picons) == "file://" + str(picon)
    for bad in ("/etc/passwd", "file:///home/u/.ssh/id_rsa", "ftp://x/y.png", "", None,
                str(tmp_path / "other.png")):
        assert addr(bad, picons) == "", bad
    # A picon the window passed is served as it is.
    assert w._phone_logo_file(str(picon)) == str(picon)
    assert w._phone_logo_file("") is None


def test_the_page_asks_for_list_logos_lazily_and_shows_initials_without_one():
    from dopeiptv.core import phone_page
    js = phone_page._JS
    row = js[js.index("function row(c)"):js.index("function more()")]
    assert 'img.loading = "lazy"' in row and "lg.append(img)" in row
    assert 'addEventListener("error", () => img.remove())' in row
    assert '.lg.pic span{visibility:hidden}' in phone_page._CSS
    assert '$("nowImg").addEventListener("error"' in js
