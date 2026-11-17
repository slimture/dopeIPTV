"""The cast card (ui/cast_card.py) and what fills and watches it
(ui/mw_cast_watch.py): its parts for each state, a stuck TV sent the stream
again - twice, then a failure with Try again -, never two streams at once,
what plays marked "On TV" and found by the jump, and a layout that holds
from a narrow column to a wide pane."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


# -- stand-ins --------------------------------------------------------------------------

class _Bridge:
    """A converter: started per stream, never two at once."""

    def __init__(self):
        self.running, self.starts, self.cap = False, 0, 0
        self.hls = False

    def available(self):
        return True

    def stop(self):
        self.running = False

    def start(self):
        assert not self.running, "a second converter beside the first"
        self.running = True
        self.starts += 1


class _Hub:
    """The cast hub as the window sees it, a converter feeding the TV."""

    def __init__(self, state="PLAYING/None", duration=0.0, airplay=False, waiting=False):
        self.active = object()
        self.state, self.duration = state, duration
        self.bridge = _Bridge()
        self.bridge.start()
        self.casts: list[dict] = []
        self.airplay, self.waiting = airplay, waiting

    def cast(self, device, url, title, *args, **kw):
        settle = kw.get("settle", args[5] if len(args) > 5 else False)
        start = kw.get("start", args[3] if len(args) > 3 else 0.0)
        if self.bridge.running:
            assert settle, "sent again without the converter letting go first"
            self.bridge.stop()
        self.bridge.start()
        self.casts.append({"device": device, "url": url, "settle": settle, "start": start})
        return device

    def position(self):
        return 1830.0

    def bridged(self):
        return True

    def volume(self):
        return 0.4, False

    def is_airplay(self, _n):
        return self.airplay

    def plays_as_is(self, _n):
        return False

    def is_kodi(self, _n):
        return False

    def takes_feed(self, _n):
        return True

    def waiting_for_route(self):
        return self.waiting

    def ended_elsewhere(self):
        return False


class _Settings(dict):
    def value(self, key, default=None):
        return self.get(key, default)

    def setValue(self, key, value):
        self[key] = value


class _Guide:
    def current_programme(self, _item):
        start = datetime(2026, 11, 17, 19, 30).timestamp()
        return {"title": "Rapport", "start_timestamp": start, "stop_timestamp": start + 1800}


class _Loader:
    def __init__(self, cache=None):
        self.cache = dict(cache or {})

    def disk_file(self, _url):
        return None


def _window(kind="live", **hub):
    from dopeiptv.ui.cast_card import CastCard
    from dopeiptv.ui.mw_cast import _CastMixin
    from dopeiptv.ui.mw_cast_watch import _CastWatchMixin
    from dopeiptv.ui.mw_pane_follow import _PaneFollowMixin

    class Win(_CastMixin, _CastWatchMixin, _PaneFollowMixin):
        def __init__(self):
            card = self.cast_bar = CastCard()
            self.cast_bar_back, self.cast_bar_fwd = card.back, card.forward
            self.cast_bar_pause, self.cast_bar_lbl = card.pause, card.device
            self.cast_bar_mute, self.cast_bar_vol = card.mute, card.vol
            self.cast = _Hub(**hub)
            self._cast_device = "Living room TV"
            self.settings = _Settings()
            self.pool = None
            self.player = None
            self._playing_key = self._playing_group = self._playing_item = None
            self._cast_paused_at = None
            self._last_playback = {}
            self.xmltv = _Guide()
            self.logos, self.poster_art = _Loader(), _Loader()
            self.toasts, self.errors, self.asked, self.shown = [], [], [], []
            self.verdict = None
            if kind == "live":
                item = {"stream_id": 7, "name": "SVT1 HD", "stream_icon": "http://l/svt1.png"}
                self._cast_ctx = {"sid": 7, "archive": False, "title": "SVT1 HD", "key": 7,
                                  "item": item, "kind": "live", "row_url": "http://p/7.m3u8",
                                  "row_source": "http://p/7.ts", "url": "http://p/7.m3u8",
                                  "source": "http://p/7.ts", "source_id": "pl"}
            elif kind == "movie":
                item = {"stream_id": 42, "name": "Sintel (2010)", "stream_icon": "http://l/sintel.jpg"}
                self._cast_ctx = {"sid": None, "archive": False, "title": "Sintel", "key": 42,
                                  "item": item, "kind": "movie", "group": "vod",
                                  "url": "http://p/42.mkv", "source": "http://p/42.mkv",
                                  "row_url": "http://p/42.mkv", "duration": 888.0}
            else:
                item = {"id": 501, "season": 2, "episode_num": 5, "title": "Mandala"}
                self._cast_ctx = {"sid": None, "title": "S02E05", "key": 501, "item": item,
                                  "kind": "episode", "group": "episode",
                                  "series_ctx": {"series_id": 9, "name": "Breaking Bad",
                                                 "cover": "http://l/bb.jpg"},
                                  "url": "http://p/501.mkv", "source": "http://p/501.mkv"}

        def channel_display_name(self, it):
            return it.get("name") or ""

        def _item_key(self, it):
            return it.get("stream_id") or it.get("id")

        def show_cast_strip(self, device, title=""):
            self.shown.append((device, title))

        def _show_action_toast(self, text, _ms, action, label=None, decide=False, key=None):
            self.toasts.append((text, action, label, key))

        def _error(self, msg):
            self.errors.append(msg)

        def _conn_ok_cast(self, url, source_id=""):
            self.asked.append(url)
            return True

        def _conn_uses(self, free_player=False):
            return []

        def _conn_verdict(self, uses, source=""):
            return self.verdict

        def _cast_airplay_window(self):
            self.asked.append("airplay window")

        def _fmt_hms(self, s):
            return str(int(s))

        @property
        def channel_ov(self):
            return type("O", (), {"display_name": staticmethod(lambda _k, _i, n: n)})()

    return Win()


@pytest.fixture()
def sync_runs(monkeypatch):
    """The pool, run at once - or held, as a stream on its way."""
    import dopeiptv.ui.mw_cast as mwc
    held: list = []

    def run(_pool, work, done, failed, *a):
        if held and held[0] == "hold":
            held.append((work, done, failed))
            return
        try:
            got = work(*a)
        except Exception as e:
            failed(str(e))
            return
        done(got)
    monkeypatch.setattr(mwc, "run_async", run)
    return held


@pytest.fixture()
def clock(monkeypatch):
    import dopeiptv.ui.mw_cast_watch as mcw
    now = [1000.0]
    monkeypatch.setattr(mcw.time, "monotonic", lambda: now[0])
    return now


def _tick(w, clock, secs, state=None):
    for _ in range(int(secs)):
        if state is not None:
            w.cast.state = state
        clock[0] += 1.0
        w._cast_card_tick()


# -- the card's parts per state -------------------------------------------------------

def test_each_state_says_itself_and_offers_what_it_needs():
    from dopeiptv.i18n import tr
    from dopeiptv.ui import cast_card as cc
    card = cc.CastCard()
    for state in cc.STATES:
        card.set_state(state, 1, 2)
        assert card.pill.text == cc.state_text(state, 1, 2) and card.pill.text
        assert card.action.isHidden()
    card.set_state(cc.RECONNECTING, 2, 2)
    assert card.pill.text == tr("cast_state_reconnecting", n=2, of=2)
    card.set_state(cc.FAILED, note=tr("cast_note_failed"), action="retry")
    assert not card.action.isHidden() and card.action.text() == tr("cast_try_again")
    assert not card.note.isHidden() and card.note.text() == tr("cast_note_failed")
    got = []
    card.action_clicked.connect(got.append)
    card.action.click()
    assert got == ["retry"]
    card.set_state(cc.WAITING, note=tr("airplay_pick_window"), action="airplay")
    assert card.action.text() == tr("airplay_show_window")
    card.set_state(cc.PLAYING)
    assert card.action.isHidden() and card.note.isHidden()
    # The spinner turns only while it waits.
    assert cc.BUFFERING in cc.SPINNING and cc.PLAYING not in cc.SPINNING


def test_every_button_has_words_for_a_screen_reader_and_takes_the_keyboard():
    from PyQt6.QtCore import Qt
    from dopeiptv.ui import cast_card as cc
    card = cc.CastCard()
    for b in (card.back, card.pause, card.forward, card.stop, card.mute, card.tracks,
              card.ts, card.airplay, card.devices):
        assert b.accessibleName() and b.toolTip()
        assert b.focusPolicy() == Qt.FocusPolicy.StrongFocus


def test_the_card_shows_what_plays_on_the_tv(sync_runs):
    from dopeiptv.ui import cast_card as cc
    w = _window("live")
    w._cast_card_fill("SVT1 HD")
    card = w.cast_bar
    assert card.title.text() == "SVT1 HD"
    assert card.line.text().startswith("Rapport · 19:30")
    assert card.device.text() == "Living room TV" and card.device_mark.glyph == "cast"
    # No picture held: the initials on the accent, never a download.
    assert card.art.pixmap is None and card.art.initials and card.art.shape == "logo"
    from PyQt6.QtGui import QPixmap
    pm = QPixmap(64, 64)
    w.logos.cache["http://l/svt1.png"] = pm
    w._cast_card_fill("SVT1 HD")
    assert card.art.pixmap is pm
    film = _window("movie")
    film._cast_card_fill("Sintel")
    assert film.cast_bar.line.text().startswith("2010")
    assert film.cast_bar.art.shape == "poster"
    ep = _window("episode")
    ep._cast_card_fill("S02E05")
    assert ep.cast_bar.title.text() == "Breaking Bad"
    assert "Mandala" in ep.cast_bar.line.text()
    tv = _window("movie", airplay=True, waiting=True)
    tv._cast_card_fill("Sintel")
    assert tv.cast_bar.device_mark.glyph == "airplay"
    tv._cast_card_state()
    assert tv.cast_bar.pill.state == cc.WAITING
    assert tv.cast_bar.action_kind == "airplay"


def test_the_state_follows_the_receiver_and_the_pause(sync_runs, clock):
    from dopeiptv.ui import cast_card as cc
    w = _window("movie", duration=888.0)
    w._cast_card_state()
    assert w.cast_bar.pill.state == cc.PLAYING
    w._cast_paused_at = datetime.now()
    w._cast_card_state()
    assert w.cast_bar.pill.state == cc.PAUSED
    w._cast_paused_at = None
    _tick(w, clock, 1, "BUFFERING/None")
    assert w.cast_bar.pill.state == cc.BUFFERING
    # Played again: Buffering stays said a moment, then Playing.
    _tick(w, clock, 1, "PLAYING/None")
    assert w.cast_bar.pill.state == cc.BUFFERING
    _tick(w, clock, 2)
    assert w.cast_bar.pill.state == cc.PLAYING
    # A title's skips are there; a channel's only where it can go back.
    assert not w.cast_bar_back.isHidden() and not w.cast_bar_fwd.isHidden()
    live = _window("live")
    live._cast_skips_shown()
    assert live.cast_bar_back.isHidden() and live.cast_bar_fwd.isHidden()


def test_a_send_on_its_way_beside_a_tv_that_plays_says_playing(sync_runs, clock):
    """A seek's send can still be on its way while the TV plays: the pill
    is the TV's - it said Starting... until the send came back."""
    from dopeiptv.ui import cast_card as cc
    w = _window("live")
    w._cast_inflight = 1
    _tick(w, clock, 1, "PLAYING/None")
    assert w.cast_bar.pill.state == cc.PLAYING
    _tick(w, clock, 1, "BUFFERING/None")
    assert w.cast_bar.pill.state == cc.STARTING


def test_a_send_a_newer_one_replaced_is_no_error(monkeypatch):
    """Live while a seek was on its way: the seek's send stops ("Casting
    stopped.") and the newer one goes - nothing to tell."""
    from dopeiptv.i18n import tr
    w = _window("live")
    said = []
    monkeypatch.setattr(w, "_error", said.append, raising=False)
    w._cast_send_failed(tr("cast_stopped"))
    assert said == []
    w._cast_send_failed("no route")
    assert said == [tr("cast_failed", msg="no route")]


def test_the_tracks_button_is_a_chromecast_s_alone():
    """Its menu on an Apple TV, a DLNA TV or a Kodi held only the cast
    window, which has its own button."""
    w = _window("live")
    w.cast_bar_tracks = w.cast_bar.tracks
    for airplay, as_is, shown in ((False, False, True), (True, False, False),
                                  (False, True, False)):
        w._cast_on_airplay = lambda a=airplay: a
        w._cast_plays_as_is = lambda _n, x=as_is: x
        w._cast_tracks_shown("Living room TV")
        assert w.cast_bar.tracks.isVisibleTo(w.cast_bar) is shown, (airplay, as_is)
    w._cast_tracks_shown(None)
    assert not w.cast_bar.tracks.isVisibleTo(w.cast_bar)


# -- the watchdog in the window --------------------------------------------------------

def test_a_stuck_channel_is_sent_again_twice_then_the_card_says_try_again(sync_runs, clock):
    from dopeiptv.ui import cast_card as cc
    w = _window("live")
    _tick(w, clock, 11, "BUFFERING/None")
    assert len(w.cast.casts) == 1 and w.cast.casts[0]["settle"] is True
    assert w.cast.casts[0]["url"] == "http://p/7.m3u8"           # the live edge
    assert w.cast_bar.pill.state == cc.RECONNECTING and "1" in w.cast_bar.pill.text
    _tick(w, clock, 11)
    assert len(w.cast.casts) == 2 and w.cast_bar.pill.state == cc.RECONNECTING
    _tick(w, clock, 11)
    assert len(w.cast.casts) == 2, "two tries, no more by itself"
    assert w.cast_bar.pill.state == cc.FAILED and w.cast_bar.action_kind == "retry"
    assert w.toasts and w.toasts[-1][3] == "cast_stuck"
    # Never two converters: each try let the last one go first.
    assert w.cast.bridge.starts == 3 and w.cast.bridge.running
    _tick(w, clock, 30)
    assert len(w.cast.casts) == 2
    # Try again: asked about the account's connection as a cast is, then sent.
    w._cast_card_action("retry")
    assert w.asked == ["http://p/7.ts"] and len(w.cast.casts) == 3
    assert w.cast_bar.pill.state == cc.RECONNECTING
    _tick(w, clock, 6, "PLAYING/None")
    assert w.cast_bar.pill.state == cc.PLAYING
    assert w._cast_watch().tries == 0


def test_a_stuck_film_goes_on_from_where_it_was(sync_runs, clock):
    w = _window("movie", duration=888.0)
    _tick(w, clock, 11, "BUFFERING/None")
    assert len(w.cast.casts) == 1
    sent = w.cast.casts[0]
    assert sent["settle"] is True and sent["start"] == 1830.0


def test_a_pause_or_a_stall_that_ends_by_itself_sends_nothing(sync_runs, clock):
    w = _window("live")
    w._cast_paused_at = datetime.now()
    _tick(w, clock, 60, "BUFFERING/None")
    assert w.cast.casts == []
    w._cast_paused_at = None
    _tick(w, clock, 6, "BUFFERING/None")
    _tick(w, clock, 3, "PLAYING/None")
    _tick(w, clock, 6, "BUFFERING/None")
    assert w.cast.casts == [] and w._cast_watch().tries == 0


def test_nothing_is_sent_again_beside_a_stream_on_its_way(sync_runs, clock):
    sync_runs.append("hold")
    w = _window("live")
    _tick(w, clock, 11, "BUFFERING/None")
    assert len(sync_runs) == 2 and w._cast_inflight == 1     # one on its way
    _tick(w, clock, 40)
    assert len(sync_runs) == 2, "no second send while one is on its way"
    work, done, _failed = sync_runs.pop()
    work()
    done("Living room TV")
    assert w._cast_inflight == 0 and len(w.cast.casts) == 1


def test_a_full_account_is_not_sent_again_by_itself(sync_runs, clock):
    from dopeiptv.i18n import tr
    from dopeiptv.ui import cast_card as cc
    w = _window("live")
    w.verdict = object()                 # another stream holds the connection
    _tick(w, clock, 11, "BUFFERING/None")
    assert w.cast.casts == []
    assert w.cast_bar.pill.state == cc.FAILED
    assert w.cast_bar.note.text() == tr("cast_note_no_room")


def test_a_try_that_fails_to_go_waits_for_the_next(sync_runs, clock):
    from dopeiptv.ui import cast_card as cc
    w = _window("live")

    def refuse(*_a, **_k):
        raise RuntimeError("receiver gone")
    w.cast.cast = refuse
    _tick(w, clock, 11, "BUFFERING/None")
    assert w.errors == [] and w.cast_bar.pill.state == cc.RECONNECTING
    _tick(w, clock, 11, "IDLE/None")
    _tick(w, clock, 11)
    assert w.cast_bar.pill.state == cc.FAILED and w.toasts


# -- what plays, marked and found ------------------------------------------------------

def test_what_the_tv_plays_is_what_plays(sync_runs):
    from dopeiptv.ui import media_cards as mc
    w = _window("movie", duration=888.0)
    now = w._now_playing()
    assert now is not None and now.on_tv and now.key == 42 and now.group == "vod"
    assert now.device == "Living room TV" and 0 < now.fraction <= 1
    assert w._now_key("vod") == "42" and w._now_key("live") == ""
    marked = []
    w._pf_mark_playing = lambda: marked.append("films")
    w._place_series_mark = lambda: marked.append("series")
    try:
        w._cast_marks_changed()
        assert mc.on_tv() and set(marked) == {"films", "series"}
        w._cast_device = None
        assert w._now_playing() is None
        w._cast_marks_changed()
        assert not mc.on_tv()
    finally:
        mc.set_on_tv(False)
    ep = _window("episode")
    assert ep._now_playing().group == "episode"
    assert ep._now_playing().series_ctx["series_id"] == 9


def test_the_playing_mark_says_on_tv_while_it_plays_there():
    from dopeiptv.ui import media_cards as mc
    card = mc.Card(key="42", title="Sintel", pills=("new",))
    try:
        assert mc.card_pills(card, True)[0] == ("playing", "")
        mc.set_on_tv(True)
        assert mc.card_pills(card, True)[0] == ("tv", "")
        assert mc.pill_text("tv") == mc.tr("pill_on_tv")
        assert "playing" not in [k for k, _t in mc.card_pills(card, True)]
        assert mc.card_pills(card, False) == [("new", "")]
        # A view marking the same title again repaints when where it plays changed.
        view = mc.CardView()
        view.set_items([card])
        mc.set_on_tv(False)
        view.set_playing("42")
        mc.set_on_tv(True)
        view.set_playing("42")
        assert view._marked_tv is True
    finally:
        mc.set_on_tv(False)


# -- the layout, narrow to wide --------------------------------------------------------

def _leaves(card):
    from dopeiptv.ui import cast_card as cc
    parts = [card.art, card.title, card.line, card.device_mark, card.device, card.pill,
             card.note, card.action, card.back, card.pause, card.forward, card.stop,
             card.seek, card.time, card.live, card.mute, card.vol, card.tracks, card.ts,
             card.airplay, card.devices]
    return [p for p in parts if p.isVisibleTo(card)], cc


def _laid_out(width, state="reconnecting"):
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtGui import QPixmap
    from dopeiptv.i18n import tr
    from dopeiptv.ui import cast_card as cc
    card = cc.CastCard()
    card.set_media("A channel with a rather long name", "Rapport · 19:30–20:00",
                   "Living room TV with a long name")
    card.set_art(QPixmap(80, 80), "poster", "Sintel")
    card.set_state(state, 1, 2, note=tr("cast_note_failed"), action="retry")
    card.time.setText("−1:04:12 · A programme with a long title")
    for w in (card.live, card.airplay):
        w.show()
    card.sync()
    card.resize(width, card.heightForWidth(width))
    card.show()
    for _ in range(3):
        QApplication.processEvents()
    card.resize(width, card.heightForWidth(width))
    for _ in range(3):
        QApplication.processEvents()
    return card


def test_a_click_on_the_card_s_bars_lands_where_it_was_aimed():
    """The timeline and the volume are the player's own slider: a click
    lands where it points (Qt insets the groove by half a handle at each
    end, which x/width ignored), monotonic and exact at both ends."""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from dopeiptv.ui.cast_card import CastCard
    s = CastCard().seek
    s.resize(240, 18)
    s.show()

    def click_at(x):
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(x, 9.0),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        s.mousePressEvent(ev)
        return s.value()

    assert click_at(0) == 0 and click_at(240) == 1000
    assert abs(click_at(120) - 500) <= 1000 // 240 + 1
    seen = [click_at(x) for x in range(0, 241, 10)]
    assert seen == sorted(seen) and len(set(seen)) > 20
    s.hide()


@pytest.mark.parametrize("width", [300, 340, 380, 520, 700, 1100])
def test_nothing_overlaps_and_everything_fits(width):
    from PyQt6.QtCore import QPoint, QRect
    card = _laid_out(width)
    shown, _cc = _leaves(card)
    rects = []
    for wdg in shown:
        top_left = wdg.mapTo(card, QPoint(0, 0))
        r = QRect(top_left, wdg.size())
        assert card.rect().contains(r), f"{type(wdg).__name__} {r} outside {card.rect()} at {width}"
        assert r.width() > 0 and r.height() > 0
        rects.append((wdg, r))
    for i, (a, ra) in enumerate(rects):
        for b, rb in rects[i + 1:]:
            assert not ra.intersects(rb), (
                f"at {width} px {type(a).__name__} {ra} overlaps {type(b).__name__} {rb}")
    card.close()


def test_a_wide_pane_centres_the_content_and_a_narrow_one_grows_taller():
    from dopeiptv.ui import cast_card as cc
    card = _laid_out(1100)
    body = card.body.geometry()
    assert body.width() <= cc.MAX_W + 2 * cc.PAD
    assert abs(body.left() - (card.width() - body.right() - 1)) <= 1      # centred
    card.close()
    heights = []
    for width in (900, 520, 380, 300):
        c = _laid_out(width)
        heights.append(c.height())
        c.close()
    assert heights == sorted(heights), heights
    narrow = _laid_out(300)
    assert narrow.art.width() == cc.POSTER_N[0]          # the smaller picture
    narrow.close()
