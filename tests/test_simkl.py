"""Simkl: the client id a build carries, the PIN sign-in, what every request
says about the app, the bodies of scrobbles, marks and the watchlist, and
the account's lists read back - against a stand-in for Simkl's API that
answers the way its documentation shows."""
import json
import threading
import time
import logging

import pytest

from dopeiptv.providers import simkl


class FakeSettings:
    """A settings file holding *stored* already (the client reads its
    sign-in once, when it is made)."""

    def __init__(self, **stored):
        self.d = dict(stored)

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
        self.token_answers = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(("GET", url, dict(params or {}), dict(headers or {}), None))
        path = url.split("api.simkl.com", 1)[1]
        if path == "/sync/all-items/":
            return Resp(200, ALL_ITEMS)
        return Resp(404, {"error": "nope"})

    def post(self, url, params=None, json=None, data=None, headers=None, timeout=None):
        self.calls.append(("POST", url, dict(params or {}), dict(headers or {}),
                           json if json is not None else data))
        path = url.split("api.simkl.com", 1)[1]
        if path == "/oauth2/device":
            return Resp(200, {"device_code": "DEV-SECRET", "user_code": "BDWP-HQPK",
                              "verification_uri": "https://simkl.com/pin",
                              "verification_uri_complete":
                                  "https://simkl.com/pin?user_code=BDWP-HQPK",
                              "expires_in": 900, "interval": 5})
        if path == "/oauth2/token":
            return self.token_answers.pop(0)
        if path == "/oauth2/revoke":
            return Resp(200, {})
        return Resp(201, {"added": 1})

    def posts(self):
        return [(c[1].split("api.simkl.com", 1)[1], c[4]) for c in self.calls if c[0] == "POST"]


def _signed_in(fake, **stored):
    return simkl.SimklClient(FakeSettings(**{simkl.TOKEN_KEY: "TOKEN-123", **stored}),
                             session=fake, client_id="CID")


@pytest.fixture
def client():
    fake = FakeSimkl()
    return _signed_in(fake), fake


def test_the_client_id_comes_with_the_build(monkeypatch):
    monkeypatch.delenv("DOPEIPTV_SIMKL_CLIENT_ID", raising=False)
    c = simkl.SimklClient(FakeSettings())
    if not simkl.bundled_client_id():       # a source checkout: none
        assert not c.available() and not c.is_connected()
    monkeypatch.setenv("DOPEIPTV_SIMKL_CLIENT_ID", " abc ")
    assert simkl.bundled_client_id() == "abc" and c.available()
    assert not c.is_connected()
    c = simkl.SimklClient(FakeSettings(**{simkl.TOKEN_KEY: "t"}))
    assert c.is_connected()
    c.disconnect()
    assert not c.is_connected() and simkl.TOKEN_KEY not in c.settings.d


TOKENS = {"access_token": "NEWTOKEN", "refresh_token": "simkl_rt_R", "token_type":
          "bearer", "expires_in": 604800, "scope": "media:read media:write"}


def test_the_pin_sign_in_is_auth_v2s_device_flow(monkeypatch):
    monkeypatch.setattr(simkl.time, "sleep", lambda _s: None)
    fake = FakeSimkl()
    c = simkl.SimklClient(FakeSettings(), session=fake, client_id="CID")
    data = c.start_pin()
    assert data["user_code"] == "BDWP-HQPK" and data["verification_url"] == "https://simkl.com/pin"
    assert data["verification_uri_complete"].endswith("user_code=BDWP-HQPK")
    assert "device_code" not in data            # it is polled with, never shown
    _m, url, _p, headers, body = fake.calls[0]
    assert url == "https://api.simkl.com/oauth2/device"
    assert body == {"client_id": "CID", "scope": "media:read media:write"}
    assert headers["User-Agent"].startswith("dopeIPTV/") and "Authorization" not in headers
    fake.token_answers = [Resp(400, {"error": "authorization_pending"}),
                          Resp(400, {"error": "slow_down"}),
                          Resp(200, TOKENS)]
    assert c.poll_pin("BDWP-HQPK") is None
    assert c.poll_pin("BDWP-HQPK") is None
    assert c.poll_pin("BDWP-HQPK")["access_token"] == "NEWTOKEN"
    assert c.settings.value(simkl.TOKEN_KEY) == "NEWTOKEN" and c.is_connected()
    assert c.settings.value(simkl.REFRESH_KEY) == "simkl_rt_R"
    assert int(c.settings.value(simkl.EXPIRES_KEY)) > time.time() + 6 * 86400
    _m, url, _p, _h, body = fake.calls[-1]
    assert url == "https://api.simkl.com/oauth2/token"
    assert body == {"grant_type": simkl.DEVICE_GRANT, "client_id": "CID",
                    "device_code": "DEV-SECRET"}
    # Ran out, a code it never handed out, a V1 client id.
    c.start_pin()
    fake.token_answers = [Resp(400, {"error": "expired_token"})]
    with pytest.raises(simkl.SimklAuthError) as e:
        c.poll_pin("BDWP-HQPK")
    assert e.value.key == "trakt_code_expired"
    with pytest.raises(simkl.SimklAuthError):
        c.poll_pin("NOPE-NOPE")
    c.start_pin()
    fake.token_answers = [Resp(401, {"error": "invalid_client", "error_description":
                                     "This client_id is not enabled for OAuth 2.0"})]
    with pytest.raises(simkl.SimklAuthError) as e:
        c.poll_pin("BDWP-HQPK")
    assert "not enabled for OAuth 2.0" in str(e.value)


def test_the_access_token_is_renewed_before_it_runs_out_and_on_a_401():
    fake = FakeSimkl()
    c = _signed_in(fake, **{simkl.REFRESH_KEY: "simkl_rt_R",
                            simkl.EXPIRES_KEY: int(time.time()) + 60})   # nearly out
    fake.token_answers = [Resp(200, dict(TOKENS, access_token="FRESH"))]
    c.watched_movies()
    refresh = [x for x in fake.calls if x[1].endswith("/oauth2/token")]
    assert refresh and refresh[0][4] == {"grant_type": "refresh_token", "client_id": "CID",
                                         "refresh_token": "simkl_rt_R"}
    assert c.settings.value(simkl.TOKEN_KEY) == "FRESH"
    gets = [x for x in fake.calls if x[0] == "GET"]
    assert gets[-1][3]["Authorization"] == "Bearer FRESH"
    # Refused (revoked from Simkl, or 180 days unused): forgotten, connect again.
    fake.token_answers = [Resp(400, {"error": "invalid_grant"})]
    assert c.refresh() is False and not c.is_connected()
    assert c.settings.value(simkl.REFRESH_KEY) is None


class _ThreadSettings(FakeSettings):
    """Notes every read and write of the sign-in made off the main thread
    (the window's)."""

    def __init__(self, **stored):
        super().__init__(**stored)
        self.off_main = []

    def _note(self, what, key):
        if key.startswith("simkl_") and threading.current_thread() is not threading.main_thread():
            self.off_main.append((what, key))

    def value(self, key, default=None):
        self._note("read", key)
        return super().value(key, default)

    def setValue(self, key, value):  # noqa: N802
        self._note("write", key)
        super().setValue(key, value)

    def remove(self, key):
        self._note("remove", key)
        super().remove(key)


def _on_a_worker(fn):
    out = {}

    def run():
        try:
            out["result"] = fn()
        except Exception as e:  # noqa: BLE001 - handed back to the test
            out["error"] = e
    t = threading.Thread(target=run)
    t.start()
    t.join(10)
    if "error" in out:
        raise out["error"]
    return out.get("result")


def test_a_renewal_on_a_worker_is_used_at_once_and_written_on_the_windows_thread():
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    keys = (simkl.TOKEN_KEY, simkl.REFRESH_KEY, simkl.EXPIRES_KEY)
    fake = FakeSimkl()
    s = _ThreadSettings(**{simkl.TOKEN_KEY: "OLD", simkl.REFRESH_KEY: "simkl_rt_R",
                           simkl.EXPIRES_KEY: int(time.time()) + 60})
    c = simkl.SimklClient(s, session=fake, client_id="CID")
    fake.token_answers = [Resp(200, dict(TOKENS, access_token="FRESH"))]
    _on_a_worker(c.watched_movies)
    gets = [x for x in fake.calls if x[0] == "GET"]
    assert gets[-1][3]["Authorization"] == "Bearer FRESH"        # the new one, at once
    assert c.access_token == "FRESH" and s.d[simkl.TOKEN_KEY] == "OLD"
    app.processEvents()                                           # the window's thread writes it
    assert s.d[simkl.TOKEN_KEY] == "FRESH" and s.d[simkl.REFRESH_KEY] == "simkl_rt_R"
    assert int(s.d[simkl.EXPIRES_KEY]) > time.time() + 6 * 86400
    # Refused on a worker: forgotten at once, and in the settings on the window's thread.
    fake.token_answers = [Resp(400, {"error": "invalid_grant"})]
    assert _on_a_worker(c.refresh) is False
    assert not c.is_connected() and simkl.TOKEN_KEY in s.d
    app.processEvents()
    assert not any(k in s.d for k in keys)
    assert s.off_main == []                                       # never read or written there
    # Signed out here while a renewal's write is still on its way: it brings nothing back.
    s = _ThreadSettings(**{simkl.TOKEN_KEY: "OLD", simkl.REFRESH_KEY: "simkl_rt_R"})
    c = simkl.SimklClient(s, session=fake, client_id="CID")
    fake.token_answers = [Resp(200, dict(TOKENS, access_token="LATE"))]
    assert _on_a_worker(c.refresh) is True
    c.disconnect()
    assert not c.is_connected() and not any(k in s.d for k in keys)
    app.processEvents()
    assert not any(k in s.d for k in keys) and s.off_main == []


def test_a_renewal_out_when_sign_out_is_pressed_brings_nothing_back():
    """Sign out while a worker's renewal waits for Simkl's answer: the
    answer that comes after signs nothing in again."""
    widgets = pytest.importorskip("PyQt6.QtWidgets")
    app = widgets.QApplication.instance() or widgets.QApplication([])
    keys = (simkl.TOKEN_KEY, simkl.REFRESH_KEY, simkl.EXPIRES_KEY)
    out, answered = threading.Event(), threading.Event()

    class Slow(FakeSimkl):
        def post(self, url, **kw):
            if url.endswith("/oauth2/token"):
                out.set()
                answered.wait(10)
            return super().post(url, **kw)

    fake = Slow()
    fake.token_answers = [Resp(200, dict(TOKENS, access_token="LATE"))]
    s = _ThreadSettings(**{simkl.TOKEN_KEY: "OLD", simkl.REFRESH_KEY: "simkl_rt_R",
                           simkl.EXPIRES_KEY: int(time.time()) + 60})
    c = simkl.SimklClient(s, session=fake, client_id="CID")
    worker = threading.Thread(target=c.refresh)
    worker.start()
    assert out.wait(10)
    c.disconnect()                           # Sign out, on the window's thread
    answered.set()
    worker.join(10)
    assert not c.is_connected() and c.access_token == ""
    app.processEvents()
    assert not any(k in s.d for k in keys) and s.off_main == []
    assert not simkl.SimklClient(s, session=fake, client_id="CID").is_connected()


def test_signing_out_revokes_the_grant():
    fake = FakeSimkl()
    c = _signed_in(fake, **{simkl.REFRESH_KEY: "simkl_rt_R"})
    c.disconnect()
    assert not c.is_connected() and c.settings.value(simkl.REFRESH_KEY) is None
    for _ in range(50):
        if any(x[1].endswith("/oauth2/revoke") for x in fake.calls):
            break
        time.sleep(0.02)
    revoke = [x for x in fake.calls if x[1].endswith("/oauth2/revoke")]
    assert revoke and revoke[0][4] == {"client_id": "CID", "token": "simkl_rt_R"}


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
        c.start_pin()
        fake.token_answers = [Resp(200, dict(TOKENS, access_token="SECRET-T",
                                             refresh_token="SECRET-R"))]
        c.poll_pin("BDWP-HQPK")
        c.scrobble_start(simkl.scrobble_movie(1, "x"), 0)
        c.watched_movies()
    finally:
        log.removeHandler(h)
        log.setLevel(was)
    assert lines and not any(s in x for x in lines
                             for s in ("SECRET-T", "SECRET-R", "DEV-SECRET", "TOKEN-123"))
