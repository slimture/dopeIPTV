"""Internet radio's directory client (providers/radio_browser.py): the
servers from the DNS round robin, the next one tried when one does not
answer, the stations and lists as the window shows them, and nothing about
the person in a request. Against a fake radio-browser.info on the loopback
(tests/fake_radio_browser.py)."""
from __future__ import annotations

import socket

import pytest

from dopeiptv import VERSION
from dopeiptv.providers import radio_browser as rb
from tests.fake_radio_browser import FakeRadioBrowser


@pytest.fixture(autouse=True)
def _local_is_direct(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")


@pytest.fixture
def fake():
    srv = FakeRadioBrowser()
    yield srv
    srv.close()


def _dead_host() -> str:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"127.0.0.1:{port}"


def test_the_servers_come_from_the_dns_round_robin_each_once():
    infos = [(socket.AF_INET, 1, 6, "", ("192.0.2.1", 443)),
             (socket.AF_INET6, 1, 6, "", ("2001:db8::1", 443, 0, 0)),
             (socket.AF_INET, 1, 6, "", ("192.0.2.2", 443)),
             (socket.AF_INET, 1, 6, "", ("192.0.2.3", 443))]
    names = {"192.0.2.1": "de1.api.radio-browser.info.",
             "2001:db8::1": "de1.api.radio-browser.info",
             "192.0.2.2": "NL1.api.radio-browser.info",
             "192.0.2.3": "evil.example.com"}

    def resolve(host, *a):
        assert host == rb.DNS_NAME
        return infos

    def reverse(ip):
        return (names[ip], [], [ip])

    assert rb.discover_servers(resolve, reverse) == [
        "de1.api.radio-browser.info", "nl1.api.radio-browser.info"]

    def fails(*a):
        raise socket.gaierror("no network")
    assert rb.discover_servers(fails, reverse) == []


def test_without_a_server_list_the_round_robin_name_itself_answers():
    client = rb.RadioBrowser(discover=lambda: [])
    assert client.servers() == [rb.DNS_NAME]


def test_stations_are_kept_playable_and_each_once():
    rows = rb.stations([
        {"stationuuid": "a", "name": "  A  station ", "url": "http://x/a",
         "url_resolved": "https://x/a.mp3", "favicon": "javascript:alert(1)",
         "tags": "pop, rock,,jazz", "countrycode": "se", "bitrate": "128", "hls": "1"},
        {"stationuuid": "a", "name": "again", "url": "http://x/a"},
        {"stationuuid": "b", "url": "file:///etc/passwd"},
        {"stationuuid": "", "url": "http://x/c"},
        "not a station"])
    assert len(rows) == 1
    st = rows[0]
    assert st["url"] == "https://x/a.mp3" and st["name"] == "A station"
    assert st["stream_icon"] == ""                      # only http(s) logos
    assert st["tags"] == ["pop", "rock", "jazz"]
    assert st["countrycode"] == "SE" and st["bitrate"] == 128 and st["hls"] is True
    assert st["_kind"] == "radio" and st["_key"] == "radio:a"


def test_countries_languages_and_genres_become_rows_to_open():
    countries = rb.groups(rb.COUNTRY, [
        {"name": "Sweden", "iso_3166_1": "SE", "stationcount": 3},
        {"name": "Nowhere", "iso_3166_1": "XX", "stationcount": 0},
        {"name": "Broken", "iso_3166_1": "S", "stationcount": 4}])
    assert countries == [{"name": "Sweden", "_kind": "radiogroup", "_group": ["country", "SE"],
                          "_count": 3, "_key": "radiogroup:country:SE"}]
    langs = rb.groups(rb.LANGUAGE, [{"name": "swedish", "stationcount": 2}])
    assert langs[0]["name"] == "Swedish" and langs[0]["_group"] == ["language", "swedish"]


def test_a_server_that_does_not_answer_hands_over_to_the_next(fake):
    down = FakeRadioBrowser(status=503)
    try:
        client = rb.RadioBrowser([_dead_host(), down.host, fake.host], scheme="http")
        top = client.top(rb.TOP_VOTED)
        assert [s["stationuuid"] for s in top] == ["u-jazz", "u-rock", "u-news"]
        assert down.paths() == ["/json/stations/topvote"]
        # The one that answered is asked first from then on.
        client.top(rb.TOP_CLICKED)
        assert down.paths() == ["/json/stations/topvote"]
        assert fake.paths() == ["/json/stations/topvote", "/json/stations/topclick"]
    finally:
        down.close()


def test_no_server_answering_is_an_error_not_an_empty_list():
    client = rb.RadioBrowser([_dead_host()], scheme="http", timeout=2)
    with pytest.raises(rb.RadioError):
        client.top()


def test_a_refused_request_is_not_retried_elsewhere(fake):
    gone = FakeRadioBrowser(status=404)
    try:
        client = rb.RadioBrowser([gone.host, fake.host], scheme="http")
        with pytest.raises(rb.RadioError):
            client.countries()
        assert fake.paths() == []
    finally:
        gone.close()


def test_search_country_language_genre_and_what_is_asked(fake):
    client = rb.RadioBrowser([fake.host], scheme="http")
    assert [s["name"] for s in client.search("jazz")] == ["Jazz Radio"]
    assert [s["name"] for s in client.group(rb.COUNTRY, "SE")] == ["Jazz Radio", "News 24"]
    assert [s["name"] for s in client.group(rb.LANGUAGE, "german")] == ["Rock FM"]
    assert [s["name"] for s in client.group(rb.TAG, "news")] == ["News 24"]
    assert [c["name"] for c in client.lists(rb.COUNTRY)] == ["Germany", "Sweden"]
    assert [x["name"] for x in client.lists(rb.LANGUAGE)] == ["Swedish", "German"]
    assert [x["name"] for x in client.lists(rb.TAG)] == ["jazz", "rock", "news"]
    for path, q, headers in fake.requests:
        assert headers.get("User-Agent") == f"dopeIPTV/{VERSION}", path
        assert not {"Cookie", "Authorization", "Referer"} & set(headers), headers
        if path == "/json/stations/search":
            assert q["hidebroken"] == "true" and q["order"] == "votes"
    lang = next(q for p, q, _h in fake.requests if q.get("language"))
    assert lang["languageExact"] == "true"


def test_the_click_choice_goes_with_the_other_settings():
    from dopeiptv.core import sync
    assert sync.syncable(rb.SETTING_CLICKS)


def test_a_click_names_the_station_and_nothing_else(fake):
    client = rb.RadioBrowser([fake.host], scheme="http")
    assert client.click("u-jazz") is True
    path, q, headers = fake.requests[-1]
    assert path == "/json/url/u-jazz" and q == {}
    assert client.click("") is False
    down = rb.RadioBrowser([_dead_host()], scheme="http", timeout=2)
    assert down.click("u-jazz") is False
