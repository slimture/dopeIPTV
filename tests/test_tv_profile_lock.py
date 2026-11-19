"""TV mode while "Who's watching?" waits for a profile's PIN at start: no key
gets past the profiles' tiles - a number typed does not tune a channel, the
volume and play keys do nothing - and leaving TV mode brings the window's
own picker back instead of the profile without its PIN."""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from dopeiptv.ui.mw_tv import _Before, _TvMixin  # noqa: E402

_APP = None


def _app():
    from PyQt6.QtWidgets import QApplication
    global _APP
    _APP = QApplication.instance() or QApplication([])   # kept alive
    return _APP


class _Page:
    def __init__(self):
        self.handled = []

    def handle(self, action):
        self.handled.append(action)
        return True


class _Player:
    current_url = "http://x/1.ts"

    def __init__(self):
        self.done = []

    def toggle_mute(self):
        self.done.append("mute")

    def _nudge_volume(self, n):
        self.done.append(("volume", n))


class _Bar:
    def show(self):
        pass

    def setEnabled(self, on):  # noqa: N802 - Qt's name
        pass


class _Stack:
    def __init__(self):
        self.index = 5

    def indexOf(self, w):  # noqa: N802 - Qt's name
        return -1

    def setCurrentIndex(self, i):  # noqa: N802 - Qt's name
        self.index = i

    def updatesEnabled(self):  # noqa: N802 - Qt's name
        return True


class _Locked(_TvMixin):
    def __init__(self):
        _app()
        self._tv_on, self._tv_watching = True, False
        self._profile_must_pick = True
        self._tv_page = _Page()
        self.player = _Player()
        self._player_fs = False
        self._tv_saved = _Before(True, False, None, None, None, False, False, None, False)
        self._center_stack = _Stack()
        self.done = []

    def _profile_locked(self):
        return bool(self._profile_must_pick)

    def _tv_digit(self, n):
        self.done.append(("digit", n))

    def _tv_picture(self):
        self.done.append("picture")

    def _remote_media(self, name, arg=None):
        self.done.append(("media", name))

    def _play_channel_step(self, step):
        self.done.append(("step", step))
        return True

    def _tv_inputs_stop(self):
        pass

    def _tv_hide_info(self):
        pass

    def menuBar(self):  # noqa: N802 - Qt's name
        return _Bar()

    def isFullScreen(self):  # noqa: N802 - Qt's name
        return True

    def _update_mid_compact(self):
        pass

    def _events_tv_mode(self):
        pass

    def show_profile_picker(self):
        self.done.append("picker")


def test_no_tv_key_gets_past_who_is_watching():
    w = _Locked()
    for action in ("digit_5", "ok", "vol_up", "mute", "play", "play_pause", "picture", "stop"):
        w._tv_action(action)
    assert w.done == [] and w.player.done == []
    assert w._tv_page.handled == ["digit_5", "ok", "vol_up", "mute", "play", "play_pause",
                                  "picture", "stop"]
    # The phone remote, the remote API and Home Assistant come the same way.
    assert w.tv_command("tv_key", "digit_7") == {"ok": True, "tv_mode": True}
    assert w.done == [] and w._tv_page.handled[-1] == "digit_7"


def test_once_a_profile_is_picked_the_keys_are_tv_modes_again():
    w = _Locked()
    w._profile_must_pick = False
    w._tv_action("digit_5")
    w._tv_action("play")
    assert w.done == [("digit", 5), "picture"] and w._tv_page.handled == []


def test_leaving_tv_mode_brings_the_windows_own_picker_back():
    w = _Locked()
    w.exit_tv_mode()
    assert not w._tv_on and w.done == ["picker"]
    # A profile picked (or none asked for): nothing comes back.
    w = _Locked()
    w._profile_must_pick = False
    w.exit_tv_mode()
    assert not w._tv_on and w.done == []


def test_a_tab_clicked_does_not_get_past_who_is_watching():
    from dopeiptv.ui import tv_mode

    class Win:
        locked = True

        def _profile_locked(self):
            return self.locked

        def _tv_has_picture(self):
            return False

    _app()
    win = Win()
    page = tv_mode.TvPage(win)
    shown = []
    page.show_screen = lambda key, focus=True: shown.append(key)
    page.tab("home").click()
    assert shown == []
    win.locked = False
    page.tab("guide").click()
    assert shown == ["guide"]
    page.deleteLater()


# -- another profile: TV mode's screens read again, a kids profile's limits ---------------

class _Overrides:
    def is_hidden(self, mode, cid):
        return False

    def is_locked(self, mode, cid):
        return cid == "9"

    def display_name(self, mode, cid, name):
        return name


class _Parental:
    session_unlocked = True


class _Client:
    def live_categories(self):
        return [{"category_id": "1", "category_name": "News"},
                {"category_id": "2", "category_name": "XXX Adult"},
                {"category_id": "9", "category_name": "Late night"}]

    def vod_categories(self):
        return [{"category_id": "5", "category_name": "Films"},
                {"category_id": "6", "category_name": "18+ only"}]

    def live_streams(self, cid=None):
        return [{"stream_id": 1, "name": "News 1", "category_id": "1"},
                {"stream_id": 2, "name": "News adult", "category_id": "1", "is_adult": "1"}]

    def vod_streams(self, cid=None):
        return [{"stream_id": 11, "name": "Film", "category_id": "5"},
                {"stream_id": 12, "name": "Film adult", "category_id": "5", "is_adult": 1}]

    def series_list(self, cid=None):
        return []


class _Win:
    def __init__(self):
        from PyQt6.QtCore import QThreadPool
        self.kids = False
        self.overrides, self.parental, self.client = _Overrides(), _Parental(), _Client()
        self.pool = QThreadPool()
        self.logos = self.poster_art = None

    def _profile_kids(self):
        return self.kids

    def _profile_locked(self):
        return False

    def _tv_has_picture(self):
        return False

    def _favorite_channels(self):
        return []

    def _catalog_filter(self, mode):
        return lambda it: True

    def _channel_hidden(self, it, mode):
        return False


def _wait(win, cond):
    import time
    end = time.monotonic() + 5
    while not cond() and time.monotonic() < end:
        win.pool.waitForDone(20)
        _APP.processEvents()
    return cond()


def test_a_kids_profile_sees_no_adult_category_nor_title_in_tv_mode():
    from dopeiptv.ui import tv_mode
    _app()
    win = _Win()
    page = tv_mode.TvPage(win)
    chans = page.screen("channels")
    chans.enter()
    assert _wait(win, lambda: chans.chans.count() == 2)
    names = [c["name"] for c in chans.cats.items()]
    assert "XXX Adult" in names and "Late night" in names      # a grown-up, unlocked
    # A switch to a kids profile (the household's lock shut again): read again.
    win.kids, win.parental.session_unlocked = True, False
    chans.rebuild()
    assert chans.cats.count() == 0 and chans.chans.count() == 0
    chans.enter()
    assert _wait(win, lambda: chans.chans.count() == 1)
    assert [c["name"] for c in chans.cats.items()][-1] == "News"
    assert [c["name"] for c in chans.chans.items()] == ["News 1"]
    films = page.screen("vod")
    films.enter()
    assert _wait(win, lambda: films.grid.count() == 1)
    assert [c["name"] for c in films.cats.items()][1:] == ["Films"]
    assert [c["name"] for c in films.grid.items()] == ["Film"]
    page.deleteLater()


def test_search_in_tv_mode_forgets_the_profile_before():
    from dopeiptv.ui import tv_mode
    _app()
    win = _Win()
    page = tv_mode.TvPage(win)
    search = page.screen("search")
    search.type("film")
    search.search()                         # the index first, on a worker
    assert _wait(win, lambda: search._index is not None)
    assert [r["name"] for r in search._index["vod"]] == ["Film", "Film adult"]
    win.kids = True
    search.rebuild()
    assert search._index is None and search.query == ""
    search.type("film")
    search.search()
    assert _wait(win, lambda: search._index is not None)
    assert [r["name"] for r in search._index["vod"]] == ["Film"]
    # An index made for the profile before, arriving late, is dropped.
    search._index_in((search._index_gen - 1, {"vod": [{"name": "Old"}]}))
    assert [r["name"] for r in search._index["vod"]] == ["Film"]
    page.deleteLater()


class _Prof:
    def __init__(self, pid, name, pin=""):
        self.id, self.name, self._pin = pid, name, pin

    @property
    def has_pin(self):
        return bool(self._pin)

    def verify(self, pin):
        return pin == self._pin


class _Store:
    def __init__(self, *ps):
        self._ps = list(ps)

    def get(self, pid):
        return next((p for p in self._ps if p.id == pid), None)

    def profiles(self):
        return list(self._ps)

    def verify(self, pid, pin):
        # As ProfileStore.verify: the switch asks the store, which also
        # writes a PIN kept the old way anew (core/pins.py).
        p = self.get(pid)
        return p is not None and p.verify(pin)


class _NoParental:
    def has_pin(self):
        return False


def _picker_window():
    from dopeiptv.core import profiles
    from dopeiptv.ui.mw_profiles import _ProfilesMixin

    class W(_ProfilesMixin):
        def __init__(self):
            self.profiles = _Store(_Prof("me", "Me", "2468"), _Prof("kid", "Lisa"))
            self.parental = _NoParental()
            self.switched = []
            self._profile_must_pick = True
            self._profile_picker = None

        def switch_profile(self, pid):
            self.switched.append(pid)
            profiles.activate(pid)
            return True

        def _profile_state(self):
            return {}

        def menuBar(self):  # noqa: N802 - Qt's name
            return _Bar()

    profiles.activate("me")
    return W()


def test_who_is_watching_at_start_is_not_got_past_from_the_phone():
    """The phone remote, the remote API and Home Assistant switch profiles
    (_profile_switch_remote): at start, with the profile in use behind its
    PIN, asking for that same profile without the PIN closed the picker."""
    import pytest

    from dopeiptv.core import profiles
    w = _picker_window()
    try:
        with pytest.raises(ValueError):
            w._profile_switch_remote({"id": "me"})
        assert w._profile_must_pick and w.switched == []
        w._profile_switch_remote({"name": "me", "pin": "2468"})
        assert not w._profile_must_pick and w.switched == ["me"]
        # Another profile picked from the phone at start: picked as in the
        # picker - Settings and TV mode's keys are not held back after it.
        w = _picker_window()
        w._profile_switch_remote({"name": "Lisa"})
        assert w.switched == ["kid"] and not w._profile_must_pick
        assert w._profile_locked() is False
    finally:
        profiles.activate("")


def test_the_profile_switch_reads_tv_modes_screens_again_with_tv_mode_off():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "dopeiptv/ui/mw_profiles.py").read_text(
        encoding="utf-8")
    at = src.index("def _profile_refresh")
    body = src[at:src.index("def ", at + 10)]
    assert "scr.rebuild()" in body and "_tv_on" not in body
