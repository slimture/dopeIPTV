"""Casting and local playback are two ends of the same handover.

Starting a cast frees the local stream (the receiver fetches the URL itself,
which costs one provider connection). Playing something in the app is that
same switch in reverse, so it has to end the cast - otherwise the account
holds two connections at once and, on a tight limit, the new stream is simply
refused, which looks like the app failing to play anything after a cast.

The stop talks to the receiver over the network, so it must not run on the UI
thread: playback can never wait for a TV to answer.

Casting also blacks out the player pane, so the cast strip above it is the
only thing left saying that anything is happening - it has to name both the
device and what was sent there.

The window methods are borrowed onto a stub: a real MainWindow needs a GL
surface and a provider, and none of this touches either.
"""
import threading

import pytest

_METHODS = ("_stop_cast_for_local_playback", "_end_cast", "show_cast_strip",
            "_log_local_codecs")


def _window():
    try:
        from dopeiptv.ui.main_window import MainWindow
    except Exception as e:                       # pragma: no cover - no PyQt6
        pytest.skip(f"main window unavailable ({e})")
    cls = type("_StubWindow", (),
               {name: getattr(MainWindow, name) for name in _METHODS})
    return cls()


class _Cast:
    def __init__(self, active: bool) -> None:
        self.active = object() if active else None
        self.stopped = threading.Event()
        self.thread: str | None = None

    def stop(self) -> None:
        self.thread = threading.current_thread().name
        self.active = None
        self.stopped.set()


class _Bar:
    def __init__(self) -> None:
        self.shown = False

    def show(self) -> None:
        self.shown = True

    def hide(self) -> None:
        self.shown = False


class _Lbl:
    def __init__(self) -> None:
        self.text = ""
        self.visible = True

    def setText(self, t) -> None:
        self.text = t

    def setVisible(self, v) -> None:
        self.visible = bool(v)


def _with_strip():
    w = _window()
    w.cast_bar, w.cast_bar_lbl, w.cast_bar_title = _Bar(), _Lbl(), _Lbl()
    return w


def test_local_playback_ends_a_running_cast():
    w = _window()
    w.cast = _Cast(active=True)
    w._stop_cast_for_local_playback()
    assert w.cast.stopped.wait(10), "the cast was never stopped"
    assert w.cast.thread != threading.current_thread().name, (
        "stopping must not block the UI thread")


def test_nothing_happens_when_no_cast_is_running():
    w = _window()
    w.cast = _Cast(active=False)
    w._stop_cast_for_local_playback()
    assert not w.cast.stopped.wait(0.5)


def test_a_window_without_a_cast_manager_is_fine():
    _window()._stop_cast_for_local_playback()


def test_the_strip_names_the_device_and_what_is_playing():
    w = _with_strip()
    w.show_cast_strip("Alva TV", "SVT1")
    assert w.cast_bar.shown
    assert "Alva TV" in w.cast_bar_lbl.text
    assert w.cast_bar_title.text == "SVT1"
    assert w._cast_device == "Alva TV"


def test_the_strip_goes_away_when_the_cast_ends():
    w = _with_strip()
    w.show_cast_strip("Alva TV", "SVT1")
    w.cast = _Cast(active=True)
    w._stop_cast_for_local_playback()
    assert not w.cast_bar.shown
    assert w._cast_device is None
    assert w.cast.stopped.wait(10)


def test_an_untitled_cast_hides_the_second_line():
    w = _with_strip()
    w.show_cast_strip("Alva TV", "")
    assert w.cast_bar.shown
    assert w.cast_bar_title.visible is False


def test_the_codecs_of_what_is_playing_are_written_down(monkeypatch):
    """An HLS media playlist carries no codec information at all, and the
    receiver never says which part it choked on - mpv, decoding the very same
    stream, is the only place the answer exists."""
    import types

    import dopeiptv.ui.main_window as mwmod
    rec = _Log()
    monkeypatch.setattr(mwmod, "log", rec)
    w = _window()
    w.player = types.SimpleNamespace(video=types.SimpleNamespace(
        mpv=types.SimpleNamespace(track_list=[
            {"type": "video", "selected": True, "codec": "hevc"},
            {"type": "audio", "selected": True, "codec": "ac3"},
            {"type": "audio", "selected": False, "codec": "aac"},
        ])))
    w._log_local_codecs()
    assert any("video hevc" in ln for ln in rec.lines), rec.lines
    assert any("audio ac3" in ln for ln in rec.lines), rec.lines
    assert not any("aac" in ln for ln in rec.lines)


def test_no_player_no_codec_line():
    _window()._log_local_codecs()


# ── the manager itself, with a stand-in for pychromecast ──────────────────

def _fake_pychromecast(order=None):
    import types

    class FakeStatus:
        def __init__(self, state="PLAYING", why=None):
            self.player_state = state
            self.idle_reason = why

    class FakeMedia:
        def __init__(self, dev):
            self.dev = dev
            self.status = FakeStatus()

        def register_status_listener(self, listener):
            pass

        def play_media(self, url, ctype, title=None):
            self.dev.plays.append((url, ctype))
            self.dev.played = (url, ctype, title)

        def block_until_active(self, timeout=10):
            pass

    class FakeSocket:
        def register_connection_listener(self, listener):
            pass

    class FakeDevice:
        def __init__(self, name):
            self.name = name
            self.played = None
            self.plays = []
            self.media_controller = FakeMedia(self)
            self.socket_client = FakeSocket()

        def refuse(self):
            self.media_controller.status = FakeStatus("IDLE", "ERROR")

        def wait(self, timeout=10):
            pass

        def disconnect(self, timeout=2):
            if order is not None:
                order.append("device")

    class FakeBrowser:
        def stop_discovery(self):
            if order is not None:
                order.append("browser")

    def get_chromecasts(timeout=6):
        return [FakeDevice("Alva TV")], FakeBrowser()

    return types.SimpleNamespace(get_chromecasts=get_chromecasts)


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, msg, *args) -> None:
        self.lines.append(msg % args if args else msg)

    def debug(self, *a, **k) -> None:
        pass


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def iter_content(self, n):
        yield self.body[:n]


def test_the_playlist_we_hand_over_is_written_down(monkeypatch):
    """A Chromecast that refuses a stream says only IDLE/ERROR, never why -
    so the manifest itself has to be in the log."""
    from dopeiptv.providers import chromecast as cm
    rec = _Log()
    monkeypatch.setattr(cm, "log", rec)
    cm._log_playlist_head(_Response(
        b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n/hls/x/1.ts\n"))
    assert "playlist head" in rec.lines[0]
    assert "/hls/x/1.ts" in rec.lines[0]


def test_a_stream_that_is_not_a_playlist_is_called_out(monkeypatch):
    """Some panels answer an .m3u8 request with the raw TS stream, which the
    receiver can never play whatever we label it."""
    from dopeiptv.providers import chromecast as cm
    rec = _Log()
    monkeypatch.setattr(cm, "log", rec)
    cm._log_playlist_head(_Response(b"\x47\x40\x00\x10" * 8))
    assert "not a playlist" in rec.lines[0]


def _manager(monkeypatch, order=None):
    from dopeiptv.providers import chromecast as cm
    monkeypatch.setattr(cm, "_pychromecast", _fake_pychromecast(order))
    monkeypatch.setattr(cm, "_pc_checked", True)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: (u, "application/x-mpegURL"))
    return cm.ChromecastManager()


def test_casting_a_device_from_last_time_discovers_it_first(monkeypatch):
    """The dialog offers the devices you cast to last time straight away, so
    the first press can name a device this run has not discovered yet. That
    used to fail with 'not found - rescan', which meant every cast took two
    presses."""
    m = _manager(monkeypatch)
    assert m.devices == []                       # nothing discovered yet
    assert m.cast("Alva TV", "http://x/y.m3u8", "SVT1") == "Alva TV"
    assert m.active is not None
    assert m.active.played[0] == "http://x/y.m3u8"


def test_the_resolved_address_goes_first(monkeypatch):
    """Side-by-side logs of a channel that casts and one that does not both
    show the panel's own address refused outright - only the resolved CDN
    address ever reaches BUFFERING. So that is the one to try first, and a
    cast that works never sees the second attempt at all."""
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/x", "application/x-mpegURL"))
    m.scan()
    m.cast("Alva TV", "http://panel/y.m3u8", "SVT1")
    assert [u for u, _c in m.devices[0].plays] == ["http://cdn/x"]


def test_a_refused_address_falls_back_to_the_panel_url(monkeypatch):
    """A different request against a different host, and by then the cast has
    failed anyway - so it costs nothing but the wait."""
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/live/play/token/9851",
                                   "application/x-mpegURL"))
    m.scan()
    m.devices[0].refuse()
    m.cast("Alva TV", "http://panel/live/u/pw/9851.m3u8", "SVT1")
    tried = [u for u, _c in m.devices[0].plays]
    assert tried == ["http://cdn/live/play/token/9851",
                     "http://panel/live/u/pw/9851.m3u8"], tried


def test_a_stream_the_receiver_cannot_play_says_so(monkeypatch):
    """Raw MPEG-TS and Matroska are not on the Cast platform's list at all.
    Handing one over ends in a silent IDLE/ERROR every time, so a sentence in
    the dialog beats a black TV."""
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: (u, "video/mp2t"))
    with pytest.raises(RuntimeError, match="video/mp2t"):
        m.cast("Alva TV", "http://x/y.ts", "SVT1")


def test_a_rescan_drops_the_devices_before_the_browser(monkeypatch):
    """Every device holds the browser's zeroconf instance and its socket
    thread reaches for it on reconnect - and stop_discovery() closes that
    instance. The other order crashed inside pychromecast's own thread:
    'Zeroconf instance loop must be running, was it already stopped?'"""
    order: list[str] = []
    m = _manager(monkeypatch, order)
    m.scan()
    assert order == []                           # nothing to tear down yet
    m.scan()
    assert order == ["device", "browser"], order
    order.clear()
    m.shutdown()
    assert order == ["device", "browser"], order
