"""Jellyfin, Emby and Plex against fake servers that answer in each one's
own JSON shapes: signing in (AuthenticateByName, Quick Connect, Plex's PIN),
the catalogue as the app's rows (films, series, episodes, channels, the
server's resume points and watched marks), the play addresses, the guide,
what is reported back, and finding them on the network (UDP 7359, GDM)."""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from dopeiptv.core.log import redact
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import jellyfin, lan_scan, mediaserver, plex
from dopeiptv.providers.boxes import RECORDINGS
from dopeiptv.providers.client import make_client
from dopeiptv.providers.epg import XmltvGuide
from dopeiptv.providers.jellyfin import EmbyClient, JellyfinClient
from dopeiptv.providers.plex import PlexClient

NOW = int(time.time())
UID = "u" * 32
TOKEN = "tok-3f9a1c0d7e"


def _iso(t: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.0000000Z", time.gmtime(t))


class FakeServer:
    """routes: (method, path) -> body, or callable(query, body, headers) ->
    body or (status, body). Every call is kept: (method, path, query, body,
    headers)."""

    def __init__(self, routes: dict) -> None:
        self.routes, self.calls = routes, []
        srv = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _do(self, method):
                u = urlsplit(self.path)
                q = {k: v[0] for k, v in parse_qs(u.query, keep_blank_values=True).items()}
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                try:
                    body = json.loads(raw) if raw else None
                except ValueError:
                    body = raw
                headers = {k.lower(): v for k, v in self.headers.items()}
                srv.calls.append((method, u.path, q, body, headers))
                route = srv.routes.get((method, u.path))
                status, out = 200, route
                if callable(route):
                    out = route(q, body, headers)
                    if isinstance(out, tuple):
                        status, out = out
                if route is None:
                    status, out = 404, {"error": "not found"}
                data = json.dumps(out).encode() if not isinstance(out, (bytes, str)) else (
                    out if isinstance(out, bytes) else out.encode())
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._do("GET")

            def do_POST(self):
                self._do("POST")

            def do_DELETE(self):
                self._do("DELETE")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def hits(self, method: str, path: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == method and c[1] == path]

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def fake():
    made: list[FakeServer] = []

    def make(routes):
        s = FakeServer(routes)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


@pytest.fixture(autouse=True)
def _one_device(monkeypatch):
    store = {}

    class S:
        def value(self, k, d=None):
            return store.get(k, d)

        def setValue(self, k, v):  # noqa: N802 - QSettings' name
            store[k] = v
    mediaserver.install_device_store(S())
    yield
    mediaserver.install_device_store(None)


# -- Jellyfin / Emby: signing in --------------------------------------------------

def _auth_route(q, body, headers):
    if body == {"Username": "anna", "Pw": "secret pw"}:
        return {"AccessToken": TOKEN, "ServerId": "srv1", "User": {"Id": UID, "Name": "Anna"}}
    return 401, {}


def test_jellyfin_sign_in_keeps_a_token_never_the_password(fake):
    s = fake({("POST", "/Users/AuthenticateByName"): _auth_route})
    got = jellyfin.sign_in(s.base, "anna", "secret pw")
    assert got == {"token": TOKEN, "user_id": UID, "user": "Anna", "server_id": "srv1", "server": s.base}
    assert "secret pw" not in json.dumps(got)
    _m, _p, _q, body, headers = s.calls[0]
    auth = headers["authorization"]
    assert auth.startswith("MediaBrowser ") and 'Client="dopeIPTV"' in auth
    assert f'DeviceId="{mediaserver.device_id()}"' in auth and "Token=" not in auth
    with pytest.raises(LocalizedError) as e:
        jellyfin.sign_in(s.base, "anna", "wrong")
    assert e.value.key == "err_wrong_login"


def test_emby_signs_in_under_its_prefix_and_headers(fake):
    s = fake({("POST", "/emby/Users/AuthenticateByName"): _auth_route})
    got = jellyfin.sign_in(s.base + "/emby", "anna", "secret pw", "emby")
    assert got["token"] == TOKEN and got["server"] == s.base
    headers = s.calls[0][4]
    assert headers["x-emby-authorization"].startswith("Emby ") and "authorization" not in headers


def test_quick_connect(fake):
    state = {"asked": 0}

    def connect(q, _b, _h):
        state["asked"] += 1
        return {"Authenticated": state["asked"] > 1, "Secret": q.get("secret")}

    def finish(_q, body, _h):
        assert body == {"Secret": "s3cr3t"}
        return {"AccessToken": TOKEN, "User": {"Id": UID, "Name": "Anna"}}
    s = fake({("GET", "/QuickConnect/Enabled"): True,
              # 10.8 starts one with a GET: the POST is not there.
              ("GET", "/QuickConnect/Initiate"): {"Secret": "s3cr3t", "Code": "123456"},
              ("GET", "/QuickConnect/Connect"): connect,
              ("POST", "/Users/AuthenticateWithQuickConnect"): finish})
    assert jellyfin.quick_connect_enabled(s.base)
    started = jellyfin.quick_connect_start(s.base)
    assert started == {"secret": "s3cr3t", "code": "123456"}
    assert not jellyfin.quick_connect_ready(s.base, "s3cr3t")
    assert jellyfin.quick_connect_ready(s.base, "s3cr3t")
    assert jellyfin.quick_connect_finish(s.base, "s3cr3t")["user_id"] == UID


# -- Jellyfin / Emby: the catalogue ------------------------------------------------

MOVIE = {"Id": "a" * 32, "Name": "Heat", "Type": "Movie", "ProductionYear": 1995,
         "Overview": "Cops\nand robbers.", "Genres": ["Crime", "Drama"], "CommunityRating": 8.3,
         "OfficialRating": "R", "RunTimeTicks": 10_200 * 10_000_000, "Container": "mkv,webm",
         "DateCreated": _iso(NOW - 86400), "PremiereDate": "1995-12-15T00:00:00.0000000Z",
         "ProviderIds": {"Tmdb": "949", "Imdb": "tt0113277"}, "ImageTags": {"Primary": "ptag"},
         "BackdropImageTags": ["btag"],
         "UserData": {"PlaybackPositionTicks": 3_000 * 10_000_000, "Played": False,
                      "LastPlayedDate": _iso(NOW - 3600)}}
SEEN = dict(MOVIE, Id="b" * 32, Name="Ronin", ProductionYear=1998, ProviderIds={},
            UserData={"Played": True, "PlaybackPositionTicks": 0})
SERIES = {"Id": "c" * 32, "Name": "Dark", "Type": "Series", "ProductionYear": 2017,
          "Overview": "Time.", "ImageTags": {"Primary": "stag"}, "ProviderIds": {"Tmdb": "70523"},
          "DateCreated": _iso(NOW - 900_000), "DateLastMediaAdded": _iso(NOW - 1000),
          "UserData": {"Played": False}}


def _episode(n, season=1, played=False, pos=0):
    return {"Id": f"{season:02d}{n:02d}" + "e" * 28, "Name": f"Episode {n}", "Type": "Episode",
            "IndexNumber": n, "ParentIndexNumber": season, "SeriesId": SERIES["Id"],
            "SeriesPrimaryImageTag": "stag", "RunTimeTicks": 3000 * 10_000_000,
            "UserData": {"Played": played, "PlaybackPositionTicks": pos * 10_000_000,
                         "LastPlayedDate": _iso(NOW - 60)}}


CHANNELS = [{"Id": "f1" * 16, "Name": "News 24", "ChannelNumber": "2.1", "ChannelType": "TV",
             "ImageTags": {"Primary": "ltag"}, "UserData": {"IsFavorite": True}},
            {"Id": "f2" * 16, "Name": "Jazz FM", "Number": "101", "ChannelType": "Radio"}]


def jellyfin_routes(prefix: str = "", new_api: bool = True) -> dict:
    p = prefix
    users = f"{p}/Users/{UID}"

    def items(q, _b, _h):
        if q.get("IncludeItemTypes") == "Movie":
            assert q.get("ParentId") == "lib-movies" and q.get("Recursive") == "true"
            return {"Items": [MOVIE, SEEN], "TotalRecordCount": 2}
        if q.get("IncludeItemTypes") == "Series":
            return {"Items": [SERIES], "TotalRecordCount": 1}
        return {"Items": []}

    views = {"Items": [{"Id": "lib-movies", "Name": "Films", "CollectionType": "movies"},
                       {"Id": "lib-shows", "Name": "Shows", "CollectionType": "tvshows"},
                       {"Id": "lib-music", "Name": "Music", "CollectionType": "music"}]}
    resume = {"Items": [dict(MOVIE, Type="Movie"), _episode(2, pos=600)]}
    routes = {
        ("GET", f"{p}/System/Info/Public"): {"Id": "srv1", "ServerName": "Den", "Version": "10.10.3",
                                             "ProductName": "Jellyfin Server" if not p else None},
        ("GET", users): {"Id": UID, "Name": "Anna", "Policy": {"RemoteClientBitrateLimit": 4_000_000}},
        ("GET", f"{p}/LiveTv/Channels"): {"Items": CHANNELS},
        ("GET", f"{p}/LiveTv/Recordings"): {"Items": [
            {"Id": "d" * 32, "Name": "Match", "EpisodeTitle": "Final", "ChannelName": "Sport",
             "StartDate": _iso(NOW - 7200), "RunTimeTicks": 5400 * 10_000_000}]},
        ("GET", f"{p}/Shows/{SERIES['Id']}/Episodes"): {"Items": [
            _episode(2), _episode(1, played=True), _episode(1, season=0)]},
    }
    if new_api:
        routes.update({("GET", "/UserViews"): views, ("GET", "/Items"): items,
                       ("GET", "/UserItems/Resume"): resume})
    else:
        routes.update({("GET", f"{users}/Views"): views, ("GET", f"{users}/Items"): items,
                       ("GET", f"{users}/Items/Resume"): resume})
    return routes


def _jellyfin(fake, routes=None, cls=JellyfinClient, prefix="", options=None):
    s = fake(routes or jellyfin_routes(prefix))
    c = make_client({"kind": "emby" if cls is EmbyClient else "jellyfin", "server": s.base,
                     "username": "anna", "token": TOKEN,
                     "options": dict({"user_id": UID}, **(options or {}))})
    assert isinstance(c, cls)
    return s, c


def test_jellyfin_catalogue_as_the_apps_rows(fake):
    s, c = _jellyfin(fake)
    c.authenticate()
    assert c.box_info["name"] == "Den (Jellyfin 10.10.3)" and c.box_info["user"] == "Anna"
    cats = c.vod_categories()
    assert [x["category_name"] for x in cats] == ["Films", "Recordings"]
    heat, ronin = c.vod_streams("lib-movies")
    assert heat["name"] == "Heat (1995)" and heat["stream_type"] == "movie"
    assert heat["_tmdb_id"] == 949 and heat["container_extension"] == "mkv"
    assert heat["rating_5based"] == 4.2 and heat["_info"]["genre"] == "Crime, Drama"
    assert heat["_info"]["duration"] == "02:50:00" and heat["_info"]["plot"] == "Cops\nand robbers."
    assert not heat["_played"] and heat["_server_pos"] == 3000 and heat["_progress_pct"] == 29
    assert ronin["_played"] and "_progress_pct" not in ronin
    assert heat["stream_icon"] == f"{s.base}/Items/{'a' * 32}/Images/Primary?fillHeight=480&quality=90&tag=ptag"
    url = c.vod_url(heat["stream_id"])
    assert url.startswith(f"{s.base}/Videos/{'a' * 32}/stream?static=true")
    assert f"api_key={TOKEN}" in url and TOKEN not in redact(url)
    # Every header the client sends carries the token as Jellyfin reads it.
    assert f'Token="{TOKEN}"' in s.hits("GET", "/UserViews")[0][4]["authorization"]
    assert s.hits("GET", "/UserViews")[0][2]["userId"] == UID
    # The same ids on every load: favourites keep pointing at the same film.
    ids = {r["name"]: r["stream_id"] for r in c.vod_streams(None)}
    c.clear_list_cache()
    assert {r["name"]: r["stream_id"] for r in c.vod_streams(None)} == ids
    # The music library is neither films nor series.
    assert [x["category_name"] for x in c.series_categories()] == ["Shows"]


def test_jellyfin_series_and_episodes(fake):
    s, c = _jellyfin(fake)
    show, = c.series_list(None)
    assert show["name"] == "Dark (2017)" and show["_tmdb_id"] == 70523
    assert int(show["last_modified"]) == NOW - 1000
    info = c.series_info(show["series_id"])
    assert list(info["episodes"]) == ["1", "0"]           # the specials last
    e1, e2 = info["episodes"]["1"]
    assert (e1["episode_num"], e2["episode_num"]) == (1, 2) and e1["_played"]
    assert "series_id" not in e1 and e1["info"]["duration_secs"] == 3000
    assert c.episode_url(e2["id"]).startswith(f"{s.base}/Videos/0102{'e' * 28}/stream?static=true")
    # A second look within a while does not ask again.
    c.series_info(show["series_id"])
    assert len(s.hits("GET", f"/Shows/{SERIES['Id']}/Episodes")) == 1


def test_jellyfin_resume_points_become_continue_watching(fake):
    s, c = _jellyfin(fake)
    c.authenticate()
    rows = c.resume_items()
    kinds = sorted(r["_kind"] for r in rows)
    assert kinds == ["episode", "vod"]
    ep = next(r for r in rows if r["_kind"] == "episode")
    assert ep["_series_ctx"]["name"] == "Dark (2017)" and ep["_resume_pos"] == 600
    assert ep["name"] == "Dark (2017) · S1 * E2 - Episode 2"
    assert c.episode_url(ep["id"])                 # playable from the flat list
    film = next(r for r in rows if r["_kind"] == "vod")
    assert film["_resume_pos"] == 3000 and film["_ts"] == NOW - 3600
    assert c.server_state("movie", film["stream_id"]) == {
        "pos": 3000.0, "played": False, "last_played": NOW - 3600}


def test_jellyfin_before_10_9_and_emby_paths(fake):
    s, c = _jellyfin(fake, jellyfin_routes(new_api=False))
    c.authenticate()
    assert [x["category_name"] for x in c.vod_categories()][0] == "Films"
    assert s.hits("GET", f"/Users/{UID}/Views") and s.hits("GET", "/UserViews")
    s2, e = _jellyfin(fake, jellyfin_routes("/emby", new_api=False), cls=EmbyClient)
    e.authenticate()
    assert e.box_info["name"].startswith("Den (Emby")
    assert e.vod_url(e.vod_streams("lib-movies")[0]["stream_id"]).startswith(f"{s2.base}/emby/Videos/")
    assert not s2.hits("GET", "/UserViews")
    headers = s2.hits("GET", f"/emby/Users/{UID}/Views")[0][4]
    assert headers["x-emby-token"] == TOKEN


def test_jellyfin_hls_when_asked_or_off_the_home_network(fake):
    s, c = _jellyfin(fake, options={"ms_quality": "8"})
    c.authenticate()
    url = c.vod_url(c.vod_streams("lib-movies")[0]["stream_id"])
    path, q = url.split("?", 1)
    assert path.endswith(f"/Videos/{'a' * 32}/master.m3u8")
    q = {k: v[0] for k, v in parse_qs(q).items()}
    assert q["VideoCodec"] == "h264" and q["AudioCodec"] == "aac" and q["MaxStreamingBitrate"] == "8000000"
    assert q["MediaSourceId"] == "a" * 32 and q["api_key"] == TOKEN and q["PlaySessionId"]
    # Away from home the user's remote limit decides; at home the file plays.
    _s, d = _jellyfin(fake)
    d.authenticate()
    assert d._bitrate() == 0
    d.server = "https://media.example.com"
    assert d._bitrate() == 4_000_000


def test_a_token_the_server_no_longer_takes(fake):
    routes = jellyfin_routes()
    routes[("GET", f"/Users/{UID}")] = lambda q, b, h: (401, {})
    _s, c = _jellyfin(fake, routes)
    with pytest.raises(LocalizedError) as e:
        c.authenticate()
    assert e.value.key == "err_ms_signed_out"


def test_jellyfin_live_tv_opens_then_plays_and_has_a_guide(fake):
    routes = jellyfin_routes()

    def playback_info(q, body, _h):
        assert q["AutoOpenLiveStream"] == "true" and body["DeviceProfile"]["DirectPlayProfiles"]
        return {"PlaySessionId": "ps1", "MediaSources": [
            {"Id": "ms1", "LiveStreamId": "ls1", "SupportsDirectStream": True, "Container": "ts"}]}

    def programs(q, _b, _h):
        assert q["userId"] == UID and q["MinEndDate"].endswith("Z")
        return {"Items": [{"ChannelId": "f1" * 16, "Name": "Late News", "EpisodeTitle": "Tonight",
                           "Overview": "Headlines.", "StartDate": _iso(NOW), "EndDate": _iso(NOW + 1800),
                           "Genres": ["News"]}], "TotalRecordCount": 1}
    routes[("POST", f"/Items/{'f1' * 16}/PlaybackInfo")] = playback_info
    routes[("GET", "/LiveTv/Programs")] = programs
    s, c = _jellyfin(fake, routes)
    c.authenticate()
    cats = [x["category_name"] for x in c.live_categories()]
    assert cats == ["All channels", "Channels (radio)", "Favorites"]
    news = next(r for r in c.live_streams(None) if r["name"] == "News 24")
    assert news["num"] == 2 and news["epg_channel_id"] == "f1" * 16 and news["_open"]
    assert not c.link_ready("live_url", news["stream_id"])
    url = c.live_url(news["stream_id"])
    assert url.startswith(f"{s.base}/Videos/{'f1' * 16}/stream.ts?")
    q = {k: v[0] for k, v in parse_qs(url.split("?", 1)[1]).items()}
    assert q["LiveStreamId"] == "ls1" and q["PlaySessionId"] == "ps1" and q["static"] == "true"
    assert c.link_ready("live_url", news["stream_id"])
    assert c.live_url(news["stream_id"]) == url
    assert len(s.hits("POST", f"/Items/{'f1' * 16}/PlaybackInfo")) == 1
    xml = c.own_guide()
    assert b"Late News" in xml and ('channel="' + "f1" * 16 + '"').encode() in xml
    guide = XmltvGuide(c, None)
    assert guide._download() == xml
    target = c.report_target(news["stream_id"], "live", news, url)
    assert target["id"] == "f1" * 16 and target["session"] == "ps1"
    c.report("stop", target, 10, 0)
    assert c.flush_reports(3)
    stop = s.hits("POST", "/Sessions/Playing/Stopped")[0][3]
    assert stop["LiveStreamId"] == "ls1" and stop["PlaySessionId"] == "ps1"


def test_jellyfin_reports_play_and_marks(fake):
    routes = jellyfin_routes()
    for path in ("/Sessions/Playing", "/Sessions/Playing/Progress", "/Sessions/Playing/Stopped"):
        routes[("POST", path)] = (lambda q, b, h: (204, b""))
    routes[("POST", f"/UserPlayedItems/{'a' * 32}")] = {"Played": True}
    routes[("DELETE", f"/UserPlayedItems/{'a' * 32}")] = {"Played": False}
    s, c = _jellyfin(fake, routes)
    c.authenticate()
    heat = c.vod_streams("lib-movies")[0]
    url = c.vod_url(heat["stream_id"])
    # A row from History carries no server id: the address does.
    target = c.report_target(heat["stream_id"], "movie", {"_url": url}, url)
    assert target["id"] == "a" * 32 and not target["hls"]
    assert c.report_target(1, "movie", None, "http://elsewhere/x.mp4") is None
    c.report("start", target, 0, 10200)
    c.report("progress", target, 125.5, 10200, paused=True)
    c.report("stop", target, 9900, 10200)
    c.mark(target, False)
    c.mark(target, True)
    assert c.flush_reports(3)
    start = s.hits("POST", "/Sessions/Playing")[0][3]
    assert start["ItemId"] == "a" * 32 and start["PlaySessionId"] == target["session"]
    prog = s.hits("POST", "/Sessions/Playing/Progress")[0][3]
    assert prog["PositionTicks"] == 1_255_000_000 and prog["IsPaused"] and prog["EventName"] == "Pause"
    assert s.hits("POST", "/Sessions/Playing/Stopped")[0][3]["PositionTicks"] == 99_000_000_000
    assert s.hits("DELETE", f"/UserPlayedItems/{'a' * 32}")[0][2]["userId"] == UID
    assert s.hits("POST", f"/UserPlayedItems/{'a' * 32}")
    # The row knows at once, before the next load asks the server.
    assert heat["_played"] and "_progress_pct" not in heat
    order = [c_[1] for c_ in s.calls if c_[1].startswith(("/Sessions", "/UserPlayed"))]
    assert order == ["/Sessions/Playing", "/Sessions/Playing/Progress", "/Sessions/Playing/Stopped",
                     f"/UserPlayedItems/{'a' * 32}", f"/UserPlayedItems/{'a' * 32}"]


def test_a_stop_near_the_end_counts_as_watched(fake):
    _s, c = _jellyfin(fake)
    c.authenticate()
    heat = c.vod_streams("lib-movies")[0]
    target = {"id": heat["_ms_id"], "kind": "movie"}
    c._note(target, 1200, 10200, None)
    assert heat["_server_pos"] == 1200 and not heat["_played"]
    c._note(target, 9500, 10200, None)
    assert heat["_played"] and heat["_server_pos"] == 0


# -- Plex: signing in -----------------------------------------------------------

def test_plex_pin_flow_and_servers(fake, monkeypatch):
    state = {"polls": 0}

    def pin(q, _b, _h):
        state["polls"] += 1
        return {"id": 42, "code": "abcd", "authToken": "plex-account-token" if state["polls"] > 1 else None}
    resources = [
        {"name": "Shared", "provides": "server", "clientIdentifier": "m2", "accessToken": "shared-tok",
         "owned": False, "connections": [{"uri": "https://1-2-3-4.x.plex.direct:32400", "local": False}]},
        {"name": "Den", "provides": "server", "clientIdentifier": "m1", "accessToken": "den-tok",
         "owned": True, "connections": [
             {"uri": "https://relay.plex.direct:8443", "relay": True, "local": False},
             {"uri": "https://9-9-9-9.x.plex.direct:32400", "local": False},
             {"uri": "https://192-168-1-5.x.plex.direct:32400", "local": True, "protocol": "https",
              "address": "192.168.1.5", "port": 32400}]},
        {"name": "Phone", "provides": "player", "clientIdentifier": "p1"}]
    s = fake({("POST", "/api/v2/pins"): {"id": 42, "code": "abcd"},
              ("GET", "/api/v2/pins/42"): pin,
              ("GET", "/api/v2/resources"): resources,
              ("GET", "/api/v2/user"): {"username": "anna", "title": "Anna"}})
    monkeypatch.setattr(plex, "PLEX_TV", s.base)
    got = plex.create_pin()
    assert got == {"id": 42, "code": "abcd"}
    h = s.calls[0][4]
    assert h["x-plex-client-identifier"] == mediaserver.device_id() and h["x-plex-product"] == "dopeIPTV"
    assert s.calls[0][2] == {"strong": "true"}
    url = plex.auth_url("abcd")
    assert url.startswith("https://app.plex.tv/auth#?clientID=") and "code=abcd" in url
    assert plex.check_pin(42, "abcd") == ""
    assert plex.check_pin(42, "abcd") == "plex-account-token"
    assert plex.account_name("plex-account-token") == "anna"
    found = plex.servers("plex-account-token")
    assert [x["name"] for x in found] == ["Den", "Shared"]
    assert found[0]["token"] == "den-tok" and found[0]["connections"] == [
        "https://192-168-1-5.x.plex.direct:32400", "http://192.168.1.5:32400",
        "https://9-9-9-9.x.plex.direct:32400", "https://relay.plex.direct:8443"]
    assert s.hits("GET", "/api/v2/resources")[0][4]["x-plex-token"] == "plex-account-token"


def test_plex_pick_connection_keeps_the_first_that_answers(fake):
    good = fake({("GET", "/identity"): {"MediaContainer": {"machineIdentifier": "m1"}}})
    other = fake({("GET", "/identity"): {"MediaContainer": {"machineIdentifier": "zzz"}}})
    dead = "http://127.0.0.1:9"
    assert plex.pick_connection([dead, other.base, good.base], "m1", timeout=1.0) == good.base
    assert plex.pick_connection([dead], "m1", timeout=0.5) == ""


# -- Plex: the catalogue ----------------------------------------------------------

PMOVIE = {"ratingKey": "101", "type": "movie", "title": "Alien", "year": 1979, "summary": "In space.",
          "thumb": "/library/metadata/101/thumb/1", "art": "/library/metadata/101/art/1",
          "duration": 7020000, "addedAt": NOW - 500, "viewOffset": 1200000, "lastViewedAt": NOW - 30,
          "audienceRating": 9.4, "contentRating": "R", "Genre": [{"tag": "Horror"}],
          "Director": [{"tag": "Ridley Scott"}], "Role": [{"tag": "Sigourney Weaver"}],
          "Guid": [{"id": "imdb://tt0078748"}, {"id": "tmdb://348"}],
          "Media": [{"container": "mkv", "Part": [{"key": "/library/parts/9/1/file.mkv", "container": "mkv"}]}]}
PSHOW = {"ratingKey": "200", "type": "show", "title": "Severance", "year": 2022, "leafCount": 2,
         "viewedLeafCount": 2, "thumb": "/library/metadata/200/thumb/1", "addedAt": NOW - 9000,
         "updatedAt": NOW - 100}


def _pep(n, rk, offset=0):
    return {"ratingKey": rk, "type": "episode", "title": f"Part {n}", "index": n, "parentIndex": 1,
            "grandparentRatingKey": "200", "duration": 3000000, "viewOffset": offset, "viewCount": 1 if n == 1 else 0,
            "Media": [{"Part": [{"key": f"/library/parts/{rk}/1/f.mkv", "container": "mkv"}]}]}


def plex_routes(dvr: bool = False) -> dict:
    def mc(**kw):
        return {"MediaContainer": kw}
    routes = {
        ("GET", "/"): mc(friendlyName="Den", machineIdentifier="m1", version="1.41.0.1-abc"),
        ("GET", "/library/sections"): mc(Directory=[{"key": "1", "type": "movie", "title": "Movies"},
                                                    {"key": "2", "type": "show", "title": "TV"},
                                                    {"key": "3", "type": "artist", "title": "Music"}]),
        ("GET", "/library/sections/1/all"): lambda q, b, h: mc(
            Metadata=[PMOVIE] if q.get("type") == "1" else [], totalSize=1),
        ("GET", "/library/sections/2/all"): lambda q, b, h: mc(
            Metadata=[PSHOW] if q.get("type") == "2" else [], totalSize=1),
        ("GET", "/library/metadata/200/allLeaves"): mc(Metadata=[_pep(2, "202", 600000), _pep(1, "201")]),
        ("GET", "/library/onDeck"): mc(Metadata=[PMOVIE, dict(_pep(2, "202", 600000), lastViewedAt=NOW)]),
        ("GET", "/:/timeline"): mc(),
        ("GET", "/:/scrobble"): "",
        ("GET", "/:/unscrobble"): "",
    }
    if dvr:
        routes[("GET", "/livetv/dvrs")] = mc(Dvr=[{"key": "7", "lineup": "lineup://x"}])
        routes[("GET", "/livetv/epg/channels")] = mc(Channel=[
            {"identifier": "5.1", "title": "KQED", "channelVcn": "5.1", "thumb": "http://logo/kqed.png"}])
        routes[("POST", "/livetv/dvrs/7/channels/5.1/tune")] = mc(MediaSubscription=[
            {"MediaGrabOperation": [{"Metadata": {"key": "/livetv/sessions/abc"}}]}])
    return routes


def _plex(fake, routes=None, options=None):
    s = fake(routes or plex_routes())
    c = make_client({"kind": "plex", "server": s.base, "username": "anna", "token": "den-tok",
                     "options": dict({"server_id": "m1"}, **(options or {}))})
    assert isinstance(c, PlexClient)
    return s, c


def test_plex_catalogue_as_the_apps_rows(fake):
    s, c = _plex(fake)
    c.authenticate()
    assert c.box_info["name"] == "Den (Plex 1.41.0.1)"
    assert [x["category_name"] for x in c.vod_categories()] == ["Movies"]
    alien, = c.vod_streams("1")
    assert alien["name"] == "Alien (1979)" and alien["_tmdb_id"] == 348
    assert alien["_info"]["director"] == "Ridley Scott" and alien["_info"]["cast"] == "Sigourney Weaver"
    assert alien["_server_pos"] == 1200 and alien["_progress_pct"] == 17 and not alien["_played"]
    url = c.vod_url(alien["stream_id"])
    assert url == f"{s.base}/library/parts/9/1/file.mkv?X-Plex-Token=den-tok"
    assert "X-Plex-Token=den-tok" in alien["stream_icon"] and "%2Flibrary%2Fmetadata%2F101%2Fthumb" in alien["stream_icon"]
    assert "den-tok" not in redact(url)
    assert s.hits("GET", "/library/sections")[0][4]["x-plex-token"] == "den-tok"
    show, = c.series_list("2")
    assert show["_played"] and show["name"] == "Severance (2022)"
    eps = c.series_info(show["series_id"])["episodes"]["1"]
    assert [e["title"] for e in eps] == ["Part 1", "Part 2"] and eps[0]["_played"]
    assert eps[1]["_progress_pct"] == 20
    rows = c.resume_items()
    assert sorted(r["_kind"] for r in rows) == ["episode", "vod"]
    # No DVR: no channels, and a reason - never a failure.
    assert c.live_streams(None) == [] and "Plex Pass" in c.live_note


def test_plex_transcodes_when_a_bitrate_is_asked(fake):
    _s, c = _plex(fake, options={"ms_quality": "4"})
    c.authenticate()
    url = c.vod_url(c.vod_streams("1")[0]["stream_id"])
    path, q = url.split("?", 1)
    assert path.endswith("/video/:/transcode/universal/start.m3u8")
    q = {k: v[0] for k, v in parse_qs(q).items()}
    assert q["path"] == "/library/metadata/101" and q["maxVideoBitrate"] == "4000" and q["protocol"] == "hls"
    assert q["X-Plex-Client-Identifier"] == mediaserver.device_id() and q["session"]
    assert c.item_of_url(url) == "101"


def test_plex_reports_to_the_timeline(fake):
    s, c = _plex(fake)
    c.authenticate()
    alien = c.vod_streams("1")[0]
    target = c.report_target(alien["stream_id"], "movie", alien, c.vod_url(alien["stream_id"]))
    c.report("start", target, 0, 7020)
    c.report("progress", target, 61.25, 7020, paused=True)
    c.report("stop", target, 6800, 7020)
    c.mark(target, False)
    assert c.flush_reports(3)
    states = [(h[2]["state"], h[2]["time"]) for h in s.hits("GET", "/:/timeline")]
    assert states == [("playing", "0"), ("paused", "61250"), ("stopped", "6800000")]
    t = s.hits("GET", "/:/timeline")[0]
    assert t[2]["ratingKey"] == "101" and t[2]["key"] == "/library/metadata/101" and t[2]["duration"] == "7020000"
    assert t[4]["x-plex-session-identifier"] == target["session"]
    assert s.hits("GET", "/:/unscrobble")[0][2] == {"identifier": "com.plexapp.plugins.library", "key": "101"}


def test_plex_moves_to_another_of_its_addresses(fake):
    s = fake(plex_routes())
    s.routes[("GET", "/identity")] = {"MediaContainer": {"machineIdentifier": "m1"}}
    c = make_client({"kind": "plex", "server": "http://127.0.0.1:9", "token": "den-tok",
                     "options": {"server_id": "m1", "connections": f"http://127.0.0.1:9 {s.base}"}})
    c.authenticate()
    assert c.server == s.base and c.vod_url(c.vod_streams("1")[0]["stream_id"]).startswith(s.base)


def test_plex_live_tv_with_a_dvr(fake):
    s, c = _plex(fake, plex_routes(dvr=True))
    c.authenticate()
    assert c.live_note == ""
    ch, = c.live_streams(None)
    assert ch["name"] == "KQED" and ch["num"] == 5 and ch["epg_channel_id"] == "5.1"
    url = c.live_url(ch["stream_id"])
    q = {k: v[0] for k, v in parse_qs(url.split("?", 1)[1]).items()}
    assert q["path"] == "/livetv/sessions/abc" and q["live"] == "1"
    assert s.hits("POST", "/livetv/dvrs/7/channels/5.1/tune")


def test_plex_live_tv_plays_through_the_windows_address(fake):
    """The window asks a box that transcodes for its address by play_url
    (ui/mw_box.py): Plex has no such thing, and its own film helper of that
    name took a channel's id for a film's metadata - AttributeError, and
    no Plex channel played, in the player, multiview or a cast."""
    from dopeiptv.ui.mw_box import _BoxMixin

    class Host(_BoxMixin):
        pass
    s, c = _plex(fake, plex_routes(dvr=True))
    c.authenticate()
    ch, = c.live_streams(None)
    h = Host()
    h.client = c
    for purpose in ("play", "multiview", "cast"):
        url = h._live_play_url(ch["stream_id"], "ts", purpose)
        assert "/livetv/sessions/abc" in parse_qs(url.split("?", 1)[1])["path"][0]
    vod = c.vod_url(c.vod_streams("1")[0]["stream_id"])
    assert vod == f"{s.base}/library/parts/9/1/file.mkv?X-Plex-Token=den-tok"


# -- finding them on the network ---------------------------------------------------

def test_jellyfin_discovery_answer():
    got = jellyfin.parse_discovery(json.dumps(
        {"Address": "http://172.17.0.2:8096", "Id": "srv1", "Name": "Den"}).encode(), "192.168.1.5", "jellyfin")
    # A server in a container names its own address: the one that answered wins.
    assert got == {"kind": "jellyfin", "server": "http://192.168.1.5:8096", "id": "srv1",
                   "name": "Den - 192.168.1.5"}
    own = jellyfin.parse_discovery(json.dumps(
        {"Address": "https://media.example.com", "Id": "x", "Name": "Out"}).encode(), "192.168.1.6", "emby")
    assert own["server"] == "https://media.example.com" and own["kind"] == "emby"
    assert jellyfin.parse_discovery(b"nope", "1.2.3.4", "jellyfin") is None


def _udp_responder(answers: dict[bytes, bytes]):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    sock.settimeout(3)

    def run():
        try:
            while True:
                data, addr = sock.recvfrom(2048)
                reply = answers.get(data)
                if reply:
                    sock.sendto(reply, addr)
        except OSError:
            pass
    threading.Thread(target=run, daemon=True).start()
    return sock


def test_jellyfin_and_emby_answer_their_own_question():
    sock = _udp_responder({
        b"who is JellyfinServer?": json.dumps({"Address": "http://127.0.0.1:8096", "Id": "j1",
                                               "Name": "Jelly"}).encode(),
        b"who is EmbyServer?": json.dumps({"Address": "http://127.0.0.1:8097", "Id": "e1",
                                           "Name": "Em"}).encode()})
    try:
        got = jellyfin.discover(timeout=1.0, target=sock.getsockname())
    finally:
        sock.close()
    assert sorted((d["kind"], d["server"]) for d in got) == [
        ("emby", "http://127.0.0.1:8097"), ("jellyfin", "http://127.0.0.1:8096")]


def test_plex_gdm():
    reply = (b"HTTP/1.0 200 OK\r\nContent-Type: plex/media-server\r\nResource-Identifier: m1\r\n"
             b"Name: Den\r\nPort: 32400\r\nVersion: 1.41\r\n\r\n")
    assert plex.parse_gdm(reply, "192.168.1.5") == {
        "kind": "plex", "server": "http://192.168.1.5:32400", "id": "m1", "name": "Den - 192.168.1.5"}
    assert plex.parse_gdm(b"HTTP/1.0 200 OK\r\nContent-Type: plex/media-player\r\n\r\n", "x") is None
    sock = _udp_responder({plex.GDM_SEARCH: reply})
    try:
        got = plex.discover(timeout=1.0, targets=(sock.getsockname(),))
    finally:
        sock.close()
    assert got == [{"kind": "plex", "server": "http://127.0.0.1:32400", "id": "m1", "name": "Den - 127.0.0.1"}]


def test_lan_scan_finds_media_servers(fake):
    jf = fake({("GET", "/System/Info/Public"): {"Id": "j1", "ServerName": "Den", "ProductName": "Jellyfin Server"}})
    em = fake({("GET", "/System/Info/Public"): {"Id": "e1", "ServerName": "Attic"},
               ("GET", "/emby/System/Info/Public"): {"Id": "e1", "ServerName": "Attic"}})
    px = fake({("GET", "/identity"): {"MediaContainer": {"machineIdentifier": "m1"}}})
    found = []
    for port, plex_port in ((jf.port, 1), (em.port, px.port)):
        found += lan_scan.probe("127.0.0.1", {"enigma2": 1, "tvheadend": 1, "hdhomerun": 1,
                                              "jellyfin": port, "plex": plex_port})
    assert sorted((d["kind"], d["server"], d["name"]) for d in found) == [
        ("emby", em.base, "Attic - 127.0.0.1"), ("jellyfin", jf.base, "Den - 127.0.0.1"),
        ("plex", px.base, "Plex - 127.0.0.1")]


# -- the app around them ------------------------------------------------------------

def test_the_device_id_is_one_per_install():
    store = {}

    class S:
        def value(self, k, d=None):
            return store.get(k, d)

        def setValue(self, k, v):  # noqa: N802
            store[k] = v
    mediaserver.install_device_store(S())
    first = mediaserver.device_id()
    assert len(first) == 32 and store["media_device_id"] == first
    mediaserver.install_device_store(S())
    assert mediaserver.device_id() == first
    from dopeiptv.core import backup
    assert backup.left_out("media_device_id")


def test_recordings_are_films_of_their_own(fake):
    s, c = _jellyfin(fake)
    recs = c.vod_streams(RECORDINGS)
    assert [r["name"] for r in recs] == ["Match - Final"]
    assert c.vod_url(recs[0]["stream_id"]).startswith(f"{s.base}/Videos/{'d' * 32}/stream?static=true")
    target = c.report_target(recs[0]["stream_id"], "movie", recs[0], c.vod_url(recs[0]["stream_id"]))
    assert target["id"] == "d" * 32


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):  # noqa: N802 - QSettings' name
        self[key] = value


def test_a_media_server_at_home_is_reached_without_the_proxy():
    from dopeiptv.core.nerd import Nerd
    n = Nerd(_Settings(nerd_enabled="true", nerd_proxy="http://10.8.0.1:3128"))
    assert n.proxy({"_kind": "jellyfin", "_server": "http://192.168.1.5:8096"}) == ""
    assert n.proxy({"_kind": "plex"}, "http://192.168.1.5:32400/library/parts/1/f.mkv") == ""
    assert n.proxy({"_kind": "plex", "_server": "https://relay.plex.direct:8443"}) == "http://10.8.0.1:3128"
    assert n.proxy({"_kind": "emby", "_server": "http://nas:8096", "proxy": "http://own:1"}) == "http://own:1"


def test_tokens_stay_out_of_logs_and_go_where_passwords_go():
    from dopeiptv.core import nerd_check, sync
    from dopeiptv.core.stores import PlaylistStore
    c = make_client({"kind": "jellyfin", "server": "http://10.0.0.5:8096", "token": "tok-abcdef123",
                     "options": {"user_id": UID}})
    assert "tok-abcdef123" not in redact(c.stream_url("a" * 32))
    assert "tok-abcdef123" in nerd_check.source_secrets({"token": "tok-abcdef123"})
    store = PlaylistStore(_Settings())
    store.add({"name": "Den", "kind": "plex", "server": "http://10.0.0.6:32400", "username": "anna",
               "password": "", "token": "plex-tok-9", "options": {"server_id": "m1"}})
    exported = store.export_data()
    assert exported[0]["token"] == "plex-tok-9" and exported[0]["kind"] == "plex"
    other = PlaylistStore(_Settings())
    assert other.import_data(exported) == 1
    assert other.items[0]["token"] == "plex-tok-9" and other.items[0]["options"] == {"server_id": "m1"}
    # The install's own id never travels to another computer.
    assert not sync.syncable("media_device_id")
