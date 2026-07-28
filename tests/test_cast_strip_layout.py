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

    # Hide the timeshift button (controls[2], the rewind icon).
    controls[2].hide()
    _settle(bar, 420)
    assert bar.heightForWidth(420) <= full
    # Everything after it closed up: nothing moved further along the line or
    # down onto another one, which is what an empty reserved slot would do.
    for c in controls:
        if c.isHidden():
            continue
        assert spots()[id(c)] <= before[id(c)], \
            f"{c.__class__.__name__} moved away from the hidden button"
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
    bar, controls = _strip()
    assert bar.minimumSizeHint().width() <= 160
    _settle(bar, 130)
    assert not _overlaps(controls)
