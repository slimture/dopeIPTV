"""An Enigma2 box beyond its channels, against a fake OpenWebif that answers
as the real one does (the XML is OpenWebif's own templates): the tuner's
signal, a screenshot, the remote's keys, its recordings renamed, moved and
deleted, transcoding on port 8002 brought to what the box accepts, and a
channel the box cannot decrypt told from its first TS packets - in the
diagnosis, and never for another source's address. The window's side: the
signal asked only while the stream stats show, the remote's keys in the
order pressed, the transcoding row in the source's own options."""
from __future__ import annotations

import pytest

from dopeiptv.core import ts_scramble
from dopeiptv.i18n import LocalizedError, tr
from dopeiptv.providers import diagnostics
from dopeiptv.providers.boxes import RECORDINGS
from dopeiptv.providers.enigma2 import KEYS, Enigma2Client, signal_rows
from tests.test_boxes import IPTV, SVT1, FakeBox, openwebif_routes

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60 + b"\xff\xd9"
OK = '<?xml version="1.0" encoding="UTF-8"?>\n<e2simplexmlresult><e2state>True</e2state><e2statetext>{}</e2statetext></e2simplexmlresult>'
NO = OK.replace("True", "False")
SIGNAL = """<?xml version="1.0" encoding="UTF-8"?>
<e2frontendstatus>
	<e2snrdb>12.34 dB</e2snrdb>
	<e2snr>85 %</e2snr>
	<e2ber>0</e2ber>
	<e2acg>91 %</e2acg>
</e2frontendstatus>"""
# What OpenWebif's template writes with no service (in standby).
SIGNAL_NONE = SIGNAL.replace("12.34 dB", " dB").replace("85 %", " %").replace(
    "<e2ber>0</e2ber>", "<e2ber></e2ber>").replace("91 %", " %")
TRANSCODING = """<?xml version="1.0" encoding="UTF-8" ?>
<e2configs>
<encoder number="0">
<e2config>
<e2configname>bitrate</e2configname>
<e2configlimits>100000-3000000</e2configlimits>
<e2configvalue>1000000</e2configvalue>
</e2config>
<e2config>
<e2configname>resolution</e2configname>
<e2configchoices>480x270, 640x360, 720x576, 1280x720</e2configchoices>
<e2configvalue>1280x720</e2configvalue>
</e2config>
</encoder>
<e2config>
<e2configname>port</e2configname>
<e2configlimits>8002-9999</e2configlimits>
<e2configvalue>8003</e2configvalue>
</e2config>
</e2configs>"""
MOVIE = "/media/hdd/movie/20260101 2000 - SVT1 - Rapport.ts"


def packet(pid: int, scrambled: bool = False, payload: bool = True) -> bytes:
    b1 = (pid >> 8) & 0x1F
    b3 = (0x80 if scrambled else 0) | (0x10 if payload else 0x20)
    return bytes([0x47, b1, pid & 0xFF, b3]) + b"\xaa" * 184


def stream(scrambled: bool, n: int = 200) -> bytes:
    out = bytearray(b"\x00\x13junk")        # starts mid-packet, as a stream may
    for i in range(n):
        out += packet(0x0000)                   # PAT, never scrambled
        out += packet(0x1FFF)                   # null
        out += packet(0x100, scrambled)         # video
        if i % 4 == 0:
            out += packet(0x101, scrambled)     # audio
    return bytes(out)


@pytest.fixture
def fake():
    made: list[FakeBox] = []

    def make(routes):
        b = FakeBox(routes)
        made.append(b)
        return b
    yield make
    for b in made:
        b.close()


# -- the TS check ----------------------------------------------------------------

def test_ts_scrambling_bits_say_whether_a_stream_is_encrypted():
    assert ts_scramble.verdict(stream(True)) is True
    assert ts_scramble.verdict(stream(False)) is False
    # Too little, or no transport stream at all: nothing said.
    assert ts_scramble.verdict(stream(True, n=5)) is None
    assert ts_scramble.verdict(b"<html>not a stream</html>" * 200) is None
    assert ts_scramble.verdict(b"") is None
    assert ts_scramble.sync_offset(stream(False)) == 6
    # Only a few scrambled packets (teletext, say) among clear ones: clear.
    mixed = b"".join(packet(0x100) for _ in range(90)) + b"".join(packet(0x102, True) for _ in range(10))
    assert ts_scramble.verdict(mixed) is False


# -- the box ---------------------------------------------------------------------

def routes(**more) -> dict:
    r = openwebif_routes()
    r.update({"/web/signal": SIGNAL,
              "/grab": lambda q: JPEG,
              "/web/remotecontrol": lambda q: (
                  "<e2remotecontrol><e2result>True</e2result>"
                  f"<e2resulttext>RC command '{q.get('command')}' has been issued</e2resulttext></e2remotecontrol>"),
              "/web/getlocations": "<e2locations><e2location>/media/hdd/movie</e2location>"
                                   "<e2location>/media/usb/archive/</e2location></e2locations>",
              "/web/moviedelete": OK.format("The movie has been deleted successfully"),
              "/web/moviemove": OK.format("The recording has been moved successfully"),
              "/web/movierename": OK.format("The recording has been renamed successfully")})
    r.update(more)
    return r


def client(box, **options) -> Enigma2Client:
    c = Enigma2Client(box.base, options=options or None)
    c.authenticate()
    return c


def sid_of(c: Enigma2Client, name: str) -> int:
    return next(r["stream_id"] for r in c.live_streams() if r["name"] == name)


def test_signal_from_the_tuner_and_none_in_standby(fake):
    box = fake(routes())
    c = client(box)
    sig = c.signal()
    assert sig == {"snr_db": 12.34, "snr": 85.0, "agc": 91.0, "ber": 0.0}
    assert signal_rows(sig) == [("SNR", "12.3 dB · 85 %"), ("AGC", "91 %"), ("BER", "0")]
    box.routes["/web/signal"] = SIGNAL_NONE
    none = c.signal()
    assert none == {"snr_db": None, "snr": None, "agc": None, "ber": None}
    assert signal_rows(none) == [(tr("box_signal"), tr("box_signal_none"))]
    # A DVB channel is tuned by the box; an IPTV entry in a bouquet is not.
    assert c.tuned(sid_of(c, "SVT1 HD")) and not c.tuned(sid_of(c, "Web Kanal"))


def test_screenshot_modes_and_a_box_that_sends_no_picture(fake):
    box = fake(routes())
    c = client(box)
    assert c.screenshot("all") == JPEG
    assert c.screenshot("osd") == JPEG and c.screenshot("video") == JPEG
    asked = box.paths("/grab")
    assert asked[0] == {"format": "jpg"}
    assert asked[1] == {"format": "jpg", "mode": "osd"} and asked[2]["mode"] == "video"
    box.routes["/grab"] = "File not found"
    with pytest.raises(LocalizedError):
        c.screenshot()


def test_remote_keys_are_the_codes_openwebif_sends(fake):
    box = fake(routes())
    c = client(box)
    assert c.remote("ok") and c.remote("1") and c.remote("power", long=True)
    asked = box.paths("/web/remotecontrol")
    assert [q["command"] for q in asked] == ["352", "2", "116"]
    assert asked[2]["type"] == "long" and "type" not in asked[0]
    assert KEYS["exit"] == 174 and KEYS["epg"] == 365 and KEYS["ch_up"] == 402
    with pytest.raises(ValueError):
        c.remote("no-such-key")


def _rec(c: Enigma2Client, title: str) -> dict:
    return next(r for r in c.vod_streams(RECORDINGS) if r["name"] == title)


def test_recordings_renamed_moved_and_deleted_by_their_reference(fake):
    box = fake(routes())
    c = client(box)
    rec = _rec(c, "Rapport")
    assert c.is_movie(rec["stream_id"])
    sref = "1:0:0:0:0:0:0:0:0:0:" + MOVIE
    # The folders: the box's locations and the movie list's bookmarks, once.
    assert c.movie_folders() == ["/media/hdd/movie/", "/media/usb/archive/"]
    ok, text = c.movie_rename(rec["stream_id"], "Rapport kväll")
    assert ok and "renamed" in text
    assert box.paths("/web/movierename")[-1] == {"sRef": sref, "newname": "Rapport kväll"}
    rec = _rec(c, "Rapport kväll")
    assert rec["_key"] == "/media/hdd/movie/Rapport kväll.ts"
    assert c.vod_url(rec["stream_id"]).endswith("file=/media/hdd/movie/Rapport%20kv%C3%A4ll.ts")
    ok, _t = c.movie_move(rec["stream_id"], "/media/usb/archive/")
    assert ok
    moved = box.paths("/web/moviemove")[-1]
    assert moved == {"sRef": "1:0:0:0:0:0:0:0:0:0:/media/hdd/movie/Rapport kväll.ts",
                     "dirname": "/media/usb/archive/"}
    assert c.vod_url(rec["stream_id"]).endswith("file=/media/usb/archive/Rapport%20kv%C3%A4ll.ts")
    before = len(c.vod_streams(RECORDINGS))
    ok, _t = c.movie_delete(rec["stream_id"])
    assert ok and len(c.vod_streams(RECORDINGS)) == before - 1
    assert box.paths("/web/moviedelete")[-1] == {
        "sRef": "1:0:0:0:0:0:0:0:0:0:/media/usb/archive/Rapport kväll.ts"}
    assert not c.is_movie(rec["stream_id"])
    # A name with a folder in it is never sent; a box that says no changes nothing.
    other = _rec(c, "Older")
    assert c.movie_rename(other["stream_id"], "../x") == (False, "")
    box.routes["/web/moviedelete"] = NO.format("Could not delete")
    assert c.movie_delete(other["stream_id"]) == (False, "Could not delete")
    assert c.is_movie(other["stream_id"])


def test_transcoding_on_8002_as_the_box_accepts_it(fake):
    box = fake(routes(**{"/transcoding": TRANSCODING}))
    plain = client(box)
    svt = sid_of(plain, "SVT1 HD")
    assert plain.play_url(svt) == plain.live_url(svt)        # nothing chosen
    assert not box.paths("/transcoding")
    c = client(box, transcode="high")
    assert box.paths("/transcoding") == [{}]                 # read, never set
    # 5 Mbit/s at 1280x720 asked; the box takes 3 Mbit/s at most, on its port.
    assert c.transcode_params("high") == (3_000_000, 1280, 720)
    assert c.transcode_params("full") == (3_000_000, 1280, 720)
    assert c.transcode_params("low") == (1_000_000, 640, 360)
    url = c.play_url(svt)
    assert url == f"http://127.0.0.1:8003/{SVT1}?bitrate=3000000?width=1280?height=720?interlaced=0"
    # Recordings and the like keep the box's own stream; casting and
    # multiview only when the source says so too.
    assert c.live_url(svt) == f"http://127.0.0.1:8001/{SVT1}"
    assert c.play_url(svt, "cast") == c.live_url(svt)
    more = client(box, transcode="high", transcode_more=True)
    assert more.play_url(svt, "multiview") == url
    # IPTV in a bouquet plays from its own address, never transcoded.
    web = sid_of(c, "Web Kanal")
    assert c.play_url(web) == "http://iptv.example/live/a.ts"


def test_transcoding_query_and_units_by_image_and_a_box_without_it(fake):
    kbps = TRANSCODING.replace("100000-3000000", "100-10000")
    about = openwebif_routes()["/web/about"].replace(">openatv<", ">openpli<")
    box = fake(routes(**{"/transcoding": kbps, "/web/about": about}))
    c = client(box, transcode="medium")
    svt = sid_of(c, "SVT1 HD")
    assert c.play_url(svt) == f"http://127.0.0.1:8003/{SVT1}?bitrate=2500&width=1280&height=720&interlaced=0"
    box.routes["/transcoding"] = NO.format("Transcoding Plugin is not installed or your STB does not support transcoding")
    none = client(box, transcode="medium")
    assert none.transcoder == {"present": False}
    assert none.play_url(svt) == none.live_url(svt)
    # A box that does not answer /transcoding at all (no OpenWebif): the
    # step as it is, on 8002.
    del box.routes["/transcoding"]
    old = client(box, transcode="low")
    assert old.play_url(svt).startswith(f"http://127.0.0.1:8002/{SVT1}?bitrate=1000000&width=640")


def test_a_channel_the_box_cannot_decrypt(fake):
    box = fake(routes())
    data = {"ts": stream(True)}
    box.routes["/" + SVT1] = lambda q: data["ts"]
    c = client(box, stream_port=str(box.port))
    url = c.live_url(sid_of(c, "SVT1 HD"))
    assert url == f"{box.base}/{SVT1}"
    assert c.scrambled(url) is True
    assert diagnostics.diagnose_stream(url, c) == tr("diag_scrambled")
    data["ts"] = stream(False)
    assert c.scrambled(url) is False
    assert diagnostics.box_reason(url, c) is None
    # Another source's address is never read; nor for a client without the check.
    calls = len(box.calls)
    assert c.scrambled("http://elsewhere.example:8001/" + SVT1) is None
    assert c.scrambled(f"http://127.0.0.1:{box.port}/{IPTV}".replace("127.0.0.1", "localhost")) is None
    assert len(box.calls) == calls
    assert diagnostics.box_reason(url, object()) is None


# -- the window's side -----------------------------------------------------------

qt = pytest.importorskip("PyQt6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _sync(pool, fn, done, fail=None, *args):
    try:
        result = fn(*args)
    except Exception as e:
        if fail is not None:
            fail(e)
        return
    done(result)


class _Player:
    def __init__(self):
        self.stats_extra: list = []


class _Box:
    """What the window's side asks of a box client."""

    def __init__(self, tuned=True):
        self.asked = 0
        self.keys: list = []
        self._tuned = tuned

    def signal(self):
        self.asked += 1
        return {"snr_db": 10.0, "snr": 70.0, "agc": 80.0, "ber": 3.0}

    def tuned(self, _sid):
        return self._tuned

    def remote(self, key):
        self.keys.append(key)
        return True


def _host(client):
    from PyQt6.QtCore import QObject

    from dopeiptv.ui.mw_box import _BoxMixin

    class Host(QObject, _BoxMixin):
        pass
    h = Host()
    h.client, h.player, h.pool = client, _Player(), None
    h._playing_group, h._playing_item, h._playing_catchup = "live", {"stream_id": 7}, False
    return h


def test_the_signal_is_asked_only_while_the_stats_show(app, monkeypatch):
    from dopeiptv.ui import mw_box
    monkeypatch.setattr(mw_box, "run_async", _sync)
    box = _Box()
    h = _host(box)
    h._box_stats_shown(True)
    assert box.asked == 1 and h._box_sig_timer.isActive()
    assert h.player.stats_extra[0] == ("SNR", "10.0 dB · 70 %")
    h._box_signal_poll()
    assert box.asked == 2
    h._box_stats_shown(False)
    assert not h._box_sig_timer.isActive() and h.player.stats_extra == []
    # An IPTV entry, a catch-up, or another kind of source: nothing asked.
    h._playing_catchup = True
    h._box_stats_shown(True)
    assert box.asked == 2 and h.player.stats_extra == []
    h._box_stats_shown(False)
    other = _host(object())
    other._box_stats_shown(True)
    assert other.player.stats_extra == []
    other._box_stats_shown(False)


def test_the_signal_shows_in_the_stats_compact_view_too(app):
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.media.embedded import _StatsLabel
    host = QWidget()
    host.resize(1200, 800)
    label = _StatsLabel(host)
    stream = [("Video", "h264"), ("Protocol", "http")]
    label.lay_out([stream, [], signal_rows({"snr_db": 9.5, "snr": 66, "agc": 70, "ber": 1})], 1100, 700)
    assert "9.5 dB" in label.text() and "AGC" in label.text()
    label.compact = True
    label.lay_out([stream, [], signal_rows({"snr_db": 9.5})], 1100, 700)
    assert "9.5 dB" in label.text() and "Protocol" not in label.text()
    host.deleteLater()


def test_a_transcoding_box_gives_the_play_address(app):
    class Plain:
        def live_url(self, sid, fmt):
            return f"plain/{sid}/{fmt}"

    class Transcoding(Plain):
        def play_url(self, sid, purpose):
            return f"tc/{sid}/{purpose}"
    assert _host(Plain())._live_play_url(3, "m3u8") == "plain/3/m3u8"
    assert _host(Transcoding())._live_play_url(3, "ts", "cast") == "tc/3/cast"


def test_the_remote_sends_keys_in_the_order_pressed(app, monkeypatch):
    from dopeiptv.ui import box_tools
    pending: list = []
    monkeypatch.setattr(box_tools, "run_async",
                        lambda pool, fn, done, fail, *a: pending.append((fn, done, a)))
    box = _Box()
    w = box_tools.BoxRemoteDialog(None, box, None, "Vu+")
    for key in ("1", "2", "ok"):
        w.buttons[key].click()
    # One at a time: the next only once the box has answered the last.
    assert len(pending) == 1
    while pending:
        fn, done, args = pending.pop(0)
        done(fn(*args))
    assert box.keys == ["1", "2", "ok"]
    assert {"red", "green", "yellow", "blue", "power", "mute", "epg"} <= set(w.buttons)
    w.deleteLater()


def test_the_box_info_window_shows_the_signal(app, monkeypatch):
    from dopeiptv.ui import box_tools
    monkeypatch.setattr(box_tools, "run_async", _sync)

    class Info(_Box):
        box_info = {"model": "Vu+ Duo 4K", "image": "openatv 7.3", "webif": "OWIF 2.2.1",
                    "tuners": ["Tuner A"]}
        transcoder: dict = {}

        def transcode_step(self):
            return ""
    w = box_tools.BoxInfoDialog(None, Info(), None)
    w.poll()
    assert w._shown_rows[0] == ("SNR", "10.0 dB · 70 %")
    assert box_tools.transcode_text(Info()) == tr("nerd_src_no_transcode")
    w.deleteLater()


def test_the_transcoding_row_in_the_sources_own_options(app):
    from dopeiptv.ui.source_nerd import SourceNerdPanel
    p = SourceNerdPanel("enigma2", {"keep_me": "x"})
    assert p.values() == {"keep_me": "x"}
    p.e2_transcode.setCurrentIndex(p.e2_transcode.findData("medium"))
    p.switches["transcode_more"].setChecked(True)
    assert p.values() == {"keep_me": "x", "transcode": "medium", "transcode_more": True}
    assert "1280×720" in p.e2_transcode.currentText()
    p2 = SourceNerdPanel("enigma2", {"transcode": "low"})
    assert p2.e2_transcode.currentData() == "low"
    # Another kind keeps nothing of it.
    p.set_kind("tvheadend")
    assert "transcode" not in p.values()


def test_a_recording_from_the_box_that_holds_only_encrypted_packets(app, tmp_path, monkeypatch):
    from PyQt6.QtCore import QObject, pyqtSignal

    from dopeiptv.core.recording import SCRAMBLED
    from dopeiptv.ui import mw_box
    monkeypatch.setattr(mw_box, "run_async", _sync)
    path = tmp_path / "rec.ts"
    path.write_bytes(stream(True))

    class Rec(QObject):
        jobs_changed = pyqtSignal()

    class Box(_Box):
        def scrambled(self, url):
            return None
    h = _host(Box())
    h.rec = Rec()
    h.rec.jobs = [{"id": "a", "title": "Film", "status": "done", "path": str(path), "url": "u"},
                  {"id": "b", "title": "Other", "status": "done", "path": str(path)}]
    said: list = []
    h._flash_status = said.append
    h._box_rec_stopped("Film", "finished")
    first = h.rec.jobs[0]
    assert first["status"] == "failed" and first["error"] == SCRAMBLED
    assert h.rec.jobs[1]["status"] == "done" and said
    # Asked once per recording.
    h._box_rec_stopped("Film", "finished")
    assert len(said) == 1
    # In the clear: left as it was.
    path.write_bytes(stream(False))
    h.rec.jobs.append({"id": "c", "title": "Clear", "status": "done", "path": str(path)})
    h._box_rec_stopped("Clear", "finished")
    assert h.rec.jobs[2]["status"] == "done"
