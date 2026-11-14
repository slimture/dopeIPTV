"""StalkerClient against a fake Stalker / Ministra portal: finding the API
behind the address a box opens, looking like a MAG box, handshake and
profile, a token renewed once, the lists in XtreamClient's own shapes,
create_link, series kept three ways, the programme guide."""
from __future__ import annotations

import calendar
import inspect
import json
import logging
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from dopeiptv.core._lazy_requests import requests
from dopeiptv.core.log import redact_url
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import epg as epg_mod
from dopeiptv.providers.client import XtreamClient, b64, epg_times, make_client
from dopeiptv.providers.epg import XmltvGuide
from dopeiptv.providers.stalker import (
    StalkerClient, normalize_mac, portal_endpoints, strip_cmd,
)

MAC = "00:1A:79:12:34:56"
NOW = int(time.time())


def utc(stamp: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(stamp))


CHANNELS = [
    {"id": "101", "name": "News HD", "number": "1", "logo": "101.png",
     "cmd": "ffmpeg http://localhost/ch/101_", "tv_genre_id": "1",
     "xmltv_id": "news.uk", "tv_archive": "1", "tv_archive_duration": "3",
     "censored": "0"},
    {"id": "102", "name": "Sport 1", "number": "2",
     "logo": "http://img.example/102.png",
     "cmd": "ffmpeg http://localhost/ch/102_", "tv_genre_id": "2",
     "xmltv_id": "", "censored": 1},
    {"id": "103", "name": "Direct", "number": "3", "logo": "",
     "cmd": "ffrt http://cdn.example/direct.ts", "tv_genre_id": "1"},
]
FILMS = [
    {"id": "201", "name": "Film One", "o_name": "Film One",
     "description": "A film.", "actors": "A, B", "director": "D",
     "genres_str": "Drama", "year": "2020", "rating_imdb": "7.4",
     "screenshot_uri": "/stalker_portal/screenshots/201.jpg",
     "cmd": "/media/201.mpg", "added": "2026-01-02 03:04:05",
     "is_series": "0", "category_id": "10", "time": "95"},
    {"id": "202", "name": "Film Two", "cmd": "/media/file_202.mkv",
     "added": "2026-02-01 00:00:00", "is_series": 0, "category_id": "10",
     "rating_imdb": "N/A"},
    {"id": "203", "name": "Old Show", "cmd": "/media/203.mpg",
     "is_series": "1", "series": [1, 2, 3], "category_id": "10",
     "description": "A show.", "year": "2019", "time": "45"},
    {"id": "204", "name": "Film Three", "cmd": "/media/204.mpg",
     "is_series": "0", "category_id": "10"},
]
PROGRAMMES = [
    {"id": "1", "name": "Morning News", "descr": "The news.",
     "time": utc(NOW - 1800), "time_to": utc(NOW + 1800),
     "start_timestamp": NOW - 1800, "stop_timestamp": NOW + 1800},
    {"id": "2", "name": "Søndag & Co", "descr": "",
     "time": utc(NOW + 1800), "time_to": utc(NOW + 5400),
     "start_timestamp": str(NOW + 1800), "stop_timestamp": str(NOW + 5400)},
    # Times as text only: read as UTC, which the box's cookie asks for.
    {"id": "3", "name": "Late", "descr": "x",
     "time": utc(NOW + 5400 - NOW % 60), "time_to": utc(NOW + 9000 - NOW % 60)},
]


class Portal(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, api: str = "/stalker_portal/server/load.php",
                 **opts) -> None:
        self.api = api
        self.opts = opts
        self.expire = 0
        self.tokens: set[str] = set()
        self.handshakes = 0
        self.links = 0
        self.calls: list[tuple[str, dict, dict]] = []
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def api_calls(self, action: str | None = None) -> list[dict]:
        return [q for path, q, _h in self.calls
                if path == self.api and action in (None, q.get("action"))]


def page(rows: list, q: dict, per: int = 2) -> dict:
    p = int(q.get("p") or 1)
    return {"total_items": len(rows), "max_page_items": per, "cur_page": p,
            "data": rows[(p - 1) * per:p * per]}


class Handler(BaseHTTPRequestHandler):
    server: Portal

    def log_message(self, *a) -> None:
        pass

    def _send(self, code: int, body: bytes, ctype: str = "text/html") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _js(self, js) -> None:
        self._send(200, json.dumps({"js": js}).encode(), "application/json")

    def do_GET(self) -> None:
        srv = self.server
        parts = urlsplit(self.path)
        q = {k: v[0] for k, v in
             parse_qs(parts.query, keep_blank_values=True).items()}
        with srv.lock:
            srv.calls.append((parts.path, q,
                              {k.lower(): v for k, v in self.headers.items()}))
        if parts.path != srv.api:
            return self._send(404, b"not here")
        kind, action = q.get("type"), q.get("action")
        if (kind, action) == ("stb", "handshake"):
            with srv.lock:
                srv.handshakes += 1
                token = f"tok{srv.handshakes}"
                srv.tokens.add(token)
            return self._js({"token": token, "random": "r4nd"})
        auth = self.headers.get("Authorization", "")
        if auth[len("Bearer "):] not in srv.tokens:
            return self._send(401, b"")
        if srv.expire and action != "get_profile":
            srv.expire -= 1
            srv.tokens.clear()
            if srv.opts.get("expire_text"):
                return self._send(200, b"Authorization failed.")
            return self._send(401, b"")
        answer = getattr(self, f"_{kind}_{action}", None)
        if answer is None:
            return self._send(404, b"no such action")
        answer(q)

    def _stb_get_profile(self, q: dict) -> None:
        o = self.server.opts
        if o.get("blocked"):
            return self._js({"id": "7", "blocked": "1",
                             "block_msg": f"The box {MAC} is blocked"})
        if o.get("unknown_box"):
            return self._js({"id": None, "name": ""})
        prof = {"id": "7", "name": "MAG", "status": 0, "blocked": "0",
                "login": "", "default_timezone": "Europe/Stockholm",
                "expire_billing_date": "0000-00-00 00:00:00"}
        if o.get("max_connections"):
            prof["max_connections"] = o["max_connections"]
        self._js(prof)

    def _account_info_get_main_info(self, q: dict) -> None:
        self._js({"mac": MAC, "phone": "January 31, 2030, 12:00 am"})

    def _itv_get_genres(self, q: dict) -> None:
        self._js([{"id": "*", "title": "All"}, {"id": "1", "title": "News"},
                  {"id": "2", "title": "Sports"}])

    def _itv_get_all_channels(self, q: dict) -> None:
        if self.server.opts.get("no_all_channels"):
            return self._send(404, b"")
        self._js({"total_items": len(CHANNELS), "data": CHANNELS})

    def _itv_get_ordered_list(self, q: dict) -> None:
        g = q.get("genre")
        self._js(page([c for c in CHANNELS
                       if g == "*" or c["tv_genre_id"] == g], q))

    def _create_link(self, q: dict) -> None:
        srv = self.server
        if srv.opts.get("slow_link"):
            time.sleep(srv.opts["slow_link"])
        if srv.opts.get("fail_links"):
            return self._js({"cmd": "", "error": "nothing_to_play"})
        with srv.lock:
            srv.links += 1
            n = srv.links
        self._js({"id": n, "cmd": f"ffmpeg {srv.url}/play/{q['type']}?"
                  f"n={n}&mac={MAC}&play_token=PT{n}"})

    _itv_create_link = _create_link
    _vod_create_link = _create_link

    def _itv_get_short_epg(self, q: dict) -> None:
        self._js(PROGRAMMES[:int(q.get("size") or 8)])

    def _epg_get_simple_data_table(self, q: dict) -> None:
        if self.server.opts.get("no_epg_table"):
            return self._send(404, b"")
        self._js(page(PROGRAMMES, q))

    def _vod_get_categories(self, q: dict) -> None:
        self._js([{"id": "*", "title": "All"},
                  {"id": "10", "title": "Movies"}])

    def _vod_get_ordered_list(self, q: dict) -> None:
        if q.get("movie_id"):
            return self._js(page([], q))      # no seasons to drill into
        cat = q.get("category")
        self._js(page([f for f in FILMS
                       if cat == "*" or f["category_id"] == cat], q))

    def _series_get_categories(self, q: dict) -> None:
        if not self.server.opts.get("series_module"):
            return self._send(404, b"")
        self._js([{"id": "*", "title": "All"},
                  {"id": "20", "title": "Shows"}])

    def _series_get_ordered_list(self, q: dict) -> None:
        if self.server.opts.get("no_drill") and q.get("movie_id"):
            return self._send(404, b"")
        movie, season, episode = (q.get("movie_id"), q.get("season_id"),
                                  q.get("episode_id"))
        if not movie:
            return self._js(page([{
                "id": "500", "name": "New Show", "description": "Fresh.",
                "cmd": "", "category_id": "20", "year": "2024",
                "screenshot_uri": "http://img.example/500.jpg"}], q))
        rows: list = []
        if movie == "500" and season == "0":
            rows = [{"id": "501", "name": "Season 1", "is_season": True,
                     "season_number": "1"},
                    {"id": "502", "name": "Season 2", "is_season": True,
                     "series": [1, 2], "cmd": "season2cmd"}]
        elif movie == "500" and season == "501" and episode == "0":
            rows = [{"id": "601", "series_number": "1", "name": "Pilot",
                     "is_episode": True},
                    {"id": "602", "series_number": "2", "name": "Second",
                     "is_episode": True, "cmd": "/media/file_602.mkv"}]
        elif movie == "500" and season == "501" and episode == "601":
            rows = [{"id": "701", "cmd": "/media/file_701.mkv",
                     "is_file": True}]
        self._js(page(rows, q))


@pytest.fixture
def portal():
    servers: list[Portal] = []

    def make(api: str = "/stalker_portal/server/load.php", **opts) -> Portal:
        p = Portal(api, **opts)
        threading.Thread(target=p.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True).start()
        servers.append(p)
        return p

    yield make
    for p in servers:
        p.shutdown()
        p.server_close()


@pytest.fixture
def records():
    """Every message the app's logger formats while the test runs."""
    got: list[str] = []

    class Keep(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            got.append(record.getMessage())

    lg = logging.getLogger("dopeiptv")
    handler, level = Keep(logging.DEBUG), lg.level
    lg.addHandler(handler)
    lg.setLevel(logging.DEBUG)
    yield got
    lg.removeHandler(handler)
    lg.setLevel(level)


def client(p: Portal, path: str = "/stalker_portal/c/", **kw) -> StalkerClient:
    # Typed the way a user might: lower case with dashes.
    return StalkerClient(p.url + path, "00-1a-79-12-34-56", **kw)


# -- what XtreamClient returns for the same kinds of data ---------------------

XTREAM = {
    "get_live_categories": [
        {"category_id": "1", "category_name": "News", "parent_id": 0}],
    "get_live_streams": [
        {"num": 1, "name": "News HD", "stream_type": "live", "stream_id": 101,
         "stream_icon": "http://img/1.png", "epg_channel_id": "news.uk",
         "added": "1700000000", "is_adult": 0, "category_id": "1",
         "category_ids": [1], "custom_sid": "", "tv_archive": 0,
         "direct_source": "", "tv_archive_duration": 0}],
    "get_vod_streams": [
        {"num": 1, "name": "Film", "stream_type": "movie", "stream_id": 201,
         "stream_icon": "http://img/2.jpg", "rating": "7.4",
         "rating_5based": 3.7, "added": "1700000000", "is_adult": 0,
         "category_id": "10", "category_ids": [10],
         "container_extension": "mkv", "custom_sid": "", "direct_source": ""}],
    "get_series": [
        {"num": 1, "name": "Show", "series_id": 301,
         "cover": "http://img/3.jpg", "plot": "A show.", "cast": "A, B",
         "director": "D", "genre": "Drama", "releaseDate": "2019",
         "last_modified": "1700000000", "rating": "8", "rating_5based": 4.0,
         "backdrop_path": [], "youtube_trailer": "", "episode_run_time": "45",
         "category_id": "20", "category_ids": [20]}],
    "get_series_info": {
        "seasons": [{"season_number": 1, "name": "Season 1",
                     "episode_count": 1, "id": 1, "cover": "",
                     "cover_big": "", "overview": "", "air_date": ""}],
        "info": {"name": "Show", "cover": "http://img/3.jpg",
                 "plot": "A show.", "category_id": "20"},
        "episodes": {"1": [
            {"id": "1001", "episode_num": 1, "title": "Pilot",
             "container_extension": "mkv",
             "info": {"movie_image": "", "plot": "", "duration_secs": 2700,
                      "duration": "00:45:00", "name": "Pilot"},
             "custom_sid": "", "added": "1700000000", "season": 1,
             "direct_source": ""}]}},
    "get_vod_info": {
        "info": {"movie_image": "http://img/2.jpg", "name": "Film",
                 "o_name": "Film", "plot": "A film.", "cast": "A, B",
                 "director": "D", "genre": "Drama", "releasedate": "2020",
                 "duration": "01:35:00", "duration_secs": 5700,
                 "rating": "7.4"},
        "movie_data": {"stream_id": 201, "name": "Film",
                       "added": "1700000000", "category_id": "10",
                       "container_extension": "mkv", "custom_sid": "",
                       "direct_source": ""}},
    "get_short_epg": {"epg_listings": [
        {"id": "1", "epg_id": "1", "title": "TmV3cw==", "lang": "en",
         "start": "2026-11-08 10:00:00", "end": "2026-11-08 11:00:00",
         "description": "", "channel_id": "news.uk",
         "start_timestamp": "1794132000", "stop_timestamp": "1794135600"}]},
    None: {"user_info": {"username": "u", "message": "", "auth": 1,
                         "status": "Active", "exp_date": "1800000000",
                         "is_trial": "0"},
           "server_info": {"url": "h.tv", "port": "80",
                           "server_protocol": "http", "timezone": "UTC",
                           "timestamp_now": 1700000000}},
}


class _Reply:
    status_code = 200
    headers: dict = {}
    encoding = "utf-8"

    def __init__(self, data) -> None:
        self.content = json.dumps(data).encode()

    @property
    def text(self) -> str:
        return self.content.decode()

    def raise_for_status(self) -> None:
        pass

    def json(self, **kw):
        return json.loads(self.text, **kw)


@pytest.fixture(scope="module")
def xtream() -> XtreamClient:
    x = XtreamClient("http://h.tv", "user1", "pass1")
    x.session.get = lambda url, params=None, **kw: _Reply(  # type: ignore
        XTREAM[(params or {}).get("action")])
    return x


def assert_shape(got, ref, where: str = "") -> None:
    """*got* has every key *ref* has, each with a value of the same type,
    all the way down."""
    if isinstance(ref, dict):
        assert isinstance(got, dict), where
        missing = set(ref) - set(got)
        assert not missing, f"{where}: missing {sorted(missing)}"
        for k, v in ref.items():
            assert_shape(got[k], v, f"{where}.{k}")
    elif isinstance(ref, list):
        assert isinstance(got, list), where
        if ref and got:
            assert_shape(got[0], ref[0], f"{where}[0]")
    else:
        assert type(got) is type(ref), (
            f"{where}: {type(got).__name__} where Xtream has "
            f"{type(ref).__name__}")


# -- the portal's address -----------------------------------------------------

def test_the_api_is_found_beside_the_address_a_box_opens():
    assert portal_endpoints("http://h.tv:8080/c/") == [
        "http://h.tv:8080/portal.php", "http://h.tv:8080/server/load.php",
        "http://h.tv:8080/stalker_portal/portal.php",
        "http://h.tv:8080/stalker_portal/server/load.php"]
    assert portal_endpoints("h.tv/stalker_portal/c/index.html") == [
        "http://h.tv/stalker_portal/portal.php",
        "http://h.tv/stalker_portal/server/load.php",
        "http://h.tv/portal.php", "http://h.tv/server/load.php"]
    # An address that names the API is tried first, and not twice.
    assert portal_endpoints("http://h.tv/stalker_portal/server/load.php") == [
        "http://h.tv/stalker_portal/server/load.php",
        "http://h.tv/stalker_portal/portal.php",
        "http://h.tv/portal.php", "http://h.tv/server/load.php"]
    assert portal_endpoints("") == []


def test_mac_and_cmd_forms():
    for typed in ("00-1a-79-12-34-56", "001A79123456", " 00:1a:79:12:34:56 "):
        assert normalize_mac(typed) == MAC
    assert strip_cmd("ffmpeg http://x.tv/1.ts") == "http://x.tv/1.ts"
    assert strip_cmd(" auto  http://x.tv/1 ") == "http://x.tv/1"
    assert strip_cmd("http://x.tv/1.ts") == "http://x.tv/1.ts"
    assert strip_cmd("/media/1.mpg") == "/media/1.mpg"


def test_load_php_under_stalker_portal_is_found_and_remembered(portal):
    p = portal()
    c = client(p)
    assert c.authenticate()["user_info"]["auth"] == 1
    tried = [path for path, q, _h in p.calls if q.get("action") == "handshake"]
    assert tried == ["/stalker_portal/portal.php",
                     "/stalker_portal/server/load.php"]
    c.live_categories()
    c.vod_categories()
    assert all(path == p.api for path, _q, _h in p.calls[len(tried):])


def test_portal_php_at_the_root_is_found(portal):
    p = portal("/portal.php")
    c = client(p, "/c/")
    c.authenticate()
    assert [path for path, _q, _h in p.calls] == ["/portal.php"] * 2


def test_a_bare_host_finds_the_stalker_portal_beneath_it(portal):
    p = portal()
    c = client(p, "")
    c.authenticate()
    assert [path for path, q, _h in p.calls
            if q.get("action") == "handshake"] == [
        "/portal.php", "/server/load.php", "/stalker_portal/portal.php",
        "/stalker_portal/server/load.php"]


def test_no_portal_api_is_an_unexpected_response(portal):
    p = portal("/elsewhere.php")
    with pytest.raises(LocalizedError) as e:
        client(p).authenticate()
    assert e.value.key == "err_unexpected_response"


# -- looking like a box, logging in -------------------------------------------

def test_every_call_looks_like_a_mag_box(portal):
    p = portal()
    c = client(p)
    c.authenticate()
    c.live_categories()
    calls = [(q, h) for path, q, h in p.calls if path == p.api]
    for q, h in calls:
        assert "MAG200 stbapp" in h["user-agent"]
        assert h["x-user-agent"] == "Model: MAG250; Link: WiFi"
        assert h["cookie"] == f"mac={MAC}; stb_lang=en; timezone=UTC"
        assert h["referer"] == p.url + "/stalker_portal/c/"
        assert q["JsHttpRequest"] == "1-xml"
    (hq, hh), (pq, ph), (gq, gh) = calls
    assert hq["action"] == "handshake" and "authorization" not in hh
    assert pq["action"] == "get_profile" and ph["authorization"] == "Bearer tok1"
    assert pq["stb_type"] == "MAG250" and pq["auth_second_step"] == "1"
    assert json.loads(pq["metrics"])["random"] == "r4nd"
    assert gq["action"] == "get_genres" and gh["authorization"] == "Bearer tok1"


@pytest.mark.parametrize("opt", ["blocked", "unknown_box"])
def test_a_box_the_portal_refuses_is_a_login_error(portal, records, opt):
    p = portal(**{opt: True})
    c = client(p)
    with pytest.raises(LocalizedError) as e:
        c.authenticate()
    assert e.value.key == "err_wrong_login"
    # What app.py looks for to show the "check your playlist" dialog.
    assert "username or password" in str(e.value)
    with pytest.raises(LocalizedError):
        c.live_categories()
    assert c.account_info() == {}
    assert any("rejected" in r for r in records)
    assert not any(MAC in r or MAC.lower() in r for r in records)


@pytest.mark.parametrize("as_text", [False, True])
def test_an_expired_token_is_renewed_once(portal, as_text):
    p = portal(expire_text=as_text)
    c = client(p)
    c.authenticate()
    p.expire = 1
    assert [x["category_id"] for x in c.live_categories()] == ["1", "2"]
    assert p.handshakes == 2


def test_a_token_refused_after_renewal_is_a_login_error(portal):
    p = portal()
    c = client(p)
    c.authenticate()
    p.expire = 5
    with pytest.raises(LocalizedError) as e:
        c.live_categories()
    assert e.value.key == "err_wrong_login"
    assert p.handshakes == 2


def test_the_account_like_xtreams(portal, xtream):
    p = portal(max_connections="2")
    c = client(p)
    info = c.account_info()
    assert_shape(info, xtream.account_info())
    ui = info["user_info"]
    assert ui["status"] == "Active" and ui["auth"] == 1
    # get_main_info's "phone" holds the end date, in words.
    assert ui["exp_date"] == str(int(datetime(2030, 1, 31).timestamp()))
    assert ui["max_connections"] == "2" and c.max_connections == 2
    assert info["server_info"]["url"] == "127.0.0.1"


def test_an_account_that_says_no_connection_limit(portal):
    c = client(portal())
    assert "max_connections" not in c.account_info()["user_info"]
    assert c.max_connections is None


# -- channels -----------------------------------------------------------------

def test_channels_in_xtreams_shapes(portal, xtream):
    p = portal()
    c = client(p)
    cats = c.live_categories()
    assert cats == [{"category_id": "1", "category_name": "News",
                     "parent_id": 0},
                    {"category_id": "2", "category_name": "Sports",
                     "parent_id": 0}]
    assert_shape(cats[0], xtream.live_categories()[0])
    rows = c.live_streams()
    assert [r["stream_id"] for r in rows] == [101, 102, 103]
    for r in rows:
        assert_shape(r, xtream.live_streams()[0], r["name"])
    assert [r["stream_id"] for r in c.live_streams("1")] == [101, 103]
    news, sport, _direct = rows
    assert news["stream_icon"] == p.url + "/stalker_portal/misc/logos/320/101.png"
    assert sport["stream_icon"] == "http://img.example/102.png"
    assert news["epg_channel_id"] == "news.uk" and news["num"] == 1
    assert sport["is_adult"] == 1
    # An archive of three hours is a day's, as the window counts; it is
    # played by programme (archive_link), so timeshift_urls has nothing.
    assert (news["tv_archive"], news["tv_archive_duration"]) == (1, 1)
    assert (sport["tv_archive"], sport["tv_archive_duration"]) == (0, 0)
    assert c.timeshift_urls(101, datetime.now(), 30) == []


def test_without_get_all_channels_the_genres_pages_are_read(portal):
    p = portal(no_all_channels=True)
    c = client(p)
    assert [r["stream_id"] for r in c.live_streams("1")] == [101, 103]
    assert [r["stream_id"] for r in c.live_streams()] == [101, 102, 103]
    pages = [(q["genre"], q["p"]) for q in p.api_calls("get_ordered_list")]
    assert pages == [("1", "1"), ("*", "1"), ("*", "2")]
    assert c.live_url(102).startswith(p.url + "/play/itv?")


def test_live_url_asks_create_link_and_keeps_the_link_a_while(
        portal, monkeypatch):
    p = portal()
    c = client(p)
    c.live_streams()
    url = c.live_url(101, "m3u8")
    assert url.startswith(p.url + "/play/itv?") and "ffmpeg" not in url
    [q] = p.api_calls("create_link")
    assert q["type"] == "itv" and q["cmd"] == "ffmpeg http://localhost/ch/101_"
    assert c.live_url("101") == url                 # the player, then the cast
    assert len(p.api_calls("create_link")) == 1
    monkeypatch.setattr(StalkerClient, "LINK_CACHE_SECS", 0.0)
    assert c.live_url(101) not in ("", url)
    assert len(p.api_calls("create_link")) == 2


def test_a_failed_link_falls_back_to_a_real_address_only(portal):
    p = portal(fail_links=True)
    c = client(p)
    c.live_streams()
    assert c.live_url(103) == "http://cdn.example/direct.ts"
    # http://localhost/ch/101_ only names the channel: it plays nowhere.
    assert c.live_url(101) == ""


def test_an_unknown_channel_has_no_address_and_asks_nothing(portal):
    p = portal()
    c = client(p)
    c.live_streams()
    assert c.live_url(999) == ""
    assert p.api_calls("create_link") == []


def test_live_url_keeps_to_its_time_budget(portal, monkeypatch):
    monkeypatch.setattr(StalkerClient, "LINK_BUDGET_SECS", 1.0)
    p = portal(slow_link=3)
    c = client(p)
    c.live_streams()
    t0 = time.monotonic()
    assert c.live_url(101) == ""
    assert time.monotonic() - t0 < 2.5


def test_a_down_portal_still_shows_the_last_lineup(portal, tmp_path,
                                                   monkeypatch, records):
    p = portal()
    cache = str(tmp_path / "lists_p1.json")
    first = client(p, cache_path=cache).live_streams()
    p.shutdown()
    p.server_close()
    monkeypatch.setattr(StalkerClient, "LIST_CACHE_SECS", 0)
    again = client(p, cache_path=cache)
    assert [r["stream_id"] for r in again.live_streams()] == [
        r["stream_id"] for r in first]
    assert any(r.startswith("stalker itv_all @") and "network failure" in r
               for r in records)


# -- films and series ---------------------------------------------------------

def test_films_in_xtreams_shapes(portal, xtream):
    p = portal()
    c = client(p)
    assert [x["category_id"] for x in c.vod_categories()] == ["10"]
    films = c.vod_streams("10")
    assert [f["stream_id"] for f in films] == [201, 202, 204]   # 203: a series
    assert [q["p"] for q in p.api_calls("get_ordered_list")] == ["1", "2"]
    for f in films:
        assert_shape(f, xtream.vod_streams()[0], f["name"])
    one, two, _three = films
    assert one["stream_icon"] == p.url + "/stalker_portal/screenshots/201.jpg"
    assert one["added"] == str(calendar.timegm((2026, 1, 2, 3, 4, 5)))
    assert one["rating"] == "7.4" and one["rating_5based"] == 3.7
    assert two["container_extension"] == "mkv" and two["rating"] == ""
    info = c.vod_info(201)
    assert_shape(info, xtream.vod_info(201))
    assert info["info"]["plot"] == "A film."
    assert info["info"]["duration"] == "01:35:00"
    assert c.vod_info(999) == {}


def test_vod_url_asks_create_link(portal):
    p = portal()
    c = client(p)
    c.vod_streams("10")
    assert c.vod_url(201, "mkv").startswith(p.url + "/play/vod?")
    [q] = p.api_calls("create_link")
    assert (q["type"], q["cmd"], q["series"]) == ("vod", "/media/201.mpg", "")


def test_a_film_plays_next_session_before_its_list_loads(portal, tmp_path):
    p = portal()
    cache = str(tmp_path / "lists_p1.json")
    first = client(p, cache_path=cache)
    first.vod_streams("10")
    first.vod_url(202)
    later = client(p, cache_path=cache)
    assert later.vod_url(202)
    assert p.api_calls("create_link")[-1]["cmd"] == "/media/file_202.mkv"
    # A film nothing is known about: the name Ministra gives it by its id.
    assert later.vod_url(999)
    assert p.api_calls("create_link")[-1]["cmd"] == "/media/999.mpg"


def test_series_among_the_films(portal, xtream):
    p = portal()
    c = client(p)
    assert c.series_categories() == c.vod_categories()
    [show] = c.series_list("10")
    assert show["series_id"] == 203 and show["plot"] == "A show."
    assert_shape(show, xtream.series_list()[0])
    info = c.series_info(203)
    assert_shape(info, xtream.series_info(301))
    eps = info["episodes"]["1"]
    assert [e["episode_num"] for e in eps] == [1, 2, 3]
    assert all(e["id"].isdigit() and e["season"] == 1 for e in eps)
    assert len({e["id"] for e in eps}) == 3
    assert info["info"]["name"] == "Old Show"
    assert c.episode_url(eps[1]["id"]).startswith(p.url + "/play/vod?")
    q = p.api_calls("create_link")[-1]
    assert (q["cmd"], q["series"]) == ("/media/203.mpg", "2")


def test_a_series_module_with_seasons_episodes_and_files(portal, xtream):
    p = portal(series_module=True)
    c = client(p)
    assert [x["category_id"] for x in c.series_categories()] == ["20"]
    [show] = c.series_list("20")
    assert show["series_id"] == 500
    assert show["cover"] == "http://img.example/500.jpg"
    info = c.series_info(500)
    assert_shape(info, xtream.series_info(301))
    assert [s["season_number"] for s in info["seasons"]] == [1, 2]
    first, second = info["episodes"]["1"], info["episodes"]["2"]
    assert [(e["id"], e["title"]) for e in first] == [("601", "Pilot"),
                                                      ("602", "Second")]
    assert [e["episode_num"] for e in second] == [1, 2]
    # Ministra 5: the episode's file is looked up when it plays.
    assert c.episode_url("601")
    assert p.api_calls("create_link")[-1]["cmd"] == "/media/file_701.mkv"
    assert c.episode_url("602")
    assert p.api_calls("create_link")[-1]["cmd"] == "/media/file_602.mkv"
    assert c.episode_url(second[0]["id"])
    q = p.api_calls("create_link")[-1]
    assert (q["cmd"], q["series"]) == ("season2cmd", "1")


def test_an_episode_plays_next_session_without_opening_its_series(
        portal, tmp_path):
    p = portal()
    cache = str(tmp_path / "lists_p1.json")
    first = client(p, cache_path=cache)
    first.series_list("10")
    eid = first.series_info(203)["episodes"]["1"][2]["id"]
    later = client(p, cache_path=cache)
    assert later.episode_url(eid)
    q = p.api_calls("create_link")[-1]
    assert (q["cmd"], q["series"]) == ("/media/203.mpg", "3")


@pytest.mark.parametrize("opts", [{}, {"no_drill": True}])
def test_series_steps_a_portal_lacks_give_nothing(portal, opts):
    c = client(portal(series_module=True, **opts))
    assert c.series_info(999) == {"seasons": [], "info": {}, "episodes": {}}
    assert c.episode_url("12345") == ""


# -- programme guide ----------------------------------------------------------

def test_short_epg_like_xtreams(portal, xtream):
    p = portal()
    c = client(p)
    c.live_streams()
    got = c.short_epg(101, limit=2)
    assert len(got) == 2
    for e in got:
        assert_shape(e, xtream.short_epg(1)[0])
    now, nxt = got
    assert b64(now["title"]) == "Morning News"
    assert b64(nxt["title"]) == "Søndag & Co"
    assert now["channel_id"] == "news.uk"
    start, stop = epg_times(now)
    assert (int(start.timestamp()), int(stop.timestamp())) == (
        NOW - 1800, NOW + 1800)


def test_epg_table_reads_every_page_and_text_times_as_utc(portal):
    p = portal()
    c = client(p)
    table = c.epg_table(101)
    assert [b64(e["title"]) for e in table] == ["Morning News", "Søndag & Co",
                                               "Late"]
    pages = [q["p"] for q in p.api_calls("get_simple_data_table")]
    assert pages == ["1", "2"]
    start, _stop = epg_times(table[2])
    assert int(start.timestamp()) == NOW + 5400 - NOW % 60


def test_epg_table_falls_back_to_short_epg(portal):
    p = portal(no_epg_table=True)
    c = client(p)
    assert len(c.epg_table(101)) == 3
    assert p.api_calls("get_short_epg")[0]["size"] == "24"


def test_no_xmltv_and_the_guide_says_so_cleanly(portal):
    p = portal()
    c = client(p)
    assert c.xmltv() == b"" and c.epg_url == ""
    guide = XmltvGuide(c)
    with pytest.raises(RuntimeError, match="no EPG source"):
        guide._download()
    assert p.calls == []        # nothing asked of the portal


# -- secrets, interface, construction -----------------------------------------

def test_the_mac_and_the_token_stay_out_of_the_log(portal, records):
    p = portal()
    c = client(p)
    c.authenticate()
    p.shutdown()
    p.server_close()
    with pytest.raises(requests.ConnectionError):
        c.live_categories()
    text = "\n".join(records)
    assert "request failed" in text
    assert MAC not in text and "tok1" not in text
    assert c._redact(f"a {MAC} b {MAC.lower()} c 00%3A1A%3A79%3A12%3A34%3A56"
                     " d tok1") == "a *** b *** c *** d ***"
    # The links a portal hands out name the box and carry a play token.
    logged = redact_url(f"http://h.tv/play/live.php?mac={MAC}&stream=1"
                        "&play_token=PT9")
    assert MAC not in logged and "PT9" not in logged and "stream=1" in logged


def test_same_public_interface_as_xtream():
    def public(cls):
        return {n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                if not n.startswith("_")}
    missing = public(XtreamClient) - public(StalkerClient)
    assert not missing, f"StalkerClient missing: {sorted(missing)}"
    for name in public(XtreamClient):
        want = list(inspect.signature(getattr(XtreamClient, name)).parameters)
        got = list(inspect.signature(getattr(StalkerClient, name)).parameters)
        assert got == want, name
    c = StalkerClient("h.tv/c/", MAC)
    assert c.server == "http://h.tv/c" and c.session is not None
    assert c.username == "" and c.password == "" and c.max_connections is None


def test_make_client_builds_a_stalker_client(tmp_path, monkeypatch):
    monkeypatch.setattr(epg_mod, "_epg_cache_dir", lambda: tmp_path)
    c = make_client({"id": "p1", "kind": "stalker", "name": "Box",
                     "server": "http://h.tv/stalker_portal/c/",
                     "mac": "00:1a:79:12:34:56"})
    assert isinstance(c, StalkerClient)
    assert c.mac == MAC and c.server == "http://h.tv/stalker_portal/c"
    assert c._cache_path == str(tmp_path / "lists_p1.json")
    assert isinstance(make_client({"id": "p2", "server": "http://h.tv",
                                   "username": "u", "password": "p"}),
                      XtreamClient)


def test_the_client_module_leaves_stalker_unimported():
    code = ("import sys, dopeiptv.providers.client;"
            "print('dopeiptv.providers.stalker' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=60)
    assert out.stdout.strip() == "False", out.stderr[-500:]


def test_a_reconnect_asks_the_portal_for_a_new_link():
    from dopeiptv.providers.stalker import StalkerClient
    c = StalkerClient("http://portal.invalid/c/", "00:1A:79:12:34:56")
    c._links["l7"] = (time.monotonic(), "http://cdn.invalid/7?play_token=a")
    assert c._cached_link("l7")
    c.forget_link(7)
    assert c._cached_link("l7") == ""
