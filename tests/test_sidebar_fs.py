"""Sidebar and middle-strip state must survive video fullscreen.

Exiting fullscreen resizes the window from fullscreen width back to normal,
and restores the splitter sizes DEFERRED. Two things used to misread that
transition: the narrow-window sidebar auto-collapse saw a "just got narrow"
edge and re-collapsed a deliberately expanded sidebar, and the middle pane's
control strip read its transiently tiny width and stuck in the glyph/icon
form (setSizes doesn't emit splitterMoved, so nothing flipped it back).
Both are now muted during the exit (_fs_exiting) and recomputed from the
restored geometry in _end_fs_exit. These tests drive the real mixin methods
on a stub.
"""
from dopeiptv.ui.mw_sidebar import _SidebarMixin


class _Pane:
    def __init__(self, min_w=240, width=600, visible=True):
        self._min_w = min_w
        self._w = width
        self._visible = visible

    def minimumWidth(self):
        return self._min_w

    def width(self):
        return self._w

    def isVisible(self):
        return self._visible


class _Stub(_SidebarMixin):
    """Just enough MainWindow surface for the auto-collapse + strip logic."""

    def __init__(self, width=500, mid_width=600):
        self._w = width
        self._mid = _Pane(min_w=240, width=mid_width)
        self._det = _Pane(min_w=300)
        self._sidebar_expanded_w = 200
        self._sidebar_collapsed = False
        self._last_narrow = False
        self.collapse_calls: list[bool] = []
        self.compact_calls: list[bool] = []

    def width(self):
        return self._w

    def _set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.collapse_calls.append(collapsed)
        self._sidebar_collapsed = collapsed

    def _apply_mid_compact(self, compact: bool) -> None:
        self.compact_calls.append(compact)
        self._mid_compact = compact


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


def test_mid_strip_ignores_transient_width_during_fs_exit():
    s = _Stub(mid_width=100)   # transiently tiny while sizes are deferred
    s._fs_exiting = True
    s._update_mid_compact()
    assert s.compact_calls == [], (
        "the middle strip must not flip to the glyph form from the "
        "transient width mid fullscreen-exit")


def test_end_fs_exit_resyncs_and_recomputes_without_acting():
    s = _Stub(width=500, mid_width=600)   # narrow window, wide mid pane
    s._fs_exiting = True
    s._end_fs_exit()
    assert s._fs_exiting is False
    assert s._last_narrow is True     # baseline resynced to real geometry
    # The strip was recomputed from the RESTORED width: wide -> full form.
    assert s.compact_calls == [False]
    # And the unchanged narrow state is not an edge - no collapse call:
    s._maybe_auto_collapse_sidebar()
    assert s.collapse_calls == [], (
        "after the baseline resync the unchanged narrow state must not "
        "retrigger the auto-collapse")


def test_end_fs_exit_keeps_compact_when_mid_really_is_narrow():
    s = _Stub(mid_width=300)   # genuinely narrow middle pane
    s._fs_exiting = True
    s._end_fs_exit()
    assert s.compact_calls == [True]


def test_fullscreen_itself_stays_muted():
    s = _Stub(width=500)
    s._player_fs = True
    s._maybe_auto_collapse_sidebar()
    assert s.collapse_calls == []
