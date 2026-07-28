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
import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    """The strip's buttons carry drawn icons, and drawing needs a GUI
    application to exist - a QPixmap without one aborts the interpreter."""
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])

_METHODS = ("_stop_cast_for_local_playback", "_end_cast", "show_cast_strip",
            "_local_codecs", "_toggle_cast_pause", "_cast_from_archive",
            "_save_cast_position", "_recast_with", "_track_label",
            "_cast_volume", "_cast_quality", "_cast_quality_key",
            "_set_cast_quality", "_toggle_cast_mute", "_show_cast_volume")


class _Resume0:
    """A store that answers like the real one: a position under a minute is
    'at the start', and storing one DROPS whatever was there."""

    def __init__(self):
        self.saved = {"vod:42": 1830}

    def record(self, group, key, pos, dur, item=None, series_ctx=None):
        if 60 < pos < dur * 0.95:
            self.saved[f"{group}:{key}"] = round(pos)
        else:
            self.saved.pop(f"{group}:{key}", None)


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
        self.icon = None

    def setText(self, t) -> None:
        self.text = t

    def setIcon(self, i) -> None:
        # The strip's buttons carry drawn icons, not characters: a font
        # without a glyph draws an empty box, which is what they became.
        self.icon = i

    def setVisible(self, v) -> None:
        self.visible = bool(v)


class _Slider:
    def __init__(self):
        self.value_ = 0

    def blockSignals(self, _b):
        pass

    def setValue(self, v):
        self.value_ = v


def _with_strip():
    w = _window()
    w.cast_bar, w.cast_bar_lbl, w.cast_bar_title = _Bar(), _Lbl(), _Lbl()
    w.cast_bar_pause = _Lbl()
    w.cast_bar_mute, w.cast_bar_vol = _Lbl(), _Slider()
    w.cast = _Cast(active=False)
    w._cast_ctx = {}
    return w


def test_switching_tracks_picks_up_where_the_tv_is(monkeypatch):
    """A subtitle is burned into the picture, so there is no switching it in
    place - the stream is built again. From where the TV got to, which is the
    difference between changing the subtitles and starting the film over."""
    sent = {}
    w = _with_strip()
    w.pool = None
    w.player = None
    w._cast_device = "Alva TV"
    w.settings = _Settings()
    w.cast = _CastAt(1830.0, 6000.0)
    swede = {"index": 2, "lang": "swe", "codec": "subrip"}
    w._cast_ctx = {"url": "http://p/film.mkv", "source": "http://p/film.mkv",
                   "title": "Film", "duration": 6000.0, "audio": None,
                   "subs": None}
    import dopeiptv.ui.main_window as mwmod
    monkeypatch.setattr(mwmod, "run_async",
                        lambda pool, work, ok, err: sent.update(work=work))
    w._recast_with(None, swede)
    assert sent, "the cast is made again"
    assert w._cast_ctx["subs"] is swede

    # It is cast from the position the TV reported, not from the beginning.
    class Cast:
        def __init__(self):
            self.args = None

        def cast(self, *a, **k):
            self.args = a

    w.cast_manager = Cast()
    w.cast = _CastAt(1830.0, 6000.0)
    w.cast.cast = lambda *a, **k: sent.update(start=a[6])
    w._recast_with(None, swede)
    sent["work"]()
    assert sent["start"] == 1830.0, sent


def test_the_volume_buttons_reach_the_tv_off_the_ui_thread():
    """It is a message to a device on the network; nothing in the window
    should wait for it."""
    done = threading.Event()
    seen = {}
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.set_volume = lambda step: (seen.update(step=step, thread=
                                      threading.current_thread().name),
                                      done.set())
    w._cast_volume(0.1)
    assert done.wait(5)
    assert seen["step"] == 0.1
    assert seen["thread"] != threading.current_thread().name


def test_nothing_is_sent_when_no_cast_is_running():
    w = _with_strip()
    w.cast = _Cast(active=False)
    w.cast.set_volume = lambda step: (_ for _ in ()).throw(
        AssertionError("must not be called"))
    w._cast_volume(0.1)


def test_the_picture_setting_can_be_put_back_from_the_strip():
    """It is remembered per device, so a channel that did need scaling leaves
    it set for everything cast after it. Having to reopen the panel to undo
    that would be the wrong place to keep it."""
    recast = []
    w = _with_strip()
    w.settings = _Settings()
    w._cast_device = "Alva TV"
    w._cast_ctx = {"audio": None, "subs": None}
    w._recast_with = lambda a, s: recast.append((a, s))
    assert w._cast_quality() == "original"
    w._set_cast_quality("720p30")
    assert w._cast_quality() == "720p30"
    assert recast, "the change is shown straight away"
    # Setting the same thing again does nothing at all.
    recast.clear()
    w._set_cast_quality("720p30")
    assert recast == []
    w._set_cast_quality("original")
    assert w._cast_quality() == "original"
    # And it is per device: another one starts clean.
    w._cast_device = "Vardagsrummet"
    assert w._cast_quality() == "original"


def test_every_strip_icon_actually_draws_something():
    """They were characters - a gear, a pause bar, a minus sign - and a font
    without the glyph draws an empty box, which is what the volume buttons
    turned into on macOS."""
    from dopeiptv.ui.widgets import cast_strip_icon
    for kind in ("minus", "plus", "tracks", "pause", "play"):
        img = cast_strip_icon(kind, "#ffffff").pixmap(42, 42).toImage()
        ink = sum(1 for x in range(img.width()) for y in range(img.height())
                  if img.pixelColor(x, y).alpha() > 20)
        assert ink > 60, f"{kind} drew almost nothing ({ink} px)"


def test_the_volume_slider_starts_where_the_television_is():
    """Not where the app guessed: the receiver's own level is the one the TV
    remote changes, and it is the only one that is true."""
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.volume = lambda: (0.35, True)
    w._show_cast_volume()
    assert w.cast_bar_vol.value_ == 35
    assert w._cast_muted is True
    assert w.cast_bar_mute.icon is not None


def test_mute_is_a_toggle_and_reaches_the_tv():
    done = threading.Event()
    seen = {}
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.set_muted = lambda m: (seen.update(muted=m), done.set())
    w._cast_muted = False
    w._toggle_cast_mute()
    assert done.wait(5)
    assert seen["muted"] is True and w._cast_muted is True


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
    w._local_codecs()
    assert any("video hevc" in ln for ln in rec.lines), rec.lines
    assert any("audio ac3" in ln for ln in rec.lines), rec.lines
    assert not any("aac" in ln for ln in rec.lines)


def test_no_player_no_codec_line():
    _window()._local_codecs()


def test_pausing_a_film_is_the_receiver_s_own_pause():
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.paused = threading.Event()
    w.cast.pause = w.cast.paused.set
    w.cast.resume = lambda: w.cast.paused.clear()
    w._cast_ctx = {"archive": False}
    w._cast_paused_at = None
    w._toggle_cast_pause()
    assert w.cast.paused.wait(5)
    paused_icon = w.cast_bar_pause.icon
    assert paused_icon is not None
    w._toggle_cast_pause()
    assert w.cast_bar_pause.icon is not paused_icon, "back to the pause icon"


def test_pausing_live_television_comes_back_from_the_archive():
    """A receiver cannot pause a live stream - there is nothing buffered ahead
    to come back to. The provider's archive answers instead: the moment you
    pressed pause is remembered and play casts the channel again from there,
    which is what a pause on live television has to mean."""
    from datetime import datetime, timedelta
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.pause = lambda: None
    resumed = {}
    w._cast_from_archive = lambda at: resumed.setdefault("at", at)
    w._cast_ctx = {"archive": True, "sid": 9851, "title": "SVT1"}
    w._cast_paused_at = None
    w._toggle_cast_pause()
    paused_at = w._cast_paused_at
    assert paused_at is not None
    w._cast_paused_at = paused_at - timedelta(minutes=3)
    w._toggle_cast_pause()
    assert resumed["at"] <= datetime.now()
    assert w._cast_paused_at is None


def test_the_archive_url_starts_where_you_paused(monkeypatch):
    from datetime import datetime, timedelta
    asked = {}

    class Client:
        def timeshift_url(self, sid, start, minutes):
            asked.update(sid=sid, start=start, minutes=minutes)
            return "http://p/timeshift/9851"

    w = _with_strip()
    w.client = Client()
    w.pool = None
    w._cast_device = "Alva TV"
    w._cast_ctx = {"archive": True, "sid": 9851, "title": "SVT1"}
    sent = {}
    import dopeiptv.ui.main_window as mwmod
    monkeypatch.setattr(mwmod, "run_async",
                        lambda pool, work, ok, err: sent.update(work=work))
    at = datetime.now() - timedelta(minutes=5)
    w._cast_from_archive(at)
    assert asked["sid"] == 9851 and asked["start"] == at
    assert asked["minutes"] > 240, asked          # room to keep watching
    assert sent, "the archive URL is cast"


class _Settings:
    def __init__(self):
        self.data = {}

    def value(self, key, default=None):
        return self.data.get(key, default)

    def setValue(self, key, val):
        self.data[key] = val


class _Resume:
    def __init__(self):
        self.saved = []

    def record(self, group, key, pos, dur, item=None, series_ctx=None):
        self.saved.append((group, key, round(pos), round(dur)))


class _CastAt:
    """A manager stand-in that has got somewhere."""

    def __init__(self, pos, dur):
        self.active = object()
        self.duration = dur
        self._pos = pos

    def position(self):
        return self._pos


def test_where_the_tv_got_to_is_kept_as_the_resume_point():
    """The receiver is the only thing that knows where the film reached, and
    it stops knowing the moment the cast ends."""
    w = _with_strip()
    w.resume = _Resume()
    w.cast = _CastAt(1830.0, 6000.0)
    w._cast_ctx = {"group": "vod", "key": "42", "item": {"name": "Film"}}
    w._save_cast_position()
    assert w.resume.saved == [("vod", "42", 1830, 6000)]


def test_a_cast_that_just_started_does_not_wipe_the_resume_point():
    """A position too small to be worth keeping is a reason to leave the
    store alone, not to write to it - writing one drops what was there, and
    the film would lose the point it already had."""
    w = _with_strip()
    w.resume = _Resume0()
    w.cast = _CastAt(12.0, 6000.0)
    w._cast_ctx = {"group": "vod", "key": "42"}
    w._save_cast_position()
    assert w.resume.saved == {"vod:42": 1830}


def test_a_live_channel_has_no_resume_point_to_keep():
    w = _with_strip()
    w.resume = _Resume()
    w.cast = _CastAt(1830.0, 0.0)
    w._cast_ctx = {"group": None, "key": None}
    w._save_cast_position()
    assert w.resume.saved == []


def test_nothing_is_kept_without_a_known_runtime():
    """A position is only meaningful against a length - and a converted
    stream arrives down a pipe with no end in it, so the receiver cannot
    report one."""
    w = _with_strip()
    w.resume = _Resume()
    w.cast = _CastAt(1830.0, 0.0)
    w._cast_ctx = {"group": "vod", "key": "42"}
    w._save_cast_position()
    assert w.resume.saved == []


# ── the manager itself, with a stand-in for pychromecast ──────────────────

def _fake_pychromecast(order=None):
    import types

    class FakeStatus:
        def __init__(self, state="PLAYING", why=None):
            self.player_state = state
            self.idle_reason = why
            self.current_time = 0.0

    class FakeMedia:
        def __init__(self, dev):
            self.dev = dev
            self.status = FakeStatus()

        def register_status_listener(self, listener):
            pass

        def play_media(self, url, ctype, title=None, current_time=None):
            self.dev.plays.append((url, ctype))
            self.dev.started_at = current_time
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
            self.started_at = None
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


def test_a_native_cast_starts_where_you_left_off(monkeypatch):
    """The receiver can be handed a start time; it does the seeking."""
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/x.mp4", "video/mp4"))
    m.scan()
    m.cast("Alva TV", "http://p/film.mp4", "Film", start=1830.0, duration=6000)
    assert m.devices[0].started_at == 1830.0
    assert m.duration == 6000
    assert m.position_offset == 0.0, "the receiver reports absolute time"


def test_a_converted_cast_is_seeked_by_ffmpeg(monkeypatch):
    """A converted stream starts at zero whatever it was seeked to, so the
    offset is added back before the position is read."""
    from dopeiptv.providers import chromecast as cm
    asked = {}
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects", lambda u: (u, "video/mp2t"))
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    monkeypatch.setattr(
        m.bridge, "start",
        lambda *a, **k: asked.update(k) or "http://me/s.mp4")
    m.scan()
    m.cast("Alva TV", "http://p/film.ts", "Film", start=1830.0, duration=6000)
    assert asked["start_at"] == 1830.0
    assert m.position_offset == 1830.0
    m.last_position = 42.0
    assert m.position() == 1872.0


def test_the_provider_is_given_a_moment_after_local_playback_stopped(
        monkeypatch):
    """These panels keep counting a session for a few seconds after the
    socket closes, and the account allows one. Going straight for the stream
    is refused for exactly that long - the receiver first, then the
    converter."""
    from dopeiptv.providers import chromecast as cm
    slept = []
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects", lambda u: ("http://cdn/x",
                                                             "video/mp4"))
    monkeypatch.setattr(cm.time, "sleep", lambda s: slept.append(s))
    m.scan()
    def long_waits():
        return [s for s in slept if s >= 2]        # not the verdict polling

    m.cast("Alva TV", "http://p/y.mp4", "Film")
    assert long_waits() == [], "nothing was stopped, nothing to wait for"
    m.cast("Alva TV", "http://p/y.mp4", "Film", settle=True)
    assert long_waits(), slept


def test_the_picture_setting_is_a_ceiling_not_an_instruction():
    """Most channels come in three versions - SD, HD and FHD - and only the
    last is beyond an older receiver. Scaling the HD one down would throw
    away picture for nothing."""
    from dopeiptv.providers.chromecast import ChromecastManager as M
    need = M._needed_quality
    # Already under it: sent as it is.
    assert need("720p", 576, 25.0) == "original"
    assert need("720p", 720, 50.0) == "original"
    assert need("720p30", 720, 25.0) == "original"
    # Over it, one way or the other.
    assert need("720p", 1080, 25.0) == "720p"
    assert need("720p30", 720, 50.0) == "720p30"
    assert need("720p30", 1080, 50.0) == "720p30"
    # Nothing to compare against: the device was given that setting for a
    # reason, so it stands.
    assert need("720p", 0, 0.0) == "720p"
    # And a device with no setting never adapts anything.
    assert need("original", 1080, 50.0) == "original"


def test_an_empty_address_is_refused_rather_than_cast(monkeypatch):
    """A panel reopened on a session whose address had not been recorded cast
    an empty string, and the receiver was left mystified."""
    m = _manager(monkeypatch)
    m.scan()
    with pytest.raises(RuntimeError, match="no address"):
        m.cast("Alva TV", "", "SVT1")
    assert m.devices[0].plays == []


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


def test_the_converter_comes_before_the_panel_url(monkeypatch):
    """Every attempt opens the stream again, and an account with a single
    connection is refused for as long as the panel keeps counting the last
    one. The panel address has never worked with this provider, so it is
    tried last - after the converter, which does."""
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/live/play/token/9851",
                                   "application/x-mpegURL"))
    monkeypatch.setattr(cm, "_probe_codecs", lambda u: [])
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    monkeypatch.setattr(m.bridge, "start", lambda *a, **k: "http://me/s.mp4")
    m.scan()
    m.devices[0].refuse()
    with pytest.raises(RuntimeError):
        m.cast("Alva TV", "http://panel/live/u/pw/9851.m3u8", "SVT1")
    tried = [u for u, _c in m.devices[0].plays]
    assert tried == ["http://cdn/live/play/token/9851",   # the real address
                     "http://me/s.mp4",                   # then converted
                     "http://panel/live/u/pw/9851.m3u8"], tried


def test_silence_is_not_treated_as_a_refusal(monkeypatch):
    """A second load replaces whatever the receiver is doing. A channel that
    is merely slow to start would be killed by the retry meant to save it, so
    only an explicit refusal may trigger one."""
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/x", "application/x-mpegURL"))
    m.scan()
    # IDLE with no reason: nothing has come back yet, one way or the other.
    m.devices[0].media_controller.status.player_state = "IDLE"
    m.devices[0].media_controller.status.idle_reason = None
    m.VERDICT_WAIT = 0.4
    m.cast("Alva TV", "http://panel/y.m3u8", "SVT1")
    assert [u for u, _c in m.devices[0].plays] == ["http://cdn/x"]


def test_the_codecs_are_read_out_of_the_transport_stream():
    """A refusal names no reason and the playlist names no codecs - the PMT
    inside the stream itself is the only place the answer exists."""
    from dopeiptv.providers.chromecast import _ts_codecs

    def packet(pid, section):
        head = bytes([0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10, 0x00])
        body = head + section
        return body + b"\xFF" * (188 - len(body))

    pat = bytes([0x00, 0xB0, 0x0D, 0x00, 0x01, 0xC1, 0x00, 0x00,
                 0x00, 0x01, 0xE1, 0x00]) + b"\x00" * 4
    pmt = bytes([0x02, 0xB0, 0x17, 0x00, 0x01, 0xC1, 0x00, 0x00,
                 0xE1, 0x01, 0xF0, 0x00,
                 0x24, 0xE1, 0x01, 0xF0, 0x00,        # HEVC video
                 0x81, 0xE1, 0x02, 0xF0, 0x00]) + b"\x00" * 4
    assert _ts_codecs(packet(0, pat) + packet(0x100, pmt)) == ["hevc", "ac3"]


def test_unreadable_bytes_yield_no_codecs():
    from dopeiptv.providers.chromecast import _ts_codecs
    assert _ts_codecs(b"") == []
    assert _ts_codecs(b"not a transport stream at all") == []


def test_a_receiver_that_takes_the_stream_never_converts_it(monkeypatch):
    """Converting is for the devices that need it. A receiver that decodes
    E-AC-3 - an Ultra, a Google TV - gets the provider's own stream untouched
    and ffmpeg is never started, whatever the codecs say."""
    from dopeiptv.providers import chromecast as cm
    started = []
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/x", "application/x-mpegURL"))
    monkeypatch.setattr(m.bridge, "start",
                        lambda *a, **k: started.append(a) or "http://me/s.mp4")
    m.scan()
    m.cast("Alva TV", "http://panel/y.m3u8", "SVT1", ["h264", "eac3"])
    assert started == [], "the stream played natively - nothing to convert"
    assert [u for u, _c in m.devices[0].plays] == ["http://cdn/x"]


def test_a_device_that_refused_once_goes_straight_to_the_converter(
        monkeypatch):
    """The second cast of an E-AC-3 channel to the same device does not spend
    twenty seconds being refused all over again."""
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/x", "application/x-mpegURL"))
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    monkeypatch.setattr(m.bridge, "start", lambda *a, **k: "http://me/s.mp4")
    m.scan()
    dev = m.devices[0]
    dev.refuse()
    with pytest.raises(RuntimeError):
        m.cast("Alva TV", "http://panel/y.m3u8", "SVT1", ["h264", "eac3"])
    assert "eac3" in m._refused["Alva TV"]
    dev.plays.clear()
    with pytest.raises(RuntimeError):
        m.cast("Alva TV", "http://panel/y.m3u8", "SVT1", ["h264", "eac3"])
    assert [u for u, _c in dev.plays] == ["http://me/s.mp4"], dev.plays


def test_an_unidentified_stream_is_converted_anyway(monkeypatch):
    """Knowing the codecs only decides whether the video can be copied
    through. Not knowing them is no reason to give up: by then the device has
    refused the stream twice and converting is the only thing left."""
    from dopeiptv.providers import chromecast as cm
    started = []
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/x", "application/x-mpegURL"))
    monkeypatch.setattr(cm, "_probe_codecs", lambda u: [])
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    monkeypatch.setattr(m.bridge, "start",
                        lambda *a, **k: started.append(a) or "http://me/s.mp4")
    m.scan()
    m.devices[0].refuse()
    with pytest.raises(RuntimeError, match="refused this stream"):
        m.cast("Alva TV", "http://panel/y.m3u8", "SVT1")
    assert started, "the converter must run even with no codec information"
    assert [u for u, _c in m.devices[0].plays][1] == "http://me/s.mp4"

    # ...and the channel is remembered, so the next cast of it does not spend
    # twenty seconds being refused all over again. Remembered per channel,
    # never per device: everything else this device plays natively still does.
    m.devices[0].plays.clear()
    with pytest.raises(RuntimeError):
        m.cast("Alva TV", "http://panel/y.m3u8", "SVT1")
    assert [u for u, _c in m.devices[0].plays][0] == "http://me/s.mp4"
    m.devices[0].plays.clear()
    with pytest.raises(RuntimeError):
        m.cast("Alva TV", "http://panel/other.m3u8", "Another channel")
    assert [u for u, _c in m.devices[0].plays][0] == "http://cdn/x", (
        "a different channel must still be tried natively first")


def test_a_channel_the_receiver_cannot_decode_is_named(monkeypatch):
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/x", "application/x-mpegURL"))
    monkeypatch.setattr(cm, "_probe_codecs", lambda u: ["hevc", "ac3"])
    m.scan()
    m.devices[0].refuse()
    with pytest.raises(RuntimeError, match="hevc \\+ ac3"):
        m.cast("Alva TV", "http://panel/y.m3u8", "SVT1")


def test_a_container_no_chromecast_plays_is_repackaged(monkeypatch):
    """Matroska and raw MPEG-TS are not on the Cast platform's list at all, so
    there is nothing to attempt natively. But the container is a separate
    question from the codecs inside it: ffmpeg repackages both into fragmented
    MP4 without touching a frame of video."""
    from dopeiptv.providers import chromecast as cm
    started = []
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects", lambda u: (u, "video/mp2t"))
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    monkeypatch.setattr(m.bridge, "start",
                        lambda *a, **k: started.append(a) or "http://me/s.mp4")
    m.scan()
    assert m.cast("Alva TV", "http://x/y.ts", "SVT1") == "Alva TV"
    assert started, "an unplayable container must go to the converter"
    # Never handed over as-is: that is a guaranteed silent IDLE/ERROR.
    assert [u for u, _c in m.devices[0].plays] == ["http://me/s.mp4"]


def test_a_container_that_cannot_be_repackaged_says_so(monkeypatch):
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects", lambda u: (u, "video/mp2t"))
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    monkeypatch.setattr(m.bridge, "start", lambda *a, **k: "http://me/s.mp4")
    m.scan()
    m.devices[0].refuse()
    with pytest.raises(RuntimeError, match="video/mp2t"):
        m.cast("Alva TV", "http://x/y.ts", "SVT1")


def test_the_tv_is_told_to_stop_before_anything_else(monkeypatch):
    """On app close this runs in a daemon thread racing os._exit, with about
    a second and a half before the process is gone. Tearing the bridge down
    first spent that budget waiting on ffmpeg and the HTTP server, and the
    STOP never reached the receiver - the cast carried on after the app had
    quit."""
    order: list[str] = []
    m = _manager(monkeypatch)
    m.scan()
    dev = m.devices[0]
    dev.media_controller.stop = lambda: order.append("tv")
    m.bridge.stop = lambda: order.append("bridge")
    m.active = dev
    m.stop()
    assert order == ["tv", "bridge"], order


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
