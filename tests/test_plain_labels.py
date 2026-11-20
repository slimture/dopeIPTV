"""A provider's, a guide's, a box's or a server's words are shown as words:
Qt's AutoText reads a tag before the first line break as markup - a
programme called <span style="font-size:200px"> blew the activity panel up,
an <img src="/path"> showed a picture from the disk. Labels that show such
text are PlainText; a QMessageBox or a tooltip, which have no plain mode,
get it escaped (ui/widgets.plain_text)."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtGui import QTextDocument  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox, QWidget  # noqa: E402

HOSTILE = 'Recording <span style="font-size:200px">News</span><img src="/tmp/x.png">'


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _shown(rich: str) -> str:
    doc = QTextDocument()
    doc.setHtml(rich)
    return doc.toPlainText()


def test_plain_text_escapes_only_what_qt_would_read_as_markup(app):
    from dopeiptv.ui.widgets import plain_text
    assert plain_text("Plain words & a < b") == "Plain words & a < b"
    assert plain_text("") == ""
    out = plain_text(HOSTILE + "\nline two")
    assert "<span" not in out and "<img" not in out
    assert _shown(out).replace(" ", "\n") == HOSTILE + "\nline two"


def test_a_notice_shows_a_guide_s_title_as_words(app):
    from dopeiptv.ui.notice_widgets import ActivityLine, ActivityPanel, DecisionCard, _PanelRow
    from dopeiptv.ui.notices import NoticeHub
    host = QWidget()
    host.resize(800, 600)
    hub = NoticeHub()
    hub.SHOW_AFTER_MS = hub.MIN_SHOWN_MS = 0
    hub.start(HOSTILE)
    end = time.monotonic() + 3
    while not hub.entries() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)
    panel = ActivityPanel(hub, host)
    row = _PanelRow(panel, hub.entries()[0])
    text = row.findChild(QLabel, "NoticeRowText")
    assert text.textFormat() == Qt.TextFormat.PlainText
    assert text.text() == HOSTILE
    assert text.sizeHint().height() < 60, "laid out as one line of words"
    line = ActivityLine(hub, host)
    line.refresh()
    assert "Recording" in line.toolTip() and "<span" not in line.toolTip()
    card = DecisionCard(hub, host)
    hub.post(HOSTILE, action=lambda: None, decide=True)
    card.refresh()
    assert "Recording" in card.toolTip() and "<img" not in card.toolTip()
    host.close()


def test_the_cast_card_and_the_problem_window_show_words(app):
    from dopeiptv.ui.cast_card import CastCard
    from dopeiptv.ui.dialogs import ProblemDialog
    card = CastCard()
    for lbl in (card.title, card.line, card.note):
        assert lbl.textFormat() == Qt.TextFormat.PlainText
    d = ProblemDialog(None, HOSTILE, HOSTILE, "detail")
    shown = [lbl for lbl in d.findChildren(QLabel) if lbl.text() == HOSTILE]
    assert len(shown) == 2
    assert all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in shown)
    d.close()


def test_a_confirmation_names_a_title_as_words(app, monkeypatch):
    from dopeiptv.ui import widgets
    seen = []

    def exec_(box):
        seen.append((box.textFormat(), box.text()))
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "exec", exec_)
    assert widgets.confirm(None, "Delete", HOSTILE) is False
    assert seen == [(Qt.TextFormat.PlainText, HOSTILE)]


def test_tv_mode_s_headings_are_words(app):
    from dopeiptv.ui.tv_mode import TvScreen
    for make in (TvScreen._title, TvScreen._note):
        lbl = make(None, HOSTILE)
        assert lbl.textFormat() == Qt.TextFormat.PlainText and lbl.text() == HOSTILE


def _one_line(lbl: QLabel) -> bool:
    """Laid out as words: a 200 px span read as markup is far taller."""
    return lbl.sizeHint().height() < 100


def test_a_source_s_page_shows_its_name_and_account_as_words(app):
    from dopeiptv.ui.source_page import SourcePage

    class Store:
        def __init__(self, pl):
            self.pl = pl

        def get(self, _pid):
            return self.pl

        def update(self, _pid, **fields):
            self.pl.update(fields)
    pl = {"id": "s1", "name": HOSTILE, "type": "xtream", "server": "http://x", "username": "u"}
    page = SourcePage(dict(pl), store=Store(dict(pl)), nerd_on=False)
    page._account_came(HOSTILE)        # the provider's status, expiry, connections
    for lbl in (page.title, page.subtitle, page.crumb_name, page.problem_label):
        assert lbl.textFormat() == Qt.TextFormat.PlainText
    assert page.title.text() == HOSTILE and _one_line(page.title)
    assert HOSTILE in page.subtitle.text()
    page.close()


def test_a_settings_row_names_a_device_as_words(app):
    from dopeiptv.ui.settings_ui import SettingsPage
    page = SettingsPage("cast", HOSTILE, HOSTILE)
    card = page.card(HOSTILE)
    row = card.row(HOSTILE, None, HOSTILE)
    note = card.note(HOSTILE)
    for lbl in (row.label, row.hint_label, note):
        assert lbl.textFormat() == Qt.TextFormat.PlainText and lbl.text() == HOSTILE
    assert _one_line(row.label)
    shown = [lbl for lbl in page.findChildren(QLabel) if lbl.text() == HOSTILE]
    assert len(shown) >= 5
    assert all(lbl.textFormat() == Qt.TextFormat.PlainText for lbl in shown)
    page.close()


def test_the_lists_tooltips_show_words(app):
    from dopeiptv.ui.channel_list import ChannelListModel
    from dopeiptv.ui.media_cards import Card, CardModel
    cards = CardModel()
    cards.set_items([Card(key="a", title=HOSTILE, line="2021")])
    tip = cards.data(cards.index(0), Qt.ItemDataRole.ToolTipRole)
    assert "<img" not in tip and "<span" not in tip
    assert _shown(tip).replace(" ", "\n") == HOSTILE.replace(" ", "\n") + "\n2021"
    rows = ChannelListModel()
    rows.set_items([{"name": "x", "_tip": HOSTILE}], "vod")
    tip = rows.data(rows.index(0), Qt.ItemDataRole.ToolTipRole)
    assert "<img" not in tip and "Recording" in _shown(tip)
    rows.set_items([{"name": "x", "_tip": "Movies/2021"}], "vod")
    assert rows.data(rows.index(0), Qt.ItemDataRole.ToolTipRole) == "Movies/2021"


def test_the_cast_card_names_the_tv_as_words(app):
    from dopeiptv.ui.cast_card import CastCard
    card = CastCard()
    card.set_device(HOSTILE, where="192.168.1.20")
    assert card.device.textFormat() == Qt.TextFormat.PlainText
    tip = card.device.toolTip()
    assert "<img" not in tip and "192.168.1.20" in _shown(tip)


def test_the_seek_bar_s_programme_tip_is_words(app):
    from dopeiptv.media.seek_slider import _SeekSlider
    win = QWidget()
    slider = _SeekSlider(win)
    assert slider._hover_tip_label().textFormat() == Qt.TextFormat.PlainText
    win.close()


def test_a_box_s_names_in_its_questions_are_words(app, monkeypatch):
    from types import SimpleNamespace

    from PyQt6.QtWidgets import QInputDialog

    from dopeiptv.ui.mw_box import _BoxMixin
    asked = []

    def get_text(_parent, _title, label, **_kw):
        asked.append(label)
        return "", False
    monkeypatch.setattr(QInputDialog, "getText", get_text)
    _BoxMixin._box_rec_rename(SimpleNamespace(), {"name": HOSTILE, "stream_id": "1"})
    assert asked and "<img" not in asked[0] and "<span" not in asked[0]
    assert "Recording" in _shown(asked[0])
