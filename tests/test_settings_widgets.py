"""The Settings window's own controls: the offset in words, the stepper,
the chips, and the flat page they sit on."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from dopeiptv.ui.settings_ui import (  # noqa: E402
    Chip, ChipGroups, ChoicePicker, DurationSpin, SettingsPage, SizeSpin,
    Stepper, ThemeTile, ValueSlider, size_text,
)
from dopeiptv.ui.theme import THEME_ORDER  # noqa: E402

app = QApplication.instance() or QApplication([])


def _offset() -> DurationSpin:
    return DurationSpin("{time} later", "{time} earlier", "None", "{n} h",
                        "{n} min")


def test_an_offset_reads_as_words():
    spin = _offset()
    assert spin.textFromValue(0) == "None"
    assert spin.textFromValue(90) == "1 h 30 min later"
    assert spin.textFromValue(-45) == "45 min earlier"
    assert spin.textFromValue(120) == "2 h later"
    assert spin.textFromValue(-1439) == "23 h 59 min earlier"


def test_an_offset_steps_by_five_minutes_then_by_quarters():
    spin = _offset()
    seen = []
    for _ in range(14):
        spin.stepBy(1)
        seen.append(spin.value())
    assert seen[:12] == [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]
    assert seen[12:] == [75, 90]
    spin.setValue(60)
    spin.stepBy(-1)
    assert spin.value() == 55
    spin.setValue(0)
    spin.stepBy(-1)
    assert spin.value() == -5


def test_a_stored_offset_off_the_grid_is_kept_until_stepped():
    spin = _offset()
    spin.setValue(7)                 # the old boxes took any minute
    assert spin.value() == 7 and spin.text() == "7 min later"
    spin.stepBy(1)
    assert spin.value() == 10
    spin.setValue(7)
    spin.stepBy(-1)
    assert spin.value() == 5
    spin.setValue(-67)
    spin.stepBy(1)
    assert spin.value() == -60


def test_an_offset_stops_at_its_ends():
    spin = _offset()
    spin.setValue(spin.maximum())
    spin.stepBy(1)
    assert spin.value() == spin.maximum()
    spin.setValue(-1425)
    spin.stepBy(-1)
    assert spin.value() == -1425


def test_the_stepper_buttons_step_and_grey_out_at_the_ends():
    st = Stepper(lo=1, hi=3, value=2)
    st.plus.click()
    assert st.value() == 3 and not st.plus.isEnabled()
    st.minus.click()
    st.minus.click()
    assert st.value() == 1 and not st.minus.isEnabled()
    assert st.plus.isEnabled()
    st.setValue(2)
    assert st.minus.isEnabled() and st.plus.isEnabled()


def test_the_stepper_shows_its_number():
    st = Stepper(lo=0, hi=99, value=42)
    st.show()
    app.processEvents()
    # The app's spin box rule once left the text field one pixel wide.
    assert st.spin.lineEdit().width() > st.spin.fontMetrics().horizontalAdvance("42")
    st.hide()


def test_a_chip_is_a_checkable_switch():
    chip = Chip("Continue watching", True)
    assert chip.isChecked()
    chip.click()
    assert not chip.isChecked()
    groups = ChipGroups([("Yours", [chip, Chip("History", False)])])
    assert [c.text() for c in groups.chips] == ["Continue watching", "History"]
    assert groups.heightForWidth(600) > 0


def test_themes_in_groups_are_still_one_choice():
    tiles = [ThemeTile(k, k) for k in THEME_ORDER]
    dark = [k for k in THEME_ORDER if k != "latte"]
    picker = ChoicePicker(tiles, "graphite",
                          groups=[("Dark", dark), ("Light", ["latte"])],
                          fill=True)
    assert picker.value() == "graphite"
    [t for t in tiles if t.key == "latte"][0].click()
    assert picker.value() == "latte"
    assert sum(t.isChecked() for t in tiles) == 1


def test_filled_tiles_reach_both_edges():
    tiles = [ThemeTile(k, k) for k in THEME_ORDER[:7]]
    picker = ChoicePicker(tiles, THEME_ORDER[0], fill=True)
    picker.resize(700, picker.heightForWidth(700))
    picker.show()
    app.processEvents()
    first_row = [t for t in tiles if t.y() == tiles[0].y()]
    assert first_row[0].x() == 0
    assert 700 - (first_row[-1].x() + first_row[-1].width()) < len(first_row)
    picker.hide()


def test_a_section_heading_is_plain_text_in_line_with_its_rows():
    page = SettingsPage("x", "Page")
    page.card("Time offsets").row("Replay delay")
    head = [lb for lb in page.findChildren(QLabel)
            if lb.objectName() == "SettingsCardTitle"][0]
    assert head.text() == "Time offsets"     # no longer in capitals
    assert head.indent() == 0


def test_a_size_reads_with_its_unit():
    assert size_text(0, "No limit") == "No limit"
    assert size_text(500, "") == "500 MB"
    assert size_text(4500, "") == "4.5 GB"
    assert size_text(25000, "") == "25 GB"
    assert size_text(1_500_000, "") == "1.5 TB"


def test_a_size_steps_along_its_grid_and_keeps_a_stored_one():
    spin = SizeSpin([0, 1000, 5000, 10000], "No limit", current=100_000_000)
    assert spin.maximum() == 100_000_000      # 100 TB stored: not clamped
    spin.setValue(100_000_000)
    assert spin.value() == 100_000_000 and spin.text() == "100 TB"
    spin.setValue(3000)                       # off the grid
    spin.stepBy(1)
    assert spin.value() == 5000
    spin.setValue(0)
    assert spin.text() == "No limit"
    spin.stepBy(-1)
    assert spin.value() == 0


def test_the_slider_writes_its_value_beside_it():
    sl = ValueSlider(0.0, 3.0, 1.0, scale=10,
                     text=lambda v: "Off" if v == 0 else f"{v:.1f}")
    assert sl.value() == 1.0 and sl.label.text() == "1.0"
    sl.slider.setValue(0)
    assert sl.value() == 0.0 and sl.label.text() == "Off"


def test_settings_closed_while_its_pages_fold_out_is_no_error(monkeypatch):
    """The pages under a parent fold out one after another on timers; the
    window closed in between left them touching rows already deleted - an
    error from a timer, offered as a report."""
    import sys
    import time

    from PyQt6 import sip
    from PyQt6.QtGui import QIcon

    from dopeiptv.ui.settings_ui import SettingsShell
    errors: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    for how in ("enable", "unfold"):
        shell = SettingsShell("Search", "Nothing")
        for key in ("nerd", "net", "boxes", "lists"):
            shell.add_page(SettingsPage(key, key.title()), QIcon())
        shell.set_children("nerd", ["net", "boxes", "lists"], "#888888")
        if how == "enable":
            shell.set_group_enabled("nerd", False)
            shell.set_group_enabled("nerd", True)
        else:
            shell.set_folded("nerd", True)
            shell.set_folded("nerd", False)
        sip.delete(shell)
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
    assert errors == [], errors
