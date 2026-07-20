"""The sidebar must keep its expanded/rail choice across video fullscreen.

Exiting fullscreen resizes the window from fullscreen width back to normal,
which used to look like a genuine "window just got narrow" edge to the
auto-collapse - so a deliberately expanded sidebar came back as the icon rail
after every fullscreen. The exit now sets _fs_exiting to mute the
auto-collapse for the transition, then resyncs its baseline (_end_fs_exit).
These tests drive the real mixin methods on a stub.
"""
from dopeiptv.ui.mw_sidebar import _SidebarMixin


class _Pane:
    def __init__(self, min_w):
        self._min_w = min_w

    def minimumWidth(self):
        return self._min_w


class _Stub(_SidebarMixin):
    """Just enough MainWindow surface for the auto-collapse logic."""

    def __init__(self, width=500):
        self._w = width
        self._mid = _Pane(240)
        self._det = _Pane(300)
        self._sidebar_expanded_w = 200
        self._sidebar_collapsed = False
        self._last_narrow = False
        self.collapse_calls: list[bool] = []

    def width(self):
        return self._w

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.collapse_calls.append(collapsed)
        self._sidebar_collapsed = collapsed


def test_auto_collapse_still_fires_on_a_real_narrow_edge():
    s = _Stub(width=500)   # threshold = 200+240+300+40 = 780 -> narrow
    s._maybe_auto_collapse_sidebar()
    assert s.collapse_calls == [True]
    assert s._auto_collapsed is True


def test_fs_exit_transition_never_auto_collapses():
    s = _Stub(width=500)
    s._fs_exiting = True   # mid fullscreen-exit resize storm
    s._maybe_auto_collapse_sidebar()
    assert s.collapse_calls == [], (
        "the fullscreen-exit resize must not be treated as a narrow edge - "
        "this is what re-collapsed an expanded sidebar after every fullscreen")


def test_end_fs_exit_resyncs_baseline_without_acting():
    s = _Stub(width=500)   # narrow window, sidebar deliberately expanded
    s._fs_exiting = True
    s._end_fs_exit()
    assert s._fs_exiting is False
    assert s._last_narrow is True     # baseline resynced to real geometry
    # And the next resize event within the same narrow state is NOT an edge:
    s._maybe_auto_collapse_sidebar()
    assert s.collapse_calls == [], (
        "after the baseline resync the unchanged narrow state must not "
        "retrigger the auto-collapse")


def test_fullscreen_itself_stays_muted():
    s = _Stub(width=500)
    s._player_fs = True
    s._maybe_auto_collapse_sidebar()
    assert s.collapse_calls == []
