"""Fuzzing a Stalker / Ministra portal's answers (tests/fuzz_util.py): the
handshake, the profile, the account, genres and channels, films and series
page by page, a series' seasons and episodes, links, the short guide and
the archive - each answer's JSON of the wrong shape. The client raises
what its callers catch (a refused box, an answer that is no JSON, a failed
request) and hands on rows that are objects; the small readers it is
built from never raise at all."""
from __future__ import annotations

import random

from dopeiptv.core._lazy_requests import requests
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import stalker
from tests import fuzz_util as fz

NOW = 1_780_000_000
CHANNEL = {"id": "11", "name": "SVT1 HD", "number": "1", "cmd": "ffmpeg http://localhost/ch/11",
           "tv_genre_id": "1", "logo": "11.png", "xmltv_id": "svt1.se", "tv_archive": "1",
           "tv_archive_duration": "72", "censored": "0", "cmds": [{"url": "auto http://x/11"}]}
FILM = {"id": "21", "name": "Dune", "o_name": "Dune", "description": "Sand.", "actors": "A, B",
        "director": "V", "genres_str": "Sci-Fi", "year": "2021", "time": "155", "rating_imdb": "8.0",
        "screenshot_uri": "/screens/21.jpg", "added": "2026-05-01 10:00:00", "category_id": "5",
        "cmd": "/media/21.mpg", "censored": "0", "country": "US", "age": "12+"}
SHOW = {"id": "31", "name": "Show", "is_series": "1", "series": [1, 2, "3"], "category_id": "5",
        "cmd": "/media/31.mpg", "time": "45", "year": "2019"}
SEASON = {"id": "41", "name": "Season 2", "is_season": "1", "season_number": "2", "series": []}
EPISODE = {"id": "51", "name": "Pilot", "series_number": "1", "time": "44", "cmd": "/media/51.mpg",
           "screenshot_uri": "", "description": "First.", "added": "2026-05-01 10:00:00"}
PROGRAMME = {"id": "61", "real_id": "11_1779962400", "name": "Rapport", "descr": "Nyheter",
             "time": "2026-05-28 10:00:00", "time_to": "2026-05-28 10:30:00",
             "start_timestamp": 1779962400, "stop_timestamp": 1779964200, "duration": 1800,
             "mark_archive": 1, "cmd": ""}
ANSWERS = {
    "handshake": {"js": {"token": "T0K3N", "random": "r4nd"}},
    "get_profile": {"js": {"id": "1", "status": 0, "blocked": "0", "max_connections": 2, "login": "box",
                           "expire_billing_date": "2027-01-01 00:00:00", "default_timezone": "UTC"}},
    "get_main_info": {"js": {"phone": "February 1, 2027, 12:00 am", "end_date": "", "max_connections": "1"}},
    "get_genres": {"js": [{"id": "*", "title": "All"}, {"id": "1", "title": "News"}]},
    "get_categories": {"js": [{"id": "5", "title": "Films"}, {"id": 6, "name": "Kids"}]},
    "get_all_channels": {"js": {"data": [CHANNEL, dict(CHANNEL, id="12", cmd="", name="Two")]}},
    "get_ordered_list": {"js": {"total_items": 4, "max_page_items": 14,
                                "data": [FILM, SHOW, SEASON, EPISODE]}},
    "create_link": {"js": {"cmd": "ffmpeg http://real.example/stream/11.ts?token=x position:0 media_len:30"}},
    "get_short_epg": {"js": [PROGRAMME]},
    "get_simple_data_table": {"js": {"total_items": 1, "max_page_items": 10, "data": [PROGRAMME]}},
}


def _answers(rng: random.Random) -> dict:
    out = {}
    for action, body in ANSWERS.items():
        k = rng.random()
        if k < 0.5:
            out[action] = fz.json_body(rng, body)
        elif k < 0.55:
            out[action] = rng.choice((b"Authorization failed.", b"", b"<html>", b"null", b"[]"))
        else:
            out[action] = fz.to_json(body).encode()
    return out


def test_fuzz_stalker_portal():
    state: dict = {}

    def answer(asked: fz.Asked):
        body = state["bodies"].get(asked.query.get("action"), b'{"js": []}')
        if state["rng"].random() < 0.02:
            return state["rng"].choice((401, 404, 500)), body
        return body

    def target(arg: tuple) -> list:
        bodies, n = arg
        state["bodies"], state["rng"] = bodies, random.Random(n)
        c = stalker.StalkerClient("http://portal.example:8080/c/", "00:1A:79:12:34:56")
        c.SEASON_PAGES = c.EPG_PAGES = c.MAX_PAGES = 3
        out: list = []
        try:
            c.authenticate()
        except LocalizedError:
            pass
        assert isinstance(c.account_info(), dict)
        for call in (c.live_categories, c.live_streams, c.vod_categories, c.vod_streams,
                     c.series_categories, c.series_list):
            try:
                rows = call()
            except LocalizedError:
                continue
            assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows), call.__name__
            out.append(rows)
            for r in rows[:3]:
                if "series_id" in r:
                    info = c.series_info(r["series_id"])
                    assert isinstance(info, dict)
                    for eps in (info.get("episodes") or {}).values():
                        for ep in eps[:2]:
                            assert isinstance(c.episode_url(ep["id"]), str)
                if "stream_id" in r:
                    assert isinstance(c.vod_info(r["stream_id"]), dict)
                    url = (c.live_url if r.get("stream_type") == "live" else c.vod_url)(r["stream_id"])
                    assert isinstance(url, str)
        for call in (c.short_epg, c.epg_table):
            try:
                rows = call(11)
            except LocalizedError:
                continue
            assert all(isinstance(r, dict) for r in rows)
        try:
            assert isinstance(c.archive_table(11, 1), list)
            got = c.archive_link(11, 1779962500.0)
            assert got is None or isinstance(got, (str, tuple, dict))
        except LocalizedError:
            pass
        return out

    with fz.FakeNet(answer):
        fz.fuzz("stalker", target, lambda rng: (_answers(rng), rng.randrange(1 << 30)),
                allowed=(requests.RequestException, ValueError),
                seeds=[({k: fz.to_json(v).encode() for k, v in ANSWERS.items()}, 1)], min_runs=15)


def test_fuzz_stalker_readers():
    """The small readers every answer goes through: none raises, whatever
    the value."""
    def make(rng: random.Random) -> object:
        return fz.json_value(rng, rng.choice((CHANNEL, FILM, PROGRAMME, ["x", 1])))

    def target(v: object) -> tuple:
        row = v if isinstance(v, dict) else {"x": v}
        vals = list(row.values())[:6] + [v]
        for x in vals:
            stalker._text(x), stalker._int(x), stalker._float(x), stalker._flag(x), stalker._id(x)
            stalker._numbers(x), stalker._utc_stamp(x), stalker._expiry(x), stalker.strip_cmd(x)
            stalker.archive_address(x), stalker.archive_address({"cmd": x})
        stalker._rating(row), stalker._archive_days(row), stalker._is_episode(row)
        stalker._is_season(row, "31"), stalker.archive_cmds(row)
        rows = stalker._archive_rows([r for r in (row, PROGRAMME) if isinstance(r, dict)])
        stalker.pick_programme(rows, 1779962500.0)
        stalker.pick_programme(rows, 1779962500.0, 1779964000.0)
        return rows
    fz.fuzz("stalker-readers", target, make)


PORTALS = ("http://portal.example:8080/c/", "portal.example/stalker_portal/c/index.html",
           "https://p.example/portal.php", "http://p.example/server/load.php", "http://[::1]:80/c",
           "http://p.example:99999/c/")


def test_fuzz_portal_addresses_and_macs():
    def target(text: str) -> list:
        got = stalker.portal_endpoints(text)
        for ep in got:
            stalker.api_base(ep)
        stalker.normalize_mac(text)
        stalker._direct_url(text)
        stalker._container(text)
        stalker._MAG_OPTIONS.sub("", text)
        return got
    fz.fuzz("portal-address", target, lambda rng: fz.mutate_text(rng, rng.choice(PORTALS), tokens=(
        "/c", "/stalker_portal", "/portal.php", "://", ":", "[", "]", "@", "%", " ", "position:0")),
        seeds=PORTALS)


def test_trailing_options_take_linear_time():
    for piece in (" ", " a:b", " position:0", "\t:", " x" * 2):
        fz.linear(lambda s: stalker._MAG_OPTIONS.sub("", s), lambda n, p=piece: "http://x/a" + p * n, 20000)
