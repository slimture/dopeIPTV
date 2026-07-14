"""Unit tests for dopeiptv.providers.epg utilities and the guide index."""

from datetime import datetime, timedelta, timezone

from dopeiptv.providers.epg import XmltvGuide, normalize_name, parse_xmltv_time


def test_normalize_name():
    assert normalize_name("  CNN HD  ") == "cnnhd"
    assert normalize_name("BBC One") == "bbcone"


def test_normalize_name_empty():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""


def test_parse_xmltv_time_with_offset():
    dt = parse_xmltv_time("20250101120000 +0000")
    assert dt is not None
    assert dt.hour == 12 or dt.utcoffset() is not None


def test_parse_xmltv_time_no_offset():
    dt = parse_xmltv_time("20250615180000")
    assert dt is not None


def test_parse_xmltv_time_invalid():
    assert parse_xmltv_time("not-a-date") is None
    assert parse_xmltv_time("") is None
    assert parse_xmltv_time(None) is None


# -- guide index (compact column storage) -----------------------------------

def _xml_ts(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0000")


def _make_guide(cache_path=None) -> XmltvGuide:
    """A guide around 'now': one finished, one airing, two upcoming."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    h = timedelta(hours=1)
    xml = ['<tv><channel id="c1"><display-name>Alpha TV</display-name>'
           '</channel>']
    slots = [(now - 2 * h, now - h, "Earlier Show", "was on before"),
             (now - h, now + h, "Airing Now", "currently on"),
             (now + h, now + 2 * h, "Up Next", "coming up"),
             (now + 2 * h, now + 3 * h, "Later On", "after that")]
    for start, stop, title, desc in slots:
        xml.append(f'<programme channel="C1" start="{_xml_ts(start)}" '
                   f'stop="{_xml_ts(stop)}"><title>{title}</title>'
                   f'<desc>{desc}</desc></programme>')
    xml.append("</tv>")
    g = XmltvGuide(client=None,
                   cache_path=str(cache_path) if cache_path else None)
    g._parse("".join(xml).encode())
    g._loaded = True
    return g


def test_guide_entries_shape():
    g = _make_guide()
    item = {"epg_channel_id": "c1"}
    entries = g._entries_for(item)
    assert [e["title"] for e in entries] == [
        "Earlier Show", "Airing Now", "Up Next", "Later On"]
    e = entries[1]
    assert e["_plain"] is True
    assert e["description"] == "currently on"
    assert isinstance(e["start_timestamp"], int)
    assert e["start_timestamp"] < e["stop_timestamp"]


def test_guide_queries():
    g = _make_guide()
    item = {"epg_channel_id": "c1"}
    cur = g.current_programme(item)
    assert cur and cur["title"] == "Airing Now"
    title, pct = g.now_for(item)
    assert title == "Airing Now" and 0 <= pct <= 100
    listings = g.listings_for(item)
    assert [e["title"] for e in listings] == [
        "Airing Now", "Up Next", "Later On"]
    past = g.past_programmes(item, days=1)
    assert [e["title"] for e in past] == ["Earlier Show"]


def test_guide_search():
    g = _make_guide()
    items = [{"epg_channel_id": "c1", "name": "Alpha TV", "stream_id": 1}]
    now = datetime.now(timezone.utc).timestamp()
    win_start, win_stop = now - 3 * 3600, now + 4 * 3600
    # title match, carries the channel item, only the one hit
    res = g.search(items, "up next", win_start, win_stop)
    assert [e["title"] for e in res] == ["Up Next"]
    assert res[0]["_channel"] is items[0]
    # description match, case-insensitive
    res2 = g.search(items, "CURRENTLY", win_start, win_stop)
    assert [e["title"] for e in res2] == ["Airing Now"]
    # results are sorted by start time
    res3 = g.search(items, "on", win_start, win_stop)
    starts = [e["start_timestamp"] for e in res3]
    assert starts == sorted(starts) and len(starts) >= 2
    # empty / too-short query and no match return nothing
    assert g.search(items, "  ", win_start, win_stop) == []
    assert g.search(items, "cricket", win_start, win_stop) == []


def test_guide_search_dedupes_duplicate_channels():
    g = _make_guide()
    # The same channel listed twice (common in real line-ups) must not double
    # up the same airing.
    items = [{"epg_channel_id": "c1", "name": "Alpha TV"},
             {"epg_channel_id": "c1", "name": "Alpha TV"}]
    now = datetime.now(timezone.utc).timestamp()
    res = g.search(items, "up next", now - 3 * 3600, now + 4 * 3600)
    assert len(res) == 1


def test_guide_name_fallback():
    """No epg_channel_id: resolve through the normalized display name."""
    g = _make_guide()
    cur = g.current_programme({"name": " ALPHA tv "})
    assert cur and cur["title"] == "Airing Now"


def test_guide_index_roundtrip(tmp_path):
    """The pickled index reloads into an identical, queryable guide."""
    cache = tmp_path / "epg_test.xml"
    cache.write_bytes(b"<tv/>")  # only mtime matters for the check
    g = _make_guide(cache_path=cache)
    g._write_index()
    g2 = XmltvGuide(client=None, cache_path=str(cache))
    assert g2._load_index() is True
    g2._loaded = True
    item = {"epg_channel_id": "c1"}
    assert g2._entries_for(item) == g._entries_for(item)
    assert g2.current_programme(item) == g.current_programme(item)


def test_guide_index_rejects_old_version(tmp_path):
    """A v1 (pre-compact-storage) pickle must be rejected, not half-loaded."""
    import pickle
    cache = tmp_path / "epg_test.xml"
    cache.write_bytes(b"<tv/>")
    pkl = tmp_path / "epg_test.xml.pkl"
    with pkl.open("wb") as f:
        pickle.dump({"v": 1, "by_id": {"c1": [{"title": "old"}]},
                     "by_name": {}}, f)
    g = XmltvGuide(client=None, cache_path=str(cache))
    assert g._load_index() is False
    assert g._by_id == {}


def test_effective_url_prefers_custom_then_m3u_header():
    from dopeiptv.providers.client import M3UClient
    # explicit per-playlist URL always wins
    m = M3UClient("http://x")
    m.epg_url = "http://from-header/epg.xml"
    g = XmltvGuide(m, custom_url="http://explicit/epg.xml")
    assert g._effective_url() == "http://explicit/epg.xml"
    # no explicit URL -> ensure the M3U parses and its header URL is used
    m2 = M3UClient("http://x")
    m2._fetch = lambda: ('#EXTM3U url-tvg="http://hdr/epg.xml.gz"\n'
                         '#EXTINF:-1,Ch\nhttp://s/ch\n')
    g2 = XmltvGuide(m2, custom_url=None)
    assert g2._effective_url() == "http://hdr/epg.xml.gz"


def test_download_no_epg_source_raises_for_m3u():
    import pytest
    from dopeiptv.providers.client import M3UClient
    m = M3UClient("http://x")
    m._fetch = lambda: "#EXTM3U\n#EXTINF:-1,Ch\nhttp://s/ch\n"  # no url-tvg
    g = XmltvGuide(m, custom_url=None)
    with pytest.raises(RuntimeError):
        g._download()
