"""Fuzzing Xtream Codes answers (tests/fuzz_util.py): player_api.php's
JSON of the wrong shape - lists of non-objects, nulls, wrong types, huge
or odd numbers, nesting deeper than Python reads - through the client
(authenticate, the account, every list, series_info, vod_info, the short
guide) and on into what reads its rows without Qt: a series' seasons and
episodes (core/series_view.py), a film's year, length and rating
(core/film_view.py), the guide's times and base64 text, a pasted link."""
from __future__ import annotations

import base64
import random

from dopeiptv.core import connections, film_view, series_view
from dopeiptv.core._lazy_requests import requests
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import client as xc
from tests import fuzz_util as fz

USER_INFO = {"user_info": {"username": "u", "password": "p", "auth": 1, "status": "Active",
                           "exp_date": "1790000000", "is_trial": "0", "active_cons": "1",
                           "max_connections": "2", "created_at": "1700000000",
                           "allowed_output_formats": ["m3u8", "ts"]},
             "server_info": {"url": "x.example", "port": "80", "timezone": "Europe/Stockholm",
                             "timestamp_now": 1780000000, "time_now": "2026-05-28 10:00:00"}}
CATEGORIES = [{"category_id": "1", "category_name": "Sverige", "parent_id": 0},
              {"category_id": 2, "category_name": "News | HD", "parent_id": "0"}]
LIVE = [{"num": 1, "name": "SVT1 HD", "stream_type": "live", "stream_id": 101,
         "stream_icon": "http://logo.example/1.png", "epg_channel_id": "svt1.se", "added": "1700000000",
         "category_id": "1", "category_ids": [1], "custom_sid": "", "tv_archive": 1,
         "direct_source": "", "tv_archive_duration": "7"},
        {"num": "2", "name": "CNN", "stream_id": "102", "category_id": "2", "tv_archive": 0}]
VOD = [{"num": 1, "name": "Dune (2021)", "stream_type": "movie", "stream_id": 201, "stream_icon": "",
        "rating": "7.9", "rating_5based": 3.95, "added": "1779000000", "category_id": "5",
        "container_extension": "mkv", "year": "2021"}]
SERIES = [{"num": 1, "name": "Show", "series_id": 301, "cover": "", "plot": "x", "cast": "", "director": "",
           "genre": "Drama", "releaseDate": "2019-01-01", "last_modified": "1779000000", "rating": "8",
           "rating_5based": 4, "backdrop_path": ["http://b.example/1.jpg"], "category_id": "7"}]
SERIES_INFO = {
    "seasons": [{"season_number": 1, "name": "Season 1", "episode_count": 2}],
    "info": {"name": "Show", "cover": "", "plot": "A show.", "releaseDate": "2019", "rating": "8.1",
             "backdrop_path": ["http://b.example/1.jpg"], "episode_run_time": "45"},
    "episodes": {"1": [{"id": "1001", "episode_num": 1, "title": "Show - S01E01 - Pilot",
                        "container_extension": "mkv", "season": 1,
                        "info": {"duration_secs": 2700, "duration": "00:45:00", "plot": "First.",
                                 "movie_image": "http://s.example/1.jpg"}},
                       {"id": 1002, "episode_num": "2", "title": "Episode 2", "season": "1",
                        "info": {"duration": "45 min"}}],
                 "0": [{"id": "1100", "episode_num": 1, "title": "Special", "info": []}]},
}
VOD_INFO = {"info": {"name": "Dune", "plot": "Sand.", "duration_secs": 9300, "duration": "02:35:00",
                     "rating": "7.9", "releasedate": "2021-09-15", "movie_image": "", "genre": "Sci-Fi"},
            "movie_data": {"stream_id": 201, "name": "Dune", "added": "1779000000", "category_id": "5",
                           "container_extension": "mkv"}}
EPG = {"epg_listings": [{"id": "1", "epg_id": "1", "title": base64.b64encode("Rapport".encode()).decode(),
                         "lang": "sv", "start": "2026-05-28 10:00:00", "end": "2026-05-28 10:30:00",
                         "description": base64.b64encode("<b>Nyheter</b> &amp; väder".encode()).decode(),
                         "channel_id": "svt1.se", "start_timestamp": "1779962400",
                         "stop_timestamp": "1779964200"}]}
ANSWERS = {None: USER_INFO, "get_live_categories": CATEGORIES, "get_vod_categories": CATEGORIES,
           "get_series_categories": CATEGORIES, "get_live_streams": LIVE, "get_vod_streams": VOD,
           "get_series": SERIES, "get_series_info": SERIES_INFO, "get_vod_info": VOD_INFO,
           "get_short_epg": EPG, "get_simple_data_table": EPG}


def _answers(rng: random.Random) -> dict:
    """One mutated answer per action, the others as they are."""
    out = {}
    for action, body in ANSWERS.items():
        out[action] = fz.json_body(rng, body) if rng.random() < 0.6 else fz.to_json(body).encode()
    return out


def test_fuzz_xtream_client():
    """Whatever player_api.php answers: the client raises what its callers
    catch (a refused login, an answer that is no list, a request that
    failed, JSON that is none) and hands on nothing but what it promises -
    rows that are objects, an info that is an object."""
    state: dict = {}

    def answer(asked: fz.Asked):
        if asked.path.endswith("/xmltv.php"):
            return 404, b""
        return state["bodies"].get(asked.query.get("action"), b"[]")

    def target(bodies: dict) -> list:
        state["bodies"] = bodies
        c = xc.XtreamClient("http://xtream.example:8080", "u", "p")
        out: list = []
        try:
            c.authenticate()
        except (LocalizedError, ValueError):
            pass
        info = c.account_info()
        assert isinstance(info, dict)
        connections.numbers(info.get("user_info") if isinstance(info.get("user_info"), dict) else {})
        for call in (c.live_categories, c.live_streams, c.vod_categories, c.vod_streams,
                     c.series_categories, c.series_list):
            try:
                rows = call()
            except LocalizedError:
                continue
            assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows), call.__name__
            out.append(rows)
        for call, arg in ((c.series_info, 301), (c.vod_info, 201)):
            got = call(arg)
            assert isinstance(got, dict), f"{call.__name__} gave a {type(got).__name__}"
            out.append(got)
        for call in (c.short_epg, c.epg_table):
            rows = call(101)
            assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows), call.__name__
            for r in rows:
                xc.epg_times(r)
                xc.b64(r.get("title"))
                xc.b64(r.get("description"))
        return out

    with fz.FakeNet(answer):
        fz.fuzz("xtream", target, _answers, allowed=(requests.RequestException, ValueError),
                seeds=[{k: fz.to_json(v).encode() for k, v in ANSWERS.items()}], min_runs=20)


def test_fuzz_series_info_as_the_page_reads_it():
    """A series_info answer of any shape through core/series_view.py - its
    seasons, the watching order, what is next, each card's words."""
    def target(info: object) -> list:
        episodes = info.get("episodes") if isinstance(info, dict) else None
        seasons = series_view.seasons(episodes)
        order = series_view.flow(seasons)
        series_view.flow(seasons, specials=True)
        nxt = series_view.next_up(order, lambda ep: series_view.episode_number(ep) == 1,
                                  lambda ep: series_view.Progress(120.0, 2700.0, 5))
        series_view.opening_season(seasons, nxt, (1, "1001"), order[0] if order else None)
        meta = info.get("info") if isinstance(info, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        series_view.year_of(meta.get("releaseDate"), meta.get("year"))
        series_view.rating_of(meta.get("rating"), meta.get("rating_5based"))
        series_view.backdrop_of(meta.get("backdrop_path"), meta.get("cover"))
        for ep in order[:50]:
            series_view.episode_title(ep, str(meta.get("name") or ""))
            series_view.plot_of(ep)
            series_view.minutes_of(ep)
            series_view.still_of(ep)
            series_view.episode_key(ep)
        return [s.episodes for s in seasons]
    fz.fuzz("series-info", target, lambda rng: fz.json_value(rng, SERIES_INFO), seeds=[SERIES_INFO])


def test_fuzz_film_rows_as_the_movies_place_reads_them():
    def target(arg: tuple) -> tuple:
        row, info = arg
        row = row if isinstance(row, dict) else {}
        more = info.get("info") if isinstance(info, dict) else info
        return (film_view.key_of(row), film_view.is_movie(row), film_view.year_of(row, more),
                film_view.minutes_of(row, more), film_view.rating_of(row, more),
                film_view.plot_of(more, row), film_view.is_new(row, 1_780_000_000.0),
                film_view.primary(100.0, 0.0, film_view.minutes_of(row, more)))
    fz.fuzz("film-rows", target, lambda rng: (fz.json_value(rng, VOD[0]), fz.json_value(rng, VOD_INFO)),
            seeds=[(VOD[0], VOD_INFO)])


def test_fuzz_list_rows_and_epg_helpers():
    def rows(data: object) -> object:
        got = xc.list_rows(data)
        assert got is None or all(isinstance(r, dict) for r in got)
        return got
    fz.fuzz("list-rows", rows, lambda rng: fz.json_value(rng, rng.choice((LIVE, CATEGORIES, USER_INFO))))

    def times(entry: object) -> object:
        return xc.epg_times(entry if isinstance(entry, dict) else {})
    fz.fuzz("epg-times", times, lambda rng: fz.json_value(rng, EPG["epg_listings"][0]))

    def b64(text: object) -> str:
        out = xc.b64(text)  # type: ignore[arg-type]
        assert isinstance(out, str)
        return out
    texts = [base64.b64encode(s.encode()).decode() for s in ("<p>a</p>", "plain", "&lt;b&gt;")]
    fz.fuzz("xtream-b64", b64, lambda rng: fz.mutate_text(rng, rng.choice(texts), 2)
            if rng.random() < 0.8 else fz.json_value(rng, ["x"]), seeds=texts)


LINKS = ("http://host.example:8080/get.php?username=U&password=P&type=m3u_plus",
         "http://host.example/player_api.php?username=U&password=P", "http://host.example:80/live/U/P/1.ts",
         "https://host.example/U/P/123", "http://host.example:9191/output/m3u", "http://old.hosthttp://new.host/get.php?username=a&password=b",
         "host.example", "http://[::1]:80/live/a/b/1.ts")


def test_fuzz_pasted_links():
    def target(text: str) -> object:
        got = xc.detect_provider_link(text)
        assert got is None or (len(got) == 4 and got[0] in ("xtream", "m3u"))
        xc.parse_xtream_url(text)
        xc.dropped_playlist([text, text + ".m3u"])
        return got
    fz.fuzz("provider-link", target, lambda rng: fz.mutate_text(rng, rng.choice(LINKS), tokens=(
        "://", "http://", "?", "&", "=", "/", "@", ":", "[", "]", "%", "username=", "password=")), seeds=LINKS)


def test_episode_titles_take_linear_time():
    for piece in ("-", " -", ": ", "|*", "S01E01 "):
        fz.linear(lambda ep: series_view.episode_title(ep, "Show"),
                  lambda n, p=piece: {"title": "a" + p * n + "b"}, 2000)
