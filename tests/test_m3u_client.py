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
        raise AssertionError("empty playlist should raise")
    except RuntimeError:
        pass


def test_url_tvg_header_detected_as_epg_url():
    hdr = ('#EXTM3U url-tvg="http://guide/epg.xml.gz"\n'
           '#EXTINF:-1,Ch\nhttp://s/ch\n')
    c = M3UClient("http://x")
    c._parse(hdr)
    assert c.epg_url == "http://guide/epg.xml.gz"


def test_x_tvg_url_alias_and_first_of_many():
    hdr = ('#EXTM3U x-tvg-url="http://a/1.xml,http://b/2.xml"\n'
           '#EXTINF:-1,Ch\nhttp://s/ch\n')
    c = M3UClient("http://x")
    c._parse(hdr)
    assert c.epg_url == "http://a/1.xml"


def test_no_tvg_header_leaves_epg_url_empty():
    c = _parsed()
    assert c.epg_url == ""


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


def test_a_comma_inside_a_quoted_attribute_stays_in_the_attribute():
    c = M3UClient("http://x")
    c._parse('#EXTM3U\n'
             '#EXTINF:-1 tvg-id="a" group-title="News, Sports",Channel One\n'
             'http://stream/1.ts\n'
             '#EXTINF:0.000 tvg-name="Two",Channel Two\n'
             'http://stream/2.ts\n'
             '#EXTINF:-1 tvg-name="unbalanced,Channel Three\n'
             'http://stream/3.ts\n')
    one, two, three = c._channels
    assert (one["name"], one["category_name"]) == ("Channel One",
                                                   "News, Sports")
    assert two["name"] == "Channel Two"
    assert three["_url"] == "http://stream/3.ts"      # loose form still reads


def test_refresh_downloads_the_playlist_again():
    # The Refresh button and the auto-refresh timer call clear_list_cache();
    # the M3U client inherited a no-op, so its lineup never changed after
    # the first download.
    c = M3UClient("http://x")
    c._fetch = lambda: SAMPLE
    c.authenticate()
    assert len(c.live_streams(None)) == 4
    c._fetch = lambda: SAMPLE + "#EXTINF:-1,Fifth\nhttp://stream/5.ts\n"
    assert len(c.live_streams(None)) == 4             # no refresh asked yet
    c.clear_list_cache()
    assert len(c.live_streams(None)) == 5


def test_a_failed_refresh_keeps_the_channels_it_had():
    c = M3UClient("http://x")
    c._fetch = lambda: SAMPLE
    c.authenticate()

    def down():
        raise OSError("offline")
    c._fetch = down
    c.clear_list_cache()
    assert len(c.live_streams(None)) == 4
    c._fetch = lambda: "#EXTM3U\n"                    # empty answer
    c.clear_list_cache()
    assert len(c.live_streams(None)) == 4
    assert c.live_url(1) == "http://stream/bbc1.m3u8"


def test_a_failed_first_load_is_tried_again_later():
    # A failed first download used to mark the client loaded for good: the
    # list stayed empty until the app restarted.
    c = M3UClient("http://x")
    calls = {"n": 0}

    def down():
        calls["n"] += 1
        raise OSError("offline")
    c._fetch = down
    assert c.live_streams(None) == []
    assert c.live_categories() == []
    assert calls["n"] == 1                            # no retry storm
    c._failed_at -= M3UClient.RETRY_SECS + 1
    c._fetch = lambda: SAMPLE
    assert len(c.live_streams(None)) == 4


def test_a_url_lookup_never_waits_for_a_refresh():
    c = M3UClient("http://x")
    c._fetch = lambda: SAMPLE
    c.authenticate()

    def must_not_run():
        raise AssertionError("live_url downloaded the playlist")
    c._fetch = must_not_run
    c.clear_list_cache()
    assert c.live_url(2) == "http://stream/cnn.ts"


def _served(body: bytes, content_type: str):
    """A requests-like response, decoding .text the way requests does."""
    import requests

    r = requests.models.Response()
    r.status_code = 200
    r._content = body
    r.headers["Content-Type"] = content_type
    r.encoding = requests.utils.get_encoding_from_headers(r.headers)
    return r


def test_utf8_names_survive_a_server_that_names_no_charset():
    # requests reads text/* without a charset as ISO-8859-1: "SVT Barnkanalen
    # Å" became "Ã…". The playlist is UTF-8 whatever the header says.
    body = ("﻿#EXTM3U url-tvg=\"http://epg/x.xml\"\n"
            "#EXTINF:-1 group-title=\"Sverige\",Kanal Fem Ö\n"
            "http://s/1.ts\n").encode("utf-8")
    c = M3UClient("http://example/list.m3u")
    c.session.get = lambda *_a, **_k: _served(body, "text/plain")
    c._parse(c._fetch())
    assert c.live_streams(None)[0]["name"] == "Kanal Fem Ö"
    # The byte-order mark no longer hides the header and its guide address.
    assert c.epg_url == "http://epg/x.xml"


def test_a_declared_legacy_charset_is_still_honoured():
    body = ("#EXTM3U\n#EXTINF:-1,Первый канал\nhttp://s/1.ts\n"
            ).encode("cp1251")
    c = M3UClient("http://example/list.m3u")
    c.session.get = lambda *_a, **_k: _served(
        body, "audio/x-mpegurl; charset=windows-1251")
    c._parse(c._fetch())
    assert c.live_streams(None)[0]["name"] == "Первый канал"


def test_a_file_url_with_spaces_is_read(tmp_path):
    folder = tmp_path / "My Lists"
    folder.mkdir()
    f = folder / "tv ä.m3u"
    f.write_text("#EXTM3U\n#EXTINF:-1,Local\nhttp://s/1.ts\n",
                 encoding="utf-8")
    c = M3UClient(f.as_uri())               # file:///.../My%20Lists/tv%20%C3%A4.m3u
    c._parse(c._fetch())
    assert c.live_streams(None)[0]["name"] == "Local"


def test_what_a_drop_on_the_window_adds_as_a_playlist():
    from dopeiptv.providers.client import dropped_playlist
    assert dropped_playlist(["/home/u/My list.m3u"]) == (
        "m3u", "/home/u/My list.m3u", "", "")
    # A video beside it is not a playlist; the .M3U8 file after it is.
    assert dropped_playlist(["/v/film.mkv", "/v/tv.M3U8"])[0] == "m3u"
    assert dropped_playlist(
        ["https://iptv-org.github.io/iptv/index.m3u"])[0] == "m3u"
    # A provider link fills in the account, exactly like a paste.
    assert dropped_playlist(
        ["http://h.tv:8080/get.php?username=a&password=b&type=m3u_plus"]
    ) == ("xtream", "http://h.tv:8080", "a", "b")
    assert dropped_playlist(["https://example.com/page.html"]) is None
    assert dropped_playlist(["/home/u/notes.txt", ""]) is None
    assert dropped_playlist(["ftp://h/list.m3u"]) is None
