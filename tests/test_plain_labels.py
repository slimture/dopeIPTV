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
