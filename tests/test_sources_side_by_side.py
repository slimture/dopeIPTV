"""A box on the home network and an ordinary Xtream account, side by side
in the same app: each source gets its own client, its own channels and its
own Nerd options, and switching back and forth never carries one source's
settings over to the other - played for real in mpv, from fake servers."""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from dopeiptv.core import nerd
from dopeiptv.providers.client import XtreamClient, make_client
from dopeiptv.providers.enigma2 import Enigma2Client
from tests.test_boxes import SVT1, openwebif_routes
from tests.test_boxes_playback import Box
from tests.test_nerd import TS, Recorder, S, _libmpv, _plays
from tests.test_nerd_check import Server as ForwardingProxy


class Xtream:
    """An Xtream Codes panel: the API (login, categories, channels) and
    its /live/ streams; records what each request said."""

    def __init__(self, host="127.0.0.1"):
        self.requests: list[dict] = []
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                u = urlsplit(self.path)
                srv.requests.append({"path": u.path, "headers": {k.lower(): v for k, v in self.headers.items()}})
                q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/player_api.php":
                    if (q.get("username"), q.get("password")) != ("anna", "pw-anna"):
                        body = json.dumps({"user_info": {"auth": 0}}).encode()
                    else:
                        body = json.dumps(self._api(q.get("action", ""))).encode()
                    ctype = "application/json"
                elif u.path.startswith("/live/anna/pw-anna/"):
                    body, ctype = TS, "video/mp2t"
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _api(self, action):
                if action == "get_live_categories":
                    return [{"category_id": "1", "category_name": "Sverige"}]
                if action == "get_live_streams":
                    return [{"num": 1, "name": "TV4 HD", "stream_id": 401, "stream_icon": "",
                             "epg_channel_id": "tv4.se", "category_id": "1"},
                            {"num": 2, "name": "Kanal 5", "stream_id": 405, "stream_icon": "",
                             "epg_channel_id": "", "category_id": "1"}]
                return {"user_info": {"auth": 1, "status": "Active", "max_connections": "1"},
                        "server_info": {"url": "127.0.0.1"}}

        self.httpd = ThreadingHTTPServer((host, 0), H)
        self.base = f"http://{host}:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _own_address() -> str | None:
    """This machine's address on its network: a provider is never on the
    loopback, and the player never takes a proxy to the loopback (that is
    the rewind buffer's own server)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 9))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Nerd on with a VLC user agent and a proxy for everything; an Xtream
    account; an Enigma2 box whose own options say Enigma2 and no proxy."""
    # This machine's own proxy settings stay out of it; the fake servers are local.
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    host = _own_address()
    if not host or host.startswith("127."):
        pytest.skip("no network address on this machine")
    try:
        xt = Xtream(host)
    except OSError as e:
        pytest.skip(f"cannot serve on {host}: {e}")
    web = Box(openwebif_routes())
    stream_port = Recorder()
    proxy = ForwardingProxy(proxy=True)  # a real HTTP proxy: forwards and records
    s = S()
    s.setValue("nerd_enabled", "true")
    s.setValue("nerd_user_agent", "vlc")
    s.setValue("nerd_proxy", f"http://127.0.0.1:{proxy.port}")
    nerd.install(s)
    xtream_pl = {"id": "x1", "name": "Anna's IPTV", "kind": "xtream", "server": xt.base,
                 "username": "anna", "password": "pw-anna"}
    box_pl = {"id": "b1", "name": "Vu+ Duo", "kind": "enigma2", "server": web.base,
              "options": {"stream_port": str(stream_port.port), "user_agent": "enigma2"}}
    yield {"xt": xt, "web": web, "stream_port": stream_port, "proxy": proxy,
           "xtream": xtream_pl, "box": box_pl}
    nerd.install(None)
    nerd.set_source(None)
    for srv in (xt, web, stream_port, proxy):
        srv.close()


def _switch_to(pl):
    """What the app does on choosing a source: its Nerd options in force,
    its own client, logged in."""
    nerd.set_source(pl)
    client = make_client(pl)
    client.authenticate()
    return client


def _play(player, client, name, applied):
    row = next(r for r in client.live_streams(None) if r["name"] == name)
    url = client.live_url(row["stream_id"])
    nerd.apply_stream(player, nerd.source(), applied, url)
    assert _plays(player, url), url
    return url


def test_a_box_and_an_xtream_account_side_by_side(world):
    player = _libmpv()
    try:
        for round_ in range(2):                    # there and back, twice
            # -- the Xtream account ---------------------------------------------
            xt_client = _switch_to(world["xtream"])
            assert isinstance(xt_client, XtreamClient)
            names = [r["name"] for r in xt_client.live_streams(None)]
            assert names == ["TV4 HD", "Kanal 5"]            # its own channels only
            asked = len(world["proxy"].requests)
            url = _play(player, xt_client, "TV4 HD", applied=True)
            # The global proxy and user agent: the stream went through the
            # proxy, saying VLC - not the box's Enigma2.
            via = world["proxy"].requests[asked:]
            assert any(r["path"] == url for r in via), via
            assert all("VLC" in r["headers"].get("user-agent", "") for r in via)
            # The account's own API calls went the same way, saying the same.
            assert any("/player_api.php" in r["path"] for r in world["proxy"].requests)
            assert "VLC" in world["xt"].requests[-1]["headers"]["user-agent"]

            # -- the box ----------------------------------------------------------
            box_client = _switch_to(world["box"])
            assert isinstance(box_client, Enigma2Client)
            names = [r["name"] for r in box_client.live_streams(None)]
            assert "SVT1 HD" in names and "TV4 HD" not in names
            asked = len(world["proxy"].requests)
            _play(player, box_client, "SVT1 HD", applied=True)
            # Straight to the box (a box never takes the global proxy), with
            # the box's own user agent.
            assert len(world["proxy"].requests) == asked, round_
            got = world["stream_port"].requests[-1]
            assert got["path"] == "/" + SVT1
            assert got["headers"]["user-agent"].startswith("Enigma2")
    finally:
        player.terminate()


def test_both_sources_stored_together_and_each_keeps_its_own(world, tmp_path):
    """The source list holds both; editing one changes nothing of the other,
    and the export carries the box's options and the account's login."""
    from PyQt6.QtCore import QSettings

    from dopeiptv.core.stores import PlaylistStore
    qs = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    store = PlaylistStore(qs)
    store.add(dict(world["xtream"]))
    store.add(dict(world["box"]))
    assert [p["kind"] for p in store.playlists()] == ["xtream", "enigma2"]
    again = PlaylistStore(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    box = again.get("b1")
    xt = again.get("x1")
    assert box["options"] == {"stream_port": str(world["stream_port"].port), "user_agent": "enigma2"}
    assert "options" not in xt or not xt["options"]
    assert (xt["username"], xt["password"]) == ("anna", "pw-anna")
