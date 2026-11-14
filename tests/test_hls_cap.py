"""Nerd: the highest quality adaptive (HLS) streams play at. What each
choice puts on mpv (nothing at all for Best or with Nerd off), which
variant a cap picks from a stream's tracks - its sound from the same
variant - the Nerd page storing it with a line that says what it does, and
a real mpv playing a three-variant HLS stream from a fake server at the
variant each cap allows (a stream that is not HLS untouched)."""
from __future__ import annotations

import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dopeiptv.core import hls_cap, nerd
from dopeiptv.core.hls_cap import Cap, cap_of, mpv_options, pick
from dopeiptv.core.nerd_check import FAIL, INFO, OK
from tests.test_nerd import TS, S, _libmpv, _plays

DATA = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def _nerd_reset(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost,::1")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost,::1")
    yield
    nerd.install(None)


# -- the choices and mpv --------------------------------------------------------------

@pytest.mark.parametrize("quality, kbps, options", [
    ("", 0, []), ("", "0", []), (None, None, []), ("best", 0, []),
    ("lowest", 0, [("hls-bitrate", "min")]), ("lowest", 5000, [("hls-bitrate", "min")]),
    ("1080", 0, [("hls-bitrate", "min")]), ("720p", 0, [("hls-bitrate", "min")]),
    ("480", 2000, [("hls-bitrate", "min")]),
    ("", 2500, [("hls-bitrate", "2500000")]), ("", "800.0", [("hls-bitrate", "800000")]),
    ("", "junk", []), ("", -5, []), ("4k", 0, []),
])
def test_each_choice_as_mpv_options(quality, kbps, options):
    assert mpv_options(cap_of(quality, kbps)) == options


def test_a_cap_reaches_the_core_only_while_nerd_is_on_and_not_best():
    def opts(**kw):
        return [p for p in nerd.Nerd(S(**kw)).core_options() if p[0] == "hls-bitrate"]
    assert opts(nerd_enabled="false", nerd_hls_quality="720", nerd_hls_max_kbps="3000") == []
    assert nerd.Nerd(S(nerd_enabled="false", nerd_hls_quality="720")).hls_cap() == Cap()
    assert opts(nerd_enabled="true") == []
    assert opts(nerd_enabled="true", nerd_hls_quality="", nerd_hls_max_kbps="0") == []
    assert opts(nerd_enabled="true", nerd_hls_quality="720") == [("hls-bitrate", "min")]
    assert opts(nerd_enabled="true", nerd_hls_max_kbps="4000") == [("hls-bitrate", "4000000")]
    # After the user's own mpv options: the page's choice wins.
    n = nerd.Nerd(S(nerd_enabled="true", nerd_mpv_options="hls-bitrate=max", nerd_hls_quality="lowest"))
    assert [v for k, v in n.core_options() if k == "hls-bitrate"] == ["max", "min"]
    on = nerd.Nerd(S(nerd_enabled="true", nerd_hls_quality="480", nerd_hls_max_kbps="1500")).hls_cap()
    assert on == Cap(height=480, bits=1_500_000) and on.picks and on.active
    assert not Cap(lowest=True).picks and not Cap(bits=1).picks and not Cap().active


# -- which variant ---------------------------------------------------------------------------

def _variants(*specs, selected=0, audio=True):
    """(w, h, bits) per variant, in the playlist's order; mpv's track-list."""
    out = []
    for i, (w, h, bits) in enumerate(specs):
        out.append({"id": i + 1, "type": "video", "demux-w": w, "demux-h": h, "hls-bitrate": bits,
                    "program-id": i, "selected": i == selected})
        if audio:
            out.append({"id": i + 1, "type": "audio", "hls-bitrate": bits, "program-id": i,
                        "lang": "swe", "selected": i == selected})
    return out


LADDER = [(1920, 1080, 6_000_000), (1280, 720, 3_000_000), (854, 480, 1_500_000), (426, 240, 400_000)]


def test_the_tallest_variant_within_the_cap_with_its_own_sound():
    lowest = _variants(*LADDER, selected=3)            # hls-bitrate=min opened the smallest
    assert pick(lowest, Cap(height=720)) == {"vid": 2, "aid": 2}
    assert pick(lowest, Cap(height=1080)) == {"vid": 1, "aid": 1}
    assert pick(lowest, Cap(height=480)) == {"vid": 3, "aid": 3}
    # And the bitrate on top of the size.
    assert pick(lowest, Cap(height=1080, bits=2_000_000)) == {"vid": 3, "aid": 3}
    # Already on it: nothing to switch.
    assert pick(_variants(*LADDER, selected=1), Cap(height=720)) == {}
    # Nothing fits: the smallest there is.
    assert pick(_variants(*LADDER[:2], selected=0), Cap(height=480)) == {"vid": 2, "aid": 2}


def test_a_variant_is_measured_as_a_wide_picture_and_1088_lines_are_1080():
    films = _variants((1920, 800, 5_000_000), (1280, 536, 2_500_000), (640, 268, 700_000), selected=2)
    assert pick(films, Cap(height=720)) == {"vid": 2, "aid": 2}
    coded = _variants((1920, 1088, 6_000_000), (1280, 720, 3_000_000), selected=1)
    assert pick(coded, Cap(height=1080)) == {"vid": 1, "aid": 1}
    assert hls_cap.lines({"demux-w": 1920, "demux-h": 800}) == 1080
    assert hls_cap.lines({"demux-w": 1920}) == 0


def test_the_sound_keeps_its_language_in_the_new_variant():
    tracks = _variants(*LADDER[1:], selected=2, audio=False)
    for prog, base in ((0, 10), (1, 20), (2, 30)):
        for k, lang in enumerate(("eng", "swe")):
            tracks.append({"id": base + k, "type": "audio", "hls-bitrate": 1, "program-id": prog,
                           "lang": lang, "selected": prog == 2 and lang == "swe"})
    assert pick(tracks, Cap(height=720)) == {"vid": 1, "aid": 11}
    # Sound of its own (no variant of its own): left as it is.
    alone = _variants(*LADDER, selected=3, audio=False) + [
        {"id": 1, "type": "audio", "hls-bitrate": 0, "selected": True}]
    assert pick(alone, Cap(height=720)) == {"vid": 2}


def test_nothing_for_a_stream_that_is_not_adaptive_or_cannot_be_measured():
    cap = Cap(height=720)
    assert pick([], cap) == {} and pick(None, cap) == {}
    plain = [{"id": 1, "type": "video", "demux-w": 1920, "demux-h": 1080, "selected": True},
             {"id": 2, "type": "video", "demux-w": 1280, "demux-h": 720}]
    assert pick(plain, cap) == {}                      # no variant bitrates: not HLS
    assert pick(_variants(LADDER[0]), cap) == {}       # one variant
    unknown = [dict(t, **{"demux-h": None, "demux-w": None}) for t in _variants(*LADDER, selected=3)]
    assert pick(unknown, cap) == {}                    # stays on the smallest
    cover = _variants(*LADDER, selected=3)
    cover[0]["albumart"] = True
    assert pick(cover, cap)["vid"] == 2
    for no in (Cap(), Cap(lowest=True), Cap(bits=2_000_000)):
        assert pick(_variants(*LADDER, selected=3), no) == {}


def test_radio_takes_the_best_sound_within_the_bitrate():
    radio = [{"id": i + 1, "type": "audio", "hls-bitrate": b, "program-id": i, "selected": i == 2}
             for i, b in enumerate((320_000, 128_000, 48_000))]
    assert pick(radio, Cap(height=720)) == {"aid": 1}
    assert pick(radio, Cap(height=720, bits=200_000)) == {"aid": 2}


def test_apply_pick_switches_and_never_raises():
    class FakeMpv(dict):
        track_list = _variants(*LADDER, selected=3)
    m = FakeMpv()
    assert hls_cap.apply_pick(m, Cap(height=480)) == {"vid": 3, "aid": 3}
    assert m == {"vid": 3, "aid": 3}

    class Broken:
        @property
        def track_list(self):
            raise RuntimeError("core gone")
    assert hls_cap.apply_pick(Broken(), Cap(height=480)) == {}


# -- a core without the player's variant switch (a multiview cell) ---------------------------

@pytest.mark.parametrize("quality, kbps, options", [
    ("", 0, []), ("lowest", 0, [("hls-bitrate", "min")]),
    ("720", 0, []), ("1080", 0, []), ("480", 2000, [("hls-bitrate", "2000000")]),
    ("", 2500, [("hls-bitrate", "2500000")]),
])
def test_a_core_without_the_switch_gets_no_picture_size_cap(quality, kbps, options):
    """A size cap is hls-bitrate=min plus the player's switch at file-loaded;
    on a core without that switch it kept every HLS stream on its smallest
    variant. There it is the bitrate cap alone (Lowest still asks for it)."""
    assert mpv_options(cap_of(quality, kbps), picker=False) == options
    n = nerd.Nerd(S(nerd_enabled="true", nerd_hls_quality=quality, nerd_hls_max_kbps=str(kbps)))
    assert [p for p in n.core_options(picker=False) if p[0] == "hls-bitrate"] == options


class _Core(dict):
    """An mpv core as nerd and the player's switch use one."""

    def __init__(self):
        super().__init__({"hls-bitrate": "max"})
        self.hooks: list = []
        self.track_list = _variants(*LADDER, selected=3)

    def event_callback(self, name):
        def hook(fn):
            self.hooks.append((name, fn))
            return fn
        return hook


def test_only_a_core_with_the_switch_opens_on_the_smallest_variant():
    from dopeiptv.media.video_surface import _MpvGLWidget
    assert _MpvGLWidget.variant_picker is False
    nerd.install(S(nerd_enabled="true", nerd_hls_quality="720"))
    cell, docked = _Core(), _Core()
    nerd.apply_core(cell, picker=False)
    nerd.apply_core(docked, picker=True)
    assert cell["hls-bitrate"] == "max" and docked["hls-bitrate"] == "min"


def test_a_cap_changed_while_playing_arms_the_switch_before_any_reconnect():
    """Settings changed mid-stream put hls-bitrate=min on the core at once;
    a reconnect that reopens the stream before the next play() must still
    switch up to the cap, not stay on the smallest variant."""
    from dopeiptv.media.embedded import EmbeddedPlayer

    class Player:
        _watch_variants = EmbeddedPlayer._watch_variants
        nerd_reapply = EmbeddedPlayer.nerd_reapply
        _hls_cap = Cap()

        def __init__(self):
            self.video = type("V", (), {"mpv": _Core()})()
    p = Player()
    m = p.video.mpv
    s = S(nerd_enabled="true")
    nerd.install(s)
    nerd.apply_core(m)
    assert p.nerd_reapply() is not None and not m.hooks and m["hls-bitrate"] == "max"
    s["nerd_hls_quality"] = "720"
    nerd.install(s)
    p.nerd_reapply()
    assert m["hls-bitrate"] == "min" and p._hls_cap.picks
    assert [name for name, _fn in m.hooks] == ["file-loaded"]
    # The stream reopens (a reconnect): the switch takes it up to 720p.
    m.hooks[0][1](None)
    assert m["vid"] == 2 and m["aid"] == 2
    # Again: hooked once only.
    p.nerd_reapply()
    assert len(m.hooks) == 1
    # Settings > Nerd closes: the window goes through the player's own way.
    from dopeiptv.ui.mw_settings import _SettingsMixin
    calls = []
    win = type("W", (), {"player": type("P", (), {"video": p.video,
                                                  "nerd_reapply": lambda self: calls.append(1)})()})()
    _SettingsMixin._nerd_to_player(win)
    assert calls == [1]


# -- the line under the setting ----------------------------------------------------------------

class _Takes(dict):
    def terminate(self):
        pass


class _Refuses(_Takes):
    def __setitem__(self, key, value):
        raise RuntimeError("('mpv property does not exist', -8, (None, b'x'))")


def test_the_line_says_what_the_cap_does_and_that_mpv_takes_it():
    assert hls_cap.check(Cap()).state == INFO
    c = hls_cap.check(cap_of("720", 2000), make_mpv=_Takes)
    assert (c.state, c.key, c.params) == (OK, "nerd_ck_hls_cap", {"what": "720p · 2000 kbit/s"})
    assert "hls-bitrate=min" in c.detail and "not affected" in c.detail
    assert hls_cap.check(cap_of("", 900), make_mpv=_Takes).params == {"what": "900 kbit/s"}
    assert hls_cap.check(cap_of("lowest"), make_mpv=_Takes).key == "nerd_ck_hls_lowest"
    bad = hls_cap.check(cap_of("480"), make_mpv=_Refuses)
    assert (bad.state, bad.key) == (FAIL, "nerd_ck_hls_refused")
    assert hls_cap.check(cap_of("480"), make_mpv=None).key == "nerd_ck_mpv_unavailable"


@pytest.fixture(scope="module")
def app():
    pytest.importorskip("PyQt6.QtWidgets")
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_the_player_page_stores_the_cap(app, tmp_path, monkeypatch):
    from PyQt6.QtCore import QSettings
    from PyQt6.QtWidgets import QComboBox, QDialog, QSpinBox

    from dopeiptv.ui import nerd_settings
    from tests.test_nerd_ui import _shell
    monkeypatch.setattr(nerd_settings, "throwaway_mpv", lambda: _Takes())
    s = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    s.setValue("nerd_enabled", "true")
    shell, page = _shell()
    commit = nerd_settings.build(page, s, QDialog())
    stored: dict = {}
    commit(lambda k, v: stored.__setitem__(k, v))
    assert "nerd_hls_quality" not in stored and "nerd_hls_max_kbps" not in stored
    q = next(c for c in page.findChildren(QComboBox) if c.findData("lowest") >= 0)
    rate = next(sp for sp in page.findChildren(QSpinBox) if sp.suffix() == " kbit/s")
    assert [q.itemData(i) for i in range(q.count())] == list(hls_cap.QUALITIES)
    assert rate.value() == 0 and rate.specialValueText()
    q.setCurrentIndex(q.findData("720"))
    rate.setValue(3000)
    commit(lambda k, v: stored.__setitem__(k, v))
    assert stored["nerd_hls_quality"] == "720" and stored["nerd_hls_max_kbps"] == "3000"
    assert "nerd_hls_quality" in nerd_settings.PART_KEYS["player"]
    # Read back as it was left.
    s.setValue("nerd_hls_quality", "lowest")
    s.setValue("nerd_hls_max_kbps", "1500")
    shell, page = _shell()
    nerd_settings.build(page, s, QDialog())
    q = next(c for c in page.findChildren(QComboBox) if c.findData("lowest") >= 0)
    rate = next(sp for sp in page.findChildren(QSpinBox) if sp.suffix() == " kbit/s")
    assert q.currentData() == "lowest" and rate.value() == 1500


# -- a real mpv, a real HLS stream ---------------------------------------------------------------

VARIANTS = [("v720", 3_000_000, "hls_720.ts"), ("v480", 1_500_000, "hls_480.ts"),
            ("v240", 400_000, "hls_240.ts")]


class HlsServer:
    """Three variants of one stream (720p, 480p, 240p), each one segment,
    and a plain MPEG-TS beside them. Records what is asked."""

    def __init__(self):
        self.requests: list[str] = []
        segs = {f"/{name}/seg.ts": (DATA / file).read_bytes() for name, _b, file in VARIANTS}
        master = "#EXTM3U\n" + "".join(
            f"#EXT-X-STREAM-INF:BANDWIDTH={bits}\n{name}/index.m3u8\n" for name, bits, _f in VARIANTS)
        media = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:0\n"
                 "#EXTINF:0.6,\nseg.ts\n#EXT-X-ENDLIST\n")
        srv = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def do_GET(self):
                srv.requests.append(self.path)
                p = self.path.split("?")[0]
                if p == "/master.m3u8":
                    body, ctype = master.encode(), "application/vnd.apple.mpegurl"
                elif p.endswith("/index.m3u8"):
                    body, ctype = media.encode(), "application/vnd.apple.mpegurl"
                elif p in segs:
                    body, ctype = segs[p], "video/mp2t"
                elif p == "/plain.ts":
                    body, ctype = TS, "video/mp2t"
                else:
                    self.send_response(404)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def hls():
    srv = HlsServer()
    yield srv
    srv.close()


def _player_with(m):
    """The player's own hook for the core, on a stand-in for the widget."""
    from dopeiptv.media.embedded import EmbeddedPlayer

    class _P:
        _watch_variants = EmbeddedPlayer._watch_variants
        _hls_cap = Cap()
    p = _P()
    # What play() does for each stream.
    p._hls_cap = nerd.current().hls_cap()
    if p._hls_cap.picks:
        p._watch_variants(m)
    return p


def _playing(m) -> tuple[int, object, object]:
    """(lines of the selected video track, its variant, the selected
    sound's variant)."""
    tracks = m.track_list or []
    v = next((t for t in tracks if t.get("type") == "video" and t.get("selected")), None)
    a = next((t for t in tracks if t.get("type") == "audio" and t.get("selected")), None)
    return ((v or {}).get("demux-h") or 0, (v or {}).get("program-id"), (a or {}).get("program-id"))


@pytest.mark.parametrize("on, quality, kbps, height", [
    ("false", "480", "0", 720),          # Nerd off: as ever
    ("true", "", "0", 720),              # Best
    ("true", "480", "0", 480),
    ("true", "1080", "0", 720),
    ("true", "lowest", "0", 240),
    ("true", "", "2000", 480),
    ("true", "720", "1000", 240),
])
def test_real_mpv_plays_the_variant_the_cap_allows(hls, on, quality, kbps, height):
    m = _libmpv()
    kw = {"nerd_enabled": on, "nerd_hls_quality": quality, "nerd_hls_max_kbps": kbps}
    try:
        nerd.install(S(**kw))
        nerd.apply_core(m)
        p = _player_with(m)
        m.command("loadfile", f"{hls.base}/master.m3u8")
        end = time.monotonic() + 20
        now = (0, None, None)
        while time.monotonic() < end:
            try:
                now = _playing(m)
            except Exception:
                pass
            if now[0] == height and now[1] is not None and now[1] == now[2]:
                break
            time.sleep(0.05)
        assert now[0] == height, (kw, now, m.track_list)
        assert now[1] == now[2], ("picture and sound from one variant", now)
        assert p._hls_cap.picks == (on == "true" and quality in ("480", "720", "1080"))
    finally:
        m.terminate()


def test_real_mpv_plays_a_stream_that_is_not_adaptive_as_it_is(hls):
    m = _libmpv()
    try:
        nerd.install(S(nerd_enabled="true", nerd_hls_quality="480"))
        nerd.apply_core(m)
        p = _player_with(m)
        assert p._hls_cap.picks
        assert _plays(m, f"{hls.base}/plain.ts")
        assert m["vid"] in ("auto", 1, True) and _playing(m)[0] == 48
    finally:
        m.terminate()


def test_real_mpv_takes_a_changed_cap_and_goes_back_to_its_own_choice():
    """Settings changed while a core plays: the cap goes on it for the next
    stream, and back to mpv's own (max) once it is Best again or Nerd off."""
    m = _libmpv()
    try:
        before = m["hls-bitrate"]
        s = S(nerd_enabled="true")
        nerd.install(s)
        nerd.apply_core(m)
        s["nerd_hls_quality"] = "720"
        nerd.install(s)
        nerd.reapply_core(m)
        assert m["hls-bitrate"] == "min"
        s["nerd_hls_quality"], s["nerd_hls_max_kbps"] = "", "2500"
        nerd.install(s)
        nerd.reapply_core(m)
        assert m["hls-bitrate"] == 2_500_000
        s["nerd_hls_max_kbps"] = "0"
        nerd.install(s)
        assert "hls-bitrate" in nerd.reapply_core(m)["restored"]
        assert m["hls-bitrate"] == before
        s["nerd_hls_quality"] = "lowest"
        nerd.install(s)
        nerd.reapply_core(m)
        s["nerd_enabled"] = "false"
        nerd.install(s)
        nerd.reapply_core(m)
        assert m["hls-bitrate"] == before
    finally:
        m.terminate()
