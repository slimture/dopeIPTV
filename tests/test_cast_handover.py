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
import inspect
import os
import threading
import time
import types

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
            "_record_cast_history", "_history_extra",
            "_show_cast_progress", "_cast_seek", "_cast_seek_released",
            "_fmt_hms", "_cast_moment", "_cast_to_moment", "_cast_go_live",
            "_local_tracks", "_cast_continue_archive", "_cast_time_at",
            "_cast_timeline", "_show_cast_timeline", "_cast_programme_at",
            "_effective_ts_minutes", "_show_cast_edge",
            "_set_cast_quality", "_toggle_cast_mute", "_show_cast_volume",
            "_cast_tracks_menu", "manage_cast", "_cast_ended_there",
            "_cast_plays_as_is", "_cast_is_kodi", "_cast_began",
            "_cast_hooks_end", "_cast_on_airplay", "_show_cast_label", "_cast_tracks_shown",
            "_cast_drop_feed", "_cast_takes_feed", "_cast_title_ended",
            "_cast_feed", "_cast_written", "_cast_from_moment",
            "_cast_send_feed", "_archive_feed", "_buffer_feed",
            "_cast_buffer", "_cast_buffer_span", "_cast_can_go_back",
            "_cast_release_buffer", "_cast_keep_buffer", "_playing_buffer",
            "_cast_hold_name", "_cast_buffer_feed", "_cast_back_steps",
            "_cast_timeshift_entries", "_open_cast_dialog", "_cast_converts",
            "_cast_keeps_buffer", "_cast_source_of", "_cast_route_from",
            "_cast_playlist", "_cast_send", "_cast_send_failed",
            "_show_cast_position")


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
    # Borrowed as they are declared: a staticmethod put into a class dict as
    # a plain function turns into an instance method and is handed a self it
    # never asked for.
    def borrow(name):
        raw = inspect.getattr_static(MainWindow, name)
        return raw if isinstance(raw, staticmethod) else getattr(
            MainWindow, name)

    ns = {name: borrow(name) for name in _METHODS}
    # The constants those methods read - a class attribute is as much part of
    # a borrowed method as its body is.
    ns.update({name: getattr(MainWindow, name)
               for name in ("ARCHIVE_LAG", "TIMESHIFT_STEPS",
                            "CAST_TIMELINE_MIN", "_cast_zap_gen",
                            "CAST_BUFFER_BEHIND_S", "CAST_BUFFER_STEPS",
                            "_cast_hooked",
                            "_CAST_RESUME_KIND", "_RESUME_GROUP",
                            "_CAST_GROUP_OF")})
    cls = type("_StubWindow", (), ns)
    return cls()


class _Bridge:
    """The converter's recording, and how much room a pause may take."""

    def __init__(self) -> None:
        self.cap = 4_500_000_000


class _Cast:
    def bridged(self) -> bool:
        return True

    def resume(self) -> None:
        self.resumed.set()

    def __init__(self, active: bool) -> None:
        self.active = object() if active else None
        self.stopped = threading.Event()
        self.thread: str | None = None
        self.at = 0.0
        self.released = threading.Event()
        self.held = threading.Event()
        self.resumed = threading.Event()
        self.bridge = _Bridge()

    def release(self) -> None:
        self.released.set()

    def pause(self) -> None:
        self.held.set()

    def position(self) -> float:
        return self.at

    def stop(self) -> None:
        self.thread = threading.current_thread().name
        self.active = None
        self.stopped.set()


class _Ticker:
    """The once-a-second ask for the receiver's position."""

    def __init__(self):
        self.running = False

    def isActive(self):
        return self.running

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


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
        self.style = ""

    def setStyleSheet(self, s) -> None:
        self.style = s

    def setText(self, t) -> None:
        self.text = t

    def setIcon(self, i) -> None:
        # The strip's buttons carry drawn icons, not characters: a font
        # without a glyph draws an empty box, which is what they became.
        self.icon = i

    def set_glyph(self, kind) -> None:
        # The card's round buttons draw their glyph themselves.
        self.icon = kind

    def setVisible(self, v) -> None:
        self.visible = bool(v)


class _Slider:
    def __init__(self):
        self.value_ = 0
        self.shown = None
        self.dragging = False
        self.segments = None

    def set_segments(self, segs):
        self.segments = list(segs)

    def blockSignals(self, _b):
        pass

    def setValue(self, v):
        self.value_ = v

    def value(self):
        return self.value_

    def isSliderDown(self):
        return False

    def setVisible(self, on):
        self.shown = on


def _with_strip():
    w = _window()
    w.cast_bar, w.cast_bar_lbl, w.cast_bar_title = _Bar(), _Lbl(), _Lbl()
    w.cast_bar_pause = _Lbl()
    w.cast_bar_mute, w.cast_bar_vol = _Lbl(), _Slider()
    w.cast_bar_seek, w.cast_bar_time = _Slider(), _Lbl()
    w.cast_bar_live = _Lbl()
    w.cast_bar_ts = _Lbl()
    w._cast_tick = _Ticker()
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
    import dopeiptv.ui.mw_cast as mwmod
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
    w._set_cast_quality("oldest")
    assert w._cast_quality() == "oldest"
    assert recast, "the change is shown straight away"
    # Setting the same thing again does nothing at all.
    recast.clear()
    w._set_cast_quality("oldest")
    assert recast == []
    w._set_cast_quality("original")
    assert w._cast_quality() == "original"
    # And it is per device: another one starts clean.
    w._cast_device = "Vardagsrummet"
    assert w._cast_quality() == "original"
    # A device set while the question was still a three-way choice is
    # remembered as "720p30", which is no longer an answer that exists. Read
    # it as the answer it was rather than as nothing at all.
    w.settings.setValue("cast_quality_Vardagsrummet", "720p30")
    assert w._cast_quality() == "oldest"


def test_every_cast_glyph_actually_draws_something():
    """They were characters - a gear, a pause bar, a minus sign - and a font
    without the glyph draws an empty box, which is what the volume buttons
    turned into on macOS. The cast card's buttons draw them."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QColor, QImage, QPainter
    from dopeiptv.ui.widgets import STRIP_GLYPHS, paint_strip_glyph
    for kind in STRIP_GLYPHS:
        img = QImage(42, 42, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        paint_strip_glyph(p, kind, 42.0, QColor("#ffffff"), "30")
        p.end()
        ink = sum(1 for x in range(img.width()) for y in range(img.height())
                  if img.pixelColor(x, y).alpha() > 20)
        assert ink > 60, f"{kind} drew almost nothing ({ink} px)"


def test_pause_is_hidden_where_pausing_cannot_work():
    """A broadcast can only be held while the converter is recording it -
    that recording IS the pause. A channel that went straight to the receiver
    has nothing behind it, and a button that cannot do what it says is worse
    than no button."""
    w = _with_strip()
    w.cast = _Cast(active=True)          # bridged() is True
    w.cast.volume = lambda: (0.5, False)

    w._cast_ctx = {"sid": 9851, "archive": True, "key": 9851}
    w.show_cast_strip("Alva TV", "SVT1")
    assert w.cast_bar_pause.visible is True

    w.cast.bridged = lambda: False       # straight to the receiver
    w.show_cast_strip("Alva TV", "SVT1")
    assert w.cast_bar_pause.visible is False, "no recording, no pause"

    # A film pauses on the receiver itself, so it keeps the button.
    w._cast_ctx = {"sid": None, "archive": False, "key": "42"}
    w.show_cast_strip("Alva TV", "Film")
    assert w.cast_bar_pause.visible is True


def test_the_volume_slider_starts_where_the_television_is():
    """Not where the app guessed: the receiver's own level is the one the TV
    remote changes, and it is the only one that is true."""
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.volume = lambda: (0.35, False)
    w._show_cast_volume()
    assert w.cast_bar_vol.value_ == 35
    assert w._cast_muted is False
    assert w.cast_bar_mute.icon is not None

    # A television that is already muted shows a slider at zero, whatever
    # level it would go back to - the two have to say the same thing.
    w.cast.volume = lambda: (0.35, True)
    w._show_cast_volume()
    assert w.cast_bar_vol.value_ == 0
    assert w._cast_muted is True


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

    import dopeiptv.ui.mw_cast as mwmod
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
    w.settings = _Settings()
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


def test_the_archive_url_starts_where_you_paused(monkeypatch):
    from datetime import datetime, timedelta
    asked = {}

    class Client:
        def timeshift_urls(self, sid, start, minutes):
            asked.update(sid=sid, start=start, minutes=minutes)
            return ["http://p/timeshift/241/x/9851.ts",
                    "http://p/timeshift/241/x/9851.m3u8"]

    w = _with_strip()
    w.client = Client()
    w.pool = None
    w._cast_device = "Alva TV"
    w._cast_ctx = {"archive": True, "sid": 9851, "title": "SVT1"}
    sent = {}
    import dopeiptv.ui.mw_cast as mwmod
    monkeypatch.setattr(mwmod, "run_async",
                        lambda pool, work, ok, err: sent.update(work=work))
    at = (datetime.now() - timedelta(minutes=10)).replace(microsecond=0)
    w._cast_from_archive(at)
    # The archive is addressed by the minute, so the request is for the minute
    # the pause fell in - and the seconds it cut off are handed to the
    # receiver as a starting offset instead. Rounding them away meant
    # rewatching up to a minute of television on every single pause.
    assert asked["sid"] == 9851
    assert asked["start"] == at.replace(second=0)
    # Generous room ahead: the .ts is one stream, not a segment list, so
    # the panel serves what exists and the rest of the request costs
    # nothing. Windowing it tightly is what built the sliver playlists.
    assert asked["minutes"] > 240, asked
    assert sent, "the archive URL is cast"
    assert w._cast_ctx["archive_from"] == at.replace(second=0)
    # The TRANSPORT STREAM, decided by the logs: every cast that actually
    # played the right content read the .ts through the converter, and
    # every freeze-after-one-second was the panel's HLS wrapper of the same
    # archive - including a minute over four minutes old, which sank the
    # theory that age was the problem.
    assert w._cast_ctx["url"].endswith(".ts")
    assert w._cast_ctx["source"].endswith(".ts")

    # A point inside the minute still being written is pulled back to one
    # the panel can serve.
    asked.clear()
    w._cast_from_archive(datetime.now())
    assert asked["start"] <= datetime.now() - w.ARCHIVE_LAG


def test_where_the_tv_got_to_is_kept_as_the_resume_point():
    """The receiver is the only thing that knows where the film reached, and
    it stops knowing the moment the cast ends."""
    w = _with_strip()
    w.resume = _Resume()
    w.cast = _CastAt(1830.0, 6000.0)
    w._cast_ctx = {"group": "vod", "key": "42", "item": {"name": "Film"}}
    w._save_cast_position()
    assert w.resume.saved == [("vod", "42", 1830, 6000)]


def test_the_position_goes_to_the_profile_that_started_the_cast():
    """A profile switch while a cast runs leaves the cast going: where the
    TV got to is the Continue watching of whoever started it, not of the
    profile in use when it ends."""
    w = _with_strip()
    started, now = _Resume(), _Resume()
    w.resume = now
    w.cast = _CastAt(1830.0, 6000.0)
    w._cast_ctx = {"group": "vod", "key": "42", "item": {"name": "Film"},
                   "resume": started}
    w._save_cast_position()
    assert started.saved == [("vod", "42", 1830, 6000)]
    assert now.saved == []


def test_the_cast_goes_into_the_history_of_the_profile_that_started_it():
    """The receiver says it plays a few seconds after the cast was asked
    for - by then the phone may have switched the profile: the cast is in
    the History of whoever started it."""
    class Hist:
        def __init__(self):
            self.added = []

        def add(self, url, title, icon, key, kind, extra=None):
            self.added.append((key, kind))

    w = _with_strip()
    started, now = Hist(), Hist()
    w.history = now
    w._cast_ctx = {"row_url": "http://p/film.mkv", "key": "42", "kind": "movie",
                   "title": "Film", "item": {"name": "Film"}, "history": started}
    w._record_cast_history()
    assert started.added == [("42", "movie")] and now.added == []


def test_private_viewing_turned_on_mid_cast_keeps_the_cast_private_to_its_end():
    """As the docked player's: what is cast when private viewing goes on is
    private for every reporter to its end - where the TV got to is not kept
    once private viewing is off again; a title cast after it is."""
    import types

    from dopeiptv.core import privacy
    from dopeiptv.ui.main_window import MainWindow
    w = _with_strip()
    started = _Resume()
    w.resume, w.player = started, None
    w.cast = _CastAt(1830.0, 6000.0)
    w._cast_device = "Alva TV"
    w._show_toast = lambda *a, **k: None
    private = types.MethodType(MainWindow.set_private_viewing, w)
    try:
        privacy.set_private(False)
        privacy.playback_stopped()
        w._cast_ctx = {"group": "vod", "key": "42", "kind": "movie",
                       "item": {"name": "Film"}, "resume": started}
        privacy.playback_started("movie", "42")       # the cast began
        private(True)
        private(False)
        assert privacy.playback_private("movie", "42"), "History, events, Discord..."
        w._save_cast_position()
        assert started.saved == []
        # The next title on the TV, chosen after private viewing: kept.
        w._cast_ctx = dict(w._cast_ctx, key="43")
        privacy.playback_started("movie", "43")
        w._save_cast_position()
        assert started.saved == [("vod", "43", 1830, 6000)]
    finally:
        privacy.set_private(False)
        privacy.playback_stopped()


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

        def play_media(self, url, ctype, title=None, current_time=None,
                       stream_type="LIVE", media_info=None):
            self.dev.plays.append((url, ctype))
            self.dev.announced.append((stream_type, media_info, title))
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
            self.announced = []
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
        return [s for s in slept if s >= 1]        # not the verdict polling

    m.cast("Alva TV", "http://p/y.mp4", "Film")
    assert long_waits() == [], "nothing was stopped, nothing to wait for"
    m.cast("Alva TV", "http://p/y.mp4", "Film", settle=True)
    assert long_waits(), slept
    # Kept short: this is on every cast that follows local playback, and a
    # refusal is no longer fatal - the converter tries again by itself.
    assert max(long_waits()) <= 2, slept


def test_a_zap_stops_the_last_channel_s_converter_first(monkeypatch):
    """Another channel on the same Chromecast (the window zaps with settle
    while a converter runs): the old ffmpeg lets go of the account's
    connection before the provider is given its moment and the new one is
    asked for - and none is left running that the receiver no longer
    reads."""
    from dopeiptv.providers import chromecast as cm
    order = []
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm.time, "sleep",
                        lambda s: order.append("wait") if s >= 1 else None)
    monkeypatch.setattr(m.bridge, "stop", lambda: (order.append("stop"),
                                                   setattr(m.bridge, "path", None)))
    m.scan()
    m.bridge.path = "/token/"                     # the last channel, converted
    m.cast("Alva TV", "http://p/live/u/pw/2.m3u8", "SVT2", settle=True)
    assert order[:2] == ["stop", "wait"] and not m.bridged()
    assert m.active.played[0] == "http://p/live/u/pw/2.m3u8"
    # A track put back to its default: native again, and the converter
    # that fed the receiver goes once the receiver has the new stream.
    order.clear()
    m.bridge.path = "/token/"
    m.cast("Alva TV", "http://p/live/u/pw/3.m3u8", "SVT3")
    assert order == ["stop"] and not m.bridged()


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

    def bridged(self):
        return True

    def is_dlna(self, _name):
        return False


class _History:
    def __init__(self):
        self.rows = []

    def add(self, url, title, icon, key, kind, extra=None):
        self.rows.append((url, title, icon, key, kind, extra))


def test_what_you_watch_on_the_tv_lands_in_history_too():
    """Every other route into playback runs through _start_playback, which a
    cast deliberately does not - the stream never touches this machine. So an
    evening's television watched on the TV left no trace at all, and could not
    be picked up again from History the way anything played here can."""
    w = _with_strip()
    w.history = _History()
    w.xmltv = _Xmltv()
    w._effective_ts_minutes = lambda it: 0
    w.series_ctx = None
    w._cast_ctx = {
        "title": "SVT1 HD", "key": 9851, "kind": "live",
        "row_url": "http://p/live/u/pw/9851.m3u8", "sid": 9851,
        "archive": True,
        "item": {"stream_id": 9851, "num": 1, "tv_archive": 1,
                 "tv_archive_duration": 7, "stream_icon": "http://p/1.png"}}
    w.show_cast_strip("Alva TV", "SVT1 HD")
    assert len(w.history.rows) == 1
    url, title, icon, key, kind, extra = w.history.rows[0]
    assert url == "http://p/live/u/pw/9851.m3u8"
    assert (title, key, kind) == ("SVT1 HD", 9851, "live")
    assert icon == "http://p/1.png"
    # The archive depth comes along, so replaying it from History still has
    # timeshift and catch-up - exactly as when it is played here.
    assert extra["tv_archive"] == 1 and extra["tv_archive_duration"] == 7

    # An archive resume replaces the address on the receiver with a timeshift
    # URL good for a few minutes. History must not learn that one.
    w._cast_ctx["url"] = "http://p/timeshift/u/pw/241/x/9851.ts?token=abc"
    w.show_cast_strip("Alva TV", "SVT1 HD")
    assert w.history.rows[-1][0] == "http://p/live/u/pw/9851.m3u8"

    # Taking the strip down is not a play.
    w.show_cast_strip(None)
    assert len(w.history.rows) == 2


def test_an_episode_cast_remembers_which_series_it_belongs_to():
    """Without it the row degrades to a context-less "movie": restarted from
    zero, duplicated in History and posterless."""
    w = _with_strip()
    w.history = _History()
    w.xmltv = _Xmltv()
    w._effective_ts_minutes = lambda it: 0
    w.series_ctx = {"series_id": 77, "name": "Bron", "cover": "http://p/c.jpg"}
    w._cast_ctx = {"title": "S01 E02", "key": "77:1:2", "kind": "episode",
                   "row_url": "http://p/series/u/pw/5.mkv", "item": {}}
    w.show_cast_strip("Alva TV", "S01 E02")
    extra = w.history.rows[0][-1]
    assert extra["_series_ctx"]["series_id"] == 77
    assert extra["name"] == "Bron · S01 E02"


class _CastSeek(_CastAt):
    def __init__(self, pos, dur, bridged):
        super().__init__(pos, dur)
        self.bridged_ = bridged
        self.sought = None

    def bridged(self):
        return self.bridged_

    def seek(self, to):
        self.sought = to


def test_a_film_on_the_tv_can_be_moved_to_another_point():
    """A file the receiver fetched itself it can seek on its own. What the
    converter serves it cannot - that is a pipe with no length and no index -
    so moving inside it means building the stream again from the new point."""
    w = _with_strip()
    w.settings = _Settings()
    w._cast_device = "Alva TV"
    w._cast_ctx = {"url": "http://p/film.mp4", "audio": None, "subs": None}

    # Fetched by the receiver: it does the seeking.
    w.cast = _CastSeek(600.0, 6000.0, bridged=False)
    w._cast_seek(1800.0)
    for _ in range(50):
        if w.cast.sought is not None:
            break
        threading.Event().wait(0.02)
    assert w.cast.sought == 1800.0

    # Coming through the converter: built again from there.
    again = []
    w.cast = _CastSeek(600.0, 6000.0, bridged=True)
    w._recast_with = lambda a, s, start=None: again.append(start)
    w._cast_seek(1800.0)
    assert again == [1800.0]


def test_the_progress_bar_is_for_things_with_an_end():
    """A broadcast has no end to measure against, so a bar showing how far
    through it you are would be showing nothing."""
    w = _with_strip()
    w._cast_device = "Alva TV"

    w.cast = _CastAt(1830.0, 6000.0)
    w._show_cast_progress()
    assert w.cast_bar_seek.shown is True
    assert w._cast_tick.isActive() is True
    assert w.cast_bar_seek.value_ == 305        # 1830 of 6000, in thousandths
    assert w.cast_bar_time.text == "30:30 / 1:40:00"

    # A broadcast has no length and so no bar - but the ticker keeps going,
    # because a timeshifted channel is exactly the thing that has to be
    # watched for the end of its playlist.
    w.cast = _CastAt(0.0, 0.0)
    w._cast_ctx = {}
    w._show_cast_progress()
    assert w.cast_bar_seek.shown is False
    assert w._cast_tick.isActive() is True

    # Nothing casting at all: nothing to ask about each second.
    w._cast_device = None
    w._show_cast_progress()
    assert w._cast_tick.isActive() is False


def test_the_archive_on_the_tv_answers_the_same_way_it_does_here():
    """A cast channel with an archive can be wound back and pointed at an
    earlier programme exactly as it can in the player - it is the same
    archive, asked the same way. What differs is only where the picture
    currently is: a step back has to be a step back from THERE, not from
    live, or winding back twice would land in the same place twice."""
    from datetime import datetime, timedelta
    w = _with_strip()
    w.cast = _Cast(active=True)
    w._cast_device = "Alva TV"
    points = []
    w._cast_from_archive = lambda at, settle=False: points.append(at)
    w._effective_ts_minutes = lambda it: 2880          # two days of archive
    w._cast_ctx = {"archive": True, "sid": 9851, "title": "SVT1",
                   "item": {"stream_id": 9851}}

    # Live: the moment is now, and a step back is a step back from now.
    before = datetime.now()
    w._cast_to_moment(w._cast_moment() - timedelta(minutes=30))
    assert before - timedelta(minutes=31) < points[0] < before

    # Already in the archive: from where the picture IS.
    began = datetime.now() - timedelta(hours=3)
    w._cast_ctx["archive_from"] = began
    w.cast.at = 600.0                                  # ten minutes in
    assert w._cast_moment() == began + timedelta(seconds=600)
    points.clear()
    w._cast_to_moment(w._cast_moment() - timedelta(minutes=30))
    assert points == [began + timedelta(seconds=600) - timedelta(minutes=30)]

    # Never past what the archive holds, and never into the minute that has
    # not been written yet.
    points.clear()
    w._cast_to_moment(datetime.now() - timedelta(days=7))
    assert points[0] > datetime.now() - timedelta(minutes=2881)
    points.clear()
    w._cast_to_moment(datetime.now() + timedelta(hours=1))
    assert points[0] <= datetime.now() - timedelta(seconds=59)


def test_going_live_leaves_the_archive_behind(monkeypatch):
    """The row's own address, not the timeshift URL the session drifted to."""
    from datetime import datetime
    sent = {}
    w = _with_strip()
    w.pool = None
    w.player = None
    w.settings = _Settings()
    w._cast_device = "Alva TV"
    w.cast = _Cast(active=True)
    w._cast_ctx = {"archive": True, "sid": 9851, "title": "SVT1",
                   "row_url": "http://p/live/u/pw/9851.m3u8",
                   "row_source": "http://p/live/u/pw/9851.ts",
                   "url": "http://p/timeshift/u/pw/241/x/9851.ts",
                   "archive_from": datetime.now(), "item": {}}
    import dopeiptv.ui.mw_cast as mwmod
    monkeypatch.setattr(mwmod, "run_async",
                        lambda pool, work, ok, err: sent.update(work=work))
    w._cast_go_live()
    assert w._cast_ctx["url"] == "http://p/live/u/pw/9851.m3u8"
    assert w._cast_ctx["archive_from"] is None
    assert sent, "the live address is cast"


def test_a_film_handed_over_from_the_player_knows_how_long_it_is():
    """The ordinary way of casting a film is to be watching it here first,
    and then the track list comes from mpv rather than from ffprobe - opening
    the stream a second time costs a connection these accounts do not have.
    Without the length coming along, the strip had nothing to measure a
    position against and offered no way to move within the film at all."""
    w = _window()
    w._playing_key = 5
    w._item_key = lambda it: it.get("stream_id")
    w._play_kind_for = lambda it: "movie"

    class Mpv:
        duration = 6000.0
        height = 1080
        container_fps = 25.0
        track_list = [{"type": "video", "demux-h": 1080, "demux-fps": 25.0},
                      {"type": "audio", "codec": "aac", "lang": "swe"}]

    class Video:
        mpv = Mpv()

    class Player:
        video = Video()

    w.player = Player()
    got = w._local_tracks({"stream_id": 5})
    assert got["duration"] == 6000.0
    assert got["height"] == 1080
    assert len(got["audio"]) == 1

    # Any other row is left to ffprobe, which answers for what is not playing.
    assert w._local_tracks({"stream_id": 6}) == {}


def test_the_picture_question_names_the_device_in_the_strip_menu_too():
    """The string carries the receiver's name, and the strip's own menu was
    handing it none - so it read out the placeholder instead of a device."""
    from dopeiptv.i18n import tr
    w = _with_strip()
    w._cast_device = "Alva TV"
    label = tr("cast_older_device", name=w._cast_device)
    assert "Alva TV" in label
    assert "{name}" not in label


def test_muting_the_tv_takes_the_slider_with_it():
    """A slider sitting at half while nothing comes out of the television is
    the control disagreeing with itself."""
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.muted = None
    w.cast.set_muted = lambda on: setattr(w.cast, "muted", on)
    w.cast.set_volume = lambda lvl: setattr(w.cast, "level", lvl)
    w.cast_bar_vol.setValue(70)

    w._toggle_cast_mute()
    assert w.cast_bar_vol.value() == 0
    for _ in range(50):
        if w.cast.muted is True:
            break
        threading.Event().wait(0.02)
    assert w.cast.muted is True

    # And back to where it was, not to a guess: the level was never changed.
    w._toggle_cast_mute()
    assert w.cast_bar_vol.value() == 70

    # Reaching for the volume while muted means you want to hear it.
    w._toggle_cast_mute()
    assert w.cast_bar_vol.value() == 0
    w._cast_volume(0.4)
    assert w._cast_muted is False
    assert w.cast_bar_vol.value() == 40


def test_the_archive_is_never_asked_for_a_minute_it_has_not_written():
    """A panel writes catch-up as the broadcast goes out, so the current
    minute is not in it. Asking for it comes back as one unfinished segment
    with #EXT-X-ENDLIST after it, which the receiver shows as a frozen
    picture and a spinner - which is what pausing a channel and resuming it
    a few seconds later did every time."""
    from datetime import datetime, timedelta
    asked = {}

    class Client:
        def timeshift_urls(self, sid, start, minutes):
            asked.update(start=start, minutes=minutes)
            return ["http://p/ts.ts", "http://p/ts.m3u8"]

    w = _with_strip()
    w.client = Client()
    w.pool = None
    w.player = None
    w.settings = _Settings()
    w._cast_device = "Alva TV"
    w._local_codecs = lambda: []
    w._cast_ctx = {"archive": True, "sid": 9851, "title": "SVT1", "item": {}}
    import dopeiptv.ui.mw_cast as mwmod
    real, mwmod.run_async = mwmod.run_async, lambda p, work, ok, err: None
    try:
        w._cast_from_archive(datetime.now() - timedelta(seconds=5))
    finally:
        mwmod.run_async = real
    assert asked["start"] <= datetime.now() - w.ARCHIVE_LAG


def test_a_timeshifted_cast_asks_for_the_next_stretch_when_one_runs_out():
    """The archive comes as a finite playlist: it stops where it caught up
    with now. The player asks for more as it goes and a cast cannot, so an
    hour behind live used to simply end when the requested stretch ran out."""
    from datetime import datetime, timedelta
    w = _with_strip()
    w.cast = _Cast(active=True)
    w._cast_device = "Alva TV"
    began = datetime.now() - timedelta(hours=1)
    w._cast_ctx = {"archive": True, "sid": 9851, "archive_from": began,
                   "item": {}}
    w.cast.at = 1800.0                       # half an hour into it
    asked = []
    w._cast_from_archive = lambda at, settle=False: asked.append(at)
    w._cast_paused_at = None

    # Still playing: nothing to do.
    w.cast.state = "PLAYING/None"
    w._cast_continue_archive()
    assert asked == []

    # The playlist ran out - carry on from exactly where it stopped.
    w.cast.state = "IDLE/FINISHED"
    w._cast_continue_archive()
    assert asked == [began + timedelta(seconds=1800)]

    # And only once: loading the next stretch takes a few seconds, during
    # which the receiver still reports the end of the last one.
    w._cast_continue_archive()
    assert len(asked) == 1

    # A live cast has no playlist to run out of.
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {}}
    w._cast_continued = 0.0
    w._cast_continue_archive()
    assert len(asked) == 1


def test_the_cast_bars_take_a_click_like_every_other_bar_of_their_kind():
    """A slider you have to drag, when the player's own bar right below it
    takes a click, reads as broken. Both cast bars are that same bar - so a
    click lands where you clicked, and hovering says what is under the cursor
    before you commit to it."""
    from dopeiptv.media.embedded import _SeekSlider
    import inspect

    src = inspect.getsource(_SeekSlider)
    assert "def mousePressEvent" in src, "a click has to jump, not page"
    assert "setMouseTracking(True)" in src, "hover needs move events"
    assert "set_time_provider" in src

    # And the cast card has one for each, with something to say on hover.
    import dopeiptv.ui.cast_card as card_mod
    import dopeiptv.ui.main_window as mwmod
    card = inspect.getsource(card_mod)
    assert "self.seek = _SeekSlider()" in card
    assert "self.vol = _SeekSlider()" in card
    build = inspect.getsource(mwmod)
    assert "card.seek.set_time_provider" in build
    assert "card.vol.set_time_provider" in build


def test_the_time_under_the_cursor_is_named():
    w = _with_strip()
    w.cast = _CastAt(0.0, 6000.0)
    assert w._cast_time_at(0.0) == "0:00"
    assert w._cast_time_at(0.5) == "50:00"
    assert w._cast_time_at(1.0) == "1:40:00"
    # A broadcast has no length, so there is no time to name.
    w.cast = _CastAt(0.0, 0.0)
    assert w._cast_time_at(0.5) == ""


def test_a_paused_cast_is_not_mistaken_for_one_that_ran_out():
    from datetime import datetime, timedelta
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.cast.state = "IDLE/FINISHED"
    w._cast_device = "Alva TV"
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {},
                   "archive_from": datetime.now() - timedelta(minutes=10)}
    asked = []
    w._cast_from_archive = lambda at, settle=False: asked.append(at)
    w._cast_paused_at = datetime.now()
    w._cast_continue_archive()
    assert asked == [], "a pause is not an archive running out"


class _Xmltv:
    def __init__(self, progs=()):
        self.progs = list(progs)

    def programmes_in(self, it, start, stop):
        return [p for p in self.progs
                if p["stop_timestamp"] > start and p["start_timestamp"] < stop]


def test_a_broadcast_gets_a_timeline_rather_than_no_bar_at_all():
    """A broadcast has no length to run a position bar against, so the strip
    showed nothing for the very channels that can be moved around in most
    freely. What it spans instead is time itself - and where in it the
    picture is, is also what says a channel is paused."""
    from datetime import datetime, timedelta
    import time as _t
    w = _with_strip()
    w.cast = _CastAt(0.0, 0.0)
    w._cast_device = "Alva TV"
    w.xmltv = _Xmltv()
    w._effective_ts_minutes = lambda it: 4320        # three days of archive
    began = datetime.now() - timedelta(hours=1)
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {"stream_id": 9851},
                   "archive_from": began}

    # Six hours of it, not three days: a week across two hundred pixels is a
    # bar where every click is half an hour out.
    start, span = w._cast_timeline()
    assert span == w.CAST_TIMELINE_MIN * 60
    assert abs((_t.time() - span) - start) < 2

    w._show_cast_progress()
    assert w.cast_bar_seek.shown is True
    # An hour back on a six-hour bar sits five sixths of the way along.
    assert 820 < w.cast_bar_seek.value_ < 850, w.cast_bar_seek.value_
    assert "−" in w.cast_bar_time.text and ":" in w.cast_bar_time.text

    # Paused says so, in the one place that is looking at the time anyway.
    w._cast_paused_at = datetime.now()
    w._show_cast_progress()
    assert w.cast_bar_time.text.startswith("⏸")
    assert w.cast_bar_live.visible is True, "the red way back to live"

    # At the live edge it says LIVE rather than a hair's breadth behind it.
    w._cast_paused_at = None
    w._cast_ctx["archive_from"] = None
    w._show_cast_progress()
    assert w.cast_bar_time.text == "● LIVE"
    assert w.cast_bar_live.visible is False

    # And a film keeps its own bar: a length to measure against.
    w._cast_ctx = {}
    w.cast = _CastAt(1830.0, 6000.0)
    w._show_cast_progress()
    assert w.cast_bar_time.text == "30:30 / 1:40:00"
    # With nothing of last night's television left drawn along it. The
    # programme blocks were only ever cleared when the cast ENDED, so a film
    # cast straight after a timeshifted channel inherited its evening - on a
    # bar that is now measuring the film.
    assert w.cast_bar_seek.segments == []


def test_a_new_cast_does_not_inherit_the_last_ones_pause(monkeypatch):
    """How far behind live the last thing was is not about this one.

    It used to be wiped on every call, which is wrong in the other
    direction: changing the audio track shows the strip again, and that
    would have declared a channel held ten minutes back to be live while the
    picture stayed where it was. Only starting something new resets it, and
    what says "new" is the context object, which only a new cast builds."""
    w = _with_strip()
    w.cast = _CastAt(0.0, 0.0)
    w.settings = _Settings()
    w._record_cast_history = lambda: None
    w._show_cast_volume = lambda: None
    w._effective_ts_minutes = lambda it: 360
    w.xmltv = _Xmltv([])
    w._cast_behind = 600.0

    # A track change on the same cast: the same context, so the counter is
    # left exactly where the pause put it.
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {}, "_counted": True}
    w.show_cast_strip("Alva TV", "SVT1")
    assert w._cast_behind == 600.0

    # Something else entirely: a new context, and the counter goes with it.
    w._cast_ctx = {"title": "Film", "kind": "movie"}
    w.show_cast_strip("Alva TV", "Film")
    assert w._cast_behind == 0.0
    assert w._cast_paused_at is None


def test_hovering_the_timeline_names_the_time_and_what_was_on():
    import time as _t
    w = _with_strip()
    w.cast = _CastAt(0.0, 0.0)
    w._cast_device = "Alva TV"
    w._effective_ts_minutes = lambda it: 360
    now = _t.time()
    w.xmltv = _Xmltv([{"start_timestamp": now - 7200,
                       "stop_timestamp": now - 3600,
                       "title": "Rapport"}])
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {}}
    # Two hours back is two thirds of the way along a six-hour bar.
    label = w._cast_time_at(1 - 2 / 6)
    assert "Rapport" in label and ":" in label


def test_clicking_the_timeline_moves_the_broadcast_not_a_film():
    import time as _t
    w = _with_strip()
    w.cast = _CastAt(0.0, 0.0)
    w._cast_device = "Alva TV"
    w.xmltv = _Xmltv()
    w._effective_ts_minutes = lambda it: 360
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {}}
    moved = []
    w._cast_to_moment = lambda when: moved.append(when)
    w.cast_bar_seek.setValue(500)                    # halfway: three hours back
    w._cast_seek_released()
    assert moved, "the click moves the broadcast"
    assert abs(moved[0].timestamp() - (_t.time() - 3 * 3600)) < 30


def test_an_unimportable_pychromecast_is_not_a_missing_one():
    """Being told to install a package that is already installed is a wild
    goose chase; the reason it would not import is the only thing that ends
    it."""
    import dopeiptv.providers.chromecast as cc

    saved = (cc._pychromecast, cc._pc_checked, cc._pc_error)
    try:
        cc._pychromecast, cc._pc_checked, cc._pc_error = None, True, ""
        assert cc.cast_import_error() == ""      # simply not installed
        cc._pc_error = "ImportError: libsomething.so"
        assert "libsomething" in cc.cast_import_error()
    finally:
        cc._pychromecast, cc._pc_checked, cc._pc_error = saved


def test_buffering_is_not_an_ending(monkeypatch):
    """The restarts were ours. A stretch that was playing, eleven seconds in
    and pausing to fill its buffer, got killed and asked for again - and the
    new one paid the whole start-up cost afresh, seek discard and all, only
    to buffer again a little later.

    A slow stretch is slow whoever asks for it. Only an ending is an ending.
    """
    from datetime import datetime, timedelta
    w = _with_strip()
    w.cast = _Cast(active=True)
    w._cast_device = "Alva TV"
    began = datetime.now() - timedelta(minutes=10)
    w._cast_ctx = {"archive": True, "sid": 9851, "archive_from": began,
                   "item": {}}
    w._cast_paused_at = None
    asked = []
    w._cast_from_archive = lambda at, settle=False: asked.append(at)

    clock = {"now": 1000.0}
    import dopeiptv.ui.mw_cast as mwmod
    monkeypatch.setattr(mwmod.time, "monotonic", lambda: clock["now"])

    # Buffering, for as long as it likes, at a position that does not move.
    w.cast.state = "BUFFERING/None"
    w.cast.at = 300.0
    for _ in range(10):
        w._cast_continue_archive()
        clock["now"] += 30
    assert asked == [], "a slow stretch is not rescued by asking again"

    # An ending is an ending, and the next stretch starts where it stopped.
    w.cast.state = "IDLE/FINISHED"
    w._cast_continue_archive()
    assert asked == [began + timedelta(seconds=300)]

def test_the_tv_is_told_what_kind_of_thing_it_is_playing(monkeypatch):
    """pychromecast announces everything as LIVE unless told otherwise, so a
    film got the live UI on the television: a LIVE badge, a counting bar and
    a title that never faded. A thing with an end is BUFFERED, and its length
    goes along - the converted stream is an endless pipe the receiver cannot
    measure by itself."""
    m = _manager(monkeypatch)
    m.scan()
    dev = m.devices[0]

    m.cast("Alva TV", "http://p/film.mp4", "Film", duration=5400.0)
    # BUFFERED with its length and its title: the receiver's ordinary VOD
    # chrome, which fades on stable playback. LIVE was tried here and is
    # never right for a film - LIVE IS the counter and the badge, drawn for
    # as long as the stream is live, which for a live stream is always.
    assert dev.announced[-1] == ("BUFFERED", {"duration": 5400.0}, "Film")
    assert m.duration == 5400.0
    # And it starts at its beginning, said outright, not at the newest
    # segment of a growing playlist.
    assert dev.started_at == 0.0

    # A broadcast the same way - and it alone keeps the live edge, which
    # for a broadcast is the truth.
    dev.announced.clear()
    m.cast("Alva TV", "http://p/live/u/pw/9851.m3u8", "SVT1")
    assert dev.announced[-1] == ("LIVE", None, None)
    assert dev.started_at is None


def test_a_recorded_broadcast_is_still_a_broadcast(monkeypatch):
    """A length handed in with a channel does not make it a title.

    This is how the overlay came back after it had been taken away. Nothing
    in the sender says outright what a stream is, so it was worked out from
    the length - and a length turns up for a channel from two directions:
    mpv answers with the seekable window of a live playlist, and ffprobe
    measures a catch-up .ts down to the second. Either one announced the
    channel as BUFFERED with a title, and the television put a name and a
    progress bar over the picture and left them there for the whole match.

    Being recorded here is what a dvr cast IS, so it settles the question on
    its own, whatever number came along.
    """
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects", lambda u: (u, "video/mp2t"))
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    monkeypatch.setattr(m.bridge, "start", lambda *a, **k: "http://me/s.mp4")
    m.scan()
    m.cast("Alva TV", "http://p/timeshift/u/pw/240/1.ts", "SVT1",
           duration=3600.0, dvr=True)
    assert m.devices[0].announced[-1] == ("LIVE", None, None)
    assert m.duration == 0.0, "a broadcast has no length for the strip either"


def test_pausing_a_converted_broadcast_asks_the_provider_for_nothing():
    """The one that finally works, and the reason it does: the converter
    records into a spool as it goes, so a pause is the television stopping
    reading and play carries on at the very next frame. Every earlier way
    asked the provider for the missing minutes afterwards, and the provider
    is exactly what could not be relied on."""
    w = _with_strip()
    w.cast = _Cast(active=True)          # bridged() is True
    w.settings = _Settings()
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {}}
    w._cast_paused_at = None
    w._cast_from_archive = lambda at, settle=False: pytest.fail(
        "the provider must not be asked for anything")

    w._toggle_cast_pause()
    assert w.cast.held.wait(5), "held, not let go of - the recording goes on"
    assert not w.cast.released.is_set()

    w._toggle_cast_pause()
    assert w.cast.resumed.wait(5), "and simply carries on"


def test_a_pause_leaves_the_picture_behind_live_and_the_strip_says_so():
    """The recording carries on from where it was held, so every pause puts
    the picture that much further behind the broadcast - and it never catches
    up. The strip said LIVE while showing something ten minutes old."""
    from datetime import datetime, timedelta
    import time as _t
    w = _with_strip()
    w.cast = _Cast(active=True)
    w._cast_device = "Alva TV"
    w.xmltv = _Xmltv()
    w._effective_ts_minutes = lambda it: 360
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {}}
    w._cast_paused_at, w._cast_behind = None, 0.0

    # At the live edge, LIVE is the truth - and there is nowhere to go back
    # to, so the red button stays out of the way.
    w._show_cast_progress()
    assert w.cast_bar_time.text == "● LIVE"
    assert w.cast_bar_live.visible is False

    # Paused ten minutes ago: the gap is growing while it is held.
    w._cast_paused_at = datetime.now() - timedelta(minutes=10)
    assert abs((_t.time() - 600) - w._cast_moment().timestamp()) < 5

    # Play again, and the gap is kept - the recording resumes where it was.
    w._toggle_cast_pause()
    assert 595 < w._cast_behind < 605, w._cast_behind
    w._show_cast_progress()
    assert "−" in w.cast_bar_time.text
    assert w.cast_bar_live.visible is True, "the red way back appears"

    # A fresh cast starts at the live edge again.
    w.show_cast_strip("Alva TV", "SVT1")
    assert w._cast_behind == 0.0


def test_how_much_room_a_pause_may_take_is_the_user_s_to_say():
    """It is the one number that decides how long a pause can be, so it
    belongs where the user can see it - and a nonsense answer falls back to
    the default rather than to no pause at all."""
    w = _with_strip()
    w.cast = _Cast(active=True)
    w.settings = _Settings()
    w._cast_ctx = {"archive": True, "sid": 9851, "item": {}}
    w._cast_paused_at = None

    w.settings.setValue("cast_pause_gb", "12")
    w._toggle_cast_pause()
    assert w.cast.bridge.cap == 12 * 10**9

    w._toggle_cast_pause()               # play again
    w.settings.setValue("cast_pause_gb", "nonsense")
    w._toggle_cast_pause()
    assert w.cast.bridge.cap == int(4.5 * 10**9)


def test_going_live_keeps_the_channel_recorded_so_it_can_be_paused_again():
    """Going back to the live edge used to hand the channel straight to the
    receiver, and the pause button quietly vanished with the recording behind
    it - so the second pause of an evening was simply not offered."""
    sent = {}
    w = _with_strip()
    w.pool = None
    w.player = None
    w.settings = _Settings()
    w._cast_device = "Alva TV"
    w.cast = _Cast(active=True)
    w._local_codecs = lambda: []
    w._cast_behind = 900.0
    w._cast_ctx = {"archive": True, "sid": 9851, "title": "SVT1",
                   "row_url": "http://p/live/u/pw/9851.m3u8",
                   "row_source": "http://p/live/u/pw/9851.ts",
                   "url": "http://p/timeshift/x.ts", "item": {}}

    class Recorder:
        def cast(self, *a, **kw):
            sent.update(kw)
            return "Alva TV"

    w.cast = Recorder()
    w.cast.bridged = lambda: True
    import dopeiptv.ui.mw_cast as mwmod
    real, mwmod.run_async = mwmod.run_async, lambda p, work, ok, err: work()
    try:
        w._cast_go_live()
    finally:
        mwmod.run_async = real
    assert sent.get("dvr") is True, "still recorded, so still pausable"
    assert w._cast_behind == 0.0, "and back at the live edge"


def test_the_subtitle_is_turned_on_when_the_receiver_has_found_it():
    """Asking straight after handing the stream over found nothing.

    At that moment the receiver has not fetched the manifest and knows of
    no tracks at all - so the subtitle was chosen in the dialog, sent in the
    playlist, and arrived switched off, with the log saying nothing because
    from the sender's side everything had gone perfectly.

    So it is done when the receiver says it has tracks, which is exactly
    what a media status is for.
    """
    from dopeiptv.providers.chromecast import _CastWatch

    class Bridge:
        hls, subs = True, 0

    class Manager:
        bridge = Bridge()
        last_position = 0.0
        state = ""

        def stale(self, status):
            return False

    class MC:
        def __init__(self):
            self.enabled = []

        def enable_subtitle(self, track_id, timeout=10.0):
            self.enabled.append(track_id)

    class Status:
        def __init__(self, tracks=None, active=None):
            self.player_state = "PLAYING"
            self.idle_reason = None
            self.current_time = 3.0
            self.subtitle_tracks = tracks or []
            self.current_subtitle_tracks = active or []

    mc = MC()
    w = _CastWatch("Alva TV", Manager(), mc)

    # Nothing known yet: no guess, and no giving up either.
    w.new_media_status(Status())
    assert mc.enabled == []

    # The manifest has been read. Only the text track, and only once.
    tracks = [{"trackId": 1, "type": "AUDIO"},
              {"trackId": 2, "type": "TEXT", "language": "swe"}]
    w.new_media_status(Status(tracks))
    for _ in range(50):
        if mc.enabled:
            break
        threading.Event().wait(0.02)
    assert mc.enabled == [2]
    w.new_media_status(Status(tracks))
    threading.Event().wait(0.1)
    assert mc.enabled == [2], "asked once, not once a second"

    # A receiver that already has it on is left alone.
    mc2 = MC()
    w2 = _CastWatch("Alva TV", Manager(), mc2)
    w2.new_media_status(Status(tracks, active=[2]))
    assert mc2.enabled == []

    # And a cast with no subtitle never asks at all.
    class Plain:
        hls, subs = False, None

    class M2:
        bridge = Plain()
        last_position = 0.0
        state = ""

        def stale(self, status):
            return False

    mc3 = MC()
    w3 = _CastWatch("Alva TV", M2(), mc3)
    w3.new_media_status(Status(tracks))
    assert mc3.enabled == []


def test_the_player_pane_goes_when_a_cast_starts():
    """Casting stops local playback, so what was left behind was a black
    rectangle with a toolbar under it that controlled nothing."""
    w = _with_strip()
    w.cast = _CastAt(0.0, 0.0)
    w.settings = _Settings()
    w._record_cast_history = lambda: None
    w._show_cast_volume = lambda: None
    w._effective_ts_minutes = lambda it: 360
    w.xmltv = _Xmltv([])

    class Player:
        def __init__(self):
            self.shown = True

        def isVisible(self):
            return self.shown

        def hide(self):
            self.shown = False

    w.player = Player()
    w._cast_ctx = {"title": "Film", "kind": "movie"}
    w.show_cast_strip("Alva TV", "Film")
    assert w.player.shown is False

    # Ending the cast does not bring it back: nothing is playing locally
    # then either, and every path that starts playback shows it itself.
    w.show_cast_strip(None)
    assert w.player.shown is False

    # And a window with no embedded player at all is not a special case.
    w.player = None
    w._cast_ctx = {"title": "Film", "kind": "movie"}
    w.show_cast_strip("Alva TV", "Film")


def test_the_tracks_menu_can_actually_be_opened():
    """It could not: it imported a function that had been deleted, and the
    strip's tracks button crashed the app every time it was pressed. Every
    other test drove the cast logic directly, so nothing ever built this
    menu - the same hole the cast dialog fell through once already."""
    w = _with_strip()
    w.cast = _CastAt(0.0, 0.0)
    w._cast_device = "Alva TV"
    w.settings = _Settings()
    w._cast_ctx = {"tracks": {"audio": [{"index": 0, "lang": "swe",
                                         "codec": "aac"},
                                        {"index": 1, "lang": "eng",
                                         "codec": "ac3"}],
                              "subtitle": [{"index": 0, "lang": "swe",
                                            "codec": "subrip"},
                                           {"index": 1, "lang": "eng",
                                            "codec": "dvb_subtitle"}]},
                   "audio": None, "subs": None}
    w.cast_bar_tracks = _Lbl()
    w.cast_bar_tracks.mapToGlobal = lambda p: p
    w.cast_bar_tracks.rect = lambda: type("R", (), {
        "bottomLeft": lambda self: None})()

    class FakeMenu:
        opened = []

        def __init__(self, *a):
            self.items = []

        def addAction(self, label, slot=None):
            act = type("A", (), {"setCheckable": lambda s, b: None,
                                 "setChecked": lambda s, b: None,
                                 "triggered": type("T", (), {
                                     "connect": lambda s, f: None})()})()
            self.items.append(label)
            return act

        def addMenu(self, label):
            self.items.append(label)
            return FakeMenu()

        def addSeparator(self):
            pass

        def exec(self, *a):
            FakeMenu.opened.append(list(self.items))

    import dopeiptv.ui.mw_cast as mwmod
    real = mwmod.QMenu
    mwmod.QMenu = FakeMenu
    try:
        w._cast_tracks_menu()
    finally:
        mwmod.QMenu = real
    assert FakeMenu.opened, "the menu was never opened"
    # Every subtitle in the stream is offered - text and picture-based
    # alike. The libass question that used to filter this list is gone.
    assert w._last_menu_subs == 2


def test_only_a_text_track_counts_as_the_subtitle_being_on():
    """activeTrackIds lists EVERY active track, audio included.

    So a cast whose audio happened to be track 1 reported "the subtitle is
    already on ([1])" and left it off - and the log agreed with itself while
    the television showed nothing.
    """
    from dopeiptv.providers.chromecast import _CastWatch

    class Bridge:
        hls, subs = True, 0

    class Manager:
        bridge = Bridge()
        last_position = 0.0
        state = ""

        def stale(self, status):
            return False

    class MC:
        def __init__(self):
            self.enabled = []

        def enable_subtitle(self, track_id, timeout=10.0):
            self.enabled.append(track_id)

    class Status:
        player_state, idle_reason, current_time = "PLAYING", None, 1.0

        def __init__(self, active):
            self.subtitle_tracks = [{"trackId": 1, "type": "AUDIO"},
                                    {"trackId": 2, "type": "TEXT"}]
            self.current_subtitle_tracks = active

    mc = MC()
    _CastWatch("Alva TV", Manager(), mc).new_media_status(Status([1]))
    for _ in range(50):
        if mc.enabled:
            break
        threading.Event().wait(0.02)
    assert mc.enabled == [2], "the audio track being on is not the subtitle"

    mc2 = MC()
    _CastWatch("Alva TV", Manager(), mc2).new_media_status(Status([1, 2]))
    threading.Event().wait(0.1)
    assert mc2.enabled == [], "a text track that is on is left alone"


def test_a_playlist_is_seeked_rather_than_rebuilt():
    """Every segment is still there and named, so the receiver jumps within
    it by itself. Rebuilding the stream for that started the film over,
    which is what dragging the bar appeared to do."""
    w = _with_strip()
    w._cast_device = "Alva TV"
    again = []
    w._recast_with = lambda a, s, start=None: again.append(start)

    class Bridge:
        hls = True

    w.cast = _CastSeek(3600.0, 6000.0, bridged=True)
    w.cast.bridge = Bridge()
    w._cast_seek(1800.0)                # back, into what has been made
    for _ in range(50):
        if w.cast.sought is not None:
            break
        threading.Event().wait(0.02)
    assert w.cast.sought == 1800.0, "the receiver does its own seeking"
    assert again == [], "and the stream is not built again"

    # A single long response cannot be seeked at all, so that one still is.
    w.cast = _CastSeek(3600.0, 6000.0, bridged=True)
    w.cast.bridge = type("B", (), {"hls": False})()
    w._cast_seek(1800.0)
    assert again == [1800.0]


def test_the_clock_keeps_counting_between_the_receiver_s_reports(monkeypatch):
    """A receiver sends a media status when something happens to it, not
    once a second. A film that simply plays sends nothing at all, so the
    counter stood completely still; a channel that reports now and then made
    it jump five seconds and stop again.

    So the seconds in between are counted here, and only while the picture
    is actually moving.
    """
    from dopeiptv.providers import chromecast as cm
    clock = {"now": 1000.0}
    monkeypatch.setattr(cm.time, "monotonic", lambda: clock["now"])

    m = cm.ChromecastManager()
    m.state = "PLAYING/None"
    m.last_position, m.position_at = 300.0, clock["now"]
    assert m.position() == 300.0
    clock["now"] += 5                       # five seconds, no report at all
    assert m.position() == 305.0, "the clock has to carry itself"

    # A report lands: it wins, and the counting starts again from there.
    m.last_position, m.position_at = 306.0, clock["now"]
    assert m.position() == 306.0
    clock["now"] += 2
    assert m.position() == 308.0

    # A held picture is not getting any further in.
    m.state = "PAUSED/None"
    clock["now"] += 60
    assert m.position() == 306.0
    m.state = "IDLE/FINISHED"
    assert m.position() == 306.0

    # A converted stream starts at zero whatever it was seeked to, so the
    # offset still comes first.
    m.state, m.position_offset = "PLAYING/None", 1830.0
    m.position_at = clock["now"]
    clock["now"] += 3
    assert m.position() == 1830.0 + 306.0 + 3.0

    # Nothing reported yet: no invented seconds.
    m2 = cm.ChromecastManager()
    m2.state = "PLAYING/None"
    assert m2.position() == 0.0


def test_turning_the_subtitle_on_does_not_block_the_receive_thread():
    """It ran on pychromecast's own receive thread and then waited there for
    an answer that could only arrive on that same thread. It locked itself
    out and gave up ten seconds later, every time:

        could not turn the subtitle on (Execution of enable subtitle timed
        out after 10.0 s.)
    """
    from dopeiptv.providers.chromecast import _CastWatch

    class Bridge:
        hls, subs = True, 0

    class Manager:
        bridge = Bridge()
        last_position = 0.0
        state = ""

        def stale(self, status):
            return False

    done = threading.Event()

    class Blocking:
        def enable_subtitle(self, track_id, timeout=10.0):
            done.wait(2)                # as slow as the real one, blocking

    class Status:
        player_state, idle_reason, current_time = "PLAYING", None, 1.0
        subtitle_tracks = [{"trackId": 2, "type": "TEXT"}]
        current_subtitle_tracks: list = []

    import time as _t
    began = _t.monotonic()
    _CastWatch("Alva TV", Manager(), Blocking()).new_media_status(Status())
    took = _t.monotonic() - began
    done.set()
    assert took < 0.5, (
        f"the status callback was held for {took:.1f} s - on the real "
        "receive thread that is the whole cast held with it")


def test_a_jump_forward_is_rebuilt_and_a_jump_back_is_not():
    """A playlist can be seeked, but only into what has been made.

    The converter runs a little ahead of the picture and no further, so
    asking for thirteen minutes in landed at the end of what existed - on
    the television, a jump of about a minute and then nothing. Going back is
    free: those segments are all still there.
    """
    w = _with_strip()
    w._cast_device = "Alva TV"
    again = []
    w._recast_with = lambda a, s, start=None: again.append(start)

    class Bridge:
        hls = True

    # Backwards, into what has already been made: the receiver does it.
    w.cast = _CastSeek(600.0, 6000.0, bridged=True)
    w.cast.bridge = Bridge()
    w._cast_seek(120.0)
    for _ in range(50):
        if w.cast.sought is not None:
            break
        threading.Event().wait(0.02)
    assert w.cast.sought == 120.0 and again == []

    # Forwards, past it: there is nothing there to seek into, so the stream
    # is made again from that point.
    w.cast = _CastSeek(600.0, 6000.0, bridged=True)
    w.cast.bridge = Bridge()
    w._cast_seek(1800.0)
    assert again == [1800.0]
    assert w.cast.sought is None

    # A nudge forward is still a seek - the converter is that far ahead.
    w.cast = _CastSeek(600.0, 6000.0, bridged=True)
    w.cast.bridge = Bridge()
    w._cast_seek(603.0)
    for _ in range(50):
        if w.cast.sought is not None:
            break
        threading.Event().wait(0.02)
    assert w.cast.sought == 603.0


def test_a_farewell_from_the_old_stream_is_not_an_answer_about_the_new_one():
    """Changing subtitle mid-film loads a new stream, and the first report
    to arrive is the old one's farewell - IDLE/INTERRUPTED, still carrying
    the old track list. Acting on it ticked the job off as done, and the
    subtitle that had just been chosen was never switched on:

        serving ... subtitle track 11 as webvtt beside it
        receiver IDLE/INTERRUPTED
        the subtitle is already on ([2])
    """
    from dopeiptv.providers.chromecast import _CastWatch

    class Bridge:
        hls, subs = True, 11

    class Manager:
        bridge = Bridge()
        last_position = 0.0
        state = ""
        old_session = None

        def stale(self, status):
            sid = getattr(status, "media_session_id", None)
            return sid is not None and sid == self.old_session

    class MC:
        def __init__(self):
            self.enabled = []

        def enable_subtitle(self, track_id, timeout=10.0):
            self.enabled.append(track_id)

    class Status:
        def __init__(self, state, tracks, active):
            self.player_state, self.idle_reason = state, None
            self.current_time = 0.0
            self.subtitle_tracks = tracks
            self.current_subtitle_tracks = active

    old = [{"trackId": 2, "type": "TEXT"}]
    new = [{"trackId": 1, "type": "AUDIO"}, {"trackId": 3, "type": "TEXT"}]
    mc = MC()
    w = _CastWatch("Alva TV", Manager(), mc)

    # The old stream's last word, about the old stream's tracks.
    w.new_media_status(Status("IDLE", old, [2]))
    threading.Event().wait(0.1)
    assert mc.enabled == [] and w._subs_done is False

    # The new one, when it actually starts.
    w.new_media_status(Status("PLAYING", new, []))
    for _ in range(50):
        if mc.enabled:
            break
        threading.Event().wait(0.02)
    assert mc.enabled == [3]


def test_the_subtitle_is_asked_for_when_nobody_reports_it():
    """Waiting to be told was not enough.

    pychromecast keeps the previous track list when a status arrives with
    no media in it, so a cast that began without subtitles left an empty
    list standing - and the listener never saw a text track at all. Nothing
    was switched on, and nothing in the log said why.
    """
    from dopeiptv.providers import chromecast as cm

    class Bridge:
        hls, subs = True, 0

    class Status:
        def __init__(self):
            self.subtitle_tracks: list = []
            self.current_subtitle_tracks: list = []

    class MC:
        def __init__(self):
            self.status = Status()
            self.asked = 0
            self.enabled = []

        def update_status(self):
            self.asked += 1
            if self.asked >= 2:          # it turns up on the second ask
                self.status.subtitle_tracks = [
                    {"trackId": 1, "type": "AUDIO"},
                    {"trackId": 4, "type": "TEXT"}]

        def enable_subtitle(self, track_id, timeout=10.0):
            self.enabled.append(track_id)

    m = cm.ChromecastManager()
    m.bridge = Bridge()
    m.active = type("D", (), {"name": "Alva TV"})()
    mc = MC()
    m._ask_for_the_subtitle(mc)
    for _ in range(120):
        if mc.enabled:
            break
        threading.Event().wait(0.05)
    assert mc.enabled == [4], f"asked {mc.asked} times, enabled {mc.enabled}"

    # A cast with no subtitle asks nothing at all.
    class Plain:
        hls, subs = False, None

    m2 = cm.ChromecastManager()
    m2.bridge = Plain()
    mc2 = MC()
    m2._ask_for_the_subtitle(mc2)
    threading.Event().wait(0.2)
    assert mc2.asked == 0


def test_a_new_stream_is_not_judged_by_the_old_one_s_track_list():
    """ffmpeg builds every stream the same way, so the text track has the
    same number in both - and the receiver goes on reporting the old one for
    a moment after the new one is loaded. Both paths read it, both concluded
    the subtitle was already on, and the one that had just been chosen never
    was:

        serving ... subtitle track 12 as webvtt beside it
        receiver IDLE/INTERRUPTED
        the subtitle is on ([2])
        the subtitle is already on ([2])

    The track number cannot tell them apart. The media session can.
    """
    from dopeiptv.providers import chromecast as cm

    m = cm.ChromecastManager()
    m.old_session = 7

    class S:
        media_session_id = 7

    class New:
        media_session_id = 8

    class Nameless:
        pass

    assert m.stale(S()) is True, "the stream we just replaced"
    assert m.stale(New()) is False, "the one we just loaded"
    # A receiver that names no session is taken at its word - saying
    # nothing is not the same as saying the old number.
    assert m.stale(Nameless()) is False
    # And a fresh manager has no old session to be confused by.
    assert cm.ChromecastManager().stale(S()) is False


def test_every_path_announces_what_the_receiver_can_actually_do(monkeypatch):
    """The full walk of every road to play_media, pinned.

    The announcement is also the order for an on-screen UI, and the
    first-generation receiver never takes its UI down once drawn - measured
    on the television, five combinations, one round each. So each delivery
    announces only what the receiver can really do with it:

      native film (seekable file)      BUFFERED + title + length
      converted film (fMP4 pipe)       LIVE, nothing - the receiver can
                                       neither measure nor seek a pipe, and
                                       the scrubber it drew could not scrub
      converted film + text subtitle   BUFFERED (the playlist really is
                                       seekable; its segments are all kept)
      channel, converted or native     LIVE, nothing
    """
    from dopeiptv.providers import chromecast as cm

    # Native film: a real file the receiver seeks itself.
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: ("http://cdn/f.mp4", "video/mp4"))
    m.scan()
    m.cast("Alva TV", "http://p/movie/u/pw/5.mp4", "Film", duration=6000.0)
    assert m.devices[0].announced[-1] == \
        ("BUFFERED", {"duration": 6000.0}, "Film")

    # Converted film, no subtitle: announced exactly as it was in the week
    # everything worked and the chrome faded on its own. One round announced
    # this pipe as LIVE/bare mid-crisis; the announcement was never the
    # problem (the playback under it was), so it went back.
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: (u, "video/x-matroska"))
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    m.scan()
    monkeypatch.setattr(m.bridge, "start", lambda *a, **k: "http://me/s.mp4")
    m.bridge.hls = False
    m.cast("Alva TV", "http://p/movie/u/pw/5.mkv", "Film", duration=6000.0)
    # Like a channel. The receiver reported the bar still drawn with
    # metadata={} - so it comes from BUFFERED plus a duration, not from the
    # metadata - and a pipe cannot be seeked anyway, so that bar was a
    # control for something that was not there.
    assert m.devices[0].announced[-1] == ("LIVE", None, None)
    assert m.duration == 6000.0, "the strip still knows how long it is"

    # Converted film WITH a text subtitle: an HLS playlist whose segments
    # are all kept - genuinely seekable, so BUFFERED is the truth.
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm, "_resolve_redirects",
                        lambda u: (u, "video/x-matroska"))
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    m.scan()
    monkeypatch.setattr(m.bridge, "start",
                        lambda *a, **k: "http://me/master.m3u8")
    m.bridge.hls = True
    m.bridge.subs = 0
    m.cast("Alva TV", "http://p/movie/u/pw/5.mkv", "Film", duration=6000.0,
           subs={"index": 0, "codec": "subrip", "lang": "swe"})
    assert m.devices[0].announced[-1] == ("LIVE", None, None)
    assert m.devices[0].started_at == 0.0, "a playlist starts at the top"

    # A channel, native: nothing, as ever.
    m = _manager(monkeypatch)
    m.scan()
    m.cast("Alva TV", "http://p/live/u/pw/9851.m3u8", "SVT1")
    assert m.devices[0].announced[-1] == ("LIVE", None, None)


def test_a_kodi_stopped_by_its_own_remote_takes_the_strip_down():
    """Kodi stopped with its own remote, or playing something else now: the
    strip says what is true, the film's place is kept - and Kodi is not told
    to stop, which would end whatever it plays now."""
    w = _with_strip()
    w.resume = _Resume0()
    w._cast_ctx = {"group": "vod", "key": 42, "title": "Film"}
    w.show_cast_strip("Den (Kodi)", "Film")
    assert w.cast_bar.shown

    class _Gone(_Cast):
        duration = 6000.0

        def ended_elsewhere(self):
            return True

    w.cast = _Gone(active=False)
    w.cast.at = 2400.0
    w._show_cast_progress()
    assert not w.cast_bar.shown and w._cast_device is None
    assert w.resume.saved["vod:42"] == 2400
    assert not w.cast.stopped.is_set()




def test_the_buffer_s_loopback_address_never_reaches_a_chromecast(monkeypatch):
    """The rewind buffer's server is on this computer: a Chromecast is
    handed the converter's address for it, never the loopback one - and
    the pieces of a feed go to the converter as they are."""
    import types
    from dopeiptv.providers import chromecast as cm
    m = _manager(monkeypatch)
    monkeypatch.setattr(cm.CastBridge, "available", staticmethod(lambda: True))
    asked = []
    monkeypatch.setattr(m.bridge, "start", lambda *a, **k: (
        asked.append((a, k)), "http://192.168.1.5:5000/t/stream.mp4")[1])
    m.scan()
    m.cast("Alva TV", "http://127.0.0.1:4321/tok/s/7.ts", "SVT1")
    assert asked and asked[-1][0][0] == "http://127.0.0.1:4321/tok/s/7.ts"
    feed = types.SimpleNamespace(kind="rewind buffer")
    m.cast("Alva TV", "http://p/live/u/pw/1.m3u8", "SVT1", feed=feed)
    assert asked[-1][1]["feed"] is feed and asked[-1][1]["start_at"] == 0.0
    plays = [u for u, _c in m.devices[0].plays]
    assert plays == ["http://192.168.1.5:5000/t/stream.mp4"] * 2, plays
    assert m.duration == 0.0


class _FeedCast(_CastAt):
    """A cast reading a feed: the converter's playlist holds *written* s."""

    def __init__(self, at, written):
        super().__init__(at, 0.0)
        self.seeks = []
        self.bridge = types.SimpleNamespace(cap=0, hls=True,
                                            written=lambda: written)

    def bridged(self):
        return True

    def seek(self, to):
        self.seeks.append(to)


def test_moving_within_what_the_playlist_holds_is_a_seek_on_the_tv(monkeypatch):
    """The archive in pieces is one playlist on the TV: going back within
    what is written moves the TV there, no new stream - and the strip's
    moment follows the TV's position in that playlist."""
    import dopeiptv.ui.mw_cast as mwmod
    from datetime import datetime, timedelta
    w = _with_strip()
    w._cast_device = "Alva TV"
    w.cast = _FeedCast(600.0, 1200.0)
    began = datetime.now() - timedelta(minutes=60)
    feed = types.SimpleNamespace(first_wall=began.timestamp(), closed=False,
                                 kind="archive", source=None,
                                 running=lambda: True)
    w._cast_ctx = {"archive": True, "sid": 7, "item": {"tv_archive_duration": 3},
                   "archive_from": began, "feed": feed}
    w._effective_ts_minutes = lambda it: 180
    sent = []
    monkeypatch.setattr(mwmod, "run_async", lambda *a, **k: sent.append(a))
    assert abs((w._cast_moment() - (began + timedelta(seconds=600))
                ).total_seconds()) < 1
    w._cast_to_moment(began + timedelta(minutes=5))
    end = time.monotonic() + 5
    while not w.cast.seeks and time.monotonic() < end:
        time.sleep(0.02)
    assert w.cast.seeks == [pytest.approx(300.0, abs=1)] and not sent
    # Beyond what is written: a new stream from there.
    called = []
    w._cast_from_moment = lambda when: called.append(when)
    w._cast_to_moment(began + timedelta(minutes=40))
    assert called and len(w.cast.seeks) == 1


# -- going back in the rewind buffer during a live cast ---------------------------


class _Seg:
    def __init__(self, seq, t0):
        self.seq, self.t0 = seq, t0


class _BufferSession:
    """A DvrSession stand-in: a segment every two seconds over the last
    *held* seconds, its holds and its loopback server's addresses."""

    def __init__(self, held):
        now = time.time()
        self.t0, self.t1 = now - held, now
        self.holds, self.let_gone = set(), []
        self.closed, self.streaming = False, True

    def span(self):
        return (self.t0, self.t1) if self.t1 > self.t0 else None

    def segment_at(self, t):
        n = int((max(self.t0, min(t, self.t1)) - self.t0) // 2)
        return _Seg(n, self.t0 + 2 * n)

    def next_run_after(self, t, seq=None):
        return None

    def url_for(self, seg):
        return f"http://127.0.0.1:4321/tok/s/{seg.seq}.ts"

    def hold(self, who):
        self.holds.add(who)

    def let_go(self, who):
        self.holds.discard(who)
        self.let_gone.append(who)

    @property
    def held(self):
        return bool(self.holds)


class _Hub(_FeedCast):
    """The cast as the window sees it: a Chromecast fed through the
    converter here (*feeds*), or a DLNA TV that is not."""

    def __init__(self, feeds=True, written=0.0):
        super().__init__(0.0, written)
        self.feeds, self.sent = feeds, []

    def takes_feed(self, _name):
        return self.feeds

    def plays_as_is(self, _name):
        return not self.feeds

    def cast(self, device, url, title, *a, **kw):
        self.sent.append((url, kw))
        return device


class _Guide(_Xmltv):
    def current_programme(self, _it):
        return None


def _buffer_cast(monkeypatch, held=120.0, archive=False, feeds=True, kept=True):
    import dopeiptv.ui.mw_cast as mwmod
    w = _with_strip()
    w.pool, w.player = None, None
    w.settings = _Settings()
    w.xmltv = _Guide()
    w._local_codecs = lambda: []
    w._effective_ts_minutes = lambda it: 2880 if archive else 0
    w._timeshift_days = lambda it: 2
    w._open_catchup_dialog = lambda it, on_pick=None: None
    w._cast_device = "Alva TV"
    w.cast = _Hub(feeds=feeds)
    session = _BufferSession(held)
    w._cast_ctx = {"sid": 7, "archive": archive, "title": "SVT1",
                   "item": {"stream_id": 7}, "key": 7, "kind": "live",
                   "row_url": "http://p/live/u/pw/7.m3u8",
                   "row_source": "http://p/live/u/pw/7.ts",
                   "url": "http://p/live/u/pw/7.m3u8",
                   "source": "http://p/live/u/pw/7.ts"}
    if kept:
        session.hold("cast-live:1")
        w._cast_ctx.update(buffer=session, buffer_hold="cast-live:1")
    monkeypatch.setattr(mwmod, "run_async",
                        lambda pool, work, ok, err: work())
    return w, session


def _entry(w, text):
    return next(act for t, act in (e for e in w._cast_timeshift_entries() if e)
                if t == text)


def _back(n=None, s=None):
    from dopeiptv.i18n import tr
    return tr("ts_go_back", t=tr("health_secs", s=s) if s is not None
              else tr("opt_minutes", n=n))


def _wait_until(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.02)
    return cond()


def test_a_cast_channel_without_an_archive_goes_back_in_the_rewind_buffer(monkeypatch):
    """No archive, but the buffer of the channel the player was watching
    goes on recording beside the cast: the strip's menu and its timeline
    offer what it holds, and no further."""
    from dopeiptv.i18n import tr
    w, session = _buffer_cast(monkeypatch, held=120.0)
    assert w._cast_can_go_back()
    texts = [e[0] for e in w._cast_timeshift_entries() if e is not None]
    assert texts == [tr("ts_go_live"), _back(s=10), _back(s=30), _back(n=1)]
    # Nothing of the archive's: no programme list, no depth.
    assert tr("ts_browse_past") not in texts
    # A buffer of twenty minutes: its short steps, not the archive's 30 min.
    session.t0 = time.time() - 20 * 60
    assert [s for s, _ in w._cast_back_steps(w._cast_moment())] == [
        10, 30, 60, 300, 900]
    # The strip: the menu's button, and a timeline as long as the buffer.
    w._show_cast_progress()
    assert w.cast_bar_ts.visible and w.cast_bar_seek.shown is True
    start, width = w._cast_timeline()
    assert abs(start - session.t0) < 2 and abs(width - 20 * 60) < 2
    # No ffmpeg here: no converter to feed the TV the buffer's pieces.
    w.cast.bridge.available = lambda: False
    assert not w._cast_can_go_back() and w._cast_timeline() is None
    del w.cast.bridge.available
    # A DLNA TV plays an address as it is - the buffer's is this
    # computer's loopback one: nothing of the buffer is offered.
    w.cast = _Hub(feeds=False)
    assert w._cast_timeshift_entries() == [] and not w._cast_can_go_back()
    w._show_cast_progress()
    assert not w.cast_bar_ts.visible


def test_a_step_the_buffer_holds_feeds_the_tv_from_it(monkeypatch):
    from datetime import datetime
    w, session = _buffer_cast(monkeypatch, held=120.0)
    before = time.time()
    _entry(w, _back(s=30))()
    feed = w._cast_ctx["feed"]
    try:
        assert feed.kind == "rewind buffer" and feed.source is session
        # From the segment holding that moment (a segment every 2 s).
        assert before - 33 <= feed.first_wall <= before - 29
        assert w._cast_ctx["archive_from"] == datetime.fromtimestamp(
            feed.first_wall)
        # The converter reads the pieces; the TV gets its playlist.
        url, kw = w.cast.sent[-1]
        assert kw["feed"] is feed and kw["dvr"] is True
        # Held while the feed reads it, beside the cast's own hold.
        assert len(session.holds) == 2 and "cast-live:1" in session.holds
    finally:
        feed.close()
    assert session.holds == {"cast-live:1"}, "the feed's hold went with it"


def test_a_step_beyond_the_buffer_goes_to_the_archive(monkeypatch):
    """With an archive too: what the buffer holds comes out of it, what is
    earlier out of the archive - and the archive alone never offers the
    minutes it is still writing."""
    from dopeiptv.i18n import tr
    w, session = _buffer_cast(monkeypatch, held=120.0, archive=True)
    archived = []
    w._cast_from_archive = lambda at: archived.append(at)
    steps = [s for s, _ in w._cast_back_steps(w._cast_moment())]
    assert steps[:6] == [10, 30, 60, 300, 900, 1800] and 2880 * 60 in steps
    texts = [e[0] for e in w._cast_timeshift_entries() if e is not None]
    assert tr("ts_browse_past") in texts
    assert tr("ts_archive_depth", n=2) in texts
    _entry(w, _back(n=5))()
    assert len(archived) == 1 and not w._cast_ctx.get("feed")
    assert abs(archived[0].timestamp() - (time.time() - 300)) < 5
    _entry(w, _back(s=30))()
    feed = w._cast_ctx["feed"]
    assert feed.kind == "rewind buffer" and len(archived) == 1
    feed.close()
    # The archive alone, at live: from five minutes back, never the newest
    # minute - and a minute back from 30 s behind is 90 s, which it holds.
    w._cast_ctx.pop("buffer")
    w._cast_ctx.update(feed=None, archive_from=None)
    assert [s for s, _ in w._cast_back_steps(w._cast_moment())][:2] == [300, 900]
    w._cast_behind = 30.0
    assert [s for s, _ in w._cast_back_steps(w._cast_moment())][:2] == [60, 300]


def test_the_timeline_seeks_within_the_buffer_feed_and_back_to_live_ends_it(
        monkeypatch):
    from datetime import datetime, timedelta
    w, session = _buffer_cast(monkeypatch, held=300.0)
    w._cast_to_moment(datetime.now() - timedelta(seconds=120))
    feed = w._cast_ctx["feed"]
    sent = len(w.cast.sent)
    # Within what the converter has written: the TV moves there itself.
    w.cast.bridge.written = lambda: 60.0
    w._cast_to_moment(datetime.fromtimestamp(feed.first_wall + 20))
    assert _wait_until(lambda: w.cast.seeks)
    assert w.cast.seeks == [pytest.approx(20.0, abs=1)]
    assert len(w.cast.sent) == sent and w._cast_ctx["feed"] is feed
    # Dragged to the live end: that is live - the feed ends, and the TV is
    # given the channel's own address again.
    w._cast_to_moment(datetime.now() - timedelta(seconds=2))
    url, kw = w.cast.sent[-1]
    assert url == "http://p/live/u/pw/7.m3u8" and "feed" not in kw
    assert kw["source"] == "http://p/live/u/pw/7.ts"
    assert kw["settle"] is False, "the buffer kept beside it goes on"
    assert w._cast_ctx["archive_from"] is None and "feed" not in w._cast_ctx
    assert _wait_until(lambda: feed.closed)
    assert session.holds == {"cast-live:1"}
    assert w._cast_can_go_back(), "still recording, still offered"


def test_back_to_live_lets_a_buffer_with_no_room_beside_the_cast_go_first(
        monkeypatch):
    """No room for the buffer beside the cast (it was the cast's only
    connection to the provider): its feed ends before the TV asks for the
    channel, and the provider is given a moment."""
    from datetime import datetime, timedelta
    w, session = _buffer_cast(monkeypatch, held=300.0, kept=False)
    assert not w._cast_can_go_back()        # nothing reads it: not offered
    feed = w._buffer_feed(session, time.time() - 60)
    w._cast_send_feed(feed)
    assert w._cast_can_go_back() and session.held
    w._cast_to_moment(datetime.now() - timedelta(seconds=90))
    assert w._cast_ctx["feed"] is not feed
    assert w._cast_ctx["feed"].source is session
    feed.close()
    w._cast_go_live()
    assert w.cast.sent[-1][1]["settle"] is True
    assert _wait_until(lambda: not session.holds), "let go once the feed ends"


def test_the_rewind_buffer_is_kept_for_a_live_cast_only_when_asked_for(monkeypatch):
    """Kept recording beside a live cast is a second connection to the
    provider, and nearly every account allows one: by default the buffer
    is never held for it (it freezes as the player stops). With Settings >
    Casting's switch on it is held before the cast dialog stops the player
    - once the connections check says yes (asked over a full or unknown
    limit, a No kept to) - and let go when the cast ends, goes to a DLNA TV,
    or never starts."""
    import dopeiptv.ui.mw_cast as mwmod
    w = _with_strip()
    w.settings = _Settings()
    session = _BufferSession(60.0)
    w.player = types.SimpleNamespace(_dvr=session, _dvr_wall_now=time.time,
                                     current_url="http://p/live/u/pw/7.ts",
                                     isVisible=lambda: False)
    w._playing_key = 7
    w._item_key = lambda it: it.get("stream_id")
    w._stream_for = lambda it: ("http://p/live/u/pw/7.ts", "SVT1")
    w.client = types.SimpleNamespace(
        live_url=lambda sid, fmt: f"http://p/live/u/pw/{sid}.{fmt}")
    w._vpn_guard_blocks = lambda url: False
    w._cast_pin_ok = lambda *u, **_k: True
    w._conn_ok_cast = lambda url, **_k: True
    w._play_kind_for = lambda it: "live"
    w._local_codecs = lambda: []
    w._local_audio_index = lambda: 0
    w._local_tracks = lambda it: {}
    w._busy_elsewhere = lambda it: False
    w._record_cast_history = lambda: None
    w._show_cast_volume = lambda: None
    w._show_cast_progress = lambda: None
    w._cast_began = lambda ctx: None
    w._cast_device = None
    room, seen, device = [True], {}, [None]

    class Dialog:
        def __init__(self, win, url, title, *a, **k):
            seen.update(url=url, source=k.get("source"))

        def exec(self):
            seen["held"] = set(session.holds)
            if device[0]:
                w.show_cast_strip(device[0], "SVT1")

    monkeypatch.setattr(mwmod, "CastDialog", Dialog)
    checked = []
    w._conn_ok_cast_buffer = lambda url, **_k: (checked.append(url), room[0])[1]
    channel = {"stream_id": 7, "name": "SVT1", "_kind": "live"}

    # Off, as it is unless asked for: never held beside the cast, nothing
    # even asked - not for the dialog, not for a cast to a Chromecast.
    w.cast = _Hub(feeds=True)
    w.cast.active = None
    device[0] = "Alva TV"
    w._open_cast_dialog(channel)
    assert seen["held"] == set() and not session.holds and not checked
    assert w._cast_ctx["buffer"] is None and not session.let_gone
    assert not w._cast_can_go_back(), "a frozen buffer is not offered"
    w.show_cast_strip(None)
    w.cast = _Cast(active=False)
    device[0] = None

    w.settings.setValue("cast_keep_buffer", "true")
    # Closed without a cast: held for the dialog, let go after it.
    w._open_cast_dialog(channel)
    assert checked == ["http://p/live/u/pw/7.ts"]
    assert len(seen["held"]) == 1 and not session.holds
    assert len(seen["held"]) == 1 and not session.holds
    assert not seen["url"].startswith("http://127.")
    # Cast to a Chromecast: held for as long as the cast runs.
    w.cast = _Hub(feeds=True)
    w.cast.active = None                    # nothing on it yet
    device[0] = "Alva TV"
    w._open_cast_dialog(channel)
    assert len(session.holds) == 1 and w._cast_ctx["buffer"] is session
    w.show_cast_strip(None)
    assert not session.holds and "buffer" not in w._cast_ctx
    # A DLNA TV: nothing here reads the buffer for it.
    w.cast = _Hub(feeds=False)
    w.cast.active = None
    w._cast_device = None
    device[0] = "Den (DLNA)"
    w._open_cast_dialog(channel)
    assert len(seen["held"]) == 1 and not session.holds
    # Asked (a full or unknown limit) and told No: not kept at all.
    w.show_cast_strip(None)
    gone = len(session.let_gone)
    room[0] = False
    device[0] = None
    w._open_cast_dialog(channel)
    assert seen["held"] == set() and len(session.let_gone) == gone
    assert len(checked) == 4


def test_no_receiver_is_handed_the_rewind_buffer_s_loopback_address(monkeypatch):
    from dopeiptv.i18n import LocalizedError
    from dopeiptv.providers.casting import CastHub

    class Mgr:
        TAKES_FEED = False
        active = None

        def __init__(self, names):
            self.names, self.calls = names, []

        def knows(self, name):
            return name in self.names

        def cast(self, *a, **kw):
            self.calls.append((a, kw))
            return a[0]

    class Fed(Mgr):
        TAKES_FEED = True

    cc, dlna, kodi = Fed(set()), Mgr({"Den (DLNA)"}), Mgr({"Hall (Kodi)"})
    hub = CastHub(chromecast=cc, dlna=dlna, kodi=kodi)
    feed = types.SimpleNamespace(kind="rewind buffer")
    loop = "http://127.0.0.1:4321/tok/s/7.ts"
    for name, mgr in (("Den (DLNA)", dlna), ("Hall (Kodi)", kodi)):
        with pytest.raises(LocalizedError):
            hub.cast(name, loop, "SVT1", source=loop, feed=feed)
        with pytest.raises(LocalizedError):
            hub.cast(name, "http://p/live/u/pw/7.m3u8", "SVT1", [], None,
                     None, 0.0, 0.0, False, loop, feed=feed)
        assert mgr.calls == []
    # The channel's own address goes to a TV, without the pieces.
    hub.cast("Den (DLNA)", "http://p/live/u/pw/7.m3u8", "SVT1",
             source="http://p/live/u/pw/7.ts", feed=feed)
    assert "feed" not in dlna.calls[-1][1]
    # A Chromecast's converter here reads the pieces.
    hub.cast("Alva TV", loop, "SVT1", feed=feed)
    assert cc.calls[-1][1]["feed"] is feed
    # The window never feeds a TV, and its panel never offers it the
    # buffer's address for another device.
    w, session = _buffer_cast(monkeypatch, feeds=False)
    piece = w._buffer_feed(session, time.time() - 30)
    w._cast_send_feed(piece)
    assert w.cast.sent == [] and _wait_until(lambda: piece.closed)
    import dopeiptv.ui.mw_cast as mwmod
    shown = {}
    monkeypatch.setattr(mwmod, "CastDialog", lambda win, url, *a, **k: (
        shown.update(url=url, source=k.get("source")),
        types.SimpleNamespace(exec=lambda: None))[1])
    w._cast_ctx.update(url=loop, source=loop)
    w.manage_cast()
    assert shown == {"url": "http://p/live/u/pw/7.m3u8",
                     "source": "http://p/live/u/pw/7.ts"}
