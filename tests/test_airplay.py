"""AirPlay to an Apple TV: the plan and the HLS bridge (providers/
airplay_bridge.py), run here with a stand-in for ffmpeg and a real HTTP
server, and the macOS side (services/airplay_macos.py) driven through
stand-ins for PyObjC's AVFoundation and AVKit, which the suite never has.
"""
import importlib.machinery
import os
import sys
import threading
import time
import types
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from dopeiptv.providers import airplay_bridge as ab

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_REPO = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pump(qapp, seconds=0.05):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.005)


# -- the plan -----------------------------------------------------------------

def test_hls_and_mp4_the_apple_tv_decodes_go_as_they_are():
    p = ab.plan("http://p/live/u/pw/1.m3u8", "http://p/live/u/pw/1.ts",
                ["h264", "aac"], live=True)
    assert p.direct and p.url.endswith("1.m3u8") and p.live
    assert ab.plan("http://p/movie/u/pw/7.mp4", "", []).direct  # unknown: try
    # A running transport stream or Matroska needs the bridge.
    for url in ("http://p/live/u/pw/1.ts", "http://p/movie/u/pw/7.mkv",
                "/home/me/Recordings/news.ts"):
        p = ab.plan(url, url, ["h264", "aac"])
        assert not p.direct and p.video == "copy" and p.audio == "copy"


def test_what_the_apple_tv_refuses_is_converted_and_only_that():
    p = ab.plan("http://p/1.m3u8", "http://p/1.ts", ["h264", "mp2"])
    assert not p.direct and p.url == "http://p/1.ts"
    assert (p.video, p.audio) == ("copy", "aac") and not p.costs_cpu
    p = ab.plan("http://p/1.m3u8", "http://p/1.ts", ["mpeg2video", "ac3"])
    assert (p.video, p.audio) == ("h264", "copy") and p.costs_cpu
    p = ab.plan("", "http://p/1.ts", ["hevc", "eac3"])
    assert p.hevc and p.video == "copy"
    # mpv's names and aliases; a name that is neither is assumed fine.
    assert ab.split_codecs(["H264", "ac-3", "e-ac-3", "something"]) == (
        ["h264"], ["ac3", "eac3"])


def test_a_sound_track_or_the_source_s_own_network_needs_the_bridge():
    url = "http://p/live/u/pw/1.m3u8"
    assert not ab.plan(url, url, ["h264", "aac"], audio_track=1).direct
    p = ab.plan(url, url, ["h264", "aac"], own_network=True)
    assert not p.direct and "user agent" in p.why


def test_the_rewind_buffer_s_address_never_goes_to_the_tv():
    for url in ("http://127.0.0.1:41234/tok/live.m3u8",
                "http://localhost:8080/x.mp4", "http://[::1]:9/x.m3u8"):
        assert ab.is_loopback(url) and not ab.direct_ok(url)
        assert not ab.plan(url, url, ["h264", "aac"]).direct


def test_what_ffmpeg_finds_refines_the_plan():
    found = ab.parse_probe(
        "Input #0, mpegts, from 'http://p/1.ts':\n"
        "  Duration: N/A, start: 1.400000, bitrate: N/A\n"
        "  Stream #0:0[0x100]: Video: mpeg2video (Main), yuv420p, 720x576\n"
        "  Stream #0:1[0x101](swe): Audio: mp2, 48000 Hz, stereo, 192 kb/s\n"
        "  Stream #0:2[0x102](eng): Audio: ac3, 48000 Hz, 5.1(side)\n"
        "Input #1, x: Stream #1:0: Video: h264\n")
    assert found == {"video": "mpeg2video", "audio": ["mp2", "ac3"],
                     "duration": 0.0}
    p = ab.plan("", "http://p/1.ts", [])
    assert (p.video, p.audio) == ("copy", "copy")
    p = ab.refine(p, found)
    assert (p.video, p.audio) == ("h264", "aac")
    p = ab.refine(ab.plan("", "http://p/1.ts", [], audio_track=1), found)
    assert p.audio == "copy"                     # the second track is AC-3
    film = ab.parse_probe("  Duration: 01:30:00.50, start: 0\n"
                          "  Stream #0:0: Video: hevc (Main 10)\n"
                          "  Stream #0:1: Video: mjpeg, (attached pic)\n")
    assert film["duration"] == 5400.5 and film["video"] == "hevc"


# -- ffmpeg's command line -----------------------------------------------------

def test_a_seeks_pieces_come_in_shorter_segments():
    """A moment gone back to (cast_feed's pieces): the Apple TV takes a
    stream once LEAD segments are there - 2 s ones, so 6 s converted, not
    18 - and a converted picture keys at the same step."""
    p = ab.plan("", "http://p/live/u/pw/1.ts", ["h264", "aac"], live=True)
    args = ab.ffmpeg_args("ffmpeg", p, feed=True)
    assert args[args.index("-hls_time") + 1] == str(ab.FEED_SEGMENT_S) == "2"
    assert args[args.index("-hls_list_size") + 1] == "0"          # every segment kept
    p = ab.plan("", "http://p/live/u/pw/1.ts", ["mpeg2video", "mp2"], live=True)
    args = ab.ffmpeg_args("ffmpeg", p, feed=True)
    assert args[args.index("-force_key_frames") + 1] == "expr:gte(t,n_forced*2)"


def test_ffmpeg_copies_into_fmp4_hls_relative_to_its_folder():
    p = ab.plan("", "http://p/live/u/pw/1.ts", ["h264", "aac"], live=True)
    args = ab.ffmpeg_args("ffmpeg", p, net=["-user_agent", "X"])
    assert args[args.index("-c:v") + 1] == "copy"
    assert args[args.index("-c:a") + 1] == "copy"
    assert args[args.index("-hls_segment_type") + 1] == "fmp4"
    assert args[args.index("-hls_time") + 1] == str(ab.SEGMENT_S)
    assert args[args.index("-hls_list_size") + 1] == str(
        ab.LIVE_WINDOW_S // ab.SEGMENT_S)
    assert "delete_segments" in args[args.index("-hls_flags") + 1]
    assert args[-1] == ab.PLAYLIST and not os.path.isabs(args[-1])

    assert "-sn" in args and "-tag:v" not in args
    assert args.index("-user_agent") < args.index("-i")


def test_a_film_keeps_every_segment_and_starts_where_asked():
    p = ab.plan("", "/films/Ocean's Eleven.mkv", ["hevc", "dts"])
    args = ab.ffmpeg_args("ffmpeg", p, start=1830.5)
    assert args[args.index("-hls_list_size") + 1] == "0"
    assert args[args.index("-hls_playlist_type") + 1] == "event"
    assert args[args.index("-tag:v") + 1] == "hvc1"
    assert args[args.index("-c:a") + 1] == "aac"
    assert args[args.index("-ss") + 1] == "1830.500"
    assert args.index("-ss") < args.index("-i")
    assert os.path.isabs(args[args.index("-i") + 1])


def test_a_converted_picture_is_deinterlaced_with_a_keyframe_per_segment(
        monkeypatch):
    monkeypatch.setattr(ab, "video_encoder", lambda: "libx264")
    p = ab.plan("", "http://p/1.ts", ["mpeg2video", "mp2"], live=True)
    args = ab.ffmpeg_args("ffmpeg", p)
    assert args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-vf") + 1] == "yadif=deint=1"
    assert "-force_key_frames" in args and "yuv420p" in args


def test_ffmpeg_reads_the_provider_as_the_source_plays():
    opts = ab.input_options("http://p/live/1.ts", "TiviMate/5",
                            {"Referer": "http://p/", "X-Bad": "a\r\nb"},
                            "http://proxy:3128")
    assert opts[opts.index("-user_agent") + 1] == "TiviMate/5"
    assert opts[opts.index("-headers") + 1] == "Referer: http://p/\r\n"
    assert opts[opts.index("-http_proxy") + 1] == "http://proxy:3128"
    assert "-reconnect" in opts
    # No reconnect for an archive window; nothing at all for a file or UDP.
    assert "-reconnect" not in ab.input_options(
        "http://p/timeshift/u/pw/60/2026-11-15:20-00/1.ts", "X")
    assert ab.input_options("/home/me/x.ts", "X") == []
    assert ab.input_options("udp://@239.0.0.1:1234", "X") == []


def test_a_finished_film_is_served_as_a_vod_playlist():
    growing = b"#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXTINF:2,\ns00000.m4s\n"
    assert ab.serve_playlist(growing) == growing
    done = growing + b"#EXT-X-ENDLIST\n"
    assert b"PLAYLIST-TYPE:VOD" in ab.serve_playlist(done)
    assert ab.segments_in(done) == 1


def test_the_firewall_s_answers():
    on, off = "Firewall is enabled. (State = 1)", "Firewall is disabled. (State = 0)"
    allowed = "The application /Applications/dopeIPTV.app is permitted"
    blocked = "The application /Applications/dopeIPTV.app is blocked"
    assert not ab.parse_firewall(off, "", blocked)
    assert not ab.parse_firewall(on, "Firewall has block all state set to disabled.",
                                 allowed)
    assert ab.parse_firewall(on, "Firewall has block all state set to enabled.",
                             allowed)
    assert ab.parse_firewall(on, "", blocked)
    assert ab.parse_firewall("Firewall is enabled. (State = 2)", "", allowed)
    assert not ab.parse_firewall(on, "", "")        # not asked yet: macOS asks
    assert not ab.parse_firewall("", "", blocked)   # no answer: not refused
    assert not ab.firewall_blocks() or sys.platform == "darwin"


# -- the bridge, for real ----------------------------------------------------------

_FAKE = r"""
import os, sys, time
open("init.mp4", "wb").write(b"INIT")
n = int(sys.argv[1])
end = sys.argv[2] == "end"
for i in range(n):
    open("s%05d.m4s" % i, "wb").write(b"SEG%d" % i)
    body = "#EXTM3U\n#EXT-X-PLAYLIST-TYPE:EVENT\n#EXT-X-MAP:URI=\"init.mp4\"\n"
    body += "".join("#EXTINF:2,\ns%05d.m4s\n" % j for j in range(i + 1))
    if end and i == n - 1:
        body += "#EXT-X-ENDLIST\n"
    open("index.m3u8.tmp", "w").write(body)
    os.replace("index.m3u8.tmp", "index.m3u8")
if sys.argv[2] == "fail":
    sys.stderr.write("Unknown encoder 'h264_videotoolbox'\n")
    sys.exit(1)
if not end:
    time.sleep(60)
"""


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    seen = {}

    def use(segments=4, how="live"):
        def args(exe, p, start=0.0, net=None, feed=False):
            seen.update(plan=p, start=start, net=net, feed=feed)
            return [sys.executable, "-c", _FAKE, str(segments), how]
        monkeypatch.setattr(ab, "ffmpeg_args", args)
        return seen
    return use


def _get(url, method="GET"):
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.headers, r.read()


def test_the_bridge_serves_the_playlist_under_its_token(fake_ffmpeg):
    seen = fake_ffmpeg(4, "live")
    b = ab.AirPlayBridge()
    b.exe = sys.executable
    p = ab.plan("", "http://p/live/u/pw/1.ts", ["h264", "mp2"], live=True)
    url = b.start(p, net=["-user_agent", "X"])
    folder = b.folder
    try:
        assert seen["net"] == ["-user_agent", "X"]
        assert url.startswith("http://") and url.endswith("/index.m3u8")
        assert len(b.prefix) > 20                     # unguessable
        assert b.wait_ready(10) and b.hls
        code, headers, body = _get(url)
        assert code == 200 and body.startswith(b"#EXTM3U")
        assert headers["Content-Type"] == "application/vnd.apple.mpegurl"
        assert headers["Cache-Control"] == "no-cache"
        base = url.rsplit("/", 1)[0]
        code, headers, body = _get(base + "/s00001.m4s")
        assert body == b"SEG1" and headers["Content-Type"] == "video/iso.segment"
        assert _get(base + "/init.mp4", "HEAD")[0] == 200
        # Only this computer asked: not the Apple TV, not yet.
        assert not b.remote_seen
        root = url.split(b.prefix)[0]
        for bad in ("/index.m3u8", "/wrongtoken/index.m3u8",
                    b.prefix + "../index.m3u8", b.prefix + "..%2Fx",
                    b.prefix + ".hidden", b.prefix + "nothere.m4s"):
            with pytest.raises(urllib.error.HTTPError) as e:
                _get(root + bad)
            assert e.value.code == 404, bad
        b.saw("192.168.1.50")
        assert b.remote_seen
    finally:
        b.stop()
    assert not os.path.exists(folder) and not b.hls and b.url == ""
    with pytest.raises(urllib.error.URLError):
        _get(url)
    b.stop()                                          # twice is fine


def test_a_live_playlist_waits_for_three_segments(fake_ffmpeg):
    fake_ffmpeg(2, "live")
    b = ab.AirPlayBridge()
    b.exe = sys.executable
    b.start(ab.plan("", "http://p/live/1.ts", [], live=True))
    try:
        assert not b.wait_ready(1.5)
        assert "no stream" in b.error
    finally:
        b.stop()
    fake_ffmpeg(1, "end")                             # a short film
    b.start(ab.plan("", "/films/short.mkv", []))
    try:
        assert b.wait_ready(10)
        assert b"PLAYLIST-TYPE:VOD" in _get(b.url)[2]
    finally:
        b.stop()


def test_a_moment_gone_back_to_waits_for_three_segments_too(monkeypatch):
    """Pieces read here (the archive or the rewind buffer while casting):
    handed the Apple TV at one segment, it played on where it was (1.6.4) -
    a channel's three, as live."""
    b = ab.AirPlayBridge()
    b.plan = ab.plan("", "http://p/live/1.ts", [], live=True)
    one = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\ns00000.m4s\n"
    monkeypatch.setattr(b, "_playlist", lambda: one)
    assert not b.ready()                              # live: three from the edge
    b.feed = object()                                 # type: ignore[assignment]
    assert not b.ready()


def test_ffmpeg_s_own_words_say_why_it_failed(fake_ffmpeg):
    fake_ffmpeg(0, "fail")
    b = ab.AirPlayBridge()
    b.exe = sys.executable
    b.start(ab.plan("", "http://p/1.ts", ["mpeg2video"], live=True))
    try:
        assert not b.wait_ready(10)
        assert "Unknown encoder" in b.error
    finally:
        b.stop()


def test_a_stopping_cast_does_not_wait_for_the_bridge(fake_ffmpeg):
    fake_ffmpeg(0, "live")
    b = ab.AirPlayBridge()
    b.exe = sys.executable
    b.start(ab.plan("", "http://p/1.ts", [], live=True))
    began = time.monotonic()
    try:
        assert not b.wait_ready(20, stopping=lambda: True)
        assert time.monotonic() - began < 2
    finally:
        b.stop()


# -- the macOS side, with stand-ins for PyObjC ----------------------------------------

def _module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__spec__ = importlib.machinery.ModuleSpec(name, None)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class _Time:
    def __init__(self, s):
        self.value, self.timescale, self.flags = int(s * 600), 600, 1


class _Observable:
    """Key-value observing as AVFoundation offers it: one observer a key."""

    def addObserver_forKeyPath_options_context_(self, obs, key, _opts, _ctx):
        self.kvo[key] = obs

    def removeObserver_forKeyPath_(self, obs, key):
        assert self.kvo.pop(key) is obs

    def changed(self, key):
        obs = self.kvo.get(key)
        if obs is not None:
            obs.observeValueForKeyPath_ofObject_change_context_(key, self, {}, None)


class _Item(_Observable):
    def __init__(self, url):
        self.url, self._status, self._dur, self.kvo = url, 1, _Time(0), {}
        self._dur.flags = 17                       # indefinite: live
        self.events = []

    def status(self):
        return self._status

    def duration(self):
        return self._dur

    def error(self):
        return None

    def errorLog(self):
        return types.SimpleNamespace(events=lambda: self.events)


class _Player(_Observable):
    made = []

    def __init__(self, item):
        self.item, self.calls, self.kvo = item, [], {}
        self.external, self._rate, self.t, self.allows = False, 0.0, 0.0, None
        self.muted = False
        _Player.made.append(self)

    @classmethod
    def playerWithPlayerItem_(cls, item):
        return cls(item)

    def setAllowsExternalPlayback_(self, on):
        self.allows = on

    def allowsExternalPlayback(self):
        return self.allows

    def isExternalPlaybackActive(self):
        return self.external

    def route(self, on):
        """The Apple TV picked (or gone): AVFoundation tells by KVO."""
        self.external = on
        self.changed("externalPlaybackActive")

    def play(self):
        self.calls.append(("play", threading.get_ident()))
        self._rate = 1.0

    def pause(self):
        self.calls.append(("pause", threading.get_ident()))
        self._rate = 0.0

    def rate(self):
        return self._rate

    def timeControlStatus(self):
        return 2 if self._rate else 0

    def reasonForWaitingToPlay(self):
        return None

    def currentTime(self):
        return _Time(self.t)

    def currentItem(self):
        return self.item

    def replaceCurrentItemWithPlayerItem_(self, item):
        self.item = item

    def seekToTime_(self, t):
        self.calls.append(("seek", t))

    def setVolume_(self, v):
        self.calls.append(("volume", v, threading.get_ident()))

    def setMuted_(self, m):
        self.muted = m


def _plays(p):
    return [c[0] for c in p.calls if c[0] in ("play", "pause")]


class _NSObject:
    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self


class _View:
    def __init__(self):
        self.subviews, self.layer, self.wants = [], None, False

    @classmethod
    def alloc(cls):
        return cls()

    def initWithFrame_(self, frame):
        self.frame = frame
        return self

    def setLayer_(self, layer):
        self.layer = layer

    def setWantsLayer_(self, on):
        self.wants = on

    def addSubview_(self, v):
        self.subviews.append(v)


class _Layer:
    def __init__(self, player):
        self.player = player

    @classmethod
    def playerLayerWithPlayer_(cls, player):
        return cls(player)

    def setPlayer_(self, p):
        self.player = p

    def setBackgroundColor_(self, c):
        self.background = c


class _Picker:
    """Apple's button. Nothing here can be clicked from outside: it has no
    subviews to reach into."""
    made = []

    def __init__(self):
        self.player = self.delegate = None
        _Picker.made.append(self)

    @classmethod
    def alloc(cls):
        return cls()

    def initWithFrame_(self, frame):
        self.frame = frame
        return self

    def setPlayer_(self, p):
        self.player = p

    def setDelegate_(self, d):
        self.delegate = d

    def setRoutePickerButtonBordered_(self, on):
        self.bordered = on

    def setToolTip_(self, text):
        self.tip = text


class _Label:
    @classmethod
    def wrappingLabelWithString_(cls, text):
        label = cls()
        label.text = text
        return label

    def setFrame_(self, f):
        self.frame = f

    def setStringValue_(self, text):
        self.text = text


class _NSPanel:
    made = []

    def __init__(self):
        self.content, self.visible, self.closed, self.settings = _View(), False, 0, {}
        _NSPanel.made.append(self)

    @classmethod
    def alloc(cls):
        return cls()

    def initWithContentRect_styleMask_backing_defer_(self, rect, style, _b, _d):
        self.rect, self.style = rect, style
        return self

    def __getattr__(self, name):            # setTitle_, setWorksWhenModal_, ...
        if name.startswith("set") and name.endswith("_"):
            return lambda v: self.settings.__setitem__(name, v)
        raise AttributeError(name)

    def contentView(self):
        return self.content

    def orderFront_(self, _s):
        self.visible = True

    def orderOut_(self, _s):
        self.visible = False

    def close(self):
        self.visible = False
        self.closed += 1

    def center(self):
        self.settings["center"] = True

    def label(self):
        return next(v for v in self.content.subviews if isinstance(v, _Label))

    def video(self):
        return next(v for v in self.content.subviews if isinstance(v, _View))


class _Detector:
    found = True

    def __init__(self):
        self.enabled = None

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def setRouteDetectionEnabled_(self, on):
        self.enabled = on

    def multipleRoutesDetected(self):
        return _Detector.found


class _Center:
    def __init__(self):
        self.blocks = {}

    def addObserverForName_object_queue_usingBlock_(self, name, _obj, queue, block):
        assert queue == "main"
        self.blocks[name] = block
        return name

    def removeObserver_(self, token):
        self.blocks.pop(token, None)

    def post(self, name, note=None):
        self.blocks[name](note)


_ROUTES = "AVRouteDetectorMultipleRoutesDetectedDidChangeNotification"
_CLOSE = "NSWindowWillCloseNotification"


@pytest.fixture
def macos(monkeypatch):
    from dopeiptv.services import airplay_macos as am
    center = _Center()
    av = _module("AVFoundation", AVPlayer=_Player, AVPlayerItem=types.SimpleNamespace(
        playerItemWithURL_=_Item), AVPlayerLayer=_Layer, AVRouteDetector=_Detector)
    kit = _module("AVKit", AVRoutePickerView=_Picker)
    fd = _module("Foundation", NSObject=_NSObject,
                 NSURL=types.SimpleNamespace(URLWithString_=lambda u: u or None),
                 NSNotificationCenter=types.SimpleNamespace(defaultCenter=lambda: center),
                 NSOperationQueue=types.SimpleNamespace(mainQueue=lambda: "main"))
    ak = _module("AppKit", NSPanel=_NSPanel, NSView=_View, NSTextField=_Label,
                 NSColor=types.SimpleNamespace(blackColor=lambda: types.SimpleNamespace(
                     CGColor=lambda: "black")))
    objc = _module("objc", typedSelector=lambda _sig: (lambda f: f))
    for mod in (av, kit, fd, ak, objc):
        monkeypatch.setitem(sys.modules, mod.__name__, mod)
    monkeypatch.setitem(sys.modules, "CoreMedia", None)
    monkeypatch.setattr(am, "supported", lambda: True)
    monkeypatch.setattr(am, "_OBSERVER_CLASS", [])
    monkeypatch.setattr(am.ab, "firewall_blocks", lambda: False)
    from dopeiptv.services import media_session
    monkeypatch.setattr(media_session, "_said", set())
    for cls in (_Player, _Picker, _NSPanel):
        cls.made.clear()
    _Detector.found = True
    return am, center


def _settle(am, rx):
    """ROUTE_SETTLE_S goes by with the route as it is."""
    rx._raw_at -= am.ROUTE_SETTLE_S + 0.1
    rx._sync()


def test_nothing_plays_on_the_mac_until_the_apple_tv_has_it(qapp, macos):
    am, center = macos
    rx = am.AirPlayReceiver()
    rx.context = lambda: {"kind": "live"}
    ended = []
    rx.ended.connect(ended.append)
    assert rx.entries() == [am.NAME] and rx.knows(am.NAME)
    assert am.bundle_problem() is None
    url = "http://p/live/u/pw/1.m3u8"
    assert rx.cast(am.NAME, url, "SVT1", ["h264", "aac"]) == am.NAME
    p = _Player.made[-1]
    assert p.item.url == url and p.allows is True
    assert rx.active == am.NAME and not rx.bridged() and rx.duration == 0.0
    assert _plays(p) == ["pause"] and p.muted        # silent and still here
    assert set(p.kvo) == {"externalPlaybackActive", "timeControlStatus"}
    assert set(p.item.kvo) == {"status"}
    rx._sync()
    assert rx.state == "WAITING/ROUTE" and p.rate() == 0.0
    # Picked in Apple's menu: KVO says so from AVFoundation's own thread,
    # and the route is believed once it has held.
    t = threading.Thread(target=p.route, args=(True,))
    t.start()
    t.join()
    _pump(qapp)
    assert p.rate() == 0.0 and p.muted and rx.state == "WAITING/ROUTE"
    _settle(am, rx)
    assert p.rate() == 1.0 and not p.muted and rx.state == "PLAYING/NONE"
    assert all(c[1] == threading.get_ident() for c in p.calls if c[0] == "play")
    p.t = 12.5
    rx._sync()
    assert rx.position() == 12.5
    rx.pause()
    rx._sync()
    assert p.rate() == 0.0 and rx.state == "PAUSED/NONE"   # held, not resumed
    rx.resume()
    assert p.rate() == 1.0
    # The Apple TV goes away: silent at once, paused once that holds, over
    # after the grace.
    p.route(False)
    _pump(qapp)
    assert p.muted and rx.active == am.NAME
    _settle(am, rx)
    assert p.rate() == 0.0 and rx.active == am.NAME and not ended
    rx._lost_at -= am.ROUTE_GRACE_S + 1
    rx._sync()
    assert ended == ["airplay_route_lost"] and rx.active is None
    panel, picker = _NSPanel.made[-1], _Picker.made[-1]
    assert p.item is None and not p.kvo and not center.blocks
    assert panel.closed and picker.player is None and panel.video().layer.player is None


def test_a_route_that_blinks_neither_starts_nor_stops_the_player(qapp, macos):
    am, _ = macos
    rx = am.AirPlayReceiver()
    rx.context = lambda: {"kind": "live"}
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"])
    p = _Player.made[-1]
    before = len(_plays(p))
    for on in (True, False, True, False, True):
        p.route(on)
        _pump(qapp)
    assert _plays(p)[before:] == [] and p.muted
    _settle(am, rx)
    assert _plays(p)[before:] == ["play"] and not p.muted
    for on in (False, True, False, True):          # moving between TVs
        p.route(on)
        _pump(qapp)
    _settle(am, rx)
    assert _plays(p)[before:] == ["play"] and not p.muted and rx.active
    rx.stop()


def test_nothing_picked_ends_the_cast_and_an_ending_is_noticed(qapp, macos):
    am, center = macos
    rx = am.AirPlayReceiver()
    rx.context = lambda: {"kind": "movie"}
    ended = []
    rx.ended.connect(ended.append)
    rx.cast(am.NAME, "http://p/movie/u/pw/7.mp4", "Film", [], start=600.0,
            duration=5400.0)
    p = _Player.made[-1]
    assert ("seek", (360000, 600, 1, 0)) in p.calls      # resumed there
    assert rx.duration == 5400.0
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    center.post("AVPlayerItemDidPlayToEndTimeNotification")
    _pump(qapp)
    assert rx.state == "IDLE/FINISHED"
    rx.stop()
    assert rx.active is None and p.item is None
    rx.cast(am.NAME, "http://p/movie/u/pw/7.mp4", "Film", [])
    rx._opened_at -= am.ROUTE_WAIT_S + 1
    rx._sync()
    assert ended == ["airplay_no_route"]


def test_the_strip_s_calls_from_other_threads_run_on_the_gui_thread(qapp, macos):
    am, _ = macos
    rx = am.AirPlayReceiver()
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"])
    p = _Player.made[-1]
    t = threading.Thread(target=rx.set_volume, args=(0.4,))
    t.start()
    t.join()
    assert not any(c[0] == "volume" and c[1] == 0.4 for c in p.calls)
    _pump(qapp)
    vol = [c for c in p.calls if c[0] == "volume" and c[1] == 0.4]
    assert vol and vol[0][2] == threading.get_ident()
    assert rx.volume() == (0.4, False)
    rx.stop()


def test_a_transport_stream_goes_through_the_bridge(qapp, macos, fake_ffmpeg,
                                                    monkeypatch):
    am, _ = macos
    fake_ffmpeg(4, "live")
    monkeypatch.setattr(am.ab, "probe", lambda *a, **k: {
        "video": "mpeg2video", "audio": ["mp2"], "duration": 0.0})
    rx = am.AirPlayReceiver()
    rx.bridge.exe = sys.executable
    rx.context = lambda: {"kind": "live"}
    out = {}

    def cast():
        try:
            out["name"] = rx.cast(am.NAME, "http://p/live/u/pw/1.ts", "SVT1")
        except Exception as e:                     # pragma: no cover
            out["error"] = e

    t = threading.Thread(target=cast)
    t.start()
    while t.is_alive():
        _pump(qapp, 0.02)
    assert out.get("name") == am.NAME, out
    assert rx.bridged() and rx.plan.video == "h264" and rx.plan.audio == "aac"
    p = _Player.made[-1]
    assert p.item.url == rx.bridge.url and rx.bridge.prefix in p.item.url
    assert "127.0.0.1" not in p.item.url or ab.lan_address() == "127.0.0.1"
    assert rx.bridge.prefix.strip("/") not in rx._where(p.item.url)
    folder = rx.bridge.folder
    rx.stop()
    assert not rx.bridge.hls and not os.path.exists(folder)


def test_a_direct_stream_the_tv_refuses_is_tried_once_through_the_bridge(
        qapp, macos, fake_ffmpeg, monkeypatch, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="dopeiptv")
    am, center = macos
    fake_ffmpeg(4, "live")
    monkeypatch.setattr(am.ab, "probe", lambda *a, **k: {
        "video": "h264", "audio": ["aac"], "duration": 0.0})
    rx = am.AirPlayReceiver()
    rx.bridge.exe = sys.executable
    rx.context = lambda: {"kind": "live"}
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"])
    p = _Player.made[-1]
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    # The TV said no, and why: the error, with its domain and code, logged.
    err = _nserror("CoreMediaErrorDomain", -12642, "Cannot play http://p/live/u/pw/1.m3u8")
    p.item._status = 2
    center.post("AVPlayerItemFailedToPlayToEndTimeNotification", types.SimpleNamespace(
        userInfo=lambda: {"AVPlayerItemFailedToPlayToEndTimeErrorKey": err}))
    _pump(qapp)
    end = time.monotonic() + 10
    while time.monotonic() < end and p.item.url != rx.bridge.url:
        _pump(qapp, 0.05)
    assert rx.bridged() and p.item.url == rx.bridge.url
    # The same window and button: a new stream is not a new cast.
    assert len(_NSPanel.made) == 1 and len(_Picker.made) == 1
    assert _Picker.made[0].player is p and _plays(p)[-1] == "play"
    said = "\n".join(r.getMessage() for r in caplog.records)
    assert "CoreMediaErrorDomain -12642" in said and "/u/pw/" not in said
    rx.stop()


# -- zapping while casting: another channel in the same player --------------------

def test_a_zap_swaps_the_item_and_keeps_the_player_route_and_window(qapp, macos):
    am, _ = macos
    rx = am.AirPlayReceiver()
    rx.context = lambda: {"kind": "live"}
    ended = []
    rx.ended.connect(ended.append)
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1")
    p = _Player.made[-1]
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    assert rx.state == "PLAYING/NONE" and not p.muted
    # Another channel: direct HLS goes direct, into the same AVPlayer - no
    # new player, no new window, no new button, the Apple TV stays picked.
    assert rx.cast(am.NAME, "http://p/live/u/pw/2.m3u8", "SVT2") == am.NAME
    assert len(_Player.made) == 1 and len(_NSPanel.made) == 1
    assert len(_Picker.made) == 1 and _Picker.made[0].player is p
    assert not _NSPanel.made[0].closed and _NSPanel.made[0].visible
    assert p.item.url == "http://p/live/u/pw/2.m3u8" and not rx.bridged()
    assert _plays(p)[-1] == "play" and not p.muted   # on the TV at once
    # The route goes while the TV loads the new item: not the end of the
    # cast, however long the ordinary grace would have been.
    p.item._status = 0
    p.route(False)
    _pump(qapp)
    _settle(am, rx)
    rx._lost_at -= am.ROUTE_GRACE_S + 5
    rx._sync()
    assert rx.active == am.NAME and not ended
    p.item._status = 1                              # loaded; the route is back
    rx._sync()
    assert rx.active == am.NAME and not ended       # a grace from now
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    assert rx.state == "PLAYING/NONE" and _plays(p)[-1] == "play" and not ended
    # A route that really went still ends it - after the swap's grace.
    rx.cast(am.NAME, "http://p/live/u/pw/3.m3u8", "SVT3")
    p.item._status = 0
    p.route(False)
    _pump(qapp)
    _settle(am, rx)
    rx._swapped_at -= am.SWAP_GRACE_S + 1
    rx._lost_at -= am.ROUTE_GRACE_S + 1
    rx._sync()
    assert ended == ["airplay_route_lost"] and rx.active is None


def test_a_new_stream_gives_the_apple_tv_its_own_time_to_reach_it(qapp, macos):
    """A seek in the archive is a new stream in the same player: the time
    the Apple TV has to reach it starts then, not when the route came up -
    or "has not reached this Mac" came at once, a false alarm."""
    am, _ = macos
    rx = am.AirPlayReceiver()
    rx.context = lambda: {"kind": "live"}
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1")
    p = _Player.made[-1]
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    assert rx._external
    rx._external_since = time.monotonic() - 10 * am.UNREACHED_S
    rx.cast(am.NAME, "http://p/live/u/pw/2.m3u8", "SVT1")
    assert time.monotonic() - rx._external_since < 5
    assert not rx._said_unreached
    rx.stop()


def test_the_airplay_window_floats_over_the_app_only_while_it_is_in_front(qapp, macos):
    am, _ = macos
    rx = am.AirPlayReceiver()
    rx.context = lambda: {"kind": "live"}
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1")
    assert _NSPanel.made[-1].settings.get("setHidesOnDeactivate_") is True
    rx.stop()


def test_a_zap_through_the_bridge_stops_the_old_ffmpeg_first(
        qapp, macos, fake_ffmpeg, monkeypatch):
    am, _ = macos
    fake_ffmpeg(4, "live")
    monkeypatch.setattr(am, "SETTLE_S", 0.0)
    monkeypatch.setattr(am.ab, "probe", lambda *a, **k: {
        "video": "h264", "audio": ["mp2"], "duration": 0.0})
    rx = am.AirPlayReceiver()
    rx.bridge.exe = sys.executable
    rx.context = lambda: {"kind": "live"}
    out = {}

    def cast(url, settle=False):
        def run():
            try:
                out["name"] = rx.cast(am.NAME, url, "TV", settle=settle)
            except Exception as e:                 # pragma: no cover
                out["error"] = e
        out.clear()
        t = threading.Thread(target=run)
        t.start()
        while t.is_alive():
            _pump(qapp, 0.02)
        assert out.get("name") == am.NAME, out

    cast("http://p/live/u/pw/1.ts")
    p = _Player.made[-1]
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    first, old_proc = rx.bridge.folder, rx.bridge._proc
    stopped = []
    real_stop = rx.bridge.stop
    monkeypatch.setattr(rx.bridge, "stop", lambda: (stopped.append(
        rx.bridge._proc is old_proc), real_stop()))
    cast("http://p/live/u/pw/2.ts", settle=True)
    # The old ffmpeg went before the new one asked the provider; the new
    # one serves under a new token, in the same player and window.
    assert stopped and stopped[0] is True and old_proc.poll() is not None
    assert not os.path.exists(first) and rx.bridge.running()
    assert p.item.url == rx.bridge.url and rx.bridged()
    assert len(_Player.made) == 1 and len(_NSPanel.made) == 1
    assert rx.active == am.NAME
    rx.stop()


def _nserror(domain, code, text, under=None):
    return types.SimpleNamespace(
        domain=lambda: domain, code=lambda: code, localizedDescription=lambda: text,
        userInfo=lambda: {"NSUnderlyingError": under} if under else {})


def test_apple_s_button_is_in_a_window_of_its_own(qapp, macos):
    am, center = macos
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QDialog, QWidget
    dialog = QDialog()
    rx = am.AirPlayReceiver()
    row = rx.dialog_row(dialog)
    label, button = rx._row
    assert label.text() == am.tr("airplay_note") and button.isHidden()
    # Plain Qt in the dialog: no native view of Apple's inside a Qt widget.
    for w in [row, *row.findChildren(QWidget)]:
        assert not w.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"])
    p = _Player.made[-1]
    assert len(_NSPanel.made) == 1 and len(_Picker.made) == 1
    panel, picker = _NSPanel.made[0], _Picker.made[0]
    assert picker.player is p and picker.delegate is rx._obs
    video = panel.video()
    assert video.layer.player is p and video.wants        # the video output
    assert panel.visible and panel.settings["setWorksWhenModal_"] is True
    assert panel.settings["setFloatingPanel_"] is True
    assert panel.settings["setReleasedWhenClosed_"] is False
    # Apple's HUD style, small, and nothing but the button and the video
    # output: what the cast does is said in the cast strip.
    assert panel.style & am_hud(am)
    (_x, _y), (w, h) = panel.rect
    assert w <= 200 and h <= 160, panel.rect
    assert (video.frame[1][0], video.frame[1][1]) == (160.0, 90.0)
    assert [type(v) for v in panel.content.subviews] == [_View, _Picker]
    assert rx.waiting_for_route()
    assert not button.isHidden() and label.text() == am.tr("airplay_pick_window")
    # The dialog's list refreshed, the row redrawn, another stream: the same
    # window and button, never made again.
    rx._show_row()
    rx._open("http://p/live/u/pw/2.m3u8", 0.0, 0.0)
    assert len(_NSPanel.made) == 1 and len(_Picker.made) == 1 and picker.player is p
    # Apple's menu tells the delegate; the app clicks nothing itself.
    rx._obs.routePickerViewWillBeginPresentingRoutes_(picker)
    rx._obs.routePickerViewDidEndPresentingRoutes_(picker)
    _pump(qapp)
    assert rx._menu_timer.isActive()
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    assert label.text() == am.tr("airplay_on_tv_window")
    assert not rx.waiting_for_route()
    # Closed once on the TV: the cast goes on, and the dialog brings it back.
    center.post(_CLOSE)
    panel.visible = False
    _pump(qapp)
    assert rx.active == am.NAME
    button.click()
    assert panel.visible
    rx.stop()
    assert panel.closed and picker.player is None and picker.delegate is None
    assert video.layer.player is None
    assert button.isHidden() and label.text() == am.tr("airplay_note")
    dialog.deleteLater()
    _pump(qapp)


def am_hud(am):
    return 1 << 13          # NSWindowStyleMaskHUDWindow


def test_apple_s_button_opens_under_the_pointer_and_takes_the_keyboard(qapp, macos,
                                                                       monkeypatch):
    """Fewer steps: the window opens with Apple's button where the click
    that cast to AirPlay was, as the key window with the button focused,
    Apple TVs first in Apple's menu - each step only where AppKit offers it
    (nothing reached into: the menu is still Apple's to open)."""
    am, _center = macos
    calls = []
    monkeypatch.setattr(_NSPanel, "makeKeyAndOrderFront_",
                        lambda self, _s: (calls.append("key"), setattr(self, "visible", True)),
                        raising=False)
    monkeypatch.setattr(_NSPanel, "makeFirstResponder_",
                        lambda self, v: (calls.append(("first", v)), True)[1], raising=False)
    monkeypatch.setattr(_Picker, "setPrioritizesVideoDevices_",
                        lambda self, on: setattr(self, "videos_first", on), raising=False)
    monkeypatch.setattr(am, "_at_pointer", lambda w, h, b: (400.0, 700.0))
    rx = am.AirPlayReceiver()
    rx.context = lambda: {"kind": "live"}
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"])
    panel, picker = _NSPanel.made[-1], _Picker.made[-1]
    assert panel.visible and calls == ["key", ("first", picker)]
    assert panel.settings["setFrameTopLeftPoint_"] == (400.0, 700.0)
    assert picker.videos_first is True
    # Shown again from the card: in front as it always was.
    calls.clear()
    panel.visible = False
    rx.show_window()
    _pump(qapp)
    assert panel.visible and calls == []
    rx.stop()


def test_where_the_pointer_is_decides_where_apple_s_button_opens(monkeypatch):
    from dopeiptv.services import airplay_macos as am
    # Qt counts down from the primary screen's top, AppKit up from its foot.
    assert am.place_at_pointer((500, 300), (0, 0, 1440, 900), 900, 180, 160,
                               (90, 120)) == (410.0, 720.0)
    # Near an edge it stays on the screen, the button as near as it can be.
    assert am.place_at_pointer((10, 890), (0, 25, 1440, 875), 900, 180, 160,
                               (90, 120)) == (0.0, 160.0)
    assert am.place_at_pointer((1435, 30), (0, 25, 1440, 875), 900, 180, 160,
                               (90, 120)) == (1260.0, 875.0)
    # The cast card first: its top right, 16 points in from its corner.
    assert am.place_in((300, 100, 700, 400), (0, 25, 1440, 875), 900,
                       180, 160) == (804.0, 784.0)
    assert am.place_in((1300, 100, 700, 400), (0, 25, 1440, 875), 900,
                       180, 160) == (1260.0, 784.0)       # kept on the screen
    assert am._in_anchor(None, 180, 160) is None
    assert am._in_anchor((0, 0, 100, 400), 180, 160) is None   # too narrow
    monkeypatch.setattr(am, "_in_anchor", lambda a, w, h: ("card", a) if a else None)
    assert am._Panel.place((1, 2, 700, 400)) == ("card", (1, 2, 700, 400))
    # Nothing known of the pointer: beside the window in front, as before.
    monkeypatch.setattr(am, "_at_pointer", lambda *_a: None)
    monkeypatch.setattr(am, "_beside_front_window", lambda w: ("beside", w))
    assert am._Panel.place() == ("beside", am._Panel.W)


def test_the_window_steps_aside_once_the_apple_tv_holds_the_stream(qapp, macos):
    """A few seconds on the Apple TV and the AirPlay window is ordered out -
    not closed: the player keeps its layer, so the TV keeps the picture.
    The strip's AirPlay button brings it back; a route that went before the
    time was up leaves it where Apple's button can be reached."""
    am, center = macos
    rx = am.AirPlayReceiver()
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"])
    p, panel = _Player.made[-1], _NSPanel.made[-1]
    video = panel.video()
    assert panel.visible and not rx._hide_timer.isActive()
    p.route(True)
    _pump(qapp)
    _settle(am, rx)
    assert rx._hide_timer.isActive() and panel.visible
    assert rx._hide_timer.interval() == int(am.PANEL_HIDE_S * 1000)
    rx._hide_timer.stop()
    rx._hide_panel()
    assert not panel.visible and not panel.closed
    assert video.layer.player is p and rx.active == am.NAME
    rx.show_window()
    assert panel.visible and not rx._hide_timer.isActive()
    # The route gone before the time is up: it stays.
    p.route(False)
    _pump(qapp)
    _settle(am, rx)
    rx._hide_panel()
    assert panel.visible
    rx.stop()


def test_closing_the_window_before_a_tv_is_picked_ends_the_cast(qapp, macos):
    am, center = macos
    rx = am.AirPlayReceiver()
    ended = []
    rx.ended.connect(ended.append)
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"])
    center.post(_CLOSE)
    _pump(qapp)
    assert ended == ["airplay_no_route"] and rx.active is None
    assert _plays(_Player.made[-1]) == ["pause", "pause"]     # never played


def test_route_detection_decides_the_entry(qapp, macos, monkeypatch):
    am, center = macos
    from PyQt6.QtWidgets import QDialog
    rx = am.AirPlayReceiver()
    assert rx.entries() == [am.NAME]               # not looking: listed
    _Detector.found = False
    dialog = QDialog()
    rx.dialog_row(dialog)
    det = rx._detector
    assert det.enabled is True and rx._routes is None
    assert rx.entries() == [am.NAME]               # no answer yet
    rx._detect_settled()                           # it looked: none
    assert rx.entries() == []
    found = []
    rx.routes_changed.connect(found.append)
    _Detector.found = True
    center.post(_ROUTES)
    _pump(qapp)
    assert found == [True] and rx.entries() == [am.NAME]
    # The scan's worker waits for the first answer, not longer.
    _Detector.found = False
    rx._detect(True)
    out = []
    t = threading.Thread(target=lambda: out.append(rx.entries()))
    began = time.monotonic()
    t.start()
    _pump(qapp, 0.1)
    _Detector.found = True
    center.post(_ROUTES)
    while t.is_alive():
        _pump(qapp, 0.02)
    assert out == [[am.NAME]] and time.monotonic() - began < am.DETECT_SETTLE_S
    # Off with the dialog: Apple says it costs power.
    dialog.finished.emit(0)
    assert det.enabled is False and rx.entries() == [am.NAME] and rx._row is None
    # No detector to be had: still listed.
    monkeypatch.setattr(sys.modules["AVFoundation"], "AVRouteDetector", None)
    other = am.AirPlayReceiver()
    other.dialog_row(QDialog())
    assert other._detector is None and other.entries() == [am.NAME]
    dialog.deleteLater()
    _pump(qapp)


def test_errors_and_error_log_entries_are_logged_without_logins(qapp, macos, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="dopeiptv")
    am, center = macos
    under = _nserror("NSURLErrorDomain", -1022, "App Transport Security")
    text = am._error_text(_nserror("AVFoundationErrorDomain", -11800,
                                   "http://h/live/user1/secret1/1.m3u8", under))
    assert "AVFoundationErrorDomain -11800" in text and "NSURLErrorDomain -1022" in text
    assert "secret1" not in text and am._error_text(None) == ""
    rx = am.AirPlayReceiver()
    rx.cast(am.NAME, "http://p/live/user1/secret1/1.m3u8", "SVT1", ["h264", "aac"])
    p = _Player.made[-1]
    p.item.events = [types.SimpleNamespace(
        errorDomain=lambda: "CoreMediaErrorDomain", errorStatusCode=lambda: -12642,
        errorComment=lambda: "Playlist parse error",
        URI=lambda: "http://p/live/user1/secret1/1.m3u8")]
    center.post("AVPlayerItemNewErrorLogEntryNotification")
    _pump(qapp)
    said = "\n".join(r.getMessage() for r in caplog.records)
    assert "CoreMediaErrorDomain -12642: Playlist parse error" in said
    assert "secret1" not in said and "user1" not in said
    rx.stop()


def test_no_private_tricks_and_no_view_inside_a_qt_widget():
    src = (_REPO / "dopeiptv" / "services" / "airplay_macos.py").read_text(encoding="utf-8")
    for trick in ("performClick", "subviews()", "winId", "objc_object"):
        assert trick not in src, trick


def test_without_pyobjc_there_is_no_airplay(monkeypatch):
    from dopeiptv.services import airplay_macos as am
    monkeypatch.setitem(sys.modules, "AVKit", None)
    assert am.bundle_problem()                  # says what is missing
    if sys.platform != "darwin":
        assert not am.supported()


# -- the hub, the panel, the window ---------------------------------------------------

class _Mgr:
    def __init__(self, names, kind=""):
        self.names, self.active, self.calls, self.KIND = names, None, [], kind

    def knows(self, name):
        return name in self.names

    def entries(self):
        return list(self.names)

    def scan(self):
        return list(self.names)

    def cast(self, name, *a, **k):
        self.active = name
        return name

    def stop(self):
        self.calls.append("stop")
        self.active = None

    def shutdown(self):
        self.calls.append("shutdown")


def test_the_hub_routes_an_added_receiver_and_keeps_one_cast(monkeypatch):
    from dopeiptv.providers import casting
    monkeypatch.setattr(casting.ChromecastManager, "available",
                        staticmethod(lambda: True))
    cc, tv = _Mgr(["Kitchen"]), _Mgr(["TV (DLNA)"])
    tv.knows = lambda n: n.endswith("(DLNA)")
    hub = casting.CastHub(cc, tv)
    assert not hub.is_airplay("AirPlay (Apple TV)")
    ap = _Mgr(["AirPlay (Apple TV)"], "airplay")
    hub.add_receiver(ap)
    assert hub.scan() == ["AirPlay (Apple TV)", "Kitchen", "TV (DLNA)"]
    assert hub.is_airplay("AirPlay (Apple TV)") and not hub.is_airplay("Kitchen")
    hub.cast("Kitchen", "http://a", "A")
    hub.cast("AirPlay (Apple TV)", "http://a", "A")
    assert "stop" in cc.calls and hub.active == "AirPlay (Apple TV)"
    hub.cast("TV (DLNA)", "http://a", "A")
    assert "stop" in ap.calls
    hub.shutdown()
    assert "shutdown" in ap.calls


def test_the_cast_panel_shows_the_airplay_row(qapp, macos, tmp_path):
    am, _ = macos
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.providers import casting, chromecast
    hub = casting.CastHub(_Mgr(["Living room"]), _Mgr([]))
    rx = am.AirPlayReceiver()
    hub.add_receiver(rx)
    win = QWidget()
    win.settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    win.cast, win.pool, win._cast_device = hub, QThreadPool(), None
    tracks = {"audio": [{"index": 0, "codec": "aac"},
                        {"index": 1, "codec": "ac3", "lang": "swe"}],
              "subtitle": [{"index": 0, "codec": "subrip", "lang": "swe"}]}
    d = chromecast.CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1",
                              tracks=tracks, probe=False)
    win.pool.waitForDone(3000)
    _pump(qapp)
    names = [d.list.item(i).text() for i in range(d.list.count())]
    assert names == [am.NAME, "Living room"]
    d.list.setCurrentRow(0)
    assert not d.airplay_row.isHidden()
    assert all(r.isHidden() for r in d.track_rows) and d.kind_box.isHidden()
    shot = os.environ.get("DOPEIPTV_SHOT")
    if shot:
        d.resize(460, 380)
        d.show()
        _pump(qapp, 0.2)
        d.grab().save(shot)
    d.list.setCurrentRow(1)
    assert d.airplay_row.isHidden() and not d.track_rows[0].isHidden()
    d.close()
    win.deleteLater()
    _pump(qapp)


def test_the_cast_panel_lists_airplay_only_where_there_are_routes(
        qapp, macos, tmp_path, monkeypatch):
    am, center = macos
    monkeypatch.setattr(am, "DETECT_SETTLE_S", 0.1)
    from PyQt6.QtCore import QSettings, QThreadPool
    from PyQt6.QtWidgets import QWidget

    from dopeiptv.providers import casting, chromecast
    hub = casting.CastHub(_Mgr(["Living room"]), _Mgr([]), _Mgr([]))
    rx = am.AirPlayReceiver()
    hub.add_receiver(rx)
    win = QWidget()
    win.settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    win.cast, win.pool, win._cast_device = hub, QThreadPool(), None
    _Detector.found = False
    d = chromecast.CastDialog(win, "http://p/live/u/pw/1.m3u8", "SVT1",
                              tracks={}, probe=False)
    d.show()
    end = time.monotonic() + 10
    while time.monotonic() < end and not d.rescan_btn.isEnabled():
        _pump(qapp, 0.02)
    names = [d.list.item(i).text() for i in range(d.list.count())]
    assert names == ["Living room"]                # it looked and found none
    _Detector.found = True                         # an Apple TV woke up
    center.post(_ROUTES)
    _pump(qapp)
    names = [d.list.item(i).text() for i in range(d.list.count())]
    assert names == [am.NAME, "Living room"]
    d.close()
    assert rx._detector.enabled is False
    win.deleteLater()
    _pump(qapp)


def test_linux_and_windows_add_no_airplay(monkeypatch):
    from dopeiptv.ui.mw_airplay import _AirPlayMixin
    for system in ("linux", "win32"):
        monkeypatch.setattr(sys, "platform", system)
        w = types.SimpleNamespace(cast=types.SimpleNamespace(
            add_receiver=lambda _m: pytest.fail("added")))
        _AirPlayMixin._init_airplay(w)


def test_the_mac_build_bundles_and_checks_airplay():
    req = (_REPO / "requirements.txt").read_text(encoding="utf-8")
    for name in ("pyobjc-framework-AVFoundation", "pyobjc-framework-AVKit"):
        line = next(x for x in req.splitlines() if x.startswith(name))
        assert line.endswith('sys_platform == "darwin"'), line
    spec = (_REPO / "dopeiptv-mac.spec").read_text(encoding="utf-8")
    for module in ("AVFoundation", "AVKit", "Quartz"):
        assert f'"{module}"' in spec, module
    for module in ("AVFoundation", "AVKit"):
        assert module not in (_REPO / "dopeiptv.spec").read_text(encoding="utf-8")
    app = (_REPO / "dopeiptv" / "app.py").read_text(encoding="utf-8")
    assert "airplay_macos import bundle_problem" in app


def test_copied_aac_from_a_ts_is_made_mp4_ready():
    from dopeiptv.providers import airplay_bridge as b
    p = b.refine(b.Plan(False, "http://x/1.ts"), {"video": "h264", "audio": ["aac", "aac"]})
    assert p.adts and "aac_adtstoasc" in b.ffmpeg_args("ffmpeg", p)
    p = b.refine(b.Plan(False, "http://x/1.ts"), {"video": "h264", "audio": ["ac3"]})
    assert not p.adts and "aac_adtstoasc" not in b.ffmpeg_args("ffmpeg", p)


def test_the_mac_app_may_load_http_media_for_airplay():
    spec = open("dopeiptv-mac.spec", encoding="utf-8").read()
    assert "NSAllowsArbitraryLoadsForMedia" in spec


def test_the_mac_app_declares_airplay_s_bonjour_services():
    spec = (_REPO / "dopeiptv-mac.spec").read_text(encoding="utf-8")
    line = next(x for x in spec.splitlines() if "'NSBonjourServices'" in x)
    assert "'_airplay._tcp'" in line and "'_raop._tcp'" in line
    for key in ("NSAllowsLocalNetworking", "NSLocalNetworkUsageDescription"):
        assert key in spec


def test_a_cast_from_the_rewind_buffer_gives_the_apple_tv_the_bridge(
        qapp, macos, fake_ffmpeg, monkeypatch):
    """The buffer's pieces come from its loopback server: ffmpeg reads them
    here, and the Apple TV is handed the bridge's playlist on the LAN -
    never the loopback address, whatever the plan would say of it."""
    from dopeiptv.providers import cast_feed
    am, _center = macos
    seen = fake_ffmpeg(4, "live")
    attached = []
    monkeypatch.setattr(cast_feed.Feed, "attach",
                        lambda self, sink, exe, net_for=None, audio=0: attached.append(
                            (self, net_for)))
    loop = "http://127.0.0.1:4321/tok/s/7.ts"
    feed = cast_feed.Feed(lambda m: (loop, 0.0, m - 2.0), 1000.0,
                          kind="rewind buffer")
    rx = am.AirPlayReceiver()
    rx.bridge.exe = sys.executable
    rx.context = lambda: {"kind": "live"}
    rx.cast(am.NAME, "http://p/live/u/pw/1.m3u8", "SVT1", ["h264", "aac"],
            feed=feed)
    p = _Player.made[-1]
    try:
        assert seen["feed"] is True and attached and attached[0][0] is feed
        assert rx.bridged() and rx.bridge.feed is feed
        assert p.item.url == rx.bridge.url and not ab.is_loopback(p.item.url)
        assert rx.position() == 0.0 and rx.duration == 0.0
        # Each piece read with the source's own network options.
        assert callable(attached[0][1])
    finally:
        rx.stop()
    assert feed.closed
    _pump(qapp)
