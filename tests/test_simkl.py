"""Simkl: the client id a build carries, the PIN sign-in, what every request
says about the app, the bodies of scrobbles, marks and the watchlist, and
the account's lists read back - against a stand-in for Simkl's API that
answers the way its documentation shows."""
import json
import logging

import pytest

from dopeiptv.providers import simkl


class FakeSettings:
    def __init__(self):
        self.d = {}

    def value(self, key, default=None):
        return self.d.get(key, default)

    def setValue(self, key, value):  # noqa: N802
        self.d[key] = value

    def remove(self, key):
        self.d.pop(key, None)


class Resp:
    def __init__(self, status=200, data=None):
        self.status_code = status
        self._data = data
        self.content = json.dumps(data).encode() if data is not None else b""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


ALL_ITEMS = {
    "movies": [
        {"status": "completed", "last_watched_at": "2026-10-02T20:00:00Z",
         "movie": {"title": "Arrival", "year": 2016, "ids": {"simkl": 1, "tmdb": "329865"}}},
        {"status": "plantowatch", "added_to_watchlist_at": "2026-09-01T10:00:00Z",
         "movie": {"title": "Dune", "year": 2021, "ids": {"tmdb": 438631}}},
        {"status": "completed", "movie": {"title": "No id", "ids": {"simkl": 5}}},
    ],
    "shows": [
        {"status": "watching", "last_watched_at": "2026-11-01T21:00:00Z",
         "show": {"title": "Severance", "year": 2022, "ids": {"tmdb": "95396"}},
         "seasons": [{"number": 1, "episodes": [
             {"number": 1, "watched_at": "2026-10-30T20:00:00Z"},
             {"number": 2, "watched_at": "2026-10-31T20:00:00Z"},
             {"number": 3}]}]},
        {"status": "completed", "last_watched_at": "2026-05-01T21:00:00Z",
         "show": {"title": "Chernobyl", "year": 2019, "ids": {"tmdb": 87108}},
         "seasons": [{"number": 1, "episodes": [{"number": 1}, {"number": 2}]}]},
        {"status": "plantowatch", "show": {"title": "Shogun", "ids": {"tmdb": 126308}},
         "seasons": [{"number": 1, "episodes": [{"number": 1}]}]},
    ],
    "anime": [
        {"status": "watching", "last_watched_at": "2026-11-02T21:00:00Z",
         "show": {"title": "Frieren", "ids": {"tmdb": 209867}},
         "seasons": [{"number": 1, "episodes": [{"number": 7, "watched_at": "x"}]}]},
    ],
}


class FakeSimkl:
    """requests' get/post, answering as Simkl does and remembering what was
    asked."""

    def __init__(self):
        self.calls = []
        self.pin_answers = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {}), dict(headers or {}), None))
        path = url.split("api.simkl.com", 1)[1]
        if path == "/oauth/pin":
            return Resp(200, {"result": "OK", "device_code": "DEV", "user_code": "AB12C",
                              "verification_url": "https://simkl.com/pin/",
                              "expires_in": 900, "interval": 5})
        if path.startswith("/oauth/pin/"):
            return self.pin_answers.pop(0)
        if path == "/sync/all-items/":
            return Resp(200, ALL_ITEMS)
        return Resp(404, {"error": "nope"})

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, dict(params or {}), dict(headers or {}), json))
        return Resp(201, {"added": 1})

    def posts(self):
        return [(c[1].split("api.simkl.com", 1)[1], c[4]) for c in self.calls if c[0] == "POST"]


@pytest.fixture
def client():
    fake = FakeSimkl()
    c = simkl.SimklClient(FakeSettings(), session=fake, client_id="CID")
    c.settings.setValue(simkl.TOKEN_KEY, "TOKEN-123")
    return c, fake


def test_the_client_id_comes_with_the_build(monkeypatch):
    monkeypatch.delenv("DOPEIPTV_SIMKL_CLIENT_ID", raising=False)
    c = simkl.SimklClient(FakeSettings())
    if not simkl.bundled_client_id():       # a source checkout: none
        assert not c.available() and not c.is_connected()
    monkeypatch.setenv("DOPEIPTV_SIMKL_CLIENT_ID", " abc ")
    assert simkl.bundled_client_id() == "abc" and c.available()
    assert not c.is_connected()
    c.settings.setValue(simkl.TOKEN_KEY, "t")
    assert c.is_connected()
    c.disconnect()
    assert not c.is_connected() and simkl.TOKEN_KEY not in c.settings.d


def test_the_pin_sign_in(monkeypatch):
    monkeypatch.setattr(simkl.time, "sleep", lambda _s: None)
    fake = FakeSimkl()
    c = simkl.SimklClient(FakeSettings(), session=fake, client_id="CID")
    data = c.start_pin()
    assert data["user_code"] == "AB12C" and data["verification_url"] == "https://simkl.com/pin/"
    _m, url, params, headers, _b = fake.calls[0]
    assert url == "https://api.simkl.com/oauth/pin"
    # Every request names the app the three ways Simkl asks.
    assert params == {"client_id": "CID", "app-name": "dopeiptv",
                      "app-version": simkl.VERSION}
    assert headers["simkl-api-key"] == "CID" and headers["User-Agent"].startswith("dopeIPTV/")
    assert "Authorization" not in headers
    fake.pin_answers = [Resp(200, {"result": "KO", "message": "Authorization pending"}),
                        Resp(200, {"result": "KO", "message": "Slow down"}),
                        Resp(200, {"result": "OK", "access_token": "NEWTOKEN"})]
    assert c.poll_pin("AB12C") is None
    assert c.poll_pin("AB12C") is None
    assert c.poll_pin("AB12C")["result"] == "OK"
    assert c.settings.value(simkl.TOKEN_KEY) == "NEWTOKEN" and c.is_connected()
    assert fake.calls[-1][1] == "https://api.simkl.com/oauth/pin/AB12C"
    for answer, key in ((Resp(404), "trakt_code_expired"),
                        (Resp(200, {"result": "KO", "message": "Code expired"}),
                         "trakt_code_expired")):
        fake.pin_answers = [answer]
        with pytest.raises(simkl.SimklAuthError) as e:
            c.poll_pin("AB12C")
        assert e.value.key == key
    fake.pin_answers = [Resp(200, {"result": "KO", "message": "Wrong client"})]
    with pytest.raises(simkl.SimklAuthError) as e:
        c.poll_pin("AB12C")
    assert str(e.value) == "Wrong client"


def test_scrobbles_and_marks_say_what_simkl_finds_them_by(client):
    c, fake = client
    film = simkl.scrobble_movie(329865, "Arrival", 2016)
    assert film == {"movie": {"title": "Arrival", "year": 2016, "ids": {"tmdb": 329865}}}
    ep = simkl.scrobble_episode(None, "Severance", 2, 4)
    assert ep == {"show": {"title": "Severance"}, "episode": {"season": 2, "number": 4}}
    c.scrobble_start(film, 0)
    c.scrobble_pause(film, 41.237)
    c.scrobble_stop(ep, 180)
    c.add_movie_history(329865)
    c.remove_episode_history(95396, 1, 3)
    c.add_show_history(87108)
    c.add_movie_watchlist(438631)
    c.add_show_watchlist(126308)
    assert fake.posts() == [
        ("/scrobble/start", {**film, "progress": 0.0}),
        ("/scrobble/pause", {**film, "progress": 41.24}),
        ("/scrobble/stop", {**ep, "progress": 100.0}),
        ("/sync/history", {"movies": [{"ids": {"tmdb": 329865}}]}),
        ("/sync/history/remove", {"shows": [{"ids": {"tmdb": 95396}, "seasons": [
            {"number": 1, "episodes": [{"number": 3}]}]}]}),
        ("/sync/history", {"shows": [{"ids": {"tmdb": 87108}}]}),
        ("/sync/add-to-list", {"movies": [{"ids": {"tmdb": 438631}, "to": "plantowatch"}]}),
        ("/sync/add-to-list", {"shows": [{"ids": {"tmdb": 126308}, "to": "plantowatch"}]}),
    ]
    for _m, _u, params, headers, _b in fake.calls:
        assert params["client_id"] == "CID" and params["app-name"] == "dopeiptv"
        assert headers["Authorization"] == "Bearer TOKEN-123"


def test_the_account_read_back(client):
    c, fake = client
    titles: dict = {}
    assert c.watched_movies(titles) == [329865]
    assert titles == {329865: "Arrival (2016)"}
    show_titles: dict = {}
    shows = c.watched_shows(show_titles)
    # Watched times count; so does every episode of a completed show; a
    # planned show and an episode never watched do not.
    assert shows == {95396: [[1, 1], [1, 2]], 87108: [[1, 1], [1, 2]], 209867: [[1, 7]]}
    assert show_titles[95396] == "Severance (2022)"
    assert c.watchlist_movies() == [438631] and c.watchlist_shows() == [126308]
    # One answer for all four: Simkl asks not to be read over and over.
    gets = [x for x in fake.calls if x[0] == "GET"]
    assert len(gets) == 1
    assert gets[0][2]["extended"] == "full" and gets[0][2]["episode_watched_at"] == "yes"
    assert [r["title"] for r in c.watchlist()] == ["Dune (2021)", "Shogun"]
    assert [r["title"] for r in c.history()] == [
        "Frieren", "Severance (2022)", "Arrival (2016)", "Chernobyl (2019)"]
    # A change is read afresh next time.
    c.add_movie_history(1)
    c.watched_movies()
    assert len([x for x in fake.calls if x[0] == "GET"]) == 2


def test_off_the_watchlist_only_what_is_only_planned(client):
    c, fake = client
    c.remove_movie_watchlist(329865)        # watched, not planned: kept
    c.remove_show_watchlist(95396)
    assert fake.posts() == []
    c.remove_movie_watchlist(438631)
    c.remove_show_watchlist(126308)
    assert fake.posts() == [("/sync/history/remove", {"movies": [{"ids": {"tmdb": 438631}}]}),
                            ("/sync/history/remove", {"shows": [{"ids": {"tmdb": 126308}}]})]


def test_the_token_is_never_logged(client):
    from dopeiptv.core.log import log
    c, fake = client
    lines = []

    class Grab(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    h = Grab(logging.DEBUG)
    was = log.level
    log.setLevel(logging.DEBUG)
    log.addHandler(h)
    try:
        fake.pin_answers = [Resp(200, {"result": "OK", "access_token": "SECRET-T"})]
        c.poll_pin("AB12C")
        c.scrobble_start(simkl.scrobble_movie(1, "x"), 0)
        c.watched_movies()
    finally:
        log.removeHandler(h)
        log.setLevel(was)
    assert lines and not any("SECRET-T" in x or "TOKEN-123" in x for x in lines)
