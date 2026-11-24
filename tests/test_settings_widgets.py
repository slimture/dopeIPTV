"""The Settings window's own controls: the offset in words, the stepper,
the chips, and the flat page they sit on."""
from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel  # noqa: E402

from dopeiptv.ui.settings_ui import (  # noqa: E402
    Chip, ChipGroups, ChoicePicker, DurationSpin, FoldedPicker, SettingsPage, SettingsShell,
    SizeSpin, Stepper, ThemeTile, ValueSlider, size_text,
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


def _folded(current="nord"):
    tiles = [ThemeTile(k, k.title()) for k in THEME_ORDER]
    picker = ChoicePicker(tiles, current, groups=[("Dark", list(THEME_ORDER))], fill=True)
    picked = []
    picker.picked.connect(picked.append)
    folded = FoldedPicker(picker, lambda k: ThemeTile(k, k.title()), f"All themes ({len(tiles)})",
                          "Fewer themes", count=5,
                          showcase=("graphite", "light", "nord", "oled", "latte", "dracula"))
    return folded, tiles, picked


def test_folded_five_themes_show_the_one_chosen_first():
    folded, tiles, picked = _folded()
    folded.resize(700, 400)
    folded.show()
    app.processEvents()
    # Folded: five in a row, the chosen one first and checked, the
    # showcase's after it (the chosen one not twice); the button under them.
    assert not folded.is_open() and folded.picker.isHidden() and folded.row.isVisible()
    assert [q.key for q in folded.quick] == ["nord", "graphite", "light", "oled", "latte"]
    assert [q.text() for q in folded.quick][:2] == ["Nord", "Graphite"]
    assert [q.isChecked() for q in folded.quick] == [True, False, False, False, False]
    assert folded.button.text() == f"All themes ({len(tiles)})"
    assert folded.button.y() > folded.row.y()
    assert folded.heightForWidth(700) >= ThemeTile.H, "a folded row is no row at all"
    # A theme of the row picked there is picked - and the row stays put.
    folded.quick[2].click()
    assert folded.picker.value() == "light" and picked == ["light"]
    assert [q.key for q in folded.quick] == ["nord", "graphite", "light", "oled", "latte"]
    assert [q.isChecked() for q in folded.quick] == [False, False, True, False, False]
    # Out: every tile, the chosen one checked; one picked there that the row
    # does not have comes first in it, the rest moving along.
    folded.button.click()
    assert folded.is_open() and folded.picker.isVisible() and folded.row.isHidden()
    assert folded.button.text() == "Fewer themes"
    assert folded.heightForWidth(700) > 2 * ThemeTile.H
    [t for t in tiles if t.key == "dracula"][0].click()
    assert folded.picker.value() == "dracula" and picked[-1] == "dracula"
    folded.button.click()
    assert not folded.is_open() and folded.row.isVisible()
    assert [q.key for q in folded.quick] == ["dracula", "nord", "graphite", "light", "oled"]
    assert folded.quick[0].isChecked() and folded.quick[0].text() == "Dracula"
    folded.hide()


def test_a_search_for_a_theme_by_name_folds_them_out_and_in_again():
    folded, _tiles, _picked = _folded()
    folded.searched("drac")
    assert folded.is_open()
    folded.searched("")
    assert not folded.is_open()
    folded.searched("accent")                 # no theme's name: as it was
    assert not folded.is_open()
    # Folded out by hand, a search going away leaves them out.
    folded.button.click()
    folded.searched("nord")
    folded.searched("")
    assert folded.is_open()


def test_the_settings_sidebar_is_dragged_within_its_bounds():
    shell = SettingsShell("Search", "Nothing")
    shell.resize(1000, 600)
    shell.show()
    app.processEvents()
    assert shell.side_width() == SettingsShell.SIDE_W
    moved = []
    shell.side_moved.connect(moved.append)
    shell.split.moveSplitter(320, 1)
    app.processEvents()
    assert shell.side_width() == 320 and moved and moved[-1] == 320
    shell.split.moveSplitter(900, 1)          # never past its widest
    app.processEvents()
    assert shell.side_width() == SettingsShell.SIDE_MAX
    shell.split.moveSplitter(20, 1)           # nor narrower than its narrowest
    app.processEvents()
    assert shell.side_width() == SettingsShell.SIDE_MIN
    shell.set_side_width(10_000)
    app.processEvents()
    assert shell.side_width() == SettingsShell.SIDE_MAX
    # The page beside it takes what is left, and a resize leaves the side be.
    shell.set_side_width(300)
    shell.resize(1200, 600)
    app.processEvents()
    assert shell.side_width() == 300
    shell.hide()


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


def test_settings_closed_while_its_pages_come_out_is_no_error(monkeypatch):
    """A group's pages come out one after another on timers when it is
    turned on; the window closed in between left them touching rows already
    deleted - an error from a timer, offered as a report."""
    import sys
    import time

    from PyQt6 import sip
    from PyQt6.QtGui import QIcon

    from dopeiptv.ui.settings_ui import SettingsShell
    errors: list = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: errors.append(a))
    for _round in range(2):
        shell = SettingsShell("Search", "Nothing")
        for key in ("nerd", "net", "boxes", "player"):
            shell.add_page(SettingsPage(key, key.title()), QIcon())
        shell.set_gated("nerd", ["net", "boxes", "player"])
        shell.arrange([("Nerd", ["nerd", "net", "boxes", "player"], "#888888")])
        shell.set_group_enabled("nerd", False)
        shell.set_group_enabled("nerd", True)
        sip.delete(shell)
        end = time.monotonic() + 0.4
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
    assert errors == [], errors
