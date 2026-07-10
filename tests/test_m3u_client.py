"""M3U playlist provider: parsing, grouping, url lookup and the client
factory. No network - the sample text is parsed directly."""

import inspect

from dopeiptv.providers.client import (
    M3UClient, XtreamClient, make_client,
)

SAMPLE = """#EXTM3U
#EXTINF:-1 tvg-id="bbc1" tvg-logo="http://logo/bbc.png" group-title="UK",BBC One
http://stream/bbc1.m3u8
#EXTINF:-1 tvg-logo="http://logo/cnn.png" group-title="News",CNN
http://stream/cnn.ts
#EXTINF:-1 group-title="News",Al Jazeera
http://stream/aljazeera.m3u8
#EXTINF:-1,No Group Channel
http://stream/nogroup.ts
"""


def _parsed():
    c = M3UClient("http://example/list.m3u")
    c._parse(SAMPLE)
    c._loaded = True
    return c


def test_interface_parity_with_xtream():
    def pub(cls):
        return {n for n, _ in inspect.getmembers(cls, inspect.isfunction)
                if not n.startswith("_")}
    assert not (pub(XtreamClient) - pub(M3UClient))


def test_groups_become_categories_in_order():
    c = _parsed()
    assert [x["category_id"] for x in c.live_categories()] == [
        "UK", "News", "Uncategorized"]


def test_streams_filter_by_group_and_carry_metadata():
    c = _parsed()
    uk = c.live_streams("UK")
    news = c.live_streams("News")
    assert [s["name"] for s in uk] == ["BBC One"]
    assert [s["name"] for s in news] == ["CNN", "Al Jazeera"]
    assert len(c.live_streams(None)) == 4
    assert uk[0]["stream_icon"] == "http://logo/bbc.png"
    assert uk[0]["epg_channel_id"] == "bbc1"
    assert c.live_streams("Uncategorized")[0]["name"] == "No Group Channel"


def test_live_url_lookup():
    c = _parsed()
    sid = c.live_streams("UK")[0]["stream_id"]
    assert c.live_url(sid) == "http://stream/bbc1.m3u8"
    assert c.live_url(999) == ""
    assert c.live_url("bad") == ""


def test_no_vod_series_or_epg():
    c = _parsed()
    assert c.vod_categories() == []
    assert c.series_categories() == []
    assert c.xmltv() == b""


def test_authenticate_parses_and_empty_raises():
    c = M3UClient("http://x")
    c._fetch = lambda: SAMPLE
    auth = c.authenticate()
    assert auth["user_info"]["auth"] == 1
    assert len(c.live_streams(None)) == 4

    empty = M3UClient("http://x")
    empty._fetch = lambda: "#EXTM3U\n"
    try:
        empty.authenticate()
        assert False, "empty playlist should raise"
    except RuntimeError:
        pass


def test_make_client_factory():
    assert isinstance(
        make_client({"kind": "m3u", "server": "http://x"}), M3UClient)
    xt = make_client(
        {"kind": "xtream", "server": "http://x", "username": "u",
         "password": "p"})
    assert isinstance(xt, XtreamClient)
    # No kind defaults to Xtream (back-compat with old stored playlists).
    assert isinstance(
        make_client({"server": "http://x", "username": "u", "password": "p"}),
        XtreamClient)
