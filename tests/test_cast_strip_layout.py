"""The cast strip has to survive being pulled narrow.

It sits in the right-hand column, and that column is draggable - so its width
is not ours to assume. A plain QHBoxLayout handed less than its contents need
does not stop at their minimum: it goes on taking pixels away until the mute
button, the volume slider and the timeshift, tracks, pause and stop controls
are drawn on top of one another. Which is what a small laptop's column is to
begin with.

Measured, not eyeballed: build the real strip, resize it down, and check that
every visible control still has a rectangle of its own inside the widget.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    from PyQt6.QtWidgets import QApplication
    yield QApplication.instance() or QApplication([])


def _strip():
    """The cast strip's own widget tree, built the way the window builds it."""
    from PyQt6.QtWidgets import (
        QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
    )
    from dopeiptv.media.embedded import _SeekSlider
    from dopeiptv.ui.widgets import FlowRow, cast_strip_icon

    bar = QWidget()
    pol = QSizePolicy(QSizePolicy.Policy.Preferred,
                      QSizePolicy.Policy.Preferred)
    pol.setHeightForWidth(True)
    bar.setSizePolicy(pol)
    outer = QVBoxLayout(bar)
    outer.setContentsMargins(12, 8, 8, 8)
    outer.setSpacing(6)

    row = FlowRow(spacing=10)
    outer.addLayout(row)
    names = QWidget()
    col = QVBoxLayout(names)
    col.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel("Casting to Alva TV")
    lbl.setWordWrap(True)
    col.addWidget(lbl)
    title = QLabel("SVT1 HD")
    title.setWordWrap(True)
    col.addWidget(title)
    names.setMinimumWidth(90)
    row.add(names, grow=True)

    buttons = []
    for kind in ("volume", "rewind", "tracks", "pause"):
        b = QPushButton()
        b.setIcon(cast_strip_icon(kind, "#fff"))
        b.setFixedWidth(36)
        row.add(b)
        buttons.append(b)
    vol = _SeekSlider()
    vol.setFixedWidth(110)
    row.add(vol)
    stop = QPushButton("Stop")
    row.add(stop)

    seek_row = FlowRow(spacing=10)
    seek_row.setContentsMargins(2, 0, 2, 0)
    seek = _SeekSlider()
    seek.setMinimumWidth(120)
    seek_row.add(seek, grow=True)
    clock = QLabel("−12:30 · Rapport")
    seek_row.add(clock)
    live = QPushButton("⏭ LIVE")
    seek_row.add(live)
    outer.addLayout(seek_row)

    return bar, [names, *buttons, vol, stop, seek, clock, live]


def _settle(bar, width):
    bar.resize(width, bar.heightForWidth(width))
    bar.layout().activate()
    return bar


def _overlaps(controls):
    """Every pair of rectangles that share a pixel."""
    bad = []
    for i, a in enumerate(controls):
        for b in controls[i + 1:]:
            if a.geometry().intersects(b.geometry()):
                bad.append((a, b, a.geometry(), b.geometry()))
    return bad


@pytest.mark.parametrize("width", [640, 480, 380, 300, 240, 200, 160, 140])
def test_nothing_is_ever_drawn_on_top_of_anything_else(width):
    bar, controls = _strip()
    _settle(bar, width)
    bad = _overlaps(controls)
    assert not bad, (
        f"at {width} px: " +
        "; ".join(f"{x.geometry()} over {y.geometry()}"
                  for _x, _y, x, y in
                  [(a, b, a, b) for a, b, _ga, _gb in bad]))


@pytest.mark.parametrize("width", [640, 380, 240, 160])
def test_every_control_stays_inside_the_strip(width):
    """Wrapping is only worth anything if the strip grows to hold the extra
    lines - otherwise the controls are merely hidden further down."""
    bar, controls = _strip()
    _settle(bar, width)
    inside = bar.rect()
    for c in controls:
        g = c.geometry()
        assert inside.contains(g), (
            f"at {width} px, {c.__class__.__name__} at {g} "
            f"is outside {inside}")
        assert g.width() > 0 and g.height() > 0


def test_it_gets_taller_as_it_gets_narrower():
    """The lines have to be paid for in height, and the widget is what asks
    for it - heightForWidth is how a parent layout knows."""
    bar, _ = _strip()
    tall = [bar.heightForWidth(w) for w in (640, 380, 240, 160)]
    assert tall == sorted(tall), f"height did not grow as it narrowed: {tall}"
    assert tall[-1] > tall[0]


def test_wide_enough_and_it_is_still_the_row_it_was():
    """Wrapping must not cost anything when there is room: one line for the
    controls, one for the position, and the labels holding the left."""
    bar, controls = _strip()
    _settle(bar, 700)
    names = controls[0]
    row = controls[:7]                         # labels through stop
    # Centred on the line, so it is the middles that line up - a 36-pixel
    # button beside a two-line label reads as a row, not as a staircase.
    mids = {c.geometry().center().y() for c in row}
    assert len(mids) == 1, f"the control row broke up at 700 px: {mids}"
    # The labels take the slack, so the controls sit out at the right edge.
    assert names.geometry().width() > 200
    assert row[-1].geometry().right() >= bar.width() - 12


def test_a_hidden_control_takes_no_room():
    """Timeshift, pause and the red LIVE button come and go with what is
    being cast. Reserving a gap for one that is not shown would break the
    line early and leave a hole where it used to be - and on a strip this
    narrow that hole is a whole extra row."""
    def spots():
        return {id(c): (c.geometry().top(), c.geometry().left())
                for c in controls if not c.isHidden()}

    bar, controls = _strip()
    _settle(bar, 420)
    full, before = bar.heightForWidth(420), spots()
    line = [c for c in controls[1:7] if c.geometry().top() == controls[2].geometry().top()]

    # Hide the timeshift button (controls[2], the rewind icon).
    controls[2].hide()
    _settle(bar, 420)
    assert bar.heightForWidth(420) <= full
    # Everything after it closed up: nothing moved further along the line or
    # down onto another one, which is what an empty reserved slot would do.
    for c in controls[3:]:
        if c.isHidden():
            continue
        assert spots()[id(c)] <= before[id(c)], \
            f"{c.__class__.__name__} moved away from the hidden button"
    # Before it, nothing went down a line either; on its line the controls
    # stay together at the right edge, so with the label taking the slack
    # they may move along by what the hidden one took. Where nothing came up
    # from the next line - the fonts of a big desktop - they do.
    for c in (controls[0], controls[1]):
        assert spots()[id(c)][0] <= before[id(c)][0], \
            f"{c.__class__.__name__} went down a line"
    # No hole where it was: its neighbours on the line are a spacing apart.
    k = line.index(controls[2])
    if 0 < k < len(line) - 1:
        left, right = line[k - 1], line[k + 1]
        if left.geometry().top() == right.geometry().top():
            gap = right.geometry().left() - left.geometry().right() - 1
            assert gap == bar.layout().itemAt(0).layout().spacing(), gap
    assert not _overlaps([c for c in controls if not c.isHidden()])

    # And with everything optional gone, the strip is shorter still.
    for c in (controls[3], controls[4], controls[-1]):
        c.hide()
    _settle(bar, 200)
    assert not _overlaps([c for c in controls if not c.isHidden()])


def test_it_can_be_narrower_than_the_widest_control_asks_for():
    """The minimum is one item per line, not the sum of them - that sum is
    what a plain row insists on, and being refused it is what made it
    overlap."""
    from PyQt6.QtGui import QFontMetrics
    bar, controls = _strip()
    # The widest one alone, in the fonts this desktop has, and the margins
    # (the strip's own, and the position row's two pixels each side).
    left, _top, right, _bottom = bar.layout().getContentsMargins()
    widest = max(max(c.minimumSize().width(), c.minimumSizeHint().width()) for c in controls)
    least = bar.minimumSizeHint().width()
    assert least <= widest + 4 + left + right, (least, widest)
    assert least < sum(c.minimumSizeHint().width() for c in controls[:7]), least
    # In the fonts of a desktop at 100 % that is a 160-pixel column.
    if QFontMetrics(controls[-2].font()).horizontalAdvance(controls[-2].text()) <= 120:
        assert least <= 160
    _settle(bar, 130)
    assert not _overlaps(controls)


def test_a_click_lands_where_it_was_aimed():
    """A click was mapped as x/width, which ignores that Qt insets the
    groove by half a handle at each end - the handle is DRAWN centred on
    its position. So a click went up to half a handle from where the handle
    then landed, and on a strip a couple of hundred pixels wide that is
    several per cent of a film. Dragging hid it, because the eye corrects
    as the handle follows the pointer.
    """
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QMouseEvent
    from dopeiptv.media.embedded import _SeekSlider

    s = _SeekSlider()
    s.setRange(0, 1000)
    s.resize(240, 18)
    s.show()

    def click_at(x):
        ev = QMouseEvent(QMouseEvent.Type.MouseButtonPress,
                         QPointF(x, 9.0), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        s.mousePressEvent(ev)
        return s.value()

    # The ends are exact: a click at the far left is the beginning, at the
    # far right the end. x/width got the right end but not the left.
    assert click_at(0) == 0
    assert click_at(240) == 1000

    # And the middle is the middle, within a pixel's worth of value.
    mid = click_at(120)
    assert abs(mid - 500) <= 1000 // 240 + 1, mid

    # Monotonic all the way across - no dead zone at either end.
    seen = [click_at(x) for x in range(0, 241, 10)]
    assert seen == sorted(seen)
    assert len(set(seen)) > 20, "the whole width has to be reachable"
    s.hide()
