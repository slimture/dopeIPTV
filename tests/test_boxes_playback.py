"""The whole way from a box to the picture: each box client hands over a
play address and a real mpv core plays it from a fake box - Enigma2's
stream port with a login, its recordings through /file, Tvheadend behind a
digest login, an HDHomeRun lineup. Skipped where libmpv is missing (CI
installs it)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from dopeiptv.providers.enigma2 import Enigma2Client
from dopeiptv.providers.hdhomerun import HDHomeRunClient
from dopeiptv.providers.tvheadend import TvheadendClient
from tests.test_boxes import SVT1, openwebif_routes, tvh_routes
from tests.test_nerd import TS, Recorder, _libmpv, _plays


@pytest.fixture
def player():
    m = _libmpv()
    yield m
    m.terminate()


class Box:
    """A box's web interface (routes as in test_boxes) that also serves the
    test clip at any path under /stream/, /file and /auto/."""

    def __init__(self, routes, auth=None):
        self.rec = Recorder(auth)        # for its auth check only
        self.routes, self.paths = routes, []
        box = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlsplit(self.path)
                box.paths.append(self.path)
                if box.rec.auth and not box.rec.authorized(self.headers.get("Authorization", "")):
                    self.send_response(401)
                    kind = box.rec.auth[0]
                    self.send_header("WWW-Authenticate", 'Digest realm="tvheadend", nonce="abc", qop="auth"'
                                     if kind == "digest" else 'Basic realm="enigma2"')
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if u.path.startswith(("/stream/", "/file", "/auto/", "/dvrfile/")):
                    body, ctype = TS, "video/mp2t"
                else:
                    route = box.routes.get(u.path)
                    if route is None:
                        self.send_response(404)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    if callable(route):
                        from urllib.parse import parse_qs
                        route = route({k: v[0] for k, v in parse_qs(u.query).items()})
                    body = (json.dumps(route) if isinstance(route, (dict, list)) else str(route)).encode()
                    ctype = "application/json" if isinstance(route, (dict, list)) else "text/xml"
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.rec.close()


@pytest.fixture
def boxes():
    made = []

    def make(routes, auth=None):
        b = Box(routes, auth)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


def test_enigma2_channel_plays_from_the_stream_port_with_the_login(player, boxes):
    web = boxes(openwebif_routes(), auth=("basic", "root", "dream box"))
    streams = Recorder(auth=("basic", "root", "dream box"))
    try:
        c = Enigma2Client(web.base, "root", "dream box", {"stream_port": str(streams.port)})
        c.authenticate()
        sid = next(r["stream_id"] for r in c.live_streams() if r["name"] == "SVT1 HD")
        assert _plays(player, c.live_url(sid))
        assert streams.requests[-1]["path"] == "/" + SVT1
    finally:
        streams.close()


def test_enigma2_recording_plays_through_file(player, boxes):
    web = boxes(openwebif_routes())
    c = Enigma2Client(web.base)
    c.authenticate()
    film = c.vod_streams()[0]
    assert _plays(player, c.vod_url(film["stream_id"]))
    assert any(p.startswith("/file?action=download&file=/media/hdd/movie/") for p in web.paths)


def test_tvheadend_channel_and_recording_play_behind_digest(player, boxes):
    srv = boxes(tvh_routes(), auth=("digest", "kodi", "s3cret"))
    c = TvheadendClient(srv.base, "kodi", "s3cret", {"profile": "pass"})
    c.authenticate()
    ch = c.live_streams("Nyheter")[0]
    assert _plays(player, c.live_url(ch["stream_id"]))
    assert "/stream/channel/c1?profile=pass" in srv.paths
    rec = c.vod_streams()[0]
    assert _plays(player, c.vod_url(rec["stream_id"]))
    assert "/dvrfile/r1" in srv.paths


def test_hdhomerun_channel_plays(player, boxes):
    srv = boxes({})
    srv.routes.update({
        "/discover.json": {"FriendlyName": "HDHomeRun", "DeviceID": "1", "LineupURL": f"{srv.base}/lineup.json"},
        "/lineup.json": [{"GuideNumber": "2.1", "GuideName": "WCBS", "URL": f"{srv.base}/auto/v2.1"}]})
    c = HDHomeRunClient(srv.base)
    c.authenticate()
    assert _plays(player, c.live_url(c.live_streams()[0]["stream_id"]))
    assert "/auto/v2.1" in srv.paths
