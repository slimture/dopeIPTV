"""What the fuzzers (tests/test_fuzz_*.py) found, each as a plain test with
the smallest input that showed it - so it stays fixed whatever inputs a
fuzz run happens to make."""
from __future__ import annotations

import json
import random
import types
import xml.etree.ElementTree as ET

import pytest

from dopeiptv.core import (backup, commands, home_net, phone_browse, safe_xml, series_view, sync,
                           teletext_subs)
from dopeiptv.i18n import LocalizedError
from dopeiptv.providers import boxes, device_urls, dlna, epg, mediaserver, satip
from dopeiptv.providers import client as xc
from dopeiptv.providers.mythtv import MythTVClient
from dopeiptv.providers.stalker import StalkerClient
from dopeiptv.providers.tvheadend import TvheadendClient
from dopeiptv.providers.vdr import VdrClient
from tests import fuzz_util as fz

BASE = "http://192.168.1.50:8000"


# -- XML from outside --------------------------------------------------------------

@pytest.mark.parametrize("enc", ["g", "rot13", "hex", "zlib", "mbcs"])
def test_an_encoding_no_codec_reads_is_refused_as_any_other(enc):
    """encoding="g" made expat raise LookupError, which no caller of the
    prolog check expected: it is a refusal (ValueError) now."""
    data = f'<?xml version="" encoding="{enc}"?><tv/>'.encode()
    with pytest.raises(ValueError):
        safe_xml.refuse_dtd(data)
    with pytest.raises(ValueError):
        epg.parse_xmltv(data, 7)
    assert satip.parse_description(data, "http://10.0.0.7/desc.xml") is None


def test_a_box_answer_in_a_codec_that_raises_otherwise():
    """punycode raises a plain UnicodeError (no UnicodeDecodeError): a
    box's answer is read as UTF-8 then, or is a ParseError - never another
    exception."""
    data = b'<?xmlencoding="punycode'
    try:
        root = boxes.parse_xml(data)
    except ET.ParseError:
        return
    for el in root.iter():
        (el.text or "").encode("utf-8")


def test_a_guide_a_box_makes_from_control_characters_still_reads():
    """A title with \\x00 (JSON's \\u0000) made the whole guide unreadable."""
    data = boxes.build_xmltv([("<!--", "\x00")], [{"channel": "<!--", "start": 1_780_000_000,
                                                   "stop": 1_780_003_600, "title": "R\x01a￾",
                                                   "desc": "\x0b", "subtitle": "\ud800"}])
    by_id, by_name = epg.parse_xmltv(data, 10 ** 6)
    assert by_id["<!--"][2] == ("Ra",)


def test_a_typed_address_that_does_not_parse_is_no_box():
    assert boxes.normalize_server("]", 80) == "" and boxes.normalize_server("[", 9981) == ""
    assert boxes.normalize_server("h:port", 80) == ""
    with pytest.raises(LocalizedError):
        TvheadendClient("[").authenticate()


def test_ids_that_hash_alike_are_the_ids_they_always_were(monkeypatch):
    """Channels made to hash alike took a step for every one before them:
    found through runs now - to the same ids."""
    monkeypatch.setattr(boxes, "zlib", types.SimpleNamespace(crc32=lambda data: 7))
    taken = boxes.Taken()
    assert [boxes.stable_id(k, taken) for k in "abcd"] == [7, 8, 9, 10]
    assert boxes.stable_id("e", boxes.Taken({7, 9})) == 8
    # A key with a lone surrogate (JSON's "\ud800") has an id too.
    assert boxes.stable_id("\ud800", set()) > 0


def test_a_description_of_devices_nested_thousands_deep():
    """Each device was searched for its services through every one inside
    it: a description of 12 000 nested devices took seconds."""
    def describe(body: bytes) -> object:
        with fz.FakeNet(lambda asked: body):
            return dlna.describe("http://192.168.1.9:1400/desc.xml")
    fz.linear(describe, lambda n: ('<root xmlns="urn:schemas-upnp-org:device-1-0">' + "<device><deviceList>" * n
                                   + "</deviceList></device>" * n + "</root>").encode(), 1000)
    tv = ("<root><device><friendlyName>TV</friendlyName><deviceList><device><serviceList><service>"
          "<serviceType>urn:schemas-upnp-org:service:AVTransport:1</serviceType>"
          "<controlURL>/avt</controlURL></service></serviceList></device></deviceList></device></root>").encode()
    got = describe(tv)
    assert got is not None and got.name == "TV" and got.av_url == "http://192.168.1.9:1400/avt"


# -- JSON of the wrong shape ------------------------------------------------------------

def _net(routes: dict):
    def answer(asked: fz.Asked):
        got = routes.get(asked.query.get("action"))
        if got is None:
            return 404, b""
        return got if isinstance(got, (bytes, tuple)) else json.dumps(got).encode()
    return fz.FakeNet(answer)


def test_xtream_answers_that_are_no_objects():
    c = xc.XtreamClient("http://x.example", "u", "p")
    routes = {None: {"user_info": "<<<", "server_info": {}}, "get_series_info": [1, 2],
              "get_vod_info": "x", "get_short_epg": {"epg_listings": ["[]", {"title": "VA=="}]},
              "get_simple_data_table": {"epg_listings": [None]}}
    with _net(routes):
        with pytest.raises(LocalizedError) as e:
            c.authenticate()
        assert e.value.key == "err_unexpected_response"
        assert c.series_info(1) == {} and c.vod_info(1) == {}
        assert c.short_epg(1) == [{"title": "VA=="}] and c.epg_table(1) == []


def test_an_xtream_connection_limit_of_infinity():
    c = xc.XtreamClient("http://x.example", "u", "p")
    body = b'{"user_info": {"auth": 1, "max_connections": 1E400}}'
    with _net({None: body}):
        c.authenticate()
    assert c.max_connections is None


def test_a_series_whose_season_is_a_number_and_a_long_run_of_dashes():
    assert [s.number for s in series_view.seasons({"1": 4294967296, "2": [{"id": 1}], "3": True})] == [2]
    title = "a" + "-" * 50_000 + "b"
    assert series_view.episode_title({"title": title}) == title
    assert series_view.episode_title({"title": " - : Pilot -- "}) == "Pilot"


def test_a_stalker_answer_is_never_decoded_whole_to_find_a_refusal(monkeypatch):
    """r.text guessed the charset of the whole answer (32 MB at most) to
    look at its first 300 characters: seconds a call."""
    import requests

    def guessed(self):
        raise AssertionError("the whole answer's charset was guessed")
    monkeypatch.setattr(requests.models.Response, "apparent_encoding", property(guessed))
    c = StalkerClient("http://portal.example/c/", "00:1A:79:12:34:56")
    with _net({"handshake": b"Authorization failed." + b" " * 100_000}):
        with pytest.raises(LocalizedError) as e:
            c.authenticate()
    assert e.value.key == "err_wrong_login"


def _box(cls, routes: dict, then=None, **kw):
    """A box client of *cls* logged in to *routes* (path -> answer), and
    *then(client)* asked while the fake box still answers."""
    def answer(asked: fz.Asked):
        got = routes.get(asked.path)
        if got is None or got == 404:
            return 404, b""
        return got if isinstance(got, bytes) else json.dumps(got).encode()
    with fz.FakeNet(answer):
        c = cls(BASE, **kw)
        c.authenticate()
        if then is not None:
            then(c)
        return c


def test_tvheadend_tags_and_capabilities_of_the_wrong_kind():
    c = _box(TvheadendClient, {
        "/api/serverinfo": {"sw_version": "4.3", "capabilities": 5},
        "/api/channeltag/grid": {"entries": [{"uuid": "t1", "name": "News"}]},
        "/api/channel/grid": {"entries": [{"uuid": "c1", "name": "SVT1", "tags": ["t1", ["t2"], 3]},
                                          {"uuid": "c2", "name": "SVT2", "tags": 7}]},
        "/api/dvr/entry/grid_finished": {"entries": 1}})
    assert c.box_info["capabilities"] == []
    assert sorted(r["name"] for r in c.live_streams("News")) == ["SVT1"]


def test_vdr_lists_of_the_wrong_kind(monkeypatch):
    from dopeiptv.providers import vdr
    monkeypatch.setattr(vdr.time, "time", lambda: 1_780_000_100.0)
    guide: list[bytes] = []
    c = _box(VdrClient, {
        "/info.json": {"vdr": {"version": "2.6", "plugins": float("nan")}},
        "/channels.json": {"channels": [{"channel_id": "S19.2E-1", "name": "ARD"}], "total": 1},
        "/recordings.json": {"recordings": 5, "total": 1},
        "/events/S19.2E-1.json": {"events": [{"title": "T", "start_time": 1_780_000_000, "duration": 60,
                                              "contents": 3}]}},
             then=lambda c: guide.append(c.own_guide()), options={"streamdev_port": "8000"})
    assert [r["name"] for r in c.live_streams()] == ["ARD"]
    assert b"<title>T</title>" in guide[0]


def test_vdr_streamdev_a_channel_numbered_past_any_number():
    seg = "9" * 5000
    c = _box(VdrClient, {"/info.json": 404, "/channels.m3u": f"#EXTM3U\n#EXTINF:-1,{seg} Name\nhttp://x/TS/{seg}\n".encode()})
    assert c.live_streams()[0]["name"] == f"{seg} Name"


def test_a_mythtv_channel_name_that_is_an_object():
    c = _box(MythTVClient, {
        "/Myth/GetHostName": {"String": "mythbox"},
        "/Channel/GetChannelInfoList": {"ChannelInfoList": {"ChannelInfos": [
            {"ChanId": "1", "ChannelName": {"a": "b"}}, {"ChanId": "2", "ChannelName": "BBC"}]}}})
    assert len(c.live_streams()) == 2


def test_media_server_lists_and_lengths_of_the_wrong_kind():
    assert mediaserver.ticks(float("inf")) == 0.0 and mediaserver.ticks("1e400") == 0.0
    assert mediaserver.ticks(10_000_000) == 1.0
    # A resume point past any (Plex's viewOffset of 400 numerals) is none.
    assert mediaserver.to_int("1" * 400) is None and mediaserver.to_int("120000") == 120000
    from dopeiptv.providers.plex import PlexClient
    assert PlexClient._watch({"viewOffset": "9" * 400, "lastViewedAt": 10 ** 30})["pos"] == 0
    store: dict = {}
    mediaserver.install_device_store(types.SimpleNamespace(value=lambda k, d=None: store.get(k, d),
                                                           setValue=store.__setitem__))
    try:
        c = xc.make_client({"kind": "jellyfin", "server": BASE, "username": "a", "token": "t" * 32,
                            "options": {"user_id": "u" * 32}})
        with fz.FakeNet(lambda asked: b'{"Items": 4294967296}'):
            assert c._episodes_of("x") == []
            assert c._channels() == [] and c._recordings() == []
        assert c._film({"Id": "a", "Genres": 5, "BackdropImageTags": 3, "RunTimeTicks": 1e308 * 10}, "")["secs"] == 0
        row = c._episode_row({"key": "k", "season": float("inf"), "episode": float("-inf"), "title": "x"})
        assert row["season"] == 0 and row["episode_num"] == 0
    finally:
        mediaserver.install_device_store(None)


def test_an_answer_that_names_no_charset_is_never_guessed_whole(monkeypatch):
    """requests guessed the charset of an answer that names none from all
    of it - chardet takes time with the square of a run of letters, minutes
    a megabyte - and read the JSON of every answer that was not UTF-8
    through that guess. JSON is UTF-8 by its standard; another text is
    guessed from its first GUESS_BYTES."""
    import requests as real

    from dopeiptv.core import _lazy_requests
    from dopeiptv.core._lazy_requests import requests
    seen: list[int] = []
    detect = real.compat.chardet.detect
    monkeypatch.setattr(real.compat.chardet, "detect", lambda data: (seen.append(len(data)), detect(data))[1])
    body = b'{"a": "\xe9' + b"a" * 200_000 + b'"}'
    with fz.FakeNet(lambda asked: body):
        assert requests.get("http://192.168.1.9/x").json() == {"a": "\ufffd" + "a" * 200_000}
        assert not seen, "a JSON answer's charset was guessed"
        assert requests.get("http://192.168.1.9/x").text.endswith("a\"}")
    assert seen and max(seen) <= _lazy_requests.GUESS_BYTES
    with fz.FakeNet(lambda asked: (200, {"Content-Type": "text/plain; charset=punycode"}, b"x" * 300_000)):
        with fz.time_limit(3):
            assert requests.get("http://192.168.1.9/x").text == "x" * 300_000


def test_a_box_answer_declared_in_punycode():
    """punycode's decoder takes time with the square of the text: a box's
    megabyte declared so took minutes. It is read as UTF-8."""
    data = b'<?xml version="1.0" encoding="punycode"?><a>' + b"x" * 400_000 + b"</a>"
    with fz.time_limit(3):
        assert boxes.parse_xml(data).text == "x" * 400_000
    assert safe_xml.slow_codec("PunyCode") and safe_xml.slow_codec("idna") and not safe_xml.slow_codec("utf-8")
    assert not safe_xml.slow_codec("bogus") and not safe_xml.slow_codec(None)


def test_dvbviewer_favourites_in_sections_nested_past_pythons_limit():
    from dopeiptv.providers.dvbviewer import DVBViewerClient
    depth = 3000
    favs = ('<?xml version="1.0" encoding="utf-8" ?><favourites>' + '<section caption="S">' * depth
            + '<entry ID="11">Ett</entry>' + "</section>" * depth + "<entry>22|Two</entry></favourites>")
    c = _box(DVBViewerClient, {
        "/api/version.html": '<?xml version="1.0" encoding="utf-8" ?><version iver="1">DVBViewer Media Server 3</version>'.encode(),
        "/api/getchannelsxml.html": ('<?xml version="1.0" encoding="utf-8" ?><channels><root name="R"><group name="G">'
                                     '<channel nr="1" name="Ett" EPGID="1" ID="11"/><channel nr="2" name="Two" EPGID="2" ID="22"/>'
                                     "</group></root></channels>").encode(),
        "/api/getfavourites.html": favs.encode()})
    assert sorted((r["name"], r["category_name"]) for r in c.live_streams()) == [("Ett", "S"), ("Two", "Favorites")]


# -- the small readers ---------------------------------------------------------------

def test_an_other_networks_list_that_repeats_one_address():
    """Each entry looked at every one before it: thousands of lines of
    one address took minutes - and the list it gives is the same."""
    fz.linear(home_net.read, lambda n: "192.168.1.1\n" * n, 500)
    fz.linear(home_net.addresses, lambda n: "192.168.1.0/24\n" * n, 50)
    text = "192.168.1.0/30\n192.168.1.0-192.168.1.3\n192.168.1.2\n192.168.1.0/30\n"
    assert home_net.addresses(text) == ["192.168.1.1", "192.168.1.2", "192.168.1.0", "192.168.1.3"]


def test_json_nested_past_pythons_limit_in_what_is_kept():
    deep = "[" * 100_000
    assert teletext_subs.PageStore(types.SimpleNamespace(value=lambda k, d=None: deep)).get("x") is None
    assert series_view.remembered(deep, "s") is None
    assert teletext_subs._pid({"src-id": float("-inf")}) is None


def test_a_synced_copy_whose_layout_is_no_number():
    from tests.test_fuzz_readers import _sync_blob
    _slot, key = sync.derive(b"\x03" * 16)
    for v in ('"../../x"', "1e400", "[1]"):
        with pytest.raises(sync.SyncError) as e:
            sync.unseal(key, _sync_blob(key, ('{"c": {}, "v": ' + v + "}").encode()))
        assert e.value.kind == "read" if hasattr(e.value, "kind") else True
    with pytest.raises(sync.SyncError):
        sync.unseal(key, _sync_blob(key, b'{"c": {}, "v": 99}'))


def test_a_backup_whose_playlists_nest_past_pythons_limit(tmp_path):
    path = tmp_path / "b.json"
    path.write_text(json.dumps({backup.MAGIC: 1, "stores": {"main": {"playlists": "[" * 50_000}}}))
    with pytest.raises(backup.BackupError):
        backup.read_any(str(path))


def test_a_file_address_that_does_not_parse():
    assert commands.file_url_path("//[") == "" and commands.file_url_path("file://[x/a.ts", windows=True) == ""
    assert commands.checked({"play": "file://[x"}) is None
    assert commands.checked({"play": "file:///home/a.ts"}) == {"play": "file:///home/a.ts"}


def test_how_far_from_numbers_that_are_none():
    assert phone_browse.percent(float("nan"), 100) == 0 and phone_browse.percent(10, float("inf")) == 0
    assert phone_browse.percent(True, 5e-324) == 100 and phone_browse.percent(50, 100) == 50


def test_a_device_address_with_a_control_character_in_it():
    """urlsplit drops a CR or a tab without a word: the text with it in was
    handed on as the device's address."""
    assert device_urls.on_host("h\rttp://192.168.1.5:5004", "192.168.1.5") == ""
    assert device_urls.on_host("http://192.168.1.5:5004/a b", "192.168.1.5") == ""
    assert device_urls.on_host("http://192.168.1.5:5004", "192.168.1.5") == "http://192.168.1.5:5004"


def test_a_discovery_answer_nested_past_pythons_limit():
    """Anyone on the network may answer Jellyfin's search: "[[[[..." in a
    datagram raised RecursionError in the search."""
    from dopeiptv.providers import jellyfin
    assert jellyfin.parse_discovery(b"[" * 10_000, "192.168.1.7", "jellyfin") is None


def test_an_address_part_with_a_sign_is_no_address():
    """int() takes "-1" after the octal 0 ("172.17.0-1.2"), and the address
    of -254 raised: a part is its numerals alone now."""
    from dopeiptv.core import nerd
    for text in ("172.17.0-1.2", "0x-1", "0+1", "0_1.1", "0x 1"):
        assert nerd.ipv4_any(text) is None
    assert not nerd.is_home_host("172.17.0-1.2.example")
    assert str(nerd.ipv4_any("0177.1")) == "127.0.0.1" and str(nerd.ipv4_any("0x7f000001")) == "127.0.0.1"


def test_an_address_whose_host_is_thousands_of_numerals():
    """A playlist's line of http://192.1689999... (5 000 of them): int()
    refused the part, and the ValueError lost the whole playlist."""
    from dopeiptv.core import stream_address
    url = "http://192.168" + "9" * 5000 + "/live.ts"
    assert isinstance(stream_address.refusal(url, internet=True), str)
    c = xc.M3UClient("")
    c._parse(f"#EXTINF:-1,A\n{url}\n#EXTINF:-1,B\nhttp://x.example/b.ts\n")
    assert [r["name"] for r in c._channels] == ["A", "B"]
    assert str(stream_address._inet_aton("0" * 40 + "177.1")) == "127.0.0.1"


def test_a_rewrite_rule_with_a_lone_surrogate():
    """JSON's "\\ud800" in a rule (a backup's, another computer's) made an
    address no writer could write: Nerd's line under the rules raised."""
    from dopeiptv.core import nerd_check, url_rewrite
    rules = url_rewrite.parse('[{"find": "x", "replace": "\\ud800"}, {"find": "\\udc80"}, {"find": "a", "replace": "b"}]')
    assert rules == [url_rewrite.Rule("a", "b")]
    assert nerd_check.redact_url("http://h/a?b=\ud800&\udc80=1")


#: The pattern core/catchup.py read a Flussonic address with: its reading
#: now must give the same parts for every address.
_FLUSSONIC = __import__("re").compile(r"^(?P<pre>.+/)(?P<name>[^/?#]+?)(?P<ext>\.m3u8|\.ts)?(?P<qs>\?[^#]*)?$")


def test_a_flussonic_address_of_many_slashes_and_a_hash():
    """The pattern took time with the square of a channel's address of many
    "/" and a "#" after its query - seconds for one, every time the
    archive was asked. Read in linear time now, part for part the same."""
    from dopeiptv.core import catchup
    fz.linear(catchup.flussonic_template, lambda n: "http://h/" + "x?/" * n + "#", 2000)
    rng = random.Random(5)
    pieces = ["/", "?", "#", ".", "m3u8", "ts", "a", "\n", ":", "//", ".ts", ".m3u8", "?x", "mpegts"]
    for _ in range(20_000):
        text = ("http://h/" if rng.random() < 0.5 else "") + "".join(
            rng.choice(pieces) for _ in range(rng.randrange(20)))
        m = _FLUSSONIC.match(text)
        want = None if m is None else (m.group("pre"), m.group("name"), m.group("ext"), m.group("qs") or "")
        assert catchup._flussonic_parts(text) == want, repr(text)
    assert catchup.flussonic_template("http://h/live/ch1/index.m3u8?t=1") == \
        "http://h/live/ch1/index-{utc}-{duration}.m3u8?t=1"
    assert catchup.flussonic_template("http://h/ch1/mpegts") == "http://h/ch1/archive-{utc}-{duration}.ts"


class _Broker:
    """A broker's end of a connection, read back *piece* bytes at a time."""

    def __init__(self, data: bytes, piece: int) -> None:
        self.data, self.piece, self.at = data, piece, 0

    def settimeout(self, _t: float) -> None:
        pass

    def recv(self, n: int) -> bytes:
        got = self.data[self.at:self.at + min(n, self.piece)]
        self.at += len(got)
        return got

    def close(self) -> None:
        pass


def _mqtt(data: bytes, piece: int = 1 << 20):
    from dopeiptv.core import mqtt
    c = mqtt.Client("broker.lan", timeout=2.0)
    c._sock = _Broker(data, piece)  # type: ignore[assignment]
    return c


def test_a_broker_that_sends_a_large_packet_slowly():
    """What came was added to bytes, copying all of it again with each read:
    a packet of 250 KB a byte at a time took 0.8 s, with the square of its
    size (and each packet cut off the front of a read copied the rest). Read
    in linear time now, the same packets in pieces of any size."""
    from dopeiptv.core import mqtt
    fz.linear(lambda d: _mqtt(d, 1)._read(float("inf"), mqtt.PUBLISH),
              lambda n: mqtt.publish_packet("t", b"x" * n), 30_000)
    data = (mqtt.publish_packet("a/b", b"1") + bytes([0xD0, 0]) + mqtt.publish_packet("c", b"y" * 9000, True)
            + bytes([0x20, 2, 0, 0]))
    for piece in (1, 2, 7, 65536):
        c = _mqtt(data, piece)
        assert c._read(float("inf"), mqtt.CONNACK) == (mqtt.CONNACK, 0, b"\0\0")
        assert c.inbox == [("a/b", b"1", False), ("c", b"y" * 9000, True)]
        assert not c.pending()
        c._sock = _Broker(bytes([0xD0, 0, 0xD0]), piece)  # type: ignore[assignment]
        assert c._read_one(float("inf")) == (mqtt.PINGRESP, 0, b"")
        assert c.pending() == (piece > 2)


def test_m3u_categories_in_order_each_once():
    c = xc.M3UClient("")
    c._parse('#EXTINF:-1 group-title="b",1\nhttp://x/1\n#EXTINF:-1 group-title="a",2\nhttp://x/2\n'
             '#EXTINF:-1 group-title="b",3\nhttp://x/3\n')
    c._loaded = True
    assert [x["category_id"] for x in c.live_categories()] == ["b", "a"]


def test_the_fuzzer_reproduces_its_inputs():
    a = [fz.mutate_bytes(fz.rng_for("t", i, 0), b"abc") for i in range(20)]
    b = [fz.mutate_bytes(fz.rng_for("t", i, 0), b"abc") for i in range(20)]
    assert a == b
    assert fz.shrink(b"xxxxBADxxxx", lambda d: b"BAD" in d) == b"BAD"
    with pytest.raises(fz.Finding):
        fz.fuzz("t", lambda d: 1 / 0, lambda rng: rng.random(), min_runs=1, seconds=0)
    assert random.Random(1).random() == random.Random(1).random()
