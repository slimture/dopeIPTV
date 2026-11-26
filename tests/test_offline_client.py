"""OfflineClient (first-run 'explore' mode) must be a faithful empty stand-in
for XtreamClient: same interface, every data call empty, nothing hits the net."""

import inspect

from dopeiptv.providers.client import DemoClient, OfflineClient, XtreamClient


def test_same_public_interface_as_xtream():
    """Every public method on XtreamClient must exist on OfflineClient, so the
    window never hits a missing attribute when running without a provider."""
    def public_methods(cls):
        return {n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                if not n.startswith("_")}
    missing = public_methods(XtreamClient) - public_methods(OfflineClient)
    assert not missing, f"OfflineClient missing: {sorted(missing)}"


def test_data_calls_return_empty():
    c = OfflineClient()
    assert c.live_categories() == []
    assert c.vod_categories() == []
    assert c.series_categories() == []
    assert c.live_streams("1") == []
    assert c.vod_streams("1") == []
    assert c.series_list("1") == []
    assert c.series_info(1) == {}
    assert c.vod_info(1) == {}
    assert c.short_epg(1) == []
    assert c.epg_table(1) == []
    assert c.xmltv() == b""


def test_url_builders_return_empty_string():
    c = OfflineClient()
    assert c.live_url(1) == ""
    assert c.vod_url(1) == ""
    assert c.episode_url(1) == ""


def test_carries_the_attributes_the_epg_and_ui_read():
    c = OfflineClient()
    assert c.server == "" and c.username == "" and c.password == ""
    assert c.session is not None          # epg download reads client.session
    assert c.authenticate() == {}          # no-op, never raises


def test_demo_client_same_interface_as_xtream():
    def public_methods(cls):
        return {n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                if not n.startswith("_")}
    missing = public_methods(XtreamClient) - public_methods(DemoClient)
    assert not missing, f"DemoClient missing: {sorted(missing)}"


def test_the_demo_serves_channels_a_guide_and_films_of_its_own():
    """The demo (providers/demo.py): live test streams, open films on a
    loop and a test pattern as channels, each with a logo the app draws and
    a guide made here; the films under Movies with posters drawn and their
    credit; no series, no "Recently added". Its only addresses on the
    network are its streams', all in one table."""
    import time

    from dopeiptv.core import drawn_art
    from dopeiptv.providers import demo, demo_streams
    from dopeiptv.providers.epg import XmltvGuide
    c = DemoClient()
    cats = c.live_categories()
    assert [x["category_id"] for x in cats] == ["demo-live", "demo-loop", "demo-test"]
    rows = c.live_streams()
    assert 6 <= len(rows) <= 8 and len({r["stream_id"] for r in rows}) == len(rows)
    for r in rows:
        assert r["category_id"] in {x["category_id"] for x in cats}
        assert r in c.live_streams(r["category_id"])
        assert drawn_art.is_drawn(r["stream_icon"])
        url = c.live_url(r["stream_id"])
        assert url.startswith("https://") and url in demo_streams.STREAMS.values()
    assert c.live_url("nope") == "" and c.live_url(999) == ""
    films = c.vod_streams()
    assert [f["name"] for f in films] == ["Big Buck Bunny", "Sintel", "Tears of Steel"]
    assert c.vod_categories()[0]["category_id"] == films[0]["category_id"]
    for f in films:
        assert drawn_art.parse(f["stream_icon"]).kind == "poster"
        assert c.vod_url(f["stream_id"]) in demo_streams.FILMS.values()
        info = c.vod_info(f["stream_id"])["info"]
        assert "Blender Foundation" in info["plot"] and info["releasedate"] == f["year"]
    assert c.vod_info(999) == {} and c.series_categories() == [] and c.series_list() == []
    assert c.lists_recent is False
    # Its guide, made here: every channel has a programme now and next.
    g = XmltvGuide(c)
    assert g.has_source() and g.ensure_loaded()
    for r in rows:
        now = g.listings_for(r, limit=2)
        assert len(now) == 2 and now[0]["start_timestamp"] <= time.time() < now[0]["stop_timestamp"]
    assert demo.guide(1_800_000_000) == demo.guide(1_800_000_000 + 59)     # the same for the hour
    # Akamai's and Bitmovin's hosts are gone (their streams stopped
    # answering); these are the hosts it reaches.
    assert demo_streams.hosts() == ["demo.unified-streaming.com", "devstreaming-cdn.apple.com",
                                    "storage.googleapis.com", "test-streams.mux.dev"]
